"""
Shared configuration, credentials, and client initialization.

All global state (CosmosDB clients, memory providers, Agent Learning RL
components, environment-driven constants) lives here so that every other
module can ``from config import ...`` without circular dependencies.
"""

import os
import logging
from typing import Dict, Any, Optional

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient, exceptions as cosmos_exceptions
from dotenv import load_dotenv

from memory import (
    ShortTermMemory, MemoryEntry, MemoryType, CompositeMemory, LongTermMemory,
    AISEARCH_CONTEXT_PROVIDER_AVAILABLE,
)

# Native agent-learning SDK imports (policy-based reinforcement learning)
try:
    from agent_learning import (
        Action,
        CaptureConfig,
        CosmosConfig,
        Episode,
        EpisodeCapture,
        JudgeConfig,
        LearnerConfig,
        LearningRunner,
        MetricResult,
        PolicySnapshot,
        Reward,
        RewardShaper,
        RewardSource,
        RewardWriter,
        ShapingConfig,
        SoftmaxPolicy,
        TrainingRun,
        TrainingStatus,
    )
    from agent_learning.learners import ReinforceLearner
    from agent_learning.metrics import default_metrics
    from agent_learning.storage import CosmosStore, InMemoryStore, get_default_store

    LEARNING_AVAILABLE = True
except ImportError:
    LEARNING_AVAILABLE = False

# Azure AI Evaluation SDK imports (for agent evaluators)
try:
    from azure.ai.evaluation import (
        IntentResolutionEvaluator,
        ToolCallAccuracyEvaluator,
        TaskAdherenceEvaluator,
        GroundednessEvaluator,
        RelevanceEvaluator,
    )
    EVALUATION_AVAILABLE = True
except ImportError:
    EVALUATION_AVAILABLE = False

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
# In DEVELOPMENT_MODE we skip the AZD-hydrated .env entirely so the launcher
# can run the orchestrator without Cosmos / Foundry / Search credentials.
# Otherwise we still load .env with override=False so any process-level env
# vars (e.g. container env, deploy-time injection) take precedence.
if os.getenv("DEVELOPMENT_MODE", "").lower() not in {"1", "true", "yes", "on"}:
    load_dotenv(override=False)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("digital_quality_orchestrator")

if not EVALUATION_AVAILABLE:
    logger.warning("azure-ai-evaluation not available - evaluation tools will be disabled")

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
from fastapi import FastAPI

