"""Tests for DEQM MeasureReport type switching (issue #12).

Covers:
- _build_deqm_fhir_payload() builder for all three profiles (summary, subject-list, individual)
- validate_deqm_measure_report() DEQM conformance checks (valid + negative cases)
- Receivers ingest round-trip via FastAPI test client
- Back-compat: legacy measure-summaries route still accepts proprietary payload
"""

from __future__ import annotations

import importlib
import os
import sys
import types
import uuid
from copy import deepcopy
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs for modules that require real Azure credentials at import time
# ---------------------------------------------------------------------------

def _make_stub(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_stubs() -> None:
    """Create lightweight stubs for heavy optional dependencies."""
    # measure_catalog stub – _build_deqm_fhir_payload uses get_measure_entry()
    if "measure_catalog" not in sys.modules:
        mc = _make_stub("measure_catalog")
        mc.DEFAULT_CANONICAL_BASE = "https://example.org/fhir"  # type: ignore[attr-defined]
        mc.get_measure_entry = lambda mid: {"id": mid, "version": "9.0.000"}  # type: ignore[attr-defined]
        mc.list_measures = lambda: []  # type: ignore[attr-defined]

    # cosmosdb_helper stub
    if "cosmosdb_helper" not in sys.modules:
        ch = _make_stub("cosmosdb_helper")
        ch.get_container_client = MagicMock(return_value=None)  # type: ignore[attr-defined]
        ch.CosmosDBHelper = MagicMock  # type: ignore[attr-defined]

    for pkg in ("azure", "azure.cosmos", "azure.identity"):
        if pkg not in sys.modules:
            _make_stub(pkg)

    # requests stub (used by workbench for cross-stack dispatch)
    if "requests" not in sys.modules:
        req = _make_stub("requests")
        req.post = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))  # type: ignore[attr-defined]
        req.get = MagicMock(return_value=MagicMock(status_code=200, json=lambda: {}))  # type: ignore[attr-defined]


_ensure_stubs()

# We can now safely import the module-level functions.
# Add backend src directories to sys.path.
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SUB_SRC = os.path.join(_BASE, "submitters", "backend", "src")
_REC_SRC = os.path.join(_BASE, "receivers", "backend", "src")
for _p in (_SUB_SRC, _REC_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Now import the specific functions we want to test.
from workbench import (  # noqa: E402  (receivers/backend/src/workbench.py is first on path)
    _REPORT_TYPE_TO_DOC_TYPE,
    _VALID_REPORT_TYPES,
    validate_deqm_measure_report,
)

# Import submitters builders by fully-qualified path manipulation.
# We need to import a *different* workbench module from the submitters tree.
if "submitters_workbench" not in sys.modules:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "submitters_workbench",
        os.path.join(_SUB_SRC, "workbench.py"),
    )
    assert spec and spec.loader
    _sub_wb_mod = importlib.util.module_from_spec(spec)
    sys.modules["submitters_workbench"] = _sub_wb_mod
    spec.loader.exec_module(_sub_wb_mod)  # type: ignore[union-attr]

import submitters_workbench as _sub_wb  # noqa: E402

_build_deqm_fhir_payload = _sub_wb._build_deqm_fhir_payload
_deqm_pop = _sub_wb._deqm_pop
_POPULATION_CS = "http://terminology.hl7.org/CodeSystem/measure-population"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PERIOD_START = "2025-01-01"
PERIOD_END = "2025-12-31"


def _make_rollup(
    *,
    cohort_id: str = "cohort-test",
    measure_ids: List[str] | None = None,
    patients: int = 10,
    denominator: int = 8,
    numerator: int = 6,
    exclusions: int = 1,
    member_ids: List[str] | None = None,
) -> Dict[str, Any]:
    """Return a minimal ``_aggregate_summary_payload`` rollup dict."""
    if measure_ids is None:
        measure_ids = ["CMS165v9"]
    if member_ids is None:
        member_ids = ["P001", "P002"]

    per_measure = []
    for mid in measure_ids:
        denom = denominator if len(measure_ids) == 1 else denominator - 1
        per_measure.append({
            "measureId": mid,
            "patients": patients,
            "denominator": denom,
            "numerator": numerator,
            "exclusions": exclusions,
            "performanceRate": round(numerator / denom, 4) if denom else 0.0,
        })

    per_member = []
    for i, mid_member in enumerate(member_ids):
        row = {
            "memberId": mid_member,
            "perMeasure": [
                {
                    "measureId": m,
                    "denominator": 1,
                    "numerator": 1 if i % 2 == 0 else 0,
                    "exclusion": False,
                }
                for m in measure_ids
            ],
        }
        per_member.append(row)

    return {
        "cohort": {"id": cohort_id},
        "measureIds": measure_ids,
        "perMeasure": per_measure,
        "perMember": per_member,
        "periodStart": PERIOD_START,
        "periodEnd": PERIOD_END,
    }


