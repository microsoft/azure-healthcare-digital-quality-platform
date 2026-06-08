"""Response-template action space for the cohort-chat policy.

Each action is a function ``render(ctx) -> str`` that converts a structured
``ChatRenderContext`` (cohort name, measure summary, member rollups) into a
natural-language assistant response. The 4 actions are the bandit arms the
``SoftmaxPolicy`` selects between; the judge then scores the answer along
intent-resolution / task-adherence / task-completion, and REINFORCE updates
nudge the logits toward the higher-reward arm.

See ``.speckit/specifications/spec_chat_rl.md`` §3 and
``_docs/AGENTS_RL_CHAT_DESIGN.md`` §3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List


# ---------------------------------------------------------------------------
# Rendering context — populated by the chat orchestrator before action choice
# ---------------------------------------------------------------------------


@dataclass
class MemberRollup:
    """Per-member rollup of a single measure evaluation."""

    patient_id: str
    in_denominator: bool
    in_numerator: bool
    denominator_exclusion: bool
    notes: List[str] = field(default_factory=list)
    evidence_trace: List[str] = field(default_factory=list)


@dataclass
class MeasureRollup:
    measure_id: str
    measure_name: str
    inverse_measure: bool
    in_denominator: int = 0
    in_numerator: int = 0
    exclusion: int = 0
    members: List[MemberRollup] = field(default_factory=list)

    @property
    def gap_count(self) -> int:
        """Members who are gaps in care for this measure.

        For *inverse* measures (CMS122v11 = poor HbA1c), being in the numerator
        is the gap (bad outcome). For all other measures, being in the
        denominator but NOT in the numerator is the gap.
        """
        if self.inverse_measure:
            return self.in_numerator
        return max(0, self.in_denominator - self.in_numerator)

    def gap_members(self) -> List[MemberRollup]:
        if self.inverse_measure:
            return [m for m in self.members if m.in_numerator]
        return [
            m
            for m in self.members
            if m.in_denominator and not m.in_numerator and not m.denominator_exclusion
        ]


@dataclass
class ChatRenderContext:
    cohort_id: str
    cohort_name: str
    question: str
    intent: str  # routed intent (e.g. "hba1c-poor-control")
    measures: List[MeasureRollup]


# ---------------------------------------------------------------------------
# Action templates
# ---------------------------------------------------------------------------


def _member_list(rollup: MemberRollup, *, with_reasons: bool = False, max_evidence: int = 2) -> str:
    pid = rollup.patient_id
    if not with_reasons:
        return f"- {pid}"
    reasons = rollup.notes[: max(1, max_evidence)] if rollup.notes else rollup.evidence_trace[:max_evidence]
    suffix = f" — {'; '.join(reasons)}" if reasons else ""
    return f"- {pid}{suffix}"


def _render_terse_counts(ctx: ChatRenderContext) -> str:
    """Action A — terse population counts only."""
    if not ctx.measures:
        return f"No measures evaluated for cohort {ctx.cohort_name}."
    lines = [f"Cohort: {ctx.cohort_name}"]
    for m in ctx.measures:
        lines.append(
            f"- {m.measure_id} ({m.measure_name}): "
            f"denom={m.in_denominator} num={m.in_numerator} excl={m.exclusion} "
            f"gaps={m.gap_count}"
        )
    return "\n".join(lines)


def _render_gap_focused(ctx: ChatRenderContext) -> str:
    """Action B — lead with the gap-in-care roster (no evidence)."""
    if not ctx.measures:
        return f"No measures evaluated for cohort {ctx.cohort_name}."
    lines: List[str] = []
    any_gap = False
    for m in ctx.measures:
        gaps = m.gap_members()
        if not gaps:
            lines.append(f"{m.measure_id}: no gaps in care for this cohort.")
            continue
        any_gap = True
        verb = "with poor outcomes" if m.inverse_measure else "with gaps in care"
        lines.append(f"{m.measure_id} ({m.measure_name}) — {len(gaps)} member(s) {verb}:")
        lines.extend(_member_list(g) for g in gaps)
    if not any_gap:
        return f"No gaps in care detected for cohort {ctx.cohort_name}."
    return "\n".join(lines)


def _render_evidence_cited(ctx: ChatRenderContext) -> str:
    """Action C — gap roster plus one or two evidence lines per member."""
    if not ctx.measures:
        return f"No measures evaluated for cohort {ctx.cohort_name}."
    lines: List[str] = [f"Cohort surveillance — {ctx.cohort_name}"]
    any_gap = False
    for m in ctx.measures:
        gaps = m.gap_members()
        lines.append(
            f"\n{m.measure_id} ({m.measure_name}) — "
            f"denom={m.in_denominator}, num={m.in_numerator}, excl={m.exclusion}, gaps={m.gap_count}"
        )
        if not gaps:
            continue
        any_gap = True
        for g in gaps:
            lines.append(_member_list(g, with_reasons=True))
    if not any_gap:
        lines.append("\nNo gap-in-care members detected for any selected measure.")
    return "\n".join(lines)


def _render_narrative(ctx: ChatRenderContext) -> str:
    """Action D — short narrative paragraph summarising the cohort."""
    if not ctx.measures:
        return f"No measures evaluated for cohort {ctx.cohort_name}."
    total_gaps = sum(m.gap_count for m in ctx.measures)
    measure_phrases = []
    for m in ctx.measures:
        if m.in_denominator == 0:
            measure_phrases.append(f"{m.measure_id} did not have any qualifying members")
            continue
        if m.gap_count == 0:
            measure_phrases.append(f"{m.measure_id} showed no gaps in care")
        else:
            verb = "members with poor outcomes" if m.inverse_measure else "open gaps"
            measure_phrases.append(
                f"{m.measure_id} surfaced {m.gap_count} {verb} (out of {m.in_denominator} qualifying)"
            )
    summary = "; ".join(measure_phrases)
    headline = (
        f"Cohort {ctx.cohort_name} has {total_gaps} member(s) requiring follow-up."
        if total_gaps
        else f"Cohort {ctx.cohort_name} is clean against the selected measures."
    )
    return f"{headline} Specifically: {summary}."


@dataclass(frozen=True)
class ChatAction:
    id: str
    description: str
    render: Callable[[ChatRenderContext], str]


CHAT_ACTIONS: List[ChatAction] = [
    ChatAction(
        id="terse-counts",
        description="One line per measure with denominator/numerator/exclusion counts.",
        render=_render_terse_counts,
    ),
    ChatAction(
        id="gap-focused",
        description="Lead with the gap-in-care roster; concise member ids only.",
        render=_render_gap_focused,
    ),
    ChatAction(
        id="evidence-cited",
        description="Gap roster annotated with measure-evidence reasons.",
        render=_render_evidence_cited,
    ),
    ChatAction(
        id="narrative",
        description="Short paragraph summarising the cohort across selected measures.",
        render=_render_narrative,
    ),
]


def get_action(action_id: str) -> ChatAction:
    for a in CHAT_ACTIONS:
        if a.id == action_id:
            return a
    # Fallback to the safe default action.
    return CHAT_ACTIONS[0]


ACTION_IDS: List[str] = [a.id for a in CHAT_ACTIONS]


# ---------------------------------------------------------------------------
# Intent → measure routing (keyword fallback used when intent_hint=="unknown").
# Kept deterministic so the chat path stays fast even when the LLM intent
# classifier is unavailable / degraded.
# ---------------------------------------------------------------------------

INTENT_MEASURE_MAP: Dict[str, List[str]] = {
    "hba1c-poor-control": ["CMS122v11"],
    "uncontrolled-htn": ["CMS165v9"],
    "severe-ob-complications": ["ePC02"],
    "gaps-in-care": ["CMS122v11", "CMS165v9", "ePC02"],
}


_INTENT_KEYWORDS: List[tuple] = [
    ("hba1c-poor-control", ("hba1c", "a1c", "diabet", "glycemic", "hemoglobin")),
    ("uncontrolled-htn", ("htn", "hypertension", "blood pressure", "bp control")),
    ("severe-ob-complications", ("obstetric", "ob complication", "maternal", "smm", "delivery")),
    ("gaps-in-care", ("gap", "care gap", "open measure")),
]


def classify_intent(question: str, intent_hint: str = "unknown") -> str:
    """Return one of the canonical intent ids.

    Preference order:
        1. ``intent_hint`` if it is already a canonical id (canned button).
        2. Keyword scan of ``question`` against ``_INTENT_KEYWORDS``.
        3. ``"gaps-in-care"`` as the safe default (routes to all 3 measures).
    """
    if intent_hint in INTENT_MEASURE_MAP:
        return intent_hint
    q = (question or "").lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(k in q for k in keywords):
            return intent
    return "gaps-in-care"


def route_measures(intent: str, selected: List[str]) -> List[str]:
    """Intersect the intent's measure set with the user's selection."""
    target = INTENT_MEASURE_MAP.get(intent, list(INTENT_MEASURE_MAP["gaps-in-care"]))
    if not selected:
        return target
    sel = set(selected)
    routed = [m for m in target if m in sel or m.split("v")[0] in {s.split("v")[0] for s in sel}]
    return routed or target


# ---------------------------------------------------------------------------
# Question-shape guard
# ---------------------------------------------------------------------------


_ROSTER_PATTERNS: List[str] = [
    "which member",
    "which members",
    "which patient",
    "which patients",
    "who in",
    "who has",
    "who is",
    "who are",
    "list the member",
    "list members",
    "list the patient",
    "list patients",
    "name the member",
    "name members",
    "show me the member",
    "show me member",
    "show members",
    "show patients",
    "show the patient",
    "members with",
    "patients with",
    "roster",
    "identify the",
    "identify which",
]


def is_roster_question(question: str) -> bool:
    """Return True when the question explicitly asks for a member list.

    The policy is allowed to optimise between *summary-shaped* actions
    (``terse-counts`` / ``narrative``) and *roster-shaped* actions
    (``gap-focused`` / ``evidence-cited``) for ambiguous questions like
    "what's going on with this cohort?". But when the user explicitly asks
    "which members…", "who has…", "list the patients with…", we must always
    return identities — a count-only reply is a hard failure of the user's
    intent. This guard short-circuits the action choice in that case.
    """
    if not question:
        return False
    q = question.strip().lower()
    return any(p in q for p in _ROSTER_PATTERNS)
