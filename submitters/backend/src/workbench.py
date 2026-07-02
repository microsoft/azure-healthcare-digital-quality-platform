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
import logging
import os
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

import measure_catalog


# ---------------------------------------------------------------------------
# Default seed data
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
    agencyId: str
    measureIds: List[str]
    note: str = ""


class SubmissionMemberPayload(BaseModel):
    id: str
    bundle: Optional[Dict[str, Any]] = None
    displayName: Optional[str] = None
    birthDate: Optional[str] = None
    gender: Optional[str] = None
    patientResourceId: Optional[str] = None
    mrn: Optional[str] = None


class SubmissionProcessRequest(BaseModel):
    """Cross-stack payload from providers backend.

    Carries the cohort definition plus each member's FHIR Bundle so the
    submitters stack can persist the whole cohort locally and trigger a
    Kubernetes Job that runs CQL measures via the orchestrator.
    """

    cohort: Dict[str, Any]
    members: List[SubmissionMemberPayload] = Field(default_factory=list)
    measureIds: List[str] = Field(default_factory=list)
    note: str = ""
    sourceSubmissionId: Optional[str] = None
    sourceStack: str = "providers"
    periodStart: Optional[str] = None
    periodEnd: Optional[str] = None


class MeasureSummarySendRequest(BaseModel):
    """Request to send a cohort measure summary to receivers and platform.

    Aggregates rows from `measurement_history` for the given cohort + measures
    and dispatches the roll-up to the receivers and platform stacks.
    """

    agencyId: str
    programId: Optional[str] = None
    measureIds: List[str] = Field(default_factory=list)
    periodStart: Optional[str] = None
    periodEnd: Optional[str] = None
    note: str = ""
    engine: Optional[str] = None
    sourceSubmissionId: Optional[str] = None
    reportType: Optional[str] = None


# ---------------------------------------------------------------------------
# DEQM FHIR payload builders (cohort-level)
# ---------------------------------------------------------------------------

_DEQM_VALID_REPORT_TYPES = {"individual", "subject-list", "summary"}
_POPULATION_CS = "http://terminology.hl7.org/CodeSystem/measure-population"


def _deqm_canonical_base() -> str:
    from measure_catalog import DEFAULT_CANONICAL_BASE  # noqa: PLC0415
    return os.getenv("DEQM_CANONICAL_BASE", DEFAULT_CANONICAL_BASE).rstrip("/")


def _deqm_reporter() -> Dict[str, Any]:
    """Build a reporter Reference from env vars (DEQM_REPORTER_REFERENCE / DEQM_REPORTER_DISPLAY)."""
    ref = os.getenv("DEQM_REPORTER_REFERENCE", "").strip()
    display = os.getenv("DEQM_REPORTER_DISPLAY", "").strip()
    reporter: Dict[str, Any] = {}
    if ref:
        reporter["reference"] = ref
    if display:
        reporter["display"] = display
    if not reporter:
        reporter["display"] = "Submitters Stack"
    return reporter


def _deqm_pop(code: str, count: int) -> Dict[str, Any]:
    return {
        "code": {"coding": [{"system": _POPULATION_CS, "code": code}]},
        "count": int(count),
    }


def _deqm_group_resource(cohort_id: str, member_ids: List[str]) -> Dict[str, Any]:
    """Build a minimal FHIR Group describing the cohort."""
    return {
        "resourceType": "Group",
        "id": f"group-{cohort_id}",
        "type": "person",
        "actual": True,
        "quantity": len(member_ids),
        "member": [{"entity": {"reference": f"Patient/{mid}"}} for mid in member_ids],
    }


def _deqm_fhir_bundle(resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": "collection",
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "entry": [{"resource": r} for r in resources if r],
    }


