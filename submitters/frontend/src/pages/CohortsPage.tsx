import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  CohortMeasurementHistoryRow,
  WorkbenchAgency,
  WorkbenchCohort,
  WorkbenchMeasure,
  WorkbenchMember,
  WorkbenchProgram,
  WorkbenchSubmission,
  WorkbenchSubmissionMeasurement,
  WorkbenchTag,
  MeasureSummarySend,
  DeqmReportType,
  deleteWorkbenchCohort,
  getWorkbenchSubmission,
  listCohortMeasureReportSends,
  listCohortMeasurementHistory,
  listWorkbenchAgencies,
  listWorkbenchCohorts,
  listWorkbenchMeasures,
  listWorkbenchMembers,
  listWorkbenchSubmissions,
  listWorkbenchTags,
  readableTextOn,
  sendCohortMeasureReport,
  sendCohortMeasureSummary,
  submitWorkbenchData,
  updateCohortMembers,
  upsertWorkbenchCohort,
} from "../store/workbench";
import {
  FhirMeasureReport,
  evaluateMeasure,
} from "../store/qualityMeasures";
import CohortChatPanel from "../components/CohortChatPanel";

type CohortPanel = "members" | "surveillance" | "evaluate" | "history" | "submit";

const PANEL_LABELS: Array<{ id: CohortPanel; label: string; help: string }> = [
  { id: "members", label: "Members", help: "Tag patients into the cohort" },
  { id: "surveillance", label: "Agentic Surveillance", help: "Surveil the cohort with LLM Q&A graded by reinforcement learning" },
  { id: "evaluate", label: "Evaluate measures", help: "Run measures across the cohort or a single member" },
  { id: "history", label: "Evaluation history", help: "Audit trail of every measure evaluation for this cohort, with provenance (submission vs direct)" },
  { id: "submit", label: "Submit to agency", help: "Mock regulatory submission" },
];

