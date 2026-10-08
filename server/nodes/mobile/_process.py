"""Bounded, shell-free optional runtime subprocesses."""

from __future__ import annotations
import asyncio
import codecs
import contextlib
import os
import re
import subprocess
from typing import Callable
from services.process_environment import without_onepassword_environment

OUTPUT_TAIL_BYTES = 64 * 1024


def hidden_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


async def command(
    argv: list[str],
    *,
    timeout: float = 30,
    env: dict | None = None,
    cwd: str | None = None,
    input_text: str | None = None,
    check: bool = True,
    progress: Callable[[str], None] | None = None,
) -> str:
    proc = await asyncio.create_subprocess_exec(
        *map(str, argv),
        stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=without_onepassword_environment(env),
        cwd=cwd,
        **hidden_options(),
    )
    tail = bytearray()

    async def stream() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""

        async def write_input() -> None:
            if input_text is not None:
                try:
                    proc.stdin.write(input_text.encode())
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

        async def read_output() -> None:
            nonlocal pending
            while data := await proc.stdout.read(4096):
                tail.extend(data)
                del tail[:-OUTPUT_TAIL_BYTES]
                if progress is not None:
                    pending += decoder.decode(data)
                    lines = re.split(r"[\r\n]", pending)
                    pending = lines.pop()
                    for line in lines:
                        if line.strip():
                            progress(line[-4096:])
                    # Some installers print long status fragments with no
                    # newline. Bound both memory and time-to-visible-output.
                    if len(pending) >= 4096:
                        progress(pending[-4096:])
                        pending = ""
            if progress is not None:
                pending += decoder.decode(b"", final=True)
                if pending.strip():
                    progress(pending[-4096:])

        await asyncio.gather(write_input(), read_output())
        await proc.wait()

    try:
        await asyncio.wait_for(stream(), timeout)
    except asyncio.TimeoutError:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        last_output = bytes(tail).decode("utf-8", "replace")[-1500:].strip()
        raise TimeoutError(
            f"{os.path.basename(str(argv[0]))} timed out after {timeout:g} seconds. "
            "Check your network connection and available disk space, then retry setup; completed packages are reused."
            + (f" Last output: {last_output}" if last_output else " No output was received.")
        ) from None
    except BaseException:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        raise
    text = bytes(tail).decode("utf-8", "replace")
    if check and proc.returncode:
        raise RuntimeError(f"{os.path.basename(str(argv[0]))} failed ({proc.returncode}): {text[-1500:]}")
    return text
