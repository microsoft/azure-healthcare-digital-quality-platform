"""
MCP Tool executor — execute_tool() and _execute_tool_impl() dispatch.
Extracted from digital_quality_orchestrator.py.
"""

import json
import logging
import uuid
import time
from typing import Dict, Any
from dataclasses import asdict
from datetime import datetime

from azure.identity import DefaultAzureCredential
from azure.cosmos import exceptions as cosmos_exceptions

from config import (
    FOUNDRY_PROJECT_ENDPOINT,
    FOUNDRY_MODEL_DEPLOYMENT_NAME,
    cosmos_tasks_container,
    cosmos_plans_container,
    chat,
    long_term_memory,
    learning_capture,
    get_model_deployment,
)
from schemas import MCPToolResult
from embedding import (
    get_embedding,
    find_similar_tasks,
    analyze_intent,
    generate_plan_with_instructions,
)
from memory import MemoryEntry, MemoryType

from digital_quality_measures import (
    FHIRQualityRequest,
    compute_quality_measures,
    get_measure_catalog,
    gather_context,
    plan_quality_measures as dq_plan_quality_measures,
    _summarise_patient_context,
)

from evaluation_tools import (
    handle_get_evaluation_status,
    handle_evaluate_intent_resolution,
    handle_evaluate_tool_call_accuracy,
    handle_evaluate_task_adherence,
    handle_evaluate_groundedness,
    handle_evaluate_relevance,
    handle_run_agent_evaluation,
    handle_run_batch_evaluation,
)

logger = logging.getLogger(__name__)

# Import Agent Learning dispatch handlers
try:
    from config import (
        LEARNING_AVAILABLE,
        LEARNING_AGENT_ID,
        ENABLE_LEARNING_CAPTURE,
        learning_store,
        learning_policy,
        learning_reward_writer,
        learning_runner,
        learning_judge_config,
        learning_capture_config,
    )
    from learning_tools import (
        learning_list_episodes_tool,
        learning_get_episode_tool,
        learning_assign_reward_tool,
        learning_list_rewards_tool,
        learning_get_policy_tool,
        learning_run_batch_tool,
        learning_list_training_runs_tool,
        learning_get_stats_tool,
    )
except ImportError as _learning_import_err:
    LEARNING_AVAILABLE = False
    logger.warning("agent-learning integration unavailable: %s", _learning_import_err)

    def _learning_unavailable_stub(arguments):  # type: ignore[unused-ignore]
        return MCPToolResult(
            content=[{"type": "text", "text": "Agent Learning SDK not available"}],
            isError=True,
        )

    learning_list_episodes_tool = _learning_unavailable_stub
    learning_get_episode_tool = _learning_unavailable_stub
    learning_assign_reward_tool = _learning_unavailable_stub
    learning_list_rewards_tool = _learning_unavailable_stub
    learning_get_policy_tool = _learning_unavailable_stub
    learning_run_batch_tool = _learning_unavailable_stub
    learning_list_training_runs_tool = _learning_unavailable_stub
    learning_get_stats_tool = _learning_unavailable_stub


async def execute_tool(tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
    """Execute an MCP tool with optional Agent Learning episode capture."""
    start_time = time.time()
    result = None
    error_message = None

    try:
        result = await _execute_tool_impl(tool_name, arguments)
    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}")
        error_message = str(e)
        result = MCPToolResult(
            content=[{"type": "text", "text": f"Error: {str(e)}"}],
            isError=True,
        )

    # Capture episode via agent-learning if enabled (per-tool granularity, mirrors prior behavior).
    if learning_capture is not None and learning_capture.is_enabled():
        try:
            duration_ms = int((time.time() - start_time) * 1000)
            result_text = ""
            if result and result.content:
                for item in result.content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        result_text += item.get("text", "")
            user_input = (
                f"Call tool '{tool_name}' with arguments: "
                f"{json.dumps(arguments, default=str)}"
            )
            ctx = learning_capture.start(
                user_input=user_input,
                model_deployment=get_model_deployment(),
                metadata={"tool_name": tool_name},
            )
            learning_capture.record_tool_call(
                ctx,
                name=tool_name,
                arguments=arguments,
                result=result_text,
                duration_ms=duration_ms,
                error=error_message,
            )
            learning_capture.end(ctx, assistant_output=result_text)
        except Exception as capture_error:
            logger.warning(f"Failed to capture episode: {capture_error}")

    return result


