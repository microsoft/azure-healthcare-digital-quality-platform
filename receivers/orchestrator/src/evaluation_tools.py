"""
Evaluation tool dispatch handlers for Azure AI Evaluation SDK.
Extracted from digital_quality_orchestrator.py.
"""

import json
import logging
from azure.identity import DefaultAzureCredential

from config import (
    EVALUATION_AVAILABLE,
    FOUNDRY_PROJECT_ENDPOINT,
    EVALUATOR_MODEL_DEPLOYMENT_NAME,
)
from schemas import MCPToolResult

try:
    from azure.ai.evaluation import (
        IntentResolutionEvaluator,
        ToolCallAccuracyEvaluator,
        TaskAdherenceEvaluator,
        GroundednessEvaluator,
        RelevanceEvaluator,
    )
except ImportError:
    pass

logger = logging.getLogger(__name__)


def _get_eval_model_config():
    """Build model config dict for evaluators."""
    base_endpoint = (
        FOUNDRY_PROJECT_ENDPOINT.split('/api/projects')[0]
        if '/api/projects' in FOUNDRY_PROJECT_ENDPOINT
        else FOUNDRY_PROJECT_ENDPOINT
    )
    return {
        "azure_endpoint": base_endpoint.rstrip('/'),
        "azure_deployment": EVALUATOR_MODEL_DEPLOYMENT_NAME,
        "api_version": "2024-10-21",
    }


def _check_eval_available():
    """Return an MCPToolResult error if evaluation is not available, else None."""
    if not EVALUATION_AVAILABLE:
        return MCPToolResult(
            content=[{"type": "text", "text": "Azure AI Evaluation SDK not available. Install with: pip install azure-ai-evaluation"}],
            isError=True,
        )
    if not FOUNDRY_PROJECT_ENDPOINT:
        return MCPToolResult(
            content=[{"type": "text", "text": "FOUNDRY_PROJECT_ENDPOINT not configured"}],
            isError=True,
        )
    return None


def handle_get_evaluation_status(arguments):
    return MCPToolResult(
        content=[{
            "type": "text",
            "text": json.dumps({
                "evaluation_available": EVALUATION_AVAILABLE,
                "foundry_configured": bool(FOUNDRY_PROJECT_ENDPOINT),
                "model_deployment": EVALUATOR_MODEL_DEPLOYMENT_NAME if FOUNDRY_PROJECT_ENDPOINT else None,
                "evaluators": [
                    "IntentResolutionEvaluator", "ToolCallAccuracyEvaluator",
                    "TaskAdherenceEvaluator", "GroundednessEvaluator", "RelevanceEvaluator",
                ] if EVALUATION_AVAILABLE else [],
                "message": (
                    "Evaluation tools ready"
                    if EVALUATION_AVAILABLE and FOUNDRY_PROJECT_ENDPOINT
                    else "Evaluation tools not available - check azure-ai-evaluation package and FOUNDRY_PROJECT_ENDPOINT"
                ),
            }, indent=2),
        }],
    )


def handle_evaluate_intent_resolution(arguments):
    err = _check_eval_available()
    if err:
        return err

    query = arguments.get("query")
    response = arguments.get("response")
    if not query or not response:
        return MCPToolResult(
            content=[{"type": "text", "text": "Both 'query' and 'response' are required"}],
            isError=True,
        )

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()
        evaluator = IntentResolutionEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        result = evaluator(query=query, response=response)
        return MCPToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "evaluator": "IntentResolutionEvaluator",
                    "query": query[:100] + "..." if len(query) > 100 else query,
                    "score": result.get("intent_resolution", 0),
                    "explanation": result.get("intent_resolution_reason", ""),
                    "threshold_recommendation": 3,
                    "passed": result.get("intent_resolution", 0) >= 3,
                }, indent=2),
            }],
        )
    except Exception as e:
        logger.error(f"Error in evaluate_intent_resolution: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Evaluation error: {str(e)}"}],
            isError=True,
        )


