import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  fetchSamplePatients,
  fetchSamplePatient,
  runLocalMeasures,
  fetchSoapNotes,
  createSoapNote,
  deleteSoapNote,
  type SamplePatientSummary,
  type SoapEntryInput,
} from "../store";
import { PatientSOAP } from "../components/PatientSOAP";

type FhirResource = Record<string, unknown> & { resourceType?: string };

interface BundleEntry {
  resource?: FhirResource;
}

interface PatientBundle {
  resourceType?: string;
  entry?: BundleEntry[];
}

interface MeasureResult {
  measureId: string;
  measureName: string;
  status: string;
  denominator?: boolean;
  numerator?: boolean;
  exclusion?: boolean;
  evaluation?: Record<string, unknown>;
  explanation?: string;
}

interface RunResult {
  patientId: string;
  measurementPeriod: { start: string; end: string };
  engine: string;
  measures: MeasureResult[];
  summary: {
    measuresEvaluated: number;
    inDenominator: number;
    inNumerator: number;
    gapsInCare: Array<{ measureId: string; measureName: string }>;
  };
  executionTimeMs: number;
}

interface SoapRoundsState {
  rounds: Record<string, Array<SoapEntryInput & { id?: string; createdAt?: string }>>;
  count: number;
}

const ROLES = ["physician", "nurse", "case-worker", "patient"] as const;
const EMPTY_SOAP: SoapEntryInput = {
  role: "physician",
  subjective: "",
  objective: "",
  assessment: "",
  plan: "",
  encounterId: "",
  author: "",
};

function statusBadgeClass(status: string): string {
  switch (status) {
    case "meets-measure":
      return "bg-green-100 text-green-800 border-green-300";
    case "does-not-meet-measure":
      return "bg-red-100 text-red-700 border-red-300";
    case "excluded":
      return "bg-yellow-100 text-yellow-800 border-yellow-300";
    case "not-in-denominator":
      return "bg-gray-100 text-gray-600 border-gray-300";
    default:
      return "bg-blue-100 text-blue-800 border-blue-300";
  }
}

function resourceList(bundle: PatientBundle | null, type: string): FhirResource[] {
  if (!bundle?.entry) return [];
  return bundle.entry
    .map((e) => e.resource)
    .filter((r): r is FhirResource => Boolean(r) && r?.resourceType === type);
}

function readCoding(codable: unknown): string {
  if (!codable || typeof codable !== "object") return "";
  const c = codable as { text?: string; coding?: Array<{ display?: string; code?: string }> };
  if (c.coding && c.coding.length) {
    return c.coding[0].display || c.coding[0].code || "";
  }
  return c.text || "";
}

