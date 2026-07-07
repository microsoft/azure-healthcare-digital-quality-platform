import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  WorkbenchAgency,
  WorkbenchCohort,
  WorkbenchMeasure,
  WorkbenchMember,
  WorkbenchProgram,
  WorkbenchSubmission,
  WorkbenchTag,
  MeasureSummary,
  DeqmMeasureReportDoc,
  deleteWorkbenchCohort,
  exportCohortGroup,
  importCohortGroup,
  listMeasureReports,
  listMeasureSummaries,
  listWorkbenchAgencies,
  listWorkbenchCohorts,
  listWorkbenchMeasures,
  listWorkbenchMembers,
  listWorkbenchSubmissions,
  listWorkbenchTags,
  readableTextOn,
  submitWorkbenchData,
  updateCohortMembers,
  upsertWorkbenchCohort,
} from "../store/workbench";
import {
  FhirMeasureReport,
  evaluateMeasure,
} from "../store/qualityMeasures";
import CohortChatPanel from "../components/CohortChatPanel";

type CohortPanel = "members" | "history" | "surveillance" | "evaluate" | "submit";

const PANEL_LABELS: Array<{ id: CohortPanel; label: string; help: string }> = [
  { id: "members", label: "Members", help: "Tag patients into the cohort" },
  { id: "history", label: "Evaluation History", help: "Cohort numerator/denominator roll-ups received from submitters" },
];

function ensureArray<T>(v: T[] | undefined | null): T[] {
  return Array.isArray(v) ? v : [];
}

// Extract measure-population counts from a FHIR MeasureReport resource.
function reportPopulationCounts(resource: DeqmMeasureReportDoc["resource"]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const g of ensureArray(resource?.group)) {
    for (const p of ensureArray(g?.population)) {
      const code = p?.code?.coding?.[0]?.code;
      if (code) counts[code] = typeof p.count === "number" ? p.count : 0;
    }
  }
  return counts;
}

// "https://.../Measure/CMS122v11|11.0.0" -> "CMS122v11 (v11.0.0)"
function measureLabelFromCanonical(measure?: string): string {
  if (!measure) return "—";
  const [canonical, version] = measure.split("|");
  const id = canonical.split("/").pop() || canonical;
  return version ? `${id} (v${version})` : id;
}

// A DEQM report belongs to a cohort when its subject references the cohort's
// Group (summary / subject-list) or one of the cohort members (individual).
function reportBelongsToCohort(doc: DeqmMeasureReportDoc, cohort: WorkbenchCohort): boolean {
  const ref = doc.resource?.subject?.reference || "";
  if (ref.includes(cohort.id)) return true;
  const memberIds = ensureArray(cohort.memberIds);
  return memberIds.some((m) => ref.endsWith(`/${m}`) || ref === m);
}

const REPORT_TYPE_BADGE: Record<string, string> = {
  summary: "bg-purple-100 text-purple-800 border-purple-200",
  "subject-list": "bg-amber-100 text-amber-800 border-amber-200",
  individual: "bg-sky-100 text-sky-800 border-sky-200",
};

// CMS quality measures report retrospectively (e.g., 2025 results are
// reported in 2026), so the most common evaluation period is the previous
// completed calendar year. Defaulting there also aligns with the seeded
// sample patients, which carry 2025 encounter dates.
function defaultPeriodStart(): string {
  return `${new Date().getFullYear() - 1}-01-01`;
}

function defaultPeriodEnd(): string {
  return `${new Date().getFullYear() - 1}-12-31`;
}

interface MeasureRunResult {
  memberId: string;
  measureId: string;
  status?: string;
  numerator?: number;
  denominator?: number;
  populationExclusion?: number;
  note?: string;
  evidenceTrace?: string[];
  gapsInCare?: unknown[];
  raw?: FhirMeasureReport;
  error?: string;
}

type ReportSummary = Pick<
  MeasureRunResult,
  "status" | "numerator" | "denominator" | "populationExclusion" | "note" | "evidenceTrace" | "gapsInCare"
>;

function extractExtensionString(
  extensions: FhirMeasureReport["extension"],
  suffix: string,
): string | undefined {
  if (!extensions) return undefined;
  const hit = extensions.find((e) => typeof e.url === "string" && e.url.endsWith(suffix));
  return hit?.valueString;
}

function safeParseJson<T>(value: string | undefined): T | undefined {
  if (!value) return undefined;
  try {
    return JSON.parse(value) as T;
  } catch {
    return undefined;
  }
}

function deriveFallbackNote(s: ReportSummary): string {
  const denom = s.denominator;
  const num = s.numerator;
  if (typeof denom !== "number") return "";
  if (denom === 0) return "Excluded — did not meet initial population / denominator criteria.";
  if (typeof num === "number" && num > 0) return "In numerator — measure satisfied.";
  return "In denominator, not in numerator — gap in care.";
}

function summarizeReport(report: FhirMeasureReport): ReportSummary {
  const summary: ReportSummary = { status: report.status || "unknown" };
  const group = report.group?.[0];
  if (group?.population) {
    for (const pop of group.population) {
      const code = pop.code?.coding?.[0]?.code || pop.code?.text;
      if (code === "numerator") summary.numerator = pop.count;
      else if (code === "denominator") summary.denominator = pop.count;
      else if (code === "denominator-exclusion" || code === "exception")
        summary.populationExclusion = pop.count;
    }
  }
  summary.note = extractExtensionString(report.extension, "/StructureDefinition/evaluation-note");
  summary.evidenceTrace = safeParseJson<string[]>(
    extractExtensionString(report.extension, "/StructureDefinition/evidence-trace"),
  );
  summary.gapsInCare = safeParseJson<unknown[]>(
    extractExtensionString(report.extension, "/StructureDefinition/gaps-in-care"),
  );
  if (!summary.note) summary.note = deriveFallbackNote(summary);
  return summary;
}

// ---------------------------------------------------------------------------
// Inline new-cohort form (sidebar)
// ---------------------------------------------------------------------------

interface NewCohortFormProps {
  tags: WorkbenchTag[];
  measures: WorkbenchMeasure[];
  busy: boolean;
  onCancel: () => void;
  onCreate: (draft: Partial<WorkbenchCohort>) => Promise<void>;
}

