# Constitution — Azure Healthcare Digital Quality

> **Purpose**
> This constitution defines non‑negotiable principles for the **Azure Healthcare Digital Quality** platform

## 1) Mission
Enable **digital quality measurement (dQM)** solutions that are:
- **Policy‑aligned** (authoritative definitions remain governed by the policy owner)
- **Clinically and operationally useful** (actionable, explainable, timely)
- **Technically reproducible** (deterministic, performant, testable, observable)
- **Secure and compliant by default**

## 2) The “Policy is the Source of Truth” Principle
1. **Policy authority is external to the accelerator system logic.** The accelerator never invents, alters, or “improvises” measure definitions.
2. Measure logic must be treated as **versioned, signed artifacts** (e.g., specs, CQL packages, value sets, scoring rules) and referenced by immutable infrastructure.
3. Any non‑policy augmentation (e.g., AI‑assisted authoring, mapping hints) must be clearly labeled as **non‑authoritative**.

## 3) Determinism, Auditability, and Evidence
1. Every reported result must be **explainable**:
   - Which measure version ran
   - Which inputs were used
   - Which intermediate computations occurred
   - Which evidence items satisfied each clause
2. Any agent action that affects results must be logged with:
   - **who/what** performed the action (human or agent identity)
   - **when** it occurred
   - **why** (linked to a spec requirement)
   - **what changed** (diff/trace)
3. Prefer **deterministic execution paths** for scoring (e.g., CQL execution, deterministic Python computation) and use AI only for:
   - planning out measurement tasks
   - summarization that do **not** change computed results

## 4) Security & Privacy First
1. **Least privilege** for all identities (human and agent). Use managed identities / workload identity when possible.
2. **Data minimization**: only ingest the minimum necessary to compute measures and generate required evidence.
3. **No PHI exfiltration**:
   - No copying PHI into prompts, logs, telemetry, or issue trackers.
   - Redact or surrogate identifiers for debugging and demos.
4. **Environment isolation**:
   - separate dev/test/prod resources
   - explicit data boundaries and approvals to move artifacts between environments

## 5) API‑First, Contract‑First, Spec‑Driven
1. Changes start with **spec updates** and tests—then code.
2. Treat agent behaviors as APIs: stable inputs/outputs, versioned contracts, backward compatibility.
3. Prefer modular “capabilities” (tools) over monoliths so each repo can tailor agents/components to its domain.

## 6) Observability as a Feature
1. Every run produces:
   - trace ID
   - structured logs
   - metrics for throughput/latency/error rates
   - evaluation outputs (quality + correctness)
2. “If it isn’t observable, it isn’t shippable.”

## 7) Quality and Safety Gates
1. No merge without:
   - unit tests
   - reproducible measure test cases
   - evaluation baselines and drift detection where applicable
2. Treat regressions in measure results as **P0 defects**.

## 8) Interoperability by Default
1. Prefer healthcare standards for data exchange (FHIR resources for clinical evidence).
2. Keep mappings explicit and reviewable; avoid opaque transformations.

## 9) What the accelerator Will Not Do
1. The accelerator will not fabricate measure logic or policy intent.
2. The accelerator will not compute “official” scores without policy-owner alignment.
3. The accelerator will not trade security, privacy, or auditability for speed.

---

## 10) Azure Agents Control Plane — Well‑Architected Best Practices

> Ensures all Azure Healthcare Digital Quality accelerators share a common enterprise governance model.

### 10.1 Azure as Enterprise Control Plane

- **Centralized Governance**: All agent operations flow through Azure's control plane (APIM + MCP + AKS)
- **Multi-Cloud Capable**: Agents may execute on Azure, GCP, AWS, or hybrid environments
- **Policy Enforcement**: Consistent policies applied at the gateway layer before any model/agent execution
- **Single Pane of Glass**: Azure Monitor, App Insights, and Sentinel provide unified observability

### 10.2 API‑First Agent Architecture

- **APIM Gateway**: All agent tool calls route through Azure API Management
- **MCP Protocol**: Model Context Protocol standardizes tool interfaces across agents
- **OpenAPI Compliance**: All tools and services expose OpenAPI specifications
- **Rate Limiting & Quotas**: APIM policies govern resource consumption per agent/user/tenant

### 10.3 Identity‑First Security

- **Agent Identities**: Every agent receives a Microsoft Entra ID identity (Agent ID)
- **Workload Identity**: AKS pods authenticate via federated workload identity
- **Keyless Authentication**: Prefer managed identities; vault secrets only when unavoidable
- **Least Privilege**: RBAC scopes permissions to specific tools, actions, and data

### 10.4 Specification‑Driven Development

- **SpecKit Methodology**: All agents defined via structured specifications before implementation
- **GitHub Copilot Integration**: Copilot accelerates agent development from specifications
- **Test-First Approach**: Acceptance criteria are testable and automated
- **Schema Evolution**: Specifications drive code, not vice versa

### 10.5 Continuous Evaluation & Improvement

