"""
Local measure evaluator (consumers stack)
==========================================

Runs the three accelerator-supported digital quality measures —
CMS122v11, CMS165v9, and ePC02v1 — against a single patient's FHIR
bundle without requiring the orchestrator pod. The Consumers stack is
intentionally orchestrator-free, so patient-facing measure runs need a
local fallback the backend can return synchronously.

The evaluator is intentionally simple (threshold and code-set checks,
no CQL engine) and produces results in the same shape as the existing
``_evaluate_bp_measurement`` helper so the frontend can render them
with the existing MeasurementsPanel component.
"""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Dict, List, Optional

# Code values pulled from _measures/*.cql value sets (representative
# subset, not exhaustive). Patient bundles only need to use ONE of the
# listed codes to qualify; production CQL would consult the full UMLS
# value sets.
HBA1C_LOINC_CODES = {"4548-4", "4549-2", "17856-6"}
BP_PANEL_LOINC = "85354-9"
BP_SYSTOLIC_LOINC = "8480-6"
BP_DIASTOLIC_LOINC = "8462-4"

HYPERTENSION_SNOMED = {"59621000", "38341003", "1201005"}
DIABETES_SNOMED = {"44054006", "73211009", "111552007"}
ECLAMPSIA_SNOMED = {"15938005", "47200007", "398254007"}
PREECLAMPSIA_SNOMED = {"398254007", "398003007", "44215000"}
TRANSFUSION_SNOMED = {"5447007", "116859006"}
DELIVERY_PROCEDURE_SNOMED = {"177184002", "3311000", "16983000", "236984001"}


def _coding_codes(codable: Dict[str, Any] | None) -> List[str]:
    if not codable:
        return []
    coding = codable.get("coding") or []
    return [c.get("code") for c in coding if isinstance(c, dict) and c.get("code")]


def _coding_first_code(codable: Dict[str, Any] | None) -> str:
    codes = _coding_codes(codable)
    return codes[0] if codes else ""


