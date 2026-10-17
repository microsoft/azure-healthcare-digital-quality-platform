<#
.SYNOPSIS
  Compare whole-cohort SQL with per-patient in-memory CQL execution in AKS.

.DESCRIPTION
  Creates an isolated, unlogged PostgreSQL benchmark schema by replicating one
  repository sample patient to the requested cohort sizes. SQL evaluates the
  entire cohort in one correlated statement and returns aggregate counts.
  In-memory execution transfers all cohort FHIR resources into the benchmark
  pod, groups them into patient Bundles, then invokes CQL once per patient.

.EXAMPLE
  ./database/compare-cql-cohort-performance.ps1 -Stack receivers

.EXAMPLE
  ./database/compare-cql-cohort-performance.ps1 `
    -CohortSizes 10000,100000 `
    -ProjectionPatientCounts 5000000,6000000 `
    -Iterations 3 `
    -OutputPath ./tmp/cms122-cohort-performance.json
#>
[CmdletBinding()]
param(
    [ValidateSet('submitters', 'receivers')]
    [string] $Stack = 'receivers',

    [string] $Measure = 'CMS122v11_DiabetesHbA1cPoorControl.cql',

    [string] $Definition = 'Initial Population',

    [string[]] $TemplatePatientIds = @(
        'p-cms122-x01',
        'p-cms122-001',
        'p-cms122-003',
        'p-cms122-002'
    ),

    [int[]] $CohortSizes = @(10000, 100000),

    [long[]] $ProjectionPatientCounts = @(5000000, 6000000),

    [ValidateRange(1, 20)]
    [int] $Iterations = 3,

    [ValidateRange(0, 10)]
    [int] $WarmupIterations = 1,

    [ValidateRange(1, 16)]
    [int] $CpuRequest = 2,

    [ValidateRange(1, 32)]
    [int] $MemoryLimitGi = 8,

    [string] $Namespace = 'dq',

    [string] $OutputPath = '',

    [switch] $KeepBenchmarkPod,

    [string] $SubscriptionId = '1c47c29b-10d8-4bc6-a024-05ec921662cb'
)

$ErrorActionPreference = 'Stop'