def _build_deqm_fhir_payload(
    summary_payload: Dict[str, Any],
    report_type: str,
) -> Dict[str, Any]:
    """Build a FHIR MeasureReport (or Bundle) from a cohort rollup.

    Parameters
    ----------
    summary_payload:
        Result of ``_aggregate_summary_payload`` — must contain ``measureIds``,
        ``perMeasure``, ``perMember``, ``periodStart``, ``periodEnd``, ``cohort``.
    report_type:
        One of ``"individual"``, ``"subject-list"``, ``"summary"``.

    Returns
    -------
    A FHIR MeasureReport dict (single measure) or a Bundle (multiple measures
    or individual type).
    """
    base = _deqm_canonical_base()
    reporter = _deqm_reporter()
    now_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    cohort = summary_payload.get("cohort") or {}
    cohort_id = cohort.get("id") or "unknown"
    period_start = (summary_payload.get("periodStart") or "").strip()
    period_end = (summary_payload.get("periodEnd") or "").strip()
    per_measure: List[Dict[str, Any]] = summary_payload.get("perMeasure") or []
    per_member: List[Dict[str, Any]] = summary_payload.get("perMember") or []
    member_ids = [m.get("memberId") for m in per_member if m.get("memberId")]

    group_resource = _deqm_group_resource(cohort_id, member_ids)
    group_ref = {"reference": f"#{group_resource['id']}"}
    period_block = {"start": period_start, "end": period_end}

    def _measure_canonical(mid: str) -> str:
        entry = measure_catalog.get_measure_entry(mid) or {}
        version = entry.get("version") or "1"
        return f"{base}/Measure/{mid}|{version}"

    if report_type == "summary":
        reports: List[Dict[str, Any]] = []
        for rollup in per_measure:
            mid = rollup.get("measureId") or ""
            denom = int(rollup.get("denominator") or 0)
            num = int(rollup.get("numerator") or 0)
            patients = int(rollup.get("patients") or 0)
            excl = int(rollup.get("exclusions") or 0)
            perf = rollup.get("performanceRate")

            populations = [
                _deqm_pop("initial-population", patients),
                _deqm_pop("denominator", denom),
                _deqm_pop("numerator", num),
            ]
            if excl:
                populations.append(_deqm_pop("denominator-exclusion", excl))

            group: Dict[str, Any] = {"population": populations}
            if perf is not None:
                try:
                    group["measureScore"] = {"value": float(perf)}
                except (TypeError, ValueError):
                    pass

            report: Dict[str, Any] = {
                "resourceType": "MeasureReport",
                "id": str(uuid.uuid4()),
                "status": "complete",
                "type": "summary",
                "measure": _measure_canonical(mid),
                "subject": group_ref,
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [group],
                "contained": [group_resource],
            }
            reports.append(report)

        if len(reports) == 1:
            return reports[0]
        return _deqm_fhir_bundle(reports)

    if report_type == "subject-list":
        reports = []
        for rollup in per_measure:
            mid = rollup.get("measureId") or ""
            denom = int(rollup.get("denominator") or 0)
            num = int(rollup.get("numerator") or 0)
            patients = int(rollup.get("patients") or 0)
            excl = int(rollup.get("exclusions") or 0)

            measure_canon = _measure_canonical(mid)

            # Build a contained individual MeasureReport for each member.
            contained: List[Dict[str, Any]] = [group_resource]
            indiv_ids: List[str] = []
            for m in per_member:
                m_id = m.get("memberId") or ""
                indiv_id = f"indiv-{cohort_id}-{m_id}-{mid}"
                # Find this member's row for this measure
                m_row = next(
                    (r for r in (m.get("perMeasure") or []) if r.get("measureId") == mid),
                    {},
                )
                m_denom = int(m_row.get("denominator") or 0)
                m_num = int(m_row.get("numerator") or 0)
                m_pops = [
                    _deqm_pop("initial-population", 1),
                    _deqm_pop("denominator", m_denom),
                    _deqm_pop("numerator", m_num),
                ]
                indiv_report: Dict[str, Any] = {
                    "resourceType": "MeasureReport",
                    "id": indiv_id,
                    "status": "complete",
                    "type": "individual",
                    "measure": measure_canon,
                    "subject": {"reference": f"Patient/{m_id}"},
                    "reporter": reporter,
                    "date": now_ts,
                    "period": period_block,
                    "group": [{"population": m_pops}],
                }
                contained.append(indiv_report)
                indiv_ids.append(indiv_id)

            # Build populations with subjectResults pointing at all individual reports.
            sub_results_ref = [{"reference": f"#{iid}"} for iid in indiv_ids]
            sl_pops = [
                {**_deqm_pop("initial-population", patients), "subjectResults": sub_results_ref[0] if sub_results_ref else {}},
                {**_deqm_pop("denominator", denom), "subjectResults": sub_results_ref[0] if sub_results_ref else {}},
                {**_deqm_pop("numerator", num), "subjectResults": sub_results_ref[0] if sub_results_ref else {}},
            ]
            if excl:
                sl_pops.append(_deqm_pop("denominator-exclusion", excl))

            report = {
                "resourceType": "MeasureReport",
                "id": str(uuid.uuid4()),
                "status": "complete",
                "type": "subject-list",
                "measure": measure_canon,
                "subject": group_ref,
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [{"population": sl_pops}],
                "contained": contained,
            }
            reports.append(report)

        if len(reports) == 1:
            return reports[0]
        return _deqm_fhir_bundle(reports)

    # "individual" — one MeasureReport per member per measure, wrapped in a Bundle
    individual_reports: List[Dict[str, Any]] = []
    for rollup in per_measure:
        mid = rollup.get("measureId") or ""
        measure_canon = _measure_canonical(mid)
        for m in per_member:
            m_id = m.get("memberId") or ""
            m_row = next(
                (r for r in (m.get("perMeasure") or []) if r.get("measureId") == mid),
                {},
            )
            m_denom = int(m_row.get("denominator") or 0)
            m_num = int(m_row.get("numerator") or 0)
            m_excl = bool(m_row.get("exclusion"))
            m_pops = [
                _deqm_pop("initial-population", 1),
                _deqm_pop("denominator", m_denom),
                _deqm_pop("numerator", m_num),
            ]
            if m_excl:
                m_pops.append(_deqm_pop("denominator-exclusion", 1))
            individual_reports.append({
                "resourceType": "MeasureReport",
                "id": str(uuid.uuid4()),
                "status": "complete",
                "type": "individual",
                "measure": measure_canon,
                "subject": {"reference": f"Patient/{m_id}"},
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [{"population": m_pops}],
            })

    return _deqm_fhir_bundle(individual_reports)


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

    @router.get("/submissions/{submission_id}")
    async def get_submission(
        submission_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            sub = cohorts_helper.get_doc("submission", submission_id)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        if not sub:
            raise HTTPException(status_code=404, detail=f"submission {submission_id} not found")
        try:
            all_runs = cohorts_helper.list_docs("measurement_execution") or []
        except Exception:
            all_runs = []
        runs = [r for r in all_runs if r.get("submissionId") == submission_id]
        runs.sort(key=lambda r: r.get("createdAt", 0))
        return {"submission": sub, "measurements": runs}

    # ----------------------------------------------------------------------
    # Cross-stack processing endpoint
    # ----------------------------------------------------------------------
    #
    # Called by the providers backend when a clinician clicks "Submit for
    # Processing". Carries the full cohort + each member's FHIR bundle so
    # the submitters stack:
    #   1. Persists the cohort + members + a submission row in its own
    #      Cosmos (dq/cohorts).
    #   2. Dispatches a measure-execution Job (K8s in AKS, in-process
    #      worker in local dev) that iterates each member, calls the
    #      orchestrator's /tools/compute-quality-measures endpoint, and
    #      stores docType=measurement_execution rows back to Cosmos.
    #
    @router.post("/submissions/process")
    async def process_submission(
        payload: SubmissionProcessRequest = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        from measure_runner import (  # local import keeps the module optional
            configure_helper_factory,
            trigger_measure_execution_job,
        )

        configure_helper_factory(lambda: cohorts_helper)

        cohort_in = dict(payload.cohort or {})
        cohort_id = cohort_in.get("id") or _slug(cohort_in.get("name", "")) or f"cohort-{_now_ms()}"
        cohort_in["id"] = cohort_id
        cohort_in.setdefault("docType", "cohort")
        cohort_in.setdefault("createdAt", _now_ms())
        cohort_in["updatedAt"] = _now_ms()
        # Mark the cohort as having been received from the providers stack
        # so the submitters UI can show a "received" badge.
        cohort_in["source"] = payload.sourceStack or cohort_in.get("source") or "providers"
        cohort_in["lastReceivedAt"] = _now_ms()
        if payload.sourceSubmissionId:
            cohort_in["lastReceivedSubmissionId"] = payload.sourceSubmissionId
        try:
            cohorts_helper.upsert_doc("cohort", cohort_id, cohort_in)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"cohort persist failed: {e}")

        persisted_member_ids: List[str] = []
        for m in payload.members:
            member_id = m.id
            if not member_id:
                continue
            member_doc: Dict[str, Any] = {
                "id": member_id,
                "mrn": m.mrn or member_id,
                "displayName": m.displayName,
                "birthDate": m.birthDate,
                "gender": m.gender,
                "patientResourceId": m.patientResourceId,
            }
            if m.bundle:
                member_doc["bundle"] = m.bundle
            try:
                if hasattr(cohorts_helper, "save_patient_data"):
                    cohorts_helper.save_patient_data(member_id, member_doc)
                else:
                    cohorts_helper.upsert_doc("member", member_id, member_doc)
                persisted_member_ids.append(member_id)
            except Exception as exc:  # noqa: BLE001
                # Continue with the rest; the worker will mark this member as skipped.
                print(f"⚠ Failed to persist member {member_id}: {exc}")

        submission_id = payload.sourceSubmissionId or f"sub-{int(time.time() * 1000)}"
        submission_doc: Dict[str, Any] = {
            "id": submission_id,
            "docType": "submission",
            "cohortId": cohort_id,
            "memberIds": persisted_member_ids,
            "measureIds": list(payload.measureIds or []),
            "note": payload.note,
            "sourceStack": payload.sourceStack,
            "periodStart": payload.periodStart,
            "periodEnd": payload.periodEnd,
            "status": "accepted",
            "createdAt": _now_ms(),
        }
        try:
            cohorts_helper.upsert_doc("submission", submission_id, submission_doc)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"submission persist failed: {e}")

        try:
            dispatch_info = trigger_measure_execution_job(submission_id)
        except Exception as exc:  # noqa: BLE001
            dispatch_info = {"mode": "failed", "error": str(exc)}
            try:
                submission_doc["status"] = "dispatch_failed"
                submission_doc["dispatch"] = dispatch_info
                submission_doc["updatedAt"] = _now_ms()
                cohorts_helper.upsert_doc("submission", submission_id, submission_doc)
            except Exception:
                pass

        return {
            "submission": {
                "id": submission_id,
                "cohortId": cohort_id,
                "status": submission_doc["status"],
                "memberCount": len(persisted_member_ids),
                "measureIds": submission_doc["measureIds"],
            },
            "dispatch": dispatch_info,
        }

    # ----------------------------------------------------------------------
    # Measurement evaluation history (cohort-scoped audit trail)
    # ----------------------------------------------------------------------
    @router.get("/cohorts/{cohort_id}/measurement-history")
    async def get_cohort_measurement_history(
        cohort_id: str,
        member_id: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 200,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            from measurement_history import list_measurement_history  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"history module unavailable: {exc}")

        rows = list_measurement_history(
            cohorts_helper,
            cohort_id=cohort_id,
            member_id=member_id,
            limit=max(1, min(int(limit or 200), 1000)),
        )
        if source:
            rows = [r for r in rows if r.get("source") == source]
        return {"cohortId": cohort_id, "history": rows, "count": len(rows)}

    # ----------------------------------------------------------------------
    # Measure summary send (submitters -> receivers + platform)
    # ----------------------------------------------------------------------

    def _measure_meta_for(measure_id: str) -> Dict[str, Any]:
        try:
            doc = catalog_helper.get_doc("measure", measure_id) or {}
        except Exception:  # noqa: BLE001
            doc = {}
        return doc

    def _aggregate_summary_payload(
        cohort_doc: Dict[str, Any],
        member_docs: List[Dict[str, Any]],
        agency_doc: Dict[str, Any],
        program: Optional[Dict[str, Any]],
        measure_ids: List[str],
        period_start: Optional[str],
        period_end: Optional[str],
        note: str,
        engine_hint: Optional[str],
        source_submission_id: Optional[str],
        send_id: str,
    ) -> Dict[str, Any]:
        from measurement_history import list_measurement_history  # noqa: PLC0415

        history_rows = list_measurement_history(
            cohorts_helper,
            cohort_id=cohort_doc.get("id"),
            limit=1000,
        )
        # Pick the latest row per (memberId, measureId) restricted to measure_ids.
        wanted_measure_ids = set(measure_ids) if measure_ids else None
        latest: Dict[tuple, Dict[str, Any]] = {}
        engines_seen: set = set()
        for row in history_rows:
            mid = row.get("memberId")
            meas = row.get("measureId")
            if not mid or not meas:
                continue
            if wanted_measure_ids is not None and meas not in wanted_measure_ids:
                continue
            key = (mid, meas)
            prev = latest.get(key)
            if prev is None or int(row.get("createdAt") or 0) > int(prev.get("createdAt") or 0):
                latest[key] = row
            if row.get("engine"):
                engines_seen.add(str(row.get("engine")))

        # If caller did not specify measureIds, derive them from history.
        if not measure_ids:
            measure_ids = sorted({k[1] for k in latest.keys()})
            wanted_measure_ids = set(measure_ids)

        # Per-member structure (driven by cohort members so we report 0/0 for
        # members with no row yet).
        per_member: List[Dict[str, Any]] = []
        for m in member_docs:
            mid = m.get("id")
            if not mid:
                continue
            rows_for_member: List[Dict[str, Any]] = []
            for meas in measure_ids:
                row = latest.get((mid, meas))
                if row is None:
                    rows_for_member.append({
                        "measureId": meas,
                        "numerator": None,
                        "denominator": None,
                        "exclusion": None,
                    })
                else:
                    rows_for_member.append({
                        "measureId": meas,
                        "numerator": row.get("numerator"),
                        "denominator": row.get("denominator"),
                        "exclusion": row.get("exclusion"),
                    })
            per_member.append({
                "memberId": mid,
                "displayName": m.get("displayName") or m.get("name") or mid,
                "perMeasure": rows_for_member,
            })

        # Per-measure roll-up across the cohort.
        per_measure: List[Dict[str, Any]] = []
        for meas in measure_ids:
            denom = 0
            num = 0
            excl = 0
            patient_count = 0
            for m in member_docs:
                row = latest.get((m.get("id"), meas))
                if row is None:
                    continue
                patient_count += 1
                d = row.get("denominator") or 0
                n = row.get("numerator") or 0
                e = bool(row.get("exclusion"))
                denom += int(d)
                num += int(n)
                if e:
                    excl += 1
            perf = (num / denom) if denom > 0 else None
            meta = _measure_meta_for(meas)
            per_measure.append({
                "measureId": meas,
                "title": meta.get("title") or meas,
                "denominator": denom,
                "numerator": num,
                "exclusions": excl,
                "patients": patient_count,
                "performanceRate": perf,
            })

        engine = engine_hint or (sorted(engines_seen)[0] if engines_seen else None)

        agency_payload = {
            "id": agency_doc.get("id"),
            "name": agency_doc.get("name"),
            "shortName": agency_doc.get("shortName"),
        }
        if program:
            program_payload = {
                "id": program.get("id"),
                "name": program.get("name"),
                "shortName": program.get("shortName"),
            }
            rp = program.get("reportingPeriod") or {}
            if period_start is None:
                period_start = rp.get("start")
            if period_end is None:
                period_end = rp.get("end")
        else:
            program_payload = {"id": None, "name": None, "shortName": None}

        return {
            "id": send_id,
            "sourceStack": "submitters",
            "sourceSendId": send_id,
            "sourceSubmissionId": source_submission_id,
            "agency": agency_payload,
            "program": program_payload,
            "cohort": {
                "id": cohort_doc.get("id"),
                "name": cohort_doc.get("name"),
                "memberCount": len(member_docs),
            },
            "periodStart": period_start,
            "periodEnd": period_end,
            "engine": engine,
            "measureIds": measure_ids,
            "perMeasure": per_measure,
            "perMember": per_member,
            "note": note,
            "generatedAt": _now_ms(),
        }

    def _dispatch_summary_to(name: str, base_url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Allow operators to disable a downstream target by leaving its
        # *_BACKEND_BASE_URL env var blank. The send call then records a
        # "skipped" dispatch row instead of a "failed" one so the overall
        # status remains useful (receivers can succeed even when platform
        # is intentionally off).
        cleaned = (base_url or "").strip()
        if not cleaned:
            return {"target": name, "url": None, "status": "skipped"}
        url = cleaned.rstrip("/") + "/api/workbench/measure-summaries"
        result: Dict[str, Any] = {"target": name, "url": url, "status": "pending"}
        try:
            resp = requests.post(url, json=payload, timeout=30)
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failed"
            result["error"] = str(exc)[:1000]
            return result
        result["statusCode"] = resp.status_code
        if resp.ok:
            result["status"] = "sent"
            try:
                body = resp.json()
                rid = (body.get("summary") or {}).get("id")
                if rid:
                    result["remoteSummaryId"] = rid
            except ValueError:
                pass
        else:
            result["status"] = "rejected"
            result["error"] = resp.text[:1000]
        return result

    def _dispatch_measure_report_to(name: str, base_url: str, fhir_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch a FHIR MeasureReport (or Bundle) to the target stack's ingest route."""
        cleaned = (base_url or "").strip()
        if not cleaned:
            return {"target": name, "url": None, "status": "skipped"}
        url = cleaned.rstrip("/") + "/api/workbench/measure-reports"
        result: Dict[str, Any] = {"target": name, "url": url, "status": "pending"}
        try:
            resp = requests.post(
                url,
                json=fhir_payload,
                headers={"Content-Type": "application/fhir+json"},
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            result["status"] = "failed"
            result["error"] = str(exc)[:1000]
            return result
        result["statusCode"] = resp.status_code
        if resp.ok:
            result["status"] = "sent"
            try:
                body = resp.json()
                rid = (body.get("report") or {}).get("id")
                if rid:
                    result["remoteReportId"] = rid
            except ValueError:
                pass
        else:
            result["status"] = "rejected"
            result["error"] = resp.text[:1000]
        return result

    @router.post("/cohorts/{cohort_id}/measure-reports")
    async def send_cohort_measure_reports(
        cohort_id: str,
        payload: MeasureSummarySendRequest = Body(...),
        reportType: Optional[str] = Query(default=None),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        """Build a DEQM MeasureReport (summary/subject-list/individual) from the cohort
        rollup and dispatch it to the receivers stack.

        Also dispatches the legacy proprietary summary for back-compat.
        """
        # Resolve reportType: query param > body field > env default > "summary"
        rt = (
            reportType
            or payload.reportType
            or os.getenv("DEQM_DEFAULT_REPORT_TYPE", "summary")
        )
        if isinstance(rt, str):
            rt = rt.strip().lower()
        if rt not in _DEQM_VALID_REPORT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid reportType: {rt!r}. Must be one of: {', '.join(sorted(_DEQM_VALID_REPORT_TYPES))}",
            )

        cohort_doc = cohorts_helper.get_doc("cohort", cohort_id)
        if not cohort_doc:
            raise HTTPException(status_code=404, detail=f"cohort not found: {cohort_id}")

        agency_doc = catalog_helper.get_doc("agency", payload.agencyId)
        if not agency_doc:
            raise HTTPException(status_code=404, detail=f"agency not found: {payload.agencyId}")

        program: Optional[Dict[str, Any]] = None
        if payload.programId:
            for p in agency_doc.get("programs") or []:
                if p.get("id") == payload.programId or p.get("shortName") == payload.programId:
                    program = p
                    break
            if program is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"program not found in agency {payload.agencyId}: {payload.programId}",
                )

        member_docs: List[Dict[str, Any]] = []
        for mid in cohort_doc.get("memberIds") or []:
            mdoc: Optional[Dict[str, Any]] = None
            for doc_type in ("member", "patient"):
                try:
                    found = cohorts_helper.get_doc(doc_type, mid)
                except Exception:  # noqa: BLE001
                    found = None
                if found:
                    mdoc = found
                    break
            if mdoc is None:
                mdoc = {"id": mid}
            member_docs.append(mdoc)

        measure_ids = list(payload.measureIds or cohort_doc.get("measureIds") or [])

        send_id = f"mr-send-{_now_ms()}"
        summary_payload = _aggregate_summary_payload(
            cohort_doc=cohort_doc,
            member_docs=member_docs,
            agency_doc=agency_doc,
            program=program,
            measure_ids=measure_ids,
            period_start=payload.periodStart,
            period_end=payload.periodEnd,
            note=payload.note,
            engine_hint=payload.engine,
            source_submission_id=payload.sourceSubmissionId,
            send_id=send_id,
        )

        if not summary_payload.get("measureIds"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "no measureIds resolved for cohort. "
                    "Run Evaluate on the cohort first or pass measureIds in the request body."
                ),
            )

        # Build the FHIR payload from the rollup.
        fhir_payload = _build_deqm_fhir_payload(summary_payload, rt)

        receivers_url = os.getenv("RECEIVERS_BACKEND_BASE_URL", "http://127.0.0.1:8013")
        platform_url = os.getenv("PLATFORM_BACKEND_BASE_URL", "http://127.0.0.1:8014")

        dispatch = {
            # New FHIR route (primary)
            "receivers": _dispatch_measure_report_to("receivers", receivers_url, fhir_payload),
            # Legacy proprietary route (back-compat, platform only)
            "platform": _dispatch_summary_to("platform", platform_url, summary_payload),
        }

        statuses = {v.get("status") for v in dispatch.values()}
        active = statuses - {"skipped"}
        if not active:
            overall = "skipped"
        elif active == {"sent"}:
            overall = "sent"
        elif "sent" in active:
            overall = "partial"
        else:
            overall = "failed"

        audit_doc = {
            "id": send_id,
            "docType": "measure_report_send",
            "cohortId": cohort_id,
            "agencyId": payload.agencyId,
            "programId": payload.programId,
            "measureIds": summary_payload.get("measureIds") or [],
            "periodStart": summary_payload.get("periodStart"),
            "periodEnd": summary_payload.get("periodEnd"),
            "reportType": rt,
            "note": payload.note,
            "status": overall,
            "createdAt": _now_ms(),
            "dispatch": dispatch,
        }
        try:
            cohorts_helper.upsert_doc("measure_report_send", send_id, audit_doc)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to persist measure_report_send %s: %s", send_id, exc)

        return {"send": audit_doc, "reportType": rt}

    @router.get("/cohorts/{cohort_id}/measure-reports/sends")
    async def list_cohort_measure_report_sends(
        cohort_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            rows = cohorts_helper.list_docs("measure_report_send") or []
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        rows = [r for r in rows if r.get("cohortId") == cohort_id]
        rows.sort(key=lambda r: r.get("createdAt", 0), reverse=True)
        return {"cohortId": cohort_id, "sends": rows, "count": len(rows)}

    @router.post("/cohorts/{cohort_id}/measure-summary/send")
    async def send_cohort_measure_summary(
        cohort_id: str,
        payload: MeasureSummarySendRequest = Body(...),
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        cohort_doc = cohorts_helper.get_doc("cohort", cohort_id)
        if not cohort_doc:
            raise HTTPException(status_code=404, detail=f"cohort not found: {cohort_id}")

        agency_doc = catalog_helper.get_doc("agency", payload.agencyId)
        if not agency_doc:
            raise HTTPException(status_code=404, detail=f"agency not found: {payload.agencyId}")

        program: Optional[Dict[str, Any]] = None
        if payload.programId:
            for p in agency_doc.get("programs") or []:
                if p.get("id") == payload.programId or p.get("shortName") == payload.programId:
                    program = p
                    break
            if program is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"program not found in agency {payload.agencyId}: {payload.programId}",
                )

        # Resolve members. Cohort membership is the source of truth — even if
        # no doc exists under docType=member or docType=patient (e.g. seeded
        # patients stored in the cosmos helper's flat patient _store), we fall
        # back to a minimal {id} stub so the rollup loop still visits every id
        # listed in cohort.memberIds and joins it to measurement_history.
        member_docs: List[Dict[str, Any]] = []
        for mid in cohort_doc.get("memberIds") or []:
            mdoc: Optional[Dict[str, Any]] = None
            for doc_type in ("member", "patient"):
                try:
                    found = cohorts_helper.get_doc(doc_type, mid)
                except Exception:  # noqa: BLE001
                    found = None
                if found:
                    mdoc = found
                    break
            if mdoc is None:
                mdoc = {"id": mid}
            member_docs.append(mdoc)

        # Decide measureIds: use request value, else cohort.measureIds, else
        # whatever measurement_history provides.
        measure_ids = list(payload.measureIds or cohort_doc.get("measureIds") or [])

        send_id = f"send-{_now_ms()}"
        summary_payload = _aggregate_summary_payload(
            cohort_doc=cohort_doc,
            member_docs=member_docs,
            agency_doc=agency_doc,
            program=program,
            measure_ids=measure_ids,
            period_start=payload.periodStart,
            period_end=payload.periodEnd,
            note=payload.note,
            engine_hint=payload.engine,
            source_submission_id=payload.sourceSubmissionId,
            send_id=send_id,
        )

        # If history is completely empty, refuse with a helpful 409.
        if not summary_payload.get("measureIds"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "no measureIds resolved for cohort. "
                    "Run Evaluate on the cohort first or pass measureIds in the request body."
                ),
            )

        receivers_url = os.getenv("RECEIVERS_BACKEND_BASE_URL", "http://127.0.0.1:8013")
        platform_url = os.getenv("PLATFORM_BACKEND_BASE_URL", "http://127.0.0.1:8014")

        dispatch = {
            "receivers": _dispatch_summary_to("receivers", receivers_url, summary_payload),
            "platform": _dispatch_summary_to("platform", platform_url, summary_payload),
        }

        statuses = {v.get("status") for v in dispatch.values()}
        # "skipped" targets (e.g. platform when intentionally disabled) are
        # neutral — drop them before computing overall.
        active = statuses - {"skipped"}
        if not active:
            overall = "skipped"
        elif active == {"sent"}:
            overall = "sent"
        elif "sent" in active:
            overall = "partial"
        else:
            overall = "failed"

        audit_doc = {
            "id": send_id,
            "docType": "measure_summary_send",
            "cohortId": cohort_id,
            "agencyId": payload.agencyId,
            "programId": payload.programId,
            "measureIds": summary_payload.get("measureIds") or [],
            "periodStart": summary_payload.get("periodStart"),
            "periodEnd": summary_payload.get("periodEnd"),
            "note": payload.note,
            "status": overall,
            "createdAt": _now_ms(),
            "dispatch": dispatch,
            "summary": summary_payload,
        }
        try:
            cohorts_helper.upsert_doc("measure_summary_send", send_id, audit_doc)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to persist measure_summary_send %s: %s", send_id, exc)

        return {"send": audit_doc}

    @router.get("/cohorts/{cohort_id}/measure-summary/sends")
    async def list_cohort_measure_summary_sends(
        cohort_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        try:
            rows = cohorts_helper.list_docs("measure_summary_send") or []
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=str(e))
        rows = [r for r in rows if r.get("cohortId") == cohort_id]
        rows.sort(key=lambda r: r.get("createdAt", 0), reverse=True)
        return {"cohortId": cohort_id, "sends": rows, "count": len(rows)}

    @router.get("/measure-summary/sends/{send_id}")
    async def get_measure_summary_send(
        send_id: str,
        _user: Dict[str, Any] = Depends(auth_dependency),
    ):
        doc = cohorts_helper.get_doc("measure_summary_send", send_id)
        if not doc:
            raise HTTPException(status_code=404, detail=f"measure_summary_send not found: {send_id}")
        return {"send": doc}

    return router
