"""Agent Learning SDK tools — episode listing, reward assignment,
policy inspection, batch updates, and statistics.

These tools expose the native policy-bandit RL surface: the SDK optimises
a discrete softmax policy over prompt/strategy variants rather than
fine-tuning the underlying LLM.
"""

import json
import logging
from typing import Optional

from agent_framework import ai_function

from config import (
    LEARNING_AVAILABLE,
    LEARNING_AGENT_ID,
    ENABLE_LEARNING_CAPTURE,
    FOUNDRY_MODEL_DEPLOYMENT_NAME,
    learning_store,
    learning_capture,
    learning_reward_writer,
    learning_judge_config,
    learning_policy,
)

# Conditional imports — must match the try/except in config.py
if LEARNING_AVAILABLE:
    from agent_learning import (
        LearnerConfig,
        LearningRunner,
        RewardShaper,
        RewardSource,
        ShapingConfig,
        SoftmaxPolicy,
    )
    from agent_learning.learners import ReinforceLearner
    from agent_learning.metrics import default_metrics
    from agent_learning.types import Reward

logger = logging.getLogger("digital_quality_orchestrator")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_policy() -> Optional["SoftmaxPolicy"]:
    """Lazily load the most recent persisted policy snapshot."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return None
    # Prefer the module-level policy that was loaded at startup
    global learning_policy  # noqa: PLW0603
    if learning_policy is not None:
        return learning_policy
    latest = learning_store.get_latest_policy(LEARNING_AGENT_ID)
    if latest is not None:
        learning_policy = SoftmaxPolicy.from_snapshot(latest)
    return learning_policy


def _build_runner() -> Optional["LearningRunner"]:
    """Construct a LearningRunner on demand. Returns None when the SDK
    is unavailable or no policy snapshot exists yet."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return None
    policy = _ensure_policy()
    if policy is None:
        return None
    metrics = default_metrics(learning_judge_config) if learning_judge_config else []
    return LearningRunner(
        store=learning_store,
        policy=policy,
        metrics=metrics,
        shaper=RewardShaper(ShapingConfig()),
        writer=learning_reward_writer,
        learner=ReinforceLearner(LearnerConfig()),
    )


# ---------------------------------------------------------------------------
# Episode management
# ---------------------------------------------------------------------------

@ai_function
def learning_list_episodes_tool(
    agent_id: str = None,
    limit: int = 20,
    start_date: str = None,
    end_date: str = None,
) -> str:
    """List captured episodes from the agent-learning store."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        episodes = learning_store.query_episodes(
            agent_id=agent,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        episodes_data = []
        for ep in episodes:
            episodes_data.append({
                "id": ep.id,
                "agent_id": ep.agent_id,
                "user_input": (ep.user_input[:200] + "...") if len(ep.user_input) > 200 else ep.user_input,
                "assistant_output": (ep.assistant_output[:200] + "...") if len(ep.assistant_output) > 200 else ep.assistant_output,
                "tool_calls_count": len(ep.tool_calls),
                "model_deployment": ep.model_deployment,
                "request_latency_ms": ep.request_latency_ms,
                "policy_id": ep.policy_id,
                "policy_version": ep.policy_version,
                "action_id": ep.action_id,
                "created_at": ep.created_at,
            })
        return json.dumps({
            "agent_id": agent,
            "episodes_found": len(episodes_data),
            "episodes": episodes_data,
        }, indent=2)
    except Exception as e:
        logger.error("Error listing episodes: %s", e)
        return json.dumps({"error": str(e)})


@ai_function
def learning_get_episode_tool(episode_id: str, agent_id: str = None) -> str:
    """Get detailed information about a specific episode."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        episode = learning_store.get_episode(episode_id, agent)
        if not episode:
            return json.dumps({"error": f"Episode {episode_id} not found"})
        tool_calls_data = []
        for tc in episode.tool_calls:
            tool_calls_data.append({
                "name": tc.name,
                "arguments": tc.arguments,
                "result": (tc.result[:500] + "...") if tc.result and len(tc.result) > 500 else tc.result,
                "duration_ms": tc.duration_ms,
                "error": tc.error,
            })
        return json.dumps({
            "id": episode.id,
            "agent_id": episode.agent_id,
            "user_input": episode.user_input,
            "assistant_output": episode.assistant_output,
            "tool_calls": tool_calls_data,
            "model_deployment": episode.model_deployment,
            "policy_id": episode.policy_id,
            "policy_version": episode.policy_version,
            "action_id": episode.action_id,
            "action_logprob": episode.action_logprob,
            "request_latency_ms": episode.request_latency_ms,
            "token_usage": episode.token_usage,
            "metadata": episode.metadata,
            "created_at": episode.created_at,
        }, indent=2)
    except Exception as e:
        logger.error("Error getting episode: %s", e)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Reward assignment