app = FastAPI(
    title="AKS Digital Quality Orchestrator MCP Server",
    description="Model Context Protocol Server for AI Agents with Semantic Reasoning",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CosmosDB
# ---------------------------------------------------------------------------
# ``COSMOSDB_DATABASE_NAME`` is the *application* database (sessions, tasks,
# plans, short-term memory). ``AGENT_LEARNING_DATABASE_NAME`` is the RL
# persistence database used by the agent-learning SDK (episodes, metrics,
# rewards, policies, runs). They are intentionally separate so the RL
# surface can be wiped/recreated without touching application data.
COSMOSDB_ENDPOINT = os.getenv("COSMOSDB_ENDPOINT", "")
COSMOSDB_DATABASE_NAME = os.getenv("COSMOSDB_DATABASE_NAME", "dq")
AGENT_LEARNING_DATABASE_NAME = os.getenv("AGENT_LEARNING_DATABASE_NAME", "dq_rl")
COSMOSDB_TASKS_CONTAINER = "tasks"
COSMOSDB_PLANS_CONTAINER = "plans"

cosmos_client = None
cosmos_database = None
cosmos_tasks_container = None
cosmos_plans_container = None

if COSMOSDB_ENDPOINT:
    try:
        credential = DefaultAzureCredential()
        cosmos_client = CosmosClient(COSMOSDB_ENDPOINT, credential=credential)
        cosmos_database = cosmos_client.get_database_client(COSMOSDB_DATABASE_NAME)
        cosmos_tasks_container = cosmos_database.get_container_client(COSMOSDB_TASKS_CONTAINER)
        cosmos_plans_container = cosmos_database.get_container_client(COSMOSDB_PLANS_CONTAINER)
        logger.info("CosmosDB client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize CosmosDB client: {e}")
else:
    logger.warning("COSMOSDB_ENDPOINT not configured - task storage will not work")

# ---------------------------------------------------------------------------
# Memory Providers
# ---------------------------------------------------------------------------
chat: Optional[ShortTermMemory] = None
composite_memory: Optional[CompositeMemory] = None

if COSMOSDB_ENDPOINT:
    try:
        chat = ShortTermMemory(
            endpoint=COSMOSDB_ENDPOINT,
            database_name=COSMOSDB_DATABASE_NAME,
            container_name="chat",
            default_ttl=3600,
        )
        composite_memory = CompositeMemory(
            short_term=chat,
            long_term=None,
        )
        logger.info("Memory providers initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize memory providers: {e}")
else:
    logger.warning("COSMOSDB_ENDPOINT not configured - memory providers will not work")

# In-memory session storage (replace with Redis for production)
sessions: Dict[str, Dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Foundry / model configuration
# ---------------------------------------------------------------------------
FOUNDRY_PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
FOUNDRY_MODEL_DEPLOYMENT_NAME = os.getenv("FOUNDRY_MODEL_DEPLOYMENT_NAME", "gpt-4o-mini")
EVALUATOR_MODEL_DEPLOYMENT_NAME = os.getenv("EVALUATOR_MODEL_DEPLOYMENT_NAME", "gpt-5.2-chat")
EMBEDDING_MODEL_DEPLOYMENT_NAME = os.getenv("EMBEDDING_MODEL_DEPLOYMENT_NAME", "text-embedding-3-large")

# ---------------------------------------------------------------------------
# Agent Learning (native policy-bandit RL) configuration
# ---------------------------------------------------------------------------
LEARNING_AGENT_ID = os.getenv("LEARNING_AGENT_ID", "dq")
ENABLE_LEARNING_CAPTURE = os.getenv("ENABLE_LEARNING_CAPTURE", "false").lower() == "true"

learning_store: Optional[Any] = None
learning_policy: Optional["SoftmaxPolicy"] = None
learning_capture: Optional["EpisodeCapture"] = None
learning_runner: Optional["LearningRunner"] = None
learning_reward_writer: Optional["RewardWriter"] = None
learning_judge_config: Optional["JudgeConfig"] = None
learning_capture_config: Optional["CaptureConfig"] = None

if LEARNING_AVAILABLE:
    try:
        # Cosmos for persistence (falls back to in-memory if not configured or
        # if Cosmos is unreachable from the current host — e.g. local dev
        # without a firewall allowlist). RL records live in their own
        # database (``AGENT_LEARNING_DATABASE_NAME``, default ``dq_rl``) so
        # they stay isolated from the application database (``dq``).
        if COSMOSDB_ENDPOINT:
            cosmos_cfg = CosmosConfig(
                endpoint=COSMOSDB_ENDPOINT,
                database_name=AGENT_LEARNING_DATABASE_NAME,
            )
            try:
                cosmos_candidate = CosmosStore(cosmos_cfg)
                # Probe connectivity now so we fail fast and can fall back to
                # the in-memory store. ``get_latest_policy`` is a cheap read.
                cosmos_candidate.get_latest_policy(LEARNING_AGENT_ID)
                learning_store = cosmos_candidate
            except Exception as cosmos_err:  # noqa: BLE001
                logger.warning(
                    "Agent Learning SDK: Cosmos store unreachable (%s); "
                    "falling back to InMemoryStore for local development.",
                    cosmos_err,
                )
                learning_store = InMemoryStore()
        else:
            learning_store = InMemoryStore()

        # Judge model configuration (reuses the orchestrator's evaluator deployment)
        judge_endpoint = (
            os.getenv("AGENT_LEARNING_JUDGE_ENDPOINT")
            or os.getenv("AZURE_OPENAI_ENDPOINT", "")
        )
        judge_deployment = (
            os.getenv("AGENT_LEARNING_JUDGE_DEPLOYMENT")
            or os.getenv("EVALUATOR_MODEL_DEPLOYMENT_NAME", "")
        )
        if judge_endpoint and judge_deployment:
            learning_judge_config = JudgeConfig(
                azure_endpoint=judge_endpoint,
                azure_deployment=judge_deployment,
            )

        # Episode capture singleton
        learning_capture_config = CaptureConfig(
            agent_id=LEARNING_AGENT_ID,
            enabled=ENABLE_LEARNING_CAPTURE,
        )
        learning_capture = EpisodeCapture(
            store=learning_store, config=learning_capture_config
        )

        # Reward writer wraps the store and persists per-metric + aggregate rewards
        learning_reward_writer = RewardWriter(learning_store)

        # Construct/restore the policy from the latest snapshot when present
        latest = learning_store.get_latest_policy(LEARNING_AGENT_ID)
        if latest is not None:
            learning_policy = SoftmaxPolicy.from_snapshot(latest)

        # The training runner is created lazily only when needed (avoid heavy init)
        logger.info(
            f"Agent Learning SDK initialized "
            f"(capture={ENABLE_LEARNING_CAPTURE}, store={type(learning_store).__name__}, "
            f"judge_configured={learning_judge_config is not None and learning_judge_config.enabled}, "
            f"policy_loaded={learning_policy is not None})"
        )
    except Exception as e:
        logger.warning(f"Failed to initialize Agent Learning SDK: {e}")
else:
    logger.info("Agent Learning SDK not available - native RL features disabled")

# ---------------------------------------------------------------------------
# AI Search
# ---------------------------------------------------------------------------
AZURE_SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_INDEX_NAME = os.getenv("AZURE_SEARCH_INDEX_NAME", "task-instructions")
AZURE_SEARCH_KNOWLEDGE_BASE_NAME = os.getenv("AZURE_SEARCH_KNOWLEDGE_BASE_NAME", "task-instructions-kb")

long_term_memory: Optional[LongTermMemory] = None

# ---------------------------------------------------------------------------
# Model selection helper
# ---------------------------------------------------------------------------

def get_model_deployment() -> str:
    """
    Return the active model deployment.

    The native agent-learning SDK optimises *prompts/strategies* over a
    fixed model deployment rather than tuning new model weights. This
    helper is kept as a stable indirection so callers don't need to know
    which environment variable holds the deployment name.
    """
    return FOUNDRY_MODEL_DEPLOYMENT_NAME