async def _execute_tool_impl(tool_name: str, arguments: Dict[str, Any]) -> MCPToolResult:
    """Internal implementation of tool execution."""
    try:
        # =========================================
        # Core Tools
        # =========================================
        if tool_name == "ask_foundry":
            return await _handle_ask_foundry(arguments)

        elif tool_name == "digital_quality_action":
            return await _handle_digital_quality_action(arguments)

        # =========================================
        # Memory Tools
        # =========================================
        elif tool_name == "store_memory":
            return await _handle_store_memory(arguments)

        elif tool_name == "recall_memory":
            return await _handle_recall_memory(arguments)

        elif tool_name == "get_session_history":
            return await _handle_get_session_history(arguments)

        elif tool_name == "clear_session_memory":
            return await _handle_clear_session_memory(arguments)

        # =========================================
        # Agent Learning Tool Handlers (native RL via azure-agents-learning-sdk)
        # =========================================
        elif tool_name == "learning_list_episodes":
            return learning_list_episodes_tool(arguments)

        elif tool_name == "learning_get_episode":
            return learning_get_episode_tool(arguments)

        elif tool_name == "learning_assign_reward":
            return learning_assign_reward_tool(arguments)

        elif tool_name == "learning_list_rewards":
            return learning_list_rewards_tool(arguments)

        elif tool_name == "learning_get_policy":
            return learning_get_policy_tool(arguments)

        elif tool_name == "learning_run_batch":
            return learning_run_batch_tool(arguments)

        elif tool_name == "learning_list_training_runs":
            return learning_list_training_runs_tool(arguments)

        elif tool_name == "learning_get_stats":
            return learning_get_stats_tool(arguments)

        # =========================================
        # Agent Evaluation Tool Handlers
        # =========================================
        elif tool_name == "get_evaluation_status":
            return handle_get_evaluation_status(arguments)

        elif tool_name == "evaluate_intent_resolution":
            return handle_evaluate_intent_resolution(arguments)

        elif tool_name == "evaluate_tool_call_accuracy":
            return handle_evaluate_tool_call_accuracy(arguments)

        elif tool_name == "evaluate_task_adherence":
            return handle_evaluate_task_adherence(arguments)

        elif tool_name == "evaluate_groundedness":
            return handle_evaluate_groundedness(arguments)

        elif tool_name == "evaluate_relevance":
            return handle_evaluate_relevance(arguments)

        elif tool_name == "run_agent_evaluation":
            return handle_run_agent_evaluation(arguments)

        elif tool_name == "run_batch_evaluation":
            return handle_run_batch_evaluation(arguments)

        # =========================================
        # Quality Measure Tools (FHIR eCQM)
        # =========================================
        elif tool_name == "compute_quality_measures":
            return _handle_compute_quality_measures(arguments)

        elif tool_name == "list_quality_measures":
            return _handle_list_quality_measures(arguments)

        elif tool_name == "plan_quality_measures":
            return _handle_plan_quality_measures(arguments)

        elif tool_name == "get_quality_plan":
            return _handle_get_quality_plan(arguments)

        else:
            return MCPToolResult(
                content=[{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                isError=True,
            )

    except Exception as e:
        logger.error(f"Error executing tool {tool_name}: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Error: {str(e)}"}],
            isError=True,
        )


# =========================================
# Core Tool Handlers
# =========================================

