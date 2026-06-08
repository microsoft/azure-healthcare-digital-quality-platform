import { getAuthToken } from "./index";
import { githubDevSubsPort } from "../utils/ghutils";

/**
 * Quality Measures API client (FHIR R4, Da Vinci DEQM-aligned).
 *
 * Targets the backend routes described in
 * `azure-healthcare-digital-quality-backend/.speckit/spec_backend.md` §7.
 * All requests carry the caller's Entra ID bearer token.
 */

// ---------------------------------------------------------------------------
// Types (shapes we consume from the backend - not full FHIR typings)
// ---------------------------------------------------------------------------

export interface FhirCoding {
  system?: string;
  code?: string;
  display?: string;
}

export interface FhirCodeableConcept {
  coding?: FhirCoding[];
  text?: string;
}

export interface FhirCodeFilter {
  path?: string;
  searchParam?: string;
  valueSet?: string;
  code?: FhirCoding[];
}

export interface FhirDataRequirement {
  type: string;
  profile?: string[];
  mustSupport?: string[];
  codeFilter?: FhirCodeFilter[];
  dateFilter?: Array<{ path?: string; valuePeriod?: { start?: string; end?: string } }>;
}

export interface FhirLibrary {
  resourceType: "Library";
  id?: string;
  url?: string;
  version?: string;
  title?: string;
  status?: string;
  type?: FhirCodeableConcept;
  effectivePeriod?: { start?: string; end?: string };
  dataRequirement?: FhirDataRequirement[];
  relatedArtifact?: Array<{ type?: string; resource?: string }>;
  content?: Array<{ contentType?: string }>;
}

export interface FhirMeasure {
  resourceType: "Measure";
  id: string;
  url?: string;
  version?: string;
  title?: string;
  status?: string;
  description?: string;
  scoring?: FhirCodeableConcept;
  topic?: Array<FhirCodeableConcept>;
  library?: string[];
}

export interface FhirBundleEntry<T = unknown> {
  resource?: T;
  fullUrl?: string;
}

export interface FhirBundle<T = unknown> {
  resourceType: "Bundle";
  id?: string;
  type?: string;
  total?: number;
  timestamp?: string;
  entry?: Array<FhirBundleEntry<T>>;
}

export interface FhirMeasureReportPopulation {
  code?: FhirCodeableConcept;
  count?: number;
}

export interface FhirMeasureReportGroup {
  population?: FhirMeasureReportPopulation[];
}

export interface FhirMeasureReport {
  resourceType: "MeasureReport";
  id?: string;
  status?: string;
  type?: string;
  measure?: string;
  subject?: { reference?: string };
  date?: string;
  period?: { start?: string; end?: string };
  group?: FhirMeasureReportGroup[];
  extension?: Array<{ url?: string; valueString?: string }>;
  contained?: unknown[];
}

export interface FhirParameter {
  name: string;
  valueString?: string;
  valueDate?: string;
  valueDateTime?: string;
  valueCode?: string;
  valueBoolean?: boolean;
  resource?: unknown;
}

export interface FhirParameters {
  resourceType: "Parameters";
  parameter?: FhirParameter[];
}

export interface FhirOperationOutcome {
  resourceType: "OperationOutcome";
  issue?: Array<{ severity?: string; code?: string; diagnostics?: string }>;
}

export interface CapabilityStatement {
  resourceType: "CapabilityStatement";
  status?: string;
  date?: string;
  publisher?: string;
  fhirVersion?: string;
  rest?: Array<{
    mode?: string;
    resource?: Array<{
      type?: string;
      interaction?: Array<{ code?: string }>;
      operation?: Array<{ name?: string; definition?: string }>;
    }>;
  }>;
}

// ---------------------------------------------------------------------------
// Request helpers
// ---------------------------------------------------------------------------

function getFhirBase(): string {
  const hostname = window.location.hostname;
  const apiPort = 8000;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    const base = apiBaseUrl || `http://localhost:${apiPort}`;
    return base.endsWith("/") ? `${base}fhir` : `${base}/fhir`;
  }
  if (hostname.endsWith("github.dev")) {
    const base = githubDevSubsPort(hostname, apiPort);
    return base.endsWith("/") ? `${base}fhir` : `${base}/fhir`;
  }
  return "/fhir";
}

