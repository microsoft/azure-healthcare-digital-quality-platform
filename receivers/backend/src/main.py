"""
Backend API

This FastAPI application provides endpoints for managing patient data and computing
digital quality measures (CMS eCQM / DEQM). It integrates with:
- Azure Cosmos DB for patient data storage
- The digital quality orchestrator for CQL / AI-driven measure computation
- OpenAI / Azure OpenAI for AI-powered summarization
- Application Insights for telemetry and monitoring
"""

import os
import json
from pathlib import Path
from datetime import date, datetime
from time import perf_counter
# FastAPI framework and dependencies for building REST API
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
# Environment variable management
from dotenv import load_dotenv
# Prompty framework for AI prompt management and tracing
from prompty.tracer import trace
from prompty.core import PromptyStream, AsyncPromptyStream
# FastAPI response types and middleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
# OpenTelemetry instrumentation for monitoring
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from fastapi import FastAPI, Body
# Pydantic for data validation
from pydantic import BaseModel
# URL encoding for database connection strings
from urllib.parse import quote_plus
import requests
# Custom modules for database operations
import cosmosdb_helper
from receiver_reporting import ReceiverReportingSink

reportingSink: ReceiverReportingSink
from auth_middleware import get_current_user, get_current_user_from_request, require_auth, get_user_from_request, extract_token_from_request, user_has_role
from typing import Dict, Any, List, Optional


def _safe_get(d: Any, path: List[Any], default: Any = None) -> Any:
    current = d
    for key in path:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return default
        if current is None:
            return default
    return current


def _format_human_name(name_obj: Dict[str, Any] | None) -> str:
    if not name_obj:
        return ""
    given = name_obj.get("given") or []
    family = name_obj.get("family") or ""
    if isinstance(given, list):
        given_name = " ".join(str(part) for part in given if part)
    else:
        given_name = str(given)
    return " ".join(part for part in [given_name, family] if part).strip()


def _calculate_age_from_birth_date(birth_date: str | None) -> Optional[int]:
    if not birth_date:
        return None
    try:
        dob = date.fromisoformat(birth_date)
        today = date.today()
        age = today.year - dob.year
        if (today.month, today.day) < (dob.month, dob.day):
            age -= 1
        return age
    except Exception:
        return None


def _coding_label(codable: Dict[str, Any] | None) -> str:
    if not codable:
        return ""
    coding = codable.get("coding") or []
    if isinstance(coding, list) and coding:
        first = coding[0] or {}
        return first.get("display") or first.get("code") or ""
    return codable.get("text") or ""


def _coding_code(codable: Dict[str, Any] | None) -> str:
    if not codable:
        return ""
    coding = codable.get("coding") or []
    if isinstance(coding, list) and coding:
        first = coding[0] or {}
        return first.get("code") or ""
    return ""


def _extract_fhir_view(patient_blob: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patient_blob, dict):
        return {
            "hasFhirBundle": False,
            "resourceCounts": {},
            "patient": {},
            "encounters": [],
            "conditions": [],
            "observations": [],
            "procedures": [],
            "coverages": [],
            "latestBloodPressure": None,
        }

    # The Workbench seeds member docs as ``{"bundle": <FHIR Bundle>, "mrn": ..., ...}``
    # so callers that look up a patient via the cohorts container receive a
    # wrapper, not a raw Bundle. Unwrap so the rest of the extraction logic
    # works for both shapes.
    nested_bundle = patient_blob.get("bundle") if isinstance(patient_blob.get("bundle"), dict) else None
    if nested_bundle and nested_bundle.get("resourceType") == "Bundle":
        # Preserve the outer mrn/displayName for downstream metadata, but use
        # the inner Bundle as the source of FHIR resources.
        source = dict(nested_bundle)
        source.setdefault("mrn", patient_blob.get("mrn") or patient_blob.get("id"))
        patient_blob = source

    is_bundle = patient_blob.get("resourceType") == "Bundle"
    entries = patient_blob.get("entry") if is_bundle else []
    resources: List[Dict[str, Any]] = []
    for entry in entries or []:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if isinstance(resource, dict):
            resources.append(resource)

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for resource in resources:
        resource_type = resource.get("resourceType")
        if not resource_type:
            continue
        by_type.setdefault(resource_type, []).append(resource)

    patient_resource = _safe_get(by_type, ["Patient", 0], {}) or {}
    patient_name = _format_human_name(_safe_get(patient_resource, ["name", 0], {}))
    patient_mrn = _safe_get(patient_resource, ["identifier", 0, "value"], "")
    birth_date = patient_resource.get("birthDate")
    patient_info = {
        "id": patient_resource.get("id") or patient_blob.get("mrn"),
        "mrn": patient_blob.get("mrn") or patient_mrn,
        "name": patient_name,
        "gender": patient_resource.get("gender"),
        "birthDate": birth_date,
        "age": _calculate_age_from_birth_date(birth_date),
    }

    encounters: List[Dict[str, Any]] = []
    for encounter in by_type.get("Encounter", []):
        encounters.append(
            {
                "id": encounter.get("id"),
                "status": encounter.get("status"),
                "class": _safe_get(encounter, ["class", "display"], ""),
                "type": _coding_label(_safe_get(encounter, ["type", 0], {})),
                "start": _safe_get(encounter, ["period", "start"]),
                "end": _safe_get(encounter, ["period", "end"]),
            }
        )

    conditions: List[Dict[str, Any]] = []
    for condition in by_type.get("Condition", []):
        conditions.append(
            {
                "id": condition.get("id"),
                "code": _coding_label(condition.get("code")),
                "codeValue": _coding_code(condition.get("code")),
                "clinicalStatus": _coding_label(condition.get("clinicalStatus")),
                "verificationStatus": _coding_label(condition.get("verificationStatus")),
                "onset": condition.get("onsetDateTime"),
            }
        )

    observations: List[Dict[str, Any]] = []
    bp_readings: List[Dict[str, Any]] = []
    for observation in by_type.get("Observation", []):
        code_value = _coding_code(observation.get("code"))
        effective = observation.get("effectiveDateTime")
        systolic = None
        diastolic = None

        if code_value == "85354-9":
            for component in observation.get("component") or []:
                component_code = _coding_code(component.get("code"))
                if component_code == "8480-6":
                    systolic = _safe_get(component, ["valueQuantity", "value"])
                elif component_code == "8462-4":
                    diastolic = _safe_get(component, ["valueQuantity", "value"])
        elif code_value == "8480-6":
            systolic = _safe_get(observation, ["valueQuantity", "value"])
        elif code_value == "8462-4":
            diastolic = _safe_get(observation, ["valueQuantity", "value"])

        observations.append(
            {
                "id": observation.get("id"),
                "status": observation.get("status"),
                "code": _coding_label(observation.get("code")),
                "codeValue": code_value,
                "effectiveDateTime": effective,
                "systolic": systolic,
                "diastolic": diastolic,
            }
        )

        if systolic is not None or diastolic is not None:
            bp_readings.append(
                {
                    "effectiveDateTime": effective,
                    "systolic": systolic,
                    "diastolic": diastolic,
                }
            )

    def _effective_sort_key(reading: Dict[str, Any]) -> datetime:
        raw = reading.get("effectiveDateTime")
        if not raw:
            return datetime.min
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return datetime.min

    latest_bp = max(bp_readings, key=_effective_sort_key) if bp_readings else None

    procedures: List[Dict[str, Any]] = []
    for procedure in by_type.get("Procedure", []):
        procedures.append(
            {
                "id": procedure.get("id"),
                "status": procedure.get("status"),
                "code": _coding_label(procedure.get("code")),
                "codeValue": _coding_code(procedure.get("code")),
                "performedDateTime": procedure.get("performedDateTime"),
            }
        )

    coverages: List[Dict[str, Any]] = []
    for coverage in by_type.get("Coverage", []):
        payors = [
            payor.get("display")
            for payor in (coverage.get("payor") or [])
            if isinstance(payor, dict) and payor.get("display")
        ]
        coverages.append(
            {
                "id": coverage.get("id"),
                "status": coverage.get("status"),
                "type": _coding_label(coverage.get("type")),
                "payors": payors,
                "start": _safe_get(coverage, ["period", "start"]),
                "end": _safe_get(coverage, ["period", "end"]),
            }
        )

    return {
        "hasFhirBundle": is_bundle,
        "resourceCounts": {key: len(value) for key, value in by_type.items()},
        "patient": patient_info,
        "encounters": encounters,
        "conditions": conditions,
        "observations": observations,
        "procedures": procedures,
        "coverages": coverages,
        "latestBloodPressure": latest_bp,
    }


