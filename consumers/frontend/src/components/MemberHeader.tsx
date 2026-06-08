import React from "react";

interface FhirPatient {
  id?: string;
  mrn?: string;
  name?: string;
  gender?: string;
  birthDate?: string;
  age?: number;
}

interface LegacyPatient {
  id?: string;
  mrn?: string;
  name?: string;
  gender?: string;
  dob?: string;
  admit_date?: string;
  site?: string;
  bed?: string;
}

interface MemberHeaderProps {
  patient?: FhirPatient;
  fallbackPatient?: LegacyPatient;
  onSwitchPatient?: () => void;
  onEvaluateRecord?: () => void;
  evaluatingRecord?: boolean;
  useNativeCqlEngine: boolean;
  useAiCqlEngine: boolean;
  onNativeCqlEngineChange: (value: boolean) => void;
  onAiCqlEngineChange: (value: boolean) => void;
}

const calculateLegacyAge = (dob: string | undefined): string => {
  if (!dob || dob.length !== 8) {
    return "N/A";
  }

  try {
    const year = parseInt(dob.substring(0, 4), 10);
    const month = parseInt(dob.substring(4, 6), 10) - 1;
    const day = parseInt(dob.substring(6, 8), 10);
    const birthDate = new Date(year, month, day);

    const today = new Date();
    let age = today.getFullYear() - birthDate.getFullYear();
    const monthDiff = today.getMonth() - birthDate.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) {
      age -= 1;
    }
    return `${age}`;
  } catch {
    return "N/A";
  }
};

const formatDisplayDate = (dateStr: string | undefined): string => {
  if (!dateStr) {
    return "N/A";
  }

  if (dateStr.length === 8) {
    return `${dateStr.substring(0, 4)}-${dateStr.substring(4, 6)}-${dateStr.substring(6, 8)}`;
  }

  return dateStr;
};

const MemberHeader: React.FC<MemberHeaderProps> = ({
  patient,
  fallbackPatient,
  onSwitchPatient,
  onEvaluateRecord,
  evaluatingRecord,
  useNativeCqlEngine,
  useAiCqlEngine,
  onNativeCqlEngineChange,
  onAiCqlEngineChange,
}) => {
  const name = patient?.name || fallbackPatient?.name || "Patient";
  const gender = patient?.gender || fallbackPatient?.gender || "unknown";
  const mrn = patient?.mrn || fallbackPatient?.mrn || "N/A";
  const patientId = patient?.id || fallbackPatient?.id || "N/A";
  const birthDate = patient?.birthDate || formatDisplayDate(fallbackPatient?.dob);
  const age = patient?.age ?? parseInt(calculateLegacyAge(fallbackPatient?.dob), 10);

  return (
    <div className="bg-gray-100 rounded-lg p-3">
      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-xl font-semibold text-gray-800">{name}</h3>

          <div className="mt-1 grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-1 items-baseline">
            <p className="text-sm text-gray-600 whitespace-nowrap">
              <span className="font-semibold text-gray-800">Member Id:</span> {mrn}
            </p>
            <p className="text-sm text-gray-700 whitespace-nowrap">
              <span className="font-semibold">Gender:</span> {String(gender)}
            </p>

            <p className="text-sm text-gray-600">
              <span className="font-semibold text-gray-800">Patient Id:</span> {patientId}
            </p>
            <p className="text-sm text-gray-700">
              <span className="font-semibold">DOB:</span> {birthDate || "N/A"}
            </p>

            <div className="hidden sm:block" />
            <p className="text-sm text-gray-700">
              <span className="font-semibold">Age:</span> {Number.isFinite(age) ? age : "N/A"}
            </p>
          </div>
        </div>

        <div className="flex flex-col items-end gap-2 lg:min-w-[220px]">
          <label className="inline-flex items-center gap-2 text-xs text-gray-700">
            <span>Native CQL Engine</span>
            <input
              type="checkbox"
              checked={useNativeCqlEngine}
              onChange={(e) => onNativeCqlEngineChange(e.target.checked)}
            />
          </label>
          <label className="inline-flex items-center gap-2 text-xs text-gray-700">
            <span>AI CQL Engine</span>
            <input
              type="checkbox"
              checked={useAiCqlEngine}
              onChange={(e) => onAiCqlEngineChange(e.target.checked)}
            />
          </label>
          <button
            onClick={onEvaluateRecord}
            disabled={evaluatingRecord}
            className="text-sm px-3 py-2 rounded-md bg-sky-500 text-white border border-sky-600 hover:bg-sky-600 disabled:opacity-60"
          >
            {evaluatingRecord ? "Evaluating..." : "Evaluate Record"}
          </button>
          <button
            onClick={onSwitchPatient}
            className="text-sm px-3 py-2 rounded-md bg-white border border-gray-300 hover:bg-gray-50 mt-2"
          >
            Switch Patient
          </button>
        </div>
      </div>

      {(fallbackPatient?.site || fallbackPatient?.bed || fallbackPatient?.admit_date) && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-2 mt-2 text-sm text-gray-600">
          <div>Hospital: {fallbackPatient?.site || "N/A"}</div>
          <div>Bed: {fallbackPatient?.bed || "N/A"}</div>
          <div>Admitted: {fallbackPatient?.admit_date || "N/A"}</div>
        </div>
      )}
    </div>
  );
};

export default MemberHeader;
