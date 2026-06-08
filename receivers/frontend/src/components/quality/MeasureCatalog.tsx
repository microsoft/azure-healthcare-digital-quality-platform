import React, { useEffect, useMemo, useState } from "react";
import { listMeasures, FhirMeasure } from "../../store/qualityMeasures";

interface MeasureCatalogProps {
  selectedMeasureId?: string;
  onSelect: (measure: FhirMeasure) => void;
}

const MeasureCatalog: React.FC<MeasureCatalogProps> = ({ selectedMeasureId, onSelect }) => {
  const [measures, setMeasures] = useState<FhirMeasure[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    listMeasures()
      .then((data) => {
        if (!cancelled) setMeasures(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load measures.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sorted = useMemo(
    () => (measures ? [...measures].sort((a, b) => (a.id || "").localeCompare(b.id || "")) : []),
    [measures],
  );

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-800">Measure Catalog</h3>
        {loading && <span className="text-xs text-gray-500">Loading…</span>}
      </div>
      {error && <div className="text-sm text-red-600 mb-2">{error}</div>}
      {!loading && !error && sorted.length === 0 && (
        <p className="text-sm text-gray-500">No measures available.</p>
      )}
      <ul className="space-y-2">
        {sorted.map((m) => {
          const isSelected = m.id === selectedMeasureId;
          return (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => onSelect(m)}
                className={`w-full text-left border rounded p-3 transition ${
                  isSelected
                    ? "border-blue-500 bg-blue-50"
                    : "border-gray-200 hover:border-gray-400"
                }`}
              >
                <div className="flex items-baseline justify-between">
                  <span className="font-medium text-gray-900">{m.title || m.id}</span>
                  <span className="text-xs text-gray-500 ml-2">v{m.version || "?"}</span>
                </div>
                <div className="text-xs text-gray-500 mt-1">{m.id}</div>
                {m.description && (
                  <p className="text-sm text-gray-600 mt-2 line-clamp-3">{m.description}</p>
                )}
                {m.topic && m.topic.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {m.topic.map((t, idx) => (
                      <span
                        key={idx}
                        className="inline-block text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-700"
                      >
                        {t.text || t.coding?.[0]?.display || t.coding?.[0]?.code}
                      </span>
                    ))}
                  </div>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
};

export default MeasureCatalog;
