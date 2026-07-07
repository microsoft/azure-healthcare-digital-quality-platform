"""Quality Measures Workbench API.

Exposes a single FastAPI router (``/api/workbench``) that backs the redesigned
**Catalog** and **Cohorts** tabs. All state lives in two Cosmos containers
under the ``dq`` database, both partitioned by ``/docType``:

* ``dq/catalog``   — docType in ``{measure, tag, agency}``
* ``dq/cohorts``   — docType in ``{cohort, member, measurement_execution,
                                  measure_report, submission}``

The catalog is seeded from :mod:`measure_catalog` on first read so the static
MVP measures appear without requiring a manual import step.
"""

from __future__ import annotations

import json
import os
import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

import measure_catalog


# ---------------------------------------------------------------------------
# DEQM validation helpers (module-level so they can be unit-tested directly)
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_VALID_REPORT_TYPES = {"individual", "subject-list", "summary"}

# Da Vinci Member Attribution List (ATR) Group profile canonical. Received
# cohort rosters are exported/imported as Groups shaped to align with this
# profile; see _docs/DEQM_DAVINCI_ATR_GAP_ANALYSIS.md for conformance details.
_ATR_GROUP_PROFILE = "http://hl7.org/fhir/us/davinci-atr/StructureDefinition/atr-group"

# Map MeasureReport.type to the Cosmos docType used for persistence.
_REPORT_TYPE_TO_DOC_TYPE: Dict[str, str] = {
    "individual": "measure_report",
    "subject-list": "measure_report_subjectlist",
    "summary": "measure_report_summary",
}


def validate_deqm_measure_report(report: Dict[str, Any]) -> Optional[str]:
    """Validate DEQM mandatory elements.

    Returns a human-readable error string, or ``None`` when the report is valid.

    Checks:
    - Required fields: status, type, measure, date, reporter, period
    - type must be one of individual, subject-list, summary
    - deqm-0: measure must include a version (pipe separator)
    - deqm-1: period.start and period.end must have day precision (YYYY-MM-DD)
    """
    if not isinstance(report, dict):
        return "body must be a FHIR MeasureReport JSON object"

    for field in ("status", "type", "measure", "date", "reporter", "period"):
        if field not in report:
            return f"Missing required field: {field}"

    report_type = report.get("type")
    if report_type not in _VALID_REPORT_TYPES:
        return (
            f"Invalid MeasureReport.type: {report_type!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_REPORT_TYPES))}"
        )

    # deqm-0: measure must include version via pipe separator
    measure = str(report.get("measure") or "")
    if "|" not in measure:
        return (
            f"deqm-0 violation: measure must include a version suffix "
            f"(e.g. .../Measure/CMS165v9|9.0.000), got: {measure!r}"
        )

    # deqm-1: period must have start and end with at least YYYY-MM-DD precision
    period = report.get("period") or {}
    start = str(period.get("start") or "")
    end = str(period.get("end") or "")
    if not _DATE_RE.match(start):
        return (
            f"deqm-1 violation: period.start must have day precision (YYYY-MM-DD), got: {start!r}"
        )
    if not _DATE_RE.match(end):
        return (
            f"deqm-1 violation: period.end must have day precision (YYYY-MM-DD), got: {end!r}"
        )

    return None
# ---------------------------------------------------------------------------

# Initial tag list used to back-fill the catalog the first time the workbench
# is read. These are the program / classification tags users mentioned in the
# redesign brief. Colours are taken from the Okabe-Ito colour-blind safe
# palette so chips remain legible for users with deuteranopia/protanopia.
_DEFAULT_TAGS: List[Dict[str, Any]] = [
    {"id": "tag-shared-savings",   "name": "Shared Savings Program",     "color": "#0072B2"},
    {"id": "tag-universal",        "name": "Universal Foundation",       "color": "#009E73"},
    {"id": "tag-hospital-quality", "name": "Hospital Quality Reporting", "color": "#CC79A7"},
    {"id": "tag-mips",             "name": "MIPS / QPP",                 "color": "#E69F00"},
    {"id": "tag-acute-care",       "name": "Acute Care",                 "color": "#D55E00"},
]


