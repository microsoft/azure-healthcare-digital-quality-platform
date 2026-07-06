/**
 * Quality Measures Workbench API client.
 *
 * Wraps the FastAPI router exposed by `backend/src/workbench.py` at
 * `/api/workbench/...`. Two logical surfaces:
 *
 * - **Catalog** — measures, tags, regulatory agencies (programs).
 * - **Cohorts** — cohort definitions, member lists, regulatory submissions.
 *
 * All requests carry the caller's Entra ID bearer token via
 * `getAuthToken()` from `./index`.
 */

import { getAuthToken } from "./index";
import { githubDevSubsPort } from "../utils/ghutils";
import type { FhirDataRequirement } from "./qualityMeasures";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface WorkbenchMeasure {
  id: string;
  title: string;
  description?: string;
  version?: string;
  topic?: string;
  enabled: boolean;
  customName?: string | null;
  customDescription?: string | null;
  tags: string[];
  cqlLibrary?: string | null;
  dataRequirements?: FhirDataRequirement[];
  builtin: boolean;
  createdAt?: number;
  updatedAt?: number;
}

export interface WorkbenchTag {
  id: string;
  name: string;
  color: string;
  description?: string;
}

export interface WorkbenchProgram {
  id?: string;
  name: string;
  shortName?: string;
  description?: string;
  reportingPeriod?: { start?: string; end?: string };
  requiredMeasures: string[];
}

export interface WorkbenchAgency {
  id: string;
  name: string;
  shortName?: string;
  description?: string;
  website?: string;
  country?: string;
  programs: WorkbenchProgram[];
  // Legacy single-program fields kept for older docs.
  reportingPeriod?: { start?: string; end?: string };
  requiredMeasures?: string[];
}

export interface WorkbenchCohort {
  id: string;
  name: string;
  description?: string;
  memberIds: string[];
  tags: string[];
  measureIds?: string[];
  builtin?: boolean;
  createdAt?: number;
  updatedAt?: number;
  source?: string;
  lastReceivedAt?: number;
  lastReceivedSubmissionId?: string;
}

export interface WorkbenchMember {
  id: string;
  displayName?: string;
  birthDate?: string;
  gender?: string;
  patientResourceId?: string;
}

export interface WorkbenchSubmissionMeasurement {
  id?: string;
  memberId?: string;
  measureIds?: string[];
  status?: string;
  httpStatus?: number;
  error?: string;
  createdAt?: number;
  report?: unknown;
  cohortId?: string;
  sourceStack?: string;
}

export interface CohortMeasurementHistoryRow {
  id: string;
  source: "submission" | "direct" | string;
  cohortId?: string | null;
  memberId: string;
  measureId: string;
  engine?: string | null;
  submissionId?: string | null;
  sourceStack?: string | null;
  status: string;
  httpStatus?: number | null;
  numerator?: number | null;
  denominator?: number | null;
  exclusion?: boolean | null;
  note?: string | null;
  error?: string | null;
  reportId?: string | null;
  createdAt: number;
}

export interface WorkbenchSubmission {
  id: string;
  cohortId: string;
  agencyId?: string;
  measureIds: string[];
  note?: string;
  status: string;
  createdAt: number;
  updatedAt?: number;
  memberCount?: number;
  startedAt?: number;
  completedAt?: number;
  summary?: { completed?: number; failed?: number; skipped?: number };
  dispatch?: { mode?: string; jobName?: string; namespace?: string; error?: string };
  sourceStack?: string;
  sourceSubmissionId?: string;
}

// ---------------------------------------------------------------------------
// Request helpers
// ---------------------------------------------------------------------------

function getApiBase(): string {
  const hostname = window.location.hostname;
  const apiPort = 8000;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    return (apiBaseUrl || `http://localhost:${apiPort}`).replace(/\/$/, "");
  }
  if (hostname.endsWith("github.dev")) {
    return githubDevSubsPort(hostname, apiPort).replace(/\/$/, "");
  }
  return "";
}

