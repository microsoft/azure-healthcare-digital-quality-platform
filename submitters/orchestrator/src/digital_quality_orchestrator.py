"""
AKS Digital Quality Orchestrator Agent
FastAPI MCP Server
Implements Model Context Protocol (MCP) with SSE support
Enhanced with Microsoft Agent Framework for AI agent capabilities
Integrated with CosmosDB for task and plan storage with semantic reasoning
Features Memory Provider abstraction for short-term (CosmosDB) and long-term (AI Search) memory
Uses azure-agents-learning-sdk for native, judge-driven reinforcement learning over agent behaviour

This slim orchestrator imports from the extracted modules:
    config, schemas, embedding, memory_tools, learning_tools,
    evaluation_tools, tool_registry, tool_executor
"""

import json
import logging
import asyncio
import uuid
from dataclasses import asdict
from datetime import datetime

from fastapi import Request
from fastapi.responses import StreamingResponse, JSONResponse
from azure.identity import DefaultAzureCredential

# Shared config — app, sessions, logger, etc.
from config import (
    app,
    logger,
    sessions,
    FOUNDRY_PROJECT_ENDPOINT,
    chat,
    composite_memory,
)

# Embedding helpers (needed by startup)
from embedding import get_embedding

# Tool registry + executor
from tool_registry import tools
from tool_executor import execute_tool
from schemas import MCPToolResult

# Quality measure imports (for startup catalog loading + REST registration)
from digital_quality_measures import (
    get_measure_catalog,
    register_quality_tools,
    _load_measure_catalog,
)

# Cohort surveillance chat (importing this module wires `/chat/*` onto `app`
# via the @app.post decorators inside it). Imported after the catalog so the
# chat endpoint can resolve measures at first use.
import chat_orchestrator  # noqa: F401  (import for side-effects)

# Microsoft Agent Framework
from agent_framework import ai_function, AIFunction
from agent_framework.azure import AzureAIAgentClient


# =========================================
# AI Agent Factory
# =========================================

def create_mcp_agent():
    """Create and configure the MCP AI Agent with Microsoft Agent Framework."""
    if not FOUNDRY_PROJECT_ENDPOINT:
        logger.warning("FOUNDRY_PROJECT_ENDPOINT not configured - AI Agent will not be available")
        return None
    try:
        agent_credential = DefaultAzureCredential()
        client = AzureAIAgentClient(
            endpoint=FOUNDRY_PROJECT_ENDPOINT,
            credential=agent_credential,
        )
        logger.info("MCP AI Agent Client created successfully")
        return client
    except Exception as e:
        logger.error(f"Error creating AI Agent: {e}")
        return None


# Will be set on startup
mcp_ai_agent = None


# =========================================
# FastAPI Endpoints
# =========================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/runtime/webhooks/mcp/sse")
async def mcp_sse_endpoint(request: Request):
    """
    SSE endpoint for MCP protocol
    Establishes a long-lived connection for server-sent events
    """
    session_id = str(uuid.uuid4())
    logger.info(f"New SSE session established: {session_id}")

    sessions[session_id] = {
        "created_at": datetime.utcnow().isoformat(),
        "message_queue": asyncio.Queue(),
    }

    async def event_generator():
        try:
            message_url = f"message?sessionId={session_id}"
            yield f"data: {message_url}\n\n"
            while True:
                if session_id not in sessions:
                    break
                try:
                    message = await asyncio.wait_for(
                        sessions[session_id]["message_queue"].get(),
                        timeout=30.0,
                    )
                    yield f"data: {json.dumps(message)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            logger.info(f"SSE connection cancelled for session {session_id}")
        finally:
            if session_id in sessions:
                del sessions[session_id]
            logger.info(f"SSE session closed: {session_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/runtime/webhooks/mcp/message")
async def mcp_message_endpoint(request: Request):
    """
    Message endpoint for MCP protocol
    Handles JSON-RPC 2.0 requests
    """
    try:
        body = await request.json()
        logger.info(f"Received MCP message: {json.dumps(body)[:200]}")

        jsonrpc_version = body.get("jsonrpc")
        method = body.get("method")
        params = body.get("params", {})
        request_id = body.get("id")

        if jsonrpc_version != "2.0":
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "error": {"code": -32600, "message": "Invalid Request"},
                    "id": request_id,
                },
            )

        if method == "initialize":
            return JSONResponse(content={
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "dq", "version": "1.0.0"},
                },
                "id": request_id,
            })

        elif method == "tools/list":
            tools_list = [
                {"name": tool.name, "description": tool.description, "inputSchema": tool.inputSchema}
                for tool in tools
            ]
            return JSONResponse(content={
                "jsonrpc": "2.0",
                "result": {"tools": tools_list},
                "id": request_id,
            })

        elif method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            result = await execute_tool(tool_name, arguments)
            return JSONResponse(content={
                "jsonrpc": "2.0",
                "result": asdict(result),
                "id": request_id,
            })

        else:
            return JSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": request_id,
                },
            )

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": f"Internal error: {str(e)}"},
                "id": body.get("id") if 'body' in locals() else None,
            },
        )


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "MCP Server",
        "version": "1.0.0",
        "endpoints": {
            "sse": "/runtime/webhooks/mcp/sse",
            "message": "/runtime/webhooks/mcp/message",
            "health": "/health",
            "agent_chat": "/agent/chat",
        },
        "agent_enabled": mcp_ai_agent is not None,
    }


