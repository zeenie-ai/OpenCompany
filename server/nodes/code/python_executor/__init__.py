"""Python Executor — Wave 11.C migration."""

from __future__ import annotations

from typing import Any

from services.plugin import NodeContext, NodeUserError, Operation

from .._base import CodeExecutorBase, CodeExecutorParams


# Names available in the sandbox namespace -- kept here so the
# error-handler can list them when the LLM tries `import X` and hits
# the "no __import__" wall.
_SANDBOX_NAMES = "math, json, datetime, timedelta, re, random, Counter, defaultdict"


class PythonExecutorNode(CodeExecutorBase):
    type = "pythonExecutor"
    display_name = "Python Executor"
    subtitle = "Run Python"
    description = "Execute Python code for calculations, data processing, and automation"
    tool_name = "python_code"
    tool_description = "Execute Python code for calculations, data processing, and automation. Available: math, json, datetime, Counter, defaultdict. Set output variable with result."

    @Operation("execute")
    async def execute_op(self, ctx: NodeContext, params: CodeExecutorParams) -> Any:
        """Inlined from handlers/code.py (Wave 11.D.2).

        Executes user code in a restricted namespace with stdout capture.
        ``input_data`` exposes ``connected_outputs`` so upstream node
        results are reachable; ``workspace_dir`` is the per-workflow
        scratch directory.
        """
        import datetime as datetime_module
        import io
        import json as json_module
        import math
        import random as random_module
        import re as re_module
        import traceback as tb_module
        from collections import Counter, defaultdict

        if not params.code.strip():
            raise NodeUserError("No code provided")

        input_data = ctx.raw.get("connected_outputs") or {}
        stdout_capture = io.StringIO()

        def captured_print(*args, **kwargs):
            kwargs["file"] = stdout_capture
            print(*args, **kwargs)

        safe_builtins = {
            "abs": abs,
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "enumerate": enumerate,
            "filter": filter,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "map": map,
            "max": max,
            "min": min,
            "print": captured_print,
            "range": range,
            "round": round,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
            "tuple": tuple,
            "type": type,
            "zip": zip,
            "True": True,
            "False": False,
            "None": None,
            # Pre-injected modules — match the documented sandbox contract
            # (skill docs, CLAUDE.md). No __import__: callers reference these
            # by name, e.g. ``datetime.datetime.now()`` not ``import datetime``.
            "math": math,
            "json": json_module,
            "datetime": datetime_module,
            "timedelta": datetime_module.timedelta,
            "re": re_module,
            "random": random_module,
            "Counter": Counter,
            "defaultdict": defaultdict,
        }
        namespace = {
            "__builtins__": safe_builtins,
            "input_data": input_data,
            "workspace_dir": ctx.workspace_dir or "",
            "output": None,
        }
        try:
            exec(params.code, namespace)  # noqa: S102 — sandboxed namespace
        except Exception as exc:
            # Surface user-code errors as NodeUserError so the framework
            # logs a single WARN line (no traceback noise from the
            # framework) and the LLM gets an actionable message instead
            # of a raw Python exception.
            err_type = type(exc).__name__
            err_msg = str(exc)

            # Special case: `import X` translates to a `__import__` call
            # against the sandboxed builtins, which raises ImportError.
            # Tell the LLM exactly how to rewrite the code.
            if isinstance(exc, ImportError) and "__import__" in err_msg:
                raise NodeUserError(
                    "Python sandbox does not allow `import` statements. "
                    f"These names are pre-injected and ready to use: {_SANDBOX_NAMES}. "
                    "Reference them directly -- e.g. `math.sqrt(4)`, "
                    "`json.dumps(x)`, `datetime.datetime.now()`. "
                    "If you need a module not on the list, drop the "
                    "`import` and use process_manager to run a Python "
                    "script with full PATH access instead."
                ) from exc

            # Walk the traceback for the line number inside the user's
            # source (exec uses '<string>' as the filename).
            line_info = ""
            tb = exc.__traceback__
            while tb:
                if tb.tb_frame.f_code.co_filename == "<string>":
                    line_info = f" at line {tb.tb_lineno}"
                    break
                tb = tb.tb_next

            captured = stdout_capture.getvalue()
            suffix = f"\n\nstdout before error:\n{captured}" if captured else ""
            raise NodeUserError(f"{err_type}{line_info}: {err_msg}{suffix}") from exc

        return {
            "output": namespace.get("output"),
            "console_output": stdout_capture.getvalue(),
        }
