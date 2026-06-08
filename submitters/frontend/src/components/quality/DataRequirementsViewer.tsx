import React, { useState } from "react";
import { fetchDataRequirements, FhirDataRequirement, FhirLibrary } from "../../store/qualityMeasures";

interface DataRequirementsViewerProps {
  measureId?: string;
}

const DataRequirementsViewer: React.FC<DataRequirementsViewerProps> = ({ measureId }) => {
  const [library, setLibrary] = useState<FhirLibrary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [periodStart, setPeriodStart] = useState("2025-01-01");
  const [periodEnd, setPeriodEnd] = useState("2025-12-31");

  const handleLoad = async () => {
    if (!measureId) return;
    setLoading(true);
    setError(null);
    setLibrary(null);
    try {
      const result = await fetchDataRequirements(measureId, periodStart, periodEnd);
      setLibrary(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load data requirements.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-800">
          Data Requirements{" "}
          <span className="text-xs font-normal text-gray-500">
            (FHIR <code>$data-requirements</code>)
          </span>
        </h3>
      </div>

      {!measureId && (
        <p className="text-sm text-gray-500">Select a measure to view its data requirements.</p>
      )}

      {measureId && (
        <>
          <div className="flex flex-wrap items-end gap-3 mb-4">
            <label className="text-sm">
              <span className="block text-gray-700">Period start</span>
              <input
                type="date"
                value={periodStart}
                onChange={(e) => setPeriodStart(e.target.value)}
                className="mt-1 border border-gray-300 rounded px-2 py-1 text-sm"
              />
            </label>
            <label className="text-sm">
              <span className="block text-gray-700">Period end</span>
              <input
                type="date"
                value={periodEnd}
                onChange={(e) => setPeriodEnd(e.target.value)}
                className="mt-1 border border-gray-300 rounded px-2 py-1 text-sm"
              />
            </label>
            <button
              type="button"
              onClick={handleLoad}
              disabled={loading}
              className="patient-search-button"
            >
              {loading ? "Loading…" : "Gather Data Requirements"}
            </button>
          </div>

          {error && <div className="text-sm text-red-600 mb-2">{error}</div>}

          {library && (
            <div className="space-y-4">
              <div className="text-xs text-gray-500">
                Library <code>{library.id}</code> · v{library.version || "?"} ·{" "}
                <span>type = {library.type?.coding?.[0]?.code || "n/a"}</span>
              </div>
              <DataRequirementList requirements={library.dataRequirement || []} />
              <details className="text-xs text-gray-500">
                <summary className="cursor-pointer">Raw FHIR Library</summary>
                <pre className="mt-2 overflow-auto bg-gray-50 p-2 rounded">
                  {JSON.stringify(library, null, 2)}
                </pre>
              </details>
            </div>
          )}
        </>
      )}
    </section>
  );
};

const DataRequirementList: React.FC<{ requirements: FhirDataRequirement[] }> = ({
  requirements,
}) => {
  if (requirements.length === 0) {
    return <p className="text-sm text-gray-500">No data requirements returned.</p>;
  }
  return (
    <table className="min-w-full text-sm">
      <thead>
        <tr className="text-left border-b border-gray-200">
          <th className="py-2 pr-3">Resource</th>
          <th className="py-2 pr-3">Profile</th>
          <th className="py-2 pr-3">Must Support</th>
          <th className="py-2">Code Filters</th>
        </tr>
      </thead>
      <tbody>
        {requirements.map((r, idx) => (
          <tr key={idx} className="border-b border-gray-100 align-top">
            <td className="py-2 pr-3 font-medium text-gray-900">{r.type}</td>
            <td className="py-2 pr-3 text-xs text-gray-600 break-all">
              {(r.profile || []).map((p) => (
                <div key={p}>{p}</div>
              ))}
            </td>
            <td className="py-2 pr-3 text-xs text-gray-600">
              {(r.mustSupport || []).join(", ") || "—"}
            </td>
            <td className="py-2 text-xs text-gray-600">
              {(r.codeFilter || []).length === 0 && "—"}
              {(r.codeFilter || []).map((cf, cfIdx) => (
                <div key={cfIdx} className="mb-1">
                  <span className="font-mono">{cf.path || cf.searchParam}</span>
                  {cf.valueSet && (
                    <>
                      {" "}
                      ∈ <span className="font-mono break-all">{cf.valueSet}</span>
                    </>
                  )}
                  {cf.code && cf.code.length > 0 && (
                    <div>
                      codes:{" "}
                      {cf.code.map((c, i) => (
                        <span
                          key={i}
                          className="inline-block text-xs px-1.5 py-0.5 mr-1 bg-gray-100 rounded font-mono"
                        >
                          {c.code}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

export default DataRequirementsViewer;
