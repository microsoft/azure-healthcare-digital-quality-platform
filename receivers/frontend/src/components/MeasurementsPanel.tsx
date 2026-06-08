import React, { useMemo, useState } from "react";

interface MeasurementsPanelProps {
  measurementResult?: EngineMeasurementResult;
  measurementLoading?: boolean;
  measurementError?: string;
}

export interface MeasurementResult {
  mode?: string;
  measureId?: string;
  measureName?: string;
  status?: string;
  bpAvailable?: boolean;
  bpControlled?: boolean;
  systolic?: number | null;
  diastolic?: number | null;
  denominator?: boolean;
  numerator?: boolean;
  exclusion?: boolean;
  executionTimeMs?: number;
  executionSource?: string;
  explanation?: string;
  orchestratorErrors?: Array<{ url?: string; error?: string }>;
}

export interface MeasurementExecutionRecord {
  executedAtUtc?: string;
  mode?: string;
  result?: MeasurementResult;
}

export interface QualityEngineSummary {
  measuresEvaluated?: number;
  inDenominator?: number;
  controlled?: number;
  gapsInCare?: string[];
}

export interface QualityMeasureEvaluation {
  measure_id?: string;
  measure_name?: string;
  program?: string;
  in_initial_population?: boolean;
  in_denominator?: boolean;
  denominator_exclusion?: boolean;
  denominator_exclusion_reasons?: string[];
  in_numerator?: boolean;
  numerator_reasons?: string[];
  inverse_measure?: boolean;
  controlled?: boolean;
  evidence_trace?: string[];
  detail?: Record<string, unknown>;
}

export interface QualityEngineReport {
  patient_id?: string;
  measurement_period_start?: string;
  measurement_period_end?: string;
  computed_at?: string;
  measures?: QualityMeasureEvaluation[];
  summary?: {
    measures_evaluated?: number;
    in_denominator?: number;
    controlled?: number;
    gaps_in_care?: string[];
  };
}

export interface EngineMeasurementResult {
  status?: string;
  executionSource?: string;
  engines?: {
    native?: QualityEngineReport | null;
    ai?: QualityEngineReport | null;
  };
  summary?: {
    native?: QualityEngineSummary | null;
    ai?: QualityEngineSummary | null;
    combined?: QualityEngineSummary | null;
  };
  orchestratorErrors?: Array<{ url?: string; engine?: string; error?: string }>;
}

export type MeasureRowStatus = "PASS" | "FAIL" | "EXCLUDED" | "NOT-IN-MEM";

export interface MeasureRowItem {
  measure_id?: string;
  measure_name?: string;
  status: MeasureRowStatus;
  program?: string;
  in_initial_population?: boolean;
  in_denominator?: boolean;
  denominator_exclusion?: boolean;
  denominator_exclusion_reasons?: string[];
  in_numerator?: boolean;
  numerator_reasons?: string[];
  controlled?: boolean;
  inverse_measure?: boolean;
  evidence_trace?: string[];
}

const engineBadgeClass = (enabled: boolean): string => {
  return enabled
    ? "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 border border-emerald-200"
    : "inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-gray-100 text-gray-600 border border-gray-200";
};

const getMeasureStatus = (measure: {
  in_initial_population?: boolean;
  in_denominator?: boolean;
  denominator_exclusion?: boolean;
  controlled?: boolean;
}): MeasureRowStatus => {
  if (!measure.in_initial_population || !measure.in_denominator) {
    return "NOT-IN-MEM";
  }
  if (measure.denominator_exclusion) {
    return "EXCLUDED";
  }
  return measure.controlled ? "PASS" : "FAIL";
};

const sortMeasureRows = <T extends { measure_id?: string; measure_name?: string }>(rows: T[]): T[] => {
  const order = ["CMS122", "CMS165", "EPC02"];
  const rank = (row: T): number => {
    const key = `${row.measure_id || ""} ${row.measure_name || ""}`.toUpperCase().replace(/[-_\s]/g, "");
    for (let i = 0; i < order.length; i += 1) {
      if (key.includes(order[i])) {
        return i;
      }
    }
    return order.length;
  };
  return [...rows].sort((a, b) => {
    const ra = rank(a);
    const rb = rank(b);
    if (ra !== rb) {
      return ra - rb;
    }
    return (a.measure_id || a.measure_name || "").localeCompare(b.measure_id || b.measure_name || "");
  });
};