# ---------------------------------------------------------------------------

_REWARD_SOURCE_MAP = {
    "human_approval": "HUMAN_APPROVAL",
    "metric": "METRIC",
    "aggregate": "AGGREGATE",
    "test_result": "TEST_RESULT",
    "latency_penalty": "LATENCY_PENALTY",
    "cost_penalty": "COST_PENALTY",
}


@ai_function
def learning_assign_reward_tool(
    episode_id: str,
    reward_value: float,
    reward_source: str = "human_approval",
    agent_id: str = None,
    rubric: str = None,
    evaluator: str = None,
    comments: str = None,
) -> str:
    """Manually assign a reward to an episode (e.g. human feedback)."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        source_name = _REWARD_SOURCE_MAP.get(reward_source.lower(), "HUMAN_APPROVAL")
        source = RewardSource[source_name]
        metadata = {"comments": comments} if comments else {}
        reward = Reward(
            episode_id=episode_id,
            agent_id=agent,
            source=source,
            value=reward_value,
            rubric=rubric,
            evaluator=evaluator,
            metadata=metadata,
        )
        learning_store.store_reward(reward)
        return json.dumps({
            "success": True,
            "reward_id": reward.id,
            "episode_id": episode_id,
            "value": reward.value,
            "source": source.value,
            "rubric": rubric,
            "evaluator": evaluator,
            "created_at": reward.created_at,
        }, indent=2)
    except Exception as e:
        logger.error("Error assigning reward: %s", e)
        return json.dumps({"error": str(e)})


@ai_function
def learning_list_rewards_tool(
    episode_id: str = None,
    agent_id: str = None,
    limit: int = 50,
) -> str:
    """List rewards assigned to episodes."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        if episode_id:
            rewards = learning_store.get_rewards_for_episode(episode_id, agent)
        else:
            rewards = learning_store.query_rewards(agent_id=agent, limit=limit)
        rewards_data = []
        for r in rewards:
            rewards_data.append({
                "id": r.id,
                "episode_id": r.episode_id,
                "source": r.source.value,
                "value": r.value,
                "raw_value": r.raw_value,
                "metric": r.metric.value if r.metric else None,
                "rubric": r.rubric,
                "evaluator": r.evaluator,
                "created_at": r.created_at,
            })
        return json.dumps({
            "agent_id": agent,
            "episode_filter": episode_id,
            "rewards_found": len(rewards_data),
            "rewards": rewards_data,
        }, indent=2)
    except Exception as e:
        logger.error("Error listing rewards: %s", e)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Policy inspection & batch training
# ---------------------------------------------------------------------------