async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getApiBase()}/api/workbench${path}`;
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const token = await getAuthToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const response = await fetch(url, { ...init, headers });
  const text = await response.text();
  let parsed: unknown = text;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      /* keep text */
    }
  }
  if (!response.ok) {
    const detail =
      (parsed as { detail?: string } | undefined)?.detail ||
      (typeof parsed === "string" ? parsed : `${response.status} ${response.statusText}`);
    throw new Error(`Workbench ${response.status}: ${detail}`);
  }
  return parsed as T;
}

// ---------------------------------------------------------------------------
// Catalog: Measures
// ---------------------------------------------------------------------------

export async function listWorkbenchMeasures(): Promise<WorkbenchMeasure[]> {
  const data = await api<{ measures: WorkbenchMeasure[] }>("/catalog/measures");
  return data.measures || [];
}

export async function addWorkbenchMeasure(measure: Partial<WorkbenchMeasure> & { id: string; title: string }) {
  const data = await api<{ measure: WorkbenchMeasure }>("/catalog/measures", {
    method: "POST",
    body: JSON.stringify(measure),
  });
  return data.measure;
}

export async function updateWorkbenchMeasure(
  measureId: string,
  patch: Partial<
    Pick<
      WorkbenchMeasure,
      "enabled" | "customName" | "customDescription" | "tags" | "dataRequirements"
    >
  >,
) {
  const data = await api<{ measure: WorkbenchMeasure }>(
    `/catalog/measures/${encodeURIComponent(measureId)}`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
  return data.measure;
}

export async function deleteWorkbenchMeasure(measureId: string) {
  return api<{ deleted: string }>(
    `/catalog/measures/${encodeURIComponent(measureId)}`,
    { method: "DELETE" },
  );
}

export async function generateMeasureSampleData(measureId: string) {
  return api<{ measureId: string; cohortId: string; seeded: string[] }>(
    `/catalog/measures/${encodeURIComponent(measureId)}/sample-data`,
    { method: "POST", body: "{}" },
  );
}

// ---------------------------------------------------------------------------
// Catalog: Tags
// ---------------------------------------------------------------------------

export async function listWorkbenchTags(): Promise<WorkbenchTag[]> {
  const data = await api<{ tags: WorkbenchTag[] }>("/catalog/tags");
  return data.tags || [];
}

export async function upsertWorkbenchTag(tag: Partial<WorkbenchTag> & { name: string }) {
  const data = await api<{ tag: WorkbenchTag }>("/catalog/tags", {
    method: "POST",
    body: JSON.stringify(tag),
  });
  return data.tag;
}

export async function deleteWorkbenchTag(tagId: string) {
  return api<{ deleted: string }>(`/catalog/tags/${encodeURIComponent(tagId)}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Catalog: Agencies (programs)
// ---------------------------------------------------------------------------

export async function listWorkbenchAgencies(): Promise<WorkbenchAgency[]> {
  const data = await api<{ agencies: WorkbenchAgency[] }>("/catalog/agencies");
  return data.agencies || [];
}

export async function upsertWorkbenchAgency(agency: Partial<WorkbenchAgency> & { name: string }) {
  const data = await api<{ agency: WorkbenchAgency }>("/catalog/agencies", {
    method: "POST",
    body: JSON.stringify(agency),
  });
  return data.agency;
}

export async function deleteWorkbenchAgency(agencyId: string) {
  return api<{ deleted: string }>(`/catalog/agencies/${encodeURIComponent(agencyId)}`, {
    method: "DELETE",
  });
}

// ---------------------------------------------------------------------------
// Cohorts
// ---------------------------------------------------------------------------

export async function listWorkbenchCohorts(): Promise<WorkbenchCohort[]> {
  const data = await api<{ cohorts: WorkbenchCohort[] }>("/cohorts");
  return data.cohorts || [];
}

export async function upsertWorkbenchCohort(cohort: Partial<WorkbenchCohort> & { name: string }) {
  const data = await api<{ cohort: WorkbenchCohort }>("/cohorts", {
    method: "POST",
    body: JSON.stringify(cohort),
  });
  return data.cohort;
}

