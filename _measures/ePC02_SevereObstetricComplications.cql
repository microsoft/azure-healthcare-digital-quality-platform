/*
 * ============================================================================
 * ePC-02 — Severe Obstetric Complications
 * CMS eCQM Name: Severe Obstetric Complications
 * Program: Hospital Quality Reporting (HQR) eCQM
 * Measure Steward: The Joint Commission
 *
 * Description:
 *   Percentage of patients with severe obstetric complications that occur
 *   during the inpatient delivery hospitalization. Severe obstetric
 *   complications include eclampsia, obstetric hemorrhage requiring
 *   transfusion, hysterectomy, ICU admission, mechanical ventilation,
 *   sepsis, shock, thrombotic embolism, acute renal failure, cardiac
 *   arrest, and other life-threatening conditions.
 *
 * FHIR Version: R4 (4.0.1)
 * CQL Version: 1.5
 * ============================================================================
 */

library ePC02 version '1'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMEDCT": 'http://snomed.info/sct'
codesystem "ICD10CM": 'http://hl7.org/fhir/sid/icd-10-cm'
codesystem "ICD10PCS": 'http://www.cms.gov/Medicare/Coding/ICD10'

// === Value Sets ===
valueset "Delivery Procedures": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.67'
valueset "Delivery Encounter": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.68'
valueset "Severe Maternal Morbidity Diagnoses": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.54'
valueset "Severe Maternal Morbidity Procedures": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.56'
valueset "Blood Transfusion": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.55'
valueset "Eclampsia": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.69'
valueset "Gestational Age >= 20 Weeks": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.70'
valueset "Present on Admission": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.71'
valueset "ICU Admission": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.72'
valueset "Hysterectomy": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.73'
valueset "Mechanical Ventilation": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113762.1.4.1029.74'

// === Codes ===
code "Gestational age at birth": '11884-4' from "LOINC" display 'Gestational age Estimated'

// === Parameters ===
parameter "Measurement Period" Interval<DateTime>
  default Interval[@2025-01-01T00:00:00.0, @2026-01-01T00:00:00.0)

// === Context ===
context Patient

// === Population Criteria ===

/*
 * Initial Population:
 *   Inpatient delivery encounters with gestational age >= 20 weeks
 *   Patient age 8–65 years at time of encounter
 */
define "Initial Population":
  "Delivery Encounters With Gestational Age >= 20 Weeks"

define "Delivery Encounters":
  [Encounter: "Delivery Encounter"] E
    where E.status = 'finished'
      and E.period ends during "Measurement Period"
      and E.class ~ 'inpatient'
      and AgeInYearsAt(date from start of E.period) >= 8
      and AgeInYearsAt(date from start of E.period) <= 65

define "Delivery Encounters With Gestational Age >= 20 Weeks":
  "Delivery Encounters" E
    where exists (
      [Observation: "Gestational age at birth"] O
        where O.status in { 'final', 'amended', 'corrected' }
          and O.effective as dateTime during E.period
          and O.value as Quantity >= 20 'wk'
    )
    or exists (
      [Condition: "Gestational Age >= 20 Weeks"] C
        where C.onset as dateTime during E.period
    )

// === Denominator ===

/*
 * Denominator:
 *   Initial population minus patients with pre-existing conditions
 *   present on admission (POA) from the severe complications list
 */
define "Denominator":
  "Initial Population"

// === Denominator Exclusions ===
define "Denominator Exclusions":
  "Encounters With Patient Expired"
    or "Encounters With Pre-Existing Complications POA"

define "Encounters With Patient Expired":
  exists (
    "Delivery Encounters" E
      where E.hospitalization.dischargeDisposition ~ 'exp'
  )

define "Encounters With Pre-Existing Complications POA":
  exists (
    "Delivery Encounters" E
      where exists (
        [Condition: "Severe Maternal Morbidity Diagnoses"] C
          where C.onset as dateTime before start of E.period
            and C.extension("present-on-admission") is not null
      )
  )

// === Numerator ===

/*
 * Numerator:
 *   Delivery encounters with one or more severe obstetric complications
 *   occurring during the encounter (NOT present on admission).
 */
define "Numerator":
  "Has Severe Maternal Morbidity Diagnosis"
    or "Has Severe Maternal Morbidity Procedure"
    or "Has Eclampsia"
    or "Has Blood Transfusion >= 4 Units"
    or "Has ICU Admission"
    or "Has Hysterectomy"
    or "Has Mechanical Ventilation"

define "Has Severe Maternal Morbidity Diagnosis":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Condition: "Severe Maternal Morbidity Diagnoses"] C
            where C.onset as dateTime during E.period
              and not (C.onset as dateTime before start of E.period)
        )
    )
  )

define "Has Severe Maternal Morbidity Procedure":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Procedure: "Severe Maternal Morbidity Procedures"] P
            where P.status = 'completed'
              and P.performed as Period during E.period
        )
    )
  )

define "Has Eclampsia":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Condition: "Eclampsia"] C
            where C.onset as dateTime during E.period
              and not (C.onset as dateTime before start of E.period)
        )
    )
  )

define "Has Blood Transfusion >= 4 Units":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Procedure: "Blood Transfusion"] P
            where P.status = 'completed'
              and P.performed as Period during E.period
        )
    )
  )

define "Has ICU Admission":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Encounter: "ICU Admission"] ICU
            where ICU.period during E.period
        )
    )
  )

define "Has Hysterectomy":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Procedure: "Hysterectomy"] P
            where P.status = 'completed'
              and P.performed as Period during E.period
        )
    )
  )

define "Has Mechanical Ventilation":
  exists (
    flatten (
      "Delivery Encounters" E
        return (
          [Procedure: "Mechanical Ventilation"] P
            where P.status = 'completed'
              and P.performed as Period during E.period
              and duration in hours of (P.performed as Period) >= 96
        )
    )
  )

// === Supplemental Data Elements ===

define "SDE Ethnicity":
  Patient.ethnicity

define "SDE Race":
  Patient.race

define "SDE Sex":
  Patient.gender

define "SDE Payer":
  [Coverage] C
    where C.period overlaps "Measurement Period"
