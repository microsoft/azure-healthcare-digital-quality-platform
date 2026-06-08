"""
MCP Tool registry — the canonical list of MCPTool definitions.
Extracted from digital_quality_orchestrator.py.
"""

from schemas import MCPTool

tools = [
    MCPTool(
        name="ask_foundry",
        description="Ask a question and get an answer using the Azure AI Foundry model.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the AI model"
                }
            },
            "required": ["question"]
        }
    ),
    MCPTool(
        name="digital_quality_action",
        description="Execute a healthcare digital quality management task and return concrete results. Uses three memory layers: (1) Short-term memory - finds similar past tasks from CosmosDB, (2) Long-term memory - retrieves task instructions from AI Search, Identifies quality gaps, scores actions, queues outreach, creates care alerts, logs interventions, and delivers specific data-driven recommendations. Returns executed results with confirmed actions and quantified outcomes.",
        inputSchema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task description in natural language (English sentence) to analyze and plan"
                }
            },
            "required": ["task"]
        }
    ),
    MCPTool(
        name="store_memory",
        description="Store information in short-term memory for later retrieval. Useful for remembering context, user preferences, or intermediate results within a session.",
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The content to remember"
                },
                "session_id": {
                    "type": "string",
                    "description": "The session ID to associate the memory with"
                },
                "memory_type": {
                    "type": "string",
                    "description": "Type of memory: context, conversation, task, or plan",
                    "enum": ["context", "conversation", "task", "plan"]
                }
            },
            "required": ["content", "session_id"]
        }
    ),
    MCPTool(
        name="recall_memory",
        description="Recall relevant memories from short-term memory based on semantic similarity. Returns memories that are contextually related to the query.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The query to search for relevant memories"
                },
                "session_id": {
                    "type": "string",
                    "description": "The session ID to search within"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of memories to return (default: 5)"
                }
            },
            "required": ["query", "session_id"]
        }
    ),
    MCPTool(
        name="get_session_history",
        description="Get conversation history for a session. Returns the messages exchanged in the session.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID to get history for"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of messages to return (default: 20)"
                }
            },
            "required": ["session_id"]
        }
    ),
    MCPTool(
        name="clear_session_memory",
        description="Clear all short-term memory for a session. Use when starting fresh or cleaning up.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID to clear"
                }
            },
            "required": ["session_id"]
        }
    ),

    # =========================================
    # Agent Learning Tools (native RL via azure-agents-learning-sdk)
    # =========================================
    MCPTool(
        name="learning_list_episodes",
        description="List captured episodes from agent-learning. Episodes represent agent interactions (user input → tool calls → response) used as the source data for policy updates and judge evaluation.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Filter by agent ID (defaults to LEARNING_AGENT_ID)"},
                "limit": {"type": "integer", "description": "Maximum number of episodes to return (default: 20)"},
                "start_date": {"type": "string", "description": "Filter episodes after this ISO timestamp"},
                "end_date": {"type": "string", "description": "Filter episodes before this ISO timestamp"},
                "policy_id": {"type": "string", "description": "Filter by the policy snapshot id that produced the episode"}
            },
            "required": []
        }
    ),
    MCPTool(
        name="learning_get_episode",
        description="Retrieve a single episode including tool calls, conversation context, model deployment, and latency.",
        inputSchema={
            "type": "object",
            "properties": {
                "episode_id": {"type": "string", "description": "Episode ID"},
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"}
            },
            "required": ["episode_id"]
        }
    ),
    MCPTool(
        name="learning_assign_reward",
        description="Attach a scalar reward to an episode. Used to feed human/eval signals into the policy update pipeline.",
        inputSchema={
            "type": "object",
            "properties": {
                "episode_id": {"type": "string", "description": "Target episode"},
                "reward_value": {"type": "number", "description": "Reward in [-1, 1] (or unbounded if the shaping pipeline normalizes)"},
                "reward_source": {
                    "type": "string",
                    "description": "Provenance of the reward",
                    "enum": ["human_approval", "metric", "aggregate", "test_result", "latency_penalty", "cost_penalty"]
                },
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"},
                "rubric": {"type": "string", "description": "Optional rubric or metric name"},
                "evaluator": {"type": "string", "description": "Who/what issued the reward"},
                "comments": {"type": "string", "description": "Free-form notes"}
            },
            "required": ["episode_id", "reward_value"]
        }
    ),
    MCPTool(
        name="learning_list_rewards",
        description="List rewards assigned to episodes, optionally filtered to one episode.",
        inputSchema={
            "type": "object",
            "properties": {
                "episode_id": {"type": "string", "description": "Optional episode filter"},
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"},
                "limit": {"type": "integer", "description": "Maximum number of rewards to return"}
            },
            "required": []
        }
    ),
    MCPTool(
        name="learning_get_policy",
        description="Return the latest softmax bandit policy snapshot for an agent (action ids, logits, normalised probabilities, and version).",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"}
            },
            "required": []
        }
    ),
    MCPTool(
        name="learning_run_batch",
        description="Run an offline REINFORCE batch over recent episodes: judges the episodes with the configured Azure AI Evaluation metrics, shapes rewards, applies the policy update, and persists a new TrainingRun + PolicySnapshot.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"},
                "limit": {"type": "integer", "description": "Maximum number of recent episodes to include (default: 50)"},
                "start_date": {"type": "string", "description": "ISO start date filter"},
                "end_date": {"type": "string", "description": "ISO end date filter"}
            },
            "required": []
        }
    ),
    MCPTool(
        name="learning_list_training_runs",
        description="List historical training runs (REINFORCE batches) for an agent.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"},
                "limit": {"type": "integer", "description": "Maximum number of runs to return"}
            },
            "required": []
        }
    ),
    MCPTool(
        name="learning_get_stats",
        description="Return aggregate statistics: episode count, reward count, average reward, training run count by status, and active policy version.",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent ID (defaults to LEARNING_AGENT_ID)"}
            },
            "required": []
        }
    ),

    # =========================================
    # Agent Evaluation Tools (Azure AI Eval SDK)
    # =========================================
    MCPTool(
        name="evaluate_intent_resolution",
        description="Evaluate how well an agent resolved the user's intent using the Azure AI Evaluation SDK IntentResolutionEvaluator. Returns a score from 1-5.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user query or conversation history"},
                "response": {"type": "string", "description": "The agent's response to evaluate"}
            },
            "required": ["query", "response"]
        }
    ),
    MCPTool(
        name="evaluate_tool_call_accuracy",
        description="Evaluate the accuracy of tool calls made by an agent using the Azure AI Evaluation SDK ToolCallAccuracyEvaluator. Returns a score from 1-5.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user query"},
                "tool_calls": {
                    "type": "array",
                    "description": "Array of tool calls made by the agent",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {"type": "string"},
                            "tool_call_id": {"type": "string"},
                            "name": {"type": "string"},
                            "arguments": {"type": "object"}
                        }
                    }
                },
                "tool_definitions": {"type": "array", "description": "Array of available tool definitions (optional, uses defaults if not provided)"}
            },
            "required": ["query", "tool_calls"]
        }
    ),
    MCPTool(
        name="evaluate_task_adherence",
        description="Evaluate how well an agent's response adheres to the assigned task using the Azure AI Evaluation SDK TaskAdherenceEvaluator. Returns flagged (true/false) and reasoning.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user query (task)"},
                "response": {"type": "string", "description": "The agent's response"},
                "tool_calls": {"type": "array", "description": "Optional array of tool calls made (for context)"},
                "system_message": {"type": "string", "description": "Optional system message defining the agent's role"}
            },
            "required": ["query", "response"]
        }
    ),
    MCPTool(
        name="evaluate_groundedness",
        description="Evaluate how well an agent's response is grounded in the provided context using the Azure AI Evaluation SDK GroundednessEvaluator. Returns a score from 1-5.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user query"},
                "response": {"type": "string", "description": "The agent's response to evaluate"},
                "context": {"type": "string", "description": "The grounding context/source documents the response should be based on"}
            },
            "required": ["query", "response", "context"]
        }
    ),
    MCPTool(
        name="evaluate_relevance",
        description="Evaluate how relevant an agent's response is to the user query using the Azure AI Evaluation SDK RelevanceEvaluator. Returns a score from 1-5.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user query"},
                "response": {"type": "string", "description": "The agent's response to evaluate"}
            },
            "required": ["query", "response"]
        }
    ),
    MCPTool(
        name="run_agent_evaluation",
        description="Run a comprehensive evaluation on agent response data using all five evaluators (IntentResolution, ToolCallAccuracy, TaskAdherence, Groundedness, Relevance). Returns scores and pass/fail status.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user query"},
                "response": {"type": "string", "description": "The agent's response"},
                "tool_calls": {"type": "array", "description": "Array of tool calls made by the agent"},
                "tool_definitions": {"type": "array", "description": "Available tool definitions (optional)"},
                "system_message": {"type": "string", "description": "Optional system message"},
                "context": {"type": "string", "description": "Optional grounding context for GroundednessEvaluator"},
                "thresholds": {
                    "type": "object",
                    "description": "Optional score thresholds (default: 3 for each)",
                    "properties": {
                        "intent_resolution": {"type": "integer"},
                        "tool_call_accuracy": {"type": "integer"},
                        "task_adherence": {"type": "integer"},
                        "groundedness": {"type": "integer"},
                        "relevance": {"type": "integer"}
                    }
                }
            },
            "required": ["query", "response"]
        }
    ),
    MCPTool(
        name="run_batch_evaluation",
        description="Run evaluation on multiple query/response pairs. Returns aggregated metrics including average scores and pass rates.",
        inputSchema={
            "type": "object",
            "properties": {
                "evaluation_data": {
                    "type": "array",
                    "description": "Array of evaluation items, each containing query, response, and optional tool_calls/context",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "response": {"type": "string"},
                            "tool_calls": {"type": "array"},
                            "system_message": {"type": "string"},
                            "context": {"type": "string"}
                        },
                        "required": ["query", "response"]
                    }
                },
                "thresholds": {"type": "object", "description": "Optional score thresholds"}
            },
            "required": ["evaluation_data"]
        }
    ),
    MCPTool(
        name="get_evaluation_status",
        description="Check if agent evaluation tools are available and properly configured.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),

    # =========================================
    # Quality Measure Tools (FHIR eCQM)
    # =========================================
    MCPTool(
        name="compute_quality_measures",
        description="Compute eCQM quality measures for a patient from FHIR R4 data. Uses LLM-driven measure evaluation against auto-discovered CQL measure definitions. Returns measure results including initial population, denominator, numerator, and gaps in care.",
        inputSchema={
            "type": "object",
            "properties": {
                "fhir_bundle": {"type": "object", "description": "FHIR R4 Bundle containing Patient and related resources"},
                "patient": {"type": "object", "description": "FHIR R4 Patient resource (if not in bundle)"},
                "conditions": {"type": "array", "description": "FHIR R4 Condition resources"},
                "encounters": {"type": "array", "description": "FHIR R4 Encounter resources"},
                "observations": {"type": "array", "description": "FHIR R4 Observation resources"},
                "procedures": {"type": "array", "description": "FHIR R4 Procedure resources"},
                "coverages": {"type": "array", "description": "FHIR R4 Coverage resources"},
                "measurement_period_start": {"type": "string", "description": "Start of measurement period (YYYY-MM-DD, default: 2025-01-01)"},
                "measurement_period_end": {"type": "string", "description": "End of measurement period (YYYY-MM-DD, default: 2025-12-31)"},
                "measures": {"type": "array", "description": "Explicit list of measure IDs to evaluate (if null, LLM identifies applicable measures)", "items": {"type": "string"}}
            },
            "required": []
        }
    ),
    MCPTool(
        name="list_quality_measures",
        description="List all available quality measures in the catalog. Measures are auto-discovered from CQL and markdown files.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": []
        }
    ),
    MCPTool(
        name="plan_quality_measures",
        description="Use LLM to identify applicable quality measures for a patient's FHIR context without executing evaluation. Returns planned measure IDs.",
        inputSchema={
            "type": "object",
            "properties": {
                "fhir_bundle": {"type": "object", "description": "FHIR R4 Bundle containing Patient and related resources"},
                "patient": {"type": "object", "description": "FHIR R4 Patient resource (if not in bundle)"},
                "conditions": {"type": "array", "description": "FHIR R4 Condition resources"},
                "encounters": {"type": "array", "description": "FHIR R4 Encounter resources"},
                "observations": {"type": "array", "description": "FHIR R4 Observation resources"},
                "procedures": {"type": "array", "description": "FHIR R4 Procedure resources"},
                "coverages": {"type": "array", "description": "FHIR R4 Coverage resources"},
                "measurement_period_start": {"type": "string", "description": "Start of measurement period (YYYY-MM-DD, default: 2025-01-01)"},
                "measurement_period_end": {"type": "string", "description": "End of measurement period (YYYY-MM-DD, default: 2025-12-31)"}
            },
            "required": []
        }
    ),
    MCPTool(
        name="get_quality_plan",
        description="Retrieve a quality measure plan and its task statuses from CosmosDB by plan ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "The ID of the quality measure plan to retrieve"}
            },
            "required": ["plan_id"]
        }
    ),
]
