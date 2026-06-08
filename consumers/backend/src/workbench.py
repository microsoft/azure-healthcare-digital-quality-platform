"""Quality Measures Workbench API.

Exposes a single FastAPI router (``/api/workbench``) that backs the redesigned
**Catalog** and **Cohorts** tabs. All state lives in two Cosmos containers
under the ``dq`` database, both partitioned by ``/docType``:

* ``dq/catalog``   — docType in ``{measure, tag, agency}``
* ``dq/cohorts``   — docType in ``{cohort, member, measurement_execution,
                                  measure_report, submission}``

The catalog is seeded out-of-band by ``_scripts/seed_workbench_data.py`` at
provision time (see ``consumers/_infra/hooks/postprovision.{ps1,sh}``). The
router only reads from / writes to Cosmos; it does not touch the local
``_data/`` directory.
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import measure_catalog


# ---------------------------------------------------------------------------
# Default seed data (used when sample_data_dir is provided and the on-disk
# catalog files are missing). Kept in sync with submitters/backend/src/workbench.py.
# ---------------------------------------------------------------------------

_DEFAULT_TAGS: List[Dict[str, Any]] = [
    {"id": "tag-shared-savings",   "name": "Shared Savings Program",     "color": "#0072B2"},
    {"id": "tag-universal",        "name": "Universal Foundation",       "color": "#009E73"},
    {"id": "tag-hospital-quality", "name": "Hospital Quality Reporting", "color": "#CC79A7"},
    {"id": "tag-mips",             "name": "MIPS / QPP",                 "color": "#E69F00"},
    {"id": "tag-acute-care",       "name": "Acute Care",                 "color": "#D55E00"},
]

_DEFAULT_AGENCIES: List[Dict[str, Any]] = [
    {
        "id": "agency-cms",
        "name": "Centers for Medicare & Medicaid Services",
        "shortName": "CMS",
        "description": "U.S. federal agency that administers Medicare and Medicaid quality programs.",
        "website": "https://www.cms.gov",
        "country": "US",
        "programs": [],
    },
]


def _builtin_measure_meta(measure_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": measure_id,
        "docType": "measure",
        "title": entry.get("title", measure_id),
        "description": entry.get("description", ""),
        "version": entry.get("version", ""),
        "topic": entry.get("topic", ""),
        "enabled": True,
        "customName": None,
        "customDescription": None,
        "tags": [],
        "cqlLibrary": entry.get("cqlLibrary"),
        "dataRequirements": deepcopy(entry.get("dataRequirements") or []),
        "builtin": True,
        "createdAt": int(time.time() * 1000),
        "updatedAt": int(time.time() * 1000),
    }


# ---------------------------------------------------------------------------
# Pydantic models (light-weight; full FHIR shape lives in measure_catalog)
# ---------------------------------------------------------------------------


class MeasureMeta(BaseModel):
    """Workbench-specific metadata layered on top of the FHIR Measure entry."""

    id: str
    title: str
    description: str = ""
    version: str = ""
    topic: str = ""
    enabled: bool = True
    customName: Optional[str] = None
    customDescription: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    cqlLibrary: Optional[str] = None
    builtin: bool = False


class MeasureUpdate(BaseModel):
    enabled: Optional[bool] = None
    customName: Optional[str] = None
    customDescription: Optional[str] = None
    tags: Optional[List[str]] = None
    dataRequirements: Optional[List[Dict[str, Any]]] = None


class TagModel(BaseModel):
    id: Optional[str] = None
    name: str
    color: str = "#64748b"
    description: str = ""


class ReportingPeriod(BaseModel):
    start: Optional[str] = None
    end: Optional[str] = None


class ProgramModel(BaseModel):
    id: Optional[str] = None
    name: str
    shortName: str = ""
    description: str = ""
    reportingPeriod: ReportingPeriod = Field(default_factory=ReportingPeriod)
    requiredMeasures: List[str] = Field(default_factory=list)


class AgencyModel(BaseModel):
    id: Optional[str] = None
    name: str
    shortName: str = ""
    description: str = ""
    website: str = ""
    country: str = ""
    programs: List[ProgramModel] = Field(default_factory=list)
    # Legacy single-program fields kept for backward-compat with older clients.
    reportingPeriod: Optional[ReportingPeriod] = None
    requiredMeasures: Optional[List[str]] = None


class CohortModel(BaseModel):
    id: Optional[str] = None
    name: str
    description: str = ""
    memberIds: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    measureIds: List[str] = Field(default_factory=list)


class CohortMembersUpdate(BaseModel):
    add: List[str] = Field(default_factory=list)
    remove: List[str] = Field(default_factory=list)


class SubmissionModel(BaseModel):
    cohortId: str
    measureIds: List[str]
    note: str = ""
    # agencyId is kept for backwards-compat with the submitters stack schema
    # but is no longer required in consumers (no regulatory-agency picker).
    agencyId: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(value: str) -> str:
    out = []
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_"):
            out.append("-")
    slug = "".join(out).strip("-") or f"item-{int(time.time() * 1000)}"
    return slug[:64]


def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_workbench_router(
    *,
    catalog_helper: Any,
    cohorts_helper: Any,
    auth_dependency: Callable[..., Any],
    sample_data_dir: Optional[Path] = None,
) -> APIRouter:
    """Build the workbench router.

    ``catalog_helper`` and ``cohorts_helper`` must implement the doc-type
    methods (``upsert_doc``, ``get_doc``, ``list_docs``, ``delete_doc``)
    added to :class:`cosmosdb_helper.CosmosDBHelper`.

    When ``sample_data_dir`` points at a directory containing the workbench
    seed JSON files (``measures.json``, ``cohorts.json``, ``patients.json``,
    ``regulatory-agencies.json``, ``regulatory-agency-programs.json``,
    ``measures-tags.json``), the catalog and cohorts containers are
    auto-seeded on first read. This mirrors submitters/backend so local
    dev runs against the file-backed helper get a populated UI without
    a manual seed step.
    """

    router = APIRouter(prefix="/api/workbench", tags=["workbench"])

    # --- seeding -----------------------------------------------------------

    _seeded = {"value": False}

    def _read_data_json(name: str) -> Any:
        if not sample_data_dir:
            return None
        path = sample_data_dir / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _seed_measures(existing_ids: set) -> None:
        for measure_id in measure_catalog.list_measure_ids():
            if measure_id in existing_ids:
                continue
            entry = measure_catalog.get_measure_entry(measure_id) or {}
            try:
                catalog_helper.upsert_doc(
                    "measure", measure_id, _builtin_measure_meta(measure_id, entry)
                )
                existing_ids.add(measure_id)
            except Exception:
                pass
        for raw in _read_data_json("measures.json") or []:
            if not isinstance(raw, dict):
                continue
            measure_id = raw.get("id")
            if not measure_id:
                continue
            try:
                current = catalog_helper.get_doc("measure", measure_id) or {}
            except Exception:
                current = {}
            merged = dict(current)
            merged.update({k: v for k, v in raw.items() if v is not None})
            merged["id"] = measure_id
            merged.setdefault("docType", "measure")
            merged.setdefault("createdAt", _now_ms())
            merged["updatedAt"] = _now_ms()
            try:
                catalog_helper.upsert_doc("measure", measure_id, merged)
                existing_ids.add(measure_id)
            except Exception:
                pass

    def _seed_tags(existing_ids: set) -> None:
        on_disk = _read_data_json("measures-tags.json") or []
        seen: set = set()
        for raw in on_disk:
            if not isinstance(raw, dict):
                continue
            tag_id = raw.get("id") or _slug(raw.get("name", ""))
            if not tag_id:
                continue
            doc = dict(raw)
            doc["id"] = tag_id
            doc.setdefault("color", "#64748b")
            try:
                catalog_helper.upsert_doc("tag", tag_id, doc)
                seen.add(tag_id)
                existing_ids.add(tag_id)
            except Exception:
                pass
        for tag in _DEFAULT_TAGS:
            if tag["id"] in existing_ids or tag["id"] in seen:
                continue
            try:
                catalog_helper.upsert_doc("tag", tag["id"], dict(tag))
                existing_ids.add(tag["id"])
            except Exception:
                pass

    def _seed_agencies(existing_ids: set) -> None:
        agency_records = _read_data_json("regulatory-agencies.json") or []
        program_records = _read_data_json("regulatory-agency-programs.json") or []
        agency_map: Dict[str, Dict[str, Any]] = {}
        for raw in agency_records:
            if not isinstance(raw, dict):
                continue
            aid = raw.get("id") or _slug(raw.get("name", ""))
            if not aid:
                continue
            doc = dict(raw)
            doc["id"] = aid
            doc["programs"] = []
            agency_map[aid] = doc
        for raw in program_records:
            if not isinstance(raw, dict):
                continue
            aid = raw.get("agencyId")
            if not aid or aid not in agency_map:
                continue
            program = {k: v for k, v in raw.items() if k != "agencyId"}
            agency_map[aid]["programs"].append(program)
        if not agency_map:
            for raw in _DEFAULT_AGENCIES:
                doc = dict(raw)
                agency_map[doc["id"]] = doc
        for aid, doc in agency_map.items():
            try:
                catalog_helper.upsert_doc("agency", aid, doc)
                existing_ids.add(aid)
            except Exception:
                pass

    def _seed_cohorts() -> None:
        cohort_defs = _read_data_json("cohorts.json") or []
        for raw in cohort_defs:
            if not isinstance(raw, dict):
                continue
            cid = raw.get("id") or _slug(raw.get("name", ""))
            if not cid:
                continue
            try:
                existing = cohorts_helper.get_doc("cohort", cid) or {}
            except Exception:
                existing = {}
            doc = dict(existing)
            doc.update({k: v for k, v in raw.items() if v is not None})
            doc["id"] = cid
            doc.setdefault("docType", "cohort")
            doc.setdefault("createdAt", _now_ms())
            doc["updatedAt"] = _now_ms()
            try:
                cohorts_helper.upsert_doc("cohort", cid, doc)
            except Exception:
                pass

    def _seed_members() -> None:
        bundles = _read_data_json("patients.json") or []
        if isinstance(bundles, dict):
            bundles = [bundles]
        for bundle in bundles:
            if not isinstance(bundle, dict):
                continue
            member_id = bundle.get("id") or bundle.get("mrn")
            if not member_id:
                continue
            payload: Dict[str, Any] = {"bundle": bundle, "mrn": member_id}
            for entry in bundle.get("entry") or []:
                resource = (entry or {}).get("resource") or {}
                if resource.get("resourceType") == "Patient":
                    name = (resource.get("name") or [{}])[0] or {}
                    payload["patientResourceId"] = resource.get("id")
                    payload["displayName"] = (
                        " ".join(filter(None, [
                            " ".join(name.get("given") or []),
                            name.get("family", ""),
                        ])).strip()
                        or member_id
                    )
                    payload["birthDate"] = resource.get("birthDate")
                    payload["gender"] = resource.get("gender")
                    break
            try:
                if hasattr(cohorts_helper, "save_patient_data"):
                    cohorts_helper.save_patient_data(member_id, payload)
                else:
                    cohorts_helper.upsert_doc("member", member_id, payload)
            except Exception:
                pass

    def _ensure_seeded() -> None:
        if _seeded["value"]:
            return
        try:
            existing_measures = {m["id"] for m in catalog_helper.list_docs("measure") if "id" in m}
        except Exception:
            existing_measures = set()
        try:
            existing_tags = {t["id"] for t in catalog_helper.list_docs("tag") if "id" in t}
        except Exception:
            existing_tags = set()
        try:
            existing_agencies = {a["id"] for a in catalog_helper.list_docs("agency") if "id" in a}
        except Exception:
            existing_agencies = set()

        _seed_measures(existing_measures)
        _seed_tags(existing_tags)
        _seed_agencies(existing_agencies)
        _seed_cohorts()
        _seed_members()

        _seeded["value"] = True

    # ----------------------------------------------------------------------
    # Catalog: measures
    # ----------------------------------------------------------------------

    @router.get("/catalog/measures")
    async def list_measures(_user: Dict[str, Any] = Depends(auth_dependency)):
        _ensure_seeded()
        try:
            measures = catalog_helper.list_docs("measure")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"catalog read failed: {e}")
        # For built-in measures seeded before dataRequirements was tracked,
        # backfill the field from the in-process catalog. Once the key exists
        # on the stored doc (including explicit []), the user-supplied value
        # wins and no overlay happens.
        for m in measures:
            mid = m.get("id")
            if not mid or "dataRequirements" in m:
                continue
            entry = measure_catalog.get_measure_entry(mid)
            if entry and entry.get("dataRequirements"):
                m["dataRequirements"] = deepcopy(entry["dataRequirements"])
        # Ensure alphabetical order on display name (custom > title).
        def display_name(m: Dict[str, Any]) -> str:
            return (m.get("customName") or m.get("title") or m.get("id") or "").lower()
        measures.sort(key=display_name)
        return {"measures": measures}

    @router.post("/catalog/measures")
    async def add_measure(
        payload: MeasureMeta = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        if not payload.id:
            raise HTTPException(status_code=400, detail="id is required")
        doc = payload.dict()
        doc["docType"] = "measure"
        doc.setdefault("createdAt", _now_ms())
        doc["updatedAt"] = _now_ms()
        doc["builtin"] = False
        try:
            saved = catalog_helper.upsert_doc("measure", payload.id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"measure": saved}

    @router.patch("/catalog/measures/{measure_id}")
    async def update_measure(
        measure_id: str,
        payload: MeasureUpdate = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            current = catalog_helper.get_doc("measure", measure_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not current:
            raise HTTPException(status_code=404, detail=f"measure {measure_id} not found")
        updates = {k: v for k, v in payload.dict().items() if v is not None}
        current.update(updates)
        current["updatedAt"] = _now_ms()
        try:
            saved = catalog_helper.upsert_doc("measure", measure_id, current)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"measure": saved}

    @router.delete("/catalog/measures/{measure_id}")
    async def delete_measure(
        measure_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            current = catalog_helper.get_doc("measure", measure_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not current:
            raise HTTPException(status_code=404, detail=f"measure {measure_id} not found")
        if current.get("builtin"):
            raise HTTPException(status_code=400, detail="built-in measures cannot be deleted; disable instead")
        try:
            catalog_helper.delete_doc("measure", measure_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"deleted": measure_id}

    # ----------------------------------------------------------------------
    # Catalog: tags
    # ----------------------------------------------------------------------

    @router.get("/catalog/tags")
    async def list_tags(_user: Dict[str, Any] = Depends(auth_dependency)):
        _ensure_seeded()
        try:
            tags = catalog_helper.list_docs("tag")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        tags.sort(key=lambda t: (t.get("name") or "").lower())
        return {"tags": tags}

    @router.post("/catalog/tags")
    async def add_tag(
        payload: TagModel = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        tag_id = payload.id or _slug(payload.name)
        doc = payload.dict()
        doc["id"] = tag_id
        doc["docType"] = "tag"
        doc["updatedAt"] = _now_ms()
        try:
            saved = catalog_helper.upsert_doc("tag", tag_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"tag": saved}

    @router.delete("/catalog/tags/{tag_id}")
    async def delete_tag(
        tag_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            catalog_helper.delete_doc("tag", tag_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"deleted": tag_id}

    # ----------------------------------------------------------------------
    # Catalog: agencies (regulatory-agencies / programs)
    # ----------------------------------------------------------------------

    @router.get("/catalog/agencies")
    async def list_agencies(_user: Dict[str, Any] = Depends(auth_dependency)):
        _ensure_seeded()
        try:
            agencies = catalog_helper.list_docs("agency")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        agencies.sort(key=lambda a: (a.get("name") or "").lower())
        return {"agencies": agencies}

    @router.post("/catalog/agencies")
    async def upsert_agency(
        payload: AgencyModel = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        agency_id = payload.id or _slug(payload.name)
        doc = payload.dict(exclude_none=True)
        doc["id"] = agency_id
        doc["docType"] = "agency"
        # If the legacy fields were sent and no programs[], roll them up into
        # a single program entry so the on-disk shape stays consistent.
        if not doc.get("programs") and (doc.get("reportingPeriod") or doc.get("requiredMeasures")):
            doc["programs"] = [{
                "id": f"{agency_id}-default",
                "name": doc.get("shortName") or doc.get("name") or "Default program",
                "shortName": doc.get("shortName") or "",
                "description": "",
                "reportingPeriod": doc.get("reportingPeriod") or {},
                "requiredMeasures": doc.get("requiredMeasures") or [],
            }]
        doc.pop("reportingPeriod", None)
        doc.pop("requiredMeasures", None)
        # Stamp ids onto programs that arrived without one.
        for p in doc.get("programs") or []:
            if not p.get("id"):
                p["id"] = f"{agency_id}-{_slug(p.get('shortName') or p.get('name') or 'program')}"
        doc.setdefault("createdAt", _now_ms())
        doc["updatedAt"] = _now_ms()
        try:
            saved = catalog_helper.upsert_doc("agency", agency_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"agency": saved}

    @router.delete("/catalog/agencies/{agency_id}")
    async def delete_agency(
        agency_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            catalog_helper.delete_doc("agency", agency_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"deleted": agency_id}

    # ----------------------------------------------------------------------
    # Cohorts
    # ----------------------------------------------------------------------

    @router.get("/cohorts")
    async def list_cohorts(_user: Dict[str, Any] = Depends(auth_dependency)):
        _ensure_seeded()
        try:
            cohorts = cohorts_helper.list_docs("cohort")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        cohorts.sort(key=lambda c: (c.get("name") or "").lower())
        return {"cohorts": cohorts}

    @router.post("/cohorts")
    async def upsert_cohort(
        payload: CohortModel = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        cohort_id = payload.id or _slug(payload.name)
        doc = payload.dict()
        doc["id"] = cohort_id
        doc["docType"] = "cohort"
        doc.setdefault("createdAt", _now_ms())
        doc["updatedAt"] = _now_ms()
        try:
            saved = cohorts_helper.upsert_doc("cohort", cohort_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"cohort": saved}

    @router.delete("/cohorts/{cohort_id}")
    async def delete_cohort(
        cohort_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            current = cohorts_helper.get_doc("cohort", cohort_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not current:
            raise HTTPException(status_code=404, detail=f"cohort {cohort_id} not found")
        if current.get("builtin"):
            raise HTTPException(status_code=400, detail="built-in cohorts cannot be deleted")
        try:
            cohorts_helper.delete_doc("cohort", cohort_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"deleted": cohort_id}

    @router.post("/cohorts/{cohort_id}/members")
    async def update_cohort_members(
        cohort_id: str,
        payload: CohortMembersUpdate = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            current = cohorts_helper.get_doc("cohort", cohort_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not current:
            raise HTTPException(status_code=404, detail=f"cohort {cohort_id} not found")
        member_ids = list(current.get("memberIds") or [])
        for member in payload.add:
            if member not in member_ids:
                member_ids.append(member)
        member_ids = [m for m in member_ids if m not in set(payload.remove)]
        current["memberIds"] = member_ids
        current["updatedAt"] = _now_ms()
        try:
            saved = cohorts_helper.upsert_doc("cohort", cohort_id, current)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"cohort": saved}

    # ----------------------------------------------------------------------
    # Members directory (read-only listing of patients available for cohorts)
    # ----------------------------------------------------------------------

    @router.get("/members")
    async def list_members(_user: Dict[str, Any] = Depends(auth_dependency)):
        try:
            # Patients are stored under docType=patient by save_patient_data.
            patients = cohorts_helper.list_docs("patient")
        except Exception:
            patients = []
        try:
            members = cohorts_helper.list_docs("member")
        except Exception:
            members = []

        seen: set = set()
        out: List[Dict[str, Any]] = []
        for row in (patients or []) + (members or []):
            if not isinstance(row, dict):
                continue
            mid = row.get("id") or row.get("mrn")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append({
                "id": mid,
                "displayName": row.get("displayName"),
                "birthDate": row.get("birthDate"),
                "gender": row.get("gender"),
                "patientResourceId": row.get("patientResourceId"),
            })
        out.sort(key=lambda r: ((r.get("displayName") or r["id"]).lower()))
        return {"members": out}

    # ----------------------------------------------------------------------
    # Submissions (regulatory submission stub from inside the Cohorts tab)
    # ----------------------------------------------------------------------

    @router.post("/submissions")
    async def submit(
        payload: SubmissionModel = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        submission_id = f"sub-{int(time.time() * 1000)}"
        doc = payload.dict()
        doc["id"] = submission_id
        doc["docType"] = "submission"
        doc["createdAt"] = _now_ms()
        doc["status"] = "queued"

        # Gather cohort + member FHIR bundles for the cross-stack forward.
        cohort_doc: Optional[Dict[str, Any]] = None
        member_payloads: List[Dict[str, Any]] = []
        try:
            cohort_doc = cohorts_helper.get_doc("cohort", payload.cohortId)
        except Exception:
            cohort_doc = None
        if not cohort_doc:
            raise HTTPException(status_code=404, detail=f"cohort {payload.cohortId} not found")

        # Resolve each member's stored FHIR Bundle. The seed pipeline writes
        # members under docType=patient via save_patient_data; new uploads may
        # land under docType=member. Try both.
        member_ids = list(cohort_doc.get("memberIds") or [])
        for mid in member_ids:
            mdoc: Optional[Dict[str, Any]] = None
            for dt in ("patient", "member"):
                try:
                    mdoc = cohorts_helper.get_doc(dt, mid)
                except Exception:
                    mdoc = None
                if mdoc:
                    break
            if not mdoc and hasattr(cohorts_helper, "get_patient"):
                try:
                    mdoc = cohorts_helper.get_patient(mid)
                except Exception:
                    mdoc = None
            if not mdoc:
                # Forward the id so the submitters stack can record a skipped row.
                member_payloads.append({"id": mid})
                continue
            member_payloads.append(
                {
                    "id": mid,
                    "displayName": mdoc.get("displayName"),
                    "birthDate": mdoc.get("birthDate"),
                    "gender": mdoc.get("gender"),
                    "patientResourceId": mdoc.get("patientResourceId"),
                    "mrn": mdoc.get("mrn") or mid,
                    "bundle": mdoc.get("bundle") or mdoc.get("fhirBundle"),
                }
            )

        submitters_base = os.getenv(
            "SUBMITTERS_BACKEND_BASE_URL", "http://localhost:8001"
        ).rstrip("/")
        forward_url = f"{submitters_base}/api/workbench/submissions/process"
        forward_payload = {
            "cohort": cohort_doc,
            "members": member_payloads,
            "measureIds": list(payload.measureIds or []),
            "note": payload.note,
            "sourceSubmissionId": submission_id,
            "sourceStack": "consumers",
        }
        doc["remoteUrl"] = forward_url
        doc["memberCount"] = len(member_payloads)

        try:
            resp = requests.post(forward_url, json=forward_payload, timeout=60)
        except Exception as exc:  # noqa: BLE001
            doc["status"] = "forward_failed"
            doc["forwardError"] = str(exc)[:2000]
        else:
            doc["forwardStatusCode"] = resp.status_code
            if resp.ok:
                try:
                    body = resp.json()
                except ValueError:
                    body = {"raw": resp.text[:2000]}
                doc["status"] = "forwarded"
                doc["remoteSubmission"] = body.get("submission") if isinstance(body, dict) else None
                doc["remoteDispatch"] = body.get("dispatch") if isinstance(body, dict) else None
            else:
                doc["status"] = "forward_rejected"
                doc["forwardError"] = resp.text[:2000]

        try:
            cohorts_helper.upsert_doc("submission", submission_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"submission": doc}

    def _refresh_submission_from_submitters(doc: Dict[str, Any]) -> Dict[str, Any]:
        """Pull the live submission state from the submitters stack and merge.

        Only fires when the local submission was forwarded successfully and is
        not yet in a terminal state. The remote `GET /submissions/{id}` returns
        `{submission, measurements: [...]}`.
        """
        terminal = {
            "completed",
            "completed_with_errors",
            "dispatch_failed",
            "forward_failed",
            "forward_rejected",
        }
        if doc.get("status") in terminal:
            return doc
        remote_id = doc.get("id")
        if not remote_id:
            return doc
        submitters_base = os.getenv(
            "SUBMITTERS_BACKEND_BASE_URL", "http://localhost:8001"
        ).rstrip("/")
        url = f"{submitters_base}/api/workbench/submissions/{remote_id}"
        try:
            resp = requests.get(url, timeout=8)
        except Exception as exc:  # noqa: BLE001
            doc["refreshError"] = str(exc)[:500]
            return doc
        if not resp.ok:
            doc["refreshError"] = f"HTTP {resp.status_code}"
            return doc
        try:
            body = resp.json()
        except ValueError:
            doc["refreshError"] = "non-JSON response"
            return doc
        remote_sub = body.get("submission") if isinstance(body, dict) else None
        if isinstance(remote_sub, dict):
            doc["remoteSubmission"] = remote_sub
            remote_status = remote_sub.get("status")
            if remote_status:
                # Surface the remote lifecycle so the UI shows real progress.
                doc["status"] = remote_status
            for key in ("summary", "dispatch", "memberCount", "startedAt", "completedAt"):
                if key in remote_sub:
                    doc[key] = remote_sub[key]
        measurements = body.get("measurements") if isinstance(body, dict) else None
        if isinstance(measurements, list):
            doc["remoteMeasurements"] = measurements
        doc["refreshedAt"] = _now_ms()
        doc.pop("refreshError", None)
        return doc

    @router.get("/submissions")
    async def list_submissions(_user: Dict[str, Any] = Depends(auth_dependency)):
        try:
            subs = cohorts_helper.list_docs("submission")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        subs.sort(key=lambda s: s.get("createdAt", 0), reverse=True)
        return {"submissions": subs}

    @router.get("/submissions/{submission_id}")
    async def get_submission(
        submission_id: str,
        refresh: bool = True,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            doc = cohorts_helper.get_doc("submission", submission_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not doc:
            raise HTTPException(status_code=404, detail=f"submission {submission_id} not found")
        if refresh:
            doc = _refresh_submission_from_submitters(doc)
            try:
                cohorts_helper.upsert_doc("submission", submission_id, doc)
            except Exception:
                pass
        return {"submission": doc}

    @router.post("/submissions/{submission_id}/refresh")
    async def refresh_submission(
        submission_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            doc = cohorts_helper.get_doc("submission", submission_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not doc:
            raise HTTPException(status_code=404, detail=f"submission {submission_id} not found")
        doc = _refresh_submission_from_submitters(doc)
        try:
            cohorts_helper.upsert_doc("submission", submission_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"submission": doc}

    return router
