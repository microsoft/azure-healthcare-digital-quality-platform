/**
 * samplePatients.ts
 * Provider-side helpers for SOAP notes + sample patient bundles (mirrors consumers store).
 * Uses the same auth/base-URL pattern as workbench.ts so it shares the providers MSAL token.
 */

import { getAuthToken } from "./index";

function githubDevSubsPort(hostname: string, port: number): string {
  // Codespaces / github.dev: replace `-<port>` segment, fall back to https://<host>:port
  const match = hostname.match(/^(.*)-(\d+)\.app\.github\.dev$/);
  if (match) {
    return `https://${match[1]}-${port}.app.github.dev`;
  }
  return `https://${hostname}:${port}`;
}

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
  const url = `${getApiBase()}${path.startsWith("/") ? path : `/${path}`}`;
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
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return parsed as T;
}

export interface SamplePatientSummary {
  id: string;
  patient: {
    id?: string;
    mrn?: string;
    name?: string;
    gender?: string;
    birthDate?: string;
  } | null;
  counts: { encounters: number; conditions: number; observations: number };
  primaryMeasures: string[];
}

export async function fetchSamplePatients(): Promise<{
  seedDir: string;
  count: number;
  samples: SamplePatientSummary[];
}> {
  return api("/api/sample-patients");
}

export async function fetchSamplePatient(
  bundleId: string,
): Promise<{ id: string; bundle: unknown; summary: SamplePatientSummary }> {
  return api(`/api/sample-patients/${bundleId}`);
}

export async function runLocalMeasures(
  bundleId: string,
  periodStart = "2025-01-01",
  periodEnd = "2025-12-31",
) {
  const qs = new URLSearchParams({ period_start: periodStart, period_end: periodEnd });
  return api(`/api/sample-patients/${bundleId}/measures/run-local?${qs.toString()}`, {
    method: "POST",
  });
}

export interface SoapEntryInput {
  role: string;
  subjective?: string;
  objective?: string;
  assessment?: string;
  plan?: string;
  encounterId?: string;
  author?: string;
}

export async function fetchSoapNotes(patientId: string): Promise<{
  patientId: string;
  rounds: Record<
    string,
    Array<SoapEntryInput & { id?: string; createdAt?: string; updatedAt?: string }>
  >;
  count: number;
}> {
  return api(`/api/patients/${patientId}/soap-notes`);
}

export async function createSoapNote(
  patientId: string,
  round: number,
  entry: SoapEntryInput,
) {
  return api(`/api/patients/${patientId}/soap-notes`, {
    method: "POST",
    body: JSON.stringify({ round, entry }),
  });
}

export async function deleteSoapNote(patientId: string, noteId: string) {
  return api(`/api/patients/${patientId}/soap-notes/${noteId}`, { method: "DELETE" });
}
