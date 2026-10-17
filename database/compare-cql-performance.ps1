<#
.SYNOPSIS
  Compare in-memory CQL execution with PostgreSQL SQL in an orchestrator pod.

.EXAMPLE
  ./database/compare-cql-performance.ps1 -Stack receivers

.EXAMPLE
  ./database/compare-cql-performance.ps1 `
    -Stack submitters `
    -Measure CMS165v9_ControllingHighBloodPressure.cql `
    -Definition Numerator `
    -Iterations 250 `
    -PatientCounts 1000000,5000000,10000000 `
    -ParallelWorkers 1,10,100
#>
[CmdletBinding()]
param(
    [ValidateSet('submitters', 'receivers')]
    [string] $Stack = 'receivers',

    [string] $Measure = 'CMS122v11_DiabetesHbA1cPoorControl.cql',

    [string] $Definition = 'Initial Population',

    [string] $PatientId = '',

    [ValidateRange(1, 100000)]
    [int] $Iterations = 100,

    [ValidateRange(0, 10000)]
    [int] $WarmupIterations = 5,

    [long[]] $PatientCounts = @(1000000, 5000000, 10000000),

    [int[]] $ParallelWorkers = @(1, 10, 100),

    [string] $PeriodStart = '',

    [string] $PeriodEnd = '',

    [string] $Namespace = 'dq',

    [string] $Schema = 'public',

    [string] $OutputPath = '',

    [string] $SubscriptionId = '1c47c29b-10d8-4bc6-a024-05ec921662cb'
)

$ErrorActionPreference = 'Stop'

if (($PeriodStart -and -not $PeriodEnd) -or ($PeriodEnd -and -not $PeriodStart)) {
    throw 'Specify both -PeriodStart and -PeriodEnd, or omit both to use the CQL default.'
}
if ($PatientCounts | Where-Object { $_ -le 0 }) {
    throw '-PatientCounts values must be positive.'
}
if ($ParallelWorkers | Where-Object { $_ -le 0 }) {
    throw '-ParallelWorkers values must be positive.'
}

foreach ($command in @('az', 'kubectl')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' was not found."
    }
}

$targets = @{
    submitters = @{
        ResourceGroup = 'rg-dq-submitters'
        Cluster = 'aks-qfutdk4mapmlm'
    }
    receivers = @{
        ResourceGroup = 'rg-dq-receivers'
        Cluster = 'aks-5o6t35lpdsx4i'
    }
}

$python = @'
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import statistics
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import cql_sdk
from cql_sdk.api import load_library_from_cql_text
from cql_sdk.fhir.context import context_from_bundle
from cql_sdk.invocation.toolkit import InvocationToolkit
from cql_sdk.postgres import PostgresCompiler
from cql_sdk.postgres.driver import connect


def json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime, Decimal)):
        return str(value)
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, list):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value


def parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(sorted_values: list[float], fraction: float) -> float:
    index = max(0, math.ceil(len(sorted_values) * fraction) - 1)
    return sorted_values[index]


def benchmark(
    name: str,
    operation: Callable[[], Any],
    iterations: int,
) -> tuple[dict[str, Any], Any]:
    started_at = datetime.now(UTC)
    wall_start = time.perf_counter_ns()
    samples_ms: list[float] = []
    result: Any = None
    for _ in range(iterations):
        sample_start = time.perf_counter_ns()
        result = operation()
        samples_ms.append((time.perf_counter_ns() - sample_start) / 1_000_000)
    elapsed_ms = (time.perf_counter_ns() - wall_start) / 1_000_000
    ended_at = datetime.now(UTC)
    ordered = sorted(samples_ms)
    mean_ms = statistics.fmean(samples_ms)
    return (
        {
            "engine": name,
            "started_at_utc": started_at.isoformat(),
            "ended_at_utc": ended_at.isoformat(),
            "iterations": iterations,
            "elapsed_ms": elapsed_ms,
            "latency_ms": {
                "mean": mean_ms,
                "median": statistics.median(samples_ms),
                "p95": percentile(ordered, 0.95),
                "min": ordered[0],
                "max": ordered[-1],
            },
            "throughput_patients_per_second": iterations / (elapsed_ms / 1000),
        },
        result,
    )


def projected_duration(seconds: float) -> dict[str, float]:
    return {
        "seconds": seconds,
        "hours": seconds / 3600,
        "days": seconds / 86400,
    }