def _bundle_resources(bundle: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    if not isinstance(bundle, dict):
        return by_type
    for entry in bundle.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        resource = entry.get("resource")
        if not isinstance(resource, dict):
            continue
        rt = resource.get("resourceType")
        if rt:
            by_type.setdefault(rt, []).append(resource)
    return by_type


def _patient_age(patient: Dict[str, Any], reference: datetime) -> Optional[int]:
    dob = patient.get("birthDate")
    if not isinstance(dob, str):
        return None
    try:
        born = datetime.fromisoformat(dob).replace(tzinfo=timezone.utc)
    except Exception:
        return None
    years = reference.year - born.year
    if (reference.month, reference.day) < (born.month, born.day):
        years -= 1
    return years


def _has_condition_code(conditions: List[Dict[str, Any]], code_set: set[str]) -> bool:
    for cond in conditions:
        for code in _coding_codes(cond.get("code")):
            if code in code_set:
                return True
    return False


def _has_procedure_code(procedures: List[Dict[str, Any]], code_set: set[str]) -> bool:
    for proc in procedures:
        for code in _coding_codes(proc.get("code")):
            if code in code_set:
                return True
    return False


def _latest_observation_value(observations: List[Dict[str, Any]], codes: set[str]) -> Optional[Dict[str, Any]]:
    matching: List[Dict[str, Any]] = []
    for obs in observations:
        if any(c in codes for c in _coding_codes(obs.get("code"))):
            matching.append(obs)
    if not matching:
        return None
    matching.sort(key=lambda o: o.get("effectiveDateTime") or o.get("issued") or "", reverse=True)
    top = matching[0]
    value_q = top.get("valueQuantity") or {}
    return {
        "effective": top.get("effectiveDateTime") or top.get("issued"),
        "value": value_q.get("value"),
        "unit": value_q.get("unit"),
        "raw": top,
    }


def _latest_bp(observations: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    panels = [o for o in observations if _coding_first_code(o.get("code")) == BP_PANEL_LOINC]
    panels.sort(key=lambda o: o.get("effectiveDateTime") or "", reverse=True)
    if panels:
        panel = panels[0]
        systolic = diastolic = None
        for comp in panel.get("component") or []:
            code = _coding_first_code(comp.get("code"))
            value = (comp.get("valueQuantity") or {}).get("value")
            if code == BP_SYSTOLIC_LOINC:
                systolic = value
            elif code == BP_DIASTOLIC_LOINC:
                diastolic = value
        if systolic is not None and diastolic is not None:
            return {"systolic": systolic, "diastolic": diastolic, "effective": panel.get("effectiveDateTime")}
    # Fallback: separate observations
    sys_obs = _latest_observation_value(observations, {BP_SYSTOLIC_LOINC})
    dia_obs = _latest_observation_value(observations, {BP_DIASTOLIC_LOINC})
    if sys_obs and dia_obs:
        return {
            "systolic": sys_obs.get("value"),
            "diastolic": dia_obs.get("value"),
            "effective": sys_obs.get("effective") or dia_obs.get("effective"),
        }
    return None


def evaluate_cms122(bundle: Dict[str, Any], period_end: datetime) -> Dict[str, Any]:
    by_type = _bundle_resources(bundle)
    patient = (by_type.get("Patient") or [{}])[0]
    age = _patient_age(patient, period_end)
    conditions = by_type.get("Condition", [])
    observations = by_type.get("Observation", [])
    encounters = by_type.get("Encounter", [])

    has_diabetes = _has_condition_code(conditions, DIABETES_SNOMED)
    age_ok = age is not None and 18 <= age <= 75
    has_qualifying_encounter = len(encounters) > 0
    in_denominator = has_diabetes and age_ok and has_qualifying_encounter

    hba1c = _latest_observation_value(observations, HBA1C_LOINC_CODES)
    hba1c_value = hba1c["value"] if hba1c else None
    poor_control = hba1c_value is None or hba1c_value > 9.0

    if not in_denominator:
        status = "not-in-denominator"
        in_numerator = False
    else:
        in_numerator = bool(poor_control)
        status = "meets-measure" if in_numerator else "does-not-meet-measure"

    return {
        "measureId": "CMS122v11",
        "measureName": "Diabetes: Hemoglobin A1c (HbA1c) Poor Control (>9%)",
        "status": status,
        "denominator": in_denominator,
        "numerator": in_numerator,
        "evaluation": {
            "patientAge": age,
            "hasDiabetes": has_diabetes,
            "hasQualifyingEncounter": has_qualifying_encounter,
            "latestHba1c": hba1c,
            "poorControl": poor_control,
        },
        "explanation": (
            "Numerator captures diabetic patients aged 18-75 with most recent "
            "HbA1c > 9% or missing during the measurement period."
        ),
    }


def evaluate_cms165(bundle: Dict[str, Any], period_end: datetime) -> Dict[str, Any]:
    by_type = _bundle_resources(bundle)
    patient = (by_type.get("Patient") or [{}])[0]
    age = _patient_age(patient, period_end)
    conditions = by_type.get("Condition", [])
    procedures = by_type.get("Procedure", [])
    observations = by_type.get("Observation", [])
    encounters = by_type.get("Encounter", [])

    has_htn = _has_condition_code(conditions, HYPERTENSION_SNOMED)
    age_ok = age is not None and 18 <= age <= 85
    has_qualifying_encounter = len(encounters) > 0
    excluded = _has_procedure_code(
        procedures, {"108241001", "265764009", "70536003"}
    )  # dialysis / kidney transplant placeholders
    in_denominator = has_htn and age_ok and has_qualifying_encounter

    bp = _latest_bp(observations)
    controlled = bool(bp and bp["systolic"] < 140 and bp["diastolic"] < 90)

    if excluded:
        status = "excluded"
        in_numerator = False
    elif not in_denominator:
        status = "not-in-denominator"
        in_numerator = False
    else:
        in_numerator = controlled
        status = "meets-measure" if in_numerator else "does-not-meet-measure"

    return {
        "measureId": "CMS165v9",
        "measureName": "Controlling High Blood Pressure",
        "status": status,
        "denominator": in_denominator,
        "numerator": in_numerator,
        "exclusion": excluded,
        "evaluation": {
            "patientAge": age,
            "hasHypertension": has_htn,
            "hasQualifyingEncounter": has_qualifying_encounter,
            "hasDialysisOrKidneyTransplant": excluded,
            "latestBloodPressure": bp,
            "bpControlled": controlled,
        },
        "explanation": (
            "Numerator captures hypertensive patients aged 18-85 with most recent "
            "BP < 140/90 mmHg; excludes ESRD / dialysis / kidney transplant."
        ),
    }


def evaluate_epc02(bundle: Dict[str, Any], period_end: datetime) -> Dict[str, Any]:
    by_type = _bundle_resources(bundle)
    conditions = by_type.get("Condition", [])
    procedures = by_type.get("Procedure", [])
    encounters = by_type.get("Encounter", [])

    delivery_encounters: List[Dict[str, Any]] = []
    for enc in encounters:
        codes = _coding_codes((enc.get("type") or [{}])[0]) if enc.get("type") else []
        if any(c in DELIVERY_PROCEDURE_SNOMED for c in codes):
            delivery_encounters.append(enc)
    # Procedure-based delivery fallback
    if not delivery_encounters and _has_procedure_code(procedures, DELIVERY_PROCEDURE_SNOMED):
        delivery_encounters = encounters  # treat all encounters as delivery context

    in_denominator = len(delivery_encounters) > 0

    severe = any(
        [
            _has_condition_code(conditions, ECLAMPSIA_SNOMED),
            _has_condition_code(conditions, PREECLAMPSIA_SNOMED),
            _has_procedure_code(procedures, TRANSFUSION_SNOMED),
        ]
    )

    if not in_denominator:
        status = "not-in-denominator"
        in_numerator = False
    else:
        in_numerator = bool(severe)
        status = "meets-measure" if in_numerator else "does-not-meet-measure"

    return {
        "measureId": "ePC02v1",
        "measureName": "Severe Obstetric Complications",
        "status": status,
        "denominator": in_denominator,
        "numerator": in_numerator,
        "evaluation": {
            "deliveryEncounterCount": len(delivery_encounters),
            "hasEclampsia": _has_condition_code(conditions, ECLAMPSIA_SNOMED),
            "hasPreeclampsia": _has_condition_code(conditions, PREECLAMPSIA_SNOMED),
            "hasTransfusion": _has_procedure_code(procedures, TRANSFUSION_SNOMED),
        },
        "explanation": (
            "Denominator: delivery encounters during the measurement period. "
            "Numerator: those with eclampsia, severe preeclampsia, or blood "
            "transfusion."
        ),
    }


def evaluate_all_measures(
    bundle: Dict[str, Any],
    period_start: str = "2025-01-01",
    period_end: str = "2025-12-31",
) -> Dict[str, Any]:
    start = perf_counter()
    try:
        period_end_dt = datetime.fromisoformat(period_end).replace(tzinfo=timezone.utc)
    except Exception:
        period_end_dt = datetime.now(tz=timezone.utc)

    measures = [
        evaluate_cms122(bundle, period_end_dt),
        evaluate_cms165(bundle, period_end_dt),
        evaluate_epc02(bundle, period_end_dt),
    ]
    summary = {
        "measuresEvaluated": len(measures),
        "inDenominator": sum(1 for m in measures if m.get("denominator")),
        "inNumerator": sum(1 for m in measures if m.get("numerator")),
        "gapsInCare": [
            {"measureId": m["measureId"], "measureName": m["measureName"]}
            for m in measures
            if m.get("denominator") and not m.get("numerator")
        ],
    }
    return {
        "measurementPeriod": {"start": period_start, "end": period_end},
        "engine": "local-stub",
        "measures": measures,
        "summary": summary,
        "executionTimeMs": round((perf_counter() - start) * 1000, 2),
    }
