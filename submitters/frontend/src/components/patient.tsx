import { useState } from "react";
import { fetchPatient } from "../store";
import MemberHeader from "./MemberHeader";
import FhirResourcePanel from "./FhirResourcePanel";
import MeasurementsPanel, { EngineMeasurementResult, MeasurementExecutionRecord, MeasurementResult } from "./MeasurementsPanel";
import { runPatientMeasurement } from "../store";
import { FhirView } from "./fhirTypes";
import "./patient.css";

interface LegacyPatient {
  id?: string;
  mrn?: string;
  name?: string;
  dob?: string;
  gender?: string;
  visit_id?: string;
  admit_date?: string;
  site?: string;
  bed?: string;
  measurement_executions?: MeasurementExecutionRecord[];
}

interface PatientApiResponse {
  patient?: LegacyPatient;
  fhir?: FhirView;
  measurementPreview?: MeasurementResult;
}

export const Patient = () => {

  const showDebugInfo = false;

  const [patientData, setPatientData] = useState<PatientApiResponse | null>(null);
  const [searchId, setSearchId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showSearch, setShowSearch] = useState(true);
  const [useNativeCqlEngine, setUseNativeCqlEngine] = useState(true);
  const [useAiCqlEngine, setUseAiCqlEngine] = useState(false);
  const [measurementsLoading, setMeasurementsLoading] = useState(false);
  const [measurementsError, setMeasurementsError] = useState("");
  const [measurementResult, setMeasurementResult] = useState<EngineMeasurementResult | undefined>(undefined);

  const resolveEvaluationPatientId = (): string => {
    return (
      patientData?.patient?.mrn ||
      patientData?.patient?.id ||
      patientData?.fhir?.patient?.mrn ||
      patientData?.fhir?.patient?.id ||
      searchId.trim()
    );
  };

  const handleEvaluateRecord = async () => {
    const patientId = resolveEvaluationPatientId();
    if (!patientId) {
      setMeasurementsError("Unable to evaluate this record: member identifier is missing.");
      return;
    }
    setMeasurementsLoading(true);
    setMeasurementsError("");
    try {
      const response = await runPatientMeasurement(patientId, useNativeCqlEngine, useAiCqlEngine);
      setMeasurementResult(response.result as EngineMeasurementResult);
    } catch (e) {
      const message = e instanceof Error ? e.message : "Unable to run quality measures.";
      const failedWithServerOrNoResponse =
        /Failed to run measurement:\s*5\d\d/i.test(message) ||
        /Failed to fetch|NetworkError|Network request failed|Load failed|ERR_NETWORK|timed out|timeout/i.test(message);
      setMeasurementsError(failedWithServerOrNoResponse ? "Error evaluating member data." : message);
      setMeasurementResult(undefined);
    } finally {
      setMeasurementsLoading(false);
    }
  };

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setPatientData(null);
    if (!searchId.trim()) {
      setError("Please enter a Member Id.");
      return;
    }
    setLoading(true);
    try {
      const response = await fetchPatient(searchId.trim());
      const normalized: PatientApiResponse = response.patient
        ? {
            patient: response.patient,
            fhir: response.fhir,
            measurementPreview: response.measurementPreview,
          }
        : {
            patient: response as LegacyPatient,
          };
      setPatientData(normalized);
      setShowSearch(false); // Hide search after successful patient load
    } catch (err: unknown) {
      setPatientData(null);
      
      // Handle specific authentication errors
      const message = err instanceof Error ? err.message : "Unknown error";
      if (message.includes('Authentication')) {
        setError("Authentication failed. Please refresh the page and log in again.");
      } else if (message.includes('401')) {
        setError("Unauthorized access. Please check your permissions.");
      } else {
        setError("Member not found or error fetching member data.");
      }
      
      console.error("Error fetching member data:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSwitchPatient = () => {
    setPatientData(null);
    setSearchId('');
    setError('');
    setMeasurementResult(undefined);
    setMeasurementsError("");
    setShowSearch(true); // Show search when switching patients
  };

  return (
    <div className={`text-left patient-container ${showSearch ? "mt-10" : "mt-2"} mb-40`}>
      {showSearch && (
        <div className="px-3 lg:px-0">
          <h3 className="text-xl font-normal text-gray-600 mb-1 text-left">Member Search</h3>
          <form onSubmit={handleSearch} className="patient-search-form">
            <div className="flex flex-col space-y-2">
              <label htmlFor="patient-search" className="text-sm font-medium text-gray-700">
                Member Id
              </label>
              <div className="flex flex-col sm:flex-row space-y-2 sm:space-y-0 sm:space-x-2">
                <input
                  id="patient-search"
                  type="text"
                  placeholder="Enter Member Id"
                  value={searchId}
                  onChange={e => setSearchId(e.target.value)}
                  className="patient-search-input focus:outline-none focus:ring-2 focus:ring-blue-400 flex-1"
                />
                <button
                  type="submit"
                  className="patient-search-button w-full sm:w-auto"
                  disabled={loading}
                  style={loading ? { backgroundColor: '#1F84C6' } : undefined}
                >
                  {loading ? "Searching..." : "Search"}
                </button>
              </div>
            </div>
          </form>
          {error && <div className="text-red-500 mb-2 text-sm md:text-base">{error}</div>}
        </div>
      )}
            
      <div className="flex flex-col lg:flex-row gap-3 lg:gap-8 mb-6 lg:items-stretch">
        <div className="w-full">
          {patientData && (
            <MemberHeader
              patient={patientData.fhir?.patient}
              fallbackPatient={patientData.patient}
              onSwitchPatient={handleSwitchPatient}
              onEvaluateRecord={handleEvaluateRecord}
              evaluatingRecord={measurementsLoading}
              useNativeCqlEngine={useNativeCqlEngine}
              useAiCqlEngine={useAiCqlEngine}
              onNativeCqlEngineChange={setUseNativeCqlEngine}
              onAiCqlEngineChange={setUseAiCqlEngine}
            />
          )}
        </div>
      </div>

      {patientData && (
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
          <div className="xl:col-span-7">
            <FhirResourcePanel fhir={patientData.fhir} />
          </div>
          <div className="xl:col-span-5">
            <MeasurementsPanel
              measurementResult={measurementResult}
              measurementLoading={measurementsLoading}
              measurementError={measurementsError}
            />
          </div>
        </div>
      )}

      {/* Debug information */}
      {patientData && showDebugInfo && (
        <div className="debug-section mt-4 p-2 bg-gray-100 rounded text-xs px-3 lg:px-0">
          <details>
            <summary className="cursor-pointer">Debug: Raw Patient Data</summary>
            <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(patientData, null, 2)}</pre>
          </details>
        </div>
      )}
    </div>
  );
};

export default Patient;