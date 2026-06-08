import React from "react";
import {
  FhirView,
  FhirEncounter,
  FhirCondition,
  FhirObservation,
  FhirProcedure,
  FhirCoverage,
} from "./fhirTypes";

interface FhirResourcePanelProps {
  fhir?: FhirView;
}

const formatDate = (value: string | undefined): string => {
  if (!value) {
    return "N/A";
  }
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    return value;
  }
  return d.toLocaleString();
};

const sectionHeaderClass = "text-lg font-semibold text-gray-800 mb-2";
const cardClass = "bg-white rounded-lg border border-gray-200 p-4";

const listOrEmpty = <T,>(list: T[] | undefined, renderer: (item: T, idx: number) => React.ReactNode): React.ReactNode => {
  if (!list || list.length === 0) {
    return <p className="text-sm text-gray-500">No resources available.</p>;
  }
  return <div className="space-y-2">{list.map(renderer)}</div>;
};

const FhirResourcePanel: React.FC<FhirResourcePanelProps> = ({ fhir }) => {
  const encounters = fhir?.encounters || [];
  const conditions = fhir?.conditions || [];
  const observations = fhir?.observations || [];
  const procedures = fhir?.procedures || [];
  const coverages = fhir?.coverages || [];
  const encounterCount = fhir?.resourceCounts?.Encounter ?? encounters.length;
  const conditionCount = fhir?.resourceCounts?.Condition ?? conditions.length;
  const observationCount = fhir?.resourceCounts?.Observation ?? observations.length;
  const procedureCount = fhir?.resourceCounts?.Procedure ?? procedures.length;
  const coverageCount = fhir?.resourceCounts?.Coverage ?? coverages.length;

  return (
    <div className="space-y-4 max-w-4xl">
      <section className={cardClass}>
        <h4 className={sectionHeaderClass}>Encounter ({encounterCount})</h4>
        {listOrEmpty<FhirEncounter>(encounters, (encounter, idx) => (
          <div key={encounter.id || `enc-${idx}`} className="border border-gray-100 rounded p-3 text-sm">
            <div className="font-medium text-gray-800">{encounter.type || "Encounter"}</div>
            <div>Status: {encounter.status || "N/A"}</div>
            <div>Class: {encounter.class || "N/A"}</div>
            <div>Start: {formatDate(encounter.start)}</div>
            <div>End: {formatDate(encounter.end)}</div>
          </div>
        ))}
      </section>

      <section className={cardClass}>
        <h4 className={sectionHeaderClass}>Condition ({conditionCount})</h4>
        {listOrEmpty<FhirCondition>(conditions, (condition, idx) => (
          <div key={condition.id || `cond-${idx}`} className="border border-gray-100 rounded p-3 text-sm">
            <div className="font-medium text-gray-800">{condition.code || "Condition"}</div>
            <div>Code: {condition.codeValue || "N/A"}</div>
            <div>Clinical Status: {condition.clinicalStatus || "N/A"}</div>
            <div>Verification: {condition.verificationStatus || "N/A"}</div>
            <div>Onset: {formatDate(condition.onset)}</div>
          </div>
        ))}
      </section>

      <section className={cardClass}>
        <h4 className={sectionHeaderClass}>Observation ({observationCount})</h4>
        {listOrEmpty<FhirObservation>(observations, (observation, idx) => (
          <div key={observation.id || `obs-${idx}`} className="border border-gray-100 rounded p-3 text-sm">
            <div className="font-medium text-gray-800">{observation.code || "Observation"}</div>
            <div>LOINC: {observation.codeValue || "N/A"}</div>
            <div>Status: {observation.status || "N/A"}</div>
            <div>Effective: {formatDate(observation.effectiveDateTime)}</div>
            <div>
              BP: Systolic {observation.systolic ?? "N/A"} / Diastolic {observation.diastolic ?? "N/A"}
            </div>
          </div>
        ))}
      </section>

      <section className={cardClass}>
        <h4 className={sectionHeaderClass}>Procedure ({procedureCount})</h4>
        {listOrEmpty<FhirProcedure>(procedures, (procedure, idx) => (
          <div key={procedure.id || `proc-${idx}`} className="border border-gray-100 rounded p-3 text-sm">
            <div className="font-medium text-gray-800">{procedure.code || "Procedure"}</div>
            <div>Code: {procedure.codeValue || "N/A"}</div>
            <div>Status: {procedure.status || "N/A"}</div>
            <div>Performed: {formatDate(procedure.performedDateTime)}</div>
          </div>
        ))}
      </section>

      <section className={cardClass}>
        <h4 className={sectionHeaderClass}>Coverage ({coverageCount})</h4>
        {listOrEmpty<FhirCoverage>(coverages, (coverage, idx) => (
          <div key={coverage.id || `cov-${idx}`} className="border border-gray-100 rounded p-3 text-sm">
            <div className="font-medium text-gray-800">{coverage.type || "Coverage"}</div>
            <div>Status: {coverage.status || "N/A"}</div>
            <div>Payor: {(coverage.payors || []).join(", ") || "N/A"}</div>
            <div>Start: {formatDate(coverage.start)}</div>
            <div>End: {formatDate(coverage.end)}</div>
          </div>
        ))}
      </section>
    </div>
  );
};

export default FhirResourcePanel;
