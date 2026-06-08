/**
 * CohortChatPanel — surveils a cohort with reinforcement-learning-graded
 * Q&A backed by the orchestrator's policy + Foundry LLM.
 *
 * The user picks a canned question (or types one); the panel posts to
 * `/api/chat/cohort-question` which forwards to the orchestrator's
 * `/chat/answer` where a `SoftmaxPolicy` selects the response template,
 * an `EpisodeCapture` records the turn, and an asynchronous judge grades
 * `intent_resolution`, `task_adherence`, and `task_completion` for offline
 * REINFORCE policy updates.
 *
 * See `.speckit/specifications/spec_chat_rl.md`.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ChatAnswer,
  ChatIntent,
  ChatRewardSummary,
  askCohortQuestion,
  fetchChatReward,
} from "../store/cohortChat";

interface CannedQuestion {
  id: ChatIntent;
  label: string;
  question: string;
  measureId: string;
  description: string;
}

const CANNED_QUESTIONS: CannedQuestion[] = [
  {
    id: "hba1c-poor-control",
    label: "Hemoglobin A1c Poor Control?",
    question: "Which members in this cohort have poor Hemoglobin A1c control?",
    measureId: "CMS122v11",
    description: "CMS122v11 — inverse measure: members in numerator are gaps in care.",
  },
  {
    id: "uncontrolled-htn",
    label: "Uncontrolled High Blood Pressure?",
    question: "Which members in this cohort have uncontrolled hypertension?",
    measureId: "CMS165v9",
    description: "CMS165v9 — members NOT in numerator (last BP not <140/<90) are gaps in care.",
  },
  {
    id: "severe-ob-complications",
    label: "Severe Obstetric Complications?",
    question: "Which deliveries in this cohort have severe obstetric complications?",
    measureId: "ePC02",
    description: "ePC02 — members in numerator experienced severe maternal morbidity.",
  },
];

interface ChatTurn {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  /** Set when role==assistant. */
  answer?: ChatAnswer;
  reward?: ChatRewardSummary;
  rewardError?: string;
}

interface Props {
  cohortId: string;
  cohortName: string;
  /** Subset of measure ids the user has ticked in the Evaluate panel. */
  selectedMeasureIds: string[];
  /** Cohort member count (used to disable Ask when empty). */
  memberCount: number;
  periodStart?: string;
  periodEnd?: string;
}

const REWARD_POLL_INTERVAL_MS = 2000;
const REWARD_POLL_TIMEOUT_MS = 30000;

export const CohortChatPanel: React.FC<Props> = ({
  cohortId,
  cohortName,
  selectedMeasureIds,
  memberCount,
  periodStart,
  periodEnd,
}) => {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [draft, setDraft] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const turnsEndRef = useRef<HTMLDivElement | null>(null);

  // Clear the thread (and any pending reward pollers) when the cohort changes.
  useEffect(() => {
    setTurns([]);
    setError(null);
    setDraft("");
  }, [cohortId]);

  useEffect(() => {
    turnsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns.length]);

  const selectedMeasureSet = useMemo(
    () => new Set(selectedMeasureIds || []),
    [selectedMeasureIds],
  );

  const pollReward = useCallback(async (episodeId: string, turnId: string) => {
    const deadline = Date.now() + REWARD_POLL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      try {
        const reward = await fetchChatReward(episodeId);
        if (reward.status !== "pending") {
          setTurns((prev) =>
            prev.map((t) => (t.id === turnId ? { ...t, reward } : t)),
          );
          return;
        }
      } catch (err) {
        setTurns((prev) =>
          prev.map((t) =>
            t.id === turnId
              ? { ...t, rewardError: (err as Error).message }
              : t,
          ),
        );
        return;
      }
      await new Promise((r) => setTimeout(r, REWARD_POLL_INTERVAL_MS));
    }
    // Timed out — leave the (reward pending) caption in place.
  }, []);

  const sendQuestion = useCallback(
    async (question: string, intent?: ChatIntent) => {
      if (!question.trim()) return;
      if (busy) return;
      const userTurnId = `u-${Date.now()}`;
      const assistantTurnId = `a-${Date.now()}`;
      setTurns((prev) => [
        ...prev,
        { id: userTurnId, role: "user", text: question },
        { id: assistantTurnId, role: "assistant", text: "…thinking…" },
      ]);
      setBusy(true);
      setError(null);
      try {
        const answer = await askCohortQuestion({
          cohortId,
          question,
          intent,
          selectedMeasureIds,
          periodStart,
          periodEnd,
        });
        setTurns((prev) =>
          prev.map((t) =>
            t.id === assistantTurnId
              ? { ...t, text: answer.answer, answer }
              : t,
          ),
        );
        // Kick off the reward poll asynchronously — the judge grades the
        // episode in the background and writes results to Cosmos.
        pollReward(answer.episodeId, assistantTurnId).catch(() => {
          /* swallow — pollReward already records errors on the turn */
        });
      } catch (err) {
        const message = (err as Error).message || "Chat request failed";
        setError(message);
        setTurns((prev) =>
          prev.map((t) =>
            t.id === assistantTurnId
              ? { ...t, text: `Error: ${message}`, role: "system" }
              : t,
          ),
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, cohortId, pollReward, periodEnd, periodStart, selectedMeasureIds],
  );

  const handleCanned = (q: CannedQuestion) => {
    sendQuestion(q.question, q.id);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendQuestion(draft.trim());
    setDraft("");
  };

  const disabledReason = useMemo(() => {
    if (!cohortId) return "No cohort selected";
    if (!memberCount) return "Cohort has no members";
    return null;
  }, [cohortId, memberCount]);

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4 space-y-4">
      <div>
        <h4 className="text-sm font-medium text-gray-700">
          Cohort surveillance chat — {cohortName || cohortId}
        </h4>
        <p className="text-xs text-gray-500">
          The chat is graded by a Foundry judge (intent / adherence / completion) and the
          orchestrator's softmax policy learns from those rewards. See{" "}
          <code>_docs/AGENTS_RL_CHAT_DESIGN.md</code>.
        </p>
      </div>

      {disabledReason && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
          {disabledReason}. Add members in the <strong>Members</strong> panel first.
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {CANNED_QUESTIONS.map((q) => {
          const inSelection =
            selectedMeasureSet.size === 0 || selectedMeasureSet.has(q.measureId);
          const isDisabled = !!disabledReason || busy || !inSelection;
          return (
            <button
              key={q.id}
              type="button"
              onClick={() => handleCanned(q)}
              disabled={isDisabled}
              title={
                !inSelection
                  ? `Select ${q.measureId} in the Evaluate panel to enable this question.`
                  : q.description
              }
              className={`px-3 py-1.5 text-xs rounded border transition ${
                isDisabled
                  ? "bg-gray-100 text-gray-400 border-gray-200 cursor-not-allowed"
                  : "bg-blue-100 text-blue-700 border-blue-200 hover:bg-blue-200"
              }`}
            >
              {q.label}
              <span className="ml-1 text-[10px] opacity-60">
                ({q.measureId})
              </span>
            </button>
          );
        })}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Ask about this cohort…"
          className="flex-1 px-3 py-1.5 text-sm border border-gray-300 rounded"
          disabled={!!disabledReason || busy}
        />
        <button
          type="submit"
          disabled={!!disabledReason || busy || !draft.trim()}
          className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>

      {error && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
          {error}
        </div>
      )}

      <div className="space-y-2 max-h-[28rem] overflow-y-auto pr-1">
        {turns.length === 0 && !disabledReason && (
          <div className="text-xs text-gray-500 italic">
            Pick a canned question above to get started.
          </div>
        )}
        {turns.map((t) => (
          <ChatBubble key={t.id} turn={t} />
        ))}
        <div ref={turnsEndRef} />
      </div>
    </div>
  );
};

