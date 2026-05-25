"""
Chat WebSocket Client - JSON-RPC 2.0 Protocol

Simple client for chat backend. Uses aiohttp (already in project).
Connection URL: ws://{host}:{port}/ws/{sessionId}?api_key={apiKey}&client_type=web
"""

import asyncio
import json
import uuid
import aiohttp
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()


async def send_chat_message(
    host: str, port: int, session_id: str, api_key: str, content: str, metadata: Optional[Dict[str, Any]] = None, timeout: float = 10.0
) -> Dict[str, Any]:
    """Send a chat message via JSON-RPC 2.0 WebSocket.

    Args:
        host: Chat server host
        port: Chat server port
        session_id: Session identifier
        api_key: API key for authentication
        content: Message content
        metadata: Optional message metadata
        timeout: Request timeout in seconds

    Returns:
        JSON-RPC result or error dict
    """
    url = f"ws://{host}:{port}/ws/{session_id}?api_key={api_key}&client_type=web"
    request_id = str(uuid.uuid4())

    request = {"jsonrpc": "2.0", "method": "chat.sendMessage", "params": {"content": content, **(metadata or {})}, "id": request_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, timeout=timeout) as ws:
                await ws.send_json(request)

                # Wait for response with matching id
                async with asyncio.timeout(timeout):
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("id") == request_id:
                                if "error" in data:
                                    return {"success": False, "error": data["error"].get("message", "Unknown error")}
                                return {"success": True, "result": data.get("result", {})}
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        return {"success": False, "error": "No response received"}

    except asyncio.TimeoutError:
        return {"success": False, "error": "Connection timeout"}
    except Exception as e:
        logger.error("[Chat] Send error", error=str(e))
        return {"success": False, "error": str(e)}


async def get_chat_history(host: str, port: int, session_id: str, api_key: str, limit: int = 50, timeout: float = 10.0) -> Dict[str, Any]:
    """Get chat history via JSON-RPC 2.0 WebSocket.

    Args:
        host: Chat server host
        port: Chat server port
        session_id: Session identifier
        api_key: API key for authentication
        limit: Max messages to return
        timeout: Request timeout in seconds

    Returns:
        JSON-RPC result with messages or error dict
    """
    url = f"ws://{host}:{port}/ws/{session_id}?api_key={api_key}&client_type=web"
    request_id = str(uuid.uuid4())

    request = {"jsonrpc": "2.0", "method": "chat.getHistory", "params": {"limit": limit}, "id": request_id}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, timeout=timeout) as ws:
                await ws.send_json(request)

                async with asyncio.timeout(timeout):
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("id") == request_id:
                                if "error" in data:
                                    return {"success": False, "error": data["error"].get("message", "Unknown error")}
                                return {"success": True, "messages": data.get("result", {}).get("messages", [])}
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

        return {"success": False, "error": "No response received"}

    except asyncio.TimeoutError:
        return {"success": False, "error": "Connection timeout"}
    except Exception as e:
        logger.error("[Chat] History error", error=str(e))
        return {"success": False, "error": str(e)}


async def ping_chat_server(host: str, port: int, session_id: str, api_key: str, timeout: float = 5.0) -> bool:
    """Ping chat server to check connectivity."""
    url = f"ws://{host}:{port}/ws/{session_id}?api_key={api_key}&client_type=web"
    request_id = str(uuid.uuid4())

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, timeout=timeout) as ws:
                await ws.send_json({"jsonrpc": "2.0", "method": "ping", "params": {}, "id": request_id})

                async with asyncio.timeout(timeout):
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("id") == request_id:
                                return "error" not in data
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
        return False

    except Exception:
        return False