- **Agent Evaluations**: Built-in evaluation framework measures task adherence, safety, and quality
- **Fine-Tuning Pipeline**: Agent Lightning captures episodes for reinforcement learning
- **Behavioral Optimization**: Continuous feedback loops improve agent performance
- **Human-in-the-Loop**: Manual review enables oversight for critical decisions

### 10.6 Development Standards

#### Analysis Phase
- Document business problem and target outcomes
- Identify multi-agent decomposition opportunities
- Map data sources, inputs, and outputs
- Define success KPIs and measurement approach
- Assess compliance and requirements

#### Design Phase
- Create agent architecture diagrams (Mermaid format)
- Define tool catalog with MCP schemas
- Specify memory architecture (short-term, long-term, facts)
- Design identity and access patterns
- Plan observability instrumentation

#### Development Phase
- Type hints for all functions (Python 3.10+)
- Comprehensive docstrings (Google style)
- Unit tests with >80% coverage for core logic
- Integration tests for end-to-end workflows
- UV as exclusive dependency manager
- FastAPI for MCP server endpoints
- Azure SDK for service integration
- OpenTelemetry for distributed tracing
- Pydantic for request/response validation

#### Testing Phase
- **Unit Tests**: Isolated agent logic and tool implementations
- **Integration Tests**: End-to-end MCP protocol flows
- **Infrastructure Tests**: AKS connectivity, APIM routing, identity
- **Functional Tests**: Use case validation via Copilot-driven testing
- **Security Tests**: Authentication, authorization, input validation

#### Fine‑Tuning Phase
- **Episode Capture**: Enable Lightning capture for training data collection
- **Reward Labeling**: Human or automated scoring of agent behaviors
- **Dataset Building**: Create training datasets from labeled episodes
- **Model Training**: Execute fine-tuning jobs via Azure AI Foundry
- **Promotion**: Deploy tuned models to production after validation

#### Evaluation Phase
- **Task Adherence**: Measure agent compliance with specified workflows
- **Safety Scoring**: Evaluate content safety and policy compliance
- **Quality Metrics**: Accuracy, latency, token efficiency
- **Business KPIs**: Domain-specific success metrics
- **Regression Testing**: Ensure new versions don't degrade performance

### 10.7 Technical Architecture

#### Data Flow
1. **Request Ingestion**: AI client → APIM → OAuth validation
2. **Protocol Handling**: APIM → AKS → MCP Server (SSE/JSON-RPC)
3. **Tool Execution**: Agent → Tool → External Service (via managed identity)
4. **Memory Operations**: Agent → Memory Provider → CosmosDB/AI Search
5. **Telemetry**: All operations → OpenTelemetry → App Insights
6. **Fine-Tuning**: Episodes → Lightning → AI Foundry → Tuned Models

### 10.8 Success Criteria

#### Functional Requirements
- Agents authenticate via Entra ID with managed identities
- All tool calls governed by APIM policies
- MCP protocol compliance for tool discovery and execution
- Memory persistence across sessions (CosmosDB + AI Search)
- Multi-agent orchestration with semantic reasoning

#### Quality Requirements
- API latency P95 < 500ms for tool calls
- Agent evaluation scores > 0.85 for task adherence
- Zero secrets in code or logs
- 100% authentication for all endpoints

#### Operational Requirements
- Infrastructure provisioned via azd in < 30 minutes
- Zero-downtime deployments via AKS rolling updates
- Observability dashboards available within 5 minutes of deployment
- Fine-tuning pipeline executable end-to-end

### 10.9 Constraints & Assumptions

#### Technical Constraints
- Python 3.10+ for agent implementations
- Azure Kubernetes Service as container orchestrator
- Azure API Management for gateway (Developer or Standard tier)
- Azure AI Foundry for model hosting and evaluation
- CosmosDB for stateful storage with vector search

#### Security Constraints
- All traffic encrypted in transit (TLS 1.2+)
- Secrets stored only in Azure Key Vault
- Network isolation via VNet and private endpoints
- Audit logging for all authentication events

#### Compliance Assumptions
- Agents operate within Microsoft cloud boundary
- Data residency controls enforced per requirements

## 11) Practical Defaults (Repo‑level)

The accelerator ships as four independently deployable stacks (`providers/`, `submitters/`, `receivers/`, `platform/`) plus underscore-prefixed support directories shared across stacks.

| Directory | Purpose |
|-----------|---------|
| `<stack>/{backend,frontend,orchestrator}/` | Per-stack services (Submitters is the active reference implementation; Receivers mirrors it; Platform and Providers are phase-0 stubs) |
| `<stack>/_infra/` | Stack-scoped Bicep (`main.bicep` + `app/`, `core/` modules) |
| `<stack>/azure.yaml` | Stack-scoped `azd` service manifest |
| `<stack>/docker-compose.yml` | Local-dev orchestration for the stack |
| `_measures/` | Authoritative measure artifacts (CQL + value sets + narrative) |
| `_data/` | Sample FHIR bundles and seed catalog data |
| `_evals/` | Evaluation harnesses and baseline datasets |
| `_tests/` | Cross-stack pytest integration tests |
| `_scripts/` | Bootstrap and operational scripts |
| `_docs/` | Architecture, identity, evaluation, and operations docs |
| `_images/` | README assets |