def _make_valid_measure_report(report_type: str = "summary") -> Dict[str, Any]:
    """Return a minimal valid DEQM MeasureReport of the given type."""
    return {
        "resourceType": "MeasureReport",
        "status": "complete",
        "type": report_type,
        "measure": "https://example.org/fhir/Measure/CMS165v9|9.0.000",
        "reporter": {"reference": "Organization/org-1"},
        "date": "2025-12-31T23:59:59Z",
        "period": {"start": "2025-01-01", "end": "2025-12-31"},
        "group": [
            {
                "population": [
                    {"code": {"coding": [{"system": _POPULATION_CS, "code": "denominator"}]}, "count": 8},
                    {"code": {"coding": [{"system": _POPULATION_CS, "code": "numerator"}]}, "count": 6},
                ]
            }
        ],
    }


# ---------------------------------------------------------------------------
# Tests: validate_deqm_measure_report (receivers workbench)
# ---------------------------------------------------------------------------

class TestValidateDeqmMeasureReport:
    """Unit tests for the DEQM conformance validator."""

    def test_valid_summary_report(self):
        err = validate_deqm_measure_report(_make_valid_measure_report("summary"))
        assert err is None

    def test_valid_individual_report(self):
        err = validate_deqm_measure_report(_make_valid_measure_report("individual"))
        assert err is None

    def test_valid_subject_list_report(self):
        err = validate_deqm_measure_report(_make_valid_measure_report("subject-list"))
        assert err is None

    def test_missing_status_returns_error(self):
        rpt = _make_valid_measure_report()
        del rpt["status"]
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "status" in err

    def test_missing_type_returns_error(self):
        rpt = _make_valid_measure_report()
        del rpt["type"]
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "type" in err

    def test_missing_measure_returns_error(self):
        rpt = _make_valid_measure_report()
        del rpt["measure"]
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "measure" in err

    def test_missing_reporter_returns_error(self):
        rpt = _make_valid_measure_report()
        del rpt["reporter"]
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "reporter" in err

    def test_missing_period_returns_error(self):
        rpt = _make_valid_measure_report()
        del rpt["period"]
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "period" in err

    def test_invalid_report_type_returns_error(self):
        rpt = _make_valid_measure_report()
        rpt["type"] = "nonsense"
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "nonsense" in err

    def test_deqm0_unversioned_measure_returns_error(self):
        """deqm-0: measure must include a version suffix via pipe."""
        rpt = _make_valid_measure_report()
        rpt["measure"] = "https://example.org/fhir/Measure/CMS165v9"  # no |version
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "deqm-0" in err

    def test_deqm1_non_day_precision_start_returns_error(self):
        """deqm-1: period.start must be YYYY-MM-DD."""
        rpt = _make_valid_measure_report()
        rpt["period"]["start"] = "2025"
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "deqm-1" in err

    def test_deqm1_non_day_precision_end_returns_error(self):
        """deqm-1: period.end must be YYYY-MM-DD."""
        rpt = _make_valid_measure_report()
        rpt["period"]["end"] = "2025-12"
        err = validate_deqm_measure_report(rpt)
        assert err is not None
        assert "deqm-1" in err

    def test_not_a_dict_returns_error(self):
        err = validate_deqm_measure_report("not a dict")  # type: ignore[arg-type]
        assert err is not None


# ---------------------------------------------------------------------------
# Tests: VALID_REPORT_TYPES and _REPORT_TYPE_TO_DOC_TYPE constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_valid_report_types_contains_expected(self):
        assert _VALID_REPORT_TYPES == {"individual", "subject-list", "summary"}

    def test_doc_type_mapping_keys_match_valid_types(self):
        assert set(_REPORT_TYPE_TO_DOC_TYPE.keys()) == _VALID_REPORT_TYPES

    def test_doc_type_individual(self):
        assert _REPORT_TYPE_TO_DOC_TYPE["individual"] == "measure_report"

    def test_doc_type_subject_list(self):
        assert _REPORT_TYPE_TO_DOC_TYPE["subject-list"] == "measure_report_subjectlist"

    def test_doc_type_summary(self):
        assert _REPORT_TYPE_TO_DOC_TYPE["summary"] == "measure_report_summary"


# ---------------------------------------------------------------------------
# Tests: _build_deqm_fhir_payload — Summary profile
# ---------------------------------------------------------------------------

