# Azure Healthcare Digital Quality — Copilot Instructions

> **REPO REFACTOR — FOUR-STACK LAYOUT IN EFFECT.** The original single-stack
> tree (`backend/`, `frontend/`, `orchestrator/`, `infra/`,
> `docker-compose.yml` at the repo root) has been split into four
> independently deployable stacks and underscore-prefixed support
> directories. See [_docs/REFACTORING_PLAN.md](../_docs/REFACTORING_PLAN.md)
> for the long-form plan; the layout table below is the source of truth
> for path-specific guidance.
>
> | Concern                  | Location                                        |
> |--------------------------|-------------------------------------------------|
> | Submitters stack (active)| `submitters/{backend,frontend,orchestrator}/`   |
> | Receivers stack          | `receivers/{backend,frontend,orchestrator}/`    |
> | Platform stack (phase-0) | `platform/main.py`, `platform/docker-compose.yml`|
> | Providers stack (phase-0)| `providers/main.py`, `providers/docker-compose.yml`|
> | Bicep (per stack)        | `submitters/_infra/`, `receivers/_infra/`        |
> | azd manifest (per stack) | `submitters/azure.yaml`, `receivers/azure.yaml`  |
> | Compose (per stack)      | `<stack>/docker-compose.yml`                    |
> | Sample FHIR data         | `_data/`                                        |
> | Measure definitions      | `_measures/`                                    |
> | Docs                     | `_docs/`                                        |
> | Evals                    | `_evals/`                                       |
> | Scripts                  | `_scripts/`                                     |
> | Tests                    | `_tests/`                                       |
> | README assets            | `_images/`                                      |
>
> Where this file still says bare `<service>/...`, read it as
> `submitters/<service>/...`.

This monorepo implements an Azure-hosted **digital quality measurement** platform for CMS electronic Clinical Quality Measures (eCQM) and DEQM workflows. The Submitters stack is the active reference implementation and is composed of three services that run together on AKS in namespace `dq`:

- **`submitters/frontend/`** — Vite + React + TypeScript SPA served by nginx; authenticates users via Microsoft Entra ID and calls the backend over `/api/*` and `/fhir/*`.
- **`submitters/backend/`** — Python FastAPI service that owns patient data in Azure Cosmos DB and delegates measure computation to the orchestrator.
- **`submitters/orchestrator/`** — Python MCP service that executes CQL-based and AI-assisted digital quality measures (CMS122, CMS165, EPC02, ...) and persists agent state (plans, tasks, short-term memory) in Cosmos DB.

Always reference these instructions first. Only fall back to searching the codebase or running shell commands if you find information that contradicts them.

## Working Effectively

### Repository layout

```
azure-healthcare-digital-quality-platform/
├── _data/                       # CMS122/CMS165/EPC02 sample patients, seed catalog data
├── _docs/                       # AGENTS_*, DEFENDER_FOR_CLOUD, PURVIEW, REFACTORING_PLAN, ...
├── _evals/                      # Foundry evaluations + store_results.py
├── _images/                     # README assets (platform.svg, stack.svg, sa.png, ...)
├── _measures/                   # CQL + markdown measure definitions
├── _scripts/                    # build-and-push, fine-tuning, episode generation
├── _tests/                      # pytest test suite
├── providers/                   # Providers stack (phase-0 FastAPI stub)
│   ├── main.py
│   ├── requirements.txt
│   └── docker-compose.yml
├── submitters/                  # Submitters stack (active)
│   ├── azure.yaml               # azd service mapping for this stack
│   ├── docker-compose.yml       # local dev for the 3 submitters services
│   ├── _infra/                  # Bicep (main.bicep + modules)
│   ├── frontend/
│   │   ├── src/                 # React + TypeScript
│   │   ├── nginx/               # nginx.conf (proxies /api, /fhir to backend.dq)
│   │   ├── k8s/                 # Deployment + Ingress (host: dq-frontend.eastus2.cloudapp.azure.com)
│   │   ├── Dockerfile           # build context is submitters/frontend
│   │   └── package.json
│   ├── backend/
│   │   ├── src/main.py          # FastAPI entry point
│   │   ├── src/cosmosdb_helper.py
│   │   ├── k8s/                 # Deployment + Service (backend.dq.svc.cluster.local)
│   │   ├── Dockerfile           # build context is the REPO ROOT (COPY submitters/...)
│   │   └── requirements.txt
│   └── orchestrator/
│       ├── src/                 # MCP server, CQL executors, lightning RLHF
│       ├── k8s/                 # Deployment template (uses ${COSMOSDB_DATABASE_NAME})
│       ├── Dockerfile           # build context is the REPO ROOT (COPY submitters/... + measures/)
│       └── src/requirements.txt
├── receivers/                   # Receivers stack (mirrors submitters layout)
│   ├── azure.yaml
│   ├── docker-compose.yml
│   ├── _infra/
│   ├── backend/                 # Dockerfile still COPYs submitters/...; rewrite is pending
│   ├── frontend/
│   └── orchestrator/
└── platform/                    # Platform stack (phase-0 FastAPI stub)
    ├── main.py
    ├── requirements.txt
    └── docker-compose.yml
```