async def _handle_ask_foundry(arguments):
    question = arguments.get("question")
    if not question:
        return MCPToolResult(content=[{"type": "text", "text": "No question provided"}], isError=True)
    if not FOUNDRY_PROJECT_ENDPOINT:
        return MCPToolResult(content=[{"type": "text", "text": "Foundry endpoint not configured"}], isError=True)
    try:
        from openai import AzureOpenAI
        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        base_endpoint = FOUNDRY_PROJECT_ENDPOINT.split('/api/projects')[0] if '/api/projects' in FOUNDRY_PROJECT_ENDPOINT else FOUNDRY_PROJECT_ENDPOINT
        model_deployment = get_model_deployment()
        logger.info(f"Using Foundry endpoint: {base_endpoint}, model: {model_deployment}")
        client = AzureOpenAI(azure_endpoint=base_endpoint, api_key=token.token, api_version="2024-02-15-preview")
        response = client.chat.completions.create(model=model_deployment, messages=[{"role": "user", "content": question}])
        answer = "No response generated"
        if response.choices and len(response.choices) > 0:
            answer = response.choices[0].message.content
        return MCPToolResult(content=[{"type": "text", "text": answer}])
    except Exception as e:
        logger.error(f"Error calling Foundry model: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error calling Foundry model: {str(e)}"}], isError=True)


async def _handle_digital_quality_action(arguments):
    task = arguments.get("task")
    if not task:
        return MCPToolResult(content=[{"type": "text", "text": "No task provided"}], isError=True)
    if not FOUNDRY_PROJECT_ENDPOINT:
        return MCPToolResult(content=[{"type": "text", "text": "Foundry endpoint not configured"}], isError=True)
    if not cosmos_tasks_container or not cosmos_plans_container:
        return MCPToolResult(content=[{"type": "text", "text": "CosmosDB not configured"}], isError=True)

    try:
        task_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat()

        logger.info(f"Generating embedding for task: {task[:100]}...")
        task_embedding = get_embedding(task)

        logger.info("Analyzing task intent...")
        intent = analyze_intent(task)

        logger.info("Searching for similar past tasks in CosmosDB...")
        similar_tasks = find_similar_tasks(task_embedding, threshold=0.7, limit=5)

        task_instructions = []
        long_term_context = ""
        if long_term_memory:
            logger.info("Retrieving context via LongTermMemory with AzureAISearchContextProvider...")
            try:
                long_term_context = await long_term_memory.get_context(task)
                if long_term_context:
                    logger.info(f"AzureAISearchContextProvider returned context: {len(long_term_context)} chars")
                else:
                    logger.info("AzureAISearchContextProvider returned no context")
            except Exception as e:
                logger.warning(f"Failed to retrieve context from AzureAISearchContextProvider: {e}")
            logger.info("Searching for task instructions in LongTermMemory...")
            try:
                task_instructions = await long_term_memory.search_task_instructions(
                    task_description=task, limit=3, include_steps=True,
                )
                logger.info(f"Found {len(task_instructions)} relevant task instructions from LongTermMemory")
            except Exception as e:
                logger.warning(f"Failed to retrieve task instructions from LongTermMemory: {e}")
        else:
            logger.info("Long-term memory not configured - skipping task instructions lookup")

        logger.info("Generating execution plan...")
        plan_steps = generate_plan_with_instructions(task, similar_tasks, task_instructions)

        task_doc = {
            'id': task_id, 'task': task, 'intent': intent,
            'embedding': task_embedding, 'created_at': timestamp,
            'similar_task_count': len(similar_tasks),
            'task_instructions_count': len(task_instructions),
            'long_term_memory_used': len(task_instructions) > 0,
        }
        cosmos_tasks_container.upsert_item(task_doc)
        logger.info(f"Task stored in CosmosDB with id: {task_id}")

        plan_doc = {
            'id': str(uuid.uuid4()), 'taskId': task_id, 'task': task, 'intent': intent,
            'steps': plan_steps,
            'similar_tasks_referenced': [{'id': st['id'], 'similarity': st['similarity']} for st in similar_tasks],
            'task_instructions_used': [{'name': ti.get('name', 'unknown')} for ti in task_instructions] if task_instructions else [],
            'created_at': timestamp, 'status': 'planned',
        }
        cosmos_plans_container.upsert_item(plan_doc)
        logger.info(f"Plan stored in CosmosDB for task: {task_id}")

        response = {
            'task_id': task_id, 'task': task, 'intent': intent,
            'analysis': {
                'similar_tasks_found': len(similar_tasks),
                'similar_tasks': [{'task': st['task'], 'intent': st['intent'], 'similarity_score': round(st['similarity'], 3)} for st in similar_tasks],
                'task_instructions_found': len(task_instructions),
                'task_instructions': [{'name': ti.get('name', 'unknown'), 'description': ti.get('description', '')[:200] if ti.get('description') else ''} for ti in task_instructions] if task_instructions else [],
            },
            'plan': {'steps': plan_steps, 'total_steps': len(plan_steps)},
            'metadata': {
                'created_at': timestamp,
                'embedding_dimensions': len(task_embedding),
                'stored_in_cosmos': True,
                'long_term_memory_used': len(task_instructions) > 0,
            },
        }
        return MCPToolResult(content=[{"type": "text", "text": json.dumps(response, indent=2)}])
    except Exception as e:
        logger.error(f"Error in digital_quality_action: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error in digital_quality_action: {str(e)}"}], isError=True)


