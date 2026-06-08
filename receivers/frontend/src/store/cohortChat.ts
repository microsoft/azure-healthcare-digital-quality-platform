/**
 * Cohort Chat store — talks to the backend `/api/chat/*` surface that
 * forwards to the orchestrator's `/chat/answer` reinforcement-learning
 * endpoint. See `.speckit/specifications/spec_chat_rl.md` for the contract.
 */

import { getAuthToken } from "./index";
import { githubDevSubsPort } from "../utils/ghutils";

// ---------------------------------------------------------------------------
// Types — must stay in lock-step with backend/src/chat.py response shapes.
// ---------------------------------------------------------------------------

export type ChatIntent =
  | "hba1c-poor-control"
  | "uncontrolled-htn"
  | "severe-ob-complications"
  | "gaps-in-care"
  | "unknown";

export interface ChatMeasureRouting {
  intent: ChatIntent;
  measureIds: string[];
  confidence: number;
  rationale: string;
}

export interface ChatAnswer {
  episodeId: string;
  cohortId: string;
  question: string;
  answer: string;
  routing: ChatMeasureRouting;
  /** Identifier of the response-template action the policy selected. */
  actionId: string;
  /** Policy version that produced this turn (null if RL disabled). */
  policyVersion: number | null;
  /** True when the orchestrator selected the action via SoftmaxPolicy.choose(). */
  policyDriven: boolean;
  latencyMs: number;
  /** Snapshot of the measure evaluation that backed the answer. */
  measureSummary: {
    measureId: string;
    inDenominator: number;
    inNumerator: number;
    exclusion: number;
    notes: string[];
  }[];
}

export interface ChatRewardSummary {
  episodeId: string;
  status: "pending" | "graded" | "failed";
  shapedReward: number | null;
  metrics: { metric: string; score: number | null; status: string }[];
  rewardAt: string | null;
}

// ---------------------------------------------------------------------------
// URL + auth helpers (mirror qualityMeasures.ts so we behave identically
// under github.dev, localhost, and in-cluster prod environments).
// ---------------------------------------------------------------------------

function getApiBase(): string {
  const hostname = window.location.hostname;
  const apiPort = 8000;
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    const apiBaseUrl = import.meta.env.VITE_API_URL as string | undefined;
    return apiBaseUrl || `http://localhost:${apiPort}`;
  }
  if (hostname.endsWith("github.dev")) {
    return githubDevSubsPort(hostname, apiPort);
  }
  return "";
}

async function apiFetch<T>(
  path: string,
  init?: RequestInit & { requireAuth?: boolean },
): Promise<T> {
  const base = getApiBase();
  const url = `${base}${path.startsWith("/") ? "" : "/"}${path}`;
  const requireAuth = init?.requireAuth !== false;

  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (requireAuth) {
    const token = await getAuthToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
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
      // leave as text
    }
  }
  if (!response.ok) {
    const fallback = `${response.status} ${response.statusText}`;
    const detail =
      (parsed as { detail?: string })?.detail ||
      (typeof parsed === "string" ? parsed : undefined) ||
      fallback;
    throw new Error(`API ${response.status}: ${detail}`);
  }
  return parsed as T;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export interface AskCohortQuestionPayload {
  cohortId: string;
  question: string;
  /** Optional intent hint when a canned-question button is clicked. */
  intent?: ChatIntent;
  /** Subset of measure ids the user has selected in the Evaluate panel. */
  selectedMeasureIds?: string[];
  /** Measurement period (default: previous calendar year per CMS retrospective reporting). */
  periodStart?: string;
  periodEnd?: string;
}

export async function askCohortQuestion(
  payload: AskCohortQuestionPayload,
): Promise<ChatAnswer> {
  return apiFetch<ChatAnswer>("/api/chat/cohort-question", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function fetchChatReward(
  episodeId: string,
): Promise<ChatRewardSummary> {
  return apiFetch<ChatRewardSummary>(
    `/api/chat/episodes/${encodeURIComponent(episodeId)}/reward`,
  );
}
