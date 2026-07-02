"""
Da Vinci DEQM (Data Exchange for Quality Measures) FHIR surface
================================================================

Implements the minimal, read-mostly FHIR R4 endpoints required by the
accelerator's backend specification (`.speckit/spec_backend.md` §7):

* ``GET  /fhir/metadata``                            — CapabilityStatement
* ``GET  /fhir/Measure``                             — search
* ``GET  /fhir/Measure/{id}``                        — read
* ``GET  /fhir/Library/{id}``                        — read
* ``POST/GET /fhir/Measure/{id}/$data-requirements`` — Gather Data Requirements
* ``POST/GET /fhir/Measure/{id}/$collect-data``      — Collect Data
* ``POST     /fhir/Measure/{id}/$submit-data``       — Submit Data
* ``POST/GET /fhir/Measure/{id}/$evaluate-measure``  — Evaluate Measure

The ``$data-requirements`` operation realizes the "Gather Data Requirements
from Consumer" flow described in the Da Vinci DEQM IG:
https://build.fhir.org/ig/HL7/davinci-deqm/en/datax.html#gather-data-requirements-from-consumer
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse

from measure_catalog import (
    DEFAULT_CANONICAL_BASE,
    build_cql_library_resource,
    build_library_resource,
    build_measure_resource,
    get_measure_entry,
    list_measure_ids,
)

FHIR_MEDIA_TYPE = "application/fhir+json"

VALID_REPORT_TYPES = {"individual", "subject-list", "summary"}


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _canonical_base() -> str:
    return os.getenv("DEQM_CANONICAL_BASE", DEFAULT_CANONICAL_BASE).rstrip("/")


def _reporter_reference() -> Dict[str, Any]:
    """Build a FHIR Reference for the reporter from env vars.

    Reads ``DEQM_REPORTER_REFERENCE`` (e.g. ``Organization/reporter-org-1``)
    and ``DEQM_REPORTER_DISPLAY`` for the human-readable label.
    Falls back to a display-only reference when the env var is absent.
    """
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


def _fhir_response(resource: Dict[str, Any], status_code: int = 200, location: Optional[str] = None) -> JSONResponse:
    headers = {"Content-Type": f"{FHIR_MEDIA_TYPE}; fhirVersion=4.0"}
    if location:
        headers["Location"] = location
    return JSONResponse(status_code=status_code, content=resource, headers=headers)


def _operation_outcome(
    severity: str,
    code: str,
    diagnostics: str,
    status_code: int = 400,
) -> JSONResponse:
    outcome = {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ],
    }
    return _fhir_response(outcome, status_code=status_code)


def _extract_parameter(
    parameters: Optional[Dict[str, Any]],
    name: str,
) -> Optional[Any]:
    """Pull a single named value out of a FHIR ``Parameters`` resource body."""
    if not isinstance(parameters, dict):
        return None
    if parameters.get("resourceType") != "Parameters":
        return None
    for param in parameters.get("parameter") or []:
        if not isinstance(param, dict) or param.get("name") != name:
            continue
        for key in (
            "valueDate",
            "valueDateTime",
            "valueString",
            "valueCode",
            "valueReference",
            "valueBoolean",
            "resource",
        ):
            if key in param:
                return param[key]
    return None


def _coerce_period(
    body: Optional[Dict[str, Any]],
    period_start: Optional[str],
    period_end: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    start = period_start or _extract_parameter(body, "periodStart")
    end = period_end or _extract_parameter(body, "periodEnd")
    if isinstance(start, str):
        start = start.strip() or None
    else:
        start = None
    if isinstance(end, str):
        end = end.strip() or None
    else:
        end = None
    return start, end


def _bundle(resources: List[Dict[str, Any]], bundle_type: str = "collection") -> Dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "id": str(uuid.uuid4()),
        "type": bundle_type,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "entry": [{"resource": r} for r in resources if r],
    }


# -----------------------------------------------------------------------------
# Router factory
# -----------------------------------------------------------------------------
def create_deqm_router(
    *,
    auth_dependency: Callable[..., Any],
    get_patient_bundle: Callable[[str], Optional[Dict[str, Any]]],
    evaluate_measure: Callable[[str, str, Dict[str, Any], Dict[str, bool]], Dict[str, Any]],
    save_submission: Callable[[str, Dict[str, Any]], bool],
    save_measure_report: Callable[[str, str, Dict[str, Any]], bool],
    record_history: Optional[Callable[..., Any]] = None,
) -> APIRouter:
    """Build the DEQM router.

    Parameters
    ----------
    auth_dependency:
        FastAPI dependency that validates the caller's Entra ID JWT
        (reuse the existing ``get_current_user_conditional``).
    get_patient_bundle:
        Callable ``(subject_id) -> patient_document`` returning the stored
        FHIR Bundle / clinical record for a subject, or ``None`` if not found.
    evaluate_measure:
        Callable that runs the orchestrator for a measure and returns the
        engine-level result (the shape already produced by the existing
        ``/api/patient/{id}/measure`` path).
    save_submission:
        Persists the inbound ``$submit-data`` payload. Returns ``True`` on
        success.
    save_measure_report:
        Persists a computed ``MeasureReport``. Returns ``True`` on success.
    record_history:
        Optional keyword-only callable used to append a
        ``measurement_history`` audit row each time ``$evaluate-measure``
        runs from the submitters UI (``source="direct"``). Persistence
        failures should be swallowed by the callable.
    """

    router = APIRouter(prefix="/fhir", tags=["deqm"])

    # -------------------------------------------------------------------------
    # CapabilityStatement
    # -------------------------------------------------------------------------
    @router.get("/metadata")
    async def capability_statement():
        base = _canonical_base()
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cs = {
            "resourceType": "CapabilityStatement",
            "status": "active",
            "date": now,
            "publisher": "Azure Healthcare Digital Quality - Accelerator",
            "kind": "instance",
            "fhirVersion": "4.0.1",
            "format": ["application/fhir+json"],
            "implementation": {"description": "DEQM Producer", "url": base},
            "rest": [
                {
                    "mode": "server",
                    "resource": [
                        {
                            "type": "Measure",
                            "interaction": [{"code": "read"}, {"code": "search-type"}],
                            "operation": [
                                {"name": "data-requirements", "definition": "http://hl7.org/fhir/OperationDefinition/Measure-data-requirements"},
                                {"name": "collect-data",      "definition": "http://hl7.org/fhir/uv/deqm/OperationDefinition/collect-data"},
                                {"name": "submit-data",       "definition": "http://hl7.org/fhir/uv/deqm/OperationDefinition/submit-data"},
                                {"name": "evaluate-measure",  "definition": "http://hl7.org/fhir/OperationDefinition/Measure-evaluate-measure"},
                            ],
                        },
                        {
                            "type": "Library",
                            "interaction": [{"code": "read"}],
                        },
                        {
                            "type": "MeasureReport",
                            "interaction": [{"code": "read"}],
                        },
                    ],
                }
            ],
        }
        return _fhir_response(cs)

    # -------------------------------------------------------------------------
    # Measure read/search
    # -------------------------------------------------------------------------
    @router.get("/Measure")
    async def search_measures(_: Any = Depends(auth_dependency)):
        base = _canonical_base()
        resources = [build_measure_resource(mid, base) for mid in list_measure_ids()]
        bundle = _bundle(resources, bundle_type="searchset")
        bundle["total"] = len(resources)
        return _fhir_response(bundle)

    @router.get("/Measure/{measure_id}")
    async def read_measure(measure_id: str, _: Any = Depends(auth_dependency)):
        resource = build_measure_resource(measure_id, _canonical_base())
        if not resource:
            return _operation_outcome("error", "not-found", f"Measure/{measure_id} not found", 404)
        return _fhir_response(resource)

    # -------------------------------------------------------------------------
    # Library read (returns the CQL library skeleton)
    # -------------------------------------------------------------------------
    @router.get("/Library/{library_id}")
    async def read_library(library_id: str, _: Any = Depends(auth_dependency)):
        base = _canonical_base()
        # Match either the measure's canonical CQL library id or the
        # `{measureId}-data-requirements` helper id.
        if library_id.endswith("-data-requirements"):
            measure_id = library_id[: -len("-data-requirements")]
            resource = build_library_resource(measure_id, base)
            if not resource:
                return _operation_outcome("error", "not-found", f"Library/{library_id} not found", 404)
            return _fhir_response(resource)

        for mid in list_measure_ids():
            entry = get_measure_entry(mid) or {}
            if entry.get("cqlLibrary") == library_id:
                return _fhir_response(build_cql_library_resource(mid, base) or {})
        return _operation_outcome("error", "not-found", f"Library/{library_id} not found", 404)

    # -------------------------------------------------------------------------
    # $data-requirements  (Gather Data Requirements from Consumer)
    # -------------------------------------------------------------------------
    async def _data_requirements(
        measure_id: str,
        period_start: Optional[str],
        period_end: Optional[str],
        body: Optional[Dict[str, Any]],
    ) -> JSONResponse:
        library = build_library_resource(measure_id, _canonical_base())
        if not library:
            return _operation_outcome("error", "not-found", f"Measure/{measure_id} not found", 404)

        start, end = _coerce_period(body, period_start, period_end)
        if start or end:
            library["effectivePeriod"] = {k: v for k, v in {"start": start, "end": end}.items() if v}

        return _fhir_response(library)

    @router.get("/Measure/{measure_id}/$data-requirements")
    async def data_requirements_get(
        measure_id: str,
        periodStart: Optional[str] = Query(default=None),
        periodEnd: Optional[str] = Query(default=None),
        _: Any = Depends(auth_dependency),
    ):
        return await _data_requirements(measure_id, periodStart, periodEnd, None)

    @router.post("/Measure/{measure_id}/$data-requirements")
    async def data_requirements_post(
        measure_id: str,
        body: Dict[str, Any] = Body(default=None),
        periodStart: Optional[str] = Query(default=None),
        periodEnd: Optional[str] = Query(default=None),
        _: Any = Depends(auth_dependency),
    ):
        return await _data_requirements(measure_id, periodStart, periodEnd, body)

    # -------------------------------------------------------------------------
    # $collect-data
    # -------------------------------------------------------------------------
    async def _collect_data(
        measure_id: str,
        subject: Optional[str],
        period_start: Optional[str],
        period_end: Optional[str],
        body: Optional[Dict[str, Any]],
    ) -> JSONResponse:
        if not get_measure_entry(measure_id):
            return _operation_outcome("error", "not-found", f"Measure/{measure_id} not found", 404)

        subject_id = subject or _extract_parameter(body, "subject")
        if not subject_id:
            return _operation_outcome("error", "required", "subject parameter is required", 400)

        # Accept either a bare id or "Patient/{id}"
        if isinstance(subject_id, str) and subject_id.startswith("Patient/"):
            subject_id = subject_id.split("/", 1)[1]

        bundle = get_patient_bundle(str(subject_id))
        if not bundle:
            return _operation_outcome("error", "not-found", f"Subject {subject_id} not found", 404)

        start, end = _coerce_period(body, period_start, period_end)
        base = _canonical_base()

        # A minimal MeasureReport referencing the collected data (DEQM expects
        # $collect-data to return Parameters containing a MeasureReport + the
        # data Bundle).
        report = {
            "resourceType": "MeasureReport",
            "id": str(uuid.uuid4()),
            "status": "complete",
            "type": "data-collection",
            "measure": f"{base}/Measure/{measure_id}",
            "subject": {"reference": f"Patient/{subject_id}"},
            "date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "period": {k: v for k, v in {"start": start, "end": end}.items() if v},
        }

        # Emit the stored bundle as the data payload. If the stored document
        # is itself a Bundle, surface it directly; otherwise wrap it.
        if bundle.get("resourceType") == "Bundle":
            data_bundle = bundle
        else:
            data_bundle = _bundle([bundle])

        parameters = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "measureReport", "resource": report},
                {"name": "resource", "resource": data_bundle},
            ],
        }
        return _fhir_response(parameters)

    @router.get("/Measure/{measure_id}/$collect-data")
    async def collect_data_get(
        measure_id: str,
        subject: Optional[str] = Query(default=None),
        periodStart: Optional[str] = Query(default=None),
        periodEnd: Optional[str] = Query(default=None),
        _: Any = Depends(auth_dependency),
    ):
        return await _collect_data(measure_id, subject, periodStart, periodEnd, None)

    @router.post("/Measure/{measure_id}/$collect-data")
    async def collect_data_post(
        measure_id: str,
        body: Dict[str, Any] = Body(default=None),
        subject: Optional[str] = Query(default=None),
        periodStart: Optional[str] = Query(default=None),
        periodEnd: Optional[str] = Query(default=None),
        _: Any = Depends(auth_dependency),
    ):
        return await _collect_data(measure_id, subject, periodStart, periodEnd, body)

    # -------------------------------------------------------------------------
    # $submit-data
    # -------------------------------------------------------------------------
    @router.post("/Measure/{measure_id}/$submit-data")
    async def submit_data(
        measure_id: str,
        body: Dict[str, Any] = Body(...),
        _: Any = Depends(auth_dependency),
    ):
        if not get_measure_entry(measure_id):
            return _operation_outcome("error", "not-found", f"Measure/{measure_id} not found", 404)
        if not isinstance(body, dict) or body.get("resourceType") != "Parameters":
            return _operation_outcome(
                "error", "invalid",
                "Request body must be a FHIR Parameters resource", 400,
            )

        submission_id = str(uuid.uuid4())
        record = {
            "id": submission_id,
            "measureId": measure_id,
            "receivedAtUtc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "parameters": body,
        }
        try:
            save_submission(measure_id, record)
        except Exception as e:  # noqa: BLE001
            return _operation_outcome("error", "processing", f"Failed to persist submission: {e}", 500)

        base = _canonical_base()
        location = f"{base}/MeasureReport/{submission_id}"
        return _fhir_response(
            {
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "information",
                        "code": "informational",
                        "diagnostics": f"Accepted submission {submission_id} for Measure/{measure_id}",
                    }
                ],
            },
            status_code=201,
            location=location,
        )

    # -------------------------------------------------------------------------
    # $evaluate-measure
    # -------------------------------------------------------------------------
    def _derive_evaluation_note(
        measure_id: str,
        chosen: Dict[str, Any],
        engine_result: Any,
    ) -> Tuple[str, List[str], List[Any]]:
        """Build a short human-readable note plus evidence/gap arrays.

        Returns ``(note, evidence_trace, gaps_in_care)`` suitable for surfacing
        in MeasureReport extensions so clients can render a "Notes" column
        without re-parsing the contained explainability payload.
        """
        evidence_trace: List[str] = []
        gaps_in_care: List[Any] = []
        per_measure: Dict[str, Any] = {}
        if isinstance(engine_result, dict):
            measures = engine_result.get("measures") or []
            if isinstance(measures, list):
                for m in measures:
                    if not isinstance(m, dict):
                        continue
                    if str(m.get("measure_id") or m.get("measureId") or "").lower() == str(measure_id).lower():
                        per_measure = m
                        break
                if not per_measure and measures and isinstance(measures[0], dict):
                    per_measure = measures[0]
            trace = per_measure.get("evidence_trace") if isinstance(per_measure, dict) else None
            if isinstance(trace, list):
                evidence_trace = [str(t) for t in trace if t is not None]
            gaps = (chosen or {}).get("gapsInCare") if isinstance(chosen, dict) else None
            if isinstance(gaps, list):
                gaps_in_care = gaps

        # Build a single-sentence summary from the chosen-engine population
        # counts. Avoid leaking patient identifiers; callers can drill into
        # contained.engine-payload for full context.
        try:
            denom = int((chosen or {}).get("inDenominator", 0) or 0)
            num = int((chosen or {}).get("controlled", 0) or 0)
            evald = int((chosen or {}).get("measuresEvaluated", 0) or 0)
        except (TypeError, ValueError):
            denom = num = evald = 0

        if evald == 0:
            note = "Not evaluated — measure did not run for this subject."
        elif denom == 0:
            note = "Excluded — patient did not meet initial population / denominator criteria."
        elif num > 0:
            note = "In numerator — measure satisfied."
        else:
            note = "In denominator, not in numerator — gap in care."

        # Append the first one or two evidence items for quick context.
        if evidence_trace:
            head = "; ".join(evidence_trace[:3])
            note = f"{note} {head}"
        return note[:480], evidence_trace, gaps_in_care

    def _resolve_engines(body: Optional[Dict[str, Any]], engine_query: Optional[str]) -> Dict[str, bool]:
        engine = engine_query or _extract_parameter(body, "engine")
        if isinstance(engine, str):
            engine = engine.strip().lower()
        if engine == "ai-cql":
            return {"useNative": False, "useAi": True}
        if engine == "native-cql":
            return {"useNative": True, "useAi": False}
        # Default: native on, AI off (matches spec §7bnative/AI toggle defaults)
        return {"useNative": True, "useAi": False}

    async def _evaluate(
        measure_id: str,
        subject: Optional[str],
        period_start: Optional[str],
        period_end: Optional[str],
        body: Optional[Dict[str, Any]],
        engine_query: Optional[str],
        cohort_id: Optional[str] = None,
        report_type: Optional[str] = None,
    ) -> JSONResponse:
        # Resolve reportType: query param > body parameter > env default > "individual"
        rt = (
            report_type
            or _extract_parameter(body, "reportType")
            or os.getenv("DEQM_DEFAULT_REPORT_TYPE", "individual")
        )
        if isinstance(rt, str):
            rt = rt.strip().lower()
        if rt not in VALID_REPORT_TYPES:
            return _operation_outcome(
                "error", "value",
                f"Invalid reportType: {rt!r}. Must be one of: {', '.join(sorted(VALID_REPORT_TYPES))}",
                400,
            )

        entry = get_measure_entry(measure_id)
        if not entry:
            return _operation_outcome("error", "not-found", f"Measure/{measure_id} not found", 404)

        subject_id = subject or _extract_parameter(body, "subject")
        if not subject_id:
            return _operation_outcome("error", "required", "subject parameter is required for $evaluate-measure", 400)
        if isinstance(subject_id, str) and subject_id.startswith("Patient/"):
            subject_id = subject_id.split("/", 1)[1]

        start, end = _coerce_period(body, period_start, period_end)
        if not start or not end:
            return _operation_outcome(
                "error", "required",
                "periodStart and periodEnd are required for $evaluate-measure", 400,
            )

        engines = _resolve_engines(body, engine_query)
        engine_label = "ai-cql" if engines.get("useAi") and not engines.get("useNative") else "native-cql"
        cohort_id = cohort_id or _extract_parameter(body, "cohortId") or _extract_parameter(body, "cohort")

        def _emit_history(
            status: str,
            *,
            http_status: Optional[int],
            numerator: Optional[int] = None,
            denominator: Optional[int] = None,
            exclusion: Optional[bool] = None,
            note: Optional[str] = None,
            error: Optional[str] = None,
            report_id: Optional[str] = None,
        ) -> None:
            if record_history is None:
                return
            try:
                record_history(
                    source="direct",
                    cohort_id=cohort_id if isinstance(cohort_id, str) and cohort_id else None,
                    member_id=str(subject_id),
                    measure_id=measure_id,
                    engine=engine_label,
                    submission_id=None,
                    source_stack="submitters",
                    status=status,
                    http_status=http_status,
                    numerator=numerator,
                    denominator=denominator,
                    exclusion=exclusion,
                    note=note,
                    error=error,
                    report_id=report_id,
                )
            except Exception:  # noqa: BLE001
                pass

        try:
            engine_result = evaluate_measure(
                measure_id,
                str(subject_id),
                {"periodStart": start, "periodEnd": end},
                engines,
            )
        except LookupError:
            _emit_history("failed", http_status=404, error=f"Subject {subject_id} not found")
            return _operation_outcome("error", "not-found", f"Subject {subject_id} not found", 404)
        except Exception as e:  # noqa: BLE001
            _emit_history("failed", http_status=500, error=str(e)[:500])
            return _operation_outcome("error", "processing", f"Evaluation failed: {e}", 500)

        base = _canonical_base()
        report_id = str(uuid.uuid4())

        # Map engine output to a FHIR MeasureReport. The engine result already
        # carries summary.native / summary.ai plus per-measure detail; we pick
        # the highest-priority group that ran.
        summary = (engine_result.get("summary") or {}) if isinstance(engine_result, dict) else {}
        chosen = summary.get("native") or summary.get("ai") or {}
        populations: List[Dict[str, Any]] = []

        def _pop(code: str, count: Any) -> Dict[str, Any]:
            return {
                "code": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/measure-population",
                        "code": code,
                    }]
                },
                "count": int(count) if isinstance(count, (int, float)) else 0,
            }

        if chosen:
            populations.append(_pop("initial-population", chosen.get("measuresEvaluated", 0)))
            populations.append(_pop("denominator", chosen.get("inDenominator", 0)))
            populations.append(_pop("numerator", chosen.get("controlled", 0)))

        # Derive a short, human-readable explanation for UI consumption and
        # collect the per-measure evidence trace + gaps-in-care from the
        # accelerator engine result. These are surfaced as MeasureReport
        # extensions so clients can render "Notes" without re-parsing the
        # contained explainability payload.
        evaluation_note, evidence_trace, gaps_in_care = _derive_evaluation_note(
            measure_id, chosen, engine_result
        )

        reporter = _reporter_reference()
        now_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        measure_canonical = f"{base}/Measure/{measure_id}|{entry['version']}"
        period_block = {"start": start, "end": end}

        # ---- Build the chosen report shape ----------------------------------

        if rt == "summary":
            # DEQM Summary MeasureReport: aggregate counts, no per-subject detail.
            # For a single-subject evaluation the counts are 0 or 1.
            group: Dict[str, Any] = {"population": populations}
            try:
                denom_n = int((chosen or {}).get("inDenominator", 0) or 0)
                num_n = int((chosen or {}).get("controlled", 0) or 0)
                perf: Optional[float] = (num_n / denom_n) if denom_n > 0 else None
            except (TypeError, ValueError, ZeroDivisionError):
                perf = None
            if perf is not None:
                group["measureScore"] = {"value": perf}

            group_resource: Dict[str, Any] = {
                "resourceType": "Group",
                "id": f"group-{subject_id}",
                "type": "person",
                "actual": True,
                "quantity": 1,
                "member": [{"entity": {"reference": f"Patient/{subject_id}"}}],
            }
            report = {
                "resourceType": "MeasureReport",
                "id": report_id,
                "status": "complete",
                "type": "summary",
                "measure": measure_canonical,
                "subject": {"reference": f"#group-{subject_id}"},
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [group],
                "contained": [group_resource],
            }

        elif rt == "subject-list":
            # DEQM Subject List MeasureReport: group with population + subjectResults
            # pointing at contained individual reports.
            indiv_id = f"indiv-{report_id}"
            indiv_report: Dict[str, Any] = {
                "resourceType": "MeasureReport",
                "id": indiv_id,
                "status": "complete",
                "type": "individual",
                "measure": measure_canonical,
                "subject": {"reference": f"Patient/{subject_id}"},
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [{"population": populations}] if populations else [],
            }
            group_resource = {
                "resourceType": "Group",
                "id": f"group-{subject_id}",
                "type": "person",
                "actual": True,
                "quantity": 1,
                "member": [{"entity": {"reference": f"Patient/{subject_id}"}}],
            }
            # Build population entries with subjectResults referencing the individual report.
            sl_populations: List[Dict[str, Any]] = []
            for pop in populations:
                sl_pop = dict(pop)
                sl_pop["subjectResults"] = {"reference": f"#{indiv_id}"}
                sl_populations.append(sl_pop)

            report = {
                "resourceType": "MeasureReport",
                "id": report_id,
                "status": "complete",
                "type": "subject-list",
                "measure": measure_canonical,
                "subject": {"reference": f"#group-{subject_id}"},
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [{"population": sl_populations}] if sl_populations else [],
                "contained": [group_resource, indiv_report],
            }

        else:
            # "individual" (default) — existing shape + reporter.
            engine_ext: List[Dict[str, Any]] = [
                {
                    "url": f"{base}/StructureDefinition/engine-result",
                    "valueString": "native" if engines["useNative"] else "ai",
                },
                {
                    "url": f"{base}/StructureDefinition/evaluation-note",
                    "valueString": evaluation_note,
                },
            ]
            if evidence_trace:
                engine_ext.append({
                    "url": f"{base}/StructureDefinition/evidence-trace",
                    "valueString": json.dumps(evidence_trace),
                })
            if gaps_in_care:
                engine_ext.append({
                    "url": f"{base}/StructureDefinition/gaps-in-care",
                    "valueString": json.dumps(gaps_in_care),
                })
            try:
                payload_str = json.dumps(engine_result, default=str)
            except Exception:  # noqa: BLE001
                payload_str = str(engine_result)

            report = {
                "resourceType": "MeasureReport",
                "id": report_id,
                "status": "complete",
                "type": "individual",
                "measure": measure_canonical,
                "subject": {"reference": f"Patient/{subject_id}"},
                "reporter": reporter,
                "date": now_ts,
                "period": period_block,
                "group": [{"population": populations}] if populations else [],
                "extension": engine_ext,
                "contained": [
                    {
                        "resourceType": "Basic",
                        "id": "engine-payload",
                        "code": {"text": "engine-result"},
                        "extension": [
                            {
                                "url": f"{base}/StructureDefinition/engine-payload",
                                "valueString": payload_str[:32000],
                            }
                        ],
                    }
                ],
            }

        try:
            save_measure_report(str(subject_id), report_id, report)
        except Exception as e:  # noqa: BLE001
            # Persistence failure should not drop the computed result.
            report.setdefault("extension", []).append(
                {
                    "url": f"{base}/StructureDefinition/persistence-warning",
                    "valueString": f"Failed to persist MeasureReport: {e}",
                }
            )

        try:
            num = int((chosen or {}).get("controlled", 0) or 0)
            den = int((chosen or {}).get("inDenominator", 0) or 0)
        except (TypeError, ValueError):
            num = den = 0
        _emit_history(
            "completed",
            http_status=200,
            numerator=num,
            denominator=den,
            note=evaluation_note if rt == "individual" else f"[{rt}] {evaluation_note}",
            report_id=report_id,
        )

        return _fhir_response(report)

    @router.get("/Measure/{measure_id}/$evaluate-measure")
    async def evaluate_measure_get(
        measure_id: str,
        subject: Optional[str] = Query(default=None),
        periodStart: Optional[str] = Query(default=None),
        periodEnd: Optional[str] = Query(default=None),
        engine: Optional[str] = Query(default=None),
        cohortId: Optional[str] = Query(default=None),
        reportType: Optional[str] = Query(default=None),
        _: Any = Depends(auth_dependency),
    ):
        return await _evaluate(measure_id, subject, periodStart, periodEnd, None, engine, cohortId, reportType)

    @router.post("/Measure/{measure_id}/$evaluate-measure")
    async def evaluate_measure_post(
        measure_id: str,
        body: Dict[str, Any] = Body(default=None),
        subject: Optional[str] = Query(default=None),
        periodStart: Optional[str] = Query(default=None),
        periodEnd: Optional[str] = Query(default=None),
        engine: Optional[str] = Query(default=None),
        cohortId: Optional[str] = Query(default=None),
        reportType: Optional[str] = Query(default=None),
        _: Any = Depends(auth_dependency),
    ):
        return await _evaluate(measure_id, subject, periodStart, periodEnd, body, engine, cohortId, reportType)

    return router
