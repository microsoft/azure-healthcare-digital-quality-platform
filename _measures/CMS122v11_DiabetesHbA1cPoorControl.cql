/*
 * ============================================================================
 * CMS122v11 — Diabetes: Hemoglobin A1c (HbA1c) Poor Control (> 9%)
 * CMS eCQM Name: Diabetes: Hemoglobin A1c (HbA1c) Poor Control (> 9%)
 * NQF 0059
 * Program: Medicare Shared Savings Program (SSP)
 * Measure Steward: National Committee for Quality Assurance (NCQA)
 *
 * Description:
 *   Percentage of patients 18–75 years of age with diabetes who had
 *   hemoglobin A1c > 9.0% during the measurement period.
 *   This is an INVERSE measure — a lower rate indicates better performance.
 *
 * FHIR Version: R4 (4.0.1)
 * CQL Version: 1.5
 * ============================================================================
 */

library CMS122 version '11'

using FHIR version '4.0.1'

include FHIRHelpers version '4.0.1' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'

// === Value Sets ===
valueset "Diabetes": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.103.12.1001'
valueset "HbA1c Laboratory Test": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.198.12.1013'
valueset "Outpatient Encounter": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1061'
valueset "Hospice Care": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1048.12.1001'
valueset "Frailty": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1170.12.1001'
valueset "Advanced Illness": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1167.12.1001'

// === Codes ===
code "HbA1c": '4548-4' from "LOINC" display 'Hemoglobin A1c/Hemoglobin.total in Blood'

// === Parameters ===
parameter "Measurement Period" Interval<DateTime>
  default Interval[@2025-01-01T00:00:00.0, @2026-01-01T00:00:00.0)

// === Context ===
context Patient

// === Population Criteria ===

/*
 * Initial Population:
 *   Patients 18–75 at start of measurement period
 *   with at least one qualifying outpatient encounter
 *   and an active diagnosis of diabetes
 */
define "Initial Population":
  AgeInYearsAt(date from start of "Measurement Period") >= 18
    and AgeInYearsAt(date from start of "Measurement Period") <= 75
    and exists "Qualifying Encounters"
    and exists "Diabetes Diagnosis"

define "Qualifying Encounters":
  [Encounter: "Outpatient Encounter"] E
    where E.status = 'finished'
      and E.period during "Measurement Period"

define "Diabetes Diagnosis":
  [Condition: "Diabetes"] C
    where C.clinicalStatus ~ ToConcept(Global."active")
      and Interval[C.onset as dateTime, C.abatement as dateTime] overlaps "Measurement Period"

// === Denominator ===
define "Denominator":
  "Initial Population"

// === Denominator Exclusions ===
define "Denominator Exclusions":
  "Has Hospice"
    or "Has Frailty With Advanced Illness"

define "Has Hospice":
  exists (
    [Encounter: "Hospice Care"] E
      where E.period overlaps "Measurement Period"
  )

define "Has Frailty With Advanced Illness":
  AgeInYearsAt(date from start of "Measurement Period") >= 66
    and exists ([Condition: "Frailty"] F where F.clinicalStatus ~ ToConcept(Global."active"))
    and exists ([Condition: "Advanced Illness"] A where A.clinicalStatus ~ ToConcept(Global."active"))

// === Numerator (Inverse — inclusion = poor control) ===

/*
 * Numerator:
 *   Most recent HbA1c result > 9.0%, OR no HbA1c test performed
 *   during the measurement period (missing = poor control).
 */
define "Numerator":
  "Has Most Recent HbA1c Greater Than 9"
    or "Has No HbA1c Test Performed"

define "HbA1c Tests During Measurement Period":
  [Observation: "HbA1c Laboratory Test"] O
    where O.status in { 'final', 'amended', 'corrected' }
      and O.effective as dateTime during "Measurement Period"
      and O.value is not null

define "Most Recent HbA1c":
  Last(
    "HbA1c Tests During Measurement Period" O
      sort by effective as dateTime
  )

define "Has Most Recent HbA1c Greater Than 9":
  "Most Recent HbA1c".value as Quantity > 9.0 '%'

define "Has No HbA1c Test Performed":
  not exists "HbA1c Tests During Measurement Period"

// === Supplemental Data Elements ===

define "SDE Ethnicity":
  Patient.ethnicity

define "SDE Payer":
  [Coverage] C
    where C.period overlaps "Measurement Period"

define "SDE Race":
  Patient.race

define "SDE Sex":
  Patient.gender
