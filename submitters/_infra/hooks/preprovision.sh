#!/usr/bin/env sh
# Submitters preprovision: SKU-availability scan is intentionally skipped.
# Rely on the Bicep default for aksSystemNodePoolVmSize; let Azure surface a
# capacity error if it isn't available in the selected region.

set -eu

preferred_location="${AZURE_LOCATION:-eastus2}"
printf "Submitters preprovision: skipping AKS SKU availability scan.\n"
printf "  AZURE_LOCATION=%s\n" "$preferred_location"
azd env set AZURE_LOCATION "$preferred_location" >/dev/null
