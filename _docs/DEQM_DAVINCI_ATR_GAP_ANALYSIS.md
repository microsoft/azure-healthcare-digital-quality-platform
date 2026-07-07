# DEQM / Da Vinci ATR Cohort-Exchange Gap Analysis

> Issue: [#11 — Submitters, Receivers: Align cohort exchange with DEQM / Da Vinci
> ATR FHIR Group resource](https://github.com/microsoft/azure-healthcare-digital-quality-platform/issues/11)

## Background

During standards review, alignment was recommended between our cohort exchange
and the existing **DEQM** and **Da Vinci Risk Based Contracts Member Attribution
(ATR)** implementation guides, both of which use the FHIR **`Group`** resource
for patient-roster / attribution exchange.

Previously the platform defined a **custom cohort payload** (`docType=cohort`
with a bare `memberIds[]` string array, plus a proprietary
`SubmissionProcessRequest.cohort` dict). This reduced interoperability and
required custom mappings for providers, payers, and regulators.

## What changed

Cohort rosters are now representable as a standards-based FHIR `Group`:

- **Producer (submitters)** and **consumer (receivers)** each expose:
  - `GET /api/workbench/cohorts/{cohortId}/Group` — export a cohort as an
    ATR-aligned `Group`.
  - `POST /api/workbench/cohorts/$import-group` — create/update a cohort from a
    `Group` (inverse).
- The DEQM `subject-list` / `summary` MeasureReports already reference a cohort
  `Group` via `subject`; that contained `Group` is now the **same ATR-aligned
  builder**, so the roster carried in a report and the roster exchanged directly
  are identical.
- OpenAPI: [`_docs/openapi/cohort-group-exchange.openapi.yaml`](openapi/cohort-group-exchange.openapi.yaml)
  documents the endpoints, the `Group` schema, and a Group-based roster example.

### Cohort ⇄ Group field mapping

| Cohort field   | FHIR `Group` location                                    |
|----------------|----------------------------------------------------------|
| `id`           | `Group.id` (`group-{id}` prefix)                         |
| `name`         | `Group.name`                                            |
| `memberIds[]`  | `Group.member[].entity` = `Reference(Patient/{id})`     |
| `measureIds[]` | `Group.characteristic[].valueReference` → `Measure`      |
| period         | `Group.member[].period` + `characteristic.valuePeriod`  |
| (fixed)        | `type=person`, `actual=true`, `quantity`, `member.inactive=false` |

## DEQM capability-statement mapping

| Workflow step                    | DEQM / Da Vinci capability                              | Status |
|----------------------------------|--------------------------------------------------------|--------|
| Define cohort / patient roster   | Da Vinci ATR `atr-group` (Group profile)               | ✅ Aligned (see gaps) |
| Reference roster from report     | DEQM `subject-list` / `summary` `MeasureReport.subject` → `Group` | ✅ Implemented (#13) |
| Submit measure data              | DEQM `$submit-data` (MeasureReport + resources)         | ✅ Existing (`/fhir/Measure/{id}/$submit-data`) |
| Evaluate measure                 | DEQM `$evaluate-measure`                                | ✅ Existing |
| Collect data / data requirements | DEQM `$collect-data`, `$data-requirements`             | ✅ Existing |
| Roster attribution changes       | ATR `$member-add` / `$member-remove` operations         | ❌ Gap (see below) |

## Conformance gaps (ATR)

The current `Group` is **ATR-aligned** but not fully **ATR-conformant**. Known
gaps, deferred with justification:

1. **Attribution-list operations** — ATR defines `Group/$member-add`,
   `$member-remove`, and async `$export` for large rosters. We expose a simple
   `$import-group` upsert instead. *Justification:* the pilot exchanges whole
   cohorts, not incremental deltas; bulk async export is unnecessary at pilot
   scale.
2. **Member `changeType` extension** (`nochange` / `add` / `remove`) — not
   emitted. *Justification:* only needed for incremental roster sync (gap #1).
3. **Coverage & attributed-provider slices** — ATR `atr-group` slices
   `member.entity` by `Patient`, `Coverage`, and attributed `Practitioner/
   Organization`. We only carry `Patient` members today. *Justification:* the
   quality-measurement cohort is a patient population; coverage/provider
   attribution is not yet modeled in the workbench.
4. **`managingEntity`** — supported by the builder (optional param) but not
   populated, because the workbench has no `Organization` record for the
   reporting entity yet. Populated from `DEQM_REPORTER_*` config is a follow-up.
5. **`characteristic` slices** — ATR defines specific characteristic slices;
   we emit a generic membership marker plus period/measure characteristics.
   *Justification:* sufficient for round-tripping cohort semantics; formal slice
   conformance requires the full ATR profile validator.

## Recommended follow-ups / HL7 DEQM feedback

- Model an `Organization` for the reporting entity and populate
  `Group.managingEntity` + `MeasureReport.reporter` consistently.
- Add ATR `$member-add` / `$member-remove` if incremental roster sync is needed.
- Consider profiling `member.entity` for coverage/provider attribution if the
  platform expands beyond patient-only cohorts.
- Feedback candidate for the HL7 DEQM workgroup: clarify the expected
  relationship between a `subject-list` `MeasureReport.subject` `Group` and an
  ATR attribution `Group` (are they the same resource, or linked?).

## Acceptance criteria status

- [x] Cohort exchange representable using FHIR `Group` (or documented deviation).
- [x] OpenAPI examples include a Group-based patient-roster example.
- [x] Submission workflow mapped to DEQM capability statements (table above).
- [x] Gap analysis completed (this document).
- [x] Standards mappings referenced from the specification
      ([`SPECIFICATION.md`](SPECIFICATION.md)).
