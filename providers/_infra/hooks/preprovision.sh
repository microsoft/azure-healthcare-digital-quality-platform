#!/usr/bin/env sh
# Providers preprovision: SKU-availability scan is intentionally skipped.
# The Bicep default for aksSystemNodePoolVmSize is Standard_D2s_v5; rely on
# that and let Azure surface a capacity error if it isn't available in the
# selected region.

set -eu

preferred_location="${AZURE_LOCATION:-eastus2}"
printf "Providers preprovision: skipping AKS SKU availability scan.\n"
printf "  AZURE_LOCATION=%s\n" "$preferred_location"
azd env set AZURE_LOCATION "$preferred_location" >/dev/null