@ai_function
def learning_get_policy_tool(agent_id: str = None) -> str:
    """Return the most recent persisted policy snapshot."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        snapshot = learning_store.get_latest_policy(agent)
        if snapshot is None:
            return json.dumps({
                "has_policy": False,
                "message": "No policy snapshot stored yet. Run init-policy first.",
            }, indent=2)
        return json.dumps({
            "has_policy": True,
            "policy_id": snapshot.id,
            "agent_id": snapshot.agent_id,
            "version": snapshot.version,
            "baseline": snapshot.baseline,
            "episodes_seen": snapshot.episodes_seen,
            "updates_applied": snapshot.updates_applied,
            "actions": [
                {"id": a.id, "description": a.description, "parameters": a.parameters}
                for a in snapshot.actions
            ],
            "logits": snapshot.logits,
            "created_at": snapshot.created_at,
        }, indent=2)
    except Exception as e:
        logger.error("Error getting policy: %s", e)
        return json.dumps({"error": str(e)})


@ai_function
def learning_run_batch_tool(
    agent_id: str = None,
    episode_limit: int = 200,
    score_missing: bool = True,
) -> str:
    """Run an offline REINFORCE batch over recent episodes.

    Re-scores episodes that have no aggregate reward yet (when
    ``score_missing`` is true), updates the softmax policy, and stores
    the new snapshot.
    """
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    runner = _build_runner()
    if runner is None:
        return json.dumps({"error": "No policy snapshot found. Initialise a policy first."})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        run = runner.run_offline_batch(
            agent_id=agent,
            episode_limit=episode_limit,
            score_missing=score_missing,
        )
        return json.dumps({
            "training_run_id": run.id,
            "agent_id": run.agent_id,
            "policy_id": run.policy_id,
            "status": run.status.value,
            "metrics": run.metrics,
            "error_message": run.error_message,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }, indent=2)
    except Exception as e:
        logger.error("Error running batch update: %s", e)
        return json.dumps({"error": str(e)})


@ai_function
def learning_list_training_runs_tool(agent_id: str = None, limit: int = 20) -> str:
    """List recent training runs for an agent."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        runs = learning_store.list_training_runs(agent_id=agent, limit=limit)
        runs_data = [
            {
                "id": r.id,
                "policy_id": r.policy_id,
                "algorithm": r.algorithm,
                "status": r.status.value,
                "episode_count": len(r.episode_ids),
                "metrics": r.metrics,
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "created_at": r.created_at,
            }
            for r in runs
        ]
        return json.dumps({
            "agent_id": agent,
            "runs_found": len(runs_data),
            "training_runs": runs_data,
        }, indent=2)
    except Exception as e:
        logger.error("Error listing training runs: %s", e)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

@ai_function
def learning_get_stats_tool(agent_id: str = None) -> str:
    """Get comprehensive statistics for the agent-learning system."""
    if not LEARNING_AVAILABLE or learning_store is None:
        return json.dumps({"error": "Agent Learning SDK not available"})
    try:
        agent = agent_id or LEARNING_AGENT_ID
        episodes = learning_store.query_episodes(agent_id=agent, limit=1000)
        rewards = learning_store.query_rewards(agent_id=agent, limit=1000)
        runs = learning_store.list_training_runs(agent_id=agent, limit=100)
        snapshot = learning_store.get_latest_policy(agent)

        reward_values = [r.value for r in rewards]
        avg_reward = sum(reward_values) / len(reward_values) if reward_values else 0.0
        status_counts: dict = {}
        for run in runs:
            s = run.status.value
            status_counts[s] = status_counts.get(s, 0) + 1
        return json.dumps({
            "agent_id": agent,
            "capture_enabled": ENABLE_LEARNING_CAPTURE,
            "statistics": {
                "total_episodes": len(episodes),
                "total_rewards": len(rewards),
                "average_reward": round(avg_reward, 3),
                "total_training_runs": len(runs),
                "training_run_status": status_counts,
            },
            "active_policy": {
                "has_policy": snapshot is not None,
                "policy_id": snapshot.id if snapshot else None,
                "version": snapshot.version if snapshot else None,
                "baseline": snapshot.baseline if snapshot else None,
                "episodes_seen": snapshot.episodes_seen if snapshot else None,
                "action_count": len(snapshot.actions) if snapshot else 0,
            },
            "base_model": FOUNDRY_MODEL_DEPLOYMENT_NAME,
        }, indent=2)
    except Exception as e:
        logger.error("Error getting learning stats: %s", e)
        return json.dumps({"error": str(e)})
