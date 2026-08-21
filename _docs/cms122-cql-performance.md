# CMS122 CQL execution performance

## Executive conclusion

For whole-cohort quality measurement, execute CQL as SQL in PostgreSQL.

The original repeated single-patient benchmark favored in-memory execution,
but it measured local expression latency against a cross-region database round
trip. It did not include the work required to retrieve, transfer, group, and
evaluate millions of patient Bundles in the AKS process. It therefore could
not support a cohort architecture decision.

The replacement cohort benchmark compares complete pipelines:

- **PostgreSQL cohort SQL:** one correlated SQL statement evaluates every
  patient in the cohort and returns aggregate total/matched counts. Clinical
  resources remain in PostgreSQL.
- **Per-patient in-memory:** every FHIR JSONB resource for the cohort is
  transferred from PostgreSQL to an AKS benchmark pod, grouped into patient
  Bundles, and evaluated one patient at a time with SDK caches cleared.

Results were identical at every accepted cohort size. PostgreSQL was **4.60×
faster at 10,000 lives** and **4.96× faster at 100,000 lives**.

## Test environment

| Component | Configuration |
|---|---|
| Measure | CMS122v11 Initial Population |
| SDK | `ms-cql-sdk 0.7.1` |
| AKS | Receivers cluster, East US 2 |
| Benchmark pod | 2 CPU requested/limited, 8 GiB memory limit |
| PostgreSQL | PostgreSQL 16.14, `Standard_B1ms`, Central US |
| Source patients | Four CMS122 populations from `_data/patients.json` |
| Synthetic cohort | Cycled patient shapes; average 4.5 FHIR resources/patient |
| Warmup | 1 iteration per engine and cohort size |
| Measured runs | 3 iterations per engine and cohort size |

PostgreSQL was in a different Azure region from AKS. This adds network latency
and disadvantages SQL, yet cohort SQL still produced substantially greater
throughput.

## Benchmark data setup

The script created an isolated unlogged schema and cycled
`p-cms122-x01`, `p-cms122-001`, `p-cms122-003`, and `p-cms122-002` across
100,000 unique patient identities. It copied terminology, built indexes, and
ran `ANALYZE`.

| Item | Value |
|---|---:|
| Generated patients | 100,000 |
| Generated FHIR resources | 450,000 |
| Setup start | 2026-08-21 03:24:13.910833 UTC |
| Setup end | 2026-08-21 03:24:27.437958 UTC |
| Setup duration | 13.53 seconds |
| Included in engine timing | No |

The schema was dropped after the benchmark.

## Measured cohort results

### 10,000-life population

| Engine | Mean | Median | p95 | Throughput |
|---|---:|---:|---:|---:|
| PostgreSQL cohort SQL | 0.689 s | 0.704 s | 0.711 s | 14,522 patients/s |
| Per-patient in-memory | 3.166 s | 3.189 s | 3.232 s | 3,158 patients/s |

PostgreSQL was **4.60× faster**. Both engines returned 10,000 total and 7,500
matched patients.

### 100,000-life population

| Engine | Mean | Median | p95 | Throughput |
|---|---:|---:|---:|---:|
| PostgreSQL cohort SQL | 6.489 s | 5.862 s | 7.785 s | 15,410 patients/s |
| Per-patient in-memory | 32.170 s | 32.449 s | 32.955 s | 3,109 patients/s |

PostgreSQL was **4.96× faster**. Both engines returned 100,000 total and
75,000 matched patients.

The SQL advantage increased with cohort size. PostgreSQL throughput improved
from 14,522 to 15,410 patients/s as fixed query and network overhead was
amortized. In-memory throughput stayed near 3,100 patients/s because it scales
with resource transfer, JSON hydration, Bundle grouping, and one SDK invocation
per patient.

## Multi-million-life daily-volume projection

These projections use measured throughput from the 100,000-life run on the
same single benchmark pod and database. No idealized worker multiplier is
applied.

| Daily lives | PostgreSQL cohort SQL | Per-patient in-memory |
|---:|---:|---:|
| 5,000,000 | 324.5 s / **5.41 min** | 1,608.5 s / **26.81 min** |
| 6,000,000 | 389.3 s / **6.49 min** | 1,930.2 s / **32.17 min** |

At six million daily lives, the measured topology leaves substantial room
inside a 24-hour processing window. Production sizing must still include all
measures, multiple populations per measure, retries, source-data refresh,
result persistence, and concurrent tenants.

## Why the first benchmark was misleading

The original report measured one already-hydrated patient repeatedly:

| Engine | Mean | p95 | Apparent throughput |
|---|---:|---:|---:|
| In-memory SDK expression evaluation | 0.228 ms | 0.265 ms | 4,375 patients/s |
| One patient-scoped PostgreSQL round trip | 99.940 ms | 102.155 ms | 10 patients/s |

Run window: 2026-08-21 03:00:22.752370–03:00:33.784769 UTC, 100 iterations.

That test answered a valid but different question: *what is the latency of an
already-local single-patient expression compared with one remote SQL round
trip?* Multiplying those latencies by millions assumed one SQL request per
patient and assumed in-memory data appeared in the pod for free. Neither
assumption represents cohort processing.

Use the single-patient result for interactive API latency tuning only. Do not
use it for cohort capacity planning.

## Recommendation

1. Use PostgreSQL cohort SQL for scheduled population measurement, payer and
   provider cohorts, regulatory reporting, and daily multi-million-life runs.
2. Retain in-memory execution for interactive single-patient evaluation,
   debugging, conformance comparison, and environments without PostgreSQL.
3. Keep result-equivalence checks between engines as a release gate.
4. Co-locate AKS and PostgreSQL before production load testing; the current
   cross-region topology adds avoidable latency and egress.
5. Run concurrent cohort tests with representative measure portfolios,
   realistic code/value-set distributions, and database telemetry before
   committing production capacity.

## Reproduce

Load repository sample data:

```powershell
./database/load-sample-fhir.ps1
```

Run the decision-grade cohort benchmark:

```powershell
./database/compare-cql-cohort-performance.ps1 `
  -Stack receivers `
  -CohortSizes 10000,100000 `
  -ProjectionPatientCounts 5000000,6000000 `
  -Iterations 3 `
  -WarmupIterations 1 `
  -CpuRequest 2 `
  -MemoryLimitGi 8 `
  -OutputPath ./tmp/cms122-cohort-performance.json
```

Raw reports:

- `tmp/cms122-cohort-performance.json` — cohort benchmark and projections.
- `tmp/cms122-cql-performance.json` — single-patient latency microbenchmark.

## Limitations

- The cohort cycles four CMS122 sample populations with different resource
  shapes and a 75% match rate. Real cohorts still have wider resource-count,
  code-distribution, and selectivity variance.
- Only one measure definition was benchmarked. A production run evaluates
  several populations across many measures.
- The 5M/6M figures linearly extrapolate throughput measured at 100k lives.
- The test does not model concurrent customers, connection-pool contention,
  autoscaling delays, PostgreSQL throttling, or downstream result writes.
- Unlogged benchmark tables differ from durable production tables for writes;
  write/setup time was excluded from engine measurements.