"""Shared deepagents-backend helper for filesystem plugins.

All four filesystem plugins (file_read / file_modify / shell /
fs_search) open a :class:`deepagents.backends.LocalShellBackend`
rooted at the per-workflow workspace. This helper centralises that
setup so each plugin file stays small.

Shell execution is dispatched through Nushell when available so the
agent sees identical command semantics on Windows, macOS, and Linux
(no cmd.exe vs sh divergence). Falls back to the upstream
``LocalShellBackend`` (which uses ``shell=True``) when ``nu`` isn't
installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import PureWindowsPath
from typing import Any, Dict, Optional

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ExecuteResponse
from deepagents.backends.utils import validate_path


def normalize_virtual_path(path: str) -> str:
    """Coerce any caller-supplied path to deepagents' canonical virtual form.

    LLMs (and humans) emit paths in every flavour: ``/foo`` (POSIX),
    ``C:\\foo`` (Windows drive), ``\\\\server\\share\\foo`` (UNC),
    ``foo\\bar`` / ``foo/bar`` (relative, mixed separators). deepagents'
    ``virtual_mode`` only resolves POSIX virtual paths, and its public
    :func:`validate_path` helper rejects Windows-anchored inputs.

    ``PureWindowsPath`` is a pure (host-OS independent) parser and is a
    superset of the POSIX grammar — Windows itself accepts ``/`` as a
    separator, so ``PureWindowsPath('/tmp/foo')`` correctly identifies
    ``/`` as the root anchor. That means a single parser covers Windows
    drives, UNC, and POSIX absolutes uniformly on any host OS. Strip
    the anchor here, then delegate to :func:`validate_path` for traversal
    rejection (``..``, ``~``) and canonical normalisation.
    """
    if not path:
        return path
    pw = PureWindowsPath(path)
    if pw.drive or pw.root:
        rel = "/" + "/".join(pw.parts[1:]) if len(pw.parts) > 1 else "/"
    else:
        rel = path.replace("\\", "/")
    return validate_path(rel)


def _find_nu() -> Optional[str]:
    """Resolve the nushell binary via the parent process's PATH.

    Cached on the module so we only pay the lookup cost once. Returns
    ``None`` if nu isn't installed; callers should fall back to the
    upstream shell.

    Resolution uses the live host PATH (not the sandbox's stripped
    ``self._env``) deliberately: nu must be reachable from somewhere,
    and pinning to an absolute path means the eventual subprocess can
    still launch even when env={} is passed for sandboxing.
    """
    if not hasattr(_find_nu, "_cached"):
        _find_nu._cached = shutil.which("nu")
    return _find_nu._cached


class NushellBackend(LocalShellBackend):
    """LocalShellBackend that routes ``execute()`` through Nushell.

    Why nu: cross-platform command parity. cmd.exe (Windows) and POSIX
    sh have wildly different builtin sets, quoting rules, and pipeline
    semantics, which forces LLM agents to special-case the host OS.
    Nushell parses the command itself with identical grammar on every
    platform, and many common file ops (``ls``, ``cp``, ``mv``,
    ``mkdir``, ``rm``, ``open``) are nu builtins, so they don't depend
    on external binaries being on PATH.

    Falls back to the parent's ``execute()`` when nu isn't installed.
    """

    def execute(self, command, *, timeout=None) -> ExecuteResponse:  # type: ignore[override]
        nu = _find_nu()
        if nu is None:
            return super().execute(command, timeout=timeout)

        if not command or not isinstance(command, str):
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        # Flags per nushell/src/command.rs (long forms):
        #   -n / --no-config-file   Skip user/global config (deterministic).
        #   --no-history            Don't read or write shell history.
        #   -c / --commands         Run the next argument as a single command.
        # The std library is intentionally left loaded — agents need
        # ``path``, ``str``, ``http``, etc. without per-command imports.
        argv = [nu, "-n", "--no-history", "-c", command]

        try:
            result = subprocess.run(
                argv,
                check=False,
                shell=False,  # nu parses argv[-1] itself; no host shell needed.
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                env=self._env,
                cwd=str(self.cwd),
            )

            # Same output-shaping convention as upstream LocalShellBackend.
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                output_parts.extend(f"[stderr] {line}" for line in stderr_lines)
            output = "\n".join(output_parts) if output_parts else "<no output>"

            truncated = False
            if len(output) > self._max_output_bytes:
                output = output[: self._max_output_bytes]
                output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
                truncated = True

            if result.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"

            return ExecuteResponse(
                output=output,
                exit_code=result.returncode,
                truncated=truncated,
            )

        except subprocess.TimeoutExpired:
            if timeout is not None:
                msg = f"Error: Command timed out after {effective_timeout} seconds (custom timeout). The command may be stuck or require more time."
            else:
                msg = f"Error: Command timed out after {effective_timeout} seconds. For long-running commands, re-run using the timeout parameter."
            return ExecuteResponse(output=msg, exit_code=124, truncated=False)
        except Exception as e:  # noqa: BLE001
            return ExecuteResponse(
                output=f"Error executing command via nu ({type(e).__name__}): {e}",
                exit_code=1,
                truncated=False,
            )


def get_backend(
    parameters: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
):
    """Return a sandboxed shell backend rooted at the per-workflow workspace.

    Resolution order: explicit ``working_directory`` param >
    ``context.workspace_dir`` > ``Settings().workspace_base_resolved/default``.
    The directory is created if it doesn't exist. ``virtual_mode=True``
    sandboxes paths inside the root.
    """
    from core.config import Settings
    from core.logging import get_logger

    param_dir = parameters.get("working_directory")
    ctx_dir = context.get("workspace_dir") if context else None
    root = param_dir or ctx_dir or os.path.join(Settings().workspace_base_resolved, "default")
    os.makedirs(root, exist_ok=True)
    get_logger(__name__).info("[Filesystem] root=%s", root)
    # ``inherit_env=True`` makes the host PATH available so the agent can
    # invoke npm, node, python, git, pwd (POSIX), where (Win), etc. as
    # external commands. ``virtual_mode`` only sandboxes filesystem ops
    # per deepagents docs — shell ``execute()`` was never path-restricted —
    # so inheriting env doesn't loosen any existing security boundary.
    return NushellBackend(root_dir=root, virtual_mode=True, inherit_env=True)