def handle_evaluate_tool_call_accuracy(arguments):
    err = _check_eval_available()
    if err:
        return err

    query = arguments.get("query")
    tool_calls = arguments.get("tool_calls", [])
    tool_definitions = arguments.get("tool_definitions")

    if not query:
        return MCPToolResult(
            content=[{"type": "text", "text": "'query' is required"}],
            isError=True,
        )

    if not tool_definitions:
        tool_definitions = [
            {"name": "get_account_profile", "description": "Retrieves account profile and details.", "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]}},
            {"name": "get_recent_activities", "description": "Gets recent activities for an account.", "parameters": {"type": "object", "properties": {"account_id": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["account_id"]}},
            {"name": "recommend_next_actions", "description": "Generates recommended next actions.", "parameters": {"type": "object", "properties": {"account_context": {"type": "object"}}, "required": ["account_context"]}},
            {"name": "create_followup_task", "description": "Creates a follow-up task.", "parameters": {"type": "object", "properties": {"action": {"type": "string"}, "priority": {"type": "string"}, "due_date": {"type": "string"}}, "required": ["action"]}},
            {"name": "digital_quality_action", "description": "Analyzes a task and generates an action plan.", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}},
        ]

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()
        evaluator = ToolCallAccuracyEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        result = evaluator(query=query, tool_calls=tool_calls, tool_definitions=tool_definitions)
        return MCPToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "evaluator": "ToolCallAccuracyEvaluator",
                    "query": query[:100] + "..." if len(query) > 100 else query,
                    "tool_calls_count": len(tool_calls),
                    "score": result.get("tool_call_accuracy", 0),
                    "explanation": result.get("tool_call_accuracy_reason", ""),
                    "threshold_recommendation": 3,
                    "passed": result.get("tool_call_accuracy", 0) >= 3,
                }, indent=2),
            }],
        )
    except Exception as e:
        logger.error(f"Error in evaluate_tool_call_accuracy: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Evaluation error: {str(e)}"}],
            isError=True,
        )


def handle_evaluate_task_adherence(arguments):
    err = _check_eval_available()
    if err:
        return err

    query = arguments.get("query")
    response = arguments.get("response")
    tool_calls = arguments.get("tool_calls", [])
    system_message = arguments.get("system_message", "")

    if not query or not response:
        return MCPToolResult(
            content=[{"type": "text", "text": "Both 'query' and 'response' are required"}],
            isError=True,
        )

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()
        evaluator = TaskAdherenceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        eval_kwargs = {"query": query, "response": response}
        if tool_calls:
            eval_kwargs["tool_calls"] = tool_calls
        if system_message:
            eval_kwargs["system_message"] = system_message

        result = evaluator(**eval_kwargs)
        return MCPToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "evaluator": "TaskAdherenceEvaluator",
                    "query": query[:100] + "..." if len(query) > 100 else query,
                    "flagged": result.get("task_adherence", False),
                    "reasoning": result.get("task_adherence_reason", ""),
                    "passed": not result.get("task_adherence", True),
                }, indent=2),
            }],
        )
    except Exception as e:
        logger.error(f"Error in evaluate_task_adherence: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Evaluation error: {str(e)}"}],
            isError=True,
        )


def handle_evaluate_groundedness(arguments):
    err = _check_eval_available()
    if err:
        return err

    query = arguments.get("query")
    response = arguments.get("response")
    context = arguments.get("context")

    if not query or not response or not context:
        return MCPToolResult(
            content=[{"type": "text", "text": "'query', 'response', and 'context' are all required"}],
            isError=True,
        )

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()
        evaluator = GroundednessEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        result = evaluator(query=query, response=response, context=context)
        return MCPToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "evaluator": "GroundednessEvaluator",
                    "query": query[:100] + "..." if len(query) > 100 else query,
                    "score": result.get("groundedness", 0),
                    "explanation": result.get("groundedness_reason", ""),
                    "threshold_recommendation": 3,
                    "passed": result.get("groundedness", 0) >= 3,
                }, indent=2),
            }],
        )
    except Exception as e:
        logger.error(f"Error in evaluate_groundedness: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Evaluation error: {str(e)}"}],
            isError=True,
        )


