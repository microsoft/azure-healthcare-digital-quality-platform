# Generic Multi-Cloud Deployment

A single Terraform module + thin wrapper scripts that deploy any of the platform stacks
(`consumers`, `providers`, `submitters`, `receivers`, `platform`) to **Azure**, **AWS**,
**GCP**, an **existing Kubernetes cluster**, or **local Docker** — selected by one variable.

> This sits alongside the canonical `azd` + Bicep flow described in the top-level
> [README](../README.md#deployment). Use `azd up` when you want the full Azure-native
> stack (APIM, AI Foundry, Cosmos, Purview, workload identity, etc.). Use this module
> when you need a *portable* baseline (registry + Kubernetes + object storage +
> the three application services) on any hyperscaler.

## Layout

```text
deploy/
├── deploy.sh                  # bash wrapper
├── deploy.ps1                 # PowerShell wrapper
└── terraform/
    ├── main.tf                # dispatches on var.target_platform
    ├── variables.tf
    ├── providers.tf
    ├── versions.tf
    ├── outputs.tf
    ├── terraform.tfvars.example
    └── modules/
        ├── azure/             # AKS + ACR + Storage Account
        ├── aws/               # EKS + ECR + S3
        ├── gcp/               # GKE + Artifact Registry + GCS
        ├── kubernetes/        # bring-your-own kubeconfig
        ├── docker/            # local docker_container runtime
        └── k8s_workload/      # shared per-service Deployment/Service
```

## Prerequisites

| Target       | CLI tools required                           |
|--------------|----------------------------------------------|
| `azure`      | `az login`, Terraform >= 1.6                 |
| `aws`        | `aws configure`, Terraform >= 1.6            |
| `gcp`        | `gcloud auth application-default login`      |
| `kubernetes` | a working kubeconfig + reachable registry    |
| `docker`     | Docker Engine                                |

For image builds (`azure`/`aws`/`gcp`/`kubernetes`): Docker + already-authenticated
push credentials for the chosen registry. The wrapper assumes you have run
`az acr login`, `aws ecr get-login-password ...`, or `gcloud auth configure-docker` once.

## Quick start

```bash
cd deploy
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # optional
./deploy.sh azure submitters v1.0.0 --apply
```

PowerShell equivalent:

```powershell
cd deploy
./deploy.ps1 -Target azure -Stack submitters -Tag v1.0.0 -Action apply
```

The wrapper runs in three phases:

1. `terraform apply -target=module.<cloud>` — provisions registry, cluster, storage.
2. `docker build` + `docker push` for `backend`, `frontend`, `orchestrator` of the
   chosen stack (skip with `--no-build` / `-NoBuild`).
3. `terraform apply` — rolls out the Kubernetes Deployments / Services using the
   pushed image tags.

## Selecting a target

| `target_platform` | What gets provisioned                                                            |
|-------------------|----------------------------------------------------------------------------------|
| `azure`           | Resource Group, AKS cluster, ACR (with AcrPull for AKS), Storage Account + container |
| `aws`             | VPC + public subnets, EKS cluster + managed node group, one ECR repo per service, S3 bucket |
| `gcp`             | GKE cluster + default node pool, Artifact Registry Docker repo, GCS bucket       |
| `kubernetes`      | Nothing. Uses `var.kubernetes_byo.kubeconfig_path` + `var.kubernetes_byo.registry` |
| `docker`          | Local `docker_network` + `docker_image` + `docker_container` per service         |

## Inputs worth knowing

See `terraform/variables.tf`. The most common overrides:

```hcl
target_platform = "aws"
stack           = "receivers"
environment     = "prod"
region          = "us-west-2"
image_tag       = "2026.06.16-abcdef"

aws = {
  eks_version = "1.30"
  node_size   = "t3.xlarge"
  node_count  = 3
}
```

## Outputs

* `registry_url` — push destination for built images
* `kubeconfig_command` — copy/paste to fetch credentials locally
* `object_storage` — bucket / container provisioned for the stack
* `docker_endpoints` — populated only when `target_platform = docker`

## Limitations

This module intentionally provisions only the cross-cloud common substrate
(registry + Kubernetes + object storage + the three services). Azure-only
components in the reference architecture — API Management, Azure AI Foundry,
Cosmos DB, Purview, OAuth front door, Application Insights — are **not** ported
to AWS/GCP equivalents. Run those separately or stick to `azd up` if you need
the full Azure architecture.

## Tear down

```bash
./deploy.sh azure submitters v1.0.0 --destroy
```

```powershell
./deploy.ps1 -Target azure -Stack submitters -Action destroy
```
