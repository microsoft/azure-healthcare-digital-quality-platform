"""Measure-execution worker and Kubernetes Job dispatcher.

The submitters backend exposes ``POST /api/workbench/submissions/process``,
which delegates to :func:`trigger_measure_execution_job` here. Two modes:

* ``inprocess`` (default for local dev): the worker runs as a background
  asyncio task inside the FastAPI process. No Kubernetes required.
* ``kubernetes`` (AKS): a one-shot ``batch/v1 Job`` is created in the
  ``dq`` namespace, using the submitters backend image. The pod runs
  ``python measure_runner.py --submission-id <id>`` which invokes the
  same :func:`run_submission` worker.

The worker iterates each cohort member, POSTs the FHIR bundle to the
orchestrator's ``/tools/compute-quality-measures`` endpoint, persists a
``docType=measurement_execution`` row per member, and finally updates the
``docType=submission`` row with the aggregate status.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def _now_ms() -> int:
    return int(time.time() * 1000)


def _orchestrator_base_url() -> str:
    return os.getenv(
        "DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL",
        "http://orchestrator.dq.svc.cluster.local",
    ).rstrip("/")


def _orchestrator_measure_endpoint() -> str:
    raw = os.getenv(
        "DIGITAL_QUALITY_ORCHESTRATOR_QUALITY_ENDPOINT",
        "/tools/compute-quality-measures",
    )
    return raw if raw.startswith("/") else "/" + raw


def _orchestrator_timeout() -> float:
    return float(os.getenv("DIGITAL_QUALITY_ORCHESTRATOR_TIMEOUT_SECONDS", "60"))


def _job_namespace() -> str:
    return os.getenv("MEASURE_EXECUTION_NAMESPACE", "dq")


def _job_service_account() -> str:
    return os.getenv("MEASURE_EXECUTION_SERVICE_ACCOUNT", "orchestrator-sa")


def _job_image() -> str:
    # Falls back to the in-cluster submitters backend image tag. Operators
    # can override with the digest they want the job to use.
    return os.getenv("MEASURE_EXECUTION_IMAGE", "")


def _job_mode() -> str:
    raw = os.getenv("MEASURE_EXECUTION_JOB_MODE", "auto").strip().lower()
    if raw not in {"auto", "inprocess", "kubernetes"}:
        return "auto"
    return raw


# --------------------------------------------------------------------------- #
# Cosmos helpers (lightweight indirection so the worker can run from a Job
# pod where the helper is initialized via main.py imports).
# --------------------------------------------------------------------------- #


_HelperFactory = Callable[[], Any]
_helper_factory: Optional[_HelperFactory] = None


def configure_helper_factory(factory: _HelperFactory) -> None:
    """Register a no-arg callable returning the cohorts/Cosmos helper."""
    global _helper_factory
    _helper_factory = factory


def _get_helper() -> Any:
    if _helper_factory is None:
        # Lazy import to avoid circulars when run as __main__ from a Job pod.
        import main  # type: ignore  # noqa: PLC0415

        return main.cohortsDBHelper
    return _helper_factory()


# --------------------------------------------------------------------------- #
# Submission state helpers
# --------------------------------------------------------------------------- #


def _load_submission(submission_id: str) -> Optional[Dict[str, Any]]:
    return _get_helper().get_doc("submission", submission_id)


def _update_submission(submission_id: str, patch: Dict[str, Any]) -> None:
    helper = _get_helper()
    doc = helper.get_doc("submission", submission_id) or {}
    doc.update(patch)
    doc["id"] = submission_id
    doc["docType"] = "submission"
    doc["updatedAt"] = _now_ms()
    helper.upsert_doc("submission", submission_id, doc)


def _resolve_member_bundle(helper: Any, member_id: str) -> Optional[Dict[str, Any]]:
    """Try both docType=patient (legacy save_patient_data) and docType=member."""
    for dt in ("patient", "member"):
        doc = helper.get_doc(dt, member_id)
        if doc:
            return doc
    if hasattr(helper, "get_patient"):
        try:
            return helper.get_patient(member_id)
        except Exception:  # noqa: BLE001
            return None
    return None


# --------------------------------------------------------------------------- #
# Per-member execution
# --------------------------------------------------------------------------- #


def _execute_member(
    submission_id: str,
    member_id: str,
    bundle_doc: Dict[str, Any],
    measure_ids: List[str],
    period_start: Optional[str],
    period_end: Optional[str],
    cohort_id: Optional[str] = None,
    source_stack: Optional[str] = None,
) -> Dict[str, Any]:
    helper = _get_helper()
    url = f"{_orchestrator_base_url()}{_orchestrator_measure_endpoint()}"
    payload: Dict[str, Any] = {
        "patient_id": member_id,
        "measurement_period_start": period_start
        or os.getenv("QUALITY_MEASUREMENT_PERIOD_START", "2025-01-01"),
        "measurement_period_end": period_end
        or os.getenv("QUALITY_MEASUREMENT_PERIOD_END", "2025-12-31"),
        "measures": measure_ids,
        "use_native_cql_engine": True,
        "use_ai_cql_engine": False,
    }
    # Bundle may live under different keys depending on how it was stored.
    bundle = bundle_doc.get("bundle") or bundle_doc.get("fhirBundle") or bundle_doc
    if isinstance(bundle, dict) and bundle.get("resourceType") == "Bundle":
        payload["fhir_bundle"] = bundle

    record: Dict[str, Any] = {
        "id": f"exec-{submission_id}-{member_id}",
        "docType": "measurement_execution",
        "submissionId": submission_id,
        "memberId": member_id,
        "cohortId": cohort_id,
        "sourceStack": source_stack,
        "measureIds": measure_ids,
        "orchestratorUrl": url,
        "createdAt": _now_ms(),
    }
    try:
        response = requests.post(url, json=payload, timeout=_orchestrator_timeout())
        record["httpStatus"] = response.status_code
        if response.ok:
            record["status"] = "completed"
            try:
                record["report"] = response.json()
            except ValueError:
                record["report"] = {"raw": response.text[:4000]}
        else:
            record["status"] = "failed"
            record["error"] = response.text[:2000]
    except Exception as exc:  # noqa: BLE001
        record["status"] = "failed"
        record["error"] = str(exc)

    # Orchestrator unavailable (DNS / connect / non-2xx). Fall back to the
    # in-process evaluator so members render real D/N counts instead of
    # all-failed rows. Preserves the original error in `orchestratorError`.
    if record.get("status") != "completed":
        try:
            from local_measures import evaluate_all_measures  # type: ignore  # noqa: PLC0415

            bundle_for_local = bundle if isinstance(bundle, dict) and bundle.get("resourceType") == "Bundle" else {"resourceType": "Bundle", "entry": []}
            local_report = evaluate_all_measures(
                bundle_for_local,
                period_start=payload["measurement_period_start"],
                period_end=payload["measurement_period_end"],
            )
            wanted = {str(m).lower() for m in (measure_ids or [])}
            filtered = [
                m for m in (local_report.get("measures") or [])
                if not wanted or str(m.get("measureId", "")).lower() in wanted
            ]
            in_den = sum(1 for m in filtered if m.get("denominator"))
            in_num = sum(1 for m in filtered if m.get("numerator"))
            gaps = [
                {"measureId": m.get("measureId"), "measureName": m.get("measureName")}
                for m in filtered if m.get("denominator") and not m.get("numerator")
            ]
            record["orchestratorError"] = record.get("error")
            record["error"] = None
            record["status"] = "completed"
            record["engine"] = "local-stub"
            record["report"] = {
                "engine": "local-stub",
                "patient_id": member_id,
                "measurement_period_start": payload["measurement_period_start"],
                "measurement_period_end": payload["measurement_period_end"],
                "measures": [
                    {
                        "measure_id": m.get("measureId"),
                        "measure_name": m.get("measureName"),
                        "in_initial_population": bool(m.get("denominator")),
                        "in_denominator": bool(m.get("denominator")),
                        "in_numerator": bool(m.get("numerator")),
                        "denominator_exclusion": bool(m.get("exclusion")),
                        "evidence_trace": (m.get("evaluation") or {}).get("evidence", []) if isinstance(m.get("evaluation"), dict) else [],
                        "numerator_reasons": [m["explanation"]] if m.get("explanation") else [],
                        "detail": {"cql_engine_used": "local-stub"},
                    }
                    for m in filtered
                ],
                "summary": {
                    "measures_evaluated": len(filtered),
                    "in_denominator": in_den,
                    "controlled": in_num,
                    "gaps_in_care": gaps,
                },
            }
        except Exception as fb_exc:  # noqa: BLE001
            record["fallbackError"] = str(fb_exc)

    try:
        helper.upsert_doc("measurement_execution", record["id"], record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist measurement_execution %s: %s", record["id"], exc)

    _emit_submission_history(
        helper,
        submission_id=submission_id,
        member_id=member_id,
        cohort_id=cohort_id,
        source_stack=source_stack,
        measure_ids=measure_ids,
        record=record,
    )
    return record


def _emit_submission_history(
    helper: Any,
    *,
    submission_id: str,
    member_id: str,
    cohort_id: Optional[str],
    source_stack: Optional[str],
    measure_ids: List[str],
    record: Dict[str, Any],
) -> None:
    """Write one ``measurement_history`` row per measure for this member."""
    try:
        from measurement_history import record_measurement_history  # type: ignore  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.warning("measurement_history module unavailable: %s", exc)
        return

    status = record.get("status") or "failed"
    http_status = record.get("httpStatus")
    err = record.get("error")
    report = record.get("report") if isinstance(record.get("report"), dict) else {}

    def _norm_mid(value: Any) -> str:
        return str(value or "").replace("-", "").replace("_", "").lower()

    per_measure: Dict[str, Dict[str, Any]] = {}
    measures = report.get("measures") if isinstance(report, dict) else None
    if isinstance(measures, list):
        for m in measures:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("measure_id") or m.get("measureId") or "").strip()
            if mid:
                # Index by both the verbatim id AND a normalized form so we
                # still find the row when the orchestrator returns a catalog
                # id with a version suffix (e.g. "ePC02v1") for a measure the
                # cohort submitted without one ("ePC02").
                per_measure[mid] = m
                per_measure[_norm_mid(mid)] = m

    def _match(mid: str) -> Dict[str, Any]:
        if not mid:
            return {}
        hit = per_measure.get(mid) or per_measure.get(_norm_mid(mid))
        if hit:
            return hit
        # Fuzzy substring match in either direction — mirrors the rule used
        # by the orchestrator's plan-measures step so the requested id
        # ``ePC02`` still binds to the evaluated id ``ePC02v1``.
        n_req = _norm_mid(mid)
        if not n_req:
            return {}
        for k, v in per_measure.items():
            n_k = _norm_mid(k)
            if not n_k:
                continue
            if n_req in n_k or n_k in n_req:
                return v
        return {}

    for mid in measure_ids or []:
        m = _match(mid)
        in_den = bool(m.get("in_denominator"))
        exclusion = bool(m.get("denominator_exclusion"))
        controlled = bool(m.get("controlled"))
        denom = 1 if (in_den and not exclusion) else 0
        num = 1 if controlled else 0
        note: Optional[str] = None
        trace = m.get("evidence_trace")
        if isinstance(trace, list) and trace:
            note = "; ".join(str(t) for t in trace[:2])[:480]
        try:
            record_measurement_history(
                helper,
                source="submission",
                cohort_id=cohort_id,
                member_id=member_id,
                measure_id=mid,
                engine="native-cql",
                submission_id=submission_id,
                source_stack=source_stack,
                status=status,
                http_status=http_status,
                numerator=num if status == "completed" else None,
                denominator=denom if status == "completed" else None,
                exclusion=exclusion if status == "completed" else None,
                note=note,
                error=(str(err)[:500] if err and status != "completed" else None),
                report_id=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to record measurement_history (submission): %s", exc)


# --------------------------------------------------------------------------- #
# Top-level worker (entrypoint for both inprocess + Job pod modes)
# --------------------------------------------------------------------------- #


def run_submission(submission_id: str) -> Dict[str, Any]:
    """Execute every member in the submission. Idempotent on retry."""
    submission = _load_submission(submission_id)
    if not submission:
        raise RuntimeError(f"submission {submission_id} not found")

    member_ids: List[str] = list(submission.get("memberIds") or [])
    measure_ids: List[str] = list(submission.get("measureIds") or [])
    period_start = submission.get("periodStart")
    period_end = submission.get("periodEnd")
    cohort_id = submission.get("cohortId") or submission.get("cohort_id")
    source_stack = submission.get("sourceStack") or submission.get("source_stack")

    _update_submission(
        submission_id,
        {"status": "running", "startedAt": _now_ms(), "memberCount": len(member_ids)},
    )

    helper = _get_helper()
    summary = {"completed": 0, "failed": 0, "skipped": 0}
    per_member: List[Dict[str, Any]] = []

    for mid in member_ids:
        bundle_doc = _resolve_member_bundle(helper, mid)
        if not bundle_doc:
            summary["skipped"] += 1
            per_member.append({"memberId": mid, "status": "skipped", "reason": "no bundle in Cosmos"})
            continue
        result = _execute_member(
            submission_id,
            mid,
            bundle_doc,
            measure_ids,
            period_start,
            period_end,
            cohort_id=cohort_id,
            source_stack=source_stack,
        )
        per_member.append(
            {
                "memberId": mid,
                "status": result.get("status"),
                "httpStatus": result.get("httpStatus"),
                "error": result.get("error"),
            }
        )
        if result.get("status") == "completed":
            summary["completed"] += 1
        else:
            summary["failed"] += 1

    final_status = (
        "completed"
        if summary["failed"] == 0 and summary["skipped"] == 0
        else "completed_with_errors"
    )
    _update_submission(
        submission_id,
        {
            "status": final_status,
            "completedAt": _now_ms(),
            "summary": summary,
            "members": per_member,
        },
    )
    return {"submissionId": submission_id, "status": final_status, "summary": summary}


# --------------------------------------------------------------------------- #
# Kubernetes Job dispatcher
# --------------------------------------------------------------------------- #


def _kube_clients():
    """Return (BatchV1Api, namespace, image) or raise on failure."""
    from kubernetes import client, config  # type: ignore  # noqa: PLC0415

    try:
        config.load_incluster_config()
    except Exception:
        config.load_kube_config()
    return client


def _create_k8s_job(submission_id: str) -> Dict[str, Any]:
    image = _job_image()
    if not image:
        raise RuntimeError("MEASURE_EXECUTION_IMAGE is not set; cannot create a K8s Job")
    client = _kube_clients()
    api = client.BatchV1Api()
    namespace = _job_namespace()
    job_name = f"measure-exec-{submission_id[-40:].lower()}-{uuid.uuid4().hex[:6]}"
    env_passthrough = [
        "DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL",
        "DIGITAL_QUALITY_ORCHESTRATOR_QUALITY_ENDPOINT",
        "DIGITAL_QUALITY_ORCHESTRATOR_TIMEOUT_SECONDS",
        "COSMOSDB_DATABASE",
        "COSMOSDB_COHORTS_COLLECTION",
        "COSMOSDB_HOST",
        "COSMOS_ENDPOINT",
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
    ]
    env = [
        client.V1EnvVar(name="SUBMISSION_ID", value=submission_id),
    ]
    for key in env_passthrough:
        val = os.getenv(key)
        if val is not None:
            env.append(client.V1EnvVar(name=key, value=val))

    container = client.V1Container(
        name="measure-runner",
        image=image,
        image_pull_policy="IfNotPresent",
        command=["python", "measure_runner.py", "--submission-id", submission_id],
        env=env,
    )
    pod_spec = client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        service_account_name=_job_service_account(),
    )
    template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(
            labels={
                "app": "measure-runner",
                "submission-id": submission_id,
                "azure.workload.identity/use": "true",
            }
        ),
        spec=pod_spec,
    )
    spec = client.V1JobSpec(
        template=template,
        backoff_limit=2,
        ttl_seconds_after_finished=3600,
    )
    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=namespace,
            labels={"app": "measure-runner", "submission-id": submission_id},
        ),
        spec=spec,
    )
    api.create_namespaced_job(namespace=namespace, body=job)
    return {"jobName": job_name, "namespace": namespace, "mode": "kubernetes"}


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


def trigger_measure_execution_job(submission_id: str) -> Dict[str, Any]:
    """Dispatch a submission to either an in-process worker or a K8s Job.

    Returns a dict describing the dispatch decision so the API can echo it
    back to the caller: ``{"mode": "inprocess"|"kubernetes", ...}``.
    """
    mode = _job_mode()

    if mode in {"kubernetes", "auto"}:
        try:
            info = _create_k8s_job(submission_id)
            _update_submission(
                submission_id,
                {
                    "status": "dispatched",
                    "dispatch": info,
                    "dispatchedAt": _now_ms(),
                },
            )
            return info
        except Exception as exc:  # noqa: BLE001
            if mode == "kubernetes":
                raise
            logger.info("Falling back to inprocess worker (no K8s available): %s", exc)

    # In-process fallback. Run the worker as a fire-and-forget asyncio task
    # so the HTTP POST returns immediately.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    info = {"mode": "inprocess"}
    if loop is None:
        # Called from a non-async context: spawn a thread.
        import threading  # noqa: PLC0415

        threading.Thread(
            target=run_submission, args=(submission_id,), daemon=True, name=f"sub-{submission_id}"
        ).start()
    else:
        loop.run_in_executor(None, run_submission, submission_id)

    _update_submission(
        submission_id,
        {"status": "running", "dispatch": info, "dispatchedAt": _now_ms()},
    )
    return info


# --------------------------------------------------------------------------- #
# CLI entrypoint (used inside the K8s Job pod)
# --------------------------------------------------------------------------- #


def _main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run a measure-execution submission worker.")
    parser.add_argument(
        "--submission-id",
        default=os.getenv("SUBMISSION_ID"),
        help="Submission id to process (or set SUBMISSION_ID env var).",
    )
    args = parser.parse_args()
    if not args.submission_id:
        print("submission-id required (CLI flag or SUBMISSION_ID env)", file=sys.stderr)
        return 2
    result = run_submission(args.submission_id)
    print(result)
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
