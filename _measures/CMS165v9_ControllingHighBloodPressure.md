# CMS165v9 — Controlling High Blood Pressure

## Measure Identity

| Field | Value |
|---|---|
| **CMS eCQM ID** | CMS165v9 |
| **CMS eCQM Name** | Controlling High Blood Pressure |
| **NQF Number** | 0018 |
| **Measure Steward** | National Committee for Quality Assurance (NCQA) |
| **Program** | Universal Foundation |
| **Measure Type** | Process / Intermediate Outcome |
| **CQL Library** | `CMS165 version '9'` |
| **FHIR Version** | R4 (4.0.1) |
| **Inverse Measure** | No (higher performance rate = better) |

## Clinical Intent

Hypertension is a leading modifiable risk factor for cardiovascular disease, stroke, and kidney failure. This measure assesses whether patients with an established diagnosis of essential hypertension achieve adequate blood pressure control during the measurement period.

## Population Criteria

### Initial Population
Patients aged **18–85** at the start of the measurement period who:
1. Had at least one **outpatient encounter** during the measurement period.
2. Have an active diagnosis of **essential hypertension** that starts before and continues into, or starts during, the **first six months** of the measurement period.

### Denominator
Equals the Initial Population.

### Denominator Exclusions
- **End Stage Renal Disease (ESRD)**, dialysis services, or kidney transplant during the measurement period.
- **Pregnancy** during the measurement period.
- **Hospice** care during the measurement period.
- Patients **≥ 66 years** with evidence of frailty AND advanced illness, or residing in a long-term institutional setting.

### Numerator
Patients whose **most recent blood pressure** reading during the measurement period satisfies:
- Systolic BP **< 140 mmHg**, AND
- Diastolic BP **< 90 mmHg**

### Numerator Exclusions
None.

### Denominator Exceptions
None.

## FHIR Resource Requirements

| Resource | Usage |
|---|---|
| `Patient` | Age calculation, demographics |
| `Encounter` | Qualifying outpatient visits |
| `Condition` | Essential hypertension diagnosis |
| `Observation` | Systolic BP (LOINC 8480-6), Diastolic BP (LOINC 8462-4) |
| `Procedure` | Dialysis, kidney transplant |
| `Coverage` | Payer information (supplemental data) |

## Blood Pressure Observation Mapping

The measure requires both systolic and diastolic components. In FHIR, blood pressure is typically recorded as a panel observation:

```json
{
  "resourceType": "Observation",
  "status": "final",
  "code": {
    "coding": [{ "system": "http://loinc.org", "code": "85354-9", "display": "Blood pressure panel" }]
  },
  "component": [
    {
      "code": { "coding": [{ "system": "http://loinc.org", "code": "8480-6" }] },
      "valueQuantity": { "value": 128, "unit": "mmHg", "system": "http://unitsofmeasure.org", "code": "mm[Hg]" }
    },
    {
      "code": { "coding": [{ "system": "http://loinc.org", "code": "8462-4" }] },
      "valueQuantity": { "value": 82, "unit": "mmHg", "system": "http://unitsofmeasure.org", "code": "mm[Hg]" }
    }
  ],
  "effectiveDateTime": "2025-06-15T10:30:00Z"
}
```

## Measure Calculation Logic

1. Identify all patients in the **Initial Population**.
2. Remove patients meeting **Denominator Exclusions**.
3. For each remaining patient, find the **most recent** BP observation during the measurement period.
4. If systolic < 140 AND diastolic < 90 → patient is in the **Numerator**.
5. **Performance Rate** = Numerator / (Denominator − Exclusions)

## Key Implementation Notes

- The hypertension diagnosis must overlap the **first 6 months** of the measurement period (not the full period).
- Use the **most recent** BP reading only — not an average or any reading.
- Both systolic AND diastolic thresholds must be met simultaneously.
- If no BP reading exists during the measurement period, the patient is **not in the numerator** (gap in care).

## CQL File Reference
- [`CMS165v9_ControllingHighBloodPressure.cql`](CMS165v9_ControllingHighBloodPressure.cql)