# =========================================
# Memory Tool Handlers
# =========================================

async def _handle_store_memory(arguments):
    content = arguments.get("content")
    session_id = arguments.get("session_id")
    memory_type = arguments.get("memory_type", "context")
    if not content:
        return MCPToolResult(content=[{"type": "text", "text": "No content provided"}], isError=True)
    if not session_id:
        return MCPToolResult(content=[{"type": "text", "text": "No session_id provided"}], isError=True)
    if not chat:
        return MCPToolResult(content=[{"type": "text", "text": "Memory provider not configured"}], isError=True)
    try:
        type_map = {"context": MemoryType.CONTEXT, "conversation": MemoryType.CONVERSATION, "task": MemoryType.TASK, "plan": MemoryType.PLAN}
        mem_type = type_map.get(memory_type.lower(), MemoryType.CONTEXT)
        embedding = None
        if FOUNDRY_PROJECT_ENDPOINT:
            try:
                embedding = get_embedding(content)
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")
        entry = MemoryEntry(id=str(uuid.uuid4()), content=content, memory_type=mem_type, embedding=embedding, session_id=session_id)
        entry_id = await chat.store(entry)
        return MCPToolResult(content=[{"type": "text", "text": json.dumps({"success": True, "memory_id": entry_id, "session_id": session_id, "memory_type": memory_type, "has_embedding": embedding is not None})}])
    except Exception as e:
        logger.error(f"Error storing memory: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error storing memory: {str(e)}"}], isError=True)


async def _handle_recall_memory(arguments):
    query = arguments.get("query")
    session_id = arguments.get("session_id")
    limit = arguments.get("limit", 5)
    if not query:
        return MCPToolResult(content=[{"type": "text", "text": "No query provided"}], isError=True)
    if not session_id:
        return MCPToolResult(content=[{"type": "text", "text": "No session_id provided"}], isError=True)
    if not chat:
        return MCPToolResult(content=[{"type": "text", "text": "Memory provider not configured"}], isError=True)
    if not FOUNDRY_PROJECT_ENDPOINT:
        return MCPToolResult(content=[{"type": "text", "text": "Foundry endpoint not configured for embeddings"}], isError=True)
    try:
        query_embedding = get_embedding(query)
        results = await chat.search(query_embedding=query_embedding, limit=limit, threshold=0.6, session_id=session_id)
        memories = [{"id": r.entry.id, "content": r.entry.content, "memory_type": r.entry.memory_type.value, "similarity_score": round(r.score, 3), "created_at": r.entry.created_at} for r in results]
        return MCPToolResult(content=[{"type": "text", "text": json.dumps({"query": query, "session_id": session_id, "memories_found": len(memories), "memories": memories}, indent=2)}])
    except Exception as e:
        logger.error(f"Error recalling memory: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error recalling memory: {str(e)}"}], isError=True)


async def _handle_get_session_history(arguments):
    session_id = arguments.get("session_id")
    limit = arguments.get("limit", 20)
    if not session_id:
        return MCPToolResult(content=[{"type": "text", "text": "No session_id provided"}], isError=True)
    if not chat:
        return MCPToolResult(content=[{"type": "text", "text": "Memory provider not configured"}], isError=True)
    try:
        history = await chat.get_conversation_history(session_id, limit)
        return MCPToolResult(content=[{"type": "text", "text": json.dumps({"session_id": session_id, "message_count": len(history), "messages": history}, indent=2)}])
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error getting session history: {str(e)}"}], isError=True)


