from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_BASE = Path(__file__).resolve().parents[1]
_REC_SRC = _BASE / "receivers" / "backend" / "src"
if str(_REC_SRC) not in sys.path:
    sys.path.insert(0, str(_REC_SRC))

from receiver_reporting import ReceiverReportingSink  # noqa: E402


def _measure_report() -> dict[str, Any]:
    return {
        "resourceType": "MeasureReport",
        "id": "mr-1",
        "status": "complete",
        "type": "summary",
        "measure": "https://example.org/fhir/Measure/CMS165v9|9.0.000",
        "reporter": {"reference": "Organization/HOSP-001", "display": "Contoso Health"},
        "date": "2026-01-15T00:00:00Z",
        "period": {"start": "2026-01-01", "end": "2026-12-31"},
        "group": [
            {
                "population": [
                    {"code": {"coding": [{"code": "denominator"}]}, "count": 10},
                    {"code": {"coding": [{"code": "numerator"}]}, "count": 8},
                    {"code": {"coding": [{"code": "denominator-exclusion"}]}, "count": 1},
                ]
            }
        ],
    }


def test_measure_report_populates_reporting_sql_parameters(monkeypatch):
    monkeypatch.setenv("RECEIVER_DEFAULT_PROGRAM_ID", "MIPS-2026")
    sink = ReceiverReportingSink("Driver={ODBC Driver 18 for SQL Server};Server=tcp:example.database.windows.net;Database=dq", enabled=True)
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def capture(sql: str, *params: Any) -> bool:
        calls.append((sql, params))
        return True

    monkeypatch.setattr(sink, "_execute", capture)

    assert sink.persist_measure_report({"id": "mr-1", "subjectId": "P001", "report": _measure_report()}) is True

    merged_params = [param for _, params in calls for param in params]
    assert "CMS165v9" in merged_params
    assert "HOSP-001" in merged_params
    assert "MIPS-2026" in merged_params
    assert 8 in merged_params
    assert 10 in merged_params
    assert 0.8 in merged_params


def test_submission_parameters_extract_submitter(monkeypatch):
    monkeypatch.setenv("RECEIVER_DEFAULT_PROGRAM_ID", "ACO-2026")
    sink = ReceiverReportingSink("Driver={ODBC Driver 18 for SQL Server};Server=tcp:example.database.windows.net;Database=dq", enabled=True)
    calls: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(sink, "_execute", lambda sql, *params: calls.append((sql, params)) or True)

    record = {
        "id": "sub-1",
        "measureId": "CMS122v11",
        "receivedAtUtc": "2026-02-01T00:00:00Z",
        "parameters": {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "reporter", "valueReference": {"reference": "Organization/ACO-101", "display": "Woodgrove ACO"}}
            ],
        },
    }

    assert sink.persist_submission(record) is True
    merged_params = [param for _, params in calls for param in params]
    assert "CMS122v11" in merged_params
    assert "ACO-101" in merged_params
    assert "Woodgrove ACO" in merged_params
    assert "accepted" in merged_params