if ($CohortSizes | Where-Object { $_ -le 0 }) {
    throw '-CohortSizes values must be positive.'
}
if ($ProjectionPatientCounts | Where-Object { $_ -le 0 }) {
    throw '-ProjectionPatientCounts values must be positive.'
}
if (-not $TemplatePatientIds -or $TemplatePatientIds | Where-Object { -not $_ }) {
    throw '-TemplatePatientIds must contain at least one non-empty patient ID.'
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
import gc
import json
import math
import os
import platform
import statistics
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import cql_sdk
from cql_sdk.api import load_library_from_cql_text
from cql_sdk.fhir.context import context_from_bundle
from cql_sdk.fhir.terminology import StaticTerminologyProvider
from cql_sdk.invocation.toolkit import InvocationToolkit
from cql_sdk.postgres import PostgresCompiler
from cql_sdk.postgres.driver import connect


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def timed(operation: Callable[[], Any], iterations: int) -> tuple[dict[str, Any], Any]:
    samples: list[float] = []
    result: Any = None
    started_at = datetime.now(UTC)
    wall_start = time.perf_counter_ns()
    for _ in range(iterations):
        start = time.perf_counter_ns()
        result = operation()
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    elapsed_ms = (time.perf_counter_ns() - wall_start) / 1_000_000
    ended_at = datetime.now(UTC)
    mean_ms = statistics.fmean(samples)
    return (
        {
            'started_at_utc': started_at.isoformat(),
            'ended_at_utc': ended_at.isoformat(),
            'iterations': iterations,
            'elapsed_ms': elapsed_ms,
            'latency_ms': {
                'mean': mean_ms,
                'median': statistics.median(samples),
                'p95': percentile(samples, 0.95),
                'min': min(samples),
                'max': max(samples),
            },
        },
        result,
    )


def correlate_patient_id(sql: str, parameters: tuple[Any, ...], sentinel: str) -> tuple[str, list[Any]]:
    pieces = sql.split('%s')
    if len(pieces) != len(parameters) + 1:
        raise RuntimeError('Compiled SQL placeholder count does not match bind parameters.')
    output = [pieces[0]]
    remaining: list[Any] = []
    for parameter, suffix in zip(parameters, pieces[1:], strict=True):
        if parameter == sentinel:
            output.append('cohort.patient_id')
        else:
            output.append('%s')
            remaining.append(parameter)
        output.append(suffix)
    return ''.join(output), remaining


def duration_projection(patient_count: int, patients_per_second: float) -> dict[str, float]:
    seconds = patient_count / patients_per_second
    return {'seconds': seconds, 'hours': seconds / 3600, 'days': seconds / 86400}


parser = argparse.ArgumentParser()
parser.add_argument('--stack', required=True)
parser.add_argument('--pod', required=True)
parser.add_argument('--measure', required=True)
parser.add_argument('--definition', required=True)
parser.add_argument('--template-patient-ids', nargs='+', required=True)
parser.add_argument('--cohort-sizes', nargs='+', type=int, required=True)
parser.add_argument('--projection-patient-counts', nargs='+', type=int, required=True)
parser.add_argument('--iterations', type=int, required=True)
parser.add_argument('--warmup-iterations', type=int, required=True)
args = parser.parse_args()

measure_root = Path('/app/measures').resolve()
measure_path = (measure_root / args.measure).resolve()
if measure_root not in measure_path.parents or measure_path.suffix.lower() != '.cql':
    raise ValueError('The measure must be a .cql file under /app/measures.')
if not measure_path.is_file():
    raise FileNotFoundError(f'Measure not found: {measure_path.name}')

connection = connect(os.environ.get('DATABASE_URL'))
cursor = connection.cursor()
schema = f"cql_benchmark_{uuid.uuid4().hex[:10]}"
q_schema = quote_identifier(schema)
max_cohort = max(args.cohort_sizes)
overall_started_at = datetime.now(UTC)

try:
    cursor.execute("SELECT current_database(), current_user, current_setting('server_version')")
    database, database_user, postgres_version = cursor.fetchone()

    setup_started_at = datetime.now(UTC)
    setup_start = time.perf_counter_ns()
    cursor.execute(f'CREATE SCHEMA {q_schema}')
    cursor.execute(
        f'CREATE UNLOGGED TABLE {q_schema}.fhir_resources '
        '(LIKE public.fhir_resources INCLUDING DEFAULTS)'
    )
    cursor.execute(
        f'CREATE UNLOGGED TABLE {q_schema}.terminology_codes '
        '(LIKE public.terminology_codes INCLUDING DEFAULTS)'
    )
    cursor.execute(
        f'CREATE UNLOGGED TABLE {q_schema}.benchmark_templates '
        '(template_index integer PRIMARY KEY, template_patient_id text UNIQUE NOT NULL)'
    )
    template_values = ', '.join('(%s, %s)' for _ in args.template_patient_ids)
    template_parameters: list[Any] = []
    for index, patient_id in enumerate(args.template_patient_ids, start=1):
        template_parameters.extend((index, patient_id))
    cursor.execute(
        f'INSERT INTO {q_schema}.benchmark_templates '
        f'(template_index, template_patient_id) VALUES {template_values}',
        tuple(template_parameters),
    )
    cursor.execute(
        f'''
        SELECT templates.template_patient_id
        FROM {q_schema}.benchmark_templates AS templates
        LEFT JOIN public.fhir_resources AS patients
            ON patients.resource_type = 'Patient'
            AND patients.resource_id = templates.template_patient_id
        WHERE patients.resource_id IS NULL
        ORDER BY templates.template_index
        '''
    )
    missing_templates = [row[0] for row in cursor.fetchall()]
    if missing_templates:
        raise RuntimeError(f'Template patients not found: {missing_templates}')
    cursor.execute(
        f'INSERT INTO {q_schema}.terminology_codes SELECT * FROM public.terminology_codes'
    )
    cursor.execute(
        f'''
        INSERT INTO {q_schema}.fhir_resources
            (resource_type, resource_id, patient_id, resource, updated_at)
        SELECT
            source.resource_type,
            generated.resource_id,
            generated.patient_id,
            CASE
                WHEN source.resource ? 'subject' THEN
                    jsonb_set(
                        jsonb_set(source.resource, '{{id}}', to_jsonb(generated.resource_id)),
                        '{{subject,reference}}',
                        to_jsonb('Patient/' || generated.patient_id),
                        true
                    )
                WHEN source.resource ? 'beneficiary' THEN
                    jsonb_set(
                        jsonb_set(source.resource, '{{id}}', to_jsonb(generated.resource_id)),
                        '{{beneficiary,reference}}',
                        to_jsonb('Patient/' || generated.patient_id),
                        true
                    )
                ELSE
                    jsonb_set(source.resource, '{{id}}', to_jsonb(generated.resource_id))
            END,
            CURRENT_TIMESTAMP
        FROM generate_series(1, %s) AS series(ordinal)
        JOIN {q_schema}.benchmark_templates AS template
            ON template.template_index =
                ((series.ordinal - 1) %% %s) + 1
        JOIN public.fhir_resources AS source
            ON source.patient_id = template.template_patient_id
        CROSS JOIN LATERAL (
            SELECT
                'bench-' || lpad(series.ordinal::text, 6, '0') AS patient_id,
                CASE
                    WHEN source.resource_type = 'Patient' THEN
                        'bench-' || lpad(series.ordinal::text, 6, '0')
                    ELSE
                        source.resource_id || '-bench-' || lpad(series.ordinal::text, 6, '0')
                END AS resource_id
        ) AS generated
        ''',
        (max_cohort, len(args.template_patient_ids)),
    )
    cursor.execute(
        f'CREATE INDEX benchmark_patient_type_idx ON {q_schema}.fhir_resources '
        '(patient_id, resource_type)'
    )
    cursor.execute(
        f'CREATE INDEX benchmark_resource_idx ON {q_schema}.fhir_resources '
        '(resource_type, resource_id)'
    )
    cursor.execute(
        f'CREATE INDEX benchmark_terminology_idx ON {q_schema}.terminology_codes '
        '(value_set_url, code, system)'
    )
    cursor.execute(f'ANALYZE {q_schema}.fhir_resources')
    cursor.execute(f'ANALYZE {q_schema}.terminology_codes')
    connection.commit()
    setup_elapsed_ms = (time.perf_counter_ns() - setup_start) / 1_000_000
    setup_ended_at = datetime.now(UTC)

    library = load_library_from_cql_text(measure_path.read_text(encoding='utf-8'))
    sentinel = '__CQL_COHORT_PATIENT__'
    compiled = PostgresCompiler(
        library,
        patient_id=sentinel,
        schema=schema,
    ).compile(args.definition)
    correlated_sql, correlated_parameters = correlate_patient_id(
        compiled.sql,
        compiled.parameters,
        sentinel,
    )

    value_sets_dir = Path(cql_sdk.__file__).resolve().parent / 'data' / 'valuesets'
    terminology = StaticTerminologyProvider(value_sets_dir) if value_sets_dir.is_dir() else None
    toolkit = InvocationToolkit()
    toolkit.register(library)

    measurements: list[dict[str, Any]] = []
    for cohort_size in sorted(args.cohort_sizes):
        cohort_relation = (
            f'SELECT resource_id AS patient_id FROM {q_schema}.fhir_resources '
            'WHERE resource_type = %s ORDER BY resource_id LIMIT %s'
        )
        patient_results_sql = (
            'SELECT cohort.patient_id, evaluated.result '
            f'FROM ({cohort_relation}) AS cohort '
            f'CROSS JOIN LATERAL ({correlated_sql}) AS evaluated'
        )
        aggregate_sql = (
            'SELECT count(*), count(*) FILTER (WHERE result IS TRUE) '
            f'FROM ({patient_results_sql}) AS cohort_results'
        )
        aggregate_parameters = ['Patient', cohort_size, *correlated_parameters]

        def run_sql_cohort() -> dict[str, int]:
            cursor.execute(aggregate_sql, tuple(aggregate_parameters))
            total, matched = cursor.fetchone()
            return {'total': total, 'matched': matched}

        def run_in_memory_cohort() -> dict[str, int]:
            cursor.execute(
                f'''
                WITH cohort AS ({cohort_relation})
                SELECT resources.patient_id, resources.resource
                FROM {q_schema}.fhir_resources AS resources
                JOIN cohort ON cohort.patient_id = resources.patient_id
                ORDER BY resources.patient_id, resources.resource_type, resources.resource_id
                ''',
                ('Patient', cohort_size),
            )
            grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for patient_id, resource in cursor.fetchall():
                grouped[patient_id].append(
                    json.loads(resource) if isinstance(resource, str) else resource
                )
            matched = 0
            for patient_id, resources in grouped.items():
                bundle = {
                    'resourceType': 'Bundle',
                    'type': 'collection',
                    'entry': [{'resource': resource} for resource in resources],
                }
                context = context_from_bundle(bundle, terminology=terminology)
                toolkit.clear_cache()
                result = toolkit.invoke(
                    library_identifier=library.identifier,
                    definition=args.definition,
                    context=context,
                )
                matched += int(bool(result))
            gc.collect()
            return {'total': len(grouped), 'matched': matched}

        for _ in range(args.warmup_iterations):
            sql_warmup = run_sql_cohort()
            memory_warmup = run_in_memory_cohort()
            if sql_warmup != memory_warmup:
                raise RuntimeError(
                    f'Warmup result mismatch at {cohort_size}: '
                    f'SQL={sql_warmup}, in-memory={memory_warmup}'
                )

        sql_stats, sql_result = timed(run_sql_cohort, args.iterations)
        memory_stats, memory_result = timed(run_in_memory_cohort, args.iterations)
        if sql_result != memory_result:
            raise RuntimeError(
                f'Result mismatch at {cohort_size}: SQL={sql_result}, '
                f'in-memory={memory_result}'
            )

        for stats in (sql_stats, memory_stats):
            stats['patients_per_second'] = cohort_size / (
                stats['latency_ms']['mean'] / 1000
            )
        measurements.append(
            {
                'cohort_size': cohort_size,
                'results': sql_result,
                'postgresql_cohort_sql': sql_stats,
                'per_patient_in_memory': memory_stats,
                'speedup_sql_over_in_memory': (
                    memory_stats['latency_ms']['mean'] / sql_stats['latency_ms']['mean']
                ),
            }
        )

    largest = max(measurements, key=lambda item: item['cohort_size'])
    projections = []
    for patient_count in args.projection_patient_counts:
        projections.append(
            {
                'patient_count': patient_count,
                'basis_cohort_size': largest['cohort_size'],
                'postgresql_cohort_sql': duration_projection(
                    patient_count,
                    largest['postgresql_cohort_sql']['patients_per_second'],
                ),
                'per_patient_in_memory': duration_projection(
                    patient_count,
                    largest['per_patient_in_memory']['patients_per_second'],
                ),
            }
        )

    overall_ended_at = datetime.now(UTC)
    cursor.execute(f'SELECT count(*) FROM {q_schema}.fhir_resources')
    generated_resource_count = cursor.fetchone()[0]
    report = {
        'benchmark_started_at_utc': overall_started_at.isoformat(),
        'benchmark_ended_at_utc': overall_ended_at.isoformat(),
        'stack': args.stack,
        'pod': args.pod,
        'runtime': {
            'sdk_version': cql_sdk.__version__,
            'python_version': platform.python_version(),
            'cpu_count_visible_to_container': os.cpu_count(),
            'database': database,
            'database_user': database_user,
            'postgres_version': postgres_version,
        },
        'measurement': {
            'measure': measure_path.name,
            'library': str(library.identifier),
            'definition': args.definition,
            'template_patient_ids': args.template_patient_ids,
            'cohort_sizes': sorted(args.cohort_sizes),
            'iterations_per_engine_and_size': args.iterations,
            'warmup_iterations_per_engine_and_size': args.warmup_iterations,
        },
        'benchmark_data_setup': {
            'schema': schema,
            'started_at_utc': setup_started_at.isoformat(),
            'ended_at_utc': setup_ended_at.isoformat(),
            'elapsed_ms': setup_elapsed_ms,
            'generated_patient_count': max_cohort,
            'generated_resource_count': generated_resource_count,
            'included_in_engine_timings': False,
        },
        'methodology': {
            'postgresql_cohort_sql': (
                'One correlated SQL statement evaluates every patient in the cohort; '
                'only aggregate total/matched counts cross the network.'
            ),
            'per_patient_in_memory': (
                'Each timed iteration transfers all cohort FHIR JSONB rows from '
                'PostgreSQL into the AKS pod, groups patient Bundles, and invokes the '
                'in-memory SDK once per patient with caches cleared.'
            ),
            'result_validation': 'Total and matched counts must be identical.',
        },
        'measurements': measurements,
        'daily_volume_projections': projections,
        'projection_caveat': (
            'Projections use throughput measured at the largest cohort and assume '
            'linear scaling on this exact pod/database topology. They do not model '
            'concurrent tenants, autoscaling, database throttling, or partitioned '
            'parallel cohort execution.'
        ),
    }
    print(json.dumps(report, indent=2, default=json_value))
finally:
    try:
        cursor.execute(f'DROP SCHEMA IF EXISTS {q_schema} CASCADE')
        connection.commit()
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

$orchestratorImage = kubectl get deployment orchestrator `
    --namespace $Namespace `
    --output jsonpath='{.spec.template.spec.containers[0].image}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($orchestratorImage)) {
    throw 'Unable to determine the deployed orchestrator image.'
}

