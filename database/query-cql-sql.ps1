<#
.SYNOPSIS
  Compile a CQL definition to PostgreSQL SQL and show its live result.

.EXAMPLE
  ./database/query-cql-sql.ps1 -Stack receivers

.EXAMPLE
  ./database/query-cql-sql.ps1 -Stack submitters `
    -Measure CMS165v9_ControllingHighBloodPressure.cql `
    -Definition Numerator -PatientId patient-123
#>
[CmdletBinding()]
param(
    [ValidateSet('submitters', 'receivers')]
    [string] $Stack = 'receivers',

    [string] $Measure = 'CMS122v11_DiabetesHbA1cPoorControl.cql',

    [string] $Definition = 'Initial Population',

    [string] $PatientId = '',

    [string] $PeriodStart = '',

    [string] $PeriodEnd = '',

    [string] $Namespace = 'dq',

    [string] $Schema = 'public',

    [string] $SubscriptionId = '1c47c29b-10d8-4bc6-a024-05ec921662cb'
)

$ErrorActionPreference = 'Stop'

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

if (($PeriodStart -and -not $PeriodEnd) -or ($PeriodEnd -and -not $PeriodStart)) {
    throw 'Specify both -PeriodStart and -PeriodEnd, or omit both to use the CQL default.'
}

foreach ($command in @('az', 'kubectl')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' was not found."
    }
}

$target = $targets[$Stack]
az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to select the Azure subscription.'
}

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

$python = @'
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from cql_sdk.api import load_library_from_cql_text
from cql_sdk.postgres import PostgresCompiler
from cql_sdk.postgres.driver import connect


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, Decimal)):
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


parser = argparse.ArgumentParser()
parser.add_argument("--stack", required=True)
parser.add_argument("--measure", required=True)
parser.add_argument("--definition", required=True)
parser.add_argument("--patient-id", default="")
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
        preferred_patient = (
            "p-cms122-001"
            if "122" in measure_name
            else "p-epc02-001"
            if "epc" in measure_name
            else "p-cms165-001"
        )
        cursor.execute(
            f'SELECT resource_id FROM "{args.schema}"."fhir_resources" '
            "WHERE resource_type = 'Patient' AND resource_id = %s LIMIT 1",
            (preferred_patient,),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                f'SELECT resource_id FROM "{args.schema}"."fhir_resources" '
                "WHERE resource_type = 'Patient' ORDER BY updated_at DESC LIMIT 1"
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("No Patient resource is stored; pass -PatientId after loading a bundle.")
        patient_id = row[0]

    parameters: dict[str, Any] = {}
    if args.period_start and args.period_end:
        parameters["Measurement Period"] = (
            parse_datetime(args.period_start),
            parse_datetime(args.period_end),
        )

    library = load_library_from_cql_text(measure_path.read_text(encoding="utf-8"))
    compiled = PostgresCompiler(
        library,
        parameters=parameters,
        patient_id=patient_id,
        schema=args.schema,
    ).compile(args.definition)

    cursor.execute(compiled.sql, compiled.parameters)
    row = cursor.fetchone()
    result = None if row is None else row[0]

    cursor.execute(
        f'SELECT resource_type, count(*) FROM "{args.schema}"."fhir_resources" '
        "WHERE patient_id = %s GROUP BY resource_type ORDER BY resource_type",
        (patient_id,),
    )
    resource_counts = {resource_type: count for resource_type, count in cursor.fetchall()}

    report = {
        "stack": args.stack,
        "database": database,
        "database_user": database_user,
        "postgres_version": postgres_version,
        "sdk_version": __import__("cql_sdk").__version__,
        "measure": measure_path.name,
        "library": str(library.identifier),
        "definition": compiled.definition,
        "patient_id": patient_id,
        "patient_resource_counts": resource_counts,
        "sql": compiled.sql,
        "bind_parameters": json_value(compiled.parameters),
        "result": json_value(result),
    }
    print(json.dumps(report, indent=2, default=str))
finally:
    cursor.close()
    connection.close()
'@

$arguments = @(
    '-',
    '--stack', $Stack,
    '--measure', $Measure,
    '--definition', $Definition,
    '--schema', $Schema
)
if ($PatientId) {
    $arguments += @('--patient-id', $PatientId)
}
if ($PeriodStart -and $PeriodEnd) {
    $arguments += @('--period-start', $PeriodStart, '--period-end', $PeriodEnd)
}

Write-Host "Querying $Stack PostgreSQL through pod $pod..." -ForegroundColor Cyan
$python | kubectl exec --stdin --namespace $Namespace $pod -- python @arguments
if ($LASTEXITCODE -ne 0) {
    throw 'The CQL-to-SQL query failed.'
}