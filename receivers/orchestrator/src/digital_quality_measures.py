"""
Quality Measurements — LLM-driven measure evaluation.

No hardcoded measures. The solution uses:
  1. A measure catalog (loaded from the project-root `measures/*.cql` and
     `measures/*.md` files at startup; copied into the container at
     `/app/measures/`).
  2. An LLM call (GPT-5.4-mini) to identify which measures apply to a patient's data
  3. A second LLM call to evaluate each identified measure against the FHIR evidence

The measure catalog is extensible — add a new .cql + .md pair and the system
automatically discovers and can evaluate it.
"""

import json
import logging
import os
import glob
import re
import uuid
from datetime import datetime
from time import perf_counter
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict, field

from azure.cosmos import CosmosClient, exceptions as cosmos_exceptions
from azure.identity import DefaultAzureCredential
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from digital_quality_measures_native_cql_executor import CQLExecutor, normalize_measure_id
from digital_quality_measures_lm_cql_executor import DigitalQualityMeasuresLMCQLExecutor

logger = logging.getLogger(__name__)

# Azure OpenAI / Foundry configuration
FOUNDRY_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
QUALITY_MODEL_DEPLOYMENT = (
    os.getenv("QUALITY_MODEL_DEPLOYMENT")
    or os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME")
    or "gpt-5.4-mini"
)

# Paths — both CQL and MD files live in the `measures/` folder.
# In the container the Dockerfile copies them to `/app/measures/`.
# Locally they live at the project root: `<repo>/azure-healthcare-digital-quality/measures/`.
def _resolve_measures_dir() -> str:
    """Resolve the measures directory in priority order:
    1. ``MEASURES_DIR`` environment variable (explicit override).
    2. ``<dirname(__file__)>/measures`` — container layout (``/app/measures``).
    3. ``<dirname(__file__)>/../../measures`` — local dev (project root).
    Falls back to (2) even if missing so the original behavior is preserved.
    """
    explicit = os.getenv("MEASURES_DIR")
    if explicit:
        return explicit
    here = os.path.dirname(os.path.abspath(__file__))
    container_candidate = os.path.join(here, "measures")
    if os.path.isdir(container_candidate):
        return container_candidate
    project_root_candidate = os.path.abspath(os.path.join(here, "..", "..", "measures"))
    if os.path.isdir(project_root_candidate):
        return project_root_candidate
    return container_candidate


MEASURES_DIR = _resolve_measures_dir()
MEASURES_CQL_DIR = os.getenv("MEASURES_CQL_DIR", MEASURES_DIR)
MEASURES_MD_DIR = os.getenv("MEASURES_MD_DIR", MEASURES_DIR)

# CosmosDB configuration
COSMOSDB_ENDPOINT = os.getenv("COSMOSDB_ENDPOINT", "")
COSMOSDB_DATABASE_NAME = os.getenv("COSMOSDB_DATABASE_NAME", "dq")
COSMOSDB_PLANS_CONTAINER = os.getenv("COSMOSDB_PLANS_CONTAINER", "plans")
COSMOSDB_TASKS_CONTAINER = os.getenv("COSMOSDB_TASKS_CONTAINER", "tasks")

# Initialize CosmosDB client
cosmos_plans_container = None
cosmos_tasks_container = None

if COSMOSDB_ENDPOINT:
    try:
        _credential = DefaultAzureCredential()
        _cosmos_client = CosmosClient(COSMOSDB_ENDPOINT, credential=_credential)
        _cosmos_database = _cosmos_client.get_database_client(COSMOSDB_DATABASE_NAME)
        cosmos_plans_container = _cosmos_database.get_container_client(COSMOSDB_PLANS_CONTAINER)
        cosmos_tasks_container = _cosmos_database.get_container_client(COSMOSDB_TASKS_CONTAINER)
        logger.info("CosmosDB plans/tasks containers initialized for quality measures")
    except Exception as e:
        logger.error(f"Failed to initialize CosmosDB client: {e}")
else:
    logger.warning("COSMOSDB_ENDPOINT not configured — plan storage will be skipped")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class MeasureDefinition:
    """A quality measure definition loaded from disk."""
    measure_id: str
    measure_name: str
    filename_stem: str
    cql_content: str
    markdown_content: str


