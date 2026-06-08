import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  WorkbenchAgency,
  WorkbenchMeasure,
  WorkbenchProgram,
  WorkbenchTag,
  addWorkbenchMeasure,
  deleteWorkbenchAgency,
  deleteWorkbenchMeasure,
  deleteWorkbenchTag,
  generateMeasureSampleData,
  listWorkbenchAgencies,
  listWorkbenchMeasures,
  listWorkbenchTags,
  readableTextOn,
  updateWorkbenchMeasure,
  upsertWorkbenchAgency,
  upsertWorkbenchTag,
} from "../store/workbench";

type CatalogSection = "measures" | "tags" | "agencies";

const SECTION_LABELS: Array<{ id: CatalogSection; label: string; help: string }> = [
  { id: "measures", label: "Measures", help: "Enable, rename, describe, generate sample data" },
  { id: "tags", label: "Tags", help: "Configurable program tags (Shared Savings, Universal Foundation, …)" },
  { id: "agencies", label: "Regulatory agencies", help: "Programs, reporting periods, required measures" },
];

// Okabe-Ito colour-blind safe palette (default picker swatches).
const OKABE_ITO = [
  "#000000", "#E69F00", "#56B4E9", "#009E73",
  "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
];

function ensureArray<T>(v: T[] | undefined | null): T[] {
  return Array.isArray(v) ? v : [];
}

function yearStartIso(): string {
  return `${new Date().getFullYear()}-01-01`;
}

function yearEndIso(): string {
  return `${new Date().getFullYear()}-12-31`;
}

function emptyProgram(): WorkbenchProgram {
  return {
    name: "New program",
    shortName: "",
    description: "",
    reportingPeriod: { start: yearStartIso(), end: yearEndIso() },
    requiredMeasures: [],
  };
}

// ---------------------------------------------------------------------------
// Tag chip — accessible coloured background with auto-contrast text.
// ---------------------------------------------------------------------------

interface TagChipProps {
  tag: WorkbenchTag;
  active: boolean;
  onClick?: () => void;
  disabled?: boolean;
  title?: string;
}

const TagChip: React.FC<TagChipProps> = ({ tag, active, onClick, disabled, title }) => {
  const bg = tag.color || "#64748b";
  const fg = readableTextOn(bg);
  const baseClass = "text-[11px] px-1.5 py-0.5 rounded border transition";
  const style = active
    ? { backgroundColor: bg, color: fg, borderColor: bg }
    : { borderColor: bg, color: bg, backgroundColor: "transparent" };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`${baseClass} disabled:opacity-50`}
      style={style}
      title={title || tag.name}
      aria-pressed={onClick ? active : undefined}
    >
      {tag.name}
    </button>
  );
};

// ---------------------------------------------------------------------------
// Inline measure editor
// ---------------------------------------------------------------------------

interface MeasureRowProps {
  measure: WorkbenchMeasure;
  tags: WorkbenchTag[];
  onSave: (patch: Partial<WorkbenchMeasure>) => Promise<void>;
  onDelete: () => Promise<void>;
  onGenerateSamples: () => Promise<void>;
  busy: boolean;
  sampleStatus?: string;
}