const splitMeasureRows = <T,>(rows: T[]): { primary: T[]; overflow: T[] } => {
  return {
    primary: rows.slice(0, 3),
    overflow: rows.slice(3),
  };
};

const measureBadgeClass = (status: MeasureRowStatus): string => {
  if (status === "PASS") {
    return "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-100 text-emerald-800";
  }
  if (status === "EXCLUDED") {
    return "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-700";
  }
  if (status === "NOT-IN-MEM") {
    return "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-gray-600";
  }
  return "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold bg-rose-100 text-rose-800";
};

export const MeasureRows: React.FC<{ measures: MeasureRowItem[] }> = ({ measures }) => {
  const [expandedRows, setExpandedRows] = useState<Record<string, boolean>>({});

  const measureKeys = useMemo(
    () => measures.map((measure, idx) => `${measure.measure_id || measure.measure_name || "measure"}-${idx}`),
    [measures],
  );

  if (!measures.length) {
    return <div className="text-xs text-gray-500">No measure results.</div>;
  }

  return (
    <div className="space-y-2">
      {measures.map((measure, idx) => {
        const measureName = measure.measure_name || measure.measure_id || "Measure";
        const measureId = measure.measure_id || measure.measure_name || "measure";
        const rowKey = measureKeys[idx];
        const isExpanded = Boolean(expandedRows[rowKey]);

        const toggleRow = () => {
          setExpandedRows((prev) => ({ ...prev, [rowKey]: !prev[rowKey] }));
        };

        return (
          <div key={rowKey} className="border border-gray-100 rounded px-2 py-1.5 space-y-1">
            <div className="flex items-center justify-between gap-2 text-xs">
              <span className="text-gray-700 font-medium leading-tight">{measureName}</span>
              <button
                type="button"
                onClick={toggleRow}
                className={`${measureBadgeClass(measure.status)} gap-1 hover:brightness-95`}
                aria-expanded={isExpanded}
                aria-label={`Toggle details for ${measureName}`}
              >
                <span>{measure.status}</span>
                <span className={`transition-transform duration-150 ${isExpanded ? "rotate-180" : ""}`}>▾</span>
              </button>
            </div>
            <div className="flex items-center gap-1 flex-wrap">
              <button
                type="button"
                data-measure-id={measureId}
                className="px-2 py-0.5 text-[10px] rounded-full border border-sky-200 text-sky-700 bg-sky-50 hover:bg-sky-100"
              >
                {measureId}
              </button>
            </div>
            {isExpanded && (
              <div className="mt-1 border-t border-gray-100 pt-2 text-[11px] text-gray-600 space-y-1 break-words [overflow-wrap:anywhere]">
                <div>Program: {measure.program || "N/A"}</div>
                <div>Initial Population: {String(Boolean(measure.in_initial_population))}</div>
                <div>In Denominator: {String(Boolean(measure.in_denominator))}</div>
                <div>Denominator Exclusion: {String(Boolean(measure.denominator_exclusion))}</div>
                {measure.denominator_exclusion_reasons && measure.denominator_exclusion_reasons.length > 0 && (
                  <div>Exclusion Reasons: {measure.denominator_exclusion_reasons.join("; ")}</div>
                )}
                <div>In Numerator: {String(Boolean(measure.in_numerator))}</div>
                {measure.numerator_reasons && measure.numerator_reasons.length > 0 && (
                  <div>Numerator Reasons: {measure.numerator_reasons.join("; ")}</div>
                )}
                <div>Controlled: {String(Boolean(measure.controlled))}</div>
                <div>Inverse Measure: {String(Boolean(measure.inverse_measure))}</div>
                {measure.evidence_trace && measure.evidence_trace.length > 0 && (
                  <div>Evidence: {measure.evidence_trace.join(" | ")}</div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

const MeasurementsPanel: React.FC<MeasurementsPanelProps> = ({ measurementResult, measurementLoading, measurementError }) => {
  const nativeReport = measurementResult?.engines?.native || null;
  const aiReport = measurementResult?.engines?.ai || null;
  const nativeMeasures = sortMeasureRows(nativeReport?.measures || []);
  const aiMeasures = sortMeasureRows(aiReport?.measures || []);

  const renderMeasureRows = (
    measures: Array<{
      measure_id?: string;
      measure_name?: string;
      in_initial_population?: boolean;
      in_denominator?: boolean;
      denominator_exclusion?: boolean;
      controlled?: boolean;
    }>,
  ): React.ReactNode => {
    const { primary, overflow } = splitMeasureRows(measures);
    const toMeasureRow = (measure: {
      measure_id?: string;
      measure_name?: string;
      program?: string;
      in_initial_population?: boolean;
      in_denominator?: boolean;
      denominator_exclusion?: boolean;
      denominator_exclusion_reasons?: string[];
      in_numerator?: boolean;
      numerator_reasons?: string[];
      controlled?: boolean;
      inverse_measure?: boolean;
      evidence_trace?: string[];
    }): MeasureRowItem => ({
      measure_id: measure.measure_id,
      measure_name: measure.measure_name,
      status: getMeasureStatus(measure),
      program: measure.program,
      in_initial_population: measure.in_initial_population,
      in_denominator: measure.in_denominator,
      denominator_exclusion: measure.denominator_exclusion,
      denominator_exclusion_reasons: measure.denominator_exclusion_reasons,
      in_numerator: measure.in_numerator,
      numerator_reasons: measure.numerator_reasons,
      controlled: measure.controlled,
      inverse_measure: measure.inverse_measure,
      evidence_trace: measure.evidence_trace,
    });

    return (
      <div className="space-y-2">
        <MeasureRows measures={primary.map(toMeasureRow)} />
        {overflow.length > 0 && (
          <div className="space-y-1">
            <div className="text-[11px] uppercase tracking-wide text-gray-500">More Measures ({overflow.length})</div>
            <div className="max-h-40 overflow-y-auto pr-1 space-y-1">
              <MeasureRows measures={overflow.map(toMeasureRow)} />
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-4 xl:sticky xl:top-4">
      <h4 className="text-lg font-semibold text-gray-800">Measurements</h4>

      {measurementLoading && <p className="text-sm text-gray-500">Running quality measures...</p>}
      {measurementError && <p className="text-sm text-red-600">{measurementError}</p>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 text-sm">
        <div className="border border-gray-100 rounded p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="font-semibold text-gray-700">Native CQL Engine</div>
            <span className={engineBadgeClass(Boolean(nativeReport))}>{nativeReport ? "ON" : "OFF"}</span>
          </div>
          <div>Measures Evaluated: {measurementResult?.summary?.native?.measuresEvaluated ?? 0}</div>
          <div>In Denominator: {measurementResult?.summary?.native?.inDenominator ?? 0}</div>
          <div>Controlled: {measurementResult?.summary?.native?.controlled ?? 0}</div>
          {renderMeasureRows(nativeMeasures)}
        </div>

        <div className="border border-gray-100 rounded p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="font-semibold text-gray-700">AI CQL Engine</div>
            <span className={engineBadgeClass(Boolean(aiReport))}>{aiReport ? "ON" : "OFF"}</span>
          </div>
          <div>Measures Evaluated: {measurementResult?.summary?.ai?.measuresEvaluated ?? 0}</div>
          <div>In Denominator: {measurementResult?.summary?.ai?.inDenominator ?? 0}</div>
          <div>Controlled: {measurementResult?.summary?.ai?.controlled ?? 0}</div>
          {renderMeasureRows(aiMeasures)}
        </div>
      </div>

      <div className="border-t border-gray-200" />
    </div>
  );
};

export default MeasurementsPanel;