parser = argparse.ArgumentParser()
parser.add_argument("--stack", required=True)
parser.add_argument("--pod", required=True)
parser.add_argument("--measure", required=True)
parser.add_argument("--definition", required=True)
parser.add_argument("--patient-id", default="")
parser.add_argument("--iterations", type=int, required=True)
parser.add_argument("--warmup-iterations", type=int, required=True)
parser.add_argument("--patient-counts", nargs="+", type=int, required=True)
parser.add_argument("--parallel-workers", nargs="+", type=int, required=True)
parser.add_argument("--period-start", default="")
parser.add_argument("--period-end", default="")
parser.add_argument("--schema", default="public")
args = parser.parse_args()

if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.schema):
    raise ValueError(f"Invalid PostgreSQL schema identifier: {args.schema!r}")

measure_root = Path("/app/measures").resolve()
measure_path = (measure_root / args.measure).resolve()
if measure_root not in measure_path.parents or measure_path.suffix.lower() != ".cql":
    raise ValueError("The measure must be a .cql file under /app/measures.")
if not measure_path.is_file():
    raise FileNotFoundError(f"Measure not found in the orchestrator image: {measure_path.name}")

connection = connect(os.environ.get("DATABASE_URL"))
cursor = connection.cursor()
try:
    cursor.execute("SELECT current_database(), current_user, current_setting('server_version')")
    database, database_user, postgres_version = cursor.fetchone()

    patient_id = args.patient_id
    if not patient_id:
        measure_name = args.measure.lower()
        patient_id = (
            "p-cms122-001"
            if "122" in measure_name
            else "p-epc02-001"
            if "epc" in measure_name
            else "p-cms165-001"
        )

    cursor.execute(
        f'SELECT resource FROM "{args.schema}"."fhir_resources" '
        "WHERE patient_id = %s ORDER BY resource_type, resource_id",
        (patient_id,),
    )
    resources = []
    for (resource,) in cursor.fetchall():
        resources.append(json.loads(resource) if isinstance(resource, str) else resource)
    if not resources:
        raise RuntimeError(f"No FHIR resources are stored for patient {patient_id!r}.")
    if not any(resource.get("resourceType") == "Patient" for resource in resources):
        raise RuntimeError(f"Patient resource {patient_id!r} is not stored.")
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": resource} for resource in resources],
    }

    parameters: dict[str, Any] = {}
    if args.period_start and args.period_end:
        parameters["Measurement Period"] = (
            parse_datetime(args.period_start),
            parse_datetime(args.period_end),
        )

    overall_started_at = datetime.now(UTC)
    cql_text = measure_path.read_text(encoding="utf-8")
    shared_setup_start = time.perf_counter_ns()
    library = load_library_from_cql_text(cql_text)
    cql_parse_ms = (time.perf_counter_ns() - shared_setup_start) / 1_000_000

    value_sets_dir = Path(cql_sdk.__file__).resolve().parent / "data" / "valuesets"
    in_memory_setup_start = time.perf_counter_ns()
    context = context_from_bundle(
        bundle,
        value_sets_dir=value_sets_dir if value_sets_dir.is_dir() else None,
    )
    toolkit = InvocationToolkit()
    toolkit.register(library)
    in_memory_setup_ms = (time.perf_counter_ns() - in_memory_setup_start) / 1_000_000

    sql_setup_start = time.perf_counter_ns()
    compiled = PostgresCompiler(
        library,
        parameters=parameters,
        patient_id=patient_id,
        schema=args.schema,
    ).compile(args.definition)
    sql_compile_ms = (time.perf_counter_ns() - sql_setup_start) / 1_000_000

    def run_in_memory() -> Any:
        toolkit.clear_cache()
        return toolkit.invoke(
            library_identifier=library.identifier,
            definition=args.definition,
            parameters=parameters,
            context=context,
        )

    def run_postgres() -> Any:
        cursor.execute(compiled.sql, compiled.parameters)
        row = cursor.fetchone()
        return None if row is None else row[0]

    for _ in range(args.warmup_iterations):
        in_memory_warmup = run_in_memory()
        postgres_warmup = run_postgres()

    in_memory_stats, in_memory_result = benchmark(
        "in_memory_sdk",
        run_in_memory,
        args.iterations,
    )
    postgres_stats, postgres_result = benchmark(
        "postgresql_sql",
        run_postgres,
        args.iterations,
    )
    overall_ended_at = datetime.now(UTC)

    normalized_in_memory = json_value(in_memory_result)
    normalized_postgres = json_value(postgres_result)
    results_equal = normalized_in_memory == normalized_postgres
    truth_values_equal = bool(in_memory_result) == bool(postgres_result)

    projections = []
    for patient_count in args.patient_counts:
        for workers in args.parallel_workers:
            row: dict[str, Any] = {
                "patient_count": patient_count,
                "parallel_workers": workers,
            }
            for stats in (in_memory_stats, postgres_stats):
                seconds = (
                    stats["latency_ms"]["mean"] / 1000 * patient_count / workers
                )
                row[stats["engine"]] = projected_duration(seconds)
            projections.append(row)

    report = {
        "benchmark_started_at_utc": overall_started_at.isoformat(),
        "benchmark_ended_at_utc": overall_ended_at.isoformat(),
        "stack": args.stack,
        "pod": args.pod,
        "runtime": {
            "sdk_version": cql_sdk.__version__,
            "python_version": platform.python_version(),
            "cpu_count_visible_to_container": os.cpu_count(),
            "database": database,
            "database_user": database_user,
            "postgres_version": postgres_version,
        },
        "measurement": {
            "measure": measure_path.name,
            "library": str(library.identifier),
            "definition": args.definition,
            "patient_id": patient_id,
            "resource_counts": dict(
                sorted(
                    (resource_type, sum(r.get("resourceType") == resource_type for r in resources))
                    for resource_type in {r.get("resourceType") for r in resources}
                    if resource_type
                )
            ),
            "warmup_iterations_per_engine": args.warmup_iterations,
            "timed_iterations_per_engine": args.iterations,
        },
        "setup_ms_not_in_steady_state": {
            "cql_parse_and_load": cql_parse_ms,
            "in_memory_context_and_toolkit": in_memory_setup_ms,
            "postgres_sql_compile": sql_compile_ms,
        },
        "engines": {
            "in_memory_sdk": in_memory_stats,
            "postgresql_sql": postgres_stats,
        },
        "result_check": {
            "exactly_equal": results_equal,
            "truth_values_equal": truth_values_equal,
            "in_memory_result": normalized_in_memory,
            "postgresql_result": normalized_postgres,
        },
        "sql": {
            "characters": len(compiled.sql),
            "bind_parameter_count": len(compiled.parameters),
            "sha256": hashlib.sha256(compiled.sql.encode("utf-8")).hexdigest(),
        },
        "linear_projections": projections,
        "projection_caveat": (
            "Projections multiply observed single-patient mean latency and divide by "
            "idealized worker count. They exclude queueing, connection-pool limits, "
            "database contention, autoscaling, network saturation, and set-based batch "
            "optimization; load-test the target topology before capacity decisions."
        ),
    }
    print(json.dumps(report, indent=2, default=str))
