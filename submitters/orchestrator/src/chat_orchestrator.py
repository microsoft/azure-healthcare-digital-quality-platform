"""Cohort surveillance chat with reinforcement-learning-graded answers.

The endpoint ``POST /chat/answer`` performs the following sequence per turn:

    1. Build the (4-action) chat policy snapshot, persisting a uniform-baseline
       snapshot on first use so subsequent turns load by version.
    2. ``SoftmaxPolicy.choose()`` selects the response-template action.
    3. ``EpisodeCapture.start()`` opens an episode with policy_version,
       action_id, action_logprob, and context features (cohort size, intent,
       routed measures).
    4. Intent classification routes the question to one or more measures
       (CMS122v11, CMS165v9, ePC02) via :mod:`chat_actions`.
    5. Each (member, measure) is evaluated with the deterministic native CQL
       executor (the same executor backing ``/tools/compute-quality-measures``).
    6. The chosen action renders the assistant answer from the rollup.
    7. ``EpisodeCapture.end()`` records the episode (Cosmos when configured).
    8. An ``asyncio`` background task grades the episode with the Foundry
       judge (``IntentResolutionMetric`` / ``TaskAdherenceMetric`` /
       ``TaskCompletionMetric``), shapes the reward, and writes the per-metric
       and aggregate rewards via ``RewardWriter``.

Flags:
    * ``ENABLE_LEARNING_CAPTURE`` — turns on episode capture (default off).
    * ``ENABLE_LEARNING_POLICY`` — when off, the policy choice is forced to
      ``terse-counts`` (no exploration) so production stays deterministic
      until the A/B harness in ``_evals/healthcare_digital_quality`` proves
      reward improvement.

See ``_docs/AGENTS_RL_CHAT_DESIGN.md`` and
``.speckit/specifications/spec_chat_rl.md``.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from fastapi import Body, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import config  # NB: import the module so we can mutate ``config.learning_policy``
from config import (
    app,
    logger,
    LEARNING_AVAILABLE,
    LEARNING_AGENT_ID,
)
from chat_actions import (
    ACTION_IDS,
    CHAT_ACTIONS,
    ChatRenderContext,
    MeasureRollup,
    MemberRollup,
    classify_intent,
    get_action,
    is_roster_question,
    route_measures,
)
from digital_quality_measures import (
    MeasureResult,
    evaluate_single_measure_cql,
    get_measure_catalog,
    gather_context,
    normalize_measure_id,
)

if LEARNING_AVAILABLE:
    from agent_learning import (
        Action,
        ContextualSoftmaxPolicy,
        Episode,
        Policy,
        PolicySnapshot,
        Reward,
        RewardShaper,
        RewardSource,
        SoftmaxPolicy,
        evaluate_all,
    )

from chat_features import FEATURE_DIM, extract_measure_features

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENABLE_LEARNING_POLICY = (
    os.getenv("ENABLE_LEARNING_POLICY", "false").lower() == "true"
)
# ``marginal`` selects the original SoftmaxPolicy (one logit per action).
# ``context`` selects ContextualSoftmaxPolicy with a length-FEATURE_DIM
# measure embedding (see chat_features.py). Defaults to ``marginal`` for
# backward compatibility.
LEARNING_POLICY_KIND = os.getenv("LEARNING_POLICY_KIND", "marginal").strip().lower()
CHAT_POLICY_ID = os.getenv("CHAT_POLICY_ID", f"{LEARNING_AGENT_ID}-chat")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class _ChatMember(BaseModel):
    patient_id: str
    bundle: Dict[str, Any]


class ChatAnswerRequest(BaseModel):
    cohort_id: str
    cohort_name: str = ""
    question: str
    intent_hint: str = "unknown"
    member_ids: List[str] = Field(default_factory=list)
    members: List[_ChatMember] = Field(default_factory=list)
    measure_ids: List[str] = Field(default_factory=list)
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    user_principal: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Policy bootstrap
# ---------------------------------------------------------------------------


def _build_uniform_snapshot() -> "PolicySnapshot":
    """Create a baseline PolicySnapshot whose logits are zero (uniform)."""
    return PolicySnapshot(
        id=CHAT_POLICY_ID,
        agent_id=LEARNING_AGENT_ID,
        version=1,
        actions=[Action(id=a.id, description=a.description) for a in CHAT_ACTIONS],
        logits={a.id: 0.0 for a in CHAT_ACTIONS},
        metadata={
            "surface": "cohort-chat",
            "actions_source": "chat_actions.CHAT_ACTIONS",
            "policy_kind": "marginal",
        },
    )


def _build_uniform_context_snapshot() -> "PolicySnapshot":
    """Create a baseline contextual PolicySnapshot with zero weights.

    Returns a snapshot whose ``metadata.context_weights`` is a per-action
    zero vector of length :data:`chat_features.FEATURE_DIM`. With zero
    weights the policy is uniform regardless of the input embedding, so
    the first episode is unbiased.
    """
    return PolicySnapshot(
        id=CHAT_POLICY_ID,
        agent_id=LEARNING_AGENT_ID,
        version=1,
        actions=[Action(id=a.id, description=a.description) for a in CHAT_ACTIONS],
        logits={a.id: 0.0 for a in CHAT_ACTIONS},  # legacy field, unused
        metadata={
            "surface": "cohort-chat",
            "actions_source": "chat_actions.CHAT_ACTIONS",
            "policy_kind": "contextual_softmax",
            "feature_dim": int(FEATURE_DIM),
            "context_weights": {a.id: [0.0] * FEATURE_DIM for a in CHAT_ACTIONS},
        },
    )


def _ensure_chat_policy() -> Optional["Policy"]:
    """Return the cached chat policy, restoring or persisting as needed.

    The policy class is chosen by ``LEARNING_POLICY_KIND``:

    - ``marginal`` (default) — :class:`SoftmaxPolicy` over the chat actions.
    - ``context`` — :class:`ContextualSoftmaxPolicy` with a length-
      :data:`chat_features.FEATURE_DIM` measure embedding.

    Returns ``None`` when the SDK is unavailable so the caller can fall back
    to the deterministic baseline action.
    """
    if not LEARNING_AVAILABLE or config.learning_store is None:
        return None
    if config.learning_policy is not None:
        return config.learning_policy
    try:
        snapshot = config.learning_store.get_latest_policy(LEARNING_AGENT_ID)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to load latest policy snapshot: {e}")
        snapshot = None

    use_context = LEARNING_POLICY_KIND == "context"
    snapshot_is_context = bool(
        snapshot is not None
        and isinstance(snapshot.metadata, dict)
        and snapshot.metadata.get("policy_kind") == "contextual_softmax"
        and snapshot.metadata.get("context_weights")
    )

    # Reset the snapshot to a fresh baseline if the stored kind does not
    # match the requested kind, or if no usable snapshot exists.
    if use_context:
        if snapshot is None or not snapshot_is_context:
            snapshot = _build_uniform_context_snapshot()
            try:
                config.learning_store.store_policy(snapshot)
                logger.info(
                    f"Persisted baseline contextual chat policy v{snapshot.version} "
                    f"(feature_dim={FEATURE_DIM})"
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to persist baseline contextual policy: {e}")
        config.learning_policy = ContextualSoftmaxPolicy(snapshot=snapshot)
        return config.learning_policy

    # marginal (default) path
    if snapshot is None or not snapshot.logits or snapshot_is_context:
        snapshot = _build_uniform_snapshot()
        try:
            config.learning_store.store_policy(snapshot)
            logger.info(
                f"Persisted baseline chat policy snapshot v{snapshot.version} "
                f"with actions={list(snapshot.logits.keys())}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to persist baseline policy snapshot: {e}")
    # Validate every chat action has a logit; backfill missing keys at 0.0 so
    # offline-trained snapshots that predate a new action stay usable.
    for action_id in ACTION_IDS:
        snapshot.logits.setdefault(action_id, 0.0)
    config.learning_policy = SoftmaxPolicy(snapshot=snapshot)
    return config.learning_policy


# ---------------------------------------------------------------------------
# Measure evaluation per-member
# ---------------------------------------------------------------------------


def _summarise_member_notes(result: MeasureResult) -> List[str]:
    notes: List[str] = []
    if result.in_numerator and result.numerator_reasons:
        notes.extend(result.numerator_reasons[:2])
    if result.denominator_exclusion and result.denominator_exclusion_reasons:
        notes.extend(result.denominator_exclusion_reasons[:1])
    if not notes and result.evidence_trace:
        notes.extend(result.evidence_trace[:1])
    return notes


def _catalog_lookup(catalog: Dict[str, "MeasureDefinition"], measure_id: str) -> Optional["MeasureDefinition"]:
    """Catalog lookup tolerant of version-suffix and separator mismatches.

    The catalog is keyed by the CQL ``library NAMEvVERSION`` ids (e.g.
    ``CMS122v11``, ``ePC02v1``) while routing/intent callers often pass
    short ids (``CMS122``, ``ePC02``, ``ePC-02``). Match strategy:
      1. direct hit on the as-passed id
      2. hit on ``normalize_measure_id(measure_id)``
      3. prefix match after stripping ``-``/``_`` and the ``vN`` suffix
         from both sides (case-insensitive). This mirrors the prefix
         tolerance in ``chat_actions.route_measures``.
    """
    if measure_id in catalog:
        return catalog[measure_id]
    canonical = normalize_measure_id(measure_id)
    if canonical in catalog:
        return catalog[canonical]

    def _stem(s: str) -> str:
        s = s.replace("-", "").replace("_", "").lower()
        return s.split("v")[0]

    target = _stem(measure_id)
    for key, defn in catalog.items():
        if _stem(key) == target:
            return defn
    return None


def _evaluate_cohort(
    members: List[_ChatMember],
    measure_ids: List[str],
    period_start: str,
    period_end: str,
) -> List[MeasureRollup]:
    catalog = get_measure_catalog()
    rollups: List[MeasureRollup] = []
    for measure_id in measure_ids:
        measure_def = _catalog_lookup(catalog, measure_id)
        if measure_def is None:
            logger.warning(
                f"Chat: measure {measure_id} not in catalog (keys={list(catalog.keys())}); skipping"
            )
            continue
        canonical = measure_def.measure_id
        rollup = MeasureRollup(
            measure_id=canonical,
            measure_name=measure_def.measure_name or canonical,
            inverse_measure=False,
        )
        for member in members:
            try:
                fhir_request_like = type(
                    "_ChatFHIRRequest",
                    (),
                    {
                        "fhir_bundle": member.bundle if member.bundle.get("resourceType") == "Bundle" else None,
                        "patient": None if member.bundle.get("resourceType") == "Bundle" else member.bundle.get("patient"),
                        "conditions": None,
                        "encounters": None,
                        "observations": None,
                        "procedures": None,
                        "coverages": None,
                    },
                )()
                ctx = gather_context(fhir_request_like)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Chat: could not extract FHIR view for {member.patient_id}: {e}")
                continue

            try:
                result = evaluate_single_measure_cql(
                    measure_def=measure_def,
                    context=ctx,
                    measurement_period_start=period_start,
                    measurement_period_end=period_end,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Chat: CQL eval failed for {member.patient_id}/{canonical}: {e}")
                continue

            rollup.inverse_measure = bool(result.inverse_measure)
            if result.in_denominator:
                rollup.in_denominator += 1
            if result.in_numerator:
                rollup.in_numerator += 1
            if result.denominator_exclusion:
                rollup.exclusion += 1
            rollup.members.append(
                MemberRollup(
                    patient_id=member.patient_id,
                    in_denominator=bool(result.in_denominator),
                    in_numerator=bool(result.in_numerator),
                    denominator_exclusion=bool(result.denominator_exclusion),
                    notes=_summarise_member_notes(result),
                    evidence_trace=list(result.evidence_trace or [])[:3],
                )
            )
        rollups.append(rollup)
    return rollups


# ---------------------------------------------------------------------------
# Background reward grading (judge → shape → write)
# ---------------------------------------------------------------------------


_grading_tasks: Dict[str, "asyncio.Task"] = {}
_grading_state: Dict[str, str] = {}  # episode_id -> "pending" | "graded" | "failed" | "skipped"


def _normalize_measure_set(ids: List[str]) -> set:
    """Normalize a list of measure ids for case- and version-insensitive compare."""
    out = set()
    for raw in ids or []:
        if not raw:
            continue
        s = str(raw).strip().lower()
        # Strip trailing ``vN`` suffix so "cms122v11" and "cms122" compare equal.
        s = s.split("v")[0]
        out.add(s)
    return out


def _compute_routing_correct(
    routed: List[str], selected: List[str]
) -> Optional[bool]:
    """True if every routed measure is in the user's selection.

    Returns ``None`` when the user did not pre-select any measures and we
    therefore cannot judge routing.
    """
    if not selected:
        return None
    if not routed:
        return False  # intent classifier returned nothing despite a selection
    sel = _normalize_measure_set(selected)
    rou = _normalize_measure_set(routed)
    return rou.issubset(sel)


def _detect_hallucinated_member(
    answer_text: str, allowed_ids: List[str]
) -> bool:
    """True if ``answer_text`` mentions a member id outside ``allowed_ids``.

    Chat action templates render members as lines beginning with ``- <id>``
    (see :mod:`chat_actions`). We extract the first whitespace token after
    each ``- `` and flag any token that is not in the allowed set.
    """
    if not answer_text or not allowed_ids:
        return False
    allowed = {str(a).strip() for a in allowed_ids if a}
    for raw_line in answer_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        token = line[2:].split()[0] if len(line) > 2 else ""
        if not token:
            continue
        # Drop trailing punctuation that the render might attach.
        token = token.rstrip(":,.;")
        if token and token not in allowed:
            return True
    return False


async def _grade_and_persist(episode_id: str, episode: "Episode") -> None:
    """Run all judge metrics, shape into a scalar reward, persist rewards."""
    _grading_state[episode_id] = "pending"
    if not LEARNING_AVAILABLE or config.learning_store is None:
        _grading_state[episode_id] = "skipped"
        return
    try:
        # ``evaluate_all`` runs IntentResolution / TaskAdherence / TaskCompletion
        # against the configured judge model. When no JudgeConfig is wired the
        # metrics return status="skipped" — we still write a zero-valued shaped
        # reward so the policy update path has a well-formed episode.
        results = await asyncio.to_thread(
            evaluate_all,
            episode,
            None,  # default metrics
            judge_config=config.learning_judge_config,
        )
        shaper = RewardShaper()
        # ``routing_correct`` and ``hallucinated_member`` are stamped into
        # ``episode.metadata`` by the /chat/answer handler before grading
        # is scheduled. Both default to safe values when missing.
        ep_meta = getattr(episode, "metadata", {}) or {}
        routing_correct = ep_meta.get("routing_correct")
        if routing_correct is not None and not isinstance(routing_correct, bool):
            routing_correct = bool(routing_correct)
        hallucinated_member = bool(ep_meta.get("hallucinated_member", False))
        shaped = shaper.shape(
            results,
            latency_ms=episode.request_latency_ms,
            routing_correct=routing_correct,
            hallucinated_member=hallucinated_member,
        )
        if config.learning_reward_writer is not None:
            await asyncio.to_thread(
                config.learning_reward_writer.write,
                episode,
                results,
                shaped,
                rubric="cohort-chat-v1",
            )
        else:
            # Fall back to direct store_metric_results when writer is absent.
            await asyncio.to_thread(
                config.learning_store.store_metric_results,
                episode_id,
                episode.agent_id,
                results,
            )
        _grading_state[episode_id] = "graded"
        logger.info(
            f"Chat episode {episode_id} graded: shaped={shaped.value:.3f} "
            f"contribs={shaped.metric_contributions}"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Chat reward grading failed for {episode_id}: {e}")
        _grading_state[episode_id] = "failed"


# ---------------------------------------------------------------------------
# /chat/answer endpoint
# ---------------------------------------------------------------------------


@app.post("/chat/answer")
async def chat_answer(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        payload = ChatAnswerRequest(**body)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid payload: {e}")

    start_t = time.perf_counter()

    # 1) Intent → measures (intersected with what the user selected)
    intent = classify_intent(payload.question, payload.intent_hint)
    routed_measures = route_measures(intent, payload.measure_ids)

    # 2) Pick action (policy if enabled, else baseline)
    #
    # Per-request override: the A/B harness sends ``x-rl-arm: baseline`` or
    # ``x-rl-arm: rl`` so a single deployment can serve both arms. The
    # environment flag ``ENABLE_LEARNING_POLICY`` is the production default.
    arm_header = (request.headers.get("x-rl-arm") or "").strip().lower()
    if arm_header == "rl":
        policy_enabled = True
    elif arm_header == "baseline":
        policy_enabled = False
    else:
        policy_enabled = ENABLE_LEARNING_POLICY

    action_id = ACTION_IDS[0]
    action_logprob: Optional[float] = None
    policy_version: Optional[int] = None
    policy_driven = False

    policy = _ensure_chat_policy() if policy_enabled else None
    # Build the measure-feature vector once — reused by contextual policy and
    # logged to the episode context for offline training.
    primary_measure_id = routed_measures[0] if routed_measures else ""
    phi = extract_measure_features(
        primary_measure_id, cohort_size=len(payload.members)
    ) if primary_measure_id else None
    if policy is not None:
        try:
            if isinstance(policy, ContextualSoftmaxPolicy) and phi is not None:
                decision = policy.choose(state=phi)
            else:
                decision = policy.choose()
            action_id = decision.action.id
            action_logprob = float(decision.logprob)
            # ``SoftmaxPolicy.snapshot`` is a method that returns a fresh copy;
            # read ``version`` off the cached internal snapshot to avoid a clone.
            policy_version = policy._snapshot.version
            policy_driven = True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Chat: policy.choose failed, falling back to baseline: {e}")

    # 2b) Question-shape guard — when the user explicitly asks for a member
    # roster ("which members…", "who has…", "list patients with…") we must
    # always render identities. Any summary-only answer is a hard failure of
    # the user's intent, regardless of what the policy preferred. We keep the
    # original policy choice in metadata so REINFORCE can still penalise the
    # arm that lost the override (signal: "your action was discarded").
    roster_override = is_roster_question(payload.question)
    policy_action_id = action_id
    if roster_override and action_id not in ("gap-focused", "evidence-cited"):
        action_id = "evidence-cited"
        # The override is deterministic — it is not a policy sample, so the
        # log-prob and ``policy_driven`` flag no longer apply to the rendered
        # action. Keep ``policy_version`` for traceability.
        action_logprob = None
        policy_driven = False

    action = get_action(action_id)

    # 3) Episode capture (best-effort — never block the user flow)
    capture_ctx = None
    if LEARNING_AVAILABLE and config.learning_capture is not None:
        try:
            capture_ctx = config.learning_capture.start(
                user_input=payload.question,
                system_message=(
                    "You are a digital-quality-measure surveillance assistant. "
                    "Answer using only the supplied cohort rollup."
                ),
                policy_id=CHAT_POLICY_ID,
                policy_version=policy_version,
                action_id=action_id,
                action_logprob=action_logprob,
                model_deployment=os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME"),
                correlation_id=request.headers.get("x-correlation-id"),
                session_id=payload.cohort_id,
                context_features={
                    "cohort_id": payload.cohort_id,
                    "cohort_size": len(payload.members),
                    "intent": intent,
                    "routed_measures": routed_measures,
                    "intent_hint": payload.intent_hint,
                    "period_start": payload.period_start,
                    "period_end": payload.period_end,
                },
                metadata={"surface": "cohort-chat"},
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Chat: capture.start failed: {e}")

    # 4) Evaluate measures across the cohort
    period_start = payload.period_start or os.getenv(
        "QUALITY_MEASUREMENT_PERIOD_START", "2025-01-01"
    )
    period_end = payload.period_end or os.getenv(
        "QUALITY_MEASUREMENT_PERIOD_END", "2025-12-31"
    )
    rollups = _evaluate_cohort(payload.members, routed_measures, period_start, period_end)

    # 5) Render the answer using the chosen action template
    render_ctx = ChatRenderContext(
        cohort_id=payload.cohort_id,
        cohort_name=payload.cohort_name or payload.cohort_id,
        question=payload.question,
        intent=intent,
        measures=rollups,
    )
    answer_text = action.render(render_ctx)

    latency_ms = int((time.perf_counter() - start_t) * 1000)
    episode_id: Optional[str] = None

    # Pre-compute reward-shaping signals so they land in episode metadata.
    # ``routing_correct`` rewards the bandit when the intent classifier picked
    # a measure the user actually selected; ``hallucinated_member`` penalises
    # any rendered patient id outside the cohort's allow-list.
    routing_correct = _compute_routing_correct(routed_measures, payload.measure_ids)
    hallucinated_member = _detect_hallucinated_member(answer_text, payload.member_ids)

    # 6) Close out the episode (always best-effort)
    if capture_ctx is not None:
        try:
            episode = config.learning_capture.end(
                capture_ctx,
                answer_text,
                extra_metadata={
                    "routed_measures": routed_measures,
                    "measure_summary": [
                        {
                            "measure_id": r.measure_id,
                            "in_denominator": r.in_denominator,
                            "in_numerator": r.in_numerator,
                            "exclusion": r.exclusion,
                            "gaps": r.gap_count,
                        }
                        for r in rollups
                    ],
                    "latency_ms": latency_ms,
                    "policy_driven": policy_driven,
                    "routing_correct": routing_correct,
                    "hallucinated_member": hallucinated_member,
                    "phi": phi.tolist() if phi is not None else None,
                },
            )
            if episode is not None:
                episode.request_latency_ms = latency_ms
                episode_id = episode.id
                # 7) Schedule async grading + reward persistence
                if (
                    ENABLE_LEARNING_POLICY
                    or os.getenv("ENABLE_LEARNING_GRADING", "false").lower() == "true"
                ):
                    task = asyncio.create_task(_grade_and_persist(episode_id, episode))
                    _grading_tasks[episode_id] = task
                    _grading_state[episode_id] = "pending"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Chat: capture.end failed: {e}")

    response_body = {
        "episodeId": episode_id or str(uuid.uuid4()),
        "cohortId": payload.cohort_id,
        "question": payload.question,
        "answer": answer_text,
        "routing": {
            "intent": intent,
            "measureIds": routed_measures,
            "confidence": 1.0 if payload.intent_hint != "unknown" else 0.5,
            "rationale": f"intent={intent} from {'hint' if payload.intent_hint != 'unknown' else 'keyword-scan'}",
        },
        "actionId": action_id,
        "policyActionId": policy_action_id,
        "rosterOverride": roster_override and policy_action_id != action_id,
        "policyVersion": policy_version,
        "policyDriven": policy_driven,
        "latencyMs": latency_ms,
        "measureSummary": [
            {
                "measureId": r.measure_id,
                "measureName": r.measure_name,
                "inverseMeasure": r.inverse_measure,
                "inDenominator": r.in_denominator,
                "inNumerator": r.in_numerator,
                "exclusion": r.exclusion,
                "gaps": r.gap_count,
                "gapMembers": [
                    {
                        "patientId": gm.patient_id,
                        "notes": gm.notes[:2],
                    }
                    for gm in r.gap_members()
                ],
                "notes": [n for m in r.members for n in m.notes][:5],
            }
            for r in rollups
        ],
    }
    return JSONResponse(content=response_body)


# ---------------------------------------------------------------------------
# /chat/episodes/{id}/reward
# ---------------------------------------------------------------------------


@app.get("/chat/episodes/{episode_id}/reward")
async def chat_episode_reward(episode_id: str) -> JSONResponse:
    if not LEARNING_AVAILABLE or config.learning_store is None:
        raise HTTPException(status_code=503, detail="learning store not configured")

    status = _grading_state.get(episode_id, "pending")
    try:
        rewards: List[Reward] = (
            config.learning_store.get_rewards_for_episode(episode_id, LEARNING_AGENT_ID)
            or []
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Chat: reward lookup failed for {episode_id}: {e}")
        rewards = []

    metric_rows: List[Dict[str, Any]] = []
    shaped_value: Optional[float] = None
    reward_at: Optional[str] = None
    for r in rewards:
        row = {
            "metric": (r.metric.value if r.metric else (r.source.value if r.source else "")),
            "score": r.value,
            "status": "graded" if r.value is not None else "skipped",
        }
        metric_rows.append(row)
        if r.source == RewardSource.AGGREGATE:
            shaped_value = r.value
            reward_at = r.created_at

    # If no rewards yet but we know a grading task exists, report as pending.
    if not rewards:
        task = _grading_tasks.get(episode_id)
        if task is not None and not task.done():
            status = "pending"
        elif status == "graded":
            # Grading completed but the store returned nothing — likely
            # judge was not configured and we recorded a "skipped" state.
            status = "skipped"

    return JSONResponse(content={
        "episodeId": episode_id,
        "status": status,
        "shapedReward": shaped_value,
        "metrics": metric_rows,
        "rewardAt": reward_at,
    })


# ---------------------------------------------------------------------------
# /chat/policy/reload — admin: reload the latest snapshot from Cosmos
# ---------------------------------------------------------------------------


@app.post("/chat/policy/reload")
async def chat_policy_reload() -> JSONResponse:
    if not LEARNING_AVAILABLE or config.learning_store is None:
        raise HTTPException(status_code=503, detail="learning store not configured")
    try:
        snapshot = config.learning_store.get_latest_policy(LEARNING_AGENT_ID)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"snapshot fetch failed: {e}")
    if snapshot is None:
        raise HTTPException(status_code=404, detail="no policy snapshot found")
    for action_id in ACTION_IDS:
        snapshot.logits.setdefault(action_id, 0.0)
    config.learning_policy = SoftmaxPolicy(snapshot=snapshot)
    logger.info(
        f"Chat policy reloaded: v{snapshot.version} actions={list(snapshot.logits.keys())}"
    )
    return JSONResponse(content={
        "agentId": snapshot.agent_id,
        "version": snapshot.version,
        "actions": list(snapshot.logits.keys()),
        "logits": snapshot.logits,
        "episodesSeen": snapshot.episodes_seen,
    })


logger.info(
    f"Chat orchestrator endpoints registered "
    f"(ENABLE_LEARNING_POLICY={ENABLE_LEARNING_POLICY}, "
    f"capture_enabled={bool(LEARNING_AVAILABLE and config.learning_capture is not None)})"
)
