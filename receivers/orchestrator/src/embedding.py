"""
Embedding generation, cosine similarity, intent analysis, and plan generation.
"""

import json
import logging
from typing import Dict, Any, List

import numpy as np
from azure.identity import DefaultAzureCredential

from config import (
    FOUNDRY_PROJECT_ENDPOINT,
    EMBEDDING_MODEL_DEPLOYMENT_NAME,
    cosmos_tasks_container,
    get_model_deployment,
)

logger = logging.getLogger("digital_quality_orchestrator")


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def get_embedding(text: str) -> List[float]:
    """Generate embeddings using Azure AI Foundry's text-embedding-3-large model."""
    if not FOUNDRY_PROJECT_ENDPOINT:
        raise ValueError("Foundry endpoint not configured")

    from openai import AzureOpenAI

    credential = DefaultAzureCredential()
    token = credential.get_token("https://cognitiveservices.azure.com/.default")

    base_endpoint = (
        FOUNDRY_PROJECT_ENDPOINT.split("/api/projects")[0]
        if "/api/projects" in FOUNDRY_PROJECT_ENDPOINT
        else FOUNDRY_PROJECT_ENDPOINT
    )

    client = AzureOpenAI(
        azure_endpoint=base_endpoint,
        api_key=token.token,
        api_version="2024-02-15-preview",
    )

    response = client.embeddings.create(
        model=EMBEDDING_MODEL_DEPLOYMENT_NAME, input=text
    )
    return response.data[0].embedding


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------

def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    arr1 = np.array(vec1)
    arr2 = np.array(vec2)
    dot_product = np.dot(arr1, arr2)
    norm1 = np.linalg.norm(arr1)
    norm2 = np.linalg.norm(arr2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(dot_product / (norm1 * norm2))


def find_similar_tasks(
    task_embedding: List[float], threshold: float = 0.7, limit: int = 5
) -> List[Dict[str, Any]]:
    """Find similar tasks in CosmosDB using cosine similarity."""
    if not cosmos_tasks_container:
        return []
    try:
        query = "SELECT c.id, c.task, c.intent, c.embedding, c.created_at FROM c WHERE IS_DEFINED(c.embedding)"
        items = list(
            cosmos_tasks_container.query_items(
                query=query, enable_cross_partition_query=True
            )
        )
        similar_tasks = []
        for item in items:
            if "embedding" in item and item["embedding"]:
                similarity = cosine_similarity(task_embedding, item["embedding"])
                if similarity >= threshold:
                    similar_tasks.append(
                        {
                            "id": item["id"],
                            "task": item.get("task", ""),
                            "intent": item.get("intent", ""),
                            "similarity": similarity,
                            "created_at": item.get("created_at", ""),
                        }
                    )
        similar_tasks.sort(key=lambda x: x["similarity"], reverse=True)
        return similar_tasks[:limit]
    except Exception as e:
        logger.error(f"Error finding similar tasks: {e}")
        return []


# ---------------------------------------------------------------------------
# Intent analysis
# ---------------------------------------------------------------------------

def analyze_intent(task: str) -> str:
    """Use the LLM to categorize the intent of a task."""
    if not FOUNDRY_PROJECT_ENDPOINT:
        return "unknown"
    try:
        from openai import AzureOpenAI

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        base_endpoint = (
            FOUNDRY_PROJECT_ENDPOINT.split("/api/projects")[0]
            if "/api/projects" in FOUNDRY_PROJECT_ENDPOINT
            else FOUNDRY_PROJECT_ENDPOINT
        )
        client = AzureOpenAI(
            azure_endpoint=base_endpoint,
            api_key=token.token,
            api_version="2024-02-15-preview",
        )
        model_deployment = get_model_deployment()
        response = client.chat.completions.create(
            model=model_deployment,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a healthcare digital quality management task analyzer. "
                        "Analyze the given task and provide a brief categorization of its intent. "
                        "Return only a short phrase describing the primary intent."
                    ),
                },
                {"role": "user", "content": f"Analyze this task: {task}"},
            ],
        )
        if response.choices and len(response.choices) > 0:
            return response.choices[0].message.content.strip()
        return "unknown"
    except Exception as e:
        logger.error(f"Error analyzing intent: {e}")
        return "unknown"


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BASIC = """You are a Healthcare Digital Quality Orchestrator agent that executes quality improvement actions and delivers concrete results.

IMPORTANT: You must deliver CONCRETE RESULTS, not action plans. Execute the requested task and return specific data, confirmed actions, or quantified outcomes.

For each task, return a JSON object with:
- "status": "completed" or "in_progress"
- "results": object containing the actual data, scores, confirmations, or findings
- "actions_taken": array of actions that were executed (not planned)
- "recommendations": specific next steps with quantified expected impact

Return ONLY valid JSON, no markdown or explanation."""