def _has_hypertension_condition(conditions: List[Dict[str, Any]]) -> bool:
    for condition in conditions:
        code_value = str(condition.get("codeValue") or "")
        label = str(condition.get("code") or "").lower()
        if code_value in ("59621000", "38341003"):
            return True
        if "hypertension" in label:
            return True
    return False


def _has_dialysis_or_kidney_transplant(procedures: List[Dict[str, Any]]) -> bool:
    for procedure in procedures:
        label = str(procedure.get("code") or "").lower()
        if "dialysis" in label or "kidney transplant" in label:
            return True
    return False


def _evaluate_bp_measurement(fhir_view: Dict[str, Any], mode: str) -> Dict[str, Any]:
    start = perf_counter()
    normalized_mode = (mode or "non-cql").strip().lower()
    if normalized_mode not in ("cql", "non-cql"):
        normalized_mode = "non-cql"

    latest_bp = fhir_view.get("latestBloodPressure") or {}
    systolic = latest_bp.get("systolic")
    diastolic = latest_bp.get("diastolic")

    bp_available = systolic is not None and diastolic is not None
    bp_controlled = bool(bp_available and systolic < 140 and diastolic < 90)

    result: Dict[str, Any] = {
        "mode": normalized_mode,
        "measureId": "CMS165v9",
        "measureName": "Controlling High Blood Pressure",
        "bpAvailable": bp_available,
        "systolic": systolic,
        "diastolic": diastolic,
    }

    if normalized_mode == "non-cql":
        result.update(
            {
                "status": "meets-measure" if bp_controlled else "does-not-meet-measure",
                "bpControlled": bp_controlled,
                "explanation": "Direct threshold evaluation without CQL denominator/exclusion logic.",
            }
        )
    else:
        encounters = fhir_view.get("encounters") or []
        conditions = fhir_view.get("conditions") or []
        procedures = fhir_view.get("procedures") or []

        has_qualifying_encounter = len(encounters) > 0
        has_hypertension = _has_hypertension_condition(conditions)
        in_denominator = has_qualifying_encounter and has_hypertension
        excluded = _has_dialysis_or_kidney_transplant(procedures)
        in_numerator = bool(in_denominator and not excluded and bp_controlled)

        if excluded:
            status = "excluded"
        elif not in_denominator:
            status = "not-in-denominator"
        elif in_numerator:
            status = "meets-measure"
        else:
            status = "does-not-meet-measure"

        result.update(
            {
                "status": status,
                "bpControlled": bp_controlled,
                "denominator": in_denominator,
                "numerator": in_numerator,
                "exclusion": excluded,
                "evaluation": {
                    "hasQualifyingEncounter": has_qualifying_encounter,
                    "hasHypertensionDiagnosis": has_hypertension,
                    "hasDialysisOrKidneyTransplant": excluded,
                },
                "explanation": "CQL-like measure logic including denominator, exclusion, and numerator evaluation.",
            }
        )

    result["executionTimeMs"] = round((perf_counter() - start) * 1000, 2)
    return result