finally:
    cursor.close()
    connection.close()
'@

$targets = @{
    submitters = @{
        ResourceGroup = 'rg-dq-submitters'
        Cluster = 'aks-qfutdk4mapmlm'
    }
    receivers = @{
        ResourceGroup = 'rg-dq-receivers'
        Cluster = 'aks-5o6t35lpdsx4i'
    }
}

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to select the Azure subscription.'
}

$target = $targets[$Stack]
az aks get-credentials `
    --resource-group $target.ResourceGroup `
    --name $target.Cluster `
    --admin `
    --overwrite-existing | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Unable to acquire credentials for $($target.Cluster)."
}

$pod = kubectl get pods `
    --namespace $Namespace `
    --selector app=orchestrator `
    --field-selector status.phase=Running `
    --output jsonpath='{.items[0].metadata.name}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($pod)) {
    throw "No running orchestrator pod was found in namespace '$Namespace'."
}

$pythonEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
$bootstrap = 'import base64,sys;payload=sys.argv.pop(1);exec(base64.b64decode(payload))'
$arguments = @(
    '-c', $bootstrap, $pythonEncoded,
    '--stack', $Stack,
    '--pod', $pod,
    '--measure', $Measure,
    '--definition', $Definition,
    '--iterations', [string]$Iterations,
    '--warmup-iterations', [string]$WarmupIterations,
    '--patient-counts'
)
$arguments += $PatientCounts | ForEach-Object { [string]$_ }
$arguments += '--parallel-workers'
$arguments += $ParallelWorkers | ForEach-Object { [string]$_ }
$arguments += @('--schema', $Schema)
if ($PatientId) {
    $arguments += @('--patient-id', $PatientId)
}
if ($PeriodStart -and $PeriodEnd) {
    $arguments += @('--period-start', $PeriodStart, '--period-end', $PeriodEnd)
}

Write-Host (
    "Benchmarking {0} / {1} for patient data in {2}..." -f `
        $Measure, $Definition, $Stack
) -ForegroundColor Cyan
$output = kubectl exec --namespace $Namespace $pod -- python @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'The CQL performance benchmark failed.'
}
$output | Write-Output

if ($OutputPath) {
    $resolvedOutput = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        $OutputPath
    )
    $parent = Split-Path -Parent $resolvedOutput
    if ($parent) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    [IO.File]::WriteAllText(
        $resolvedOutput,
        ($output -join [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    Write-Host "Wrote benchmark report to $resolvedOutput" -ForegroundColor Green
}