const MeasureRow: React.FC<MeasureRowProps> = ({
  measure,
  tags,
  onSave,
  onDelete,
  onGenerateSamples,
  busy,
  sampleStatus,
}) => {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<WorkbenchMeasure>(measure);

  useEffect(() => {
    if (!editing) setDraft(measure);
  }, [measure, editing]);

  const display = measure.customName || measure.title || measure.id;
  const desc = measure.customDescription || measure.description || "";

  const toggleTag = (tagId: string) => {
    const has = ensureArray(draft.tags).includes(tagId);
    setDraft({
      ...draft,
      tags: has
        ? ensureArray(draft.tags).filter((t) => t !== tagId)
        : [...ensureArray(draft.tags), tagId],
    });
  };

  const onCancel = () => {
    setDraft(measure);
    setEditing(false);
  };

  const onSubmit = async () => {
    await onSave({
      enabled: draft.enabled,
      customName: (draft.customName ?? "").trim() || null,
      customDescription: (draft.customDescription ?? "").trim() || null,
      tags: ensureArray(draft.tags),
    });
    setEditing(false);
  };

  if (!editing) {
    return (
      <li className="py-3" data-resource-kind="measure" data-resource-id={measure.id}>
        <div className="flex flex-wrap items-start gap-3">
          <label className="inline-flex items-center mt-1">
            <input
              type="checkbox"
              checked={!!measure.enabled}
              onChange={(e) => onSave({ enabled: e.target.checked })}
              disabled={busy}
              title={measure.enabled ? "Enabled" : "Disabled"}
            />
          </label>
          <div className="flex-1 min-w-[16rem]">
            <div className="flex items-baseline gap-2">
              <button
                type="button"
                onClick={() => navigate(`/measures/${measure.id}`)}
                className="font-medium text-gray-900 hover:text-blue-700 hover:underline text-left"
                title="Open measure details"
              >
                {display}
              </button>
              <span className="text-xs text-gray-500">
                {measure.id}
                {measure.version ? ` · v${measure.version}` : ""}
              </span>
              {measure.builtin && (
                <span className="text-[10px] px-1 rounded bg-gray-100 text-gray-600">
                  built-in
                </span>
              )}
            </div>
            {desc && <p className="text-sm text-gray-600 mt-0.5">{desc}</p>}
            <div className="flex flex-wrap gap-1 mt-1">
              {ensureArray(measure.tags)
                .map((tid) => tags.find((t) => t.id === tid))
                .filter((t): t is WorkbenchTag => !!t)
                .map((t) => (
                  <TagChip key={t.id} tag={t} active />
                ))}
            </div>
            {sampleStatus && <p className="text-xs text-gray-500 mt-1">{sampleStatus}</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
            >
              Edit
            </button>
            <button
              type="button"
              onClick={onGenerateSamples}
              className="px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
            >
              Generate sample data
            </button>
            {!measure.builtin && (
              <button
                type="button"
                onClick={onDelete}
                className="px-2 py-1 text-xs rounded border border-red-300 text-red-700 hover:bg-red-50"
              >
                Delete
              </button>
            )}
          </div>
        </div>
      </li>
    );
  }

  return (
    <li className="py-3 bg-blue-50/40 border-l-4 border-blue-400 pl-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-gray-700">
          Display name
          <input
            type="text"
            value={draft.customName ?? ""}
            placeholder={measure.title || measure.id}
            onChange={(e) => setDraft({ ...draft, customName: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700">
          Identifier
          <input
            type="text"
            value={draft.id}
            disabled
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-200 bg-gray-50 rounded text-gray-500"
          />
        </label>
        <label className="text-xs text-gray-700 md:col-span-2">
          Description
          <textarea
            value={draft.customDescription ?? draft.description ?? ""}
            onChange={(e) => setDraft({ ...draft, customDescription: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
            rows={2}
          />
        </label>
        <label className="inline-flex items-center text-xs text-gray-700">
          <input
            type="checkbox"
            checked={!!draft.enabled}
            onChange={(e) => setDraft({ ...draft, enabled: e.target.checked })}
            className="mr-2"
          />
          Enabled
        </label>
        <div className="text-xs text-gray-700 md:col-span-2">
          Tags
          <div className="flex flex-wrap gap-1 mt-1">
            {tags.map((t) => (
              <TagChip
                key={t.id}
                tag={t}
                active={ensureArray(draft.tags).includes(t.id)}
                onClick={() => toggleTag(t.id)}
              />
            ))}
            {tags.length === 0 && (
              <span className="text-gray-500">No tags defined.</span>
            )}
          </div>
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onSubmit}
          disabled={busy}
          className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </li>
  );
};

interface NewMeasureRowProps {
  tags: WorkbenchTag[];
  onCancel: () => void;
  onCreate: (m: Partial<WorkbenchMeasure> & { id: string; title: string }) => Promise<void>;
}

const NewMeasureRow: React.FC<NewMeasureRowProps> = ({ tags, onCancel, onCreate }) => {
  const [draft, setDraft] = useState<Partial<WorkbenchMeasure>>({
    id: "",
    title: "",
    description: "",
    version: "",
    topic: "",
    enabled: true,
    tags: [],
  });
  const [busy, setBusy] = useState(false);
  const toggleTag = (tagId: string) => {
    const has = ensureArray(draft.tags).includes(tagId);
    setDraft({
      ...draft,
      tags: has
        ? ensureArray(draft.tags).filter((t) => t !== tagId)
        : [...ensureArray(draft.tags), tagId],
    });
  };
  const submit = async () => {
    if (!draft.id || !draft.title) return;
    setBusy(true);
    try {
      await onCreate({
        id: draft.id,
        title: draft.title,
        description: draft.description,
        version: draft.version,
        topic: draft.topic,
        enabled: draft.enabled,
        tags: ensureArray(draft.tags),
      } as Partial<WorkbenchMeasure> & { id: string; title: string });
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className="py-3 bg-green-50/40 border-l-4 border-green-400 pl-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-gray-700">
          Identifier *
          <input
            type="text"
            value={draft.id || ""}
            onChange={(e) => setDraft({ ...draft, id: e.target.value })}
            placeholder="CMS204v1"
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700">
          Title *
          <input
            type="text"
            value={draft.title || ""}
            onChange={(e) => setDraft({ ...draft, title: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700">
          Version
          <input
            type="text"
            value={draft.version || ""}
            onChange={(e) => setDraft({ ...draft, version: e.target.value })}
            placeholder="1.0.0"
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700">
          Topic
          <input
            type="text"
            value={draft.topic || ""}
            onChange={(e) => setDraft({ ...draft, topic: e.target.value })}
            placeholder="Universal Foundation"
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700 md:col-span-2">
          Description
          <textarea
            value={draft.description || ""}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
            rows={2}
          />
        </label>
        <div className="text-xs text-gray-700 md:col-span-2">
          Tags
          <div className="flex flex-wrap gap-1 mt-1">
            {tags.map((t) => (
              <TagChip
                key={t.id}
                tag={t}
                active={ensureArray(draft.tags).includes(t.id)}
                onClick={() => toggleTag(t.id)}
              />
            ))}
          </div>
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy || !draft.id || !draft.title}
          className="px-3 py-1 text-xs rounded bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
        >
          Add
        </button>
      </div>
    </li>
  );
};

// ---------------------------------------------------------------------------
// Inline tag editor
// ---------------------------------------------------------------------------

interface TagRowProps {
  tag: WorkbenchTag;
  onSave: (next: WorkbenchTag) => Promise<void>;
  onDelete: () => Promise<void>;
  isNew?: boolean;
  onCancelNew?: () => void;
}

const TagRow: React.FC<TagRowProps> = ({ tag, onSave, onDelete, isNew, onCancelNew }) => {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(!!isNew);
  const [draft, setDraft] = useState<WorkbenchTag>(tag);

  useEffect(() => {
    if (!editing) setDraft(tag);
  }, [tag, editing]);

  const cancel = () => {
    if (isNew && onCancelNew) onCancelNew();
    else {
      setDraft(tag);
      setEditing(false);
    }
  };
  const submit = async () => {
    await onSave(draft);
    setEditing(false);
  };

  if (!editing) {
    const fg = readableTextOn(tag.color);
    return (
      <li
        className="py-2 flex items-center gap-3 flex-wrap"
        data-resource-kind="tag"
        data-resource-id={tag.id}
      >
        <button
          type="button"
          onClick={() => navigate(`/tags/${tag.id}`)}
          className="inline-block px-2 py-0.5 rounded text-[12px] font-medium hover:ring-2 hover:ring-blue-300"
          style={{ backgroundColor: tag.color, color: fg }}
          title="Open tag details"
        >
          {tag.name}
        </button>
        <span className="text-xs text-gray-500">{tag.id}</span>
        {tag.description && (
          <span className="text-xs text-gray-500 italic">{tag.description}</span>
        )}
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="ml-auto px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="px-2 py-1 text-xs rounded border border-red-300 text-red-700 hover:bg-red-50"
        >
          Delete
        </button>
      </li>
    );
  }

  return (
    <li className="py-2 bg-blue-50/40 border-l-4 border-blue-400 pl-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-gray-700">
          Name *
          <input
            type="text"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700">
          Identifier
          <input
            type="text"
            value={draft.id}
            disabled={!isNew}
            placeholder="auto-generated"
            onChange={(e) => setDraft({ ...draft, id: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded disabled:bg-gray-50 disabled:text-gray-500"
          />
        </label>
        <label className="text-xs text-gray-700 md:col-span-2">
          Description
          <input
            type="text"
            value={draft.description || ""}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <div className="text-xs text-gray-700 md:col-span-2">
          Colour (Okabe-Ito palette is colour-blind safe)
          <div className="flex flex-wrap gap-2 items-center mt-1">
            {OKABE_ITO.map((c) => (
              <button
                key={c}
                type="button"
                onClick={() => setDraft({ ...draft, color: c })}
                title={c}
                className={`w-7 h-7 rounded border-2 ${
                  draft.color?.toLowerCase() === c.toLowerCase()
                    ? "border-gray-900"
                    : "border-gray-200"
                }`}
                style={{ backgroundColor: c }}
              />
            ))}
            <input
              type="color"
              value={draft.color || "#000000"}
              onChange={(e) => setDraft({ ...draft, color: e.target.value })}
              className="w-9 h-9 border border-gray-300 rounded p-0"
              aria-label="Custom colour"
            />
            <input
              type="text"
              value={draft.color || ""}
              onChange={(e) => setDraft({ ...draft, color: e.target.value })}
              className="px-2 py-1 text-sm border border-gray-300 rounded w-28 font-mono"
            />
            <span
              className="px-2 py-0.5 rounded text-[12px] font-medium"
              style={{ backgroundColor: draft.color, color: readableTextOn(draft.color) }}
            >
              {draft.name || "Preview"}
            </span>
          </div>
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button
          type="button"
          onClick={cancel}
          className="px-3 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!draft.name}
          className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </li>
  );
};

// ---------------------------------------------------------------------------
// Inline agency editor (with nested programs)
// ---------------------------------------------------------------------------

interface AgencyCardProps {
  agency: WorkbenchAgency;
  measures: WorkbenchMeasure[];
  onSave: (next: WorkbenchAgency) => Promise<void>;
  onDelete: () => Promise<void>;
  isNew?: boolean;
  onCancelNew?: () => void;
}

const AgencyCard: React.FC<AgencyCardProps> = ({
  agency,
  measures,
  onSave,
  onDelete,
  isNew,
  onCancelNew,
}) => {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(!!isNew);
  const [draft, setDraft] = useState<WorkbenchAgency>(agency);
  const [busy, setBusy] = useState(false);
  // Accordion for the programs list in the read-only view; collapsed by default.
  const [programsOpen, setProgramsOpen] = useState(false);

  useEffect(() => {
    if (!editing) setDraft(agency);
  }, [agency, editing]);

  const cancel = () => {
    if (isNew && onCancelNew) onCancelNew();
    else {
      setDraft(agency);
      setEditing(false);
    }
  };

  const submit = async () => {
    setBusy(true);
    try {
      await onSave({ ...draft, programs: ensureArray(draft.programs) });
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  const updateProgram = (idx: number, patch: Partial<WorkbenchProgram>) => {
    const next = ensureArray(draft.programs).slice();
    next[idx] = { ...next[idx], ...patch };
    setDraft({ ...draft, programs: next });
  };

  const removeProgram = (idx: number) => {
    const next = ensureArray(draft.programs).slice();
    next.splice(idx, 1);
    setDraft({ ...draft, programs: next });
  };

  const addProgram = () => {
    setDraft({ ...draft, programs: [...ensureArray(draft.programs), emptyProgram()] });
  };

  const toggleRequired = (idx: number, measureId: string) => {
    const program = ensureArray(draft.programs)[idx];
    const has = ensureArray(program?.requiredMeasures).includes(measureId);
    updateProgram(idx, {
      requiredMeasures: has
        ? ensureArray(program.requiredMeasures).filter((m) => m !== measureId)
        : [...ensureArray(program.requiredMeasures), measureId],
    });
  };

  const displayPrograms: WorkbenchProgram[] = ensureArray(agency.programs);
  if (
    !displayPrograms.length &&
    (agency.reportingPeriod || (agency.requiredMeasures || []).length)
  ) {
    displayPrograms.push({
      id: `${agency.id}-default`,
      name: agency.shortName || "Default program",
      shortName: agency.shortName || "",
      description: "",
      reportingPeriod: agency.reportingPeriod,
      requiredMeasures: ensureArray(agency.requiredMeasures),
    });
  }

  if (!editing) {
    return (
      <li className="py-3" data-resource-kind="agency" data-resource-id={agency.id}>
        <div className="flex items-baseline gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => navigate(`/agencies/${agency.id}`)}
            className="font-medium text-gray-900 hover:text-blue-700 hover:underline text-left"
            title="Open agency details"
          >
            {agency.name}
          </button>
          {agency.shortName && agency.shortName !== agency.name && (
            <span className="text-xs text-gray-500">({agency.shortName})</span>
          )}
          {agency.country && (
            <span className="text-[10px] px-1 rounded bg-gray-100 text-gray-600">
              {agency.country}
            </span>
          )}
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="ml-auto px-2 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
          >
            Edit
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="px-2 py-1 text-xs rounded border border-red-300 text-red-700 hover:bg-red-50"
          >
            Delete
          </button>
        </div>
        {agency.description && (
          <p className="text-sm text-gray-600 mt-0.5">{agency.description}</p>
        )}
        {agency.website && (
          <a
            href={agency.website}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-blue-600 hover:underline"
          >
            {agency.website}
          </a>
        )}
        {displayPrograms.length === 0 ? (
          <p className="text-xs text-gray-500 mt-2">No programs defined.</p>
        ) : (
          <div className="mt-2">
            <button
              type="button"
              onClick={() => setProgramsOpen((v) => !v)}
              aria-expanded={programsOpen}
              className="inline-flex items-center gap-1 text-xs text-gray-700 hover:text-gray-900"
            >
              <span aria-hidden="true" className="inline-block w-3">
                {programsOpen ? "▼" : "▶"}
              </span>
              {displayPrograms.length === 1
                ? "1 program"
                : `${displayPrograms.length} programs`}
            </button>
            {programsOpen && (
              <ul className="mt-2 space-y-2">
                {displayPrograms.map((p) => (
                  <li
                    key={p.id || p.name}
                    className="text-sm bg-gray-50 border border-gray-200 rounded p-2"
                    data-resource-kind="program"
                    data-resource-id={p.id || ""}
                  >
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <button
                        type="button"
                        onClick={() => p.id && navigate(`/programs/${p.id}`)}
                        disabled={!p.id}
                        className="font-medium text-gray-800 hover:text-blue-700 hover:underline text-left disabled:hover:no-underline disabled:hover:text-gray-800"
                        title={p.id ? "Open program details" : undefined}
                      >
                        {p.name}
                      </button>
                      {p.shortName && p.shortName !== p.name && (
                        <span className="text-xs text-gray-500">({p.shortName})</span>
                      )}
                    </div>
                    {p.description && (
                      <p className="text-xs text-gray-600 mt-0.5">{p.description}</p>
                    )}
                    <p className="text-xs text-gray-500 mt-1">
                      Reporting period:{" "}
                      <strong>
                        {p.reportingPeriod?.start || "?"} → {p.reportingPeriod?.end || "?"}
                      </strong>
                    </p>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {ensureArray(p.requiredMeasures).map((mid) => (
                        <span
                          key={mid}
                          className="text-[11px] px-1.5 py-0.5 rounded border border-gray-300 text-gray-700 bg-white"
                          title="Required measure"
                        >
                          {mid}
                        </span>
                      ))}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </li>
    );
  }

  return (
    <li className="py-3 bg-blue-50/40 border-l-4 border-blue-400 pl-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-gray-700">
          Name *
          <input
            type="text"
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700">
          Short name
          <input
            type="text"
            value={draft.shortName || ""}
            onChange={(e) => setDraft({ ...draft, shortName: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
        <label className="text-xs text-gray-700 md:col-span-2">
          Description
          <textarea
            value={draft.description || ""}
            onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
            rows={2}
          />
        </label>
        <label className="text-xs text-gray-700">
          Website
          <input
            type="url"
            value={draft.website || ""}
            onChange={(e) => setDraft({ ...draft, website: e.target.value })}
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
            placeholder="https://www.cms.gov"
          />
        </label>
        <label className="text-xs text-gray-700">
          Country
          <input
            type="text"
            value={draft.country || ""}
            onChange={(e) => setDraft({ ...draft, country: e.target.value })}
            placeholder="US"
            className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
          />
        </label>
      </div>

      <div className="mt-3">
        <div className="flex items-center mb-2">
          <h4 className="text-xs font-medium text-gray-700">Programs</h4>
          <button
            type="button"
            onClick={addProgram}
            className="ml-auto px-2 py-1 text-xs rounded border border-blue-300 text-blue-700 hover:bg-blue-50"
          >
            + Add program
          </button>
        </div>
        <ul className="space-y-2">
          {ensureArray(draft.programs).map((p, idx) => (
            <li key={idx} className="border border-gray-200 rounded p-2 bg-white">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                <label className="text-xs text-gray-700">
                  Name *
                  <input
                    type="text"
                    value={p.name}
                    onChange={(e) => updateProgram(idx, { name: e.target.value })}
                    className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                  />
                </label>
                <label className="text-xs text-gray-700">
                  Short name
                  <input
                    type="text"
                    value={p.shortName || ""}
                    onChange={(e) => updateProgram(idx, { shortName: e.target.value })}
                    className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                  />
                </label>
                <label className="text-xs text-gray-700 md:col-span-2">
                  Description
                  <input
                    type="text"
                    value={p.description || ""}
                    onChange={(e) => updateProgram(idx, { description: e.target.value })}
                    className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                  />
                </label>
                <label className="text-xs text-gray-700">
                  Reporting period start
                  <input
                    type="date"
                    value={p.reportingPeriod?.start || ""}
                    onChange={(e) =>
                      updateProgram(idx, {
                        reportingPeriod: {
                          ...(p.reportingPeriod || {}),
                          start: e.target.value,
                        },
                      })
                    }
                    className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                  />
                </label>
                <label className="text-xs text-gray-700">
                  Reporting period end
                  <input
                    type="date"
                    value={p.reportingPeriod?.end || ""}
                    onChange={(e) =>
                      updateProgram(idx, {
                        reportingPeriod: {
                          ...(p.reportingPeriod || {}),
                          end: e.target.value,
                        },
                      })
                    }
                    className="block mt-1 w-full px-2 py-1 text-sm border border-gray-300 rounded"
                  />
                </label>
                <div className="text-xs text-gray-700 md:col-span-2">
                  Required measures
                  <div className="flex flex-wrap gap-2 mt-1">
                    {measures.map((m) => {
                      const has = ensureArray(p.requiredMeasures).includes(m.id);
                      return (
                        <button
                          key={m.id}
                          type="button"
                          onClick={() => toggleRequired(idx, m.id)}
                          className={`text-[11px] px-2 py-0.5 rounded border transition ${
                            has
                              ? "bg-blue-600 text-white border-blue-600"
                              : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
                          }`}
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
              </div>
              <div className="flex justify-end mt-2">
                <button
                  type="button"
                  onClick={() => removeProgram(idx)}
                  className="px-2 py-1 text-xs rounded border border-red-300 text-red-700 hover:bg-red-50"
                >
                  Remove program
                </button>
              </div>
            </li>
          ))}
          {ensureArray(draft.programs).length === 0 && (
            <li className="text-xs text-gray-500">No programs yet.</li>
          )}
        </ul>
      </div>

      <div className="flex justify-end gap-2 mt-3">
        <button
          type="button"
          onClick={cancel}
          className="px-3 py-1 text-xs rounded border border-gray-300 hover:border-gray-500"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy || !draft.name}
          className="px-3 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Save
        </button>
      </div>
    </li>
  );
};

// ---------------------------------------------------------------------------
// CatalogPage
// ---------------------------------------------------------------------------

interface CatalogPageProps {
  initialSection?: CatalogSection;
  focusKind?: "program";
}

const CatalogPage: React.FC<CatalogPageProps> = ({ initialSection, focusKind }) => {
  const navigate = useNavigate();
  const { focusId } = useParams<{ focusId?: string }>();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [section, setSection] = useState<CatalogSection>(initialSection ?? "measures");
  const [measures, setMeasures] = useState<WorkbenchMeasure[]>([]);
  const [tags, setTags] = useState<WorkbenchTag[]>([]);
  const [agencies, setAgencies] = useState<WorkbenchAgency[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filterTag, setFilterTag] = useState<string>("");
  const [search, setSearch] = useState("");
  const [busyMeasureId, setBusyMeasureId] = useState<string | null>(null);
  const [sampleStatus, setSampleStatus] = useState<Record<string, string>>({});
  const [addingMeasure, setAddingMeasure] = useState(false);
  const [addingTag, setAddingTag] = useState(false);
  const [addingAgency, setAddingAgency] = useState(false);
  const [agencyFilter, setAgencyFilter] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, t, a] = await Promise.all([
        listWorkbenchMeasures(),
        listWorkbenchTags(),
        listWorkbenchAgencies(),
      ]);
      setMeasures(m);
      setTags(t);
      setAgencies(a);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load catalog.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (initialSection) setSection(initialSection);
  }, [initialSection]);

  useEffect(() => {
    if (!focusId || loading) return;
    const kind = focusKind === "program" ? "program" : section;
    const root = containerRef.current;
    if (!root) return;
    const el = root.querySelector<HTMLElement>(
      `[data-resource-kind="${kind}"][data-resource-id="${focusId}"]`,
    );
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("ring-2", "ring-blue-400", "rounded");
    const t = window.setTimeout(() => {
      el.classList.remove("ring-2", "ring-blue-400", "rounded");
    }, 1800);
    return () => window.clearTimeout(t);
  }, [focusId, focusKind, loading, section, measures, tags, agencies]);

  const filteredMeasures = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return measures
      .filter((m) => (filterTag ? ensureArray(m.tags).includes(filterTag) : true))
      .filter((m) => {
        if (!needle) return true;
        const hay = [
          m.id,
          m.customName || "",
          m.title,
          m.customDescription || "",
          m.description || "",
          m.topic || "",
        ]
          .join(" ")
          .toLowerCase();
        return hay.includes(needle);
      });
  }, [measures, filterTag, search]);

  const filteredAgencies = useMemo(() => {
    const needle = agencyFilter.trim().toLowerCase();
    if (!needle) return agencies;
    return agencies.filter((a) => {
      const hay = [
        a.name,
        a.shortName,
        a.description,
        a.country,
        a.website,
        ...ensureArray(a.programs).flatMap((p) => [
          p.name,
          p.shortName,
          p.description,
          ...ensureArray(p.requiredMeasures),
        ]),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [agencies, agencyFilter]);

  // ---------------- Measures ----------------

  const onSaveMeasure = async (m: WorkbenchMeasure, patch: Partial<WorkbenchMeasure>) => {
    setBusyMeasureId(m.id);
    try {
      const updated = await updateWorkbenchMeasure(m.id, patch);
      setMeasures((prev) => prev.map((x) => (x.id === m.id ? updated : x)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update measure.");
      throw e;
    } finally {
      setBusyMeasureId(null);
    }
  };

  const onGenerateSamples = async (m: WorkbenchMeasure) => {
    setBusyMeasureId(m.id);
    setSampleStatus((s) => ({ ...s, [m.id]: "Generating…" }));
    try {
      const result = await generateMeasureSampleData(m.id);
      setSampleStatus((s) => ({
        ...s,
        [m.id]: `Seeded ${result.seeded.length} member(s) into cohort '${result.cohortId}'.`,
      }));
    } catch (e) {
      setSampleStatus((s) => ({
        ...s,
        [m.id]: e instanceof Error ? e.message : "Failed to generate samples.",
      }));
    } finally {
      setBusyMeasureId(null);
    }
  };

  const onCreateMeasure = async (
    m: Partial<WorkbenchMeasure> & { id: string; title: string },
  ) => {
    try {
      await addWorkbenchMeasure(m);
      setAddingMeasure(false);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add measure.");
    }
  };

  const onDeleteMeasure = async (m: WorkbenchMeasure) => {
    if (m.builtin) return;
    if (!window.confirm(`Delete measure ${m.id}?`)) return;
    try {
      await deleteWorkbenchMeasure(m.id);
      setMeasures((prev) => prev.filter((x) => x.id !== m.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete measure.");
    }
  };

  // ---------------- Tags ----------------

  const onSaveTag = async (t: WorkbenchTag) => {
    try {
      await upsertWorkbenchTag(t);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save tag.");
      throw e;
    }
  };

  const onDeleteTag = async (t: WorkbenchTag) => {
    if (!window.confirm(`Delete tag '${t.name}'?`)) return;
    try {
      await deleteWorkbenchTag(t.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete tag.");
    }
  };

  // ---------------- Agencies ----------------

  const onSaveAgency = async (a: WorkbenchAgency) => {
    try {
      await upsertWorkbenchAgency(a);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save agency.");
      throw e;
    }
  };

  const onDeleteAgency = async (a: WorkbenchAgency) => {
    if (!window.confirm(`Delete agency '${a.name}'?`)) return;
    try {
      await deleteWorkbenchAgency(a.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete agency.");
    }
  };

  return (
    <div className="text-left mb-24" ref={containerRef}>
      <div className="px-3 lg:px-0">
        <h2 className="text-xl font-normal text-gray-700 mb-1">Catalog</h2>
        <p className="text-sm text-gray-500 mb-4">
          Configure quality measures, tags, and regulatory agencies. All catalog
          entries are stored in <code>dq/catalog</code> and shared across cohorts.
        </p>
      </div>

      <nav className="flex flex-wrap gap-2 mb-4" aria-label="Catalog sections">
        {SECTION_LABELS.map((s) => {
          const active = section === s.id;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                setSection(s.id);
                if (focusId) navigate("/catalog");
              }}
              title={s.help}
              className={`px-3 py-1.5 text-sm rounded border transition ${
                active
                  ? "bg-blue-600 text-white border-blue-600"
                  : "bg-white text-gray-700 border-gray-300 hover:border-gray-500"
              }`}
            >
              {s.label}
            </button>
          );
        })}
      </nav>

      {loading && <p className="text-sm text-gray-500">Loading catalog…</p>}
      {error && (
        <div className="my-2 p-2 text-sm rounded bg-red-50 text-red-800 border border-red-200">
          {error}
        </div>
      )}

      {section === "measures" && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex flex-wrap items-center gap-3 mb-4">
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search measures…"
              className="px-3 py-1.5 text-sm border border-gray-300 rounded w-64"
            />
            <select
              value={filterTag}
              onChange={(e) => setFilterTag(e.target.value)}
              className="px-3 py-1.5 text-sm border border-gray-300 rounded"
            >
              <option value="">All tags</option>
              {tags.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => setAddingMeasure(true)}
              className="ml-auto px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
            >
              + Add measure
            </button>
          </div>

          <ul className="divide-y divide-gray-100">
            {addingMeasure && (
              <NewMeasureRow
                tags={tags}
                onCancel={() => setAddingMeasure(false)}
                onCreate={onCreateMeasure}
              />
            )}
            {filteredMeasures.map((m) => (
              <MeasureRow
                key={m.id}
                measure={m}
                tags={tags}
                onSave={(patch) => onSaveMeasure(m, patch)}
                onDelete={() => onDeleteMeasure(m)}
                onGenerateSamples={() => onGenerateSamples(m)}
                busy={busyMeasureId === m.id}
                sampleStatus={sampleStatus[m.id]}
              />
            ))}
            {!addingMeasure && filteredMeasures.length === 0 && (
              <li className="py-4 text-sm text-gray-500">
                No measures match the current filters.
              </li>
            )}
          </ul>
        </div>
      )}

      {section === "tags" && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center mb-4">
            <h3 className="text-sm font-medium text-gray-700">Tags</h3>
            <button
              type="button"
              onClick={() => setAddingTag(true)}
              className="ml-auto px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
            >
              + Add tag
            </button>
          </div>
          <ul className="divide-y divide-gray-100">
            {addingTag && (
              <TagRow
                tag={{ id: "", name: "", color: "#0072B2", description: "" }}
                onSave={async (next) => {
                  await onSaveTag(next);
                  setAddingTag(false);
                }}
                onDelete={async () => setAddingTag(false)}
                isNew
                onCancelNew={() => setAddingTag(false)}
              />
            )}
            {tags.map((t) => (
              <TagRow
                key={t.id}
                tag={t}
                onSave={onSaveTag}
                onDelete={() => onDeleteTag(t)}
              />
            ))}
            {!addingTag && tags.length === 0 && (
              <li className="py-4 text-sm text-gray-500">No tags yet.</li>
            )}
          </ul>
        </div>
      )}

      {section === "agencies" && (
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center mb-4">
            <div>
              <h3 className="text-sm font-medium text-gray-700">Regulatory agencies</h3>
              <p className="text-xs text-gray-500">
                Programs and their required measures. Stored as{" "}
                <code>docType=agency</code> in <code>dq/catalog</code>.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setAddingAgency(true)}
              className="ml-auto px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
            >
              + Add agency
            </button>
          </div>
          <div className="mb-3">
            <input
              type="search"
              value={agencyFilter}
              onChange={(e) => setAgencyFilter(e.target.value)}
              placeholder="Filter agencies by name, program, description, or measure ID…"
              className="w-full px-2 py-1.5 text-sm border border-gray-300 rounded focus:outline-none focus:border-blue-500"
              aria-label="Filter regulatory agencies"
            />
          </div>
          <ul className="divide-y divide-gray-100">
            {addingAgency && (
              <AgencyCard
                agency={{
                  id: "",
                  name: "",
                  shortName: "",
                  description: "",
                  website: "",
                  country: "US",
                  programs: [emptyProgram()],
                }}
                measures={measures}
                onSave={async (next) => {
                  await onSaveAgency(next);
                  setAddingAgency(false);
                }}
                onDelete={async () => setAddingAgency(false)}
                isNew
                onCancelNew={() => setAddingAgency(false)}
              />
            )}
            {filteredAgencies.map((a) => (
              <AgencyCard
                key={a.id}
                agency={a}
                measures={measures}
                onSave={onSaveAgency}
                onDelete={() => onDeleteAgency(a)}
              />
            ))}
            {!addingAgency && agencies.length === 0 && (
              <li className="py-4 text-sm text-gray-500">No agencies yet.</li>
            )}
            {!addingAgency &&
              agencies.length > 0 &&
              filteredAgencies.length === 0 && (
                <li className="py-4 text-sm text-gray-500">
                  No agencies match &ldquo;{agencyFilter}&rdquo;.
                </li>
              )}
          </ul>
        </div>
      )}
    </div>
  );
};

export default CatalogPage;