const ChatBubble: React.FC<{ turn: ChatTurn }> = ({ turn }) => {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] px-3 py-2 text-sm rounded-lg bg-blue-600 text-white">
          {turn.text}
        </div>
      </div>
    );
  }
  if (turn.role === "system") {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] px-3 py-2 text-xs rounded-lg bg-red-50 text-red-700 border border-red-200">
          {turn.text}
        </div>
      </div>
    );
  }
  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-1">
        <div className="px-3 py-2 text-sm rounded-lg bg-gray-100 text-gray-900 whitespace-pre-wrap">
          {turn.text}
        </div>
        {turn.answer && <AssistantCaption answer={turn.answer} reward={turn.reward} rewardError={turn.rewardError} />}
      </div>
    </div>
  );
};

const AssistantCaption: React.FC<{
  answer: ChatAnswer;
  reward?: ChatRewardSummary;
  rewardError?: string;
}> = ({ answer, reward, rewardError }) => {
  const policyLabel = answer.policyVersion != null
    ? `policy v${answer.policyVersion}`
    : "policy: baseline";
  const measureChips = answer.routing.measureIds.map((m) => (
    <span key={m} className="px-1.5 py-0.5 text-[10px] rounded bg-gray-200 text-gray-700">
      {m}
    </span>
  ));

  let rewardChip: React.ReactNode = (
    <span className="px-1.5 py-0.5 text-[10px] rounded bg-amber-100 text-amber-800 border border-amber-200">
      reward pending
    </span>
  );
  if (rewardError) {
    rewardChip = (
      <span
        title={rewardError}
        className="px-1.5 py-0.5 text-[10px] rounded bg-red-100 text-red-800 border border-red-200"
      >
        reward unavailable
      </span>
    );
  } else if (reward && reward.status === "graded") {
    const v = reward.shapedReward;
    const tone =
      v == null ? "bg-gray-100 text-gray-700 border-gray-200"
      : v >= 0.5 ? "bg-green-100 text-green-800 border-green-200"
      : v >= 0.0 ? "bg-yellow-100 text-yellow-800 border-yellow-200"
      : "bg-red-100 text-red-800 border-red-200";
    rewardChip = (
      <span
        title={reward.metrics
          .map((m) => `${m.metric}: ${m.score?.toFixed?.(2) ?? "—"} (${m.status})`)
          .join(" · ")}
        className={`px-1.5 py-0.5 text-[10px] rounded border ${tone}`}
      >
        reward {v == null ? "—" : v.toFixed(2)}
      </span>
    );
  } else if (reward && reward.status === "failed") {
    rewardChip = (
      <span className="px-1.5 py-0.5 text-[10px] rounded bg-red-100 text-red-800 border border-red-200">
        reward failed
      </span>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 text-[10px] text-gray-500">
      <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-700 font-mono">
        action: {answer.actionId}
      </span>
      <span
        title={answer.policyDriven ? "Action selected by SoftmaxPolicy" : "Action chosen without policy (baseline)"}
        className={`px-1.5 py-0.5 rounded font-mono ${
          answer.policyDriven
            ? "bg-indigo-100 text-indigo-700"
            : "bg-gray-100 text-gray-600"
        }`}
      >
        {policyLabel}
      </span>
      {measureChips}
      <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600">
        {answer.latencyMs}ms
      </span>
      {rewardChip}
    </div>
  );
};

export default CohortChatPanel;