def handle_evaluate_relevance(arguments):
    err = _check_eval_available()
    if err:
        return err

    query = arguments.get("query")
    response = arguments.get("response")

    if not query or not response:
        return MCPToolResult(
            content=[{"type": "text", "text": "Both 'query' and 'response' are required"}],
            isError=True,
        )

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()
        evaluator = RelevanceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        result = evaluator(query=query, response=response)
        return MCPToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "evaluator": "RelevanceEvaluator",
                    "query": query[:100] + "..." if len(query) > 100 else query,
                    "score": result.get("relevance", 0),
                    "explanation": result.get("relevance_reason", ""),
                    "threshold_recommendation": 3,
                    "passed": result.get("relevance", 0) >= 3,
                }, indent=2),
            }],
        )
    except Exception as e:
        logger.error(f"Error in evaluate_relevance: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Evaluation error: {str(e)}"}],
            isError=True,
        )


def _safe_int_score(value, default=0):
    """Convert evaluator score to int, handling string scores."""
    if isinstance(value, str):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return default
    return value


def handle_run_agent_evaluation(arguments):
    err = _check_eval_available()
    if err:
        return err

    query = arguments.get("query")
    response = arguments.get("response")
    tool_calls = arguments.get("tool_calls", [])
    tool_definitions = arguments.get("tool_definitions")
    system_message = arguments.get("system_message", "")
    context = arguments.get("context", "")
    thresholds = arguments.get("thresholds", {
        "intent_resolution": 3,
        "tool_call_accuracy": 3,
        "task_adherence": 3,
        "groundedness": 3,
        "relevance": 3,
    })

    if not query or not response:
        return MCPToolResult(
            content=[{"type": "text", "text": "Both 'query' and 'response' are required"}],
            isError=True,
        )

    if not tool_definitions:
        tool_definitions = [
            {"name": "digital_quality_action", "description": "Analyzes a task and generates an action plan.", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}}
        ]

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()
        results = {
            "query": query[:200] + "..." if len(query) > 200 else query,
            "response_preview": response[:200] + "..." if len(response) > 200 else response,
            "evaluations": {},
            "all_passed": True,
        }

        # IntentResolution
        try:
            intent_eval = IntentResolutionEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
            intent_result = intent_eval(query=query, response=response)
            intent_score = intent_result.get("intent_resolution", 0)
            results["evaluations"]["intent_resolution"] = {
                "score": intent_score,
                "threshold": thresholds.get("intent_resolution", 3),
                "passed": intent_score >= thresholds.get("intent_resolution", 3),
                "explanation": intent_result.get("intent_resolution_reason", ""),
            }
            if not results["evaluations"]["intent_resolution"]["passed"]:
                results["all_passed"] = False
        except Exception as e:
            results["evaluations"]["intent_resolution"] = {"error": str(e), "passed": False}
            results["all_passed"] = False

        # ToolCallAccuracy
        if tool_calls:
            try:
                tool_eval = ToolCallAccuracyEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
                tool_result = tool_eval(query=query, tool_calls=tool_calls, tool_definitions=tool_definitions)
                tool_score = tool_result.get("tool_call_accuracy", 0)
                results["evaluations"]["tool_call_accuracy"] = {
                    "score": tool_score,
                    "threshold": thresholds.get("tool_call_accuracy", 3),
                    "passed": tool_score >= thresholds.get("tool_call_accuracy", 3),
                    "explanation": tool_result.get("tool_call_accuracy_reason", ""),
                }
                if not results["evaluations"]["tool_call_accuracy"]["passed"]:
                    results["all_passed"] = False
            except Exception as e:
                results["evaluations"]["tool_call_accuracy"] = {"error": str(e), "passed": False}
                results["all_passed"] = False
        else:
            results["evaluations"]["tool_call_accuracy"] = {"skipped": True, "reason": "No tool_calls provided"}

        # TaskAdherence
        try:
            task_eval = TaskAdherenceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
            eval_kwargs = {"query": query, "response": response}
            if tool_calls:
                eval_kwargs["tool_calls"] = tool_calls
            if system_message:
                eval_kwargs["system_message"] = system_message
            task_result = task_eval(**eval_kwargs)
            flagged = task_result.get("task_adherence", False)
            results["evaluations"]["task_adherence"] = {
                "flagged": flagged,
                "passed": not flagged,
                "reasoning": task_result.get("task_adherence_reason", ""),
            }
            if not results["evaluations"]["task_adherence"]["passed"]:
                results["all_passed"] = False
        except Exception as e:
            results["evaluations"]["task_adherence"] = {"error": str(e), "passed": False}
            results["all_passed"] = False

        # Groundedness
        if context:
            try:
                groundedness_eval = GroundednessEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
                ground_result = groundedness_eval(query=query, response=response, context=context)
                ground_score = _safe_int_score(ground_result.get("groundedness", 0))
                results["evaluations"]["groundedness"] = {
                    "score": ground_score,
                    "threshold": thresholds.get("groundedness", 3),
                    "passed": ground_score >= thresholds.get("groundedness", 3),
                    "explanation": ground_result.get("groundedness_reason", ""),
                }
                if not results["evaluations"]["groundedness"]["passed"]:
                    results["all_passed"] = False
            except Exception as e:
                results["evaluations"]["groundedness"] = {"error": str(e), "passed": False}
                results["all_passed"] = False
        else:
            results["evaluations"]["groundedness"] = {"skipped": True, "reason": "No context provided"}

        # Relevance
        try:
            relevance_eval = RelevanceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
            rel_result = relevance_eval(query=query, response=response)
            rel_score = _safe_int_score(rel_result.get("relevance", 0))
            results["evaluations"]["relevance"] = {
                "score": rel_score,
                "threshold": thresholds.get("relevance", 3),
                "passed": rel_score >= thresholds.get("relevance", 3),
                "explanation": rel_result.get("relevance_reason", ""),
            }
            if not results["evaluations"]["relevance"]["passed"]:
                results["all_passed"] = False
        except Exception as e:
            results["evaluations"]["relevance"] = {"error": str(e), "passed": False}
            results["all_passed"] = False

        return MCPToolResult(
            content=[{"type": "text", "text": json.dumps(results, indent=2)}],
        )
    except Exception as e:
        logger.error(f"Error in run_agent_evaluation: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Evaluation error: {str(e)}"}],
            isError=True,
        )