_SYSTEM_PROMPT_INSTRUCTIONS = """You are a Healthcare Digital Quality Orchestrator agent that EXECUTES quality improvement tasks and delivers CONCRETE RESULTS with specific data.

You have access to:
1. Short-term memory (similar past tasks and their outcomes)
2. Long-term memory (detailed task instructions and best practices)

CRITICAL INSTRUCTION: Do NOT return action plans or step-by-step outlines. Instead, EXECUTE the task and return:
- Specific data values (member IDs, scores, dates, measure names)
- Confirmed action outcomes ("outreach queued", "alert created", "intervention logged")
- Quantified results (closure probabilities, risk scores, gap counts)
- Concrete recommendations with expected impact percentages

Use ALL provided context to deliver results grounded in the domain knowledge.
When task instructions are available, follow the proven approaches.

Return a JSON object with:
- "status": "completed"
- "results": object with specific findings and data
- "actions_taken": array of completed actions with confirmation details
- "recommendations": array of specific next steps with expected impact
- "source": "adapted" if based on instructions, "original" otherwise

Return ONLY valid JSON, no markdown or explanation."""


def _parse_llm_json(content: str):
    """Best-effort parse of LLM-returned JSON (may be wrapped in code blocks)."""
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return json.loads(content)


def generate_plan(task: str, similar_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Generate plan from task + similar past tasks."""
    if not FOUNDRY_PROJECT_ENDPOINT:
        return [{"step": 1, "action": "Manual planning required", "description": "Foundry not configured"}]
    try:
        from openai import AzureOpenAI

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        base_endpoint = (
            FOUNDRY_PROJECT_ENDPOINT.split("/api/projects")[0]
            if "/api/projects" in FOUNDRY_PROJECT_ENDPOINT
            else FOUNDRY_PROJECT_ENDPOINT
        )
        client = AzureOpenAI(
            azure_endpoint=base_endpoint,
            api_key=token.token,
            api_version="2024-02-15-preview",
        )
        context = ""
        if similar_tasks:
            context = "\n\nSimilar past tasks for reference:\n"
            for st in similar_tasks[:3]:
                context += f"- {st['task']} (intent: {st['intent']}, similarity: {st['similarity']:.2f})\n"
        model_deployment = get_model_deployment()
        response = client.chat.completions.create(
            model=model_deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_BASIC},
                {"role": "user", "content": f"Create a plan for this task: {task}{context}"},
            ],
        )
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content.strip()
            try:
                return _parse_llm_json(content)
            except json.JSONDecodeError:
                return [{"step": 1, "action": "Execute task", "description": content, "estimated_effort": "medium"}]
        return [{"step": 1, "action": "Execute task", "description": task, "estimated_effort": "medium"}]
    except Exception as e:
        logger.error(f"Error generating plan: {e}")
        return [{"step": 1, "action": "Error", "description": str(e), "estimated_effort": "unknown"}]


def generate_plan_with_instructions(
    task: str,
    similar_tasks: List[Dict[str, Any]],
    task_instructions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Generate plan using short-term memory (past tasks) + long-term memory (AI Search instructions)."""
    if not FOUNDRY_PROJECT_ENDPOINT:
        return [{"step": 1, "action": "Manual planning required", "description": "Foundry not configured"}]
    try:
        from openai import AzureOpenAI

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        base_endpoint = (
            FOUNDRY_PROJECT_ENDPOINT.split("/api/projects")[0]
            if "/api/projects" in FOUNDRY_PROJECT_ENDPOINT
            else FOUNDRY_PROJECT_ENDPOINT
        )
        client = AzureOpenAI(
            azure_endpoint=base_endpoint,
            api_key=token.token,
            api_version="2024-02-15-preview",
        )
        context = ""
        if similar_tasks:
            context = "\n\n## Similar Past Tasks (Short-Term Memory):\n"
            for st in similar_tasks[:3]:
                context += f"- {st['task']} (intent: {st['intent']}, similarity: {st['similarity']:.2f})\n"
        if task_instructions:
            context += "\n\n## Task Instructions (Long-Term Memory):\n"
            for ti in task_instructions[:2]:
                context += f"\n### {ti.get('title', 'Untitled')} (relevance: {ti.get('score', 0):.2f})\n"
                context += f"Category: {ti.get('category', 'N/A')}\n"
                context += f"Description: {ti.get('description', 'N/A')}\n"
                ref_steps = ti.get("steps", [])
                if ref_steps:
                    context += "Reference Steps:\n"
                    for step in ref_steps[:5]:
                        context += f"  {step.get('step', '?')}. {step.get('action', 'N/A')}: {step.get('description', 'N/A')[:100]}...\n"
                content_excerpt = ti.get("content_excerpt", "")
                if content_excerpt:
                    context += f"\nKey Information:\n{content_excerpt[:500]}...\n"
        model_deployment = get_model_deployment()
        response = client.chat.completions.create(
            model=model_deployment,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT_INSTRUCTIONS},
                {"role": "user", "content": f"Create a detailed plan for this task: {task}{context}"},
            ],
        )
        if response.choices and len(response.choices) > 0:
            content = response.choices[0].message.content.strip()
            try:
                return _parse_llm_json(content)
            except json.JSONDecodeError:
                return [{"step": 1, "action": "Execute task", "description": content, "estimated_effort": "medium", "source": "original"}]
        return [{"step": 1, "action": "Execute task", "description": task, "estimated_effort": "medium", "source": "original"}]
    except Exception as e:
        logger.error(f"Error generating plan with instructions: {e}")
        return generate_plan(task, similar_tasks)
