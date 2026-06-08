import React, { useEffect, useState } from "react";
import "../components/patient.css";
import { fetchCapabilityStatement, CapabilityStatement, FhirMeasure } from "../store/qualityMeasures";
import MeasureCatalog from "../components/quality/MeasureCatalog";
import DataRequirementsViewer from "../components/quality/DataRequirementsViewer";
import EvaluateMeasure from "../components/quality/EvaluateMeasure";
import CollectDataViewer from "../components/quality/CollectDataViewer";
import SubmitDataForm from "../components/quality/SubmitDataForm";

type OperationTab = "data-requirements" | "evaluate" | "collect" | "submit";

const TABS: Array<{ id: OperationTab; label: string; help: string }> = [
  { id: "data-requirements", label: "Data Requirements", help: "$data-requirements" },
  { id: "evaluate", label: "Evaluate", help: "$evaluate-measure" },
  { id: "collect", label: "Collect", help: "$collect-data" },
  { id: "submit", label: "Submit", help: "$submit-data" },
];

const QualityMeasuresPage: React.FC = () => {
  const [selected, setSelected] = useState<FhirMeasure | undefined>(undefined);
  const [tab, setTab] = useState<OperationTab>("data-requirements");
  const [capability, setCapability] = useState<CapabilityStatement | null>(null);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchCapabilityStatement()
      .then((c) => {
        if (!cancelled) setCapability(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setCapabilityError(e instanceof Error ? e.message : "Failed to load capability.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="text-left mb-40">
      <div className="px-3 lg:px-0">
        <h2 className="text-xl font-normal text-gray-700 mb-1">
          Quality Measures Workbench
        </h2>
        <p className="text-sm text-gray-500 mb-4">
          FHIR R4 quality-measure operations (Da Vinci DEQM) exposed by the
          accelerator backend at <code>/fhir</code>.
        </p>

        {capability && (
          <div className="mb-4 text-xs text-gray-500">
            Connected to <strong>{capability.publisher || "Quality Measures Producer"}</strong> · FHIR{" "}
            {capability.fhirVersion} · status {capability.status}
          </div>
        )}
        {capabilityError && (
          <div className="mb-4 text-sm text-amber-700">
            Unable to reach quality-measures server: {capabilityError}
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <aside className="xl:col-span-4">
          <MeasureCatalog
            selectedMeasureId={selected?.id}
            onSelect={(m) => setSelected(m)}
          />
        </aside>

        <div className="xl:col-span-8 space-y-4">
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            {selected ? (
              <div className="flex items-baseline justify-between">
                <div>
                  <div className="text-lg font-semibold text-gray-900">
                    {selected.title || selected.id}
                  </div>
                  <div className="text-xs text-gray-500">
                    {selected.id} · v{selected.version || "?"}
                  </div>
                </div>
              </div>
            ) : (
              <p className="text-sm text-gray-500">
                Select a measure from the catalog to begin.
              </p>
            )}
          </div>

          <nav className="flex flex-wrap gap-2" aria-label="Quality-measure operations">
            {TABS.map((t) => {
              const isActive = t.id === tab;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className={`px-3 py-1.5 text-sm rounded border transition ${
                    isActive
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
                  }`}
                  title={t.help}
                >
                  {t.label}
                  <span
                    className={`ml-2 text-xs ${
                      isActive ? "text-blue-100" : "text-gray-500"
                    }`}
                  >
                    {t.help}
                  </span>
                </button>
              );
            })}
          </nav>

          {tab === "data-requirements" && (
            <DataRequirementsViewer measureId={selected?.id} />
          )}
          {tab === "evaluate" && <EvaluateMeasure measureId={selected?.id} />}
          {tab === "collect" && <CollectDataViewer measureId={selected?.id} />}
          {tab === "submit" && <SubmitDataForm measureId={selected?.id} />}
        </div>
      </div>
    </div>
  );
};

export default QualityMeasuresPage;
