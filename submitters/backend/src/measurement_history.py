"""Cohort-scoped measurement evaluation history.

Both evaluation paths on the submitters side write to the same audit
trail so the UI can show, per cohort, whether each measure run came from
an inbound provider submission or from the in-app "Evaluate measures"
tab.

* Submission-driven runs (``measure_runner._execute_member``) write one
  row per ``(submission, member, measure)`` with ``source="submission"``.
* Direct UI runs (``deqm.$evaluate-measure``) write one row per
  ``(member, measure)`` with ``source="direct"``.

Rows live in the existing cohorts container under
``docType=measurement_history`` (partition key matches existing
``measurement_execution`` partitioning so the same helper works).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


DOC_TYPE = "measurement_history"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return f"hist-{_now_ms()}-{uuid.uuid4().hex[:8]}"


def record_measurement_history(
    helper: Any,
    *,
    source: str,
    cohort_id: Optional[str],
    member_id: str,
    measure_id: str,
    engine: Optional[str] = None,
    submission_id: Optional[str] = None,
    source_stack: Optional[str] = None,
    status: str = "completed",
    http_status: Optional[int] = None,
    numerator: Optional[int] = None,
    denominator: Optional[int] = None,
    exclusion: Optional[bool] = None,
    note: Optional[str] = None,
    error: Optional[str] = None,
    report_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Persist one history row. Returns the stored doc or ``None`` on failure.

    Persistence failures are logged and swallowed so an audit write never
    breaks the foreground request that triggered the evaluation.
    """
    if helper is None:
        return None
    doc: Dict[str, Any] = {
        "id": _new_id(),
        "docType": DOC_TYPE,
        "source": source,
        "cohortId": cohort_id,
        "memberId": member_id,
        "measureId": measure_id,
        "engine": engine,
        "submissionId": submission_id,
        "sourceStack": source_stack,
        "status": status,
        "httpStatus": http_status,
        "numerator": numerator,
        "denominator": denominator,
        "exclusion": exclusion,
        "note": note,
        "error": error,
        "reportId": report_id,
        "createdAt": _now_ms(),
    }
    try:
        helper.upsert_doc(DOC_TYPE, doc["id"], doc)
        return doc
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist measurement_history row: %s", exc)
        return None


def list_measurement_history(
    helper: Any,
    *,
    cohort_id: Optional[str] = None,
    member_id: Optional[str] = None,
    submission_id: Optional[str] = None,
    limit: Optional[int] = 500,
) -> List[Dict[str, Any]]:
    """List history rows, newest first, optionally filtered."""
    if helper is None:
        return []
    try:
        rows = helper.list_docs(DOC_TYPE) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list measurement_history rows: %s", exc)
        return []

    def _match(row: Dict[str, Any]) -> bool:
        if cohort_id is not None and row.get("cohortId") != cohort_id:
            return False
        if member_id is not None and row.get("memberId") != member_id:
            return False
        if submission_id is not None and row.get("submissionId") != submission_id:
            return False
        return True

    filtered = [r for r in rows if isinstance(r, dict) and _match(r)]
    filtered.sort(key=lambda r: int(r.get("createdAt") or 0), reverse=True)
    if limit and len(filtered) > limit:
        filtered = filtered[:limit]
    return filtered
