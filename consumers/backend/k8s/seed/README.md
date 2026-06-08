# Cosmos seed Job

One-off Kubernetes Job that seeds the consumers `cosmos-qavfomo7lvk5e`
account from the JSON files under `_data/` at the repo root.

## Why a Job in AKS

The Cosmos account has `disableLocalAuth=true` and
`publicNetworkAccess=Disabled`. Workstation tooling cannot reach it,
even with data-plane RBAC. The private endpoint
`cosmos-qavfomo7lvk5e-cosmos-private-endpoint` is in the AKS VNet, so
pods can. The Job uses workload identity federated to the existing
`id-mcp-qavfomo7lvk5e` UAMI, which already has
`Cosmos DB Built-in Data Contributor` on the account.

## What it writes

| Container | docType   | Source file                              |
| --------- | --------- | ---------------------------------------- |
| catalog   | measure   | `_data/measures.json`                    |
| catalog   | tag       | `_data/measures-tags.json`               |
| catalog   | agency    | `_data/regulatory-agencies.json` + `_data/regulatory-agency-programs.json` |
| cohorts   | cohort    | `_data/cohorts.json`                     |
| cohorts   | member    | `_data/patients.json`                    |

`upsert_item` is used everywhere, so re-running the Job is safe.

## Run it

```pwsh
az aks get-credentials -g rg-dq-consumers -n aks-qavfomo7lvk5e --overwrite-existing
pwsh ./apply-seed.ps1
kubectl logs -n seed -l app=cosmos-seed -f
```

## Files

- `seed_cosmos.py` — the seeder. Pure `azure-cosmos` + `azure-identity`.
- `seed-job.yaml` — Namespace, ServiceAccount, Job. Pulls
  `mcr.microsoft.com/azurelinux/base/python:3.12` and pip-installs
  `azure-cosmos` and `azure-identity` at startup.
- `apply-seed.ps1` — builds the two ConfigMaps and (re)applies the Job.

## Federated credential

Already created (one-time setup):

```pwsh
az identity federated-credential create `
  --name fc-seed-cosmos-seeder `
  --identity-name id-mcp-qavfomo7lvk5e `
  --resource-group rg-dq-consumers `
  --issuer https://eastus2.oic.prod-aks.azure.com/16b3c013-d300-468d-ac64-7eda0820b6d3/fb3ded1e-c79d-4d09-82a1-a1464a0a8c71/ `
  --subject system:serviceaccount:seed:cosmos-seeder `
  --audience api://AzureADTokenExchange
```