### Bootstrap

- Python (backend + orchestrator + evals):
  ```bash
  python -m venv .venv && source .venv/bin/activate   # PowerShell: .\.venv\Scripts\Activate.ps1
  pip install -r submitters/backend/requirements.txt
  pip install -r submitters/orchestrator/src/requirements.txt
  ```
  **TIMING**: 4–6 minutes total. Do not cancel. Set timeout ≥ 10 minutes.

- Frontend:
  ```bash
  cd submitters/frontend
  npm install
  ```
  **TIMING**: 1–2 minutes.

### Run services locally

| Service | Command | URL |
|---|---|---|
| Backend (dev) | `cd submitters/backend && fastapi dev ./src/main.py` | http://127.0.0.1:8000 |
| Backend (prod-like) | `cd submitters/backend && fastapi run ./src/main.py --port 5000` | http://0.0.0.0:5000 |
| Orchestrator | `cd submitters/orchestrator/src && uvicorn digital_quality_orchestrator:app --port 8001` | http://127.0.0.1:8001 |
| Frontend | `cd submitters/frontend && npm run dev` | http://127.0.0.1:5173 |
| Submitters all-in-one | `cd submitters && docker compose up --build` | per `submitters/docker-compose.yml` |
| Receivers all-in-one  | `cd receivers && docker compose up --build`  | per `receivers/docker-compose.yml`  |
| Platform stub         | `cd platform && docker compose up --build`   | http://127.0.0.1:8020 |
| Providers stub        | `cd providers && docker compose up --build`  | http://127.0.0.1:8030 |

Set `DEVELOPMENT_MODE=true` to bypass Entra ID auth on the backend and use an in-memory mock datastore when Cosmos DB is unavailable.

### Load sample data

```bash
echo "y" | python submitters/backend/src/load_patients.py _data/patients.json
```

Sample patient sets for each measure live under `_data/`:

- `_data/cms122_*.json` — CMS122 (Diabetes HbA1c poor control)
- `_data/cms165_*.json` — CMS165 (Controlling High Blood Pressure)
- `_data/epc02_*.json` — EPC02 (Delivery / eClinical episodes)

## Cluster & Cloud State

### AKS

- Cluster: `aks-pynargp3zuafw` (RG `rg-azure-healthcare-digital-quality-regulatory-agency`, region `eastus2`).
- Namespace: **`dq`** (all production workloads). Old namespaces `mcp-agents`, `dq-regulatory`, `azure-healthcare-digital-quality-regulatory-agency` have been retired.
- Workloads:
  - `deploy/frontend` — image `crpynargp3zuafw.azurecr.io/frontend:latest`
  - `deploy/backend`  — image `crpynargp3zuafw.azurecr.io/backend:latest`
  - `deploy/orchestrator` — image `crpynargp3zuafw.azurecr.io/orchestrator:latest`