def _evaluate_bp_measurement_via_orchestrator(
    patient_id: str,
    patient_blob: Dict[str, Any],
    fhir_view: Dict[str, Any],
    use_native_cql_engine: bool,
    use_ai_cql_engine: bool,
    *,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    measure_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Delegate quality-measure evaluation to the digital-quality-orchestrator pod via
    in-cluster Kubernetes DNS.

    The backend executes the native and AI engines independently (based on toggles)
    so the frontend can render side-by-side summaries.
    """
    if not use_native_cql_engine and not use_ai_cql_engine:
        return {
            "status": "no-engine-selected",
            "executionSource": "none",
            "engines": {"native": None, "ai": None},
            "summary": {
                "native": None,
                "ai": None,
                "combined": {
                    "measuresEvaluated": 0,
                    "controlled": 0,
                    "inDenominator": 0,
                    "gapsInCare": [],
                },
            },
            "orchestratorErrors": [],
        }

    service_base_url = os.getenv(
        "DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL",
        "http://orchestrator.dq.svc.cluster.local",
    ).rstrip("/")
    measure_endpoint = os.getenv(
        "DIGITAL_QUALITY_ORCHESTRATOR_QUALITY_ENDPOINT",
        "/tools/compute-quality-measures",
    )
    default_timeout_seconds = float(os.getenv("DIGITAL_QUALITY_ORCHESTRATOR_TIMEOUT_SECONDS", "12"))
    native_timeout_seconds = float(
        os.getenv("DIGITAL_QUALITY_ORCHESTRATOR_NATIVE_TIMEOUT_SECONDS", str(default_timeout_seconds))
    )
    ai_timeout_seconds = float(
        os.getenv("DIGITAL_QUALITY_ORCHESTRATOR_AI_TIMEOUT_SECONDS", "180")
    )

    remote_errors: List[Dict[str, Any]] = []
    full_url = f"{service_base_url}{measure_endpoint if str(measure_endpoint).startswith('/') else '/' + str(measure_endpoint)}"

    measure_ids = measure_ids or ["CMS122v11", "CMS165v9", "ePC02v1"]
    base_payload: Dict[str, Any] = {
        "patient_id": patient_id,
        "measurement_period_start": period_start or os.getenv("QUALITY_MEASUREMENT_PERIOD_START", "2025-01-01"),
        "measurement_period_end": period_end or os.getenv("QUALITY_MEASUREMENT_PERIOD_END", "2025-12-31"),
        "measures": measure_ids,
    }

    nested_bundle = patient_blob.get("bundle") if isinstance(patient_blob.get("bundle"), dict) else None
    if patient_blob.get("resourceType") == "Bundle":
        base_payload["fhir_bundle"] = patient_blob
    elif nested_bundle and nested_bundle.get("resourceType") == "Bundle":
        # Member docs are stored as {"bundle": <FHIR Bundle>, "mrn": ...}. Send the
        # raw Bundle so the orchestrator sees proper FHIR codings (required for CQL
        # value-set matching) instead of the flattened fhir_view shape.
        base_payload["fhir_bundle"] = nested_bundle
    else:
        base_payload.update(
            {
                "patient": fhir_view.get("patient"),
                "encounters": fhir_view.get("encounters", []),
                "conditions": fhir_view.get("conditions", []),
                "observations": fhir_view.get("observations", []),
                "procedures": fhir_view.get("procedures", []),
                "coverages": fhir_view.get("coverages", []),
            }
        )

    def _call_engine(
        engine_name: str,
        native_on: bool,
        ai_on: bool,
        timeout_seconds: float,
    ) -> Optional[Dict[str, Any]]:
        payload = {
            **base_payload,
            "use_native_cql_engine": native_on,
            "use_ai_cql_engine": ai_on,
        }
        try:
            response = requests.post(full_url, json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            body = response.json() if response.content else {}
            if not isinstance(body, dict):
                raise ValueError("Unexpected orchestrator response format")
            return body
        except Exception as e:
            remote_errors.append({"url": full_url, "engine": engine_name, "error": str(e)})
            return None

    native_report = (
        _call_engine("native", True, False, native_timeout_seconds)
        if use_native_cql_engine
        else None
    )
    ai_report = (
        _call_engine("ai", False, True, ai_timeout_seconds)
        if use_ai_cql_engine
        else None
    )

    def _engine_summary(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not report:
            return None
        summary = report.get("summary") or {}
        return {
            "measuresEvaluated": summary.get("measures_evaluated", len(report.get("measures", []))),
            "inDenominator": summary.get("in_denominator", 0),
            "controlled": summary.get("controlled", 0),
            "gapsInCare": summary.get("gaps_in_care", []),
        }

    native_summary = _engine_summary(native_report)
    ai_summary = _engine_summary(ai_report)

    combined_gaps = set()
    total_measures = 0
    total_controlled = 0
    total_denominator = 0

    for engine_summary in (native_summary, ai_summary):
        if not engine_summary:
            continue
        total_measures += int(engine_summary.get("measuresEvaluated", 0))
        total_controlled += int(engine_summary.get("controlled", 0))
        total_denominator += int(engine_summary.get("inDenominator", 0))
        for gap in engine_summary.get("gapsInCare", []):
            combined_gaps.add(str(gap))

    return {
        "status": "completed" if (native_report or ai_report) else "failed",
        "mode": "engine-toggles",
        "executionSource": "digital-quality-orchestrator" if (native_report or ai_report) else "none",
        "engines": {
            "native": native_report,
            "ai": ai_report,
        },
        "summary": {
            "native": native_summary,
            "ai": ai_summary,
            "combined": {
                "measuresEvaluated": total_measures,
                "controlled": total_controlled,
                "inDenominator": total_denominator,
                "gapsInCare": sorted(combined_gaps),
            },
        },
        "orchestratorErrors": remote_errors,
    }


def _infer_patient_id_from_payload(patient_payload: Any) -> Optional[str]:
    # Catalog files (patients.json, cohorts.json, measures.json, etc.) are top-level lists,
    # not patient bundles. Skip anything that isn't a dict.
    if not isinstance(patient_payload, dict):
        return None

    mrn = patient_payload.get("mrn")
    if mrn:
        return str(mrn)

    if patient_payload.get("resourceType") != "Bundle":
        return None

    entries = patient_payload.get("entry") or []
    for entry in entries:
        resource = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(resource, dict) or resource.get("resourceType") != "Patient":
            continue
        # Prefer resource.id (e.g. "p-cms122-001") so the stored key matches what the frontend searches
        resource_id = resource.get("id")
        if resource_id:
            return str(resource_id)
        # Fall back to identifier.value if resource.id is absent
        for identifier in resource.get("identifier") or []:
            if not isinstance(identifier, dict):
                continue
            value = identifier.get("value")
            if value:
                return str(value)
    return None


class FileBackedCosmosDBHelper:
    """In-memory helper backed by local sample JSON bundles for non-Cosmos environments."""

    def __init__(self, data_dir: Path):
        self._store: Dict[str, Dict[str, Any]] = {}
        # Per-docType in-memory store for the workbench (catalog/cohorts).
        self._doc_store: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._load_from_data_dir(data_dir)

    # ------------------------------------------------------------------
    # Generic doc-type helpers (mirror CosmosDBHelper.upsert_doc/get_doc/
    # list_docs/delete_doc) so the workbench router works without Cosmos.
    # ------------------------------------------------------------------

    def upsert_doc(self, doc_type: str, item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        bucket = self._doc_store.setdefault(doc_type, {})
        doc = dict(payload)
        doc["id"] = item_id
        doc["docType"] = doc_type
        bucket[item_id] = doc
        return doc

    def get_doc(self, doc_type: str, item_id: str) -> Optional[Dict[str, Any]]:
        return self._doc_store.get(doc_type, {}).get(item_id)

    def list_docs(self, doc_type: str) -> List[Dict[str, Any]]:
        return list(self._doc_store.get(doc_type, {}).values())

    def delete_doc(self, doc_type: str, item_id: str) -> bool:
        return self._doc_store.get(doc_type, {}).pop(item_id, None) is not None

    def _load_from_data_dir(self, data_dir: Path) -> None:
        if not data_dir.exists() or not data_dir.is_dir():
            print(f"⚠ Sample data directory not found: {data_dir}")
            return

        loaded = 0
        for json_file in sorted(data_dir.glob("*.json")):
            try:
                payload = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"⚠ Could not load sample file {json_file.name}: {e}")
                continue

            patient_id = _infer_patient_id_from_payload(payload)
            if not patient_id:
                continue

            payload["mrn"] = patient_id
            payload["id"] = patient_id
            payload["_id"] = patient_id
            self._store[patient_id] = payload
            loaded += 1

        print(f"✓ Loaded {loaded} sample patients from {data_dir}")

    def get_patient(self, patient_id: str) -> Dict[str, Any]:
        patient = self._store.get(patient_id)
        if patient:
            return patient
        return {"error": f"No patient found with MRN: {patient_id}"}

    def save_patient_data(self, patient_id: str, patient_data: Dict[str, Any]) -> bool:
        document = dict(patient_data)
        document["id"] = patient_id
        document["mrn"] = patient_id
        document["_id"] = patient_id
        self._store[patient_id] = document
        return True

    def save_measurement_result(self, patient_id: str, measurement_record: Dict[str, Any]) -> bool:
        doc = self.get_patient(patient_id)
        if "error" in doc:
            raise ValueError(f"No patient found with MRN: {patient_id}")

        executions = doc.get("measurement_executions") or []
        if not isinstance(executions, list):
            executions = []
        executions.append(measurement_record)

        doc["measurement_executions"] = executions
        doc["last_measurement_result"] = measurement_record
        self.save_patient_data(patient_id, doc)
        return True


def _persist_measurement_execution(
    patient_id: str,
    patient_data: Dict[str, Any],
    mode: str,
    result: Dict[str, Any],
) -> bool:
    executed_at_utc = datetime.utcnow().isoformat() + "Z"
    record = {
        "executedAtUtc": executed_at_utc,
        "mode": mode,
        "result": result,
    }

    if hasattr(cosmosDBHelper, "save_measurement_result"):
        cosmosDBHelper.save_measurement_result(patient_id, record)
        reportingSink.persist_measurement_execution(patient_id, record)
        return True

    # Fallback for mock helper or helpers without partial-update support.
    executions = patient_data.get("measurement_executions")
    if not isinstance(executions, list):
        executions = []
    executions.append(record)
    patient_data["measurement_executions"] = executions
    patient_data["last_measurement_result"] = record
    cosmosDBHelper.save_patient_data(patient_id, patient_data)
    reportingSink.persist_measurement_execution(patient_id, record)
    return True

# Task model for background processing
class Task(BaseModel):
    id: str

# Custom telemetry setup module
from telemetry import setup_telemetry

# Get the base directory for the application
base = Path(__file__).resolve().parent

# Load environment variables from .env file - check multiple locations
env_loaded = False

# Try to load .env from the parent directory (where it actually is)
env_file_parent = base.parent / ".env"
if env_file_parent.exists():
    load_dotenv(env_file_parent)
    env_loaded = True
    #print(f"✓ Loaded .env from: {env_file_parent}")
else:
    # Try to load from current directory
    env_file_current = base / ".env"
    if env_file_current.exists():
        load_dotenv(env_file_current)
        env_loaded = True
        #print(f"✓ Loaded .env from: {env_file_current}")
    else:
        # Last try - default load_dotenv behavior
        load_dotenv()
        #print("⚠ Using default load_dotenv() - checking current working directory")

if not env_loaded:
    print("⚠ No .env file found in expected locations:")
    print(f"  - {env_file_parent}")
    print(f"  - Current working directory: {Path.cwd()}")

# Debug: Print environment variable status
#print("\nEnvironment Variables Status:")
cosmosdb_vars = [
    "COSMOSDB_DATABASE",
    "COSMOSDB_CATALOG_COLLECTION",
    "COSMOSDB_COHORTS_COLLECTION",
    "COSMOSDB_USERNAME",
    "COSMOSDB_PASSWORD",
    "COSMOSDB_HOST"
]

for var in cosmosdb_vars:
    value = os.getenv(var)
    status = "✓" if value else "✗"
    #print(f"  {var}: {status} {'(set)' if value else '(not set)'}")

# Initialize FastAPI application
app = FastAPI()

# ---------------------------------------------------------------------------
# OAuth 2.0 / Entra ID Swagger UI integration (issue #16)
# ---------------------------------------------------------------------------
# Advertise the OAuth2 client-credentials + authorization-code flows in the
# generated OpenAPI so the Swagger UI "Authorize" button can mint a token
# against the Receiver App Registration. Purely documentation/UX — request
# enforcement is done by the auth dependencies, not by this scheme.
_ENTRA_TENANT_ID = os.getenv("ENTRA_TENANT_ID") or os.getenv("AZURE_TENANT_ID", "")
_RECEIVER_APP_ID_URI = os.getenv("RECEIVER_APP_ID_URI", "").strip()
if _ENTRA_TENANT_ID and _RECEIVER_APP_ID_URI:
    _authorize_url = f"https://login.microsoftonline.com/{_ENTRA_TENANT_ID}/oauth2/v2.0/authorize"
    _token_url = f"https://login.microsoftonline.com/{_ENTRA_TENANT_ID}/oauth2/v2.0/token"
    _default_scope = f"{_RECEIVER_APP_ID_URI.rstrip('/')}/.default"

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title="DQ Receiver API",
            version="1.0.0",
            description=(
                "Receiver APIs are protected with OAuth 2.0 / Microsoft Entra ID. "
                "Submitters authenticate via the client-credentials flow and present a "
                "bearer token carrying one of the `Receiver.Submit`, `Receiver.Read`, or "
                "`Receiver.Admin` application roles."
            ),
            routes=app.routes,
        )
        components = openapi_schema.setdefault("components", {})
        components.setdefault("securitySchemes", {})["OAuth2Entra"] = {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": _token_url,
                    "scopes": {_default_scope: "Access the Receiver API"},
                },
                "authorizationCode": {
                    "authorizationUrl": _authorize_url,
                    "tokenUrl": _token_url,
                    "scopes": {_default_scope: "Access the Receiver API"},
                },
            },
        }
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi
    app.swagger_ui_init_oauth = {
        "clientId": os.getenv("ENTRA_CLIENT_ID") or os.getenv("AZURE_CLIENT_ID", ""),
        "scopes": _default_scope,
        "usePkceWithAuthorizationCodeGrant": True,
    }

# Control whether we allow fallback to an in-memory/mock database
REQUIRE_DATABASE = os.getenv("REQUIRE_DATABASE", "false").lower() in ("true", "1", "yes", "on")

# Resolve the on-disk sample-data directory once at module scope so the
# workbench router (and the file-backed fallback) can both reuse it. The
# default first tries the in-image bake location (``/app/data`` shipped via
# the Dockerfile) and then falls back to the repo-relative ``data/`` folder
# used by local dev runs.
def _resolve_sample_data_dir() -> Path:
    explicit = os.getenv("SAMPLE_DATA_DIR")
    if explicit:
        return Path(explicit)
    candidates = [
        base.parent / "_data",                                              # /app/_data inside container
        base.parent.parent / "_data",                                       # submitters/_data
        base.parent.parent.parent / "_data",                                # repo-root _data/ (current layout)
        base.parent / "data",                                               # legacy: /app/data inside container
        base.parent.parent / "data",                                        # legacy: submitters/data
        base.parent.parent / "azure-healthcare-digital-quality" / "data",   # legacy nested layout
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


sample_data_dir = _resolve_sample_data_dir()
reportingSink = ReceiverReportingSink.from_environment()


def _missing_or_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    normalized = value.strip()
    if not normalized:
        return True
    return normalized.startswith("${") and normalized.endswith("}")

# Azure Cosmos DB configuration with better error handling
try:
    database = os.getenv("COSMOSDB_DATABASE", "dq")
    cohorts_container = os.getenv("COSMOSDB_COHORTS_COLLECTION", "cohorts")
    catalog_container = os.getenv("COSMOSDB_CATALOG_COLLECTION", "catalog")
    # Legacy fallback: some older deployments still set COSMOSDB_COLLECTION
    # for the patients container. Treat it as the cohorts container so old
    # env files keep working until they are migrated.
    if not os.getenv("COSMOSDB_COHORTS_COLLECTION") and os.getenv("COSMOSDB_COLLECTION"):
        cohorts_container = os.getenv("COSMOSDB_COLLECTION")
    username = os.getenv("COSMOSDB_USERNAME", "")
    password = os.getenv("COSMOSDB_PASSWORD", "")
    host = os.getenv("COSMOSDB_HOST", "")
    endpoint = os.getenv("COSMOS_ENDPOINT") or os.getenv("COSMOSDB_ENDPOINT", "")
    options = os.getenv("COSMOSDB_OPTIONS", "")
    
    # Check if any are None or empty
    if _missing_or_placeholder(database):
        raise ValueError("COSMOSDB_DATABASE is missing or unresolved (for example, literal ${COSMOSDB_DATABASE})")
    if _missing_or_placeholder(cohorts_container):
        raise ValueError("COSMOSDB_COHORTS_COLLECTION is missing or unresolved (for example, literal ${COSMOSDB_COHORTS_COLLECTION})")
    if _missing_or_placeholder(catalog_container):
        raise ValueError("COSMOSDB_CATALOG_COLLECTION is missing or unresolved (for example, literal ${COSMOSDB_CATALOG_COLLECTION})")

    has_key_mode = not (_missing_or_placeholder(username) or _missing_or_placeholder(password) or _missing_or_placeholder(host))
    has_endpoint_mode = not _missing_or_placeholder(endpoint)

    if not has_key_mode and not has_endpoint_mode:
        raise ValueError(
            "Cosmos config is incomplete. Provide either key mode (COSMOSDB_USERNAME/COSMOSDB_PASSWORD/COSMOSDB_HOST) "
            "or endpoint mode (COSMOS_ENDPOINT or COSMOSDB_ENDPOINT) for AAD auth."
        )
    
    # Clean the values
    database = database.strip('" ')
    cohorts_container = cohorts_container.strip('" ')
    catalog_container = catalog_container.strip('" ')
    username = username.strip('" ')
    password = password.strip('" ')
    host = host.strip('" ')
    endpoint = endpoint.strip('" ')
    options = options.strip('" ')

    if has_endpoint_mode:
        # Prefer endpoint mode so Cosmos helper can use Entra ID when local auth is disabled.
        connection_string = endpoint
    else:
        # Build MongoDB-style string; helper resolves endpoint/key and can still fall back to AAD if needed.
        encoded_username = quote_plus(username)
        encoded_password = quote_plus(password)
        connection_string = f"mongodb://{encoded_username}:{encoded_password}@{host}:10255/?ssl=true&replicaSet=globaldb&retryWrites=false&maxIdleTimeMS=120000"

    # ------------------------------------------------------------------
    # Quality Measures Workbench Cosmos helpers (database `dq`)
    # ------------------------------------------------------------------
    # cohorts container holds:
    #   - docType=member             (FHIR bundles, replaces clinical/patients)
    #   - docType=cohort             (cohort definitions + member lists)
    #   - docType=measurement_execution
    #   - docType=measure_report     (DEQM)
    #   - docType=submission         (DEQM)
    cohortsDBHelper = cosmosdb_helper.CosmosDBHelper(
        connection_string,
        database,
        cohorts_container,
        partition_key_path="/docType",
        default_partition_value="member",
    )
    print(f"✓ Successfully initialized cohorts container: {cohorts_container}")

    # catalog container holds measures, tags, regulatory agencies (programs)
    #   - docType=measure | tag | agency
    catalogDBHelper = cosmosdb_helper.CosmosDBHelper(
        connection_string,
        database,
        catalog_container,
        partition_key_path="/docType",
    )
    print(f"✓ Successfully initialized catalog container: {catalog_container}")

    # Backward-compat aliases. Old code paths that wrote/read "patients" or
    # "clinical" both targeted FHIR member bundles; both now resolve to the
    # cohorts container with docType=member.
    cosmosDBHelper = cohortsDBHelper
    clinicalDBHelper = cohortsDBHelper

    # Initialize diagnostic orchestrator
    try:
        diagnostic_orchestrator = DiagnosticOrchestrator()
        print("✓ Successfully initialized Diagnostic Orchestrator")
    except Exception as e:
        print(f"⚠ Warning: Could not initialize Diagnostic Orchestrator: {e}")
        diagnostic_orchestrator = None
    
    print("✓ Successfully initialized Cosmos DB")

except Exception as e:
    print(f"✗ Error initializing Cosmos DB: {e}")
    if REQUIRE_DATABASE:
        # Fail fast instead of silently using mock services
        raise RuntimeError("Database initialization failed and REQUIRE_DATABASE is set. Aborting startup.") from e
    print("Using file-backed sample services for development (set REQUIRE_DATABASE=1 to disable this fallback)...")

    cohortsDBHelper = FileBackedCosmosDBHelper(sample_data_dir)
    catalogDBHelper = FileBackedCosmosDBHelper(sample_data_dir)
    cosmosDBHelper = cohortsDBHelper
    clinicalDBHelper = cohortsDBHelper
    
    # Initialize diagnostic orchestrator
    try:
        diagnostic_orchestrator = DiagnosticOrchestrator()
        print("✓ Successfully initialized Diagnostic Orchestrator")
    except Exception as e:
        print(f"⚠ Warning: Could not initialize Diagnostic Orchestrator: {e}")
        diagnostic_orchestrator = None

# Get environment-specific configuration
code_space = os.getenv("CODESPACE_NAME")
app_insights = os.getenv("APPINSIGHTS_CONNECTIONSTRING")

# Configure CORS origins based on environment
if code_space: 
    # GitHub Codespaces environment - use dynamic URLs
    origin_8000= f"https://{code_space}-8000.app.github.dev"
    origin_5173 = f"https://{code_space}-5173.app.github.dev"
    ingestion_endpoint = app_insights.split(';')[1].split('=')[1] if app_insights else ""
    
    origins = [origin_8000, origin_5173, os.getenv("API_SERVICE_ACA_URI"), os.getenv("WEB_SERVICE_ACA_URI"), ingestion_endpoint]
    origins = [origin for origin in origins if origin]  # Remove None/empty values
else:
    # Production/local environment - read from origins.txt file
    try:
        origins = [
            o.strip()
            for o in Path(Path(__file__).parent / "origins.txt").read_text().splitlines()
        ]
        # Add explicit localhost origins for development
        origins.extend([
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000"
        ])
    except FileNotFoundError:
        # Fallback origins for development
        origins = [
            "http://localhost:5173",
            "http://localhost:3000", 
            "http://127.0.0.1:5173",
            "http://127.0.0.1:3000",
            "*"  # Allow all origins in development (remove in production)
        ]

# Add CORS middleware to allow cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Setup telemetry and monitoring
setup_telemetry(app)

# Development mode configuration - can be controlled via environment variable
DEVELOPMENT_MODE = os.getenv("DEVELOPMENT_MODE", "false").lower() in ("true", "1", "yes", "on")
print(f"🔧 Development mode: {'ENABLED' if DEVELOPMENT_MODE else 'DISABLED'}")
if DEVELOPMENT_MODE:
    print("⚠️  WARNING: Authentication is bypassed in development mode")

# Health check endpoint
@app.get("/")
async def root():
    """Basic health check endpoint"""
    return {"message": "Hello World"}

# Optional authentication dependency for development mode
async def get_current_user_optional(request: Request) -> Dict[str, Any] | None:
    """
    Optional authentication - returns user if token is valid, None if no token or invalid
    Used in development mode for graceful degradation
    """
    try:
        token = extract_token_from_request(request)
        if not token:
            return None
        return await get_current_user_from_request(request)
    except Exception:
        return None

# Conditional authentication dependency
async def get_current_user_conditional(request: Request) -> Dict[str, Any]:
    """
    Conditional authentication based on development mode
    - Production: Requires valid authentication
    - Development: Optional authentication (allows testing without tokens)
    """
    if DEVELOPMENT_MODE:
        # In development mode, try to get user but don't fail if not authenticated
        user = await get_current_user_optional(request)
        if user:
            return user
        else:
            # Return anonymous user for development
            return {
                "user_id": "dev-user",
                "email": "dev@development.local",
                "name": "Development User",
                "tenant_id": "dev-tenant",
                "roles": [],
                "groups": []
            }
    else:
        # Production mode - require authentication
        return await get_current_user_from_request(request)

# Role-based authorization dependency factory (issue #16)
def require_role(*required_roles: str):
    """
    Build a FastAPI dependency that enforces an application role.

    - Missing / invalid token -> 401 (raised by the underlying validator).
    - Valid token without one of ``required_roles`` -> 403.
    - DEVELOPMENT_MODE bypasses the role check for local testing.

    Usage::

        @app.post("/api/receive", dependencies=[Depends(require_role("Receiver.Submit"))])
    """
    roles = tuple(required_roles)

    async def _dependency(request: Request) -> Dict[str, Any]:
        user = await get_current_user_conditional(request)
        if DEVELOPMENT_MODE:
            # Auth (and therefore role enforcement) is bypassed in dev mode.
            return user
        if roles and not user_has_role(user, list(roles)):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Insufficient permissions. This endpoint requires one of the "
                    f"following application roles: {', '.join(roles)}"
                ),
            )
        return user

    return _dependency

# Patient data retrieval endpoint with conditional auth
@app.get("/api/patient/{id}")
#@trace
async def get_patient(id: str, request: Request, current_user: Dict[str, Any] = Depends(get_current_user_conditional)):
    """
    Retrieve patient data by ID from Cosmos DB
    
    Authentication is required in production, optional in development mode.

    Args:
        id (str): Patient identifier (MRN)
        request (Request): FastAPI request object
        current_user (Dict): Authenticated user information

    Returns:
        JSON: Patient data or error message
    """
    try:
        # Log access for audit trail
        user_email = current_user.get('email', 'unknown')
        is_dev_user = user_email == 'dev@development.local'
        mode_indicator = " [DEV MODE]" if is_dev_user else ""
        #print(f"Patient data access - User: {user_email}, Patient ID: {id}{mode_indicator}")
        
        patient_data = cosmosDBHelper.get_patient(id)
        if "error" in patient_data:
            # Clinical container may contain the canonical FHIR bundle for this member.
            patient_data = clinicalDBHelper.get_patient(id)
        # Check if patient was found
        if "error" in patient_data:
            return JSONResponse(status_code=404, content=patient_data)
        fhir_view = _extract_fhir_view(patient_data)
        return {
            "patient": patient_data,
            "fhir": fhir_view,
            "measurementPreview": _evaluate_bp_measurement(fhir_view, "non-cql"),
        }
    except Exception as e:
        # Return server error for any unexpected exceptions
        print(f"Error retrieving patient {id}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# Patient data storage endpoint with conditional auth
@app.post("/api/patient")
async def save_patient_data(patient_data: dict, request: Request, current_user: Dict[str, Any] = Depends(get_current_user_conditional)):
    """
    Save complete patient data to the database
    
    Authentication is required in production, optional in development mode.

    Args:
        patient_data (dict): Complete patient record including MRN
        request (Request): FastAPI request object
        current_user (Dict): Authenticated user information

    Returns:
        JSON: Success confirmation or error message
    """
    try:
        # Log access for audit trail
        user_email = current_user.get('email', 'unknown')
        is_dev_user = user_email == 'dev@development.local'
        mode_indicator = " [DEV MODE]" if is_dev_user else ""
        #print(f"Patient data save - User: {user_email}{mode_indicator}")
        
        # Extract patient ID from the data
        patient_id = _infer_patient_id_from_payload(patient_data)
        if not patient_id:
            return JSONResponse(status_code=400, content={"error": "Missing mrn field in patient data or bundle patient identifier"})

        # Save patient data to Cosmos DB
        if patient_data.get("resourceType") == "Bundle":
            patient_data["mrn"] = patient_id
        cosmosDBHelper.save_patient_data(patient_id, patient_data)
        return {"status": "patient data saved", "mrn": patient_id}
    except Exception as e:
        print(f"Error saving patient data: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ── Clinical container endpoints ──────────────────────────────────────────

@app.post("/api/clinical/patients")
async def save_clinical_patient(patient_data: dict, request: Request, current_user: Dict[str, Any] = Depends(get_current_user_conditional)):
    """
    Save FHIR bundle or patient data into the clinical container.
    Accepts both FHIR Bundles and flat patient records.
    """
    try:
        patient_id = _infer_patient_id_from_payload(patient_data)
        if not patient_id:
            return JSONResponse(status_code=400, content={"error": "Missing member id in patient data or bundle patient identifier"})

        if patient_data.get("resourceType") == "Bundle":
            patient_data["mrn"] = patient_id
        clinicalDBHelper.save_patient_data(patient_id, patient_data)
        return {"status": "clinical patient data saved", "memberId": patient_id}
    except Exception as e:
        print(f"Error saving clinical patient data: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/clinical/patients/{id}")
async def get_clinical_patient(id: str, request: Request, current_user: Dict[str, Any] = Depends(get_current_user_conditional)):
    """
    Retrieve patient data from the clinical container by member id.
    """
    try:
        patient_data = clinicalDBHelper.get_patient(id)
        if "error" in patient_data:
            return JSONResponse(status_code=404, content=patient_data)
        fhir_view = _extract_fhir_view(patient_data)
        return {"patient": patient_data, "fhir": fhir_view}
    except Exception as e:
        print(f"Error retrieving clinical patient {id}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# Request model for patient summarization
class PatientRequest(BaseModel):
    patient_id: str

# Request models for diagnostic orchestration
class DiagnosticCaseRequest(BaseModel):
    case_info: str
    max_rounds: int = 10
    budget_limit: Optional[float] = None
    execution_mode: str = "unconstrained"  # "instant", "questions_only", "budgeted", "unconstrained", "ensemble"


class MeasurementExecutionRequest(BaseModel):
    mode: str = "non-cql"
    use_native_cql_engine: bool = True
    use_ai_cql_engine: bool = False

class DiagnosticCaseResponse(BaseModel):
    case_id: str
    session_id: str
    status: str
    message: str

# Patient summarization endpoint with conditional auth
@app.post("/api/summarize")
@trace
async def review(request_body: PatientRequest, request: Request, current_user: Dict[str, Any] = Depends(get_current_user_conditional)):
    """
    Summarization endpoint is disabled.
    
    Authentication is required in production, optional in development mode.

    Args:
        request_body (PatientRequest): Request containing patient_id
        request (Request): FastAPI request object
        current_user (Dict): Authenticated user information

    Returns:
        JSON: Feature disabled message (501 status)
    """
    try:
        return JSONResponse(
            content={"detail": "Summarization feature has been removed."},
            status_code=501,
        )
    except Exception as e:
        print(f"Error in summarization request: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/patient/{id}/measure")
async def run_measurement_for_patient(
    id: str,
    request_body: MeasurementExecutionRequest,
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user_conditional),
):
    """
    Evaluate all configured quality measures for a patient using
    native and/or AI CQL engines.
    """
    try:
        patient_data = cosmosDBHelper.get_patient(id)
        if "error" in patient_data:
            return JSONResponse(status_code=404, content=patient_data)

        fhir_view = _extract_fhir_view(patient_data)
        result = _evaluate_bp_measurement_via_orchestrator(
            id,
            patient_data,
            fhir_view,
            request_body.use_native_cql_engine,
            request_body.use_ai_cql_engine,
        )
        mode_label = (
            f"native={str(request_body.use_native_cql_engine).lower()};"
            f"ai={str(request_body.use_ai_cql_engine).lower()}"
        )
        persisted = _persist_measurement_execution(id, patient_data, mode_label, result)

        return {
            "patientId": id,
            "hasFhirBundle": fhir_view.get("hasFhirBundle", False),
            "result": result,
            "persistedToCosmos": persisted,
        }
    except Exception as e:
        print(f"Error evaluating measure for patient {id}: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# Diagnostic Orchestration endpoints
@app.post("/api/diagnostic/case", response_model=DiagnosticCaseResponse)
async def run_diagnostic_case(
    request_body: DiagnosticCaseRequest, 
    request: Request, 
    current_user: Dict[str, Any] = Depends(get_current_user_conditional)
):
    """
    Execute a diagnostic case using the MAI-DxO multi-agent orchestrator
    
    This endpoint runs the full diagnostic orchestration process with specialized agents:
    - Dr. Hypothesis: Maintains differential diagnosis with Bayesian updates
    - Dr. Test-Chooser: Selects discriminative diagnostic tests
    - Dr. Challenger: Acts as devil's advocate, prevents anchoring bias
    - Dr. Stewardship: Enforces cost-conscious care
    - Dr. Checklist: Performs quality control and consistency checks
    
    Args:
        request_body: Case information and execution parameters
        request: FastAPI request object
        current_user: Authenticated user information
        
    Returns:
        DiagnosticCaseResponse with case execution details
    """
    if not diagnostic_orchestrator:
        return JSONResponse(
            status_code=503, 
            content={"error": "Diagnostic orchestrator not available. Check Azure OpenAI configuration."}
        )
    
    try:
        # Log access for audit trail
        user_email = current_user.get('email', 'unknown')
        is_dev_user = user_email == 'dev@development.local'
        mode_indicator = " [DEV MODE]" if is_dev_user else ""
        print(f"Diagnostic orchestration request - User: {user_email}{mode_indicator}")
        
        # Execute diagnostic case
        session = await diagnostic_orchestrator.run_diagnostic_case(
            case_info=request_body.case_info,
            max_rounds=request_body.max_rounds,
            budget_limit=request_body.budget_limit,
            execution_mode=request_body.execution_mode
        )
        
        return DiagnosticCaseResponse(
            case_id=session.case_id,
            session_id=session.session_id,
            status="completed",
            message=f"Diagnostic case completed. Final diagnosis: {session.final_diagnosis or 'No diagnosis reached'}"
        )
        
    except Exception as e:
        print(f"Error in diagnostic orchestration: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/diagnostic/case/{case_id}/summary")
async def get_diagnostic_case_summary(
    case_id: str, 
    request: Request, 
    current_user: Dict[str, Any] = Depends(get_current_user_conditional)
):
    """
    Get a summary of a completed diagnostic case
    
    Args:
        case_id: Unique identifier for the diagnostic case
        request: FastAPI request object
        current_user: Authenticated user information
        
    Returns:
        JSON summary of the diagnostic session
    """
    if not diagnostic_orchestrator:
        return JSONResponse(
            status_code=503, 
            content={"error": "Diagnostic orchestrator not available"}
        )
    
    try:
        summary = diagnostic_orchestrator.get_session_summary(case_id)
        if not summary:
            return JSONResponse(status_code=404, content={"error": "Case not found"})
        
        return summary
        
    except Exception as e:
        print(f"Error retrieving case summary: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/diagnostic/case/{case_id}/traces")
async def get_diagnostic_case_traces(
    case_id: str, 
    request: Request, 
    current_user: Dict[str, Any] = Depends(get_current_user_conditional)
):
    """
    Get detailed execution traces for a diagnostic case
    
    Returns step-by-step traces of the decision-making process with actor labels,
    agent communications, debates, and reasoning chains.
    
    Args:
        case_id: Unique identifier for the diagnostic case
        request: FastAPI request object
        current_user: Authenticated user information
        
    Returns:
        JSON array of execution traces with timestamps and actor information
    """
    if not diagnostic_orchestrator:
        return JSONResponse(
            status_code=503, 
            content={"error": "Diagnostic orchestrator not available"}
        )
    
    try:
        traces = diagnostic_orchestrator.get_session_traces(case_id)
        if not traces:
            return JSONResponse(status_code=404, content={"error": "Case not found or no traces available"})
        
        # Convert traces to JSON-serializable format
        trace_data = []
        for trace in traces:
            trace_dict = {
                "case_id": trace.case_id,
                "session_id": trace.session_id,
                "timestamp": trace.timestamp.isoformat(),
                "round_number": trace.round_number,
                "action_type": trace.action_type.value,
                "actor": trace.actor,
                "content": trace.content,
                "structured_data": trace.structured_data,
                "cost_impact": trace.cost_impact
            }
            trace_data.append(trace_dict)
        
        return {"traces": trace_data, "total_traces": len(trace_data)}
        
    except Exception as e:
        print(f"Error retrieving case traces: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/diagnostic/case/{case_id}/agent-messages")
async def get_diagnostic_agent_messages(
    case_id: str, 
    request: Request, 
    current_user: Dict[str, Any] = Depends(get_current_user_conditional)
):
    """
    Get agent communication messages for a diagnostic case
    
    Returns detailed messages between the specialized diagnostic agents during
    the chain-of-debate process.
    
    Args:
        case_id: Unique identifier for the diagnostic case
        request: FastAPI request object  
        current_user: Authenticated user information
        
    Returns:
        JSON array of agent messages with roles and structured data
    """
    if not diagnostic_orchestrator:
        return JSONResponse(
            status_code=503, 
            content={"error": "Diagnostic orchestrator not available"}
        )
    
    try:
        session = diagnostic_orchestrator.active_sessions.get(case_id)
        if not session:
            return JSONResponse(status_code=404, content={"error": "Case session not found"})
        
        # Convert agent messages to JSON-serializable format
        messages_data = []
        for message in session.agent_messages:
            message_dict = {
                "agent_role": message.agent_role,
                "timestamp": message.timestamp.isoformat(),
                "message_type": message.message_type,
                "content": message.content,
                "structured_data": message.structured_data
            }
            messages_data.append(message_dict)
        
        return {"messages": messages_data, "total_messages": len(messages_data)}
        
    except Exception as e:
        print(f"Error retrieving agent messages: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# Add a simple CORS preflight handler
@app.options("/{path:path}")
async def options_handler(path: str):
    """Handle CORS preflight requests"""
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

# ---------------------------------------------------------------------------
# DEQM (Da Vinci Data Exchange for Quality Measures) FHIR surface
# ---------------------------------------------------------------------------
try:
    from deqm import create_deqm_router

    # Optional Cosmos containers for DEQM-specific persistence. If the account
    # is unavailable (or containers are missing) we fall back to in-memory
    # dictionaries so the FHIR routes stay functional in local/dev mode.
    _deqm_submissions_mem: Dict[str, Dict[str, Any]] = {}
    _deqm_measure_reports_mem: Dict[str, Dict[str, Any]] = {}

    try:
        measure_reports_helper = cosmosdb_helper.CosmosDBHelper(
            connection_string,
            database,
            cohorts_container,
            partition_key_path="/docType",
            default_partition_value="measure_report",
        )
    except Exception as _e:  # noqa: BLE001
        print(f"⚠ measure_reports Cosmos helper unavailable, using memory: {_e}")
        measure_reports_helper = None

    try:
        measure_submissions_helper = cosmosdb_helper.CosmosDBHelper(
            connection_string,
            database,
            cohorts_container,
            partition_key_path="/docType",
            default_partition_value="submission",
        )
    except Exception as _e:  # noqa: BLE001
        print(f"⚠ measure_submissions Cosmos helper unavailable, using memory: {_e}")
        measure_submissions_helper = None

    def _deqm_get_patient_bundle(subject_id: str) -> Optional[Dict[str, Any]]:
        """Resolve a subject id to its stored FHIR bundle / member record."""
        try:
            doc = cohortsDBHelper.get_patient(subject_id)
        except Exception:
            doc = None
        if not isinstance(doc, dict) or "error" in doc:
            return None
        # Workbench member docs wrap the FHIR Bundle under ``bundle`` so the
        # cohorts container can carry workbench metadata (mrn, displayName, ...).
        # Surface the raw Bundle so the orchestrator receives the full clinical
        # context and the ``resourceType == "Bundle"`` branch in
        # ``_evaluate_bp_measurement_via_orchestrator`` is exercised.
        nested = doc.get("bundle") if isinstance(doc.get("bundle"), dict) else None
        if nested and nested.get("resourceType") == "Bundle":
            bundle = dict(nested)
            bundle.setdefault("mrn", doc.get("mrn") or doc.get("id"))
            return bundle
        return doc

    def _deqm_evaluate_measure(
        measure_id: str,
        subject_id: str,
        period: Dict[str, Any],
        engines: Dict[str, bool],
    ) -> Dict[str, Any]:
        """Delegate to the existing orchestrator bridge used by /api/patient/{id}/measure."""
        patient_data = _deqm_get_patient_bundle(subject_id)
        if not patient_data:
            raise LookupError(subject_id)
        fhir_view = _extract_fhir_view(patient_data)
        period_start = (period or {}).get("periodStart") if isinstance(period, dict) else None
        period_end = (period or {}).get("periodEnd") if isinstance(period, dict) else None
        return _evaluate_bp_measurement_via_orchestrator(
            subject_id,
            patient_data,
            fhir_view,
            bool(engines.get("useNative", True)),
            bool(engines.get("useAi", False)),
            period_start=period_start,
            period_end=period_end,
            measure_ids=[measure_id] if measure_id else None,
        )

    def _deqm_save_submission(measure_id: str, record: Dict[str, Any]) -> bool:
        if measure_submissions_helper is not None:
            try:
                measure_submissions_helper.save_patient_data(record["id"], record)
                reportingSink.persist_submission(record)
                return True
            except Exception as e:  # noqa: BLE001
                print(f"⚠ Failed to persist DEQM submission to Cosmos: {e}")
        _deqm_submissions_mem[record["id"]] = record
        reportingSink.persist_submission(record)
        return True

    def _deqm_save_measure_report(subject_id: str, report_id: str, report: Dict[str, Any]) -> bool:
        record = {"id": report_id, "subjectId": subject_id, "report": report}
        if measure_reports_helper is not None:
            try:
                measure_reports_helper.save_patient_data(report_id, record)
                reportingSink.persist_measure_report(record)
                return True
            except Exception as e:  # noqa: BLE001
                print(f"⚠ Failed to persist DEQM MeasureReport to Cosmos: {e}")
        _deqm_measure_reports_mem[report_id] = record
        reportingSink.persist_measure_report(record)
        return True

    app.include_router(
        create_deqm_router(
            auth_dependency=get_current_user_conditional,
            get_patient_bundle=_deqm_get_patient_bundle,
            evaluate_measure=_deqm_evaluate_measure,
            save_submission=_deqm_save_submission,
            save_measure_report=_deqm_save_measure_report,
        )
    )
    print("✓ DEQM FHIR router registered at /fhir")
except Exception as e:  # noqa: BLE001
    print(f"⚠ Could not register DEQM router: {e}")

# ---------------------------------------------------------------------------
# Quality Measures Workbench (Catalog + Cohorts tabs)
# ---------------------------------------------------------------------------
try:
    from workbench import create_workbench_router

    app.include_router(
        create_workbench_router(
            catalog_helper=catalogDBHelper,
            cohorts_helper=cohortsDBHelper,
            auth_dependency=get_current_user_conditional,
            submit_dependency=require_role("Receiver.Submit", "Receiver.Admin"),
            read_dependency=require_role("Receiver.Read", "Receiver.Submit", "Receiver.Admin"),
            sample_data_dir=sample_data_dir,
        )
    )
    print("✓ Quality Measures Workbench router registered at /api/workbench")
except Exception as e:  # noqa: BLE001
    print(f"⚠ Could not register Workbench router: {e}")

# ---------------------------------------------------------------------------
# Cohort Chat (RL-graded Q&A)
# ---------------------------------------------------------------------------
try:
    from chat import create_chat_router

    app.include_router(
        create_chat_router(
            cohorts_helper=cohortsDBHelper,
            auth_dependency=get_current_user_conditional,
        )
    )
    print("✓ Cohort Chat router registered at /api/chat")
except Exception as e:  # noqa: BLE001
    print(f"⚠ Could not register Chat router: {e}")

# OpenTelemetry instrumentation setup
# TODO: fix open telemetry so it doesn't slow app so much
# Wrap this in a try-except to prevent failure if telemetry setup fails
try:
    # Instrument the FastAPI app for automatic telemetry collection
    FastAPIInstrumentor.instrument_app(app)
except Exception as e:
    print(f"Warning: OpenTelemetry instrumentation failed: {str(e)}")
