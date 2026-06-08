/*
 * ============================================================================
 * CMS165v9 — Controlling High Blood Pressure
 * CMS eCQM Name: Controlling High Blood Pressure
 * NQF 0018
 * Program: Universal Foundation
 * Measure Steward: National Committee for Quality Assurance (NCQA)
 *
 * Description:
 *   Percentage of patients 18–85 years of age who had a diagnosis of
 *   essential hypertension starting before and continuing into, or starting
 *   during the first six months of the measurement period, and whose most
 *   recent blood pressure was adequately controlled (<140/<90 mmHg).
 *
 * FHIR Version: R4 (4.0.1)
 * CQL Version: 1.5
 * ============================================================================
 */

library CMS165 version '9'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'

// === Value Sets ===
valueset "Essential Hypertension": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.104.12.1011'
valueset "Outpatient Encounter": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1061'
valueset "End Stage Renal Disease": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.109.12.1029'
valueset "Dialysis Services": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.109.12.1013'
valueset "Kidney Transplant": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.109.12.1012'
valueset "Pregnancy": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.111.12.1012'
valueset "Hospice Care": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1048.12.1001'
valueset "Frailty": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1170.12.1001'
valueset "Advanced Illness": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1167.12.1001'

// === Codes ===
code "Systolic blood pressure": '8480-6' from "LOINC" display 'Systolic blood pressure'
code "Diastolic blood pressure": '8462-4' from "LOINC" display 'Diastolic blood pressure'

// === Parameters ===
parameter "Measurement Period" Interval<DateTime>
  default Interval[@2025-01-01T00:00:00.0, @2026-01-01T00:00:00.0)

// === Context ===
context Patient

// === Helper Definitions ===
define "First Six Months of Measurement Period":
  Interval[start of "Measurement Period", start of "Measurement Period" + 6 months)

// === Population Criteria ===

/*
 * Initial Population:
 *   Patients 18–85 at start of measurement period
 *   with at least one qualifying outpatient encounter
 *   and a diagnosis of essential hypertension overlapping the first 6 months
 */
define "Initial Population":
  AgeInYearsAt(date from start of "Measurement Period") >= 18
    and AgeInYearsAt(date from start of "Measurement Period") < 86
    and exists "Qualifying Encounters"
    and exists "Hypertension Diagnosis"

define "Qualifying Encounters":
  [Encounter: "Outpatient Encounter"] E
    where E.status = 'finished'
      and E.period during "Measurement Period"

define "Hypertension Diagnosis":
  [Condition: "Essential Hypertension"] C
    where C.clinicalStatus ~ ToConcept(Global."active")
      and (
        C.onset as Period overlaps "First Six Months of Measurement Period"
        or C.onset as dateTime before end of "First Six Months of Measurement Period"
      )

// === Denominator ===
define "Denominator":
  "Initial Population"

// === Denominator Exclusions ===
define "Denominator Exclusions":
  "Has ESRD"
    or "Has Dialysis"
    or "Has Kidney Transplant"
    or "Has Pregnancy"
    or "Has Hospice"
    or "Has Frailty With Advanced Illness"

define "Has ESRD":
  exists (
    [Condition: "End Stage Renal Disease"] C
      where C.clinicalStatus ~ ToConcept(Global."active")
        and Interval[C.onset as dateTime, C.abatement as dateTime] overlaps "Measurement Period"
  )

define "Has Dialysis":
  exists (
    [Procedure: "Dialysis Services"] P
      where P.status = 'completed'
        and P.performed as Period overlaps "Measurement Period"
  )

define "Has Kidney Transplant":
  exists (
    [Procedure: "Kidney Transplant"] P
      where P.status = 'completed'
        and P.performed as Period overlaps "Measurement Period"
  )

define "Has Pregnancy":
  exists (
    [Condition: "Pregnancy"] C
      where Interval[C.onset as dateTime, C.abatement as dateTime] overlaps "Measurement Period"
  )

define "Has Hospice":
  exists (
    [Encounter: "Hospice Care"] E
      where E.period overlaps "Measurement Period"
  )

define "Has Frailty With Advanced Illness":
  AgeInYearsAt(date from start of "Measurement Period") >= 66
    and exists ([Condition: "Frailty"] F where F.clinicalStatus ~ ToConcept(Global."active"))
    and exists ([Condition: "Advanced Illness"] A where A.clinicalStatus ~ ToConcept(Global."active"))

// === Numerator ===

/*
 * Numerator:
 *   Most recent blood pressure during the measurement period has
 *     Systolic < 140 mmHg AND Diastolic < 90 mmHg
 */
define "Numerator":
  "Most Recent Systolic Reading" < 140 'mm[Hg]'
    and "Most Recent Diastolic Reading" < 90 'mm[Hg]'

define "Blood Pressure Observations":
  [Observation: "Systolic blood pressure"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and O.effective as dateTime during "Measurement Period"
      and O.value is not null

define "Most Recent BP Observation":
  Last(
    "Blood Pressure Observations" O
      sort by effective as dateTime
  )

define "Most Recent Systolic Reading":
  "Most Recent BP Observation".value as Quantity

define "Most Recent Diastolic Reading":
  singleton from (
    [Observation: "Diastolic blood pressure"] O
      where O.status in { 'final', 'amended', 'corrected' }
        and O.effective as dateTime = ("Most Recent BP Observation".effective as dateTime)
  ).value as Quantity