# Initial regulatory-agency seed (kept minimal; the full set is loaded from
# _data/regulatory-agencies.json + _data/regulatory-agency-programs.json on
# first read when those files are baked into the image).
_DEFAULT_AGENCIES: List[Dict[str, Any]] = [
    {
        "id": "agency-cms",
        "name": "Centers for Medicare & Medicaid Services",
        "shortName": "CMS",
        "description": "U.S. federal agency that administers Medicare and Medicaid quality programs.",
        "website": "https://www.cms.gov",
        "country": "US",
        "programs": [
            {
                "id": "program-cms-mssp",
                "name": "Medicare Shared Savings Program",
                "shortName": "MSSP",
                "description": "ACO quality reporting via APP Plus.",
                "reportingPeriod": {"start": "2026-01-01", "end": "2026-12-31"},
                "requiredMeasures": ["CMS122v11", "CMS165v9"],
            },
            {
                "id": "program-cms-hospital-iqr",
                "name": "Hospital Inpatient Quality Reporting",
                "shortName": "Hospital IQR",
                "description": "Hospital quality measures including OB complications.",
                "reportingPeriod": {"start": "2026-01-01", "end": "2026-12-31"},
                "requiredMeasures": ["ePC02"],
            },
            {
                "id": "program-cms-uf",
                "name": "Adult Universal Foundation",
                "shortName": "Universal Foundation",
                "description": "Cross-program adult universal foundation measure set.",
                "reportingPeriod": {"start": "2026-01-01", "end": "2026-12-31"},
                "requiredMeasures": ["CMS165v9"],
            },
        ],
    },
]


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
    agencyId: str
    measureIds: List[str]
    note: str = ""


class MeasureSummaryPerMember(BaseModel):
    measureId: str
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    exclusion: Optional[bool] = None


class MeasureSummaryMember(BaseModel):
    memberId: str
    displayName: Optional[str] = None
    perMeasure: List[MeasureSummaryPerMember] = Field(default_factory=list)


class MeasureSummaryRollup(BaseModel):
    measureId: str
    title: Optional[str] = None
    denominator: int = 0
    numerator: int = 0
    exclusions: int = 0
    patients: int = 0
    performanceRate: Optional[float] = None


class MeasureSummaryAgency(BaseModel):
    id: str
    name: Optional[str] = None
    shortName: Optional[str] = None


