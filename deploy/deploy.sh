#!/usr/bin/env bash
# Generic deployment wrapper for the Azure Healthcare Digital Quality Platform.
# Runs the Terraform module under deploy/terraform with sensible defaults and
# (optionally) builds + pushes the container images to whichever registry the
# Terraform run produced.
#
# Usage:
#   ./deploy.sh <target> [stack] [tag] [--apply|--plan|--destroy] [--no-build]
#
# Examples:
#   ./deploy.sh azure submitters v1.0.0 --apply
#   ./deploy.sh aws   receivers  latest --plan
#   ./deploy.sh docker submitters local --apply --no-build
#
# Targets: azure | aws | gcp | kubernetes | docker

set -euo pipefail

TARGET="${1:-}"
STACK="${2:-submitters}"
TAG="${3:-latest}"
ACTION="apply"
BUILD=1

shift $(( $# > 3 ? 3 : $# )) || true
for arg in "$@"; do
  case "$arg" in
    --apply)    ACTION="apply" ;;
    --plan)     ACTION="plan" ;;
    --destroy)  ACTION="destroy" ;;
    --no-build) BUILD=0 ;;
    *) echo "Unknown flag: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z "$TARGET" ]]; then
  echo "Usage: $0 <azure|aws|gcp|kubernetes|docker> [stack] [tag] [--apply|--plan|--destroy] [--no-build]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/terraform"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$TF_DIR"
terraform init -upgrade -input=false

TF_ARGS=(
  -var "target_platform=$TARGET"
  -var "stack=$STACK"
  -var "image_tag=$TAG"
)

case "$ACTION" in
  plan)
    terraform plan -input=false "${TF_ARGS[@]}"
    exit 0
    ;;
  destroy)
    terraform destroy -input=false -auto-approve "${TF_ARGS[@]}"
    exit 0
    ;;
esac

# Phase 1: provision infra (cluster + registry + storage).
terraform apply -input=false -auto-approve "${TF_ARGS[@]}" -target=module.azure -target=module.aws -target=module.gcp -target=module.kubernetes -target=module.docker || true

REGISTRY="$(terraform output -raw registry_url 2>/dev/null || true)"

# Phase 2: build + push images (skipped for docker target — TF builds locally).
if [[ "$BUILD" == "1" && "$TARGET" != "docker" && -n "$REGISTRY" ]]; then
  echo "==> Building and pushing images to $REGISTRY"
  for svc in backend frontend orchestrator; do
    DOCKERFILE="$REPO_ROOT/$STACK/$svc/Dockerfile"
    [[ -f "$DOCKERFILE" ]] || { echo "skip $svc (no Dockerfile)"; continue; }
    IMAGE="$REGISTRY/$svc:$TAG"
    docker build -t "$IMAGE" -f "$DOCKERFILE" "$REPO_ROOT"
    docker push "$IMAGE"
  done
fi

# Phase 3: apply the full graph (workload manifests now that images exist).
terraform apply -input=false -auto-approve "${TF_ARGS[@]}"

echo
echo "==> Done. Useful outputs:"
terraform output
