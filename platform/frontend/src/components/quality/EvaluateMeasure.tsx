import React, { useState } from "react";
import {
  evaluateMeasure,
  FhirMeasureReport,
  FhirMeasureReportPopulation,
} from "../../store/qualityMeasures";

interface EvaluateMeasureProps {
  measureId?: string;
}

const EvaluateMeasure: React.FC<EvaluateMeasureProps> = ({ measureId }) => {
  const [subjectId, setSubjectId] = useState("");
  const [periodStart, setPeriodStart] = useState("2025-01-01");
  const [periodEnd, setPeriodEnd] = useState("2025-12-31");
  const [engine, setEngine] = useState<"native-cql" | "ai-cql">("native-cql");
  const [report, setReport] = useState<FhirMeasureReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleEvaluate = async () => {
    if (!measureId || !subjectId.trim()) return;
    setLoading(true);
    setError(null);
    setReport(null);
    try {
      const r = await evaluateMeasure(
        measureId,
        subjectId.trim(),
        periodStart,
        periodEnd,
        engine,
      );
      setReport(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to evaluate measure.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-800">
          Evaluate Measure{" "}
          <span className="text-xs font-normal text-gray-500">
            (FHIR <code>$evaluate-measure</code>)
          </span>
        </h3>
      </div>

      {!measureId && (
        <p className="text-sm text-gray-500">Select a measure to evaluate.</p>
      )}

      {measureId && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-4">
            <label className="text-sm">
              <span className="block text-gray-700">Subject (Patient id)</span>
              <input
                type="text"
                value={subjectId}
                onChange={(e) => setSubjectId(e.target.value)}
                placeholder="e.g. p-cms165-001"
                className="mt-1 w-full border border-gray-300 rounded px-2 py-1 text-sm"
              />
            </label>
            <label className="text-sm">
              <span className="block text-gray-700">Engine</span>
              <select
                value={engine}
                onChange={(e) => setEngine(e.target.value as "native-cql" | "ai-cql")}
                className="mt-1 w-full border border-gray-300 rounded px-2 py-1 text-sm"
              >
                <option value="native-cql">Native CQL</option>
                <option value="ai-cql">AI CQL</option>
              </select>
            </label>
            <label className="text-sm">
              <span className="block text-gray-700">Period start</span>
              <input
                type="date"
                value={periodStart}
                onChange={(e) => setPeriodStart(e.target.value)}
                className="mt-1 w-full border border-gray-300 rounded px-2 py-1 text-sm"
              />
            </label>
            <label className="text-sm">
              <span className="block text-gray-700">Period end</span>
              <input
                type="date"
                value={periodEnd}
                onChange={(e) => setPeriodEnd(e.target.value)}
                className="mt-1 w-full border border-gray-300 rounded px-2 py-1 text-sm"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={handleEvaluate}
            disabled={loading || !subjectId.trim()}
            className="patient-search-button"
          >
            {loading ? "Evaluating…" : "Run $evaluate-measure"}
          </button>

          {error && <div className="text-sm text-red-600 mt-3">{error}</div>}

          {report && (
            <div className="mt-4 space-y-3">
              <div className="text-xs text-gray-500">
                MeasureReport <code>{report.id}</code> · status ={" "}
                <strong>{report.status}</strong> · type ={" "}
                <strong>{report.type}</strong>
              </div>
              <PopulationTable groups={report.group || []} />
              {report.extension && report.extension.length > 0 && (
                <details className="text-xs text-gray-500">
                  <summary className="cursor-pointer">Extensions</summary>
                  <ul className="list-disc ml-5 mt-1">
                    {report.extension.map((ext, idx) => (
                      <li key={idx}>
                        <span className="font-mono">{ext.url}</span>: {ext.valueString}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <details className="text-xs text-gray-500">
                <summary className="cursor-pointer">Raw MeasureReport</summary>
                <pre className="mt-2 overflow-auto bg-gray-50 p-2 rounded">
                  {JSON.stringify(report, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </>
      )}
    </section>
  );
};

const PopulationTable: React.FC<{
  groups: NonNullable<FhirMeasureReport["group"]>;
}> = ({ groups }) => {
  const populations: FhirMeasureReportPopulation[] = groups.flatMap(
    (g) => g.population || [],
  );
  if (populations.length === 0) {
    return <p className="text-sm text-gray-500">No populations reported.</p>;
  }
  return (
    <table className="min-w-full text-sm">
      <thead>
        <tr className="text-left border-b border-gray-200">
          <th className="py-1 pr-3">Population</th>
          <th className="py-1">Count</th>
        </tr>
      </thead>
      <tbody>
        {populations.map((p, idx) => (
          <tr key={idx} className="border-b border-gray-100">
            <td className="py-1 pr-3 font-mono text-xs">
              {p.code?.coding?.[0]?.code || "—"}
            </td>
            <td className="py-1">{p.count ?? 0}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

export default EvaluateMeasure;