@app.on_event("startup")
async def startup_event():
    """Initialize the AI agent, memory providers, and quality measure catalog on startup."""
    global mcp_ai_agent

    # Initialize AI Agent
    mcp_ai_agent = create_mcp_agent()

    # Load quality measure catalog
    try:
        _load_measure_catalog()
        catalog = get_measure_catalog()
        logger.info(f"Quality measure catalog loaded: {len(catalog)} measures")
    except Exception as e:
        logger.warning(f"Failed to load quality measure catalog: {e}")

    # Register quality measure REST endpoints
    register_quality_tools(app)
    logger.info("Quality measure REST endpoints registered")

    if mcp_ai_agent:
        logger.info("AI Agent initialized successfully on startup")
    else:
        logger.warning("AI Agent not initialized - check FOUNDRY_PROJECT_ENDPOINT configuration")

    # Configure embedding function for memory provider
    if chat and FOUNDRY_PROJECT_ENDPOINT:
        chat.set_embedding_function(get_embedding)
        logger.info("Memory provider embedding function configured")

    # Log memory provider status
    if composite_memory:
        health = await composite_memory.health_check()
        for provider, is_healthy in health.items():
            status = "healthy" if is_healthy else "unhealthy"
            logger.info(f"Memory provider '{provider}': {status}")


@app.post("/agent/chat")
async def agent_chat(request: Request):
    """
    Chat endpoint for Microsoft Agent Framework.
    Processes user messages using the AI agent with tool capabilities.
    """
    if mcp_ai_agent is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "AI Agent not available",
                "message": "Configure FOUNDRY_PROJECT_ENDPOINT and install agent-framework packages to enable AI Agent",
            },
        )

    try:
        body = await request.json()
        user_message = body.get("message", "")
        conversation_history = body.get("history", [])

        if not user_message:
            return JSONResponse(status_code=400, content={"error": "No message provided"})

        messages = []
        for hist_msg in conversation_history:
            messages.append({
                "role": hist_msg.get("role", "user"),
                "content": hist_msg.get("content", ""),
            })
        messages.append({"role": "user", "content": user_message})

        response = await mcp_ai_agent.run(messages)

        assistant_responses = []
        if hasattr(response, 'messages'):
            for msg in response.messages:
                if hasattr(msg, 'role') and str(msg.role).lower() == 'assistant':
                    if hasattr(msg, 'contents'):
                        for content in msg.contents:
                            if hasattr(content, 'text'):
                                assistant_responses.append(content.text)
                    elif hasattr(msg, 'content'):
                        assistant_responses.append(str(msg.content))

        return JSONResponse(content={
            "response": "\n".join(assistant_responses) if assistant_responses else "No response generated",
            "message_id": str(uuid.uuid4()),
        })

    except Exception as e:
        logger.error(f"Error in agent chat: {e}")
        return JSONResponse(status_code=500, content={"error": f"Agent error: {str(e)}"})


@app.post("/agent/chat/stream")
async def agent_chat_stream(request: Request):
    """
    Streaming chat endpoint for Microsoft Agent Framework.
    Returns responses as Server-Sent Events for real-time streaming.
    """
    if mcp_ai_agent is None:
        return JSONResponse(
            status_code=503,
            content={
                "error": "AI Agent not available",
                "message": "Configure FOUNDRY_PROJECT_ENDPOINT and install agent-framework packages to enable AI Agent",
            },
        )

    try:
        body = await request.json()
        user_message = body.get("message", "")

        if not user_message:
            return JSONResponse(status_code=400, content={"error": "No message provided"})

        messages = [{"role": "user", "content": user_message}]

        async def generate_stream():
            try:
                async for event in mcp_ai_agent.run_stream(messages):
                    if hasattr(event, 'data') and hasattr(event.data, 'contents'):
                        for content in event.data.contents:
                            if hasattr(content, 'text'):
                                yield f"data: {json.dumps({'text': content.text})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            except Exception as e:
                logger.error(f"Streaming error: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    except Exception as e:
        logger.error(f"Error in agent chat stream: {e}")
        return JSONResponse(status_code=500, content={"error": f"Agent error: {str(e)}"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
