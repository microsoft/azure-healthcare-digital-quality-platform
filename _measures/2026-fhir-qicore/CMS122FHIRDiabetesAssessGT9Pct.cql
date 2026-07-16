/*
 * ============================================================================
 * CMS122FHIR v1.0.000 — Diabetes: Glycemic Status Assessment Greater Than 9%
 * ----------------------------------------------------------------------------
 * DQM (FHIR / QI-Core) primary library — converted from the eCQI Resource
 * Center human-readable specification (Performance Year 2026).
 *
 *   Source : https://ecqi.healthit.gov/ecqm/fhir-ec/2026/cms0122fhir
 *   HTML    : CMS122FHIR-v1.0.000-FHIR.html
 *   Steward : National Committee for Quality Assurance (NCQA)
 *   Effective Period: 2026-01-01 through 2026-12-31
 *   Primary Library: https://madie.cms.gov/Library/CMS122FHIRDiabetesAssessGT9Pct
 *
 * !! IMPORTANT — NOT executable by the current cql-sdk subset engine. !!
 *   This is a QI-Core 6.0.0, multi-library eCQM. It requires the shared
 *   dependency libraries below (obtained from the official MADiE/eCQI
 *   package), the QI-Core model, and CQL features the in-repo SDK does not
 *   yet support (fluent functions, unions, `let`, `case`, tuples, timing
 *   phrases, cross-library qualified refs). See the SDK/platform gap
 *   analysis that accompanies this conversion.
 *
 * Key 2026 change vs. the legacy CMS122v11:
 *   The numerator concept is now "Glycemic Status Assessment" = HbA1c OR
 *   Glucose Management Indicator (GMI, LOINC 97506-0). The lowest result on
 *   the most recent assessment day is used; a missing/absent assessment
 *   places the patient in the numerator (inverse measure).
 * ============================================================================
 */

library CMS122FHIRDiabetesAssessGT9Pct version '1.0.000'

using QICore version '6.0.0'

include FHIRHelpers version '4.4.000' called FHIRHelpers
include SupplementalDataElements version '5.1.000' called SDE
include Status version '1.15.000' called Status
include QICoreCommon version '4.0.000' called QICoreCommon
include Hospice version '6.18.000' called Hospice
include AdvancedIllnessandFrailty version '1.27.000' called AIFrailLTCF
include CumulativeMedicationDuration version '6.0.000' called CMD
include PalliativeCare version '1.18.000' called PalliativeCare

// === Code Systems ===
codesystem "LOINC": 'http://loinc.org'
codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
codesystem "HCPCS": 'http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets'

// === Value Sets ===
valueset "Diabetes": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.103.12.1001'
valueset "HbA1c Laboratory Test": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.198.12.1013'
valueset "Office Visit": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1001'
valueset "Annual Wellness Visit": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.526.3.1240'
valueset "Preventive Care Services Established Office Visit, 18 and Up": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1025'
valueset "Preventive Care Services Initial Office Visit, 18 and Up": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1023'
valueset "Home Healthcare Services": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1016'
valueset "Nutrition Services": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.1006'
valueset "Telephone Visits": 'http://cts.nlm.nih.gov/fhir/ValueSet/2.16.840.1.113883.3.464.1003.101.12.1080'

// === Direct Reference Codes ===
code "Glucose management indicator": '97506-0' from "LOINC" display 'Glucose management indicator'
code "Medical nutrition therapy; initial assessment and intervention, individual, face-to-face with the patient, each 15 minutes": '97802' from "CPT"
code "Medical nutrition therapy; re-assessment and intervention, individual, face-to-face with the patient, each 15 minutes": '97803' from "CPT"
code "Medical nutrition therapy; group (2 or more individual(s)), each 30 minutes": '97804' from "CPT"
code "Medical nutrition therapy; reassessment and subsequent intervention(s) following second referral in same year for change in diagnosis, medical condition or treatment regimen (including additional hours needed for renal disease), individual, face to face with the patient, each 15 minutes": 'G0270' from "HCPCS"
code "Medical nutrition therapy, reassessment and subsequent intervention(s) following second referral in same year for change in diagnosis, medical condition, or treatment regimen (including additional hours needed for renal disease), group (2 or more individuals), each 30 minutes": 'G0271' from "HCPCS"

parameter "Measurement Period" Interval<DateTime>
  default Interval[@2026-01-01T00:00:00.000, @2027-01-01T00:00:00.000)

context Patient

// === Population Criteria ===

