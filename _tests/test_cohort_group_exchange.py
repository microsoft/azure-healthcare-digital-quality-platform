"""Tests for cohort ↔ FHIR Group exchange (issue #11).

Covers the Da Vinci ATR-aligned Group builder and parser on both the
submitters and receivers stacks:

- ``_deqm_group_resource`` / ``_atr_group_resource`` build a Group that carries
  the patient roster as ``member.entity`` Patient references with the ATR
  profile, attribution period, and in-scope measure characteristics.
- ``_atr_group_to_cohort`` parses a Group back into a workbench cohort doc.
- Round-trip cohort -> Group -> cohort preserves members, measures, and name.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from typing import Any, Dict, List
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Stubs for heavy optional deps (mirrors test_deqm_report_types.py)
# ---------------------------------------------------------------------------

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
_rec_wb = _load_workbench("receivers_workbench", _REC_SRC)

ATR_PROFILE = "http://hl7.org/fhir/us/davinci-atr/StructureDefinition/atr-group"


# ---------------------------------------------------------------------------
# Submitters Group builder
# ---------------------------------------------------------------------------

class TestSubmittersGroupBuilder:
    def _group(self) -> Dict[str, Any]:
        return _sub_wb._deqm_group_resource(
            "cohort-test",
            ["P001", "P002", "P003"],
            name="Test Cohort",
            measure_ids=["CMS122v11", "CMS165v9"],
            period_start="2026-01-01",
            period_end="2026-12-31",
        )

    def test_resource_type_and_id(self):
        g = self._group()
        assert g["resourceType"] == "Group"
        assert g["id"] == "group-cohort-test"

    def test_atr_profile(self):
        g = self._group()
        assert ATR_PROFILE in g["meta"]["profile"]

    def test_group_type_and_actual(self):
        g = self._group()
        assert g["type"] == "person"
        assert g["actual"] is True

    def test_quantity_matches_members(self):
        g = self._group()
        assert g["quantity"] == 3
        assert len(g["member"]) == 3

    def test_members_are_patient_references(self):
        g = self._group()
        refs = [m["entity"]["reference"] for m in g["member"]]
        assert refs == ["Patient/P001", "Patient/P002", "Patient/P003"]

    def test_members_have_inactive_and_period(self):
        g = self._group()
        for m in g["member"]:
            assert m["inactive"] is False
            assert m["period"]["start"] == "2026-01-01"
            assert m["period"]["end"] == "2026-12-31"

    def test_membership_characteristic_present(self):
        g = self._group()
        assert g["characteristic"], "characteristic is 1..* on the ATR profile"
        codes = [c["code"].get("text") for c in g["characteristic"]]
        assert "Quality measurement cohort membership" in codes

    def test_measure_characteristics(self):
        g = self._group()
        refs = [
            c["valueReference"]["reference"]
            for c in g["characteristic"]
            if c.get("valueReference")
        ]
        assert any(r.endswith("/Measure/CMS122v11") for r in refs)
        assert any(r.endswith("/Measure/CMS165v9") for r in refs)

    def test_no_period_still_valid(self):
        g = _sub_wb._deqm_group_resource("c1", ["P1"])
        assert g["member"][0].get("period") is None
        assert g["characteristic"]  # membership marker always present


# ---------------------------------------------------------------------------
# Parser + round-trip (submitters)
# ---------------------------------------------------------------------------

class TestGroupToCohort:
    def test_parse_basic(self):
        group = _sub_wb._deqm_group_resource(
            "cohort-abc", ["P1", "P2"], name="ABC", measure_ids=["CMS122v11"]
        )
        cohort_id, doc = _sub_wb._atr_group_to_cohort(group)
        assert cohort_id == "cohort-abc"
        assert doc["name"] == "ABC"
        assert doc["memberIds"] == ["P1", "P2"]
        assert doc["measureIds"] == ["CMS122v11"]
        assert doc["source"] == "fhir-group-import"
        assert doc["docType"] == "cohort"

    def test_round_trip_preserves_roster(self):
        original_members = ["P001", "P002", "P003", "P004"]
        original_measures = ["CMS122v11", "CMS165v9"]
        group = _sub_wb._deqm_group_resource(
            "cohort-rt",
            original_members,
            name="Round Trip",
            measure_ids=original_measures,
        )
        _, doc = _sub_wb._atr_group_to_cohort(group)
        assert doc["memberIds"] == original_members
        assert doc["measureIds"] == original_measures
        assert doc["name"] == "Round Trip"

    def test_id_without_group_prefix(self):
        group = {
            "resourceType": "Group",
            "id": "raw-id",
            "member": [{"entity": {"reference": "Patient/X"}}],
        }
        cohort_id, doc = _sub_wb._atr_group_to_cohort(group)
        assert cohort_id == "raw-id"
        assert doc["memberIds"] == ["X"]

    def test_ignores_non_patient_members(self):
        group = {
            "resourceType": "Group",
            "id": "group-c",
            "member": [
                {"entity": {"reference": "Patient/P1"}},
                {"entity": {"reference": "Practitioner/DR1"}},
                {"entity": {"reference": "Group/nested"}},
            ],
        }
        _, doc = _sub_wb._atr_group_to_cohort(group)
        assert doc["memberIds"] == ["P1"]


# ---------------------------------------------------------------------------
# Receivers mirror
# ---------------------------------------------------------------------------

class TestReceiversGroupBuilder:
    def test_builder_mirrors_submitters(self):
        g = _rec_wb._atr_group_resource(
            "cohort-x", ["A", "B"], name="X", measure_ids=["CMS122v11"]
        )
        assert g["resourceType"] == "Group"
        assert g["id"] == "group-cohort-x"
        assert ATR_PROFILE in g["meta"]["profile"]
        assert g["quantity"] == 2
        assert [m["entity"]["reference"] for m in g["member"]] == ["Patient/A", "Patient/B"]

    def test_receivers_round_trip(self):
        g = _rec_wb._atr_group_resource("cohort-y", ["A", "B", "C"], name="Y")
        cohort_id, doc = _rec_wb._atr_group_to_cohort(g)
        assert cohort_id == "cohort-y"
        assert doc["memberIds"] == ["A", "B", "C"]
        assert doc["source"] == "fhir-group-import"

    def test_cross_stack_export_import(self):
        """A Group exported by submitters imports cleanly on receivers."""
        exported = _sub_wb._deqm_group_resource(
            "cohort-cross", ["P1", "P2"], name="Cross", measure_ids=["CMS165v9"]
        )
        cohort_id, doc = _rec_wb._atr_group_to_cohort(exported)
        assert cohort_id == "cohort-cross"
        assert doc["memberIds"] == ["P1", "P2"]
        assert doc["measureIds"] == ["CMS165v9"]