export async function deleteWorkbenchCohort(cohortId: string) {
  return api<{ deleted: string }>(`/cohorts/${encodeURIComponent(cohortId)}`, {
    method: "DELETE",
  });
}

export async function updateCohortMembers(
  cohortId: string,
  patch: { add?: string[]; remove?: string[] },
) {
  const data = await api<{ cohort: WorkbenchCohort }>(
    `/cohorts/${encodeURIComponent(cohortId)}/members`,
    { method: "POST", body: JSON.stringify({ add: patch.add || [], remove: patch.remove || [] }) },
  );
  return data.cohort;
}

export async function listWorkbenchMembers(): Promise<WorkbenchMember[]> {
  const data = await api<{ members: WorkbenchMember[] }>("/members");
  return data.members || [];
}

// ---------------------------------------------------------------------------
// Tag rendering helpers
// ---------------------------------------------------------------------------

/**
 * Pick a readable foreground colour (near-black or white) for a hex
 * background using the WCAG relative-luminance formula. Works for any
 * user-entered hex, so custom colours stay accessible without manual
 * intervention.
 */
export function readableTextOn(hex: string | undefined | null): string {
  const fallback = "#111827";
  if (!hex) return fallback;
  let h = hex.trim().replace("#", "");
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  if (h.length !== 6) return fallback;
  const r = parseInt(h.substring(0, 2), 16) / 255;
  const g = parseInt(h.substring(2, 4), 16) / 255;
  const b = parseInt(h.substring(4, 6), 16) / 255;
  if ([r, g, b].some((v) => Number.isNaN(v))) return fallback;
  // Relative luminance, sRGB.
  const lin = (v: number) => (v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4));
  const luminance = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  return luminance > 0.5 ? "#111827" : "#ffffff";
}

// ---------------------------------------------------------------------------
// Submissions
// ---------------------------------------------------------------------------