define "Initial Population":
  AgeInYearsAt(date from end of "Measurement Period") in Interval[18, 75]
    and exists "Qualifying Encounters"
    and exists (
      ([ConditionEncounterDiagnosis: "Diabetes"].verified()) DiabetesDiagnosis
        where DiabetesDiagnosis.prevalenceInterval() overlaps day of "Measurement Period"
    )

define "Denominator":
  "Initial Population"

define "Denominator Exclusions":
  Hospice."Has Hospice Services"
    or AIFrailLTCF."Is Age 66 or Older Living Long Term in a Nursing Home"
    or AIFrailLTCF."Is Age 66 or Older with Advanced Illness and Frailty"
    or PalliativeCare."Has Palliative Care in the Measurement Period"

define "Numerator":
  "Has Most Recent Glycemic Status Assessment Without Result"
    or "Has Most Recent Elevated Glycemic Status Assessment"
    or "Has No Record Of Glycemic Status Assessment"

// === Supporting Definitions ===

define "Qualifying Encounters":
  ( [Encounter: "Office Visit"]
      union [Encounter: "Annual Wellness Visit"]
      union [Encounter: "Preventive Care Services Established Office Visit, 18 and Up"]
      union [Encounter: "Preventive Care Services Initial Office Visit, 18 and Up"]
      union [Encounter: "Home Healthcare Services"]
      union [Encounter: "Nutrition Services"]
      union ( [Encounter] E where exists ( (E.type) T where T ~ "Medical nutrition therapy; initial assessment and intervention, individual, face-to-face with the patient, each 15 minutes" ) )
      union ( [Encounter] E where exists ( (E.type) T where T ~ "Medical nutrition therapy; re-assessment and intervention, individual, face-to-face with the patient, each 15 minutes" ) )
      union ( [Encounter] E where exists ( (E.type) T where T ~ "Medical nutrition therapy; group (2 or more individual(s)), each 30 minutes" ) )
      union ( [Encounter] E where exists ( (E.type) T where T ~ "Medical nutrition therapy; reassessment and subsequent intervention(s) following second referral in same year for change in diagnosis, medical condition or treatment regimen (including additional hours needed for renal disease), individual, face to face with the patient, each 15 minutes" ) )
      union ( [Encounter] E where exists ( (E.type) T where T ~ "Medical nutrition therapy, reassessment and subsequent intervention(s) following second referral in same year for change in diagnosis, medical condition, or treatment regimen (including additional hours needed for renal disease), group (2 or more individuals), each 30 minutes" ) )
      union [Encounter: "Telephone Visits"]
  ).isEncounterPerformed() ValidEncounter
    where ValidEncounter.period during day of "Measurement Period"

define "Glycemic Status Assessment":
  ( [LaboratoryResultObservation: "HbA1c Laboratory Test"]
      union [LaboratoryResultObservation: "Glucose management indicator"]
  ).isLaboratoryTestPerformed() GlycemicStatus
    where GlycemicStatus.effective.latest() during day of "Measurement Period"

define "Most Recent Glycemic Status Date":
  Last(
    ( "Glycemic Status Assessment" QualifyingGlycemicStatus
        return date from QualifyingGlycemicStatus.effective.latest()
    ) QualifyingGlycemicStatus
      sort asc
  )

define "Lowest Glycemic Status Assessment Reading on Most Recent Day":
  First(
    "Glycemic Status Assessment" QualifyingGlycemicStatus
      where QualifyingGlycemicStatus.effective.latest() same day as "Most Recent Glycemic Status Date"
      return {
        QualifyingGlycemicStatusValue: QualifyingGlycemicStatus.value as Quantity,
        QualifyingGlycemicStatus: QualifyingGlycemicStatus
      }
      sort by QualifyingGlycemicStatusValue
  ).QualifyingGlycemicStatus

define "Has Most Recent Glycemic Status Assessment Without Result":
  "Lowest Glycemic Status Assessment Reading on Most Recent Day" is not null
    and "Lowest Glycemic Status Assessment Reading on Most Recent Day".value is null

define "Has Most Recent Elevated Glycemic Status Assessment":
  "Lowest Glycemic Status Assessment Reading on Most Recent Day".value > 9 '%'

define "Has No Record Of Glycemic Status Assessment":
  not exists "Glycemic Status Assessment"

// === Supplemental Data Elements ===

define "SDE Ethnicity":
  SDE."SDE Ethnicity"

define "SDE Payer":
  SDE."SDE Payer"

define "SDE Race":
  SDE."SDE Race"

define "SDE Sex":
  SDE."SDE Sex"