- Service account: `mcp-agent-sa` (federated to managed identity `id-mcp-pynargp3zuafw` via `dq-federated`).
- Public URL: **`https://dq-frontend.eastus2.cloudapp.azure.com/`** (Public IP `4.153.143.213`, TLS issued by Let's Encrypt via cert-manager `letsencrypt-prod`).

### Azure Container Registry

- Registry: `crpynargp3zuafw.azurecr.io`
- Build images. The backend and orchestrator Dockerfiles `COPY submitters/<svc>/...`, `_data/`, and `_measures/`, so the build context must be the repo root. The frontend Dockerfile builds from its own folder:
  ```bash
  az acr build -r crpynargp3zuafw -t backend:latest      -f submitters/backend/Dockerfile .
  az acr build -r crpynargp3zuafw -t orchestrator:latest -f submitters/orchestrator/Dockerfile .
  az acr build -r crpynargp3zuafw -t frontend:latest     ./submitters/frontend
  ```
- After a successful build, restart the rollout:
  ```bash
  kubectl -n dq rollout restart deploy/backend deploy/orchestrator deploy/frontend
  ```

### Azure Cosmos DB

Account: `cosmos-pynargp3zuafw` (SQL API).

| Database | Containers | Partition key | Used by |
|---|---|---|---|
| `dq`    | `plans`, `tasks`, `chat`, `approvals` | `/id` (orchestrator state); `approvals` is the queue read by the Logic App approver | orchestrator + agents-approval Logic App |
| `dq`    | `catalog`  (docTypes: `measure`, `tag`, `agency`) | `/docType` | backend (workbench) |
| `dq`    | `cohorts`  (docTypes: `cohort`, `member`, `measurement_execution`, `measure_report`, `submission`) | `/docType` | backend (workbench) |
| `dq_rl` | `learning_episodes`, `learning_rewards`, `learning_metrics`, `learning_policies`, `learning_runs` | `/agent_id` | agent-learning-sdk |

Notes:
- The `clinical` database (with `patients`/`measure_submissions`/`measure_reports` containers) and the `mcpdb` database have been retired. FHIR member bundles now live in `dq/cohorts` (docType=`member`); DEQM submissions and measure reports also live in `dq/cohorts` (docTypes `submission` and `measure_report`).
- `regulatory-agencies` is **not** a separate container — it is a logical doc-type (`docType=agency`) inside `dq/catalog`.
- The `plans` and `tasks` containers are dual-purpose (quality-measure plans/tasks **and** generic agent plans/tasks). See `_docs/AGENTS_ARCHITECTURE.md` → *CosmosDB Container Schema* for the full document schemas, producer/consumer code paths, and rationale for the shared layout.

The orchestrator reads `COSMOSDB_DATABASE_NAME` (default `dq`) and `COSMOS_DATABASE_NAME` (default `dq_rl`). The backend reads `COSMOSDB_DATABASE` (default `dq`), `COSMOSDB_CATALOG_COLLECTION` (default `catalog`), and `COSMOSDB_COHORTS_COLLECTION` (default `cohorts`).

## Validation

### Manual smoke tests

```bash
# Backend health
curl -s http://127.0.0.1:8000/
# {"message": "Hello World"}

# Retrieve patient
curl -s http://127.0.0.1:8000/api/patient/P001

# Save patient
curl -s -X POST http://127.0.0.1:8000/api/patient \
  -H "Content-Type: application/json" \
  -d '{"mrn": "TEST001", "name": "Test Patient", "age": 45}'

# Compute a digital quality measure for a patient
curl -s -X POST http://127.0.0.1:8000/api/patient/P001/measure \
  -H "Content-Type: application/json" \
  -d '{"measure": "CMS122"}'

# Orchestrator MCP tool catalog
curl -s http://127.0.0.1:8001/tools
```

Swagger UI: <http://127.0.0.1:8000/docs>.

### Code validation

```bash
python -m py_compile submitters/backend/src/*.py submitters/orchestrator/src/*.py
pytest _tests/ -q
(cd submitters/frontend && npm run build)
```

## Authentication & Production Mode

- **Frontend**: Microsoft Entra ID via MSAL; access gated by Entra ID security groups configured through environment variables (no hardcoded group IDs).
- **Backend**: JWT bearer tokens validated against Entra ID on every `/api/*` and `/fhir/*` route; `DEVELOPMENT_MODE=true` disables this for local work.
- **Orchestrator**: Workload identity (federated credential `dq-federated`) — calls to Cosmos DB and Azure OpenAI use the managed identity assigned to the `mcp-agent-sa` service account.

### Required environment variables

Backend (`submitters/backend/.env` or k8s `backend-secret`):

```bash
COSMOSDB_HOST=cosmos-pynargp3zuafw.documents.azure.com
COSMOSDB_DATABASE=dq
COSMOSDB_CATALOG_COLLECTION=catalog
COSMOSDB_COHORTS_COLLECTION=cohorts
COSMOSDB_USERNAME=cosmos-pynargp3zuafw
COSMOSDB_PASSWORD=<key>
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT_NAME=
DIGITAL_QUALITY_ORCHESTRATOR_BASE_URL=http://orchestrator.dq.svc.cluster.local
DEVELOPMENT_MODE=false
REQUIRE_DATABASE=true
```

Orchestrator (`submitters/orchestrator/.env` or k8s envs):

```bash
COSMOSDB_DATABASE_NAME=dq
COSMOS_DATABASE_NAME=dq
COSMOSDB_ENDPOINT=https://cosmos-pynargp3zuafw.documents.azure.com:443/
AZURE_CLIENT_ID=<workload-identity-client-id>
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT_NAME=
```

## Deploy with azd

```bash
azd auth login
az login --use-device-code
azd up
```

**TIMING**: Full provision + deploy takes 15–25 minutes. Do not cancel. Set timeout ≥ 45 minutes.

## API Surface

- `GET  /`                            — backend health (no auth)
- `GET  /api/patient/{id}`            — retrieve patient
- `POST /api/patient`                 — save patient
- `POST /api/patient/{id}/measure`    — compute a digital quality measure (delegates to orchestrator)
- `POST /api/summarize`               — AI-powered patient summary
- `GET  /fhir/Patient/{id}`           — FHIR R4 read passthrough
- `POST /fhir/Measure/{id}/$submit-data`  — DEQM submit-data
- Orchestrator MCP:
  - `GET  /tools`                      — list MCP tools
  - `POST /tools/compute-quality-measures`
  - `POST /tools/plan` / `tasks` / `memory`

## Common Tasks

### Add a new API endpoint (backend)

1. Add the route to `submitters/backend/src/main.py`.
2. Add `current_user: Dict[str, Any] = Depends(get_current_user_conditional)` for authenticated routes.
3. Add tests under `_tests/`.
4. Rebuild + redeploy (build context is the repo root):
   ```bash
   az acr build -r crpynargp3zuafw -t backend:latest -f submitters/backend/Dockerfile .
   kubectl -n dq rollout restart deploy/backend
   ```

### Add a new MCP tool (orchestrator)

1. Register the tool in `submitters/orchestrator/src/tool_registry.py`.
2. Implement the handler in `submitters/orchestrator/src/tool_executor.py` (or a dedicated module).
3. If it persists state, add a Cosmos container in `submitters/_infra/main.bicep` and reference it from `submitters/orchestrator/src/config.py`.
4. Rebuild from the repo root (the Dockerfile `COPY`s `submitters/orchestrator/...` and `_measures/`):
   ```bash
   az acr build -r crpynargp3zuafw -t orchestrator:latest -f submitters/orchestrator/Dockerfile .
   kubectl -n dq rollout restart deploy/orchestrator
   ```

### Add a new digital quality measure

1. Add CQL or AI-driven measure logic under the repo-root `_measures/` folder (`<measureId>.cql` + `<measureId>.md` pair).
2. Add sample qualifying / non-qualifying patient files under `_data/<measureId>_*.json`.
3. Add a functional test in `_tests/test_digital_quality_orchestrator_functional.py`.
4. Document the measure in `_docs/CMS_Quality_Measure_Summary.md`.

### Modify infrastructure

1. Edit `submitters/_infra/main.bicep` (or a module in `submitters/_infra/app/` or `submitters/_infra/core/`).
2. Re-run `azd provision` from the stack directory (`cd submitters && azd provision`), or use `az deployment group create` for targeted changes.
3. If you changed Cosmos containers, run `submitters/_infra/hooks/postprovision.{ps1,sh}` so the k8s manifests pick up new env values.

### Debug authentication

1. Set `DEVELOPMENT_MODE=true` and restart the backend.
2. Decode tokens at <https://jwt.ms>.
3. Verify env vars in the running pod: `kubectl -n dq exec deploy/backend -- env | sort`.

## Conventions

- **Namespace**: every k8s manifest uses `namespace: dq`.
- **Image tags**: use `:latest` for AKS by default; pin a tag for production cuts.
- **Naming**: services are `frontend`, `backend`, `orchestrator` (no `mcp-agents` / `regulatory-*` legacy prefixes).
- **Secrets**: never commit `.env` files; the `.azure/` directory is azd-managed and ignored.
- **Tests**: prefer pytest under `_tests/` for backend/orchestrator and Playwright/Vitest under `submitters/frontend/` if added.
- **Documentation**: place architecture/runbook docs under `_docs/`. Per repository policy, do not create extra markdown files just to summarize changes unless explicitly requested.
