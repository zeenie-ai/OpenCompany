"""Text generation service."""

import time
from datetime import datetime
from typing import Dict, Any

from core.logging import get_logger, log_execution_time

logger = get_logger(__name__)


class TextService:
    """Text generation and processing service."""

    def __init__(self):
        pass

    async def execute_text_generator(self, node_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute text generator node (migrated from Node.js)."""
        start_time = time.time()

        try:
            text = parameters.get("text", "Hello World")
            include_timestamp = parameters.get("include_timestamp", True)

            result_data = {"text": text, "length": len(text), "nodeId": node_id}

            if include_timestamp:
                result_data["timestamp"] = datetime.now().isoformat()

            result = {"type": "text", "data": result_data, "nodeId": node_id, "timestamp": datetime.now().isoformat()}

            log_execution_time(logger, "text_generator", start_time, time.time())

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "textGenerator",
                "result": result,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error("Text generator execution failed", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "textGenerator",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

    async def execute_file_handler(self, node_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute file handler node (migrated from Node.js)."""
        start_time = time.time()

        try:
            file_type = parameters.get("file_type", "generic")
            file_content = parameters.get("content", "")
            file_name = parameters.get("file_name", "untitled.txt")

            # Basic file processing
            result_data = {
                "fileName": file_name,
                "fileType": file_type,
                "content": file_content,
                "size": len(file_content),
                "processed": True,
                "processingType": file_type,
                "nodeId": node_id,
            }

            result = {"type": "file", "data": result_data, "nodeId": node_id, "timestamp": datetime.now().isoformat()}

            log_execution_time(logger, "file_handler", start_time, time.time())

            return {
                "success": True,
                "node_id": node_id,
                "node_type": "fileHandler",
                "result": result,
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error("File handler execution failed", node_id=node_id, error=str(e))
            return {
                "success": False,
                "node_id": node_id,
                "node_type": "fileHandler",
                "error": str(e),
                "execution_time": time.time() - start_time,
                "timestamp": datetime.now().isoformat(),
            }
