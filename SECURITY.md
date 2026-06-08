<!-- BEGIN MICROSOFT SECURITY.MD V1.0.0 BLOCK -->

## Security

Microsoft takes the security of our software products and services seriously,
including all source code repositories in our GitHub organizations.

**Please do not report security vulnerabilities through public GitHub issues.**

Report them to the Microsoft Security Response Center (MSRC) at
[https://msrc.microsoft.com/create-report](https://msrc.microsoft.com/create-report).
For complete guidance, see [https://aka.ms/SECURITY.md](https://aka.ms/SECURITY.md).

## Platform Security Overview

This repository implements an Azure-hosted **digital quality measurement** platform for CMS eCQM / DEQM workflows. The repo is organised as four independently deployable stacks (`providers/`, `submitters/`, `receivers/`, `platform/`); the Submitters stack is the active reference implementation, the Receivers stack mirrors it, and the Platform and Providers stacks ship as phase-0 FastAPI stubs. Each stack runs three workloads on AKS in namespace `dq`:

| Service | Stack | Trust boundary |
|---|---|---|
| `submitters/frontend/` | Vite + React + TypeScript behind nginx | Public ingress (TLS), Entra ID sign-in |
| `submitters/backend/`  | Python FastAPI | Cluster-internal, requires Entra ID JWT |
| `submitters/orchestrator/` | Python MCP server | Cluster-internal, workload identity |

The Receivers stack publishes the same surface from `receivers/{frontend,backend,orchestrator}/`. The Platform and Providers stacks expose only a single FastAPI process under `platform/main.py` and `providers/main.py` respectively until later phases ship their full backends.

Service-specific controls are documented in:

- [submitters/frontend/SECURITY.md](submitters/frontend/SECURITY.md) — Entra ID sign-in, group-based access control, MSAL session handling.
- [submitters/backend/SECURITY.md](submitters/backend/SECURITY.md) — JWT validation, TLS hardening, Cosmos DB access patterns.

### Identity and Access

- All user-facing access goes through **Microsoft Entra ID** with security-group gating; no hardcoded group IDs in source.
- Service-to-service calls inside the cluster use **Azure Workload Identity** (federated credential `dq-federated`, service account `mcp-agent-sa`, managed identity `id-mcp-pynargp3zuafw`).
- Backend and orchestrator do not accept anonymous traffic in production. `DEVELOPMENT_MODE=true` is for local development only.

### Network and Transport

- Public traffic terminates at the AKS ingress controller (nginx) with TLS certificates issued by Let's Encrypt via cert-manager (`letsencrypt-prod`).
- Public hostname: `dq-frontend.eastus2.cloudapp.azure.com`.
- Backend and orchestrator are exposed only as `ClusterIP` services (`backend.dq.svc.cluster.local`, `orchestrator.dq.svc.cluster.local`).
- All outbound HTTPS calls validate certificates against trusted CAs; TLS 1.2+ is required.

### Data Protection

- Patient records and DEQM artifacts live in Azure Cosmos DB (`cosmos-pynargp3zuafw`).
- Databases and containers:
  - `dq/catalog` (docTypes: `measure`, `tag`, `agency`) — backend workbench.
  - `dq/cohorts` (docTypes: `cohort`, `member`, `measurement_execution`, `measure_report`, `submission`) — backend workbench + DEQM.
  - `dq/{plans, chat, tasks, approvals}` — orchestrator state.
  - `dq_rl/{learning_episodes, learning_metrics, learning_policies, learning_rewards, learning_runs}` — agent-learning-sdk training data.
- Cosmos firewall, managed-identity authentication, and private endpoints are configured via `submitters/_infra/main.bicep`.
- The legacy `clinical` and `mcpdb` databases have been retired; their contents were consolidated into `dq/cohorts` via the `/docType` partition key.
- Secrets are stored in Azure Key Vault and surfaced to pods via `envFrom: secretRef` references; nothing sensitive lives in source.

### Supply Chain

- Container images are built and stored in Azure Container Registry (`crpynargp3zuafw.azurecr.io`) and pulled by AKS using managed-identity-backed ACR access.
- Workflow customizations and instructions live under `.github/` and `.speckit/`.
- Dependency hygiene is enforced through `requirements.txt` (Python services) and `package.json` (`submitters/frontend/`); Dependabot / GHAS alerts apply.

### Logging and Monitoring

- Application Insights is wired into backend and orchestrator for traces, exceptions, and request metrics.
- Microsoft Defender for Cloud and Microsoft Purview integrations are documented under `_docs/DEFENDER_FOR_CLOUD.md` and `_docs/PURVIEW_INTEGRATION.md`.

### Reporting

Security issues, suspected vulnerabilities, or concerns about the platform should be sent to MSRC via the link above. Do not open public issues for security topics.

<!-- END MICROSOFT SECURITY.MD BLOCK -->
