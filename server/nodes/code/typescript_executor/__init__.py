"""TypeScript Executor — Wave 11.C migration."""

from __future__ import annotations

from typing import Any

from services.plugin import NodeContext, NodeUserError, Operation

from .._base import CodeExecutorBase, CodeExecutorParams


class TypeScriptExecutorNode(CodeExecutorBase):
    type = "typescriptExecutor"
    display_name = "TypeScript Executor"
    subtitle = "Run TS"
    description = "Execute TypeScript code via persistent Node.js server with type safety"
    tool_name = "typescript_code"
    tool_description = "Execute TypeScript code via persistent Node.js server with type safety. Set output variable with result."

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: CodeExecutorParams) -> Any:
        """Inlined from handlers/code.py (Wave 11.D.2)."""
        from aiohttp import ClientConnectorError

        from .._nodejs import get_nodejs_client

        if not params.code.strip():
            raise NodeUserError("No code provided")
        input_data = dict(ctx.raw.get("connected_outputs") or {})
        input_data["workspace_dir"] = ctx.workspace_dir or ""

        try:
            result = await get_nodejs_client().execute(
                code=params.code,
                input_data=input_data,
                timeout=params.timeout * 1000,
                language="typescript",
            )
        except ClientConnectorError as exc:
            raise NodeUserError(
                "TypeScript executor is not running (cannot reach the "
                "Node.js sidecar on localhost:3020). Start the dev runner "
                "(it spawns the Node executor automatically), or fall back "
                "to python_executor for similar logic. Underlying: "
                f"{exc}"
            ) from exc

        if not result.get("success"):
            raise NodeUserError(result.get("error") or "TypeScript executor failed")
        return {
            "output": result.get("output"),
            "console_output": result.get("console_output", ""),
        }
