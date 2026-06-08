# CMS122v11 — Diabetes: Hemoglobin A1c (HbA1c) Poor Control (> 9%)

## Measure Identity

| Field | Value |
|---|---|
| **CMS eCQM ID** | CMS122v11 |
| **CMS eCQM Name** | Diabetes: Hemoglobin A1c (HbA1c) Poor Control (> 9%) |
| **NQF Number** | 0059 |
| **Measure Steward** | National Committee for Quality Assurance (NCQA) |
| **Program** | Medicare Shared Savings Program (SSP) |
| **Measure Type** | Intermediate Outcome (Inverse) |
| **CQL Library** | `CMS122 version '11'` |
| **FHIR Version** | R4 (4.0.1) |
| **Inverse Measure** | **Yes** — lower performance rate = better |

## Clinical Intent

Diabetes is a chronic condition affecting glucose metabolism. Poorly controlled diabetes (HbA1c > 9%) significantly increases the risk of microvascular complications (retinopathy, nephropathy, neuropathy) and macrovascular events (MI, stroke). This measure identifies patients with inadequate glycemic control to drive improvement in diabetes management.

## Population Criteria

### Initial Population
Patients aged **18–75** at the start of the measurement period who:
1. Had at least one **outpatient encounter** during the measurement period.
2. Have an active diagnosis of **diabetes** (Type 1 or Type 2).

### Denominator
Equals the Initial Population.

### Denominator Exclusions
- **Hospice** care during the measurement period.
- Patients **≥ 66 years** with evidence of frailty AND advanced illness.

### Numerator (Inverse — inclusion means poor control)
Patients whose **most recent HbA1c** during the measurement period is **> 9.0%**, OR patients with **no HbA1c test** performed during the measurement period.

> **Key:** Missing lab result = numerator inclusion (poor control assumed).

### Numerator Exclusions
None.

### Denominator Exceptions
None.

## FHIR Resource Requirements

| Resource | Usage |
|---|---|
| `Patient` | Age calculation, demographics |
| `Encounter` | Qualifying outpatient visits |
| `Condition` | Diabetes diagnosis (Type 1 / Type 2) |
| `Observation` | HbA1c lab result (LOINC 4548-4) |
| `Coverage` | Payer information (supplemental data) |

## HbA1c Observation Mapping

```json
{
  "resourceType": "Observation",
  "status": "final",
  "code": {
    "coding": [
      {
        "system": "http://loinc.org",
        "code": "4548-4",
        "display": "Hemoglobin A1c/Hemoglobin.total in Blood"
      }
    ]
  },
  "valueQuantity": {
    "value": 7.2,
    "unit": "%",
    "system": "http://unitsofmeasure.org",
    "code": "%"
  },
  "effectiveDateTime": "2025-08-20T14:00:00Z"
}
```

## Measure Calculation Logic

1. Identify all patients in the **Initial Population** (18–75, diabetes dx, qualifying encounter).
2. Remove patients meeting **Denominator Exclusions** (hospice, frailty + advanced illness).
3. For each remaining patient:
   - Find the **most recent** HbA1c observation during the measurement period.
   - If HbA1c > 9.0% → patient is in the **Numerator** (poor control).
   - If **no** HbA1c test exists → patient is in the **Numerator** (missing = poor control).
   - If HbA1c ≤ 9.0% → patient is **not** in the Numerator (controlled).
4. **Performance Rate** = Numerator / (Denominator − Exclusions)
5. **Lower rate = better** (inverse measure).

## Key Implementation Notes

- This is an **inverse measure**: being in the numerator is a negative outcome.
- **Missing HbA1c** lab results count as poor control — this is by design to incentivize testing.
- Use the **most recent** HbA1c only, not the highest or average.
- Diabetes diagnosis includes both Type 1 and Type 2 (the value set covers both).
- The 9.0% threshold is **exclusive** (> 9.0%, not ≥ 9.0%).

## CQL File Reference
- [`CMS122v11_DiabetesHbA1cPoorControl.cql`](CMS122v11_DiabetesHbA1cPoorControl.cql)