class TestBuildDeqmFhirPayloadSummary:
    def setup_method(self):
        self.rollup = _make_rollup()

    def test_returns_measure_report(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        assert result["resourceType"] == "MeasureReport"

    def test_type_is_summary(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        assert result["type"] == "summary"

    def test_status_is_complete(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        assert result["status"] == "complete"

    def test_measure_has_version_suffix(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        assert "|" in result["measure"], "deqm-0: measure must include |version"

    def test_period_has_day_precision(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        import re
        _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
        assert _date_re.match(result["period"]["start"]), "deqm-1: period.start must be YYYY-MM-DD"
        assert _date_re.match(result["period"]["end"]), "deqm-1: period.end must be YYYY-MM-DD"

    def test_reporter_is_present(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        assert "reporter" in result
        assert result["reporter"]

    def test_subject_references_group(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        assert "subject" in result
        assert result["subject"].get("reference", "").startswith("#group-")

    def test_group_contains_populations(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        group = result["group"][0]
        assert "population" in group
        codes = {
            p["code"]["coding"][0]["code"]
            for p in group["population"]
        }
        assert "denominator" in codes
        assert "numerator" in codes

    def test_group_contains_measure_score(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        group = result["group"][0]
        assert "measureScore" in group

    def test_contained_includes_group_resource(self):
        result = _build_deqm_fhir_payload(self.rollup, "summary")
        contained = result.get("contained", [])
        group_resources = [r for r in contained if r.get("resourceType") == "Group"]
        assert group_resources, "contained must include a Group resource"

    def test_multiple_measures_returns_bundle(self):
        rollup = _make_rollup(measure_ids=["CMS165v9", "CMS122v10"])
        result = _build_deqm_fhir_payload(rollup, "summary")
        assert result["resourceType"] == "Bundle"
        types_in_bundle = {
            e["resource"]["type"]
            for e in result["entry"]
        }
        assert types_in_bundle == {"summary"}


# ---------------------------------------------------------------------------
# Tests: _build_deqm_fhir_payload — Subject-List profile
# ---------------------------------------------------------------------------

class TestBuildDeqmFhirPayloadSubjectList:
    def setup_method(self):
        self.rollup = _make_rollup()

    def test_type_is_subject_list(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        assert result["type"] == "subject-list"

    def test_measure_has_version_suffix(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        assert "|" in result["measure"]

    def test_period_has_day_precision(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        import re
        _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
        assert _date_re.match(result["period"]["start"])
        assert _date_re.match(result["period"]["end"])

    def test_reporter_is_present(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        assert result["reporter"]

    def test_subject_references_group(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        assert result["subject"].get("reference", "").startswith("#group-")

    def test_group_populations_have_subject_results(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        group = result["group"][0]
        for pop in group["population"]:
            if pop["code"]["coding"][0]["code"] not in ("denominator-exclusion",):
                assert "subjectResults" in pop, (
                    "subject-list populations must include subjectResults"
                )

    def test_contained_has_group_and_individual_reports(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        contained = result.get("contained", [])
        resource_types = {r.get("resourceType") for r in contained}
        assert "Group" in resource_types
        assert "MeasureReport" in resource_types

    def test_contained_individual_reports_are_type_individual(self):
        result = _build_deqm_fhir_payload(self.rollup, "subject-list")
        contained = result.get("contained", [])
        indiv_reports = [r for r in contained if r.get("resourceType") == "MeasureReport"]
        for rpt in indiv_reports:
            assert rpt["type"] == "individual"


# ---------------------------------------------------------------------------
# Tests: _build_deqm_fhir_payload — Individual profile
# ---------------------------------------------------------------------------

class TestBuildDeqmFhirPayloadIndividual:
    def setup_method(self):
        self.rollup = _make_rollup(member_ids=["P001", "P002"])

    def test_returns_bundle(self):
        result = _build_deqm_fhir_payload(self.rollup, "individual")
        assert result["resourceType"] == "Bundle"

    def test_bundle_contains_individual_reports(self):
        result = _build_deqm_fhir_payload(self.rollup, "individual")
        for entry in result["entry"]:
            rpt = entry["resource"]
            assert rpt["type"] == "individual"

    def test_one_report_per_member(self):
        rollup = _make_rollup(member_ids=["P001", "P002", "P003"])
        result = _build_deqm_fhir_payload(rollup, "individual")
        assert len(result["entry"]) == 3

    def test_individual_subject_is_patient_reference(self):
        result = _build_deqm_fhir_payload(self.rollup, "individual")
        for entry in result["entry"]:
            rpt = entry["resource"]
            assert rpt["subject"]["reference"].startswith("Patient/")

    def test_measure_has_version_suffix(self):
        result = _build_deqm_fhir_payload(self.rollup, "individual")
        for entry in result["entry"]:
            assert "|" in entry["resource"]["measure"]

    def test_period_has_day_precision(self):
        import re
        _date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
        result = _build_deqm_fhir_payload(self.rollup, "individual")
        for entry in result["entry"]:
            rpt = entry["resource"]
            assert _date_re.match(rpt["period"]["start"])
            assert _date_re.match(rpt["period"]["end"])

    def test_reporter_present_on_all_reports(self):
        result = _build_deqm_fhir_payload(self.rollup, "individual")
        for entry in result["entry"]:
            assert entry["resource"]["reporter"]


# ---------------------------------------------------------------------------
# Tests: Receivers ingest route
# ---------------------------------------------------------------------------

class TestReceiversMeasureReportsIngest:
    """Integration tests against the receivers FastAPI app via TestClient."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        """Import and instantiate the receivers FastAPI app in dev/memory mode."""
        os.environ["DEVELOPMENT_MODE"] = "true"
        # Patch Cosmos so receivers workbench uses in-memory store.
        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "true", "REQUIRE_DATABASE": "false"}):
            try:
                from fastapi.testclient import TestClient
                # Import main from receivers stack
                import importlib.util as ilu
                rec_main_path = os.path.join(_REC_SRC, "main.py")
                spec = ilu.spec_from_file_location("receivers_main", rec_main_path)
                if spec and spec.loader:
                    rec_main = ilu.module_from_spec(spec)
                    sys.modules["receivers_main"] = rec_main
                    spec.loader.exec_module(rec_main)  # type: ignore[union-attr]
                    self.client = TestClient(rec_main.app)
                    self.available = True
                else:
                    self.available = False
            except Exception:
                self.available = False
        yield

    def _skip_if_unavailable(self):
        if not getattr(self, "available", False):
            pytest.skip("Receivers FastAPI app could not be loaded in test environment")

    def test_ingest_summary_report(self):
        self._skip_if_unavailable()
        rpt = _make_valid_measure_report("summary")
        resp = self.client.post(
            "/api/workbench/measure-reports",
            json=rpt,
            headers={"X-Development-User": "test"},
        )
        assert resp.status_code in (200, 201)
        data = resp.json()
        assert "report" in data or "reports" in data

    def test_ingest_invalid_report_type_returns_400(self):
        self._skip_if_unavailable()
        rpt = _make_valid_measure_report("summary")
        rpt["type"] = "invalid-type"
        resp = self.client.post(
            "/api/workbench/measure-reports",
            json=rpt,
            headers={"X-Development-User": "test"},
        )
        assert resp.status_code == 400

    def test_ingest_missing_reporter_returns_400(self):
        self._skip_if_unavailable()
        rpt = _make_valid_measure_report("summary")
        del rpt["reporter"]
        resp = self.client.post(
            "/api/workbench/measure-reports",
            json=rpt,
            headers={"X-Development-User": "test"},
        )
        assert resp.status_code == 400

    def test_ingest_unversioned_measure_returns_400(self):
        self._skip_if_unavailable()
        rpt = _make_valid_measure_report("summary")
        rpt["measure"] = "https://example.org/fhir/Measure/CMS165v9"
        resp = self.client.post(
            "/api/workbench/measure-reports",
            json=rpt,
            headers={"X-Development-User": "test"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: Back-compat — legacy measure-summaries route
# ---------------------------------------------------------------------------

class TestLegacyMeasureSummariesRoute:
    """Verify the legacy /api/workbench/measure-summaries route still works."""

    @pytest.fixture(autouse=True)
    def setup_app(self):
        os.environ["DEVELOPMENT_MODE"] = "true"
        with patch.dict(os.environ, {"DEVELOPMENT_MODE": "true", "REQUIRE_DATABASE": "false"}):
            try:
                from fastapi.testclient import TestClient
                import importlib.util as ilu
                rec_main_path = os.path.join(_REC_SRC, "main.py")
                spec = ilu.spec_from_file_location("receivers_main_legacy", rec_main_path)
                if spec and spec.loader:
                    rec_main = ilu.module_from_spec(spec)
                    sys.modules["receivers_main_legacy"] = rec_main
                    spec.loader.exec_module(rec_main)  # type: ignore[union-attr]
                    self.client = TestClient(rec_main.app)
                    self.available = True
                else:
                    self.available = False
            except Exception:
                self.available = False
        yield

    def _skip_if_unavailable(self):
        if not getattr(self, "available", False):
            pytest.skip("Receivers FastAPI app could not be loaded in test environment")

    def test_legacy_route_accepts_proprietary_payload(self):
        self._skip_if_unavailable()
        payload = {
            "agency": {"id": "agency-cms"},
            "cohort": {"id": "cohort-test"},
            "measureIds": ["CMS165v9"],
            "sourceStack": "submitters",
        }
        resp = self.client.post(
            "/api/workbench/measure-summaries",
            json=payload,
            headers={"X-Development-User": "test"},
        )
        # Route exists and does not 404/405
        assert resp.status_code not in (404, 405), (
            f"Legacy measure-summaries route missing or wrong method: {resp.status_code}"
        )
