"""Tests for QPP submission metadata + DEQM reporting enhancements (issue #14).

Covers the submitters MeasureReport builder additions and the receivers ingest:

- proof-of-submission identifier (MeasureReport.identifier)
- submission-method + reporting-role extensions
- reporter.type (reporting entity type)
- decimal numerator/denominator via deqm-population-count-decimal extension
- QCDR performanceNotMet + denominator-exception
- CAHPS/ACR survey supplemental result extension
- receivers ingest captures the metadata and issues a receipt identifier
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from typing import Any, Dict, List
from unittest.mock import MagicMock


def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_stubs() -> None:
    if "measure_catalog" not in sys.modules:
        mc = _make_stub("measure_catalog")
        mc.DEFAULT_CANONICAL_BASE = "https://example.org/fhir"  # type: ignore[attr-defined]
        mc.get_measure_entry = lambda mid: {"id": mid, "version": "9.0.000"}  # type: ignore[attr-defined]
        mc.list_measures = lambda: []  # type: ignore[attr-defined]
    if "cosmosdb_helper" not in sys.modules:
        ch = _make_stub("cosmosdb_helper")
        ch.get_container_client = MagicMock(return_value=None)  # type: ignore[attr-defined]
        ch.CosmosDBHelper = MagicMock  # type: ignore[attr-defined]
    for pkg in ("azure", "azure.cosmos", "azure.identity"):
        if pkg not in sys.modules:
            _make_stub(pkg)
    if "requests" not in sys.modules:
        req = _make_stub("requests")
        req.post = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))  # type: ignore[attr-defined]
        req.get = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))  # type: ignore[attr-defined]


_ensure_stubs()

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUB_SRC = os.path.join(_BASE, "submitters", "backend", "src")
_REC_SRC = os.path.join(_BASE, "receivers", "backend", "src")


def _load_workbench(mod_name: str, src_dir: str) -> Any:
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    spec = importlib.util.spec_from_file_location(
        mod_name, os.path.join(src_dir, "workbench.py")
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_sub_wb = _load_workbench("submitters_workbench", _SUB_SRC)


def _summary_payload(
    *,
    send_id: str = "mr-send-123",
    survey: Dict[str, Any] | None = None,
    exceptions: int = 0,
    numerator: Any = 6,
    denominator: Any = 8,
) -> Dict[str, Any]:
    rollup: Dict[str, Any] = {
        "measureId": "CMS165v9",
        "title": "Controlling High Blood Pressure",
        "denominator": denominator,
        "numerator": numerator,
        "patients": 10,
        "exclusions": 1,
        "exceptions": exceptions,
        "performanceRate": 0.75,
    }
    if survey is not None:
        rollup["survey"] = survey
    return {
        "cohort": {"id": "cohort-test", "name": "Test"},
        "measureIds": ["CMS165v9"],
        "periodStart": "2026-01-01",
        "periodEnd": "2026-12-31",
        "sourceSendId": send_id,
        "perMeasure": [rollup],
        "perMember": [
            {"memberId": "P001", "perMeasure": [{"measureId": "CMS165v9", "denominator": 1, "numerator": 1}]},
            {"memberId": "P002", "perMeasure": [{"measureId": "CMS165v9", "denominator": 1, "numerator": 0}]},
        ],
    }


def _find_ext(resource: Dict[str, Any], suffix: str) -> Dict[str, Any] | None:
    for ext in resource.get("extension") or []:
        if (ext.get("url") or "").endswith(suffix):
            return ext
    return None


# ---------------------------------------------------------------------------
# Helper-level unit tests
# ---------------------------------------------------------------------------

class TestPopulationDecimal:
    def test_integer_count_has_no_extension(self):
        pop = _sub_wb._deqm_pop("numerator", 8)
        assert pop["count"] == 8
        assert "extension" not in pop

    def test_float_integral_has_no_extension(self):
        pop = _sub_wb._deqm_pop("numerator", 8.0)
        assert pop["count"] == 8
        assert "extension" not in pop

    def test_decimal_preserved_via_extension(self):
        pop = _sub_wb._deqm_pop("denominator", 8.5)
        assert pop["count"] == 8
        ext = _find_ext(pop, "deqm-population-count-decimal")
        assert ext is not None
        assert ext["valueDecimal"] == 8.5

    def test_none_count_defaults_zero(self):
        pop = _sub_wb._deqm_pop("numerator", None)
        assert pop["count"] == 0


class TestReporterAndConfig:
    def test_reporter_type_added(self):
        r = _sub_wb._deqm_reporter("Organization")
        assert r["type"] == "Organization"

    def test_reporter_default_display(self):
        r = _sub_wb._deqm_reporter()
        assert r.get("display")
        assert "type" not in r

    def test_submission_method_override(self):
        cc = _sub_wb._deqm_submission_method("ehr")
        assert cc is not None
        assert cc["coding"][0]["code"] == "ehr"

    def test_submission_method_empty(self):
        assert _sub_wb._deqm_submission_method("") is None

    def test_reporting_role_override(self):
        cc = _sub_wb._deqm_reporting_role("group")
        assert cc["coding"][0]["code"] == "group"


# ---------------------------------------------------------------------------
# Builder integration
# ---------------------------------------------------------------------------

class TestSummaryReportMetadata:
    def _report(self, **kwargs) -> Dict[str, Any]:
        return _sub_wb._build_deqm_fhir_payload(
            _summary_payload(**kwargs.pop("payload", {})),
            "summary",
            submission_method=kwargs.get("submission_method", "ehr"),
            reporting_role=kwargs.get("reporting_role", "group"),
            reporter_type=kwargs.get("reporter_type", "Organization"),
        )

    def test_proof_of_submission_identifier(self):
        r = self._report()
        idents = r.get("identifier") or []
        assert idents and idents[0]["value"] == "mr-send-123"

    def test_submission_method_extension(self):
        r = self._report()
        ext = _find_ext(r, "deqm-submission-method")
        assert ext is not None
        assert ext["valueCodeableConcept"]["coding"][0]["code"] == "ehr"

    def test_reporting_role_extension(self):
        r = self._report()
        ext = _find_ext(r, "deqm-reporting-role")
        assert ext is not None
        assert ext["valueCodeableConcept"]["coding"][0]["code"] == "group"

    def test_reporter_type(self):
        r = self._report()
        assert r["reporter"]["type"] == "Organization"

    def test_performance_not_met(self):
        # denom=8, num=6, excl=1, exceptions=0 -> performanceNotMet = 1
        r = self._report()
        group = r["group"][0]
        ext = _find_ext(group, "deqm-performance-not-met")
        assert ext is not None
        assert ext["valueInteger"] == 1

    def test_denominator_exception_population(self):
        r = self._report(payload={"exceptions": 2})
        codes = [
            p["code"]["coding"][0]["code"] for p in r["group"][0]["population"]
        ]
        assert "denominator-exception" in codes

    def test_survey_extension(self):
        r = self._report(
            payload={"survey": {"reliabilityScore": 0.82, "maskIndicator": True, "belowMinimum": False}}
        )
        ext = _find_ext(r["group"][0], "deqm-survey-result")
        assert ext is not None
        subs = {s["url"]: s for s in ext["extension"]}
        assert subs["reliabilityScore"]["valueDecimal"] == 0.82
        assert subs["maskIndicator"]["valueBoolean"] is True
        assert subs["belowMinimum"]["valueBoolean"] is False

    def test_decimal_numerator_in_population(self):
        r = self._report(payload={"numerator": 6.5})
        num_pop = next(
            p for p in r["group"][0]["population"]
            if p["code"]["coding"][0]["code"] == "numerator"
        )
        ext = _find_ext(num_pop, "deqm-population-count-decimal")
        assert ext is not None
        assert ext["valueDecimal"] == 6.5

    def test_no_metadata_when_unset(self):
        r = _sub_wb._build_deqm_fhir_payload(_summary_payload(), "summary")
        # identifier still set from send_id, but no submission-method/reporting-role
        assert _find_ext(r, "deqm-submission-method") is None
        assert _find_ext(r, "deqm-reporting-role") is None
        assert "type" not in r["reporter"]


class TestIndividualAndSubjectListMetadata:
    def test_individual_reports_carry_identifier(self):
        bundle = _sub_wb._build_deqm_fhir_payload(
            _summary_payload(), "individual", submission_method="registry"
        )
        reports = [e["resource"] for e in bundle["entry"]]
        assert reports
        for rep in reports:
            assert (rep.get("identifier") or [])[0]["value"] == "mr-send-123"
            assert _find_ext(rep, "deqm-submission-method") is not None

    def test_subject_list_top_report_metadata(self):
        r = _sub_wb._build_deqm_fhir_payload(
            _summary_payload(), "subject-list", reporting_role="apm-entity"
        )
        assert (r.get("identifier") or [])[0]["value"] == "mr-send-123"
        assert _find_ext(r, "deqm-reporting-role") is not None


# ---------------------------------------------------------------------------
# Receivers ingest captures metadata + issues a receipt
# ---------------------------------------------------------------------------

class TestReceiverIngestReceipt:
    def test_ingest_captures_metadata_and_receipt(self):
        report = _sub_wb._build_deqm_fhir_payload(
            _summary_payload(),
            "summary",
            submission_method="ehr",
            reporting_role="group",
            reporter_type="Organization",
        )

        # Load receivers workbench and build a router backed by an in-memory helper.
        rec_wb = _load_workbench("receivers_workbench", _REC_SRC)

        class _MemHelper:
            def __init__(self) -> None:
                self.store: Dict[str, Dict[str, Dict[str, Any]]] = {}

            def upsert_doc(self, dt: str, item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
                self.store.setdefault(dt, {})[item_id] = payload
                return payload

            def get_doc(self, dt: str, item_id: str):
                return self.store.get(dt, {}).get(item_id)

            def list_docs(self, dt: str):
                return list(self.store.get(dt, {}).values())

            def delete_doc(self, dt: str, item_id: str) -> bool:
                return self.store.get(dt, {}).pop(item_id, None) is not None

        from starlette.testclient import TestClient
        from fastapi import FastAPI

        router = rec_wb.create_workbench_router(
            catalog_helper=_MemHelper(),
            cohorts_helper=_MemHelper(),
            auth_dependency=lambda: {"sub": "test"},
        )
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        resp = client.post("/api/workbench/measure-reports", json=report)
        assert resp.status_code == 200, resp.text
        doc = resp.json()["report"]
        assert doc["submissionIdentifier"] == "mr-send-123"
        assert doc["submissionMethod"] == "ehr"
        assert doc["reportingRole"] == "group"
        assert doc["reporterType"] == "Organization"
        assert doc["receiptIdentifier"].startswith("receipt-")