async function fhirFetch<T = unknown>(
  path: string,
  init?: RequestInit & { requireAuth?: boolean },
): Promise<T> {
  const url = `${getFhirBase()}${path.startsWith("/") ? "" : "/"}${path}`;
  const requireAuth = init?.requireAuth !== false;

  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/fhir+json");
  }
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/fhir+json");
  }

  if (requireAuth) {
    const token = await getAuthToken();
    if (token) {
      headers.set("Authorization", `Bearer ${token}`);
    }
  }

  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) {
    throw new Error("Authentication failed. Please log in again.");
  }
  const text = await response.text();
  let parsed: unknown = text;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      // leave as text on non-JSON payloads
    }
  }
  if (!response.ok) {
    const outcome = parsed as FhirOperationOutcome | undefined;
    const diag =
      outcome?.issue?.[0]?.diagnostics ||
      (typeof parsed === "string" ? parsed : undefined) ||
      `${response.status} ${response.statusText}`;
    throw new Error(`FHIR ${response.status}: ${diag}`);
  }
  return parsed as T;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export async function fetchCapabilityStatement(): Promise<CapabilityStatement> {
  return fhirFetch<CapabilityStatement>("/metadata", { requireAuth: false });
}

export async function listMeasures(): Promise<FhirMeasure[]> {
  const bundle = await fhirFetch<FhirBundle<FhirMeasure>>("/Measure");
  return (bundle.entry || [])
    .map((e) => e.resource)
    .filter((r): r is FhirMeasure => !!r && r.resourceType === "Measure");
}

export async function fetchDataRequirements(
  measureId: string,
  periodStart?: string,
  periodEnd?: string,
): Promise<FhirLibrary> {
  const params: FhirParameter[] = [];
  if (periodStart) params.push({ name: "periodStart", valueDate: periodStart });
  if (periodEnd) params.push({ name: "periodEnd", valueDate: periodEnd });
  const body: FhirParameters = { resourceType: "Parameters", parameter: params };

  return fhirFetch<FhirLibrary>(`/Measure/${encodeURIComponent(measureId)}/$data-requirements`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function evaluateMeasure(
  measureId: string,
  subjectId: string,
  periodStart: string,
  periodEnd: string,
  engine: "native-cql" | "ai-cql",
  cohortId?: string,
): Promise<FhirMeasureReport> {
  const params: FhirParameter[] = [
    { name: "subject", valueString: subjectId },
    { name: "periodStart", valueDate: periodStart },
    { name: "periodEnd", valueDate: periodEnd },
    { name: "engine", valueCode: engine },
  ];
  if (cohortId) {
    params.push({ name: "cohortId", valueString: cohortId });
  }
  const body: FhirParameters = {
    resourceType: "Parameters",
    parameter: params,
  };
  const qs = cohortId ? `?cohortId=${encodeURIComponent(cohortId)}` : "";
  return fhirFetch<FhirMeasureReport>(
    `/Measure/${encodeURIComponent(measureId)}/$evaluate-measure${qs}`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

export async function collectData(
  measureId: string,
  subjectId: string,
  periodStart?: string,
  periodEnd?: string,
): Promise<FhirParameters> {
  const params: FhirParameter[] = [{ name: "subject", valueString: subjectId }];
  if (periodStart) params.push({ name: "periodStart", valueDate: periodStart });
  if (periodEnd) params.push({ name: "periodEnd", valueDate: periodEnd });
  const body: FhirParameters = { resourceType: "Parameters", parameter: params };

  return fhirFetch<FhirParameters>(`/Measure/${encodeURIComponent(measureId)}/$collect-data`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function submitData(
  measureId: string,
  parameters: FhirParameters,
): Promise<{ location?: string; outcome: FhirOperationOutcome }> {
  const url = `${getFhirBase()}/Measure/${encodeURIComponent(measureId)}/$submit-data`;
  const token = await getAuthToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/fhir+json",
    Accept: "application/fhir+json",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(parameters),
  });
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
    const outcome = parsed as FhirOperationOutcome | undefined;
    const diag = outcome?.issue?.[0]?.diagnostics || `${response.status} ${response.statusText}`;
    throw new Error(`FHIR ${response.status}: ${diag}`);
  }
  return {
    location: response.headers.get("Location") || undefined,
    outcome: parsed as FhirOperationOutcome,
  };
}