const PatientsPage: React.FC = () => {
  const [samples, setSamples] = useState<SamplePatientSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [bundle, setBundle] = useState<PatientBundle | null>(null);
  const [summary, setSummary] = useState<SamplePatientSummary | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string>("");

  const [soap, setSoap] = useState<SoapRoundsState | null>(null);
  const [soapDraft, setSoapDraft] = useState<SoapEntryInput>(EMPTY_SOAP);
  const [soapRound, setSoapRound] = useState<number>(1);
  const [soapBusy, setSoapBusy] = useState(false);

  const [measureBusy, setMeasureBusy] = useState(false);
  const [measureResult, setMeasureResult] = useState<RunResult | null>(null);
  const [measureError, setMeasureError] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    setLoadingList(true);
    fetchSamplePatients()
      .then((data) => {
        if (cancelled) return;
        setSamples(data.samples);
        if (!selectedId && data.samples.length) {
          setSelectedId(data.samples[0].id);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load sample patients");
      })
      .finally(() => !cancelled && setLoadingList(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const loadPatient = useCallback(async (id: string) => {
    setLoadingDetail(true);
    setError("");
    setBundle(null);
    setMeasureResult(null);
    setMeasureError("");
    try {
      const detail = await fetchSamplePatient(id);
      setBundle((detail.bundle as PatientBundle) || null);
      setSummary(detail.summary);
      const notes = await fetchSoapNotes(id);
      setSoap({ rounds: notes.rounds || {}, count: notes.count || 0 });
      const existingRounds = Object.keys(notes.rounds || {}).map(Number).filter((n) => !Number.isNaN(n));
      setSoapRound(existingRounds.length ? Math.max(...existingRounds) : 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load patient detail");
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) {
      loadPatient(selectedId);
    }
  }, [selectedId, loadPatient]);

  const encounters = useMemo(() => resourceList(bundle, "Encounter"), [bundle]);
  const conditions = useMemo(() => resourceList(bundle, "Condition"), [bundle]);
  const observations = useMemo(() => resourceList(bundle, "Observation"), [bundle]);
  const procedures = useMemo(() => resourceList(bundle, "Procedure"), [bundle]);

  const handleAddSoap = async () => {
    if (!selectedId) return;
    setSoapBusy(true);
    try {
      await createSoapNote(selectedId, soapRound, soapDraft);
      const refreshed = await fetchSoapNotes(selectedId);
      setSoap({ rounds: refreshed.rounds || {}, count: refreshed.count || 0 });
      setSoapDraft({ ...EMPTY_SOAP });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save SOAP note");
    } finally {
      setSoapBusy(false);
    }
  };

  const handleDeleteSoap = async (noteId: string) => {
    if (!selectedId || !noteId) return;
    setSoapBusy(true);
    try {
      await deleteSoapNote(selectedId, noteId);
      const refreshed = await fetchSoapNotes(selectedId);
      setSoap({ rounds: refreshed.rounds || {}, count: refreshed.count || 0 });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete SOAP note");
    } finally {
      setSoapBusy(false);
    }
  };

  const handleRunMeasures = async () => {
    if (!selectedId) return;
    setMeasureBusy(true);
    setMeasureError("");
    try {
      const result = (await runLocalMeasures(selectedId)) as RunResult;
      setMeasureResult(result);
    } catch (e) {
      setMeasureError(e instanceof Error ? e.message : "Failed to run measures");
    } finally {
      setMeasureBusy(false);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <aside className="lg:col-span-1">
        <h2 className="text-lg font-semibold mb-3">Sample Patients</h2>
        {loadingList && <p className="text-sm text-gray-500">Loading…</p>}
        {!loadingList && samples.length === 0 && (
          <p className="text-sm text-gray-500">No sample patients available.</p>
        )}
        <ul className="space-y-2">
          {samples.map((s) => {
            const active = s.id === selectedId;
            return (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(s.id)}
                  className={`w-full text-left p-3 rounded border transition ${
                    active
                      ? "bg-pink-50 border-pink-400"
                      : "bg-white border-gray-200 hover:border-gray-400"
                  }`}
                >
                  <div className="font-medium">{s.patient?.name || s.id}</div>
                  <div className="text-xs text-gray-500">
                    MRN {s.patient?.mrn || s.id} · {s.patient?.gender || "—"} · DOB {s.patient?.birthDate || "—"}
                  </div>
                  <div className="text-xs text-gray-500 mt-1">
                    {s.counts.encounters} encounters · {s.counts.conditions} conditions · {s.counts.observations} observations
                  </div>
                  {s.primaryMeasures.length > 0 && (
                    <div className="text-[11px] text-pink-700 mt-1">
                      Primary: {s.primaryMeasures.join(", ")}
                    </div>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </aside>

      <section className="lg:col-span-2 space-y-6">
        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">{error}</div>
        )}
        {loadingDetail && <p className="text-sm text-gray-500">Loading patient…</p>}

        {summary && (
          <header className="border-b border-gray-200 pb-3">
            <h2 className="text-xl font-semibold">{summary.patient?.name || summary.id}</h2>
            <p className="text-sm text-gray-600">
              MRN {summary.patient?.mrn} · {summary.patient?.gender} · DOB {summary.patient?.birthDate}
            </p>
          </header>
        )}

        {bundle && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="border rounded p-3">
              <h3 className="text-sm font-semibold mb-2">Encounters ({encounters.length})</h3>
              <ul className="text-xs space-y-1">
                {encounters.map((e) => {
                  const period = (e as { period?: { start?: string; end?: string } }).period || {};
                  const type = readCoding(((e as { type?: unknown[] }).type || [])[0]);
                  return (
                    <li key={String(e.id)} className="border-b last:border-b-0 py-1">
                      <span className="font-medium">{period.start || "—"}</span>
                      {period.end && period.end !== period.start ? ` → ${period.end}` : ""}
                      {type ? ` · ${type}` : ""}
                    </li>
                  );
                })}
              </ul>
            </div>

            <div className="border rounded p-3">
              <h3 className="text-sm font-semibold mb-2">Conditions ({conditions.length})</h3>
              <ul className="text-xs space-y-1">
                {conditions.map((c) => (
                  <li key={String(c.id)}>{readCoding((c as { code?: unknown }).code)}</li>
                ))}
              </ul>
              <h3 className="text-sm font-semibold mt-3 mb-2">Procedures ({procedures.length})</h3>
              <ul className="text-xs space-y-1">
                {procedures.map((p) => (
                  <li key={String(p.id)}>{readCoding((p as { code?: unknown }).code)}</li>
                ))}
              </ul>
            </div>

            <div className="border rounded p-3 md:col-span-2">
              <h3 className="text-sm font-semibold mb-2">Observations ({observations.length})</h3>
              <ul className="text-xs space-y-1">
                {observations.map((o) => {
                  const code = readCoding((o as { code?: unknown }).code);
                  const value = (o as { valueQuantity?: { value?: number; unit?: string } }).valueQuantity;
                  const when = (o as { effectiveDateTime?: string }).effectiveDateTime;
                  return (
                    <li key={String(o.id)}>
                      <span className="text-gray-500">{when || ""}</span> · {code}
                      {value?.value !== undefined ? ` = ${value.value}${value.unit ? " " + value.unit : ""}` : ""}
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        )}

        {selectedId && (
          <section className="border rounded p-3">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-base font-semibold">SOAP Notes</h3>
              <span className="text-xs text-gray-500">{soap?.count || 0} entries</span>
            </div>
            {soap && soap.count > 0 ? (
              <PatientSOAP rounds={soap.rounds as never} />
            ) : (
              <p className="text-sm text-gray-500">No SOAP notes yet. Add the first one below.</p>
            )}

            <details className="mt-4 border-t pt-3">
              <summary className="cursor-pointer text-sm font-semibold text-gray-700 hover:text-gray-900 select-none">
                Manage SOAP entries
              </summary>
              {soap && soap.count > 0 && (
                <div className="mt-3 text-xs">
                  <div className="font-semibold text-gray-600 mb-1">Existing entries</div>
                  <ul className="space-y-1">
                    {Object.entries(soap.rounds).flatMap(([round, entries]) =>
                      entries.map((entry) => (
                        <li key={`${round}-${entry.id}`} className="flex items-center justify-between">
                          <span>
                            Round {round} · {entry.role}
                          </span>
                          <button
                            type="button"
                            disabled={soapBusy || !entry.id}
                            onClick={() => handleDeleteSoap(String(entry.id))}
                            className="text-red-600 hover:underline"
                          >
                            delete
                          </button>
                        </li>
                      )),
                    )}
                  </ul>
                </div>
              )}
              <fieldset className="mt-3 space-y-2">
                <div className="flex flex-wrap gap-2 items-center text-sm">
                  <label>
                    Round
                    <input
                      type="number"
                      min={1}
                      value={soapRound}
                      onChange={(e) => setSoapRound(Math.max(1, parseInt(e.target.value || "1", 10)))}
                      className="ml-2 w-16 border rounded px-2 py-1"
                    />
                  </label>
                  <label>
                    Role
                    <select
                      value={soapDraft.role}
                      onChange={(e) => setSoapDraft({ ...soapDraft, role: e.target.value })}
                      className="ml-2 border rounded px-2 py-1"
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Encounter
                    <select
                      value={soapDraft.encounterId || ""}
                      onChange={(e) => setSoapDraft({ ...soapDraft, encounterId: e.target.value })}
                      className="ml-2 border rounded px-2 py-1"
                    >
                      <option value="">(none)</option>
                      {encounters.map((e) => (
                        <option key={String(e.id)} value={String(e.id)}>
                          {String(e.id)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {(["subjective", "objective", "assessment", "plan"] as const).map((field) => (
                  <textarea
                    key={field}
                    placeholder={field.charAt(0).toUpperCase() + field.slice(1)}
                    value={(soapDraft[field] as string) || ""}
                    onChange={(e) => setSoapDraft({ ...soapDraft, [field]: e.target.value })}
                    className="w-full border rounded px-2 py-1 text-sm"
                    rows={2}
                  />
                ))}
                <button
                  type="button"
                  onClick={handleAddSoap}
                  disabled={soapBusy}
                  className="px-3 py-1.5 bg-pink-600 text-white text-sm rounded disabled:opacity-50"
                >
                  {soapBusy ? "Saving…" : "Add entry"}
                </button>
              </fieldset>
            </details>
          </section>
        )}

        {selectedId && (
          <section className="border rounded p-3">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-base font-semibold">Run 3 Quality Measures</h3>
              <button
                type="button"
                onClick={handleRunMeasures}
                disabled={measureBusy}
                className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded disabled:opacity-50"
              >
                {measureBusy ? "Running…" : "Run CMS122 + CMS165 + ePC02"}
              </button>
            </div>
            {measureError && (
              <p className="text-sm text-red-700 mb-2">{measureError}</p>
            )}
            {measureResult && (
              <div className="space-y-2">
                <div className="text-xs text-gray-500">
                  Engine: {measureResult.engine} · Period {measureResult.measurementPeriod.start} → {measureResult.measurementPeriod.end} · {measureResult.executionTimeMs} ms
                </div>
                <div className="text-xs text-gray-700">
                  {measureResult.summary.inDenominator} in denominator · {measureResult.summary.inNumerator} in numerator · {measureResult.summary.gapsInCare.length} gaps
                </div>
                <table className="w-full text-sm border-collapse">
                  <thead>
                    <tr className="bg-gray-50 text-left text-xs uppercase text-gray-600">
                      <th className="border px-2 py-1">Measure</th>
                      <th className="border px-2 py-1">Status</th>
                      <th className="border px-2 py-1">Denominator</th>
                      <th className="border px-2 py-1">Numerator</th>
                    </tr>
                  </thead>
                  <tbody>
                    {measureResult.measures.map((m) => (
                      <tr key={m.measureId}>
                        <td className="border px-2 py-1">
                          <div className="font-medium">{m.measureId}</div>
                          <div className="text-xs text-gray-500">{m.measureName}</div>
                        </td>
                        <td className="border px-2 py-1">
                          <span className={`inline-block px-2 py-0.5 rounded border text-xs ${statusBadgeClass(m.status)}`}>
                            {m.status}
                          </span>
                        </td>
                        <td className="border px-2 py-1 text-center">{m.denominator ? "yes" : "no"}</td>
                        <td className="border px-2 py-1 text-center">{m.numerator ? "yes" : "no"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </section>
    </div>
  );
};

export default PatientsPage;
