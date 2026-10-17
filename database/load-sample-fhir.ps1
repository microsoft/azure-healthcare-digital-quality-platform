<#
.SYNOPSIS
  Load the FHIR Bundles in _data/patients.json into PostgreSQL.

.EXAMPLE
  ./database/load-sample-fhir.ps1

.EXAMPLE
  ./database/load-sample-fhir.ps1 -Stack receivers
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'submitters', 'receivers')]
    [string] $Stack = 'all',

    [string] $InputPath = '',

    [string] $Namespace = 'dq',

    [string] $SubscriptionId = '1c47c29b-10d8-4bc6-a024-05ec921662cb'
)

$ErrorActionPreference = 'Stop'

if (-not $InputPath) {
    $InputPath = Join-Path $PSScriptRoot '..\_data\patients.json'
}
$InputPath = (Resolve-Path $InputPath).Path

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

import base64
import json
import os
import sys
from collections import Counter
from typing import Any

from cql_sdk.postgres import PostgresFHIRStore
from cql_sdk.postgres.driver import connect


payload = base64.b64decode(sys.stdin.read()).decode("utf-8-sig")
bundles: Any = json.loads(payload)
if not isinstance(bundles, list):
    raise ValueError("Expected patients.json to contain a list of FHIR Bundles.")

store = PostgresFHIRStore(database_url=os.environ.get("DATABASE_URL"))
store.initialize()

loaded_patients: list[str] = []
input_counts: Counter[str] = Counter()
for bundle in bundles:
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
        raise ValueError("Every patients.json item must be a FHIR Bundle.")
    entries = bundle.get("entry") or []
    patients = [
        item.get("resource")
        for item in entries
        if isinstance(item, dict)
        and isinstance(item.get("resource"), dict)
        and item["resource"].get("resourceType") == "Patient"
    ]
    if len(patients) != 1 or not isinstance(patients[0].get("id"), str):
        raise ValueError(f"Bundle {bundle.get('id')!r} must contain one identified Patient.")
    patient_id = patients[0]["id"]
    store.replace_patient_bundle(patient_id, bundle)
    loaded_patients.append(patient_id)
    input_counts.update(
        item["resource"]["resourceType"]
        for item in entries
        if isinstance(item, dict)
        and isinstance(item.get("resource"), dict)
        and isinstance(item["resource"].get("resourceType"), str)
    )

connection = connect(os.environ.get("DATABASE_URL"))
cursor = connection.cursor()
try:
    cursor.execute("SELECT current_database(), current_setting('server_version')")
    database, postgres_version = cursor.fetchone()
    cursor.execute(
        "SELECT resource_type, count(*) FROM fhir_resources "
        "GROUP BY resource_type ORDER BY resource_type"
    )
    stored_counts = {resource_type: count for resource_type, count in cursor.fetchall()}
    cursor.execute("SELECT count(DISTINCT patient_id) FROM fhir_resources")
    stored_patient_count = cursor.fetchone()[0]
finally:
    cursor.close()
    connection.close()

print(
    json.dumps(
        {
            "database": database,
            "postgres_version": postgres_version,
            "bundles_loaded": len(loaded_patients),
            "patient_ids": loaded_patients,
            "input_resource_counts": dict(sorted(input_counts.items())),
            "stored_patient_count": stored_patient_count,
            "stored_resource_counts": stored_counts,
        },
        indent=2,
    )
)
'@

az account set --subscription $SubscriptionId
if ($LASTEXITCODE -ne 0) {
    throw 'Unable to select the Azure subscription.'
}

$selectedStacks = if ($Stack -eq 'all') { @('submitters', 'receivers') } else { @($Stack) }
$payload = [Convert]::ToBase64String([IO.File]::ReadAllBytes($InputPath))
$pythonEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($python))
$bootstrap = 'import base64,sys;exec(base64.b64decode(sys.argv[1]))'

foreach ($stackName in $selectedStacks) {
    $target = $targets[$stackName]
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
        throw "No running $stackName orchestrator pod was found."
    }

    Write-Host "Loading $InputPath into $stackName PostgreSQL through pod $pod..." -ForegroundColor Cyan
    $payload | kubectl exec --stdin --namespace $Namespace $pod -- `
        python -c $bootstrap $pythonEncoded
    if ($LASTEXITCODE -ne 0) {
        throw "Loading sample FHIR data into $stackName failed."
    }
}