def handle_run_batch_evaluation(arguments):
    err = _check_eval_available()
    if err:
        return err

    evaluation_data = arguments.get("evaluation_data", [])
    thresholds = arguments.get("thresholds", {
        "intent_resolution": 3,
        "tool_call_accuracy": 3,
        "task_adherence": 3,
        "groundedness": 3,
        "relevance": 3,
    })

    if not evaluation_data:
        return MCPToolResult(
            content=[{"type": "text", "text": "'evaluation_data' array is required"}],
            isError=True,
        )

    try:
        model_config = _get_eval_model_config()
        credential = DefaultAzureCredential()

        intent_eval = IntentResolutionEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        tool_eval = ToolCallAccuracyEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        task_eval = TaskAdherenceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        groundedness_eval = GroundednessEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)
        relevance_eval = RelevanceEvaluator(model_config=model_config, credential=credential, is_reasoning_model=True)

        default_tool_defs = [
            {"name": "digital_quality_action", "description": "Analyzes a task and generates an action plan.", "parameters": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}}
        ]

        all_results = []
        intent_scores = []
        tool_scores = []
        task_passes = []
        ground_scores = []
        relevance_scores = []

        for idx, item in enumerate(evaluation_data):
            query = item.get("query", "")
            response = item.get("response", "")
            tool_calls = item.get("tool_calls", [])
            system_message = item.get("system_message", "")
            context = item.get("context", "")

            row_result = {
                "index": idx,
                "query_preview": query[:50] + "..." if len(query) > 50 else query,
            }

            # Intent Resolution
            try:
                intent_result = intent_eval(query=query, response=response)
                score = _safe_int_score(intent_result.get("intent_resolution", 0))
                intent_scores.append(score)
                row_result["intent_resolution"] = {
                    "score": score,
                    "passed": score >= thresholds.get("intent_resolution", 3),
                }
            except Exception as e:
                row_result["intent_resolution"] = {"error": str(e)}

            # Tool Call Accuracy
            if tool_calls:
                try:
                    tool_result = tool_eval(query=query, tool_calls=tool_calls, tool_definitions=default_tool_defs)
                    score = _safe_int_score(tool_result.get("tool_call_accuracy", 0))
                    tool_scores.append(score)
                    row_result["tool_call_accuracy"] = {
                        "score": score,
                        "passed": score >= thresholds.get("tool_call_accuracy", 3),
                    }
                except Exception as e:
                    row_result["tool_call_accuracy"] = {"error": str(e)}

            # Task Adherence
            try:
                eval_kwargs = {"query": query, "response": response}
                if tool_calls:
                    eval_kwargs["tool_calls"] = tool_calls
                if system_message:
                    eval_kwargs["system_message"] = system_message
                task_result = task_eval(**eval_kwargs)
                flagged = task_result.get("task_adherence", False)
                task_passes.append(not flagged)
                row_result["task_adherence"] = {"flagged": flagged, "passed": not flagged}
            except Exception as e:
                row_result["task_adherence"] = {"error": str(e)}

            # Groundedness
            if context:
                try:
                    ground_result = groundedness_eval(query=query, response=response, context=context)
                    score = _safe_int_score(ground_result.get("groundedness", 0))
                    ground_scores.append(score)
                    row_result["groundedness"] = {
                        "score": score,
                        "passed": score >= thresholds.get("groundedness", 3),
                    }
                except Exception as e:
                    row_result["groundedness"] = {"error": str(e)}

            # Relevance
            try:
                rel_result = relevance_eval(query=query, response=response)
                score = _safe_int_score(rel_result.get("relevance", 0))
                relevance_scores.append(score)
                row_result["relevance"] = {
                    "score": score,
                    "passed": score >= thresholds.get("relevance", 3),
                }
            except Exception as e:
                row_result["relevance"] = {"error": str(e)}

            all_results.append(row_result)

        # Aggregate metrics
        summary = {"total_evaluated": len(evaluation_data), "metrics": {}}

        if intent_scores:
            summary["metrics"]["intent_resolution"] = {
                "average_score": round(sum(intent_scores) / len(intent_scores), 2),
                "pass_rate": round(sum(1 for s in intent_scores if s >= thresholds.get("intent_resolution", 3)) / len(intent_scores) * 100, 1),
                "min": min(intent_scores),
                "max": max(intent_scores),
            }
        if tool_scores:
            summary["metrics"]["tool_call_accuracy"] = {
                "average_score": round(sum(tool_scores) / len(tool_scores), 2),
                "pass_rate": round(sum(1 for s in tool_scores if s >= thresholds.get("tool_call_accuracy", 3)) / len(tool_scores) * 100, 1),
                "min": min(tool_scores),
                "max": max(tool_scores),
            }
        if task_passes:
            summary["metrics"]["task_adherence"] = {
                "pass_rate": round(sum(task_passes) / len(task_passes) * 100, 1),
                "passed_count": sum(task_passes),
                "failed_count": len(task_passes) - sum(task_passes),
            }
        if ground_scores:
            summary["metrics"]["groundedness"] = {
                "average_score": round(sum(ground_scores) / len(ground_scores), 2),
                "pass_rate": round(sum(1 for s in ground_scores if s >= thresholds.get("groundedness", 3)) / len(ground_scores) * 100, 1),
                "min": min(ground_scores),
                "max": max(ground_scores),
            }
        if relevance_scores:
            summary["metrics"]["relevance"] = {
                "average_score": round(sum(relevance_scores) / len(relevance_scores), 2),
                "pass_rate": round(sum(1 for s in relevance_scores if s >= thresholds.get("relevance", 3)) / len(relevance_scores) * 100, 1),
                "min": min(relevance_scores),
                "max": max(relevance_scores),
            }

        return MCPToolResult(
            content=[{
                "type": "text",
                "text": json.dumps({
                    "summary": summary,
                    "thresholds": thresholds,
                    "per_row_results": all_results,
                }, indent=2),
            }],
        )
    except Exception as e:
        logger.error(f"Error in run_batch_evaluation: {e}")
        return MCPToolResult(
            content=[{"type": "text", "text": f"Batch evaluation error: {str(e)}"}],
            isError=True,
        )