export async function submitWorkbenchData(payload: {
  cohortId: string;
  agencyId: string;
  measureIds: string[];
  note?: string;
}) {
  const data = await api<{ submission: WorkbenchSubmission }>("/submissions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return data.submission;
}

export async function listWorkbenchSubmissions(): Promise<WorkbenchSubmission[]> {
  const data = await api<{ submissions: WorkbenchSubmission[] }>("/submissions");
  return data.submissions || [];
}

export async function getWorkbenchSubmission(
  submissionId: string,
): Promise<{ submission: WorkbenchSubmission; measurements: WorkbenchSubmissionMeasurement[] }> {
  const data = await api<{
    submission: WorkbenchSubmission;
    measurements: WorkbenchSubmissionMeasurement[];
  }>(`/submissions/${encodeURIComponent(submissionId)}`);
  return { submission: data.submission, measurements: data.measurements || [] };
}

export async function listCohortMeasurementHistory(
  cohortId: string,
  options?: { memberId?: string; source?: "submission" | "direct"; limit?: number },
): Promise<CohortMeasurementHistoryRow[]> {
  const params = new URLSearchParams();
  if (options?.memberId) params.set("member_id", options.memberId);
  if (options?.source) params.set("source", options.source);
  if (options?.limit) params.set("limit", String(options.limit));
  const qs = params.toString();
  const path = `/cohorts/${encodeURIComponent(cohortId)}/measurement-history${qs ? `?${qs}` : ""}`;
  const data = await api<{ history: CohortMeasurementHistoryRow[] }>(path);
  return data.history || [];
}

// ---------------------------------------------------------------------------
// Measure summary send (submitters -> receivers + platform)
// ---------------------------------------------------------------------------

export interface MeasureSummaryPerMember {
  measureId: string;
  numerator?: number | null;
  denominator?: number | null;
  exclusion?: boolean | null;
}

export interface MeasureSummaryMember {
  memberId: string;
  displayName?: string | null;
  perMeasure: MeasureSummaryPerMember[];
}

export interface MeasureSummaryRollup {
  measureId: string;
  title?: string;
  denominator: number;
  numerator: number;
  exclusions: number;
  patients: number;
  performanceRate?: number | null;
}

export interface MeasureSummaryPayload {
  id?: string;
  sourceStack?: string;
  sourceSendId?: string;
  sourceSubmissionId?: string | null;
  agency: { id?: string; name?: string; shortName?: string };
  program: { id?: string; name?: string; shortName?: string };
  cohort: { id: string; name?: string; memberCount?: number };
  periodStart?: string | null;
  periodEnd?: string | null;
  engine?: string | null;
  measureIds: string[];
  perMeasure: MeasureSummaryRollup[];
  perMember: MeasureSummaryMember[];
  note?: string;
  generatedAt?: number;
}

export interface MeasureSummaryDispatchTarget {
  target: string;
  url: string;
  status: "pending" | "sent" | "failed" | "rejected" | string;
  statusCode?: number;
  remoteSummaryId?: string;
  error?: string;
}

export interface MeasureSummarySend {
  id: string;
  docType?: string;
  cohortId: string;
  agencyId: string;
  programId?: string | null;
  measureIds: string[];
  periodStart?: string | null;
  periodEnd?: string | null;
  note?: string;
  status: "sent" | "partial" | "failed" | string;
  createdAt: number;
  dispatch: {
    receivers: MeasureSummaryDispatchTarget;
    platform: MeasureSummaryDispatchTarget;
  };
  summary?: MeasureSummaryPayload;
  reportType?: string;
}

export async function sendCohortMeasureSummary(
  cohortId: string,
  body: {
    agencyId: string;
    programId?: string;
    measureIds?: string[];
    periodStart?: string;
    periodEnd?: string;
    note?: string;
    engine?: string;
    sourceSubmissionId?: string;
  },
): Promise<MeasureSummarySend> {
  const data = await api<{ send: MeasureSummarySend }>(
    `/cohorts/${encodeURIComponent(cohortId)}/measure-summary/send`,
    { method: "POST", body: JSON.stringify(body) },
  );
  return data.send;
}

export async function listCohortMeasureSummarySends(
  cohortId: string,
): Promise<MeasureSummarySend[]> {
  const data = await api<{ sends: MeasureSummarySend[] }>(
    `/cohorts/${encodeURIComponent(cohortId)}/measure-summary/sends`,
  );
  return data.sends || [];
}

export async function getMeasureSummarySend(
  sendId: string,
): Promise<MeasureSummarySend> {
  const data = await api<{ send: MeasureSummarySend }>(
    `/measure-summary/sends/${encodeURIComponent(sendId)}`,
  );
  return data.send;
}

// ---------------------------------------------------------------------------
// DEQM MeasureReport send (submitters -> receivers) with reportType switching.
// Unlike the legacy proprietary summary send above, this builds a
// standards-conformant FHIR MeasureReport whose `type` is driven by
// `reportType` (individual | subject-list | summary) and dispatches it to the
// receivers' /measure-reports ingest route.
// ---------------------------------------------------------------------------

export type DeqmReportType = "individual" | "subject-list" | "summary";

export async function sendCohortMeasureReport(
  cohortId: string,
  reportType: DeqmReportType,
  body: {
    agencyId: string;
    programId?: string;
    measureIds?: string[];
    periodStart?: string;
    periodEnd?: string;
    note?: string;
    engine?: string;
    sourceSubmissionId?: string;
  },
): Promise<MeasureSummarySend> {
  const data = await api<{ send: MeasureSummarySend; reportType: string }>(
    `/cohorts/${encodeURIComponent(cohortId)}/measure-reports?reportType=${encodeURIComponent(reportType)}`,
    { method: "POST", body: JSON.stringify({ ...body, reportType }) },
  );
  return data.send;
}

export async function listCohortMeasureReportSends(
  cohortId: string,
): Promise<MeasureSummarySend[]> {
  const data = await api<{ sends: MeasureSummarySend[] }>(
    `/cohorts/${encodeURIComponent(cohortId)}/measure-reports/sends`,
  );
  return data.sends || [];
}
