"""
DEQM Measure Catalog
====================

Static, hand-curated catalog of the three MVP measures exposed by this
accelerator. Each entry carries:

* The FHIR ``Measure`` resource skeleton (versioned, canonical URL).
* The FHIR ``Library`` skeleton (points at the raw CQL artifact).
* The ``dataRequirement[]`` the native CQL engine actually uses when
  evaluating the measure — this is the authoritative list returned by
  ``Measure/{id}/$data-requirements``.

Keeping this catalog in a single module lets the DEQM surface and any
future policy-package loader agree on the same shape without drifting
from the native engine.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

# Canonical base URL used in ``Measure.url`` / ``Library.url``. Overridden
# at runtime via ``DEQM_CANONICAL_BASE`` if present.
DEFAULT_CANONICAL_BASE = "https://accelerator.local/fhir"

# -----------------------------------------------------------------------------
# Value set references shared across measures
# -----------------------------------------------------------------------------
_VS_HYPERTENSION = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.104.12.1011"
)
_VS_OUTPATIENT_ENCOUNTER = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.101.12.1061"
)
_VS_DIALYSIS = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.109.12.1013"
)
_VS_KIDNEY_TRANSPLANT = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.109.12.1012"
)
_VS_ESRD = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.109.12.1029"
)
_VS_PREGNANCY = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.111.12.1012"
)
_VS_DIABETES = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.103.12.1001"
)
_VS_HBA1C = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.464.1003.198.12.1013"
)
_VS_DELIVERY_ENCOUNTER = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.666.0.12.1001"  # placeholder OID
)
_VS_SEVERE_OB_COMPLICATIONS = (
    "http://cts.nlm.nih.gov/fhir/ValueSet/"
    "2.16.840.1.113883.3.666.0.12.1002"  # placeholder OID
)

_USCORE_PATIENT = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"
_USCORE_ENCOUNTER = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-encounter"
_USCORE_CONDITION = (
    "http://hl7.org/fhir/us/core/StructureDefinition/us-core-condition-problems-health-concerns"
)
_USCORE_OBSERVATION = (
    "http://hl7.org/fhir/us/core/StructureDefinition/us-core-observation-lab"
)
_USCORE_BP = (
    "http://hl7.org/fhir/us/core/StructureDefinition/us-core-blood-pressure"
)
_USCORE_PROCEDURE = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-procedure"


def _dr(resource_type: str, **kwargs: Any) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"type": resource_type}
    entry.update({k: v for k, v in kwargs.items() if v is not None})
    return entry


# -----------------------------------------------------------------------------
# Catalog
# -----------------------------------------------------------------------------
CATALOG: Dict[str, Dict[str, Any]] = {
    "CMS165v9": {
        "title": "Controlling High Blood Pressure",
        "version": "9.0.0",
        "cqlLibrary": "CMS165v9_ControllingHighBloodPressure",
        "description": (
            "Percentage of patients 18-85 years of age with a diagnosis of "
            "essential hypertension whose most recent blood pressure was "
            "adequately controlled (<140/<90 mmHg)."
        ),
        "scoring": "proportion",
        "topic": "Universal Foundation",
        "dataRequirements": [
            _dr("Patient", profile=[_USCORE_PATIENT], mustSupport=["birthDate", "gender"]),
            _dr(
                "Encounter",
                profile=[_USCORE_ENCOUNTER],
                mustSupport=["status", "class", "type", "period", "subject"],
                codeFilter=[{"path": "type", "valueSet": _VS_OUTPATIENT_ENCOUNTER}],
            ),
            _dr(
                "Condition",
                profile=[_USCORE_CONDITION],
                mustSupport=["code", "clinicalStatus", "onset"],
                codeFilter=[{"path": "code", "valueSet": _VS_HYPERTENSION}],
            ),
            _dr(
                "Observation",
                profile=[_USCORE_BP],
                mustSupport=["code", "effective", "component"],
                codeFilter=[{"path": "code", "code": [
                    {"system": "http://loinc.org", "code": "85354-9"},
                    {"system": "http://loinc.org", "code": "8480-6"},
                    {"system": "http://loinc.org", "code": "8462-4"},
                ]}],
            ),
            _dr(
                "Procedure",
                profile=[_USCORE_PROCEDURE],
                mustSupport=["code", "status", "performed"],
                codeFilter=[
                    {"path": "code", "valueSet": _VS_DIALYSIS},
                    {"path": "code", "valueSet": _VS_KIDNEY_TRANSPLANT},
                ],
            ),
            _dr(
                "Condition",
                mustSupport=["code"],
                codeFilter=[
                    {"path": "code", "valueSet": _VS_ESRD},
                    {"path": "code", "valueSet": _VS_PREGNANCY},
                ],
            ),
        ],
    },
    "CMS122v11": {
        "title": "Diabetes: Hemoglobin A1c Poor Control (>9%)",
        "version": "11.0.0",
        "cqlLibrary": "CMS122v11_DiabetesHbA1cPoorControl",
        "description": (
            "Percentage of patients 18-75 years of age with diabetes who had "
            "hemoglobin A1c > 9.0% during the measurement period."
        ),
        "scoring": "proportion",
        "topic": "Shared Savings Program",
        "dataRequirements": [
            _dr("Patient", profile=[_USCORE_PATIENT], mustSupport=["birthDate", "gender"]),
            _dr(
                "Encounter",
                profile=[_USCORE_ENCOUNTER],
                mustSupport=["status", "type", "period"],
                codeFilter=[{"path": "type", "valueSet": _VS_OUTPATIENT_ENCOUNTER}],
            ),
            _dr(
                "Condition",
                profile=[_USCORE_CONDITION],
                mustSupport=["code", "clinicalStatus", "onset"],
                codeFilter=[{"path": "code", "valueSet": _VS_DIABETES}],
            ),
            _dr(
                "Observation",
                profile=[_USCORE_OBSERVATION],
                mustSupport=["code", "effective", "valueQuantity"],
                codeFilter=[{"path": "code", "valueSet": _VS_HBA1C}],
            ),
        ],
    },
    "ePC02": {
        "title": "Severe Obstetric Complications",
        "version": "1.0.0",
        "cqlLibrary": "ePC02_SevereObstetricComplications",
        "description": (
            "Hospital measure of severe obstetric complications occurring "
            "during inpatient delivery encounters."
        ),
        "scoring": "proportion",
        "topic": "Hospital Quality Reporting",
        "dataRequirements": [
            _dr("Patient", profile=[_USCORE_PATIENT], mustSupport=["birthDate", "gender"]),
            _dr(
                "Encounter",
                profile=[_USCORE_ENCOUNTER],
                mustSupport=["status", "class", "type", "period", "hospitalization"],
                codeFilter=[{"path": "type", "valueSet": _VS_DELIVERY_ENCOUNTER}],
            ),
            _dr(
                "Condition",
                profile=[_USCORE_CONDITION],
                mustSupport=["code", "onset"],
                codeFilter=[{"path": "code", "valueSet": _VS_SEVERE_OB_COMPLICATIONS}],
            ),
            _dr(
                "Procedure",
                profile=[_USCORE_PROCEDURE],
                mustSupport=["code", "status", "performed"],
            ),
            _dr(
                "Observation",
                mustSupport=["code", "valueQuantity", "effective"],
            ),
        ],
    },
}


def list_measure_ids() -> List[str]:
    return sorted(CATALOG.keys())


def get_measure_entry(measure_id: str) -> Optional[Dict[str, Any]]:
    return CATALOG.get(measure_id)


def build_measure_resource(measure_id: str, canonical_base: str = DEFAULT_CANONICAL_BASE) -> Optional[Dict[str, Any]]:
    entry = get_measure_entry(measure_id)
    if not entry:
        return None
    return {
        "resourceType": "Measure",
        "id": measure_id,
        "url": f"{canonical_base}/Measure/{measure_id}",
        "version": entry["version"],
        "name": measure_id,
        "title": entry["title"],
        "status": "active",
        "experimental": False,
        "description": entry["description"],
        "scoring": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/measure-scoring",
                "code": entry["scoring"],
            }]
        },
        "topic": [{"text": entry["topic"]}],
        "library": [f"{canonical_base}/Library/{entry['cqlLibrary']}"],
    }


def build_library_resource(
    measure_id: str,
    canonical_base: str = DEFAULT_CANONICAL_BASE,
) -> Optional[Dict[str, Any]]:
    """Return the module-definition Library with ``dataRequirement[]``.

    This is the resource returned by ``$data-requirements``.
    """
    entry = get_measure_entry(measure_id)
    if not entry:
        return None
    return {
        "resourceType": "Library",
        "id": f"{measure_id}-data-requirements",
        "url": f"{canonical_base}/Library/{measure_id}-data-requirements",
        "version": entry["version"],
        "name": f"{measure_id}DataRequirements",
        "title": f"{entry['title']} — Data Requirements",
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/library-type",
                "code": "module-definition",
            }]
        },
        "relatedArtifact": [
            {
                "type": "depends-on",
                "resource": f"{canonical_base}/Measure/{measure_id}",
            },
            {
                "type": "depends-on",
                "resource": f"{canonical_base}/Library/{entry['cqlLibrary']}",
            },
        ],
        "dataRequirement": deepcopy(entry["dataRequirements"]),
    }


def build_cql_library_resource(
    measure_id: str,
    canonical_base: str = DEFAULT_CANONICAL_BASE,
) -> Optional[Dict[str, Any]]:
    """Return the source-CQL Library pointed at by ``Measure.library``."""
    entry = get_measure_entry(measure_id)
    if not entry:
        return None
    cql_id = entry["cqlLibrary"]
    return {
        "resourceType": "Library",
        "id": cql_id,
        "url": f"{canonical_base}/Library/{cql_id}",
        "version": entry["version"],
        "name": cql_id,
        "title": entry["title"],
        "status": "active",
        "type": {
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/library-type",
                "code": "logic-library",
            }]
        },
        "content": [{"contentType": "text/cql"}],
    }