@dataclass
class MeasureResult:
    measure_id: str
    measure_name: str
    program: str
    in_initial_population: bool
    in_denominator: bool
    denominator_exclusion: bool
    denominator_exclusion_reasons: List[str]
    in_numerator: bool
    numerator_reasons: List[str]
    inverse_measure: bool
    controlled: bool  # True = good outcome regardless of measure direction
    evidence_trace: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityPlanTask:
    """A single task within a quality measure plan."""
    task_id: str
    measure_id: str
    measure_name: str
    status: str  # "planned", "in_progress", "completed", "failed"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    member_fhir_record: Optional[Dict[str, Any]] = None
    cql_engine_used: Optional[str] = None
    processing_time_ms: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class QualityMeasureReport:
    patient_id: str
    measurement_period_start: str
    measurement_period_end: str
    computed_at: str
    measures: List[MeasureResult]
    plan_id: str = ""
    planned_measures: List[str] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


class FHIRQualityRequest(BaseModel):
    """Request body for quality measure computation."""
    fhir_bundle: Optional[Dict[str, Any]] = Field(
        None, description="FHIR R4 Bundle containing Patient and related resources"
    )
    patient: Optional[Dict[str, Any]] = Field(
        None, description="FHIR R4 Patient resource (if not in bundle)"
    )
    conditions: Optional[List[Dict[str, Any]]] = Field(
        None, description="FHIR R4 Condition resources"
    )
    encounters: Optional[List[Dict[str, Any]]] = Field(
        None, description="FHIR R4 Encounter resources"
    )
    observations: Optional[List[Dict[str, Any]]] = Field(
        None, description="FHIR R4 Observation resources"
    )
    procedures: Optional[List[Dict[str, Any]]] = Field(
        None, description="FHIR R4 Procedure resources"
    )
    coverages: Optional[List[Dict[str, Any]]] = Field(
        None, description="FHIR R4 Coverage resources"
    )
    measurement_period_start: str = Field(
        default="2025-01-01", description="Start of measurement period (YYYY-MM-DD)"
    )
    measurement_period_end: str = Field(
        default="2025-12-31", description="End of measurement period (YYYY-MM-DD)"
    )
    measures: Optional[List[str]] = Field(
        None,
        description=(
            "Explicit list of measure IDs to evaluate. "
            "If null, this uses an LLM to identify applicable measures."
        ),
    )
    use_native_cql_engine: bool = Field(
        default=True,
        description="When true, evaluate measures with the deterministic native CQL engine.",
    )
    use_ai_cql_engine: bool = Field(
        default=False,
        description="When true, evaluate measures with the AI CQL (LLM) engine.",
    )


# =============================================================================
# Measure Catalog — auto-discovered from the `measures/` folder (project root)
# =============================================================================

_measure_catalog: Dict[str, MeasureDefinition] = {}
_cql_executor = CQLExecutor()
_lm_executor = DigitalQualityMeasuresLMCQLExecutor(
    foundry_project_endpoint=FOUNDRY_PROJECT_ENDPOINT,
    model_deployment=QUALITY_MODEL_DEPLOYMENT,
)