async def _handle_clear_session_memory(arguments):
    session_id = arguments.get("session_id")
    if not session_id:
        return MCPToolResult(content=[{"type": "text", "text": "No session_id provided"}], isError=True)
    if not chat:
        return MCPToolResult(content=[{"type": "text", "text": "Memory provider not configured"}], isError=True)
    try:
        count = await chat.clear_session(session_id)
        return MCPToolResult(content=[{"type": "text", "text": json.dumps({"success": True, "session_id": session_id, "entries_cleared": count})}])
    except Exception as e:
        logger.error(f"Error clearing session memory: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error clearing session memory: {str(e)}"}], isError=True)


# =========================================
# Quality Measure Tool Handlers
# =========================================

def _handle_compute_quality_measures(arguments):
    try:
        fhir_request = FHIRQualityRequest(**arguments)
        report = compute_quality_measures(fhir_request)
        return MCPToolResult(content=[{"type": "text", "text": json.dumps(asdict(report), indent=2, default=str)}])
    except ValueError as e:
        return MCPToolResult(content=[{"type": "text", "text": f"Invalid request: {str(e)}"}], isError=True)
    except Exception as e:
        logger.error(f"Error computing quality measures: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error computing quality measures: {str(e)}"}], isError=True)


def _handle_list_quality_measures(arguments):
    try:
        catalog = get_measure_catalog()
        measures = []
        for mid, mdef in catalog.items():
            name = mid
            for line in mdef.markdown_content.split("\n"):
                if line.startswith("# "):
                    name = line.lstrip("# ").strip()
                    break
            measures.append({"id": mid, "name": name, "cql_file": f"{mdef.filename_stem}.cql", "markdown_file": f"{mdef.filename_stem}.md"})
        return MCPToolResult(content=[{"type": "text", "text": json.dumps({"measures": measures, "count": len(measures)}, indent=2)}])
    except Exception as e:
        logger.error(f"Error listing quality measures: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error listing quality measures: {str(e)}"}], isError=True)


def _handle_plan_quality_measures(arguments):
    try:
        fhir_request = FHIRQualityRequest(**arguments)
        ctx = gather_context(fhir_request)
        patient_summary = _summarise_patient_context(ctx)
        catalog = get_measure_catalog()
        catalog_ids = list(catalog.keys())
        catalog_descriptions = {mid: mid for mid in catalog_ids}
        measurement_period = f"{fhir_request.measurement_period_start} to {fhir_request.measurement_period_end}"
        planned = dq_plan_quality_measures(patient_summary, catalog_ids, catalog_descriptions, measurement_period)
        return MCPToolResult(content=[{"type": "text", "text": json.dumps({"planned_measures": planned}, indent=2)}])
    except ValueError as e:
        return MCPToolResult(content=[{"type": "text", "text": f"Invalid request: {str(e)}"}], isError=True)
    except Exception as e:
        logger.error(f"Error planning quality measures: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error planning quality measures: {str(e)}"}], isError=True)


def _handle_get_quality_plan(arguments):
    plan_id = arguments.get("plan_id")
    if not plan_id:
        return MCPToolResult(content=[{"type": "text", "text": "No plan_id provided"}], isError=True)
    if not cosmos_plans_container:
        return MCPToolResult(content=[{"type": "text", "text": "CosmosDB not configured"}], isError=True)
    try:
        plan_doc = cosmos_plans_container.read_item(item=plan_id, partition_key=plan_id)
        return MCPToolResult(content=[{"type": "text", "text": json.dumps(plan_doc, indent=2, default=str)}])
    except cosmos_exceptions.CosmosResourceNotFoundError:
        return MCPToolResult(content=[{"type": "text", "text": f"Plan {plan_id} not found"}], isError=True)
    except Exception as e:
        logger.error(f"Error retrieving plan {plan_id}: {e}")
        return MCPToolResult(content=[{"type": "text", "text": f"Error retrieving plan: {str(e)}"}], isError=True)