$podName = "cql-cohort-benchmark-$Stack"
kubectl delete pod $podName --namespace $Namespace --ignore-not-found --wait=true | Out-Null
$podManifest = @"
apiVersion: v1
kind: Pod
metadata:
  name: $podName
  namespace: $Namespace
  labels:
    app: cql-cohort-benchmark
spec:
  restartPolicy: Never
  containers:
    - name: benchmark
      image: $orchestratorImage
      imagePullPolicy: IfNotPresent
      command: ["sh", "-c", "sleep 7200"]
      envFrom:
        - secretRef:
            name: orchestrator-postgres
      resources:
        requests:
          cpu: "${CpuRequest}"
          memory: "2Gi"
        limits:
          cpu: "${CpuRequest}"
          memory: "${MemoryLimitGi}Gi"
"@

$podManifest | kubectl apply -f - | Out-Null
try {
    kubectl wait `
        --namespace $Namespace `
        --for=condition=Ready `
        "pod/$podName" `
        --timeout=10m
    if ($LASTEXITCODE -ne 0) {
        kubectl describe pod $podName --namespace $Namespace
        throw 'The cohort benchmark pod did not become ready.'
    }

    $pythonEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
    $bootstrap = 'import base64,sys;payload=sys.argv.pop(1);exec(base64.b64decode(payload))'
    $arguments = @(
        '-c', $bootstrap, $pythonEncoded,
        '--stack', $Stack,
        '--pod', $podName,
        '--measure', $Measure,
        '--definition', $Definition,
        '--template-patient-ids'
    )
    $arguments += $TemplatePatientIds
    $arguments += @(
        '--cohort-sizes'
    )
    $arguments += $CohortSizes | ForEach-Object { [string]$_ }
    $arguments += '--projection-patient-counts'
    $arguments += $ProjectionPatientCounts | ForEach-Object { [string]$_ }
    $arguments += @(
        '--iterations', [string]$Iterations,
        '--warmup-iterations', [string]$WarmupIterations
    )

    Write-Host (
        "Benchmarking cohort sizes {0} in $Stack AKS..." -f ($CohortSizes -join ', ')
    ) -ForegroundColor Cyan
    $output = kubectl exec --namespace $Namespace $podName -- python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw 'The CQL cohort performance benchmark failed.'
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
        Write-Host "Wrote cohort benchmark report to $resolvedOutput" -ForegroundColor Green
    }
}
finally {
    if (-not $KeepBenchmarkPod) {
        kubectl delete pod $podName --namespace $Namespace --ignore-not-found --wait=false | Out-Null
    }
}
