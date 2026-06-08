# ePC-02 — Severe Obstetric Complications

## Measure Identity

| Field | Value |
|---|---|
| **CMS eCQM ID** | ePC-02 |
| **CMS eCQM Name** | Severe Obstetric Complications |
| **NQF Number** | N/A |
| **Measure Steward** | The Joint Commission |
| **Program** | Hospital Quality Reporting (HQR) eCQM |
| **Measure Type** | Outcome |
| **CQL Library** | `ePC02 version '1'` |
| **FHIR Version** | R4 (4.0.1) |
| **Inverse Measure** | **Yes** — lower performance rate = better |

## Clinical Intent

Severe maternal morbidity (SMM) is a leading indicator of maternal health system quality. This measure captures the rate of life-threatening complications during inpatient delivery hospitalizations. It aligns with CMS and CDC efforts to reduce preventable maternal morbidity and mortality.

## Population Criteria

### Initial Population
Inpatient delivery encounters where:
1. A **delivery procedure** or delivery diagnosis is documented.
2. **Gestational age ≥ 20 weeks**.
3. Patient age **8–65 years** at the time of the encounter.
4. Encounter ends during the **measurement period**.

### Denominator
Equals the Initial Population, minus patients with certain pre-existing conditions.

### Denominator Exclusions
- Encounters where the **patient expired** (discharge disposition = expired).
- Encounters with **severe maternal morbidity diagnoses present on admission (POA)** — conditions that existed before the delivery hospitalization.

### Numerator
Delivery encounters with **one or more** of the following severe obstetric complications occurring during the encounter (NOT present on admission):

| Complication | Description |
|---|---|
| Eclampsia | New-onset seizures in pregnancy with hypertension |
| Obstetric hemorrhage with transfusion | ≥ 4 units packed red blood cells transfused |
| Hysterectomy | Emergency peripartum hysterectomy |
| ICU admission | Transfer to intensive care unit |
| Mechanical ventilation | ≥ 96 hours of assisted ventilation |
| Sepsis | Systemic infection during delivery admission |
| Shock | Hemodynamic instability requiring intervention |
| Thrombotic embolism | Pulmonary embolism, DVT during admission |
| Acute renal failure | New-onset renal failure during admission |
| Cardiac arrest | Cardiac arrest during delivery admission |
| Amniotic fluid embolism | AFE during delivery |

### Numerator Exclusions
None.

## FHIR Resource Requirements

| Resource | Usage |
|---|---|
| `Patient` | Age calculation, demographics |
| `Encounter` | Delivery encounter, ICU encounter, discharge disposition |
| `Condition` | Delivery diagnosis, SMM diagnoses, eclampsia, POA indicator |
| `Procedure` | Delivery procedure, transfusion, hysterectomy, ventilation |
| `Observation` | Gestational age (LOINC 11884-4) |
| `Coverage` | Payer information (supplemental data) |

## Delivery Encounter Mapping

```json
{
  "resourceType": "Encounter",
  "status": "finished",
  "class": { "code": "IMP", "display": "inpatient encounter" },
  "type": [
    {
      "coding": [
        {
          "system": "http://snomed.info/sct",
          "code": "177184002",
          "display": "Normal delivery procedure"
        }
      ]
    }
  ],
  "period": {
    "start": "2025-07-10T08:00:00Z",
    "end": "2025-07-12T16:00:00Z"
  },
  "hospitalization": {
    "dischargeDisposition": {
      "coding": [
        { "system": "http://terminology.hl7.org/CodeSystem/discharge-disposition", "code": "home" }
      ]
    }
  }
}
```

## Measure Calculation Logic

1. Identify all **delivery encounters** (Initial Population) with gestational age ≥ 20 weeks and patient age 8–65.
2. Remove encounters meeting **Denominator Exclusions** (patient expired, pre-existing SMM POA).
3. For each remaining encounter, check for any of the severe obstetric complications:
   - SMM diagnosis codes with onset **during** the encounter (not POA).
   - SMM procedure codes performed **during** the encounter.
   - Specific complications: eclampsia, transfusion ≥ 4 units, hysterectomy, ICU admission, ventilation ≥ 96 hrs.
4. If **any** complication is found → encounter is in the **Numerator**.
5. **Performance Rate** = Numerator / (Denominator − Exclusions)
6. **Lower rate = better** (outcome measure).

## Key Implementation Notes

- This is an **encounter-level** measure (each delivery encounter is evaluated independently), not a patient-level measure.
- The **Present on Admission (POA)** indicator is critical — pre-existing conditions must be excluded from the numerator to measure only complications arising during the hospitalization.
- Blood transfusion threshold is **≥ 4 units** of packed red blood cells (pRBCs).
- Mechanical ventilation threshold is **≥ 96 hours** (4 days continuous).
- Multiple complications in the same encounter count as a single numerator event.
- Gestational age can come from an `Observation` (LOINC 11884-4) or from a `Condition` code indicating ≥ 20 weeks.

## CQL File Reference
- [`ePC02_SevereObstetricComplications.cql`](ePC02_SevereObstetricComplications.cql)