const NewCohortForm: React.FC<NewCohortFormProps> = ({
  tags,
  measures,
  busy,
  onCancel,
  onCreate,
}) => {
  const [draft, setDraft] = useState<Partial<WorkbenchCohort>>({
    name: "",
    description: "",
    tags: [],
    measureIds: [],
    memberIds: [],
  });
  const toggleTag = (id: string) => {
    const has = ensureArray(draft.tags).includes(id);
    setDraft({
      ...draft,
      tags: has
        ? ensureArray(draft.tags).filter((t) => t !== id)
        : [...ensureArray(draft.tags), id],
    });
  };
  const toggleMeasure = (id: string) => {
    const has = ensureArray(draft.measureIds).includes(id);
    setDraft({
      ...draft,
      measureIds: has
        ? ensureArray(draft.measureIds).filter((m) => m !== id)
        : [...ensureArray(draft.measureIds), id],
    });
  };
  return (
    <div className="mb-3 p-2 border border-green-300 rounded bg-green-50/40 space-y-2">
      <input
        type="text"
        autoFocus
        value={draft.name || ""}
        onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        placeholder="Cohort name *"
        className="w-full px-2 py-1 text-sm border border-gray-300 rounded"
      />
      <textarea
        value={draft.description || ""}
        onChange={(e) => setDraft({ ...draft, description: e.target.value })}
        placeholder="Description"
        rows={2}
        className="w-full px-2 py-1 text-xs border border-gray-300 rounded"
      />
      {tags.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Tags</p>
          <div className="flex flex-wrap gap-1">
            {tags.map((t) => {
              const sel = ensureArray(draft.tags).includes(t.id);
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => toggleTag(t.id)}
                  className="text-[11px] px-1.5 py-0.5 rounded border transition"
                  style={
                    sel
                      ? { backgroundColor: t.color, color: readableTextOn(t.color), borderColor: t.color }
                      : { borderColor: t.color, color: t.color, backgroundColor: "transparent" }
                  }
                >
                  {t.name}
                </button>
              );
            })}
          </div>
        </div>
      )}
      {measures.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Measures</p>
          <div className="flex flex-wrap gap-1">
            {measures.map((m) => {
              const sel = ensureArray(draft.measureIds).includes(m.id);
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => toggleMeasure(m.id)}
                  className={`text-[11px] px-2 py-0.5 rounded border transition ${
                    sel
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
                  }`}
                >
                  {m.id}
                </button>
              );
            })}
          </div>
        </div>
      )}
      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => onCreate(draft)}
          disabled={busy || !draft.name?.trim()}
          className="px-2 py-1 text-xs rounded bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
        >
          Create
        </button>
      </div>
    </div>
  );
};

