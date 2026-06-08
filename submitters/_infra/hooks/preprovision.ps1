#!/usr/bin/env pwsh
# Submitters preprovision: SKU-availability scan is intentionally skipped.
# Rely on the Bicep default for aksSystemNodePoolVmSize; let Azure surface a
# capacity error if it isn't available in the selected region.

param()

$ErrorActionPreference = 'Stop'

$preferredLocation = if ($env:AZURE_LOCATION) { $env:AZURE_LOCATION } else { 'eastus2' }
Write-Host "Submitters preprovision: skipping AKS SKU availability scan."
Write-Host "  AZURE_LOCATION=$preferredLocation"
azd env set AZURE_LOCATION $preferredLocation | Out-Null