class MeasureSummaryProgram(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    shortName: Optional[str] = None


class MeasureSummaryCohort(BaseModel):
    id: str
    name: Optional[str] = None
    memberCount: Optional[int] = None


class MeasureSummaryModel(BaseModel):
    """Cross-stack measure summary payload sent from submitters."""

    id: Optional[str] = None
    sourceStack: str = "submitters"
    sourceSendId: Optional[str] = None
    sourceSubmissionId: Optional[str] = None
    agency: MeasureSummaryAgency
    program: MeasureSummaryProgram = Field(default_factory=MeasureSummaryProgram)
    cohort: MeasureSummaryCohort
    periodStart: Optional[str] = None
    periodEnd: Optional[str] = None
    engine: Optional[str] = None
    measureIds: List[str] = Field(default_factory=list)
    perMeasure: List[MeasureSummaryRollup] = Field(default_factory=list)
    perMember: List[MeasureSummaryMember] = Field(default_factory=list)
    note: str = ""
    generatedAt: Optional[int] = None


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


def _extract_measure_id(canonical: str) -> str:
    """Extract the measure id from a canonical URL like ``.../Measure/CMS165v9|9.0.000``.

    Returns an empty string for empty/malformed input rather than raising.
    """
    if not canonical:
        return ""
    # Strip version suffix
    canonical = canonical.split("|")[0].rstrip("/")
    if not canonical:
        return ""
    # Return the last path segment
    parts = canonical.rsplit("/", 1)
    return parts[-1] if parts[-1] else ""


def _receiver_canonical_base() -> str:
    return os.getenv("DEQM_CANONICAL_BASE", measure_catalog.DEFAULT_CANONICAL_BASE).rstrip("/")


def _atr_group_resource(
    cohort_id: str,
    member_ids: List[str],
    *,
    name: Optional[str] = None,
    measure_ids: Optional[List[str]] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    managing_entity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a Da Vinci ATR-aligned FHIR Group describing a cohort roster.

    Mirrors the submitters builder so the receivers stack can round-trip cohort
    rosters as standards-based Group resources (Da Vinci Member Attribution
    List) instead of a proprietary cohort payload.
    """
    period: Optional[Dict[str, str]] = None
    if period_start or period_end:
        period = {}
        if period_start:
            period["start"] = period_start
        if period_end:
            period["end"] = period_end

    members: List[Dict[str, Any]] = []
    for mid in member_ids:
        member: Dict[str, Any] = {
            "entity": {"reference": f"Patient/{mid}"},
            "inactive": False,
        }
        if period:
            member["period"] = dict(period)
        members.append(member)

    group: Dict[str, Any] = {
        "resourceType": "Group",
        "id": f"group-{cohort_id}",
        "meta": {"profile": [_ATR_GROUP_PROFILE]},
        "type": "person",
        "actual": True,
        "quantity": len(member_ids),
        "member": members,
    }
    if name:
        group["name"] = name

    characteristics: List[Dict[str, Any]] = [
        {
            "code": {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationValue",
                        "code": "COHORT",
                    }
                ],
                "text": "Quality measurement cohort membership",
            },
            "valueBoolean": True,
            "exclude": False,
        }
    ]
    if period:
        characteristics.append(
            {
                "code": {"text": "Measurement / attribution period"},
                "valuePeriod": dict(period),
                "exclude": False,
            }
        )
    base = _receiver_canonical_base()
    for mid in measure_ids or []:
        characteristics.append(
            {
                "code": {"text": "In-scope quality measure"},
                "valueReference": {"reference": f"{base}/Measure/{mid}"},
                "exclude": False,
            }
        )
    group["characteristic"] = characteristics

    if managing_entity:
        group["managingEntity"] = managing_entity
    return group


def _atr_group_to_cohort(group: Dict[str, Any]) -> "tuple[str, Dict[str, Any]]":
    """Parse a FHIR Group (Da Vinci ATR roster) into a workbench cohort doc."""
    gid = str(group.get("id") or "").strip()
    if gid.startswith("group-"):
        cohort_id = gid[len("group-"):]
    else:
        cohort_id = gid or f"cohort-{_now_ms()}"

    member_ids: List[str] = []
    for member in group.get("member") or []:
        ref = ((member or {}).get("entity") or {}).get("reference") or ""
        if ref.startswith("Patient/"):
            pid = ref.split("/", 1)[1]
            if pid and pid not in member_ids:
                member_ids.append(pid)

    measure_ids: List[str] = []
    for characteristic in group.get("characteristic") or []:
        ref = ((characteristic or {}).get("valueReference") or {}).get("reference") or ""
        if "/Measure/" in ref:
            mid = ref.split("/Measure/", 1)[1].split("|", 1)[0]
            if mid and mid not in measure_ids:
                measure_ids.append(mid)

    cohort_doc: Dict[str, Any] = {
        "id": cohort_id,
        "docType": "cohort",
        "name": group.get("name") or cohort_id,
        "description": "Imported from a FHIR Group (Da Vinci ATR patient roster).",
        "memberIds": member_ids,
        "measureIds": measure_ids,
        "tags": [],
        "source": "fhir-group-import",
    }
    return cohort_id, cohort_doc


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
        "builtin": True,
        "createdAt": _now_ms(),
        "updatedAt": _now_ms(),
    }


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
        # 1) Built-in FHIR Measures from the in-process catalog.
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

        # 2) Layer measure metadata (tags, custom name/description, version,
        #    topic, scoring, ...) from _data/measures.json on top so the
        #    Catalog tab shows tag-coloured chips out of the box.
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
        # On-disk tag list takes precedence over hard-coded defaults.
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
        # Build agency map with empty programs[] from the agencies file.
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
        # Merge programs into the right agency.
        for raw in program_records:
            if not isinstance(raw, dict):
                continue
            aid = raw.get("agencyId")
            if not aid or aid not in agency_map:
                continue
            program = {k: v for k, v in raw.items() if k != "agencyId"}
            agency_map[aid]["programs"].append(program)
        # Fall back to in-code defaults if the data files are absent.
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
                # Use the existing patient-aware path so legacy /patients APIs
                # see the same row.
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
        _ensure_seeded()
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

    @router.post("/catalog/measures/{measure_id}/sample-data")
    async def generate_sample_data(
        measure_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        """Materialise the on-disk sample bundles for a measure into the
        cohorts container as ``docType=member`` rows. The bundles ship in
        ``data/`` and are seeded into a per-measure cohort the first time the
        user clicks "Generate sample data".
        """
        _ensure_seeded()
        if not sample_data_dir or not sample_data_dir.exists():
            raise HTTPException(status_code=503, detail="sample data directory not configured")

        # Map measure id -> data file prefix (cms122 / cms165 / epc02).
        prefix_map = {"CMS122v11": "cms122", "CMS165v9": "cms165", "ePC02": "epc02"}
        prefix = prefix_map.get(measure_id)
        if not prefix:
            raise HTTPException(status_code=400, detail=f"no sample data prefix for {measure_id}")

        seeded: List[str] = []
        for json_file in sorted(sample_data_dir.glob(f"{prefix}_*.json")):
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            # Re-use the bundle-id derivation already in main.py via the shared
            # helper; keep it simple here by trusting top-level id/mrn fields.
            member_id = (
                payload.get("id")
                or payload.get("mrn")
                or json_file.stem
            )
            payload["mrn"] = member_id
            try:
                cohorts_helper.save_patient_data(member_id, payload)
                seeded.append(member_id)
            except Exception:
                continue

        # Tie the sample members to a per-measure cohort for easy review.
        cohort_id = f"sample-{prefix}"
        cohort_doc = {
            "id": cohort_id,
            "docType": "cohort",
            "name": f"Sample members — {measure_id}",
            "description": f"Auto-generated sample bundles shipped with the accelerator for {measure_id}.",
            "memberIds": seeded,
            "tags": [],
            "createdAt": _now_ms(),
            "updatedAt": _now_ms(),
            "builtin": True,
        }
        try:
            cohorts_helper.upsert_doc("cohort", cohort_id, cohort_doc)
        except Exception:
            pass

        return {"measureId": measure_id, "cohortId": cohort_id, "seeded": seeded}

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
    # Cohort ↔ FHIR Group exchange (Da Vinci ATR patient-roster alignment)
    # ----------------------------------------------------------------------

    @router.get("/cohorts/{cohort_id}/Group")
    async def export_cohort_group(
        cohort_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        """Return a received cohort roster as a Da Vinci ATR-aligned FHIR ``Group``."""
        _ensure_seeded()
        try:
            cohort = cohorts_helper.get_doc("cohort", cohort_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not cohort:
            raise HTTPException(status_code=404, detail=f"cohort {cohort_id} not found")
        return _atr_group_resource(
            cohort_id,
            list(cohort.get("memberIds") or []),
            name=cohort.get("name"),
            measure_ids=list(cohort.get("measureIds") or []),
        )

    @router.post("/cohorts/$import-group")
    async def import_cohort_group(
        group: Dict[str, Any] = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        """Create or update a cohort from a FHIR ``Group`` (Da Vinci ATR roster)."""
        if not isinstance(group, dict) or group.get("resourceType") != "Group":
            raise HTTPException(
                status_code=400,
                detail={
                    "resourceType": "OperationOutcome",
                    "issue": [
                        {
                            "severity": "error",
                            "code": "invalid",
                            "diagnostics": "Request body must be a FHIR Group resource.",
                        }
                    ],
                },
            )
        cohort_id, cohort_doc = _atr_group_to_cohort(group)
        try:
            existing = cohorts_helper.get_doc("cohort", cohort_id) or {}
        except Exception:  # noqa: BLE001
            existing = {}
        cohort_doc["createdAt"] = existing.get("createdAt", _now_ms())
        cohort_doc["updatedAt"] = _now_ms()
        if existing.get("tags"):
            cohort_doc["tags"] = existing["tags"]
        try:
            saved = cohorts_helper.upsert_doc("cohort", cohort_id, cohort_doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"cohort": saved, "memberCount": len(cohort_doc.get("memberIds") or [])}

    # ----------------------------------------------------------------------
    # Members directory (read-only listing of patients available for cohorts)
    # ----------------------------------------------------------------------

    @router.get("/members")
    async def list_members(_user: Dict[str, Any] = Depends(auth_dependency)):
        _ensure_seeded()
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
        try:
            cohorts_helper.upsert_doc("submission", submission_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"submission": doc}

    @router.get("/submissions")
    async def list_submissions(_user: Dict[str, Any] = Depends(auth_dependency)):
        try:
            subs = cohorts_helper.list_docs("submission")
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        subs.sort(key=lambda s: s.get("createdAt", 0), reverse=True)
        return {"submissions": subs}

    # ----------------------------------------------------------------------
    # Measure summaries (cohort num/denom roll-ups received from submitters)
    # DEPRECATED: use /measure-reports instead. Kept for back-compat.
    # ----------------------------------------------------------------------

    @router.post("/measure-summaries")
    async def receive_measure_summary(
        payload: MeasureSummaryModel = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        summary_id = payload.id or f"sum-{_now_ms()}"
        doc = payload.dict()
        doc["id"] = summary_id
        doc["docType"] = "measure_summary"
        doc["receivedAt"] = _now_ms()
        doc["status"] = "received"
        try:
            cohorts_helper.upsert_doc("measure_summary", summary_id, doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e))
        return {"summary": doc}

    @router.get("/measure-summaries")
    async def list_measure_summaries(_user: Dict[str, Any] = Depends(auth_dependency)):
        try:
            out = cohorts_helper.list_docs("measure_summary") or []
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        out.sort(key=lambda s: s.get("receivedAt", s.get("generatedAt", 0)), reverse=True)
        return {"summaries": out}

    @router.get("/measure-summaries/{summary_id}")
    async def get_measure_summary(
        summary_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        doc = cohorts_helper.get_doc("measure_summary", summary_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"measure_summary not found: {summary_id}")
        return {"summary": doc}

    # ----------------------------------------------------------------------
    # DEQM MeasureReport ingest (replaces measure-summaries for FHIR exchange)
    # ----------------------------------------------------------------------

    @router.post("/measure-reports")
    async def receive_measure_report(
        body: Dict[str, Any] = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        """Accept a DEQM MeasureReport or a Bundle of MeasureReports.

        Validates mandatory DEQM elements (deqm-0 and deqm-1) and persists
        each report under a ``docType`` derived from ``MeasureReport.type``.
        Returns an ``OperationOutcome`` 400 on validation failure.
        """
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Request body must be a JSON object")

        resource_type = body.get("resourceType")

        def _operation_outcome_400(msg: str) -> None:
            raise HTTPException(
                status_code=400,
                detail={
                    "resourceType": "OperationOutcome",
                    "issue": [{"severity": "error", "code": "invalid", "diagnostics": msg}],
                },
            )

        def _persist_one(report: Dict[str, Any]) -> Dict[str, Any]:
            err = validate_deqm_measure_report(report)
            if err:
                _operation_outcome_400(err)
            report_type = report["type"]
            doc_type = _REPORT_TYPE_TO_DOC_TYPE.get(report_type, "measure_report")
            report_id = report.get("id") or f"mr-{_now_ms()}"
            # Build a small index header alongside the raw FHIR resource so the
            # workbench can list reports without parsing every resource.
            doc: Dict[str, Any] = {
                "id": report_id,
                "docType": doc_type,
                "reportType": report_type,
                "measureIds": [_extract_measure_id(report.get("measure") or "")],
                "periodStart": (report.get("period") or {}).get("start"),
                "periodEnd": (report.get("period") or {}).get("end"),
                "receivedAt": _now_ms(),
                "resource": report,
            }
            try:
                cohorts_helper.upsert_doc(doc_type, report_id, doc)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(status_code=500, detail=str(e))
            return doc

        if resource_type == "Bundle":
            persisted = []
            for entry in body.get("entry") or []:
                res = entry.get("resource") if isinstance(entry, dict) else None
                if isinstance(res, dict) and res.get("resourceType") == "MeasureReport":
                    persisted.append(_persist_one(res))
            return {"reports": persisted, "count": len(persisted)}

        if resource_type == "MeasureReport":
            doc = _persist_one(body)
            return {"report": doc}

        _operation_outcome_400(
            f"Unsupported resourceType: {resource_type!r}. "
            "Expected MeasureReport or Bundle."
        )

    @router.get("/measure-reports")
    async def list_measure_reports(
        reportType: Optional[str] = None,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        """List persisted DEQM MeasureReports.  Pass ``reportType`` to filter by profile."""
        try:
            doc_types = list(_REPORT_TYPE_TO_DOC_TYPE.values())
            rows: List[Dict[str, Any]] = []
            for dt in doc_types:
                rows.extend(cohorts_helper.list_docs(dt) or [])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if reportType:
            rows = [r for r in rows if r.get("reportType") == reportType]
        rows.sort(key=lambda r: r.get("receivedAt", 0), reverse=True)
        return {"reports": rows, "count": len(rows)}

    @router.get("/measure-reports/{report_id}")
    async def get_measure_report(
        report_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        for doc_type in _REPORT_TYPE_TO_DOC_TYPE.values():
            doc = cohorts_helper.get_doc(doc_type, report_id)
            if doc:
                return {"report": doc}
        raise HTTPException(status_code=404, detail=f"MeasureReport not found: {report_id}")

    return router
