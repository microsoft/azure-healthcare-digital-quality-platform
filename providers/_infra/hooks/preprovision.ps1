#!/usr/bin/env pwsh
# Providers preprovision: SKU-availability scan is intentionally skipped.
# The Bicep default for aksSystemNodePoolVmSize is Standard_D2s_v6; rely on
# that and let Azure surface a capacity error if it isn't available in the
# selected region.

param()

$ErrorActionPreference = 'Stop'

$preferredLocation = if ($env:AZURE_LOCATION) { $env:AZURE_LOCATION } else { 'eastus2' }
Write-Host "Providers preprovision: skipping AKS SKU availability scan."
Write-Host "  AZURE_LOCATION=$preferredLocation"
azd env set AZURE_LOCATION $preferredLocation | Out-Null
