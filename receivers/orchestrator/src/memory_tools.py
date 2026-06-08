"""
Short-term and long-term memory management tools.

Includes @ai_function-decorated helpers and the long-term memory initialiser.
"""

import json
import uuid
import asyncio
import logging
from typing import Dict, Any, Optional, List

from agent_framework import ai_function

from config import (
    FOUNDRY_PROJECT_ENDPOINT,
    AZURE_SEARCH_ENDPOINT,
    AZURE_SEARCH_INDEX_NAME,
    AZURE_SEARCH_KNOWLEDGE_BASE_NAME,
    chat,
    composite_memory,
    long_term_memory,
    AISEARCH_CONTEXT_PROVIDER_AVAILABLE,
    logger as _config_logger,
)
from memory import MemoryEntry, MemoryType, LongTermMemory
from embedding import get_embedding

logger = logging.getLogger("digital_quality_orchestrator")


# ---------------------------------------------------------------------------
# Long-term memory initialisation
# ---------------------------------------------------------------------------

def initialize_long_term_memory() -> None:
    """Initialise AI Search long-term memory with AzureAISearchContextProvider."""
    import config  # module-level import for mutation of globals

    if AZURE_SEARCH_ENDPOINT and FOUNDRY_PROJECT_ENDPOINT:
        try:
            ltm = LongTermMemory(
                search_endpoint=AZURE_SEARCH_ENDPOINT,
                foundry_endpoint=FOUNDRY_PROJECT_ENDPOINT,
                index_name=AZURE_SEARCH_INDEX_NAME,
                knowledge_base_name=AZURE_SEARCH_KNOWLEDGE_BASE_NAME,
                mode="agentic",
            )
            ltm.set_embedding_function(get_embedding)

            config.long_term_memory = ltm

            if composite_memory:
                composite_memory._long_term = ltm

            if AISEARCH_CONTEXT_PROVIDER_AVAILABLE:
                logger.info(
                    f"LongTermMemory initialized with AzureAISearchContextProvider: {AZURE_SEARCH_INDEX_NAME}"
                )
            else:
                logger.warning(
                    f"LongTermMemory initialized WITHOUT AzureAISearchContextProvider: {AZURE_SEARCH_INDEX_NAME}"
                )
        except Exception as e:
            logger.error(f"Failed to initialize long-term memory: {e}")
    else:
        logger.warning(
            "AZURE_SEARCH_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT not configured - long-term memory disabled"
        )


# ---------------------------------------------------------------------------
# @ai_function decorated memory tools
# ---------------------------------------------------------------------------

@ai_function
def store_memory_tool(content: str, session_id: str, memory_type: str = "context") -> str:
    """Store information in short-term memory for later retrieval."""
    if not chat:
        return json.dumps({"error": "Memory provider not configured"})
    try:
        type_map = {
            "context": MemoryType.CONTEXT,
            "conversation": MemoryType.CONVERSATION,
            "task": MemoryType.TASK,
            "plan": MemoryType.PLAN,
        }
        mem_type = type_map.get(memory_type.lower(), MemoryType.CONTEXT)
        embedding = None
        if FOUNDRY_PROJECT_ENDPOINT:
            try:
                embedding = get_embedding(content)
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            content=content,
            memory_type=mem_type,
            embedding=embedding,
            session_id=session_id,
        )
        loop = asyncio.new_event_loop()
        entry_id = loop.run_until_complete(chat.store(entry))
        loop.close()
        return json.dumps({
            "success": True,
            "memory_id": entry_id,
            "session_id": session_id,
            "memory_type": memory_type,
            "has_embedding": embedding is not None,
        })
    except Exception as e:
        logger.error(f"Error storing memory: {e}")
        return json.dumps({"error": str(e)})


@ai_function
def recall_memory_tool(query: str, session_id: str, limit: int = 5) -> str:
    """Recall relevant memories from short-term memory based on semantic similarity."""
    if not chat:
        return json.dumps({"error": "Memory provider not configured"})
    if not FOUNDRY_PROJECT_ENDPOINT:
        return json.dumps({"error": "Foundry endpoint not configured for embeddings"})
    try:
        query_embedding = get_embedding(query)
        loop = asyncio.new_event_loop()
        results = loop.run_until_complete(
            chat.search(
                query_embedding=query_embedding,
                limit=limit,
                threshold=0.6,
                session_id=session_id,
            )
        )
        loop.close()
        memories = [
            {
                "id": r.entry.id,
                "content": r.entry.content,
                "memory_type": r.entry.memory_type.value,
                "similarity_score": round(r.score, 3),
                "created_at": r.entry.created_at,
            }
            for r in results
        ]
        return json.dumps({
            "query": query,
            "session_id": session_id,
            "memories_found": len(memories),
            "memories": memories,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error recalling memory: {e}")
        return json.dumps({"error": str(e)})


@ai_function
def get_session_history_tool(session_id: str, limit: int = 20) -> str:
    """Get conversation history for a session."""
    if not chat:
        return json.dumps({"error": "Memory provider not configured"})
    try:
        loop = asyncio.new_event_loop()
        history = loop.run_until_complete(
            chat.get_conversation_history(session_id, limit)
        )
        loop.close()
        return json.dumps({
            "session_id": session_id,
            "message_count": len(history),
            "messages": history,
        }, indent=2)
    except Exception as e:
        logger.error(f"Error getting session history: {e}")
        return json.dumps({"error": str(e)})


@ai_function
def clear_session_memory_tool(session_id: str) -> str:
    """Clear all short-term memory for a session."""
    if not chat:
        return json.dumps({"error": "Memory provider not configured"})
    try:
        loop = asyncio.new_event_loop()
        count = loop.run_until_complete(chat.clear_session(session_id))
        loop.close()
        return json.dumps({
            "success": True,
            "session_id": session_id,
            "entries_cleared": count,
        })
    except Exception as e:
        logger.error(f"Error clearing session memory: {e}")
        return json.dumps({"error": str(e)})
