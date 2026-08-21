"""Tests for the DQM (FHIR/QI-Core) measure executor in the submitters
orchestrator. Builds a minimal measure package on disk and evaluates it,
verifying the executor maps the ms-cql-sdk result into the orchestrator's
population/score shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_ORCH_SRC = (
    Path(__file__).resolve().parents[1]
    / "submitters"
    / "orchestrator"
    / "src"
)
if str(_ORCH_SRC) not in sys.path:
    sys.path.insert(0, str(_ORCH_SRC))

pytest.importorskip("cql_sdk.dqm", reason="requires ms-cql-sdk >= DQM support")

from digital_quality_measures_dqm_executor import DQMExecutor  # noqa: E402

FHIR = "{http://hl7.org/fhir}"


def _retrieve(data_type: str, value_set: str, code_property: str) -> dict[str, Any]:
    return {
        "type": "Retrieve",
        "dataType": f"{FHIR}{data_type}",
        "codeProperty": code_property,
        "codes": {"type": "ValueSetRef", "name": value_set},
    }


def _demo_library() -> dict[str, Any]:
    numerator = {
        "type": "Exists",
        "operand": {
            "type": "Query",
            "source": [{"alias": "O", "expression": _retrieve("Observation", "Vital", "code")}],
            "where": {
                "type": "Less",
                "operand": [
                    {
                        "type": "As",
                        "operand": {"type": "Property", "scope": "O", "path": "value"},
                        "asType": f"{FHIR}Quantity",
                    },
                    {"type": "Quantity", "value": 140, "unit": "mm[Hg]"},
                ],
            },
        },
    }
    return {
        "library": {
            "identifier": {"id": "DemoLib", "version": "1.0.0"},
            "usings": {
                "def": [
                    {"localIdentifier": "System", "uri": "urn:hl7-org:elm-types:r1"},
                    {"localIdentifier": "FHIR", "uri": "http://hl7.org/fhir", "version": "4.0.1"},
                ]
            },
            "valueSets": {
                "def": [
                    {"name": "Visit", "id": "urn:oid:visit"},
                    {"name": "Vital", "id": "urn:oid:vital"},
                ]
            },
            "parameters": {"def": [{"name": "Measurement Period"}]},
            "statements": {
                "def": [
                    {
                        "name": "Initial Population",
                        "context": "Patient",
                        "expression": {"type": "Exists", "operand": _retrieve("Encounter", "Visit", "type")},
                    },
                    {
                        "name": "Denominator",
                        "context": "Patient",
                        "expression": {"type": "ExpressionRef", "name": "Initial Population"},
                    },
                    {"name": "Numerator", "context": "Patient", "expression": numerator},
                ]
            },
        }
    }


def _demo_measure() -> dict[str, Any]:
    def pop(code: str, expr: str) -> dict[str, Any]:
        return {"code": {"coding": [{"code": code}]}, "criteria": {"expression": expr}}

    return {
        "resourceType": "Measure",
        "url": "http://example.org/Measure/DemoFHIR",
        "name": "DemoFHIR",
        "version": "1.0.0",
        "library": ["http://example.org/Library/DemoLib"],
        "scoring": {"coding": [{"code": "proportion"}]},
        "improvementNotation": {"coding": [{"code": "increase"}]},
        "group": [
            {
                "id": "Group_1",
                "extension": [
                    {
                        "url": "http://hl7.org/fhir/us/cqfmeasures/StructureDefinition/cqfm-populationBasis",
                        "valueCode": "boolean",
                    }
                ],
                "population": [
                    pop("initial-population", "Initial Population"),
                    pop("denominator", "Denominator"),
                    pop("numerator", "Numerator"),
                ],
            }
        ],
    }


def _value_set(url: str, code: str) -> dict[str, Any]:
    return {"resourceType": "ValueSet", "url": url, "expansion": {"contains": [{"code": code}]}}


@pytest.fixture()
def packages_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "packages" / "DEMOFHIR"
    (pkg / "libraries").mkdir(parents=True)
    (pkg / "valuesets").mkdir(parents=True)
    (pkg / "measure.json").write_text(json.dumps(_demo_measure()), encoding="utf-8")
    (pkg / "libraries" / "DemoLib.json").write_text(json.dumps(_demo_library()), encoding="utf-8")
    (pkg / "valuesets" / "visit.json").write_text(json.dumps(_value_set("urn:oid:visit", "visit")), encoding="utf-8")
    (pkg / "valuesets" / "vital.json").write_text(json.dumps(_value_set("urn:oid:vital", "8480-6")), encoding="utf-8")
    return tmp_path / "packages"


def _context(bp_value: float) -> dict[str, Any]:
    return {
        "patient": {"resourceType": "Patient", "id": "p1", "birthDate": "1980-01-01"},
        "encounters": [
            {
                "resourceType": "Encounter",
                "id": "e1",
                "status": "finished",
                "type": [{"coding": [{"code": "visit"}]}],
                "period": {"start": "2026-02-01", "end": "2026-02-02"},
            }
        ],
        "observations": [
            {
                "resourceType": "Observation",
                "id": "o1",
                "status": "final",
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
                "valueQuantity": {"value": bp_value, "unit": "mm[Hg]", "code": "mm[Hg]"},
            }
        ],
    }


def test_dqm_executor_has_package(packages_dir: Path):
    executor = DQMExecutor(packages_dir=packages_dir)
    assert executor.has_package("DEMOFHIR") is True
    assert executor.has_package("NOPE") is False


def test_dqm_executor_numerator_met(packages_dir: Path):
    executor = DQMExecutor(packages_dir=packages_dir)
    result = executor.evaluate("DEMOFHIR", _context(120), "2026-01-01", "2027-01-01")
    assert result.in_initial_population is True
    assert result.in_denominator is True
    assert result.in_numerator is True
    assert result.controlled is True
    assert result.detail["measure_score"] == 1.0


def test_dqm_executor_numerator_not_met(packages_dir: Path):
    executor = DQMExecutor(packages_dir=packages_dir)
    result = executor.evaluate("DEMOFHIR", _context(160), "2026-01-01", "2027-01-01")
    assert result.in_denominator is True
    assert result.in_numerator is False
    assert result.detail["measure_score"] == 0.0


def test_dqm_executor_missing_package_flag(tmp_path: Path):
    executor = DQMExecutor(packages_dir=str(tmp_path / "does-not-exist"))
    assert executor.has_package("DEMOFHIR") is False
