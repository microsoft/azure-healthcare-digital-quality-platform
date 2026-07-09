"""Receiver reporting persistence helpers for Azure SQL Database.

The receivers API remains functional without SQL connectivity. When
``RECEIVER_REPORTING_SQL_ENABLED`` and SQL connection settings are present, this
module mirrors DEQM submissions, MeasureReports, and processing events into the
receiver reporting schema under ``dq``.
"""

from __future__ import annotations

import json
import logging
import os
import struct
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# ODBC connection attribute for a pre-acquired Microsoft Entra access token and
# the token scope for Azure SQL Database.
_SQL_COPT_SS_ACCESS_TOKEN = 1256
_AZURE_SQL_TOKEN_SCOPE = "https://database.windows.net/.default"

_POPULATION_CODES = {
    "initial-population": "initialPopulation",
    "denominator": "denominator",
    "denominator-exclusion": "exclusions",
    "denominator-exclusions": "exclusions",
    "numerator": "numerator",
}


class ReceiverReportingSink:
    def __init__(self, connection_string: str = "", enabled: bool = False):
        self.connection_string = connection_string
        self.enabled = enabled and bool(connection_string)
        self._credential: Any = None

    @classmethod
    def from_environment(cls) -> "ReceiverReportingSink":
        enabled = os.getenv("RECEIVER_REPORTING_SQL_ENABLED", "false").lower() in ("true", "1", "yes", "on")
        connection_string = os.getenv("AZURE_SQL_CONNECTION_STRING", "").strip()
        if not connection_string:
            server_fqdn = os.getenv("AZURE_SQL_SERVER_FQDN", "").strip()
            server_name = os.getenv("AZURE_SQL_SERVER_NAME", "").strip()
            database = os.getenv("AZURE_SQL_DATABASE_NAME", "").strip()
            if server_fqdn:
                server = server_fqdn
                if "." not in server:
                    logger.warning("AZURE_SQL_SERVER_FQDN does not look fully qualified: %s", server)
            elif server_name:
                server = server_name if "." in server_name else f"{server_name}.database.windows.net"
                if server != server_name:
                    logger.info("Constructed Azure SQL FQDN from AZURE_SQL_SERVER_NAME: %s", server)
            else:
                server = ""
            if server and database:
                # Build a connection string without an ODBC Authentication method.
                # The Microsoft Entra access token is injected at connect time (see
                # _execute), so the same code path works for AKS workload identity
                # in-cluster and Azure CLI credentials during local development.
                # AZURE_CLIENT_ID (when set) selects the user-assigned/workload
                # identity through DefaultAzureCredential.
                connection_string = (
                    "Driver={ODBC Driver 18 for SQL Server};"
                    f"Server=tcp:{server},1433;Database={database};"
                    "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
                )
        return cls(connection_string=connection_string, enabled=enabled)

    def _access_token_struct(self) -> Optional[bytes]:
        """Return a packed access token for ``SQL_COPT_SS_ACCESS_TOKEN`` or ``None``.

        Uses azure-identity's ``DefaultAzureCredential``, which resolves to the
        AKS workload identity in-cluster (via ``AZURE_CLIENT_ID`` and the
        projected federated token) and to the Azure CLI login during local
        development. Returns ``None`` when azure-identity is unavailable or a
        token cannot be acquired, in which case the connection string is used
        as-is.
        """
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-not-found]
        except ImportError:
            return None
        try:
            if self._credential is None:
                self._credential = DefaultAzureCredential()
            token = self._credential.get_token(_AZURE_SQL_TOKEN_SCOPE).token
            token_bytes = token.encode("utf-16-le")
            return struct.pack("<i", len(token_bytes)) + token_bytes
        except Exception as exc:  # noqa: BLE001 - credential/network failures are non-fatal
            logger.warning("Receiver reporting SQL token acquisition failed: %s", exc)
            return None

    def _execute(self, sql: str, *params: Any) -> bool:
        if not self.enabled:
            return False
        try:
            import pyodbc  # type: ignore[import-not-found]
            # pyodbc enables process-wide connection pooling by default, so each
            # short-lived call can reuse an ODBC connection when the driver supports it.
        except ImportError as exc:
            logger.warning("Receiver reporting SQL persistence disabled; pyodbc is unavailable: %s", exc)
            return False
        # Inject a Microsoft Entra access token unless the connection string
        # already specifies an Authentication method (e.g. a preset
        # AZURE_SQL_CONNECTION_STRING using ActiveDirectoryMsi).
        attrs_before: Dict[int, Any] = {}
        if "Authentication=" not in self.connection_string:
            token_struct = self._access_token_struct()
            if token_struct is not None:
                attrs_before[_SQL_COPT_SS_ACCESS_TOKEN] = token_struct
        try:
            with pyodbc.connect(self.connection_string, autocommit=True, timeout=5, attrs_before=attrs_before) as conn:
                conn.cursor().execute(sql, params)
            return True
        except pyodbc.Error as exc:
            logger.warning("Receiver reporting SQL persistence failed: %s", exc)
            return False

    def persist_submission(self, record: Dict[str, Any]) -> bool:
        measure_id = str(record.get("measureId") or "unknown")
        parameters = record.get("parameters") if isinstance(record.get("parameters"), dict) else {}
        submitter = _extract_submitter(parameters)
        program_id = os.getenv("RECEIVER_DEFAULT_PROGRAM_ID", "default-program")
        payload_type = parameters.get("resourceType") if isinstance(parameters, dict) else None
        received_at = _parse_datetime(record.get("receivedAtUtc"))

        ok = self._execute(
            """
MERGE dq.Measures AS target
USING (SELECT ? AS MeasureId) AS source
ON target.MeasureId = source.MeasureId
WHEN NOT MATCHED THEN INSERT (MeasureId, MeasureName) VALUES (source.MeasureId, source.MeasureId);
""",
            measure_id,
        )
        # Ensure the program dimension exists before the submitter references it
        # (dq.Submitters.ProgramId -> dq.Programs.ProgramId foreign key).
        self._execute(
            """
MERGE dq.Programs AS target
USING (SELECT ? AS ProgramId) AS source
ON target.ProgramId = source.ProgramId
WHEN NOT MATCHED THEN INSERT (ProgramId, ProgramName) VALUES (source.ProgramId, source.ProgramId);
""",
            program_id,
        )
        ok = self._execute(
            """
MERGE dq.Submitters AS target
USING (SELECT ? AS SubmitterId, ? AS SubmitterName, ? AS ProgramId) AS source
ON target.SubmitterId = source.SubmitterId
WHEN MATCHED THEN UPDATE SET SubmitterName = COALESCE(source.SubmitterName, target.SubmitterName), ProgramId = COALESCE(source.ProgramId, target.ProgramId)
WHEN NOT MATCHED THEN INSERT (SubmitterId, SubmitterName, ProgramId) VALUES (source.SubmitterId, source.SubmitterName, source.ProgramId);
""",
            submitter["id"],
            submitter.get("name"),
            program_id,
        ) or ok
        ok = self._execute(
            """
MERGE dq.SubmissionHistory AS target
USING (SELECT ? AS SubmissionId) AS source
ON target.SubmissionId = source.SubmissionId
WHEN MATCHED THEN UPDATE SET Status = ?, PayloadJson = ?
WHEN NOT MATCHED THEN INSERT (SubmissionId, MeasureId, SubmitterId, ProgramId, CohortId, ReceivedAtUtc, Status, PayloadType, PayloadJson)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
""",
            str(record.get("id")),
            "accepted",
            _json_dumps(parameters),
            str(record.get("id")),
            measure_id,
            submitter["id"],
            program_id,
            record.get("cohortId"),
            received_at,
            "accepted",
            payload_type,
            _json_dumps(parameters),
        ) or ok
        self.persist_processing_event(
            correlation_id=str(record.get("id")),
            event_type="submission.accepted",
            measure_id=measure_id,
            submitter_id=submitter["id"],
            program_id=program_id,
            status="succeeded",
        )
        return ok

    def persist_measure_report(self, record: Dict[str, Any]) -> bool:
        report = record.get("report") if isinstance(record.get("report"), dict) else record
        measure_id = _measure_id(report) or str(record.get("measureId") or "unknown")
        submitter = _extract_reporter(report)
        populations = _extract_populations(report)
        denominator = populations.get("denominator")
        numerator = populations.get("numerator")
        performance_rate = (
            round(numerator / denominator, 4)
            if numerator is not None and denominator is not None and denominator > 0
            else None
        )
        program_id = os.getenv("RECEIVER_DEFAULT_PROGRAM_ID", "default-program")
        period = report.get("period") if isinstance(report.get("period"), dict) else {}
        received_at = _parse_datetime(record.get("receivedAtUtc") or report.get("date"))
        report_id = str(record.get("id") or report.get("id"))

        self._execute(
            """
MERGE dq.Measures AS target
USING (SELECT ? AS MeasureId, ? AS MeasureName) AS source
ON target.MeasureId = source.MeasureId
WHEN MATCHED THEN UPDATE SET MeasureName = COALESCE(source.MeasureName, target.MeasureName)
WHEN NOT MATCHED THEN INSERT (MeasureId, MeasureName) VALUES (source.MeasureId, source.MeasureName);
""",
            measure_id,
            measure_id,
        )
        ok = self._execute(
            """
MERGE dq.MeasureReports AS target
USING (SELECT ? AS MeasureReportId) AS source
ON target.MeasureReportId = source.MeasureReportId
WHEN MATCHED THEN UPDATE SET Status = ?, Numerator = ?, Denominator = ?, Exclusions = ?, PerformanceRate = ?, PayloadJson = ?
WHEN NOT MATCHED THEN INSERT (MeasureReportId, SubmissionId, MeasureId, SubmitterId, ProgramId, SubjectId, ReportType, PeriodStart, PeriodEnd, Numerator, Denominator, Exclusions, PerformanceRate, Status, ReceivedAtUtc, PayloadJson)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
""",
            report_id,
            str(report.get("status") or "complete"),
            numerator,
            denominator,
            populations.get("exclusions"),
            performance_rate,
            _json_dumps(report),
            report_id,
            record.get("submissionId"),
            measure_id,
            submitter["id"],
            program_id,
            record.get("subjectId") or _reference_id(report.get("subject")),
            report.get("type"),
            _date_or_none(period.get("start")),
            _date_or_none(period.get("end")),
            numerator,
            denominator,
            populations.get("exclusions"),
            performance_rate,
            str(report.get("status") or "complete"),
            received_at,
            _json_dumps(report),
        )
        if performance_rate is not None:
            self._execute(
                """
INSERT INTO dq.QualityMetrics (MeasureId, ProgramId, SubmitterId, PeriodStart, PeriodEnd, MetricName, MetricValue)
VALUES (?, ?, ?, ?, ?, ?, ?);
""",
                measure_id,
                program_id,
                submitter["id"],
                _date_or_none(period.get("start")),
                _date_or_none(period.get("end")),
                "performanceRate",
                performance_rate,
            )
        return ok

    def persist_measurement_execution(self, patient_id: str, record: Dict[str, Any]) -> bool:
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        combined = summary.get("combined") if isinstance(summary.get("combined"), dict) else {}
        return self.persist_processing_event(
            correlation_id=patient_id,
            event_type="measure.evaluated",
            measure_id=",".join(str(m) for m in result.get("measureIds", []) if m) or result.get("measureId"),
            submitter_id=os.getenv("RECEIVER_DEFAULT_SUBMITTER_ID", "receiver-workbench"),
            program_id=os.getenv("RECEIVER_DEFAULT_PROGRAM_ID", "default-program"),
            status=str(result.get("status") or "completed"),
            latency_ms=_coerce_int(result.get("executionTimeMs")),
            error_message=_json_dumps(result.get("orchestratorErrors")) if result.get("orchestratorErrors") else None,
        ) and self._insert_quality_count_metrics(combined)

    def persist_processing_event(
        self,
        *,
        correlation_id: Optional[str],
        event_type: str,
        measure_id: Optional[str],
        submitter_id: Optional[str],
        program_id: Optional[str],
        status: str,
        latency_ms: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        return self._execute(
            """
INSERT INTO dq.ProcessingEvents (CorrelationId, EventType, MeasureId, SubmitterId, ProgramId, Status, LatencyMs, ErrorCode, ErrorMessage)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
""",
            correlation_id,
            event_type,
            measure_id,
            submitter_id,
            program_id,
            status,
            latency_ms,
            error_code,
            error_message,
        )

    def _insert_quality_count_metrics(self, combined: Dict[str, Any]) -> bool:
        if not combined:
            return True
        measure_id = "combined"
        program_id = os.getenv("RECEIVER_DEFAULT_PROGRAM_ID", "default-program")
        submitter_id = os.getenv("RECEIVER_DEFAULT_SUBMITTER_ID", "receiver-workbench")
        ok = True
        for metric in ("measuresEvaluated", "controlled", "inDenominator"):
            value = _coerce_int(combined.get(metric))
            if value is None:
                continue
            ok = self._execute(
                """
INSERT INTO dq.QualityMetrics (MeasureId, ProgramId, SubmitterId, MetricName, MetricValue)
VALUES (?, ?, ?, ?, ?);
""",
                measure_id,
                program_id,
                submitter_id,
                metric,
                value,
            ) and ok
        return ok


def _extract_submitter(parameters: Dict[str, Any]) -> Dict[str, Optional[str]]:
    for param in parameters.get("parameter") or []:
        if not isinstance(param, dict):
            continue
        ref = param.get("valueReference") if isinstance(param.get("valueReference"), dict) else None
        if ref:
            submitter_id = _reference_id(ref) or os.getenv("RECEIVER_DEFAULT_SUBMITTER_ID", "unknown-submitter")
            return {"id": submitter_id, "name": ref.get("display")}
        resource = param.get("resource") if isinstance(param.get("resource"), dict) else None
        if resource and resource.get("resourceType") == "Organization":
            return {"id": str(resource.get("id") or "unknown-submitter"), "name": resource.get("name")}
    return {"id": os.getenv("RECEIVER_DEFAULT_SUBMITTER_ID", "unknown-submitter"), "name": None}


def _extract_reporter(report: Dict[str, Any]) -> Dict[str, Optional[str]]:
    reporter = report.get("reporter") if isinstance(report.get("reporter"), dict) else {}
    return {
        "id": _reference_id(reporter) or os.getenv("RECEIVER_DEFAULT_SUBMITTER_ID", "unknown-submitter"),
        "name": reporter.get("display"),
    }


def _extract_populations(report: Dict[str, Any]) -> Dict[str, Optional[int]]:
    counts: Dict[str, Optional[int]] = {}
    groups = report.get("group") if isinstance(report.get("group"), list) else []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for population in group.get("population") or []:
            if not isinstance(population, dict):
                continue
            code = _population_code(population)
            key = _POPULATION_CODES.get(code)
            if key:
                counts[key] = _coerce_int(population.get("count"))
    return counts


def _population_code(population: Dict[str, Any]) -> str:
    coding = (((population.get("code") or {}).get("coding") or []) if isinstance(population.get("code"), dict) else [])
    if coding and isinstance(coding[0], dict):
        return str(coding[0].get("code") or "").lower()
    return ""


def _measure_id(report: Dict[str, Any]) -> Optional[str]:
    raw = str(report.get("measure") or "")
    if not raw:
        return None
    raw = raw.split("|")[0].rstrip("/")
    return raw.rsplit("/", 1)[-1]


def _reference_id(ref: Any) -> Optional[str]:
    if not isinstance(ref, dict):
        return None
    raw = str(ref.get("reference") or ref.get("identifier", {}).get("value") or "").strip()
    if not raw:
        return None
    return raw.rstrip("/").rsplit("/", 1)[-1]


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _date_or_none(value: Any) -> Optional[str]:
    if not value:
        return None
    return str(value)[:10]


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)