def _extract_measure_name_from_markdown(md_content: str) -> Optional[str]:
    if not md_content:
        return None

    table_match = re.search(
        r"^\|\s*\*\*CMS eCQM Name\*\*\s*\|\s*(.+?)\s*\|\s*$",
        md_content,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if table_match:
        return table_match.group(1).strip()

    heading_match = re.search(r"^#\s+(.+?)\s*$", md_content, flags=re.MULTILINE)
    if heading_match:
        heading = heading_match.group(1).strip()
        parts = re.split(r"\s+[\-\u2014]\s+", heading, maxsplit=1)
        if len(parts) == 2:
            return parts[1].strip()
        return heading

    return None


def _extract_measure_name_from_cql(cql_content: str) -> Optional[str]:
    if not cql_content:
        return None

    meta_match = re.search(
        r"CMS\s+eCQM\s+Name\s*:\s*(.+?)\s*$",
        cql_content,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if meta_match:
        return meta_match.group(1).strip()

    return None


def _extract_measure_name(measure_id: str, markdown_content: str, cql_content: str) -> str:
    return (
        _extract_measure_name_from_markdown(markdown_content)
        or _extract_measure_name_from_cql(cql_content)
        or measure_id
    )


def _load_measure_catalog() -> None:
    """Scan CQL and markdown directories and build the catalog."""
    global _measure_catalog
    _measure_catalog.clear()

    cql_dir = os.path.normpath(MEASURES_CQL_DIR)
    md_dir = os.path.normpath(MEASURES_MD_DIR)

    if not os.path.isdir(cql_dir):
        logger.warning(f"CQL directory not found: {cql_dir}")
        return

    for cql_path in sorted(glob.glob(os.path.join(cql_dir, "*.cql"))):
        stem = os.path.splitext(os.path.basename(cql_path))[0]
        measure_id = _extract_measure_id(cql_path, stem)

        cql_content = ""
        with open(cql_path, "r", encoding="utf-8") as f:
            cql_content = f.read()

        md_content = ""
        md_path = os.path.join(md_dir, f"{stem}.md")
        if os.path.isfile(md_path):
            with open(md_path, "r", encoding="utf-8") as f:
                md_content = f.read()

        measure_name = _extract_measure_name(measure_id, md_content, cql_content)

        _measure_catalog[measure_id] = MeasureDefinition(
            measure_id=measure_id,
            measure_name=measure_name,
            filename_stem=stem,
            cql_content=cql_content,
            markdown_content=md_content,
        )
        logger.info(f"Loaded measure: {measure_id} from {stem}")

    logger.info(f"Measure catalog loaded: {len(_measure_catalog)} measures: {list(_measure_catalog.keys())}")


def _extract_measure_id(cql_path: str, stem: str) -> str:
    """Extract the canonical measure ID from the CQL library declaration."""
    with open(cql_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.lower().startswith("library "):
                parts = stripped.split()
                if len(parts) >= 4 and parts[2].lower() == "version":
                    lib_name = parts[1]
                    version = parts[3].strip("'\"")
                    return f"{lib_name}v{version}"
                elif len(parts) >= 2:
                    return parts[1]
    return stem.split("_")[0]


def get_measure_catalog() -> Dict[str, MeasureDefinition]:
    if not _measure_catalog:
        _load_measure_catalog()
    return _measure_catalog


# =============================================================================
# FHIR Resource Extraction (Context)
# =============================================================================

def extract_resources_from_bundle(bundle: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Extract FHIR resources by type from a Bundle."""
    resources: Dict[str, List[Dict[str, Any]]] = {}
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType", "")
        if rtype:
            resources.setdefault(rtype, []).append(resource)
    return resources


def gather_context(request: FHIRQualityRequest) -> Dict[str, Any]:
    """
    Context — Step 1: Gather Context.
    Extract and normalise FHIR resources from the request into a structured dict.
    """
    patient = request.patient or {}
    conditions = request.conditions or []
    encounters = request.encounters or []
    observations = request.observations or []
    procedures = request.procedures or []
    coverages = request.coverages or []

    if request.fhir_bundle:
        resources = extract_resources_from_bundle(request.fhir_bundle)
        patient = patient or (resources.get("Patient", [{}])[0])
        conditions = conditions or resources.get("Condition", [])
        encounters = encounters or resources.get("Encounter", [])
        observations = observations or resources.get("Observation", [])
        procedures = procedures or resources.get("Procedure", [])
        coverages = coverages or resources.get("Coverage", [])

    if not patient:
        raise ValueError("No Patient resource provided")

    return {
        "patient": patient,
        "conditions": conditions,
        "encounters": encounters,
        "observations": observations,
        "procedures": procedures,
        "coverages": coverages,
    }


def _summarise_patient_context(ctx: Dict[str, Any]) -> str:
    """Create a concise text summary of the patient context for the LLM."""
    patient = ctx["patient"]
    lines = []
    lines.append(f"Patient ID: {patient.get('id', 'unknown')}")
    lines.append(f"Birth Date: {patient.get('birthDate', 'unknown')}")
    lines.append(f"Gender: {patient.get('gender', 'unknown')}")

    if ctx["conditions"]:
        cond_strs = []
        for c in ctx["conditions"]:
            codings = c.get("code", {}).get("coding", [])
            display = codings[0].get("display", codings[0].get("code", "?")) if codings else "?"
            status = "?"
            cs = c.get("clinicalStatus")
            if isinstance(cs, dict):
                cs_codings = cs.get("coding", [{}])
                if cs_codings:
                    status = cs_codings[0].get("code", "?")
            cond_strs.append(f"  - {display} (status={status})")
        lines.append(f"Conditions ({len(ctx['conditions'])}):")
        lines.extend(cond_strs[:20])

    if ctx["encounters"]:
        enc_strs = []
        for e in ctx["encounters"]:
            enc_class = e.get("class", {})
            class_code = enc_class.get("code", enc_class) if isinstance(enc_class, dict) else str(enc_class)
            period = e.get("period", {})
            enc_strs.append(f"  - class={class_code} period={period.get('start','?')} to {period.get('end','?')}")
        lines.append(f"Encounters ({len(ctx['encounters'])}):")
        lines.extend(enc_strs[:20])

    if ctx["observations"]:
        obs_strs = []
        for o in ctx["observations"]:
            codings = o.get("code", {}).get("coding", [])
            code = codings[0].get("code", "?") if codings else "?"
            display = codings[0].get("display", code) if codings else code
            val = o.get("valueQuantity", {})
            val_str = f"{val.get('value','')} {val.get('unit','')}" if val else ""
            obs_strs.append(f"  - {display} ({code}): {val_str}")
        lines.append(f"Observations ({len(ctx['observations'])}):")
        lines.extend(obs_strs[:20])

    if ctx["procedures"]:
        proc_strs = []
        for p in ctx["procedures"]:
            codings = p.get("code", {}).get("coding", [])
            display = codings[0].get("display", codings[0].get("code", "?")) if codings else "?"
            proc_strs.append(f"  - {display}")
        lines.append(f"Procedures ({len(ctx['procedures'])}):")
        lines.extend(proc_strs[:20])

    return "\n".join(lines)


# =============================================================================
# CosmosDB Plan Persistence
# =============================================================================

def _store_plan(
    plan_id: str,
    patient_id: str,
    planned_measure_ids: List[str],
    catalog: Dict[str, "MeasureDefinition"],
    measurement_period_start: str,
    measurement_period_end: str,
    member_fhir_record: Dict[str, Any],
) -> Dict[str, Any]:
    """Persist a quality measure plan to CosmosDB.

    Each planned measure becomes a task with status 'planned'.
    Returns the plan document.
    """
    timestamp = datetime.utcnow().isoformat() + "Z"
    tasks = []
    for mid in planned_measure_ids:
        mdef = catalog.get(mid)
        name = mdef.measure_name if mdef else mid
        tasks.append(asdict(QualityPlanTask(
            task_id=str(uuid.uuid4()),
            measure_id=mid,
            measure_name=name,
            status="planned",
            member_fhir_record=member_fhir_record,
        )))

    plan_doc = {
        "id": plan_id,
        "taskId": plan_id,
        "patient_id": patient_id,
        "measurement_period_start": measurement_period_start,
        "measurement_period_end": measurement_period_end,
        "tasks": tasks,
        "status": "planned",
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    if cosmos_plans_container:
        try:
            cosmos_plans_container.upsert_item(plan_doc)
            logger.info(f"Quality plan stored in CosmosDB: {plan_id} with {len(tasks)} tasks")
        except Exception as e:
            logger.error(f"Failed to store plan in CosmosDB: {e}")
    else:
        logger.warning("CosmosDB not configured — plan not persisted")

    if cosmos_tasks_container:
        try:
            for task in tasks:
                task_doc = {
                    "id": task["task_id"],
                    "type": "quality_measure_task",
                    "plan_id": plan_id,
                    "patient_id": patient_id,
                    "measurement_period_start": measurement_period_start,
                    "measurement_period_end": measurement_period_end,
                    **task,
                }
                cosmos_tasks_container.upsert_item(task_doc)
            logger.info(f"Quality task items stored in CosmosDB tasks container: {len(tasks)}")
        except Exception as e:
            logger.error(f"Failed to store task items in CosmosDB tasks container: {e}")
    else:
        logger.warning("CosmosDB tasks container not configured — task items not persisted")

    return plan_doc


def _update_plan_task(
    plan_doc: Dict[str, Any],
    measure_id: str,
    status: str,
    cql_engine_used: Optional[str] = None,
    processing_time_ms: Optional[float] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Update a single task's status within a plan and persist to CosmosDB."""
    timestamp = datetime.utcnow().isoformat() + "Z"
    updated_task: Optional[Dict[str, Any]] = None
    for task in plan_doc.get("tasks", []):
        if task["measure_id"] == measure_id:
            task["status"] = status
            if status == "in_progress":
                task["started_at"] = timestamp
            elif status in ("completed", "failed"):
                task["completed_at"] = timestamp
            if result is not None:
                task["result"] = result
            if cql_engine_used is not None:
                task["cql_engine_used"] = cql_engine_used
            if processing_time_ms is not None:
                task["processing_time_ms"] = processing_time_ms
            if error is not None:
                task["error"] = error
            updated_task = task
            break

    # Update overall plan status
    statuses = {t["status"] for t in plan_doc.get("tasks", [])}
    if all(s == "completed" for s in statuses):
        plan_doc["status"] = "completed"
    elif "failed" in statuses and "planned" not in statuses and "in_progress" not in statuses:
        plan_doc["status"] = "completed_with_errors"
    elif "in_progress" in statuses:
        plan_doc["status"] = "in_progress"
    plan_doc["updated_at"] = timestamp

    if cosmos_plans_container:
        try:
            cosmos_plans_container.upsert_item(plan_doc)
        except Exception as e:
            logger.error(f"Failed to update plan in CosmosDB: {e}")

    if cosmos_tasks_container and updated_task:
        try:
            task_doc = {
                "id": updated_task["task_id"],
                "type": "quality_measure_task",
                "plan_id": plan_doc.get("id"),
                "patient_id": plan_doc.get("patient_id"),
                "measurement_period_start": plan_doc.get("measurement_period_start"),
                "measurement_period_end": plan_doc.get("measurement_period_end"),
                **updated_task,
            }
            cosmos_tasks_container.upsert_item(task_doc)
        except Exception as e:
            logger.error(f"Failed to update task item in CosmosDB tasks container: {e}")


# =============================================================================
# LLM Integration — Plan Quality Measures & Evaluate
# =============================================================================

def plan_quality_measures(
    patient_summary: str,
    catalog_ids: List[str],
    catalog_descriptions: Dict[str, str],
    measurement_period: str,
) -> List[str]:
    return _lm_executor.plan_quality_measures(
        patient_summary=patient_summary,
        catalog_ids=catalog_ids,
        catalog_descriptions=catalog_descriptions,
        measurement_period=measurement_period,
    )


def evaluate_single_measure_cql(
    measure_def: MeasureDefinition,
    context: Dict[str, Any],
    measurement_period_start: str,
    measurement_period_end: str,
) -> MeasureResult:
    """Evaluate a single measure using the deterministic CQL executor."""
    result = _cql_executor.evaluate(
        measure_id=normalize_measure_id(measure_def.measure_id),
        cql_text=measure_def.cql_content,
        context=context,
        measurement_period_start=measurement_period_start,
        measurement_period_end=measurement_period_end,
    )
    return MeasureResult(
        measure_id=result.measure_id,
        measure_name=measure_def.measure_name or result.measure_name,
        program=result.program,
        in_initial_population=result.in_initial_population,
        in_denominator=result.in_denominator,
        denominator_exclusion=result.denominator_exclusion,
        denominator_exclusion_reasons=result.denominator_exclusion_reasons,
        in_numerator=result.in_numerator,
        numerator_reasons=result.numerator_reasons,
        inverse_measure=result.inverse_measure,
        controlled=result.controlled,
        evidence_trace=result.evidence_trace,
        detail=result.detail,
    )


def evaluate_single_measure(
    measure_def: MeasureDefinition,
    context: Dict[str, Any],
    patient_summary: str,
    fhir_context_json: str,
    measurement_period_start: str,
    measurement_period_end: str,
    measurement_period: str,
    use_native_cql_engine: bool,
    use_ai_cql_engine: bool,
) -> MeasureResult:
    """
    Evaluate a measure using the engine flags passed in the request.
    """
    if use_native_cql_engine:
        try:
            return evaluate_single_measure_cql(
                measure_def=measure_def,
                context=context,
                measurement_period_start=measurement_period_start,
                measurement_period_end=measurement_period_end,
            )
        except Exception as e:
            logger.error(f"CQL evaluation failed for {measure_def.measure_id}: {e}", exc_info=True)
            return MeasureResult(
                measure_id=normalize_measure_id(measure_def.measure_id),
                measure_name=measure_def.measure_name,
                program="unknown",
                in_initial_population=False,
                in_denominator=False,
                denominator_exclusion=False,
                denominator_exclusion_reasons=[],
                in_numerator=False,
                numerator_reasons=[f"CQL evaluation error: {str(e)}"],
                inverse_measure=False,
                controlled=False,
                evidence_trace=[f"ERROR: {str(e)}"],
            )

    if use_ai_cql_engine:
        try:
            result_dict = _lm_executor.evaluate_single_measure(
                measure_def=measure_def,
                patient_summary=patient_summary,
                fhir_context_json=fhir_context_json,
                measurement_period=measurement_period,
            )
            return MeasureResult(
                measure_id=result_dict.get("measure_id", measure_def.measure_id),
                measure_name=result_dict.get("measure_name") or measure_def.measure_name,
                program=result_dict.get("program", ""),
                in_initial_population=result_dict.get("in_initial_population", False),
                in_denominator=result_dict.get("in_denominator", False),
                denominator_exclusion=result_dict.get("denominator_exclusion", False),
                denominator_exclusion_reasons=result_dict.get("denominator_exclusion_reasons", []),
                in_numerator=result_dict.get("in_numerator", False),
                numerator_reasons=result_dict.get("numerator_reasons", []),
                inverse_measure=result_dict.get("inverse_measure", False),
                controlled=result_dict.get("controlled", False),
                evidence_trace=result_dict.get("evidence_trace", []),
                detail=result_dict.get("detail", {}),
            )
        except Exception as e:
            logger.error(f"AI CQL evaluation failed for {measure_def.measure_id}: {e}", exc_info=True)
            return MeasureResult(
                measure_id=normalize_measure_id(measure_def.measure_id),
                measure_name=measure_def.measure_name,
                program="unknown",
                in_initial_population=False,
                in_denominator=False,
                denominator_exclusion=False,
                denominator_exclusion_reasons=[],
                in_numerator=False,
                numerator_reasons=[f"AI CQL evaluation error: {str(e)}"],
                inverse_measure=False,
                controlled=False,
                evidence_trace=[f"ERROR: {str(e)}"],
            )

    return MeasureResult(
        measure_id=normalize_measure_id(measure_def.measure_id),
        measure_name=measure_def.measure_name,
        program="unknown",
        in_initial_population=False,
        in_denominator=False,
        denominator_exclusion=False,
        denominator_exclusion_reasons=[],
        in_numerator=False,
        numerator_reasons=["No quality-measure engine was enabled for this request"],
        inverse_measure=False,
        controlled=False,
        evidence_trace=["ERROR: No engine enabled (use_native_cql_engine/use_ai_cql_engine are both false)"],
    )


# =============================================================================
# Orchestrator
# =============================================================================

def compute_quality_measures(request: FHIRQualityRequest) -> QualityMeasureReport:
    """
    Digital Quality Orchestrator — main entry point.

    Steps:
      1. Gather Context      (Context)
      2. Plan Quality Measures (Quality Measures + LLM)
      3. Evaluate Quality Measures (Quality Measures + LLM)
    """
    mp_start = request.measurement_period_start
    mp_end = request.measurement_period_end
    measurement_period = f"{mp_start} to {mp_end}"

    # --- Step 1: Gather Context ---
    ctx = gather_context(request)
    patient_id = ctx["patient"].get("id", "unknown")
    patient_summary = _summarise_patient_context(ctx)
    fhir_context_json = json.dumps(ctx, default=str)

    # --- Step 2: Plan Quality Measures ---
    catalog = get_measure_catalog()
    catalog_ids = list(catalog.keys())

    catalog_descriptions: Dict[str, str] = {}
    for mid, mdef in catalog.items():
        catalog_descriptions[mid] = mdef.measure_name or mid

    if request.measures:
        planned_ids = [m for m in request.measures if m in catalog_ids]
        if not planned_ids:
            for req_m in request.measures:
                for cat_m in catalog_ids:
                    if req_m.replace("-", "").replace("_", "").lower() in cat_m.replace("-", "").replace("_", "").lower():
                        planned_ids.append(cat_m)
            planned_ids = list(set(planned_ids))
    else:
        planned_ids = plan_quality_measures(
            patient_summary=patient_summary,
            catalog_ids=catalog_ids,
            catalog_descriptions=catalog_descriptions,
            measurement_period=measurement_period,
        )

    # --- Step 2b: Store plan in CosmosDB ---
    plan_id = str(uuid.uuid4())
    plan_doc = _store_plan(
        plan_id=plan_id,
        patient_id=patient_id,
        planned_measure_ids=planned_ids,
        catalog=catalog,
        measurement_period_start=mp_start,
        measurement_period_end=mp_end,
        member_fhir_record=ctx,
    )

    # --- Step 3: Evaluate Quality Measures (execute each task in the plan) ---
    results: List[MeasureResult] = []
    for mid in planned_ids:
        mdef = catalog.get(mid)
        if not mdef:
            continue

        # Mark task as in_progress
        _update_plan_task(plan_doc, mid, "in_progress")

        engine_used = "native" if request.use_native_cql_engine else "ai" if request.use_ai_cql_engine else "none"
        measure_start = perf_counter()

        result = evaluate_single_measure(
            measure_def=mdef,
            context=ctx,
            patient_summary=patient_summary,
            fhir_context_json=fhir_context_json,
            measurement_period_start=mp_start,
            measurement_period_end=mp_end,
            measurement_period=measurement_period,
            use_native_cql_engine=request.use_native_cql_engine,
            use_ai_cql_engine=request.use_ai_cql_engine,
        )
        processing_time_ms = round((perf_counter() - measure_start) * 1000, 2)
        result.detail = {
            **(result.detail or {}),
            "processing_time_ms": processing_time_ms,
            "cql_engine_used": engine_used,
        }
        results.append(result)

        # Mark task as completed (or failed)
        result_dict = asdict(result)
        if result.evidence_trace and any(t.startswith("ERROR:") for t in result.evidence_trace):
            _update_plan_task(
                plan_doc,
                mid,
                "failed",
                cql_engine_used=engine_used,
                processing_time_ms=processing_time_ms,
                result=result_dict,
                error=result.evidence_trace[0],
            )
        else:
            _update_plan_task(
                plan_doc,
                mid,
                "completed",
                cql_engine_used=engine_used,
                processing_time_ms=processing_time_ms,
                result=result_dict,
            )

    # --- Summary ---
    in_denominator_count = sum(1 for r in results if r.in_denominator and not r.denominator_exclusion)
    controlled_count = sum(1 for r in results if r.controlled)

    summary = {
        "measures_evaluated": len(results),
        "in_denominator": in_denominator_count,
        "controlled": controlled_count,
        "gaps_in_care": [
            r.measure_id for r in results
            if r.in_denominator and not r.denominator_exclusion and not r.controlled
        ],
    }

    return QualityMeasureReport(
        patient_id=patient_id,
        measurement_period_start=mp_start,
        measurement_period_end=mp_end,
        computed_at=datetime.utcnow().isoformat() + "Z",
        measures=[asdict(r) for r in results],
        plan_id=plan_id,
        planned_measures=planned_ids,
        summary=summary,
    )


# =============================================================================
# FastAPI Endpoints (MCP-compatible)
# =============================================================================

quality_app = FastAPI(
    title="Quality Measurements",
    description=(
        "LLM-driven eCQM quality measure evaluation from FHIR R4 resources. "
        "Measures are auto-discovered from the project-root `measures/` folder — no hardcoded measures."
    ),
    version="2.0.0",
)


@quality_app.on_event("startup")
async def startup_load_catalog():
    _load_measure_catalog()


@quality_app.post("/compute-quality-measures")
async def api_compute_quality_measures(request: FHIRQualityRequest) -> JSONResponse:
    """
    Compute quality measures for a patient from FHIR data.

    Workflow:
      1. Gather Context — extract FHIR resources
      2. Plan Quality Measures — LLM identifies applicable measures from catalog
      3. Evaluate Quality Measures — LLM evaluates each measure per CQL logic
    """
    try:
        report = compute_quality_measures(request)
        return JSONResponse(content=asdict(report))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error computing quality measures: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error computing quality measures")


@quality_app.get("/measures")
async def list_measures() -> JSONResponse:
    """List all measures in the catalog (auto-discovered)."""
    catalog = get_measure_catalog()
    measures = []
    for mid, mdef in catalog.items():
        measures.append({
            "id": mid,
            "name": mdef.measure_name,
            "cql_file": f"{mdef.filename_stem}.cql",
            "markdown_file": f"{mdef.filename_stem}.md",
        })
    return JSONResponse(content={"measures": measures, "count": len(measures)})


@quality_app.get("/health")
async def health() -> JSONResponse:
    catalog = get_measure_catalog()
    return JSONResponse(content={
        "status": "healthy",
        "agent": "quality-measurements",
        "measures_loaded": len(catalog),
        "model": QUALITY_MODEL_DEPLOYMENT,
        "cosmos_connected": cosmos_plans_container is not None,
    })


@quality_app.get("/plans/{plan_id}")
async def get_plan(plan_id: str) -> JSONResponse:
    """Retrieve a quality measure plan and its task statuses from CosmosDB."""
    if not cosmos_plans_container:
        raise HTTPException(status_code=503, detail="CosmosDB not configured")
    try:
        plan_doc = cosmos_plans_container.read_item(item=plan_id, partition_key=plan_id)
        return JSONResponse(content=plan_doc)
    except cosmos_exceptions.CosmosResourceNotFoundError:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    except Exception as e:
        logger.error(f"Error retrieving plan {plan_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal error retrieving plan")


# =============================================================================
# MCP Tool Registration (for integration with digital_quality_orchestrator)
# =============================================================================

def register_quality_tools(app: FastAPI):
    """Register quality measure computation as MCP tools on the orchestrator app."""

    @app.post("/tools/compute-quality-measures")
    async def tool_compute_quality_measures(request: Request) -> JSONResponse:
        """MCP tool: Compute eCQM quality measures from FHIR data."""
        body = await request.json()
        fhir_request = FHIRQualityRequest(**body)
        report = compute_quality_measures(fhir_request)
        return JSONResponse(content=asdict(report))

    @app.get("/tools/list-quality-measures")
    async def tool_list_quality_measures() -> JSONResponse:
        """MCP tool: List available quality measures from the catalog."""
        return await list_measures()

    @app.post("/tools/plan-quality-measures")
    async def tool_plan_quality_measures(request: Request) -> JSONResponse:
        """MCP tool: Use LLM to identify applicable measures for a FHIR context."""
        body = await request.json()
        fhir_request = FHIRQualityRequest(**body)
        ctx = gather_context(fhir_request)
        patient_summary = _summarise_patient_context(ctx)
        catalog = get_measure_catalog()
        catalog_ids = list(catalog.keys())
        catalog_descriptions = {mid: mid for mid in catalog_ids}
        measurement_period = f"{fhir_request.measurement_period_start} to {fhir_request.measurement_period_end}"
        planned = plan_quality_measures(patient_summary, catalog_ids, catalog_descriptions, measurement_period)
        return JSONResponse(content={"planned_measures": planned})

    @app.get("/tools/get-plan/{plan_id}")
    async def tool_get_plan(plan_id: str) -> JSONResponse:
        """MCP tool: Retrieve a quality measure plan with task statuses."""
        return await get_plan(plan_id)

    logger.info("Quality measurement MCP tools registered")
