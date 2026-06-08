import React, { useMemo, useState } from "react";
import { collectData, FhirBundle, FhirParameters } from "../../store/qualityMeasures";

interface CollectDataViewerProps {
  measureId?: string;
}

const CollectDataViewer: React.FC<CollectDataViewerProps> = ({ measureId }) => {
  const [subjectId, setSubjectId] = useState("");
  const [periodStart, setPeriodStart] = useState("2025-01-01");
  const [periodEnd, setPeriodEnd] = useState("2025-12-31");
  const [result, setResult] = useState<FhirParameters | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCollect = async () => {
    if (!measureId || !subjectId.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await collectData(measureId, subjectId.trim(), periodStart, periodEnd);
      setResult(r);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to collect data.");
    } finally {
      setLoading(false);
    }
  };

  const bundle = useMemo<FhirBundle | null>(() => {
    if (!result) return null;
    const entry = (result.parameter || []).find((p) => p.name === "resource");
    return (entry?.resource as FhirBundle) || null;
  }, [result]);

  const resourceCounts = useMemo<Record<string, number>>(() => {
    const counts: Record<string, number> = {};
    for (const e of bundle?.entry || []) {
      const type = (e.resource as { resourceType?: string })?.resourceType || "Unknown";
      counts[type] = (counts[type] || 0) + 1;
    }
    return counts;
  }, [bundle]);

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-800">
          Collect Data{" "}
          <span className="text-xs font-normal text-gray-500">
            (FHIR <code>$collect-data</code>)
          </span>
        </h3>
      </div>

      {!measureId && (
        <p className="text-sm text-gray-500">Select a measure to collect data.</p>
      )}

      {measureId && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <label className="text-sm md:col-span-1">
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
            onClick={handleCollect}
            disabled={loading || !subjectId.trim()}
            className="patient-search-button"
          >
            {loading ? "Collecting…" : "Run $collect-data"}
          </button>

          {error && <div className="text-sm text-red-600 mt-3">{error}</div>}

          {bundle && (
            <div className="mt-4 space-y-3">
              <div className="text-xs text-gray-500">
                Bundle <code>{bundle.id}</code> · type = {bundle.type} · entries ={" "}
                {bundle.entry?.length || 0}
              </div>
              <div className="flex flex-wrap gap-2">
                {Object.entries(resourceCounts).map(([t, c]) => (
                  <span
                    key={t}
                    className="inline-block text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700"
                  >
                    {t}: {c}
                  </span>
                ))}
              </div>
              <details className="text-xs text-gray-500">
                <summary className="cursor-pointer">Raw Parameters</summary>
                <pre className="mt-2 overflow-auto bg-gray-50 p-2 rounded max-h-96">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </>
      )}
    </section>
  );
};

export default CollectDataViewer;
