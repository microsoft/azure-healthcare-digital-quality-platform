# PostgreSQL databases

The submitters and receivers Compose stacks each own an independent
PostgreSQL 16 database for SQL-backed CQL execution.

| Stack | Database | Default host port |
|---|---|---:|
| Submitters | `dq_submitters` | `5432` |
| Receivers | `dq_receivers` | `5433` |

Both use `dq` / `dq` as local-development credentials. Override
`POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_PORT` in
the shell or a Compose environment file. Do not use the defaults outside
local development.

The local orchestrator image uses a named BuildKit context to install SDK
`0.7.1` directly from the sibling
`azure-healthcare-digital-quality-cql-sdk` checkout. This keeps Compose usable
before the package is published. Production Dockerfiles continue to install
the released `ms-cql-sdk[postgres]` package.

The initialization script creates `fhir_resources` and `terminology_codes`.
The orchestrator receives `DATABASE_URL`, seeds bundled terminology, replaces
each patient's FHIR snapshot atomically, and executes measure populations in
PostgreSQL through `ms-cql-sdk[postgres]`.

From the platform repository root:

```powershell
docker compose -f submitters/docker-compose.yml up --build
docker compose -f receivers/docker-compose.yml up --build
```

Named volumes preserve data between restarts. Use `docker compose down -v`
only when the local database data should be deleted.

## Query generated CQL SQL

Use the deployed orchestrator pod to compile a CQL definition, execute its
parameterized SQL on the private PostgreSQL server, and print the SQL, bind
parameters, patient resource counts, and result:

```powershell
./database/query-cql-sql.ps1 -Stack receivers

./database/query-cql-sql.ps1 `
	-Stack submitters `
	-Measure CMS165v9_ControllingHighBloodPressure.cql `
	-Definition Numerator `
	-PatientId patient-123
```

When `-PatientId` is omitted, the script selects the representative patient
for the chosen measure (`p-cms122-001`, `p-cms165-001`, or `p-epc02-001`) and
falls back to the most recently updated Patient. Use `-PeriodStart` and
`-PeriodEnd` together to override the measure's default Measurement Period.
Database credentials stay inside the Kubernetes Secret and are never read by
the local script.

## Load repository sample data

Load every FHIR Bundle from `_data/patients.json` into both private PostgreSQL
databases:

```powershell
./database/load-sample-fhir.ps1
```

Use `-Stack receivers` or `-Stack submitters` to seed one stack. Existing
resources for each sample patient are replaced atomically, making repeated
runs safe and removing stale resources that are no longer in the source
Bundle.

## Compare execution performance

For architecture and capacity decisions, benchmark whole-cohort SQL against
the complete per-patient in-memory pipeline:

```powershell
./database/compare-cql-cohort-performance.ps1 `
	-Stack receivers `
	-CohortSizes 10000,100000 `
	-ProjectionPatientCounts 5000000,6000000 `
	-Iterations 3 `
	-OutputPath ./tmp/cms122-cohort-performance.json
```

The script creates an isolated unlogged benchmark schema from the repository's
CMS122 sample. For each cohort size, PostgreSQL evaluates all patients in one
correlated SQL statement and returns aggregate counts. The in-memory path
transfers all FHIR JSONB resources into a dedicated AKS benchmark pod, groups
patient Bundles, and invokes CQL once per patient. Results must match before
timings are accepted. The largest measured cohort throughput is projected to
5M and 6M daily lives.

Use the original script only to investigate single-patient latency:

```powershell
./database/compare-cql-performance.ps1 -Stack receivers
./database/compare-cql-performance.ps1 `
	-Stack submitters `
	-Measure CMS165v9_ControllingHighBloodPressure.cql `
	-Definition Numerator
```

Single-patient round-trip projections are not evidence of cohort scalability
and should not be used to choose the production execution model.

## Azure

Each stack provisions its own Azure Database for PostgreSQL Flexible Server
16. Before `azd up`, set `POSTGRES_ADMINISTRATOR_PASSWORD` in that stack's
azd environment to a unique secret of at least 16 characters. The Bicep
templates do not define or output this password.

`POSTGRES_LOCATION` defaults to `centralus` in the azd parameter files because
the current MCAPS subscription is restricted from provisioning PostgreSQL in
East US 2. The private endpoint remains in the stack VNet's region.

The AKS system pool defaults to `sysd4` on `Standard_D4ds_v5`; the prior
`Standard_D2s_v6` and `Standard_D2as_v7` pools were capacity-restricted in
East US 2 during this deployment.

When VNet support is enabled, public PostgreSQL access is disabled and the
server is exposed to AKS through a private endpoint and the
`privatelink.postgres.database.azure.com` private DNS zone. Without a VNet,
the server enables Azure-service access and can optionally allow
`DEVELOPER_IP_ADDRESS`.

Provisioning outputs a passwordless `DATABASE_URL`. The deployment pipeline
must place that value and the administrator password into the existing
`orchestrator-postgres` Kubernetes Secret under these keys:

- `DATABASE_URL`
- `PGPASSWORD`

The SDK combines them when opening a connection. Keeping the password
separate prevents it from appearing in ARM deployment outputs or manifests.