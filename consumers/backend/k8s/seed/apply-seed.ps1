#!/usr/bin/env pwsh
# Apply the Cosmos seed Job for the consumers stack.
#
# Idempotent: re-running re-applies the ConfigMaps and recreates the Job.
# Requires kubectl context already pointing at aks-qavfomo7lvk5e and
# AKS RBAC Cluster Admin (or equivalent) on the caller.
#
# Usage: pwsh ./apply-seed.ps1
[CmdletBinding()]
param(
    [string]$Namespace = 'seed',
    [string]$DataDir = (Resolve-Path "$PSScriptRoot/../../../../_data").Path
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

Write-Host "namespace : $Namespace"
Write-Host "data dir  : $DataDir"
Write-Host "script    : $here/seed_cosmos.py"
Write-Host "manifest  : $here/seed-job.yaml"

if (-not (Test-Path -LiteralPath $DataDir)) {
    throw "Data dir not found: $DataDir"
}
if (-not (Test-Path -LiteralPath "$here/seed_cosmos.py")) {
    throw "Seeder script not found"
}
if (-not (Test-Path -LiteralPath "$here/seed-job.yaml")) {
    throw "Job manifest not found"
}

kubectl get ns $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    kubectl create ns $Namespace | Out-Null
}

Write-Host '--- (re)creating ConfigMap cosmos-seed-script ---'
kubectl delete configmap cosmos-seed-script -n $Namespace --ignore-not-found | Out-Null
kubectl create configmap cosmos-seed-script -n $Namespace `
    --from-file=seed_cosmos.py=$here/seed_cosmos.py

Write-Host '--- (re)creating ConfigMap cosmos-seed-data ---'
$dataFiles = @(
    'cohorts.json',
    'measures.json',
    'measures-tags.json',
    'regulatory-agencies.json',
    'regulatory-agency-programs.json',
    'patients.json'
)
$fromFileArgs = @()
foreach ($f in $dataFiles) {
    $full = Join-Path $DataDir $f
    if (-not (Test-Path -LiteralPath $full)) {
        Write-Warning "missing data file (skipping): $f"
        continue
    }
    $fromFileArgs += "--from-file=$f=$full"
}
# kubectl apply is not viable: the last-applied-configuration annotation
# has a 262 KiB cap and patients.json alone exceeds it. Use delete+create.
kubectl delete configmap cosmos-seed-data -n $Namespace --ignore-not-found | Out-Null
kubectl create configmap cosmos-seed-data -n $Namespace @fromFileArgs

Write-Host '--- deleting prior Job (if any) ---'
kubectl delete job cosmos-seed -n $Namespace --ignore-not-found

Write-Host '--- applying Job ---'
kubectl apply -f "$here/seed-job.yaml"

Write-Host ''
Write-Host 'Watch with:'
Write-Host "  kubectl get pods -n $Namespace -w"
Write-Host "  kubectl logs -n $Namespace -l app=cosmos-seed -f"
