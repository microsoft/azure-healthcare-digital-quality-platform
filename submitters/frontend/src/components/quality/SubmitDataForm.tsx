import React, { useState } from "react";
import { submitData, FhirParameters } from "../../store/qualityMeasures";

interface SubmitDataFormProps {
  measureId?: string;
}

const samplePayload = (measureId?: string): string =>
  JSON.stringify(
    {
      resourceType: "Parameters",
      parameter: [
        {
          name: "measureReport",
          resource: {
            resourceType: "MeasureReport",
            status: "complete",
            type: "data-collection",
            measure: `Measure/${measureId || "CMS165v9"}`,
            subject: { reference: "Patient/p-cms165-001" },
            date: new Date().toISOString(),
            period: { start: "2025-01-01", end: "2025-12-31" },
          },
        },
        {
          name: "resource",
          resource: {
            resourceType: "Bundle",
            type: "collection",
            entry: [],
          },
        },
      ],
    },
    null,
    2,
  );

const SubmitDataForm: React.FC<SubmitDataFormProps> = ({ measureId }) => {
  const [payload, setPayload] = useState<string>(samplePayload(measureId));
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ location?: string; diagnostics?: string } | null>(null);

  const handleSubmit = async () => {
    if (!measureId) return;
    setLoading(true);
    setError(null);
    setResult(null);
    let parsed: FhirParameters;
    try {
      parsed = JSON.parse(payload) as FhirParameters;
    } catch {
      setError("Payload is not valid JSON.");
      setLoading(false);
      return;
    }
    if (parsed?.resourceType !== "Parameters") {
      setError("Payload resourceType must be 'Parameters'.");
      setLoading(false);
      return;
    }
    try {
      const r = await submitData(measureId, parsed);
      setResult({
        location: r.location,
        diagnostics: r.outcome?.issue?.[0]?.diagnostics,
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to submit data.");
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    setPayload(samplePayload(measureId));
    setError(null);
    setResult(null);
  };

  return (
    <section className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold text-gray-800">
          Submit Data{" "}
          <span className="text-xs font-normal text-gray-500">
            (FHIR <code>$submit-data</code>)
          </span>
        </h3>
      </div>

      {!measureId && (
        <p className="text-sm text-gray-500">Select a measure before submitting data.</p>
      )}

      {measureId && (
        <>
          <label className="block text-sm text-gray-700 mb-1">
            FHIR <code>Parameters</code> payload
          </label>
          <textarea
            value={payload}
            onChange={(e) => setPayload(e.target.value)}
            className="w-full border border-gray-300 rounded p-2 font-mono text-xs min-h-[240px]"
            spellCheck={false}
          />
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={loading}
              className="patient-search-button"
            >
              {loading ? "Submitting…" : "Run $submit-data"}
            </button>
            <button
              type="button"
              onClick={handleReset}
              className="text-sm px-3 py-1 border border-gray-300 rounded"
            >
              Reset
            </button>
          </div>

          {error && <div className="text-sm text-red-600 mt-3">{error}</div>}

          {result && (
            <div className="mt-3 text-sm text-green-700">
              <div>Submission accepted.</div>
              {result.location && (
                <div>
                  Location:{" "}
                  <span className="font-mono break-all">{result.location}</span>
                </div>
              )}
              {result.diagnostics && <div>{result.diagnostics}</div>}
            </div>
          )}
        </>
      )}
    </section>
  );
};

export default SubmitDataForm;