const CohortsPage: React.FC = () => {
  const navigate = useNavigate();
  const { cohortId: cohortIdFromUrl } = useParams<{ cohortId?: string }>();
  const [cohorts, setCohorts] = useState<WorkbenchCohort[]>([]);
  const [tags, setTags] = useState<WorkbenchTag[]>([]);
  const [measures, setMeasures] = useState<WorkbenchMeasure[]>([]);
  const [agencies, setAgencies] = useState<WorkbenchAgency[]>([]);
  const [submissions, setSubmissions] = useState<WorkbenchSubmission[]>([]);
  const [members, setMembers] = useState<WorkbenchMember[]>([]);
  const [selectedCohortId, setSelectedCohortId] = useState<string | null>(
    cohortIdFromUrl ?? null,
  );
  const [panel, setPanel] = useState<CohortPanel>("members");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [memberInput, setMemberInput] = useState("");
  const [busy, setBusy] = useState(false);

  // FHIR Group (Da Vinci ATR roster) import/export
  const [importGroupOpen, setImportGroupOpen] = useState(false);
  const [importGroupText, setImportGroupText] = useState("");
  const [groupBusy, setGroupBusy] = useState(false);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [groupNotice, setGroupNotice] = useState<string | null>(null);

  // cohort list filter (search + scope)
  const [cohortFilter, setCohortFilter] = useState("");
  const [cohortScope, setCohortScope] = useState<"all" | "builtin" | "custom">("all");

  // inline cohort editing
  const [addingCohort, setAddingCohort] = useState(false);
  const [editingHeader, setEditingHeader] = useState(false);
  const [headerDraft, setHeaderDraft] = useState<Partial<WorkbenchCohort>>({});

  // evaluate panel state
  const [selectedMeasureIds, setSelectedMeasureIds] = useState<string[]>([]);
  const [periodStart, setPeriodStart] = useState<string>(defaultPeriodStart());
  const [periodEnd, setPeriodEnd] = useState<string>(defaultPeriodEnd());
  const [scope, setScope] = useState<"cohort" | "member">("cohort");
  const [scopeMemberId, setScopeMemberId] = useState<string>("");
  const [engine, setEngine] = useState<"native-cql" | "ai-cql">("native-cql");
  const [results, setResults] = useState<MeasureRunResult[]>([]);
  const [evaluating, setEvaluating] = useState(false);

  // submit panel state
  const [submitAgencyId, setSubmitAgencyId] = useState<string>("");
  const [submitProgramId, setSubmitProgramId] = useState<string>("");
  const [submitNote, setSubmitNote] = useState<string>("");
  const [submitStatus, setSubmitStatus] = useState<string | null>(null);

  // evaluation-history panel state (cohort summaries received from submitters)
  const [summaries, setSummaries] = useState<MeasureSummary[]>([]);
  const [summariesLoading, setSummariesLoading] = useState(false);
  const [summariesError, setSummariesError] = useState<string | null>(null);

  // DEQM MeasureReports (FHIR) received from submitters
  const [reports, setReports] = useState<DeqmMeasureReportDoc[]>([]);
  const [reportsError, setReportsError] = useState<string | null>(null);
  const [reportTypeFilter, setReportTypeFilter] = useState<"all" | "summary" | "subject-list" | "individual">("all");

  const loadSummaries = useCallback(async () => {
    setSummariesLoading(true);
    setSummariesError(null);
    try {
      const rows = await listMeasureSummaries();
      setSummaries(rows);
    } catch (e) {
      setSummariesError(e instanceof Error ? e.message : "Failed to load evaluation history.");
      setSummaries([]);
    } finally {
      setSummariesLoading(false);
    }
  }, []);

  const loadReports = useCallback(async () => {
    setReportsError(null);
    try {
      const rows = await listMeasureReports();
      setReports(rows);
    } catch (e) {
      setReportsError(e instanceof Error ? e.message : "Failed to load DEQM reports.");
      setReports([]);
    }
  }, []);

  useEffect(() => {
    loadSummaries();
    loadReports();
  }, [loadSummaries, loadReports]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, t, m, a, s, mem] = await Promise.all([
        listWorkbenchCohorts(),
        listWorkbenchTags(),
        listWorkbenchMeasures(),
        listWorkbenchAgencies(),
        listWorkbenchSubmissions(),
        listWorkbenchMembers().catch(() => [] as WorkbenchMember[]),
      ]);
      setCohorts(c);
      setTags(t);
      setMeasures(m);
      setAgencies(a);
      setSubmissions(s);
      setMembers(mem);
      if (!selectedCohortId && !cohortIdFromUrl && c.length) {
        navigate(`/cohorts/${c[0].id}`, { replace: true });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load cohorts.");
    } finally {
      setLoading(false);
    }
  }, [selectedCohortId, cohortIdFromUrl, navigate]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (cohortIdFromUrl && cohortIdFromUrl !== selectedCohortId) {
      setSelectedCohortId(cohortIdFromUrl);
    }
  }, [cohortIdFromUrl, selectedCohortId]);

  const cohort = useMemo(
    () => cohorts.find((c) => c.id === selectedCohortId) || null,
    [cohorts, selectedCohortId],
  );

  // Filter the cohort list by the search box and the built-in/custom scope toggle.
  // Matches against name, description, id, tag ids/names, and measureIds — all
  // case-insensitive substring matches.
  const filteredCohorts = useMemo(() => {
    const q = cohortFilter.trim().toLowerCase();
    const tagNameById = new Map(tags.map((t) => [t.id.toLowerCase(), t.name.toLowerCase()]));
    return cohorts.filter((c) => {
      if (cohortScope === "builtin" && !c.builtin) return false;
      if (cohortScope === "custom" && c.builtin) return false;
      if (!q) return true;
      const haystack: string[] = [
        c.name || "",
        c.description || "",
        c.id || "",
        ...ensureArray(c.tags).flatMap((t) => {
          const tid = (t || "").toLowerCase();
          const name = tagNameById.get(tid) || "";
          return [tid, name];
        }),
        ...ensureArray(c.measureIds),
      ];
      return haystack.some((s) => s.toLowerCase().includes(q));
    });
  }, [cohorts, cohortFilter, cohortScope, tags]);

  const memberLookup = useMemo(() => {
    const map = new Map<string, WorkbenchMember>();
    for (const m of members) map.set(m.id, m);
    return map;
  }, [members]);

  // When the active cohort changes, exit any in-flight inline-edit and
  // pre-populate the measure-selection panel with the cohort's measureIds.
  useEffect(() => {
    setEditingHeader(false);
    if (cohort && Array.isArray(cohort.measureIds) && cohort.measureIds.length) {
      setSelectedMeasureIds(cohort.measureIds);
    }
  }, [cohort?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------- Cohort CRUD ----------------

  const onAddCohort = () => {
    setAddingCohort(true);
  };

  const onCancelAddCohort = () => setAddingCohort(false);

  const onCreateCohort = async (draft: Partial<WorkbenchCohort>) => {
    setBusy(true);
    try {
      const created = await upsertWorkbenchCohort({
        name: (draft.name || "").trim(),
        description: draft.description || "",
        memberIds: ensureArray(draft.memberIds),
        tags: ensureArray(draft.tags),
        measureIds: ensureArray(draft.measureIds),
      });
      await refresh();
      navigate(`/cohorts/${created.id}`);
      setAddingCohort(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create cohort.");
    } finally {
      setBusy(false);
    }
  };

  const onStartEditHeader = () => {
    if (!cohort) return;
    setHeaderDraft({
      name: cohort.name,
      description: cohort.description || "",
      tags: ensureArray(cohort.tags),
      measureIds: ensureArray(cohort.measureIds),
    });
    setEditingHeader(true);
  };

  const onCancelEditHeader = () => {
    setEditingHeader(false);
    setHeaderDraft({});
  };

  const onSaveHeader = async () => {
    if (!cohort) return;
    setBusy(true);
    try {
      const updated = await upsertWorkbenchCohort({
        ...cohort,
        name: (headerDraft.name || "").trim() || cohort.name,
        description: headerDraft.description ?? cohort.description ?? "",
        tags: ensureArray(headerDraft.tags),
        measureIds: ensureArray(headerDraft.measureIds),
      });
      setCohorts((prev) => prev.map((c) => (c.id === cohort.id ? updated : c)));
      setEditingHeader(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update cohort.");
    } finally {
      setBusy(false);
    }
  };

  const onToggleHeaderTag = (tagId: string) => {
    const has = ensureArray(headerDraft.tags).includes(tagId);
    setHeaderDraft({
      ...headerDraft,
      tags: has
        ? ensureArray(headerDraft.tags).filter((t) => t !== tagId)
        : [...ensureArray(headerDraft.tags), tagId],
    });
  };

  const onToggleHeaderMeasure = (measureId: string) => {
    const has = ensureArray(headerDraft.measureIds).includes(measureId);
    setHeaderDraft({
      ...headerDraft,
      measureIds: has
        ? ensureArray(headerDraft.measureIds).filter((m) => m !== measureId)
        : [...ensureArray(headerDraft.measureIds), measureId],
    });
  };

  const onDeleteCohort = async () => {
    if (!cohort) return;
    if (cohort.builtin) {
      setError("Built-in cohorts cannot be deleted.");
      return;
    }
    if (!window.confirm(`Delete cohort '${cohort.name}'?`)) return;
    setBusy(true);
    try {
      await deleteWorkbenchCohort(cohort.id);
      setCohorts((prev) => prev.filter((c) => c.id !== cohort.id));
      setSelectedCohortId(null);
      navigate("/cohorts");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete cohort.");
    } finally {
      setBusy(false);
    }
  };

  // ---------------- FHIR Group export / import (Da Vinci ATR roster) ----------------

  const onExportGroup = async () => {
    if (!cohort) return;
    setGroupBusy(true);
    setGroupError(null);
    setGroupNotice(null);
    try {
      const group = await exportCohortGroup(cohort.id);
      const blob = new Blob([JSON.stringify(group, null, 2)], {
        type: "application/fhir+json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${cohort.id}.group.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setGroupNotice(
        `Exported ${cohort.id}.group.json (${ensureArray(cohort.memberIds).length} members).`,
      );
    } catch (e) {
      setGroupError(e instanceof Error ? e.message : "Export failed.");
    } finally {
      setGroupBusy(false);
    }
  };

  const onImportGroup = async () => {
    setGroupBusy(true);
    setGroupError(null);
    setGroupNotice(null);
    try {
      const parsed = JSON.parse(importGroupText);
      if (!parsed || parsed.resourceType !== "Group") {
        throw new Error("JSON must be a FHIR Group resource (resourceType: 'Group').");
      }
      const res = await importCohortGroup(parsed);
      const refreshed = await listWorkbenchCohorts();
      setCohorts(refreshed);
      setSelectedCohortId(res.cohort.id);
      setImportGroupOpen(false);
      setImportGroupText("");
      setGroupNotice(
        `Imported cohort '${res.cohort.name}' (${res.memberCount} members).`,
      );
    } catch (e) {
      setGroupError(
        e instanceof Error ? e.message : "Import failed. Paste a valid FHIR Group JSON.",
      );
    } finally {
      setGroupBusy(false);
    }
  };

  const onGroupFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setImportGroupText(String(reader.result || ""));
    reader.readAsText(file);
  };
  // ---------------- Members ----------------

  const onAddMembers = async () => {
    if (!cohort) return;
    const ids = memberInput
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!ids.length) return;
    setBusy(true);
    try {
      const updated = await updateCohortMembers(cohort.id, { add: ids });
      setCohorts((prev) => prev.map((c) => (c.id === cohort.id ? updated : c)));
      setMemberInput("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add members.");
    } finally {
      setBusy(false);
    }
  };

  const onRemoveMember = async (memberId: string) => {
    if (!cohort) return;
    setBusy(true);
    try {
      const updated = await updateCohortMembers(cohort.id, { remove: [memberId] });
      setCohorts((prev) => prev.map((c) => (c.id === cohort.id ? updated : c)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove member.");
    } finally {
      setBusy(false);
    }
  };

  // ---------------- Evaluate ----------------

  const onToggleMeasureSelection = (id: string) => {
    setSelectedMeasureIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    );
  };

  const onRunEvaluation = async () => {
    if (!cohort) return;
    if (!selectedMeasureIds.length) {
      setError("Select at least one measure.");
      return;
    }
    const targets =
      scope === "cohort"
        ? ensureArray(cohort.memberIds)
        : scopeMemberId
        ? [scopeMemberId]
        : [];
    if (!targets.length) {
      setError(scope === "cohort" ? "Cohort has no members." : "Provide a member id.");
      return;
    }
    setEvaluating(true);
    setError(null);
    const next: MeasureRunResult[] = [];
    for (const measureId of selectedMeasureIds) {
      for (const memberId of targets) {
        try {
          const report = await evaluateMeasure(measureId, memberId, periodStart, periodEnd, engine);
          next.push({ measureId, memberId, raw: report, ...summarizeReport(report) });
        } catch (e) {
          next.push({
            measureId,
            memberId,
            error: e instanceof Error ? e.message : "evaluation failed",
          });
        }
      }
    }
    setResults(next);
    setEvaluating(false);
  };

  // ---------------- Submit ----------------

  const onSubmit = async () => {
    if (!cohort) return;
    if (!submitAgencyId) {
      setError("Choose a regulatory agency.");
      return;
    }
    if (!selectedMeasureIds.length) {
      setError("Select at least one measure to submit.");
      return;
    }
    setBusy(true);
    setSubmitStatus(null);
    try {
      const sub = await submitWorkbenchData({
        cohortId: cohort.id,
        agencyId: submitAgencyId,
        measureIds: selectedMeasureIds,
        note: submitNote,
      });
      setSubmitStatus(`Submission ${sub.id} queued (${sub.status}).`);
      const refreshed = await listWorkbenchSubmissions();
      setSubmissions(refreshed);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setBusy(false);
    }
  };

  // ---------------- Render ----------------

  const enabledMeasures = measures.filter((m) => m.enabled);
  const cohortSubmissions = submissions.filter((s) => cohort && s.cohortId === cohort.id);

  return (
    <div className="text-left mb-24">
      <div className="px-3 lg:px-0">
        <h2 className="text-xl font-normal text-gray-700 mb-1">Cohorts</h2>
        <p className="text-sm text-gray-500 mb-4">
          Group patients into cohorts, evaluate measures across the cohort or a single
          member, and submit to a regulatory agency. All cohort, evaluation, and
          submission state lives in <code>dq/cohorts</code>.
        </p>
      </div>

      {error && (
        <div className="my-2 p-2 text-sm rounded bg-red-50 text-red-800 border border-red-200">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        {/* Left: cohort list */}
        <aside className="xl:col-span-3 bg-white rounded-lg border border-gray-200 p-3">
          <div className="flex items-center mb-3">
            <h3 className="text-sm font-medium text-gray-700">Cohorts</h3>
            <button
              type="button"
              onClick={onAddCohort}
              className="ml-auto px-2 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700"
            >
              + New
            </button>
          </div>
          {/* Filter row: text search + built-in/custom scope */}
          <div className="mb-2 space-y-1.5">
            <div className="relative">
              <input
                type="text"
                value={cohortFilter}
                onChange={(e) => setCohortFilter(e.target.value)}
                placeholder="Filter by name, tag, or measure…"
                className="w-full text-xs border border-gray-300 rounded px-2 py-1 pr-6 focus:outline-none focus:ring-1 focus:ring-blue-400"
                aria-label="Filter cohorts"
              />
              {cohortFilter && (
                <button
                  type="button"
                  onClick={() => setCohortFilter("")}
                  className="absolute right-1 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700 text-xs px-1"
                  aria-label="Clear filter"
                  title="Clear filter"
                >
                  ×
                </button>
              )}
            </div>
            <div className="inline-flex text-[11px] rounded border border-gray-200 overflow-hidden">
              {(["all", "builtin", "custom"] as const).map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => setCohortScope(opt)}
                  className={`px-2 py-0.5 capitalize ${
                    cohortScope === opt
                      ? "bg-blue-50 text-blue-800 font-medium"
                      : "bg-white text-gray-600 hover:bg-gray-50"
                  }`}
                  aria-pressed={cohortScope === opt}
                >
                  {opt === "builtin" ? "Built-in" : opt}
                </button>
              ))}
            </div>
            <div className="text-[10px] text-gray-500">
              Showing {filteredCohorts.length} of {cohorts.length}
            </div>
          </div>
          {addingCohort && (
            <NewCohortForm
              tags={tags}
              measures={measures}
              busy={busy}
              onCancel={onCancelAddCohort}
              onCreate={onCreateCohort}
            />
          )}
          {loading && <p className="text-xs text-gray-500">Loading…</p>}
          <ul className="space-y-1">
            {filteredCohorts.map((c) => {
              const active = c.id === selectedCohortId;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => navigate(`/cohorts/${c.id}`)}
                    className={`w-full text-left px-2 py-1.5 rounded text-sm border ${
                      active
                        ? "border-blue-300 bg-blue-50 text-blue-900"
                        : "border-transparent hover:bg-gray-50 text-gray-700"
                    }`}
                  >
                    <div className="font-medium">{c.name}</div>
                    <div className="text-[11px] text-gray-500">
                      {ensureArray(c.memberIds).length} member(s)
                      {c.builtin ? " · built-in" : ""}
                    </div>
                  </button>
                </li>
              );
            })}
            {filteredCohorts.length === 0 && !addingCohort && (
              <li className="text-xs text-gray-500">
                {cohorts.length === 0
                  ? 'No cohorts yet. Click "+ New".'
                  : "No cohorts match the current filter."}
              </li>
            )}
          </ul>
        </aside>

        {/* Right: cohort detail */}
        <section className="xl:col-span-9 space-y-4">
          {!cohort ? (
            <div className="bg-white rounded-lg border border-gray-200 p-6 text-sm text-gray-500">
              Select a cohort on the left to view members, evaluate measures, or submit
              to a regulatory agency.
            </div>
          ) : (
            <>
              <div className="bg-white rounded-lg border border-gray-200 p-4">
                {!editingHeader ? (
                  <>
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <h3 className="text-lg font-semibold text-gray-900">{cohort.name}</h3>
                      <span className="text-xs text-gray-500">{cohort.id}</span>
                      {cohort.builtin && (
                        <span className="text-[10px] px-1 rounded bg-gray-100 text-gray-600">
                          built-in
                        </span>
                      )}
                      <button
                        type="button"
                        onClick={onStartEditHeader}
                        className="ml-auto px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
                        disabled={busy}
                      >
                        Edit
                      </button>
                      {!cohort.builtin && (
                        <button
                          type="button"
                          onClick={onDeleteCohort}
                          className="px-2 py-1 text-xs rounded border border-red-300 text-red-700 hover:bg-red-50"
                          disabled={busy}
                        >
                          Delete
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={onExportGroup}
                        disabled={groupBusy}
                        title="Export this cohort roster as a Da Vinci ATR FHIR Group"
                        className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500 disabled:opacity-50"
                      >
                        Export Group
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          setImportGroupOpen((v) => !v);
                          setGroupError(null);
                          setGroupNotice(null);
                        }}
                        disabled={groupBusy}
                        title="Create or update a cohort from a FHIR Group roster"
                        className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500 disabled:opacity-50"
                      >
                        Import Group
                      </button>
                    </div>
                    {groupError && (
                      <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2 mt-2">
                        {groupError}
                      </div>
                    )}
                    {groupNotice && (
                      <div className="text-xs text-green-700 bg-green-50 border border-green-200 rounded p-2 mt-2">
                        {groupNotice}
                      </div>
                    )}
                    {importGroupOpen && (
                      <div className="mt-2 border border-gray-200 rounded p-2 space-y-2 bg-gray-50">
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                          <span className="text-xs font-medium text-gray-700">
                            Import FHIR Group (Da Vinci ATR roster)
                          </span>
                          <input
                            type="file"
                            accept=".json,application/json,application/fhir+json"
                            onChange={onGroupFile}
                            className="text-xs"
                          />
                        </div>
                        <textarea
                          value={importGroupText}
                          onChange={(e) => setImportGroupText(e.target.value)}
                          placeholder={'{"resourceType":"Group", "member":[{"entity":{"reference":"Patient/P001"}}] }'}
                          rows={6}
                          className="w-full text-xs font-mono border border-gray-300 rounded p-2"
                        />
                        <div className="flex gap-2 justify-end">
                          <button
                            type="button"
                            onClick={() => {
                              setImportGroupOpen(false);
                              setImportGroupText("");
                            }}
                            className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
                          >
                            Cancel
                          </button>
                          <button
                            type="button"
                            onClick={onImportGroup}
                            disabled={groupBusy || !importGroupText.trim()}
                            className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                          >
                            {groupBusy ? "Importing\u2026" : "Import"}
                          </button>
                        </div>
                      </div>
                    )}
                    {cohort.description && (
                      <p className="text-sm text-gray-600 mt-1">{cohort.description}</p>
                    )}
                    <div className="flex flex-wrap gap-1 mt-2">
                      {ensureArray(cohort.tags)
                        .map((tid) => tags.find((t) => t.id === tid))
                        .filter((t): t is WorkbenchTag => !!t)
                        .map((t) => (
                          <span
                            key={t.id}
                            className="text-[11px] px-1.5 py-0.5 rounded font-medium"
                            style={{ backgroundColor: t.color, color: readableTextOn(t.color) }}
                          >
                            {t.name}
                          </span>
                        ))}
                      {ensureArray(cohort.tags).length === 0 && (
                        <span className="text-[11px] text-gray-500">No tags</span>
                      )}
                    </div>
                    {ensureArray(cohort.measureIds).length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        <span className="text-[11px] uppercase tracking-wide text-gray-500 mr-1">
                          Measures:
                        </span>
                        {ensureArray(cohort.measureIds).map((mid) => (
                          <span
                            key={mid}
                            className="text-[11px] px-1.5 py-0.5 rounded border border-gray-300 text-gray-700 bg-white"
                          >
                            {mid}
                          </span>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <label className="text-xs text-gray-700">
                        Name *
                        <input
                          type="text"
                          value={headerDraft.name || ""}
                          onChange={(e) => setHeaderDraft({ ...headerDraft, name: e.target.value })}
                          className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                        />
                      </label>
                      <label className="text-xs text-gray-700">
                        Identifier
                        <input
                          type="text"
                          value={cohort.id}
                          disabled
                          className="block mt-1 w-full px-2 py-1 text-sm border border-gray-200 bg-gray-50 rounded text-gray-500"
                        />
                      </label>
                      <label className="text-xs text-gray-700 md:col-span-2">
                        Description
                        <textarea
                          value={headerDraft.description || ""}
                          onChange={(e) =>
                            setHeaderDraft({ ...headerDraft, description: e.target.value })
                          }
                          className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                          rows={2}
                        />
                      </label>
                    </div>
                    <div className="text-xs text-gray-700">
                      Tags
                      <div className="flex flex-wrap gap-1 mt-1">
                        {tags.map((t) => {
                          const sel = ensureArray(headerDraft.tags).includes(t.id);
                          const fg = readableTextOn(t.color);
                          return (
                            <button
                              key={t.id}
                              type="button"
                              onClick={() => onToggleHeaderTag(t.id)}
                              className="text-[11px] px-1.5 py-0.5 rounded border transition"
                              style={
                                sel
                                  ? { backgroundColor: t.color, color: fg, borderColor: t.color }
                                  : { borderColor: t.color, color: t.color, backgroundColor: "transparent" }
                              }
                            >
                              {t.name}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <div className="text-xs text-gray-700">
                      Measures
                      <div className="flex flex-wrap gap-1 mt-1">
                        {measures.map((m) => {
                          const sel = ensureArray(headerDraft.measureIds).includes(m.id);
                          return (
                            <button
                              key={m.id}
                              type="button"
                              onClick={() => onToggleHeaderMeasure(m.id)}
                              className={`text-[11px] px-2 py-0.5 rounded border transition ${
                                sel
                                  ? "bg-blue-600 text-white border-blue-600"
                                  : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
                              }`}
                              title={m.customName || m.title || m.id}
                            >
                              {m.id}
                            </button>
                          );
                        })}
                        {measures.length === 0 && (
                          <span className="text-gray-500">No measures defined.</span>
                        )}
                      </div>
                    </div>
                    <div className="flex justify-end gap-2">
                      <button
                        type="button"
                        onClick={onCancelEditHeader}
                        className="px-3 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={onSaveHeader}
                        disabled={busy || !headerDraft.name?.trim()}
                        className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        Save
                      </button>
                    </div>
                  </div>
                )}
              </div>

              <nav className="flex flex-wrap gap-2" aria-label="Cohort panels">
                {PANEL_LABELS.map((p) => {
                  const active = panel === p.id;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => setPanel(p.id)}
                      title={p.help}
                      className={`px-3 py-1.5 text-sm rounded border transition ${
                        active
                          ? "bg-blue-600 text-white border-blue-600"
                          : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
                      }`}
                    >
                      {p.label}
                    </button>
                  );
                })}
              </nav>

              {panel === "members" && (
                <div className="bg-white rounded-lg border border-gray-200 p-4">
                  <h4 className="text-sm font-medium text-gray-700 mb-2">
                    Members ({ensureArray(cohort.memberIds).length})
                  </h4>
                  <div className="flex gap-2 mb-3">
                    <input
                      type="text"
                      value={memberInput}
                      onChange={(e) => setMemberInput(e.target.value)}
                      placeholder="Patient ids (comma or whitespace separated)"
                      className="flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded"
                    />
                    <button
                      type="button"
                      onClick={onAddMembers}
                      disabled={busy || !memberInput.trim()}
                      className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      Add
                    </button>
                  </div>
                  <ul className="divide-y divide-gray-100">
                    {ensureArray(cohort.memberIds).map((mid) => {
                      const member = memberLookup.get(mid);
                      return (
                        <li key={mid} className="py-2 flex items-center text-sm gap-2 flex-wrap">
                          <div className="flex flex-col">
                            <span className="font-medium text-gray-800">
                              {member?.displayName || mid}
                            </span>
                            <span className="text-[11px] text-gray-500 font-mono">
                              {mid}
                              {member?.birthDate ? ` · DOB ${member.birthDate}` : ""}
                              {member?.gender ? ` · ${member.gender}` : ""}
                            </span>
                          </div>
                          <button
                            type="button"
                            onClick={() => onRemoveMember(mid)}
                            className="ml-auto px-2 py-0.5 text-xs rounded border border-gray-300 hover:border-gray-500"
                          >
                            Remove
                          </button>
                        </li>
                      );
                    })}
                    {ensureArray(cohort.memberIds).length === 0 && (
                      <li className="py-3 text-xs text-gray-500">No members yet.</li>
                    )}
                  </ul>
                </div>
              )}

              {panel === "history" && (
                <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <h4 className="text-sm font-medium text-gray-700">Evaluation history</h4>
                      <p className="text-xs text-gray-500">
                        Cohort numerator / denominator roll-ups received from the submitters stack.
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        loadSummaries();
                        loadReports();
                      }}
                      disabled={summariesLoading}
                      className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500 disabled:opacity-50"
                    >
                      {summariesLoading ? "Refreshing\u2026" : "Refresh"}
                    </button>
                  </div>

                  {summariesError && (
                    <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
                      {summariesError}
                    </div>
                  )}

                  {(() => {
                    const cohortSummaries = summaries.filter((s) => s.cohort?.id === cohort.id);
                    if (!summariesLoading && !cohortSummaries.length) {
                      return (
                        <p className="text-xs text-gray-500">
                          No measure summaries have been received for this cohort yet.
                        </p>
                      );
                    }
                    return (
                      <ul className="divide-y divide-gray-100">
                        {cohortSummaries.map((s) => {
                          const ts = s.receivedAt || s.generatedAt;
                          const when = ts ? new Date(ts).toLocaleString() : "";
                          return (
                            <li key={s.id} className="py-3 space-y-1">
                              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                                <span className="font-medium text-gray-800">
                                  {s.agency?.name || s.agency?.id}
                                </span>
                                {s.program?.name && (
                                  <span className="text-xs text-gray-600">/ {s.program.name}</span>
                                )}
                                <span className="text-xs text-gray-500">· {when}</span>
                                {s.engine && (
                                  <span className="text-[11px] text-gray-500">· engine: {s.engine}</span>
                                )}
                              </div>
                              <div className="text-[11px] text-gray-500">
                                Source: {s.sourceStack || "submitters"}
                                {s.sourceSendId ? ` · send ${s.sourceSendId}` : ""}
                                {s.periodStart || s.periodEnd
                                  ? ` · ${s.periodStart || "?"} – ${s.periodEnd || "?"}`
                                  : ""}
                              </div>
                              {ensureArray(s.perMeasure).length > 0 && (
                                <table className="w-full text-xs mt-1">
                                  <thead>
                                    <tr className="text-left text-gray-500">
                                      <th className="py-1 pr-2 font-medium">Measure</th>
                                      <th className="py-1 pr-2 font-medium text-right">Numerator</th>
                                      <th className="py-1 pr-2 font-medium text-right">Denominator</th>
                                      <th className="py-1 pr-2 font-medium text-right">Exclusions</th>
                                      <th className="py-1 pr-2 font-medium text-right">Rate</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {ensureArray(s.perMeasure).map((pm) => {
                                      const rate =
                                        typeof pm.performanceRate === "number"
                                          ? `${(pm.performanceRate * 100).toFixed(1)}%`
                                          : "—";
                                      return (
                                        <tr key={pm.measureId} className="border-t border-gray-100">
                                          <td className="py-1 pr-2 font-mono text-gray-700">
                                            {pm.title || pm.measureId}
                                          </td>
                                          <td className="py-1 pr-2 text-right">{pm.numerator ?? 0}</td>
                                          <td className="py-1 pr-2 text-right">{pm.denominator ?? 0}</td>
                                          <td className="py-1 pr-2 text-right">{pm.exclusions ?? 0}</td>
                                          <td className="py-1 pr-2 text-right">{rate}</td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              )}
                              {s.note && (
                                <div className="text-xs text-gray-500 italic">“{s.note}”</div>
                              )}
                            </li>
                          );
                        })}
                      </ul>
                    );
                  })()}

                  {/* ----- DEQM FHIR MeasureReports received (individual / subject-list / summary) ----- */}
                  <div className="mt-4 pt-3 border-t border-gray-200 space-y-2">
                    <div className="flex items-center justify-between">
                      <div>
                        <h5 className="text-sm font-medium text-gray-700">DEQM MeasureReports</h5>
                        <p className="text-xs text-gray-500">
                          Standards-conformant FHIR <code>MeasureReport</code> resources received
                          via <code>/measure-reports</code>, by profile.
                        </p>
                      </div>
                      <select
                        value={reportTypeFilter}
                        onChange={(e) => setReportTypeFilter(e.target.value as typeof reportTypeFilter)}
                        className="px-2 py-1 text-xs border border-gray-300 rounded"
                      >
                        <option value="all">All types</option>
                        <option value="summary">Summary</option>
                        <option value="subject-list">Subject list</option>
                        <option value="individual">Individual</option>
                      </select>
                    </div>

                    {reportsError && (
                      <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">
                        {reportsError}
                      </div>
                    )}

                    {(() => {
                      const cohortReports = reports
                        .filter((r) => reportBelongsToCohort(r, cohort))
                        .filter((r) => reportTypeFilter === "all" || r.reportType === reportTypeFilter)
                        .sort((a, b) => (b.receivedAt || 0) - (a.receivedAt || 0));
                      if (!cohortReports.length) {
                        return (
                          <p className="text-xs text-gray-500">
                            No DEQM MeasureReports received for this cohort yet.
                          </p>
                        );
                      }
                      return (
                        <ul className="divide-y divide-gray-100">
                          {cohortReports.map((r) => {
                            const counts = reportPopulationCounts(r.resource);
                            const when = r.receivedAt ? new Date(r.receivedAt).toLocaleString() : "";
                            const badge =
                              REPORT_TYPE_BADGE[r.reportType] || "bg-gray-100 text-gray-700 border-gray-200";
                            const subjectRef = r.resource?.subject?.reference || "";
                            return (
                              <li key={r.id} className="py-2 space-y-1">
                                <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                                  <span
                                    className={`text-[11px] px-1.5 py-0.5 rounded border font-medium ${badge}`}
                                  >
                                    {r.reportType}
                                  </span>
                                  <span className="font-mono text-gray-800">
                                    {measureLabelFromCanonical(r.resource?.measure) ||
                                      (r.measureIds || []).join(", ")}
                                  </span>
                                  <span className="text-xs text-gray-500">· {when}</span>
                                </div>
                                <div className="text-[11px] text-gray-500">
                                  {subjectRef ? `subject: ${subjectRef}` : ""}
                                  {r.periodStart || r.periodEnd
                                    ? ` · ${r.periodStart || "?"} – ${r.periodEnd || "?"}`
                                    : ""}
                                  {r.resource?.reporter?.display
                                    ? ` · reporter: ${r.resource.reporter.display}`
                                    : ""}
                                </div>
                                <div className="flex gap-3 text-xs text-gray-700">
                                  <span>
                                    Numerator:{" "}
                                    <strong>{counts["numerator"] ?? 0}</strong>
                                  </span>
                                  <span>
                                    Denominator:{" "}
                                    <strong>{counts["denominator"] ?? 0}</strong>
                                  </span>
                                  <span>
                                    Exclusions:{" "}
                                    <strong>
                                      {counts["denominator-exclusion"] ?? counts["exclusion"] ?? 0}
                                    </strong>
                                  </span>
                                  <span className="text-gray-400">·</span>
                                  <span className="font-mono text-gray-500">{r.id.slice(0, 8)}</span>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      );
                    })()}
                  </div>
                </div>
              )}

              {panel === "evaluate" && (
                <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                  <h4 className="text-sm font-medium text-gray-700">Evaluate measures</h4>

                  <div>
                    <p className="text-xs text-gray-500 mb-1">Measures (enabled in catalog):</p>
                    <div className="flex flex-wrap gap-1">
                      {enabledMeasures.map((m) => {
                        const sel = selectedMeasureIds.includes(m.id);
                        return (
                          <button
                            key={m.id}
                            type="button"
                            onClick={() => onToggleMeasureSelection(m.id)}
                            className={`text-xs px-2 py-1 rounded border transition ${
                              sel
                                ? "bg-blue-600 text-white border-blue-600"
                                : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
                            }`}
                            title={m.customDescription || m.description}
                          >
                            {m.customName || m.title || m.id}{" "}
                            <span className={sel ? "text-blue-100" : "text-gray-500"}>{m.id}</span>
                          </button>
                        );
                      })}
                      {enabledMeasures.length === 0 && (
                        <span className="text-xs text-gray-500">
                          No measures are enabled. Visit the Catalog tab.
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-3 items-end">
                    <label className="text-xs text-gray-700">
                      Period start
                      <input
                        type="date"
                        value={periodStart}
                        onChange={(e) => setPeriodStart(e.target.value)}
                        className="block mt-1 px-2 py-1 text-sm border border-gray-300 rounded"
                      />
                    </label>
                    <label className="text-xs text-gray-700">
                      Period end
                      <input
                        type="date"
                        value={periodEnd}
                        onChange={(e) => setPeriodEnd(e.target.value)}
                        className="block mt-1 px-2 py-1 text-sm border border-gray-300 rounded"
                      />
                    </label>
                    <label className="text-xs text-gray-700">
                      Engine
                      <select
                        value={engine}
                        onChange={(e) => setEngine(e.target.value as "native-cql" | "ai-cql")}
                        className="block mt-1 px-2 py-1 text-sm border border-gray-300 rounded"
                      >
                        <option value="native-cql">native-cql</option>
                        <option value="ai-cql">ai-cql</option>
                      </select>
                    </label>
                    <fieldset className="text-xs text-gray-700">
                      <legend className="mb-1">Scope</legend>
                      <label className="inline-flex items-center mr-3">
                        <input
                          type="radio"
                          name="scope"
                          value="cohort"
                          checked={scope === "cohort"}
                          onChange={() => setScope("cohort")}
                        />
                        <span className="ml-1">Whole cohort</span>
                      </label>
                      <label className="inline-flex items-center">
                        <input
                          type="radio"
                          name="scope"
                          value="member"
                          checked={scope === "member"}
                          onChange={() => setScope("member")}
                        />
                        <span className="ml-1">Single member</span>
                      </label>
                    </fieldset>
                    {scope === "member" && (
                      <input
                        type="text"
                        value={scopeMemberId}
                        onChange={(e) => setScopeMemberId(e.target.value)}
                        placeholder="Member id"
                        className="px-2 py-1 text-sm border border-gray-300 rounded"
                      />
                    )}
                    <button
                      type="button"
                      onClick={onRunEvaluation}
                      disabled={evaluating || !selectedMeasureIds.length}
                      className="ml-auto px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      {evaluating ? "Running…" : "Run"}
                    </button>
                  </div>

                  {results.length > 0 && (
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-sm border border-gray-200">
                        <thead className="bg-gray-50">
                          <tr>
                            <th className="text-left px-2 py-1 border-b">Member</th>
                            <th className="text-left px-2 py-1 border-b">Measure</th>
                            <th className="text-left px-2 py-1 border-b">Status</th>
                            <th className="text-right px-2 py-1 border-b">Numerator</th>
                            <th className="text-right px-2 py-1 border-b">Denominator</th>
                            <th className="text-right px-2 py-1 border-b">Exclusion</th>
                            <th className="text-left px-2 py-1 border-b">Notes</th>
                          </tr>
                        </thead>
                        <tbody>
                          {results.map((r, i) => (
                            <tr key={`${r.memberId}-${r.measureId}-${i}`} className="even:bg-gray-50">
                              <td className="px-2 py-1 font-mono">{r.memberId}</td>
                              <td className="px-2 py-1">{r.measureId}</td>
                              <td className="px-2 py-1">
                                {r.error ? (
                                  <span className="text-red-700">error</span>
                                ) : (
                                  r.status || "—"
                                )}
                              </td>
                              <td className="px-2 py-1 text-right">{r.numerator ?? "—"}</td>
                              <td className="px-2 py-1 text-right">{r.denominator ?? "—"}</td>
                              <td className="px-2 py-1 text-right">{r.populationExclusion ?? "—"}</td>
                              <td className="px-2 py-1 text-xs text-gray-700 max-w-md">
                                {r.error ? (
                                  <span className="text-red-700">{r.error}</span>
                                ) : (
                                  <div className="space-y-1">
                                    {r.note && <div>{r.note}</div>}
                                    {r.evidenceTrace && r.evidenceTrace.length > 0 && (
                                      <details className="text-gray-600">
                                        <summary className="cursor-pointer text-blue-700 hover:underline">
                                          Evidence ({r.evidenceTrace.length})
                                        </summary>
                                        <ul className="list-disc list-inside mt-1">
                                          {r.evidenceTrace.map((line, idx) => (
                                            <li key={idx}>{line}</li>
                                          ))}
                                        </ul>
                                      </details>
                                    )}
                                    {!r.note && !r.evidenceTrace?.length && <span>—</span>}
                                  </div>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {panel === "surveillance" && (
                <CohortChatPanel
                  cohortId={cohort.id}
                  cohortName={cohort.name || cohort.id}
                  selectedMeasureIds={selectedMeasureIds}
                  memberCount={ensureArray(cohort.memberIds).length}
                  periodStart={periodStart}
                  periodEnd={periodEnd}
                />
              )}

              {panel === "submit" && (
                <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                  <div>
                    <h4 className="text-sm font-medium text-gray-700">Submit to regulatory agency</h4>
                    <p className="text-xs text-gray-500">
                      Mocked submission flow. Persists a <code>docType=submission</code> row to{" "}
                      <code>dq/cohorts</code>.
                    </p>
                  </div>

                  <div className="flex flex-wrap gap-3 items-end">
                    <label className="text-xs text-gray-700">
                      Agency
                      <select
                        value={submitAgencyId}
                        onChange={(e) => {
                          setSubmitAgencyId(e.target.value);
                          setSubmitProgramId("");
                        }}
                        className="block mt-1 px-2 py-1 text-sm border border-gray-300 rounded min-w-[16rem]"
                      >
                        <option value="">— choose —</option>
                        {agencies.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                      </select>
                    </label>

                    {(() => {
                      const ag = agencies.find((a) => a.id === submitAgencyId);
                      const programs: WorkbenchProgram[] = ensureArray(ag?.programs);
                      if (!ag || !programs.length) return null;
                      return (
                        <label className="text-xs text-gray-700">
                          Program
                          <select
                            value={submitProgramId}
                            onChange={(e) => {
                              const pid = e.target.value;
                              setSubmitProgramId(pid);
                              const prog = programs.find((p) => p.id === pid);
                              if (prog) {
                                setSelectedMeasureIds(ensureArray(prog.requiredMeasures));
                                if (prog.reportingPeriod?.start) setPeriodStart(prog.reportingPeriod.start);
                                if (prog.reportingPeriod?.end) setPeriodEnd(prog.reportingPeriod.end);
                              }
                            }}
                            className="block mt-1 px-2 py-1 text-sm border border-gray-300 rounded min-w-[14rem]"
                          >
                            <option value="">— choose program —</option>
                            {programs.map((p) => (
                              <option key={p.id || p.name} value={p.id || ""}>
                                {p.name}
                              </option>
                            ))}
                          </select>
                        </label>
                      );
                    })()}

                    <label className="text-xs text-gray-700 flex-1 min-w-[16rem]">
                      Note
                      <input
                        type="text"
                        value={submitNote}
                        onChange={(e) => setSubmitNote(e.target.value)}
                        className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                      />
                    </label>

                    <button
                      type="button"
                      onClick={onSubmit}
                      disabled={busy || !submitAgencyId || !selectedMeasureIds.length}
                      className="ml-auto px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      Submit
                    </button>
                  </div>

                  <p className="text-xs text-gray-500">
                    Submitting <strong>{selectedMeasureIds.length || 0}</strong> measure(s)
                    selected on the Evaluate panel for cohort <strong>{cohort.name}</strong>.
                  </p>
                  {submitStatus && (
                    <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-2">
                      {submitStatus}
                    </div>
                  )}

                  {cohortSubmissions.length > 0 && (
                    <div>
                      <h5 className="text-xs uppercase tracking-wide text-gray-500 mt-3 mb-1">
                        Recent submissions
                      </h5>
                      <ul className="text-sm divide-y divide-gray-100">
                        {cohortSubmissions.map((s) => {
                          const ag = agencies.find((a) => a.id === s.agencyId);
                          return (
                            <li key={s.id} className="py-2">
                              <div className="font-medium">
                                {ag?.name || s.agencyId}{" "}
                                <span className="text-xs text-gray-500">
                                  · {new Date(s.createdAt).toLocaleString()}
                                </span>
                              </div>
                              <div className="text-xs text-gray-600">
                                Status: {s.status} · {ensureArray(s.measureIds).join(", ")}
                              </div>
                              {s.note && <div className="text-xs text-gray-500">“{s.note}”</div>}
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
};

export default CohortsPage;