function ensureArray<T>(v: T[] | undefined | null): T[] {
  return Array.isArray(v) ? v : [];
}

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
  const [submissionDetails, setSubmissionDetails] = useState<
    Record<string, { measurements: WorkbenchSubmissionMeasurement[]; loading?: boolean; error?: string }>
  >({});
  const [members, setMembers] = useState<WorkbenchMember[]>([]);
  const [selectedCohortId, setSelectedCohortId] = useState<string | null>(
    cohortIdFromUrl ?? null,
  );
  const [panel, setPanel] = useState<CohortPanel>("members");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [memberInput, setMemberInput] = useState("");
  const [busy, setBusy] = useState(false);

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
  const [submitReportType, setSubmitReportType] = useState<DeqmReportType>("summary");
  const [submitStatus, setSubmitStatus] = useState<string | null>(null);

  // measure-summary send (submitters -> receivers + platform)
  const [sendingSummary, setSendingSummary] = useState(false);
  const [summarySend, setSummarySend] = useState<MeasureSummarySend | null>(null);
  const [summarySends, setSummarySends] = useState<MeasureSummarySend[]>([]);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [expandedSendId, setExpandedSendId] = useState<string | null>(null);

  // evaluation history panel state
  const [history, setHistory] = useState<CohortMeasurementHistoryRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historySourceFilter, setHistorySourceFilter] = useState<"all" | "submission" | "direct">("all");

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

  const loadHistory = useCallback(
    async (cohortId: string) => {
      setHistoryLoading(true);
      setHistoryError(null);
      try {
        const rows = await listCohortMeasurementHistory(cohortId, { limit: 500 });
        setHistory(rows);
      } catch (e) {
        setHistoryError(e instanceof Error ? e.message : "Failed to load evaluation history.");
        setHistory([]);
      } finally {
        setHistoryLoading(false);
      }
    },
    [],
  );

  const loadSummarySends = useCallback(async (cohortId: string) => {
    try {
      const rows = await listCohortMeasureReportSends(cohortId);
      setSummarySends(rows);
    } catch (e) {
      // non-fatal; user can still send.
      setSummarySends([]);
    }
  }, []);

  useEffect(() => {
    setSummarySend(null);
    setSummaryError(null);
    setSubmitStatus(null);
    setExpandedSendId(null);
    if (cohort?.id) {
      loadHistory(cohort.id);
      loadSummarySends(cohort.id);
    } else {
      setHistory([]);
      setSummarySends([]);
    }
  }, [cohort?.id, loadHistory, loadSummarySends]);

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
          const report = await evaluateMeasure(measureId, memberId, periodStart, periodEnd, engine, cohort?.id);
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
    if (cohort?.id) {
      loadHistory(cohort.id);
    }
  };

  // ---------------- Submit ----------------

  // Submit/send always reports the cohort's canonical measure(s). The Evaluate
  // panel's selectedMeasureIds is a transient run scope (lets the user try a
  // different measure ad-hoc) and should not drive what gets sent downstream.
  const submitMeasureIds: string[] =
    cohort && Array.isArray(cohort.measureIds) && cohort.measureIds.length
      ? cohort.measureIds
      : selectedMeasureIds;

  const onSubmit = async () => {
    if (!cohort) return;
    if (!submitAgencyId) {
      setError("Choose a regulatory agency.");
      return;
    }
    if (!submitMeasureIds.length) {
      setError("Cohort has no measureIds configured. Edit the cohort to add at least one measure.");
      return;
    }
    setBusy(true);
    setSubmitStatus(null);
    setSummaryError(null);
    setSendingSummary(true);
    try {
      const sub = await submitWorkbenchData({
        cohortId: cohort.id,
        agencyId: submitAgencyId,
        measureIds: submitMeasureIds,
        note: submitNote,
      });
      setSubmitStatus(`Submission ${sub.id} queued (${sub.status}).`);
      const refreshed = await listWorkbenchSubmissions();
      setSubmissions(refreshed);
      try {
        const send = await sendCohortMeasureReport(cohort.id, submitReportType, {
          agencyId: submitAgencyId,
          programId: submitProgramId || undefined,
          measureIds: submitMeasureIds,
          periodStart: periodStart || undefined,
          periodEnd: periodEnd || undefined,
          note: submitNote,
          engine,
        });
        setSummarySend(send);
        setExpandedSendId(send.id);
        await loadSummarySends(cohort.id);
      } catch (e) {
        setSummaryError(e instanceof Error ? e.message : "Send summary failed.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Submission failed.");
    } finally {
      setSendingSummary(false);
      setBusy(false);
    }
  };

  const _onSendSummaryLegacy = async () => {
    if (!cohort) return;
    if (!submitAgencyId) {
      setSummaryError("Choose a regulatory agency.");
      return;
    }
    setSendingSummary(true);
    setSummaryError(null);
    try {
      const send = await sendCohortMeasureSummary(cohort.id, {
        agencyId: submitAgencyId,
        programId: submitProgramId || undefined,
        measureIds: selectedMeasureIds.length ? selectedMeasureIds : undefined,
        periodStart: periodStart || undefined,
        periodEnd: periodEnd || undefined,
        note: submitNote,
        engine,
      });
      setSummarySend(send);
      await loadSummarySends(cohort.id);
    } catch (e) {
      setSummaryError(e instanceof Error ? e.message : "Send summary failed.");
    } finally {
      setSendingSummary(false);
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
                      {c.source === "providers" && (
                        <span
                          className="ml-1 inline-block px-1 py-[1px] rounded text-[10px] bg-blue-50 text-blue-700 border border-blue-200"
                          title={
                            c.lastReceivedAt
                              ? `Received from providers at ${new Date(c.lastReceivedAt).toLocaleString()}`
                              : "Received from providers"
                          }
                        >
                          received
                        </span>
                      )}
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
                    </div>
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

              {panel === "history" && (
                <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h4 className="text-sm font-medium text-gray-700">Evaluation history</h4>
                      <p className="text-xs text-gray-500">
                        Audit trail of every measure evaluation for this cohort. Each row
                        records whether the run was triggered by an inbound provider
                        submission (<span className="font-medium text-blue-700">submission</span>) or
                        by clicking <em>Run</em> on the Evaluate measures tab
                        (<span className="font-medium text-emerald-700">direct</span>).
                      </p>
                    </div>
                    <button
                      type="button"
                      className="text-xs px-2 py-1 border border-gray-300 rounded hover:bg-gray-50"
                      onClick={() => cohort?.id && loadHistory(cohort.id)}
                      disabled={historyLoading}
                    >
                      {historyLoading ? "Refreshing…" : "Refresh"}
                    </button>
                  </div>

                  <div className="flex items-center gap-2 text-xs">
                    <span className="text-gray-500">Filter source:</span>
                    {(["all", "submission", "direct"] as const).map((opt) => (
                      <button
                        key={opt}
                        type="button"
                        className={`px-2 py-1 rounded border ${
                          historySourceFilter === opt
                            ? "border-blue-500 bg-blue-50 text-blue-700"
                            : "border-gray-300 text-gray-700 hover:bg-gray-50"
                        }`}
                        onClick={() => setHistorySourceFilter(opt)}
                      >
                        {opt === "all" ? "All" : opt === "submission" ? "Submission" : "Direct"}
                      </button>
                    ))}
                    <span className="ml-auto text-gray-500">
                      {history.length} row{history.length === 1 ? "" : "s"} total
                    </span>
                  </div>

                  {historyError && (
                    <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded p-2">
                      {historyError}
                    </div>
                  )}

                  {(() => {
                    const filtered =
                      historySourceFilter === "all"
                        ? history
                        : history.filter((r) => r.source === historySourceFilter);
                    if (!filtered.length) {
                      return (
                        <div className="text-xs text-gray-500 italic py-4 text-center">
                          {historyLoading
                            ? "Loading evaluation history…"
                            : "No evaluations recorded for this cohort yet."}
                        </div>
                      );
                    }
                    return (
                      <div className="overflow-x-auto">
                        <table className="min-w-full text-xs">
                          <thead className="bg-gray-50 text-gray-600">
                            <tr>
                              <th className="text-left px-2 py-1">When</th>
                              <th className="text-left px-2 py-1">Source</th>
                              <th className="text-left px-2 py-1">Measure</th>
                              <th className="text-left px-2 py-1">Member</th>
                              <th className="text-left px-2 py-1">Engine</th>
                              <th className="text-left px-2 py-1">Status</th>
                              <th className="text-left px-2 py-1">N / D</th>
                              <th className="text-left px-2 py-1">Submission</th>
                              <th className="text-left px-2 py-1">Notes</th>
                            </tr>
                          </thead>
                          <tbody>
                            {filtered.map((row) => {
                              const when = row.createdAt
                                ? new Date(row.createdAt).toLocaleString()
                                : "";
                              const sourceBadge =
                                row.source === "submission"
                                  ? "bg-blue-100 text-blue-800 border-blue-200"
                                  : row.source === "direct"
                                  ? "bg-emerald-100 text-emerald-800 border-emerald-200"
                                  : "bg-gray-100 text-gray-700 border-gray-200";
                              const statusBadge =
                                row.status === "completed"
                                  ? "text-emerald-700"
                                  : row.status === "failed"
                                  ? "text-red-700"
                                  : "text-gray-700";
                              return (
                                <tr key={row.id} className="border-t border-gray-100 align-top">
                                  <td className="px-2 py-1 whitespace-nowrap text-gray-700">{when}</td>
                                  <td className="px-2 py-1">
                                    <span
                                      className={`inline-block px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wide ${sourceBadge}`}
                                    >
                                      {row.source}
                                    </span>
                                    {row.sourceStack && (
                                      <div className="text-[10px] text-gray-500 mt-0.5">
                                        from {row.sourceStack}
                                      </div>
                                    )}
                                  </td>
                                  <td className="px-2 py-1 font-mono text-[11px] text-gray-800">
                                    {row.measureId}
                                  </td>
                                  <td className="px-2 py-1 font-mono text-[11px] text-gray-800">
                                    {row.memberId}
                                  </td>
                                  <td className="px-2 py-1 text-gray-700">{row.engine || ""}</td>
                                  <td className={`px-2 py-1 font-medium ${statusBadge}`}>
                                    {row.status}
                                    {row.httpStatus ? (
                                      <span className="text-[10px] text-gray-500 ml-1">
                                        ({row.httpStatus})
                                      </span>
                                    ) : null}
                                  </td>
                                  <td className="px-2 py-1 text-gray-800">
                                    {row.numerator != null && row.denominator != null
                                      ? `${row.numerator} / ${row.denominator}`
                                      : ""}
                                    {row.exclusion ? (
                                      <span className="ml-1 text-[10px] text-amber-700">excl</span>
                                    ) : null}
                                  </td>
                                  <td className="px-2 py-1 font-mono text-[11px] text-gray-600">
                                    {row.submissionId || "—"}
                                  </td>
                                  <td className="px-2 py-1 text-gray-700 max-w-xs">
                                    {row.error ? (
                                      <span className="text-red-700">{row.error}</span>
                                    ) : (
                                      row.note || ""
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    );
                  })()}
                </div>
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

                    <label className="text-xs text-gray-700">
                      Report type
                      <select
                        value={submitReportType}
                        onChange={(e) => setSubmitReportType(e.target.value as DeqmReportType)}
                        className="block mt-1 px-2 py-1 text-sm border border-gray-300 rounded min-w-[12rem]"
                      >
                        <option value="summary">Summary (population roll-up)</option>
                        <option value="subject-list">Subject list (per-member refs)</option>
                        <option value="individual">Individual (one per member)</option>
                      </select>
                    </label>

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
                      disabled={busy || !submitAgencyId || !submitMeasureIds.length}
                      className="ml-auto px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                    >
                      Submit
                    </button>
                  </div>

                  <p className="text-xs text-gray-500">
                    Reporting <strong>{submitMeasureIds.length || 0}</strong> measure(s)
                    configured on cohort <strong>{cohort.name}</strong>:{" "}
                    {submitMeasureIds.length ? (
                      submitMeasureIds.map((mid, i) => (
                        <span key={mid}>
                          {i > 0 && ", "}
                          <code>{mid}</code>
                        </span>
                      ))
                    ) : (
                      <em>none configured</em>
                    )}
                    .
                  </p>
                  {submitStatus && (
                    <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-2">
                      {submitStatus}
                    </div>
                  )}

                  {/* ----- Send measure summary (now triggered by Submit) ----- */}
                  <div className="ml-6 pl-3 mt-2 border-l-2 border-gray-200 space-y-2">
                    <div>
                      <h6 className="text-xs font-medium text-gray-600">
                        Send measure summary
                      </h6>
                      <p className="text-xs text-gray-500">
                        Rolls up the latest <code>measurement_history</code> rows for this
                        cohort (run <strong>Evaluate</strong> first) into a cohort-level and
                        per-patient numerator / denominator summary, then dispatches the
                        summary.
                      </p>
                    </div>

                    {summaryError && (
                      <div className="text-sm text-red-800 bg-red-50 border border-red-200 rounded p-2">
                        {summaryError}
                      </div>
                    )}

                    {summarySends.length > 0 && (
                      <details open className="text-xs text-gray-600">
                        <summary className="cursor-pointer">
                          Previous sends ({summarySends.length})
                        </summary>
                        <ul className="mt-1 divide-y divide-gray-100">
                          {summarySends.map((s) => {
                            const isExpanded = expandedSendId === s.id;
                            return (
                              <li key={s.id} className="py-1">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono">{s.id}</span>
                                  <span
                                    className={
                                      "px-1 py-[1px] rounded text-[10px] border " +
                                      (s.status === "sent"
                                        ? "bg-green-50 text-green-700 border-green-200"
                                        : s.status === "partial"
                                        ? "bg-amber-50 text-amber-700 border-amber-200"
                                        : "bg-red-50 text-red-700 border-red-200")
                                    }
                                  >
                                    {s.status}
                                  </span>
                                  <span className="text-gray-500">
                                    {new Date(s.createdAt).toLocaleString()}
                                  </span>
                                  <button
                                    type="button"
                                    className="ml-auto text-blue-600 hover:underline"
                                    onClick={() =>
                                      setExpandedSendId(isExpanded ? null : s.id)
                                    }
                                  >
                                    {isExpanded ? "hide" : "view"}
                                  </button>
                                </div>

                                {isExpanded && (
                                  <div className="mt-2 text-sm border border-gray-200 rounded p-2 bg-gray-50 space-y-2">
                                    {/* per-destination status */}
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                                      {(["receivers", "platform"] as const).map((tgt) => {
                                        const d = s.dispatch?.[tgt];
                                        if (!d) return null;
                                        return (
                                          <div
                                            key={tgt}
                                            className={
                                              "rounded border p-2 text-xs " +
                                              (d.status === "sent"
                                                ? "border-green-200 bg-green-50"
                                                : "border-red-200 bg-red-50")
                                            }
                                          >
                                            <div className="flex items-center gap-2">
                                              <span className="font-medium capitalize">{tgt}</span>
                                              <span className="text-gray-600">{d.status}</span>
                                              {d.statusCode != null && (
                                                <span className="text-gray-500">HTTP {d.statusCode}</span>
                                              )}
                                            </div>
                                            <div className="text-gray-600 break-all">{d.url}</div>
                                            {d.error && (
                                              <div className="mt-1 text-red-700 break-words">{d.error}</div>
                                            )}
                                            {d.remoteSummaryId && (
                                              <div className="mt-1 text-gray-600">
                                                remote id:{" "}
                                                <span className="font-mono">{d.remoteSummaryId}</span>
                                              </div>
                                            )}
                                          </div>
                                        );
                                      })}
                                    </div>

                                    {/* per-measure roll-up */}
                                    {s.summary?.perMeasure?.length ? (
                                      <div>
                                        <h6 className="text-xs uppercase tracking-wide text-gray-500 mt-2 mb-1">
                                          Cohort roll-up
                                        </h6>
                                        <table className="w-full text-xs">
                                          <thead className="bg-white">
                                            <tr className="text-left text-gray-500">
                                              <th className="px-2 py-1">Measure</th>
                                              <th className="px-2 py-1 text-right">Num</th>
                                              <th className="px-2 py-1 text-right">Denom</th>
                                              <th className="px-2 py-1 text-right">Excl</th>
                                              <th className="px-2 py-1 text-right">Rate</th>
                                            </tr>
                                          </thead>
                                          <tbody>
                                            {s.summary.perMeasure.map((r) => (
                                              <tr key={r.measureId} className="border-t border-gray-100">
                                                <td className="px-2 py-1">
                                                  <span className="font-mono">{r.measureId}</span>
                                                  {r.title && r.title !== r.measureId ? (
                                                    <span className="text-gray-500"> · {r.title}</span>
                                                  ) : null}
                                                </td>
                                                <td className="px-2 py-1 text-right">{r.numerator}</td>
                                                <td className="px-2 py-1 text-right">{r.denominator}</td>
                                                <td className="px-2 py-1 text-right">{r.exclusions}</td>
                                                <td className="px-2 py-1 text-right">
                                                  {r.performanceRate == null
                                                    ? "—"
                                                    : (r.performanceRate * 100).toFixed(1) + "%"}
                                                </td>
                                              </tr>
                                            ))}
                                          </tbody>
                                        </table>
                                      </div>
                                    ) : null}

                                    {/* per-member roll-up */}
                                    {s.summary?.perMember?.length ? (
                                      <details>
                                        <summary className="cursor-pointer text-xs text-gray-600 hover:text-gray-800">
                                          Per-patient breakdown ({s.summary.perMember.length})
                                        </summary>
                                        <div className="overflow-x-auto mt-1">
                                          <table className="w-full text-xs">
                                            <thead className="bg-white">
                                              <tr className="text-left text-gray-500">
                                                <th className="px-2 py-1">Member</th>
                                                {s.summary.measureIds.map((mid) => (
                                                  <th key={mid} className="px-2 py-1 text-right font-mono">
                                                    {mid}
                                                  </th>
                                                ))}
                                              </tr>
                                            </thead>
                                            <tbody>
                                              {s.summary.perMember.map((m) => (
                                                <tr key={m.memberId} className="border-t border-gray-100">
                                                  <td className="px-2 py-1">
                                                    {m.displayName || m.memberId}
                                                  </td>
                                                  {s.summary!.measureIds.map((mid) => {
                                                    const cell = m.perMeasure.find(
                                                      (c) => c.measureId === mid,
                                                    );
                                                    const n = cell?.numerator;
                                                    const d = cell?.denominator;
                                                    return (
                                                      <td
                                                        key={mid}
                                                        className={
                                                          "px-2 py-1 text-right " +
                                                          (cell?.exclusion ? "text-amber-700" : "")
                                                        }
                                                      >
                                                        {n == null || d == null ? "—" : `${n} / ${d}`}
                                                        {cell?.exclusion ? " excl" : ""}
                                                      </td>
                                                    );
                                                  })}
                                                </tr>
                                              ))}
                                            </tbody>
                                          </table>
                                        </div>
                                      </details>
                                    ) : null}
                                  </div>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </details>
                    )}
                  </div>
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
