# The Monty step probe

A small experiment behind section 4.3 of [CROSS-REFERENCE.md](CROSS-REFERENCE.md). It asks one question: on the Monty version this repository pins, can a host drive a model-style orchestration script in steps, by re-running it against recorded results?

Run on 2026-11-12 against `pydantic-monty` 0.0.18, the version installed in the server virtualenv. The script imports only `pydantic_monty`. It imports no server code, opens no database and makes no network call.

## What it shows, and what it does not

It shows the mechanism:

- A script that fans out to two sub-agent calls and then chains a third runs in three steps. Each step starts the script from the top, answers every call that already has a recorded result, and stops when the script blocks on calls that have none.
- A re-run with the same recorded results reaches the same final value.
- The static type check refuses a misspelled function, a positional argument and a wrong-typed argument before any code runs.
- The time limit stops a loop that never ends.
- The `time` and `random` modules do not exist on this version.

It does not show an implementation. There are no child workflows, no Temporal, no limit on the size of an answer, and no error paths beyond the ones printed below.

One result needs care. `datetime.datetime.now()` did not fail. It reached the host as a call, the probe answered it like a sub-agent call, and the script finished with a coroutine object as its value. A real step function must refuse clock and operating-system calls.

One branch never ran. With type-check stubs supplied, a call to a catalog function arrived as a `FunctionSnapshot` directly, so the `NameLookupSnapshot` branch in `step` was not exercised.

## How to run it

Save the script below as a file outside the repository, then run it with the server virtualenv's Python in isolated mode, for example `server/.venv/Scripts/python.exe -I monty_step_probe.py` with the full path to that interpreter. Isolated mode keeps the working directory off the import path.

## The script

```python
"""Feasibility probe: drive a model-style orchestration script on the pinned
pydantic-monty by re-running it against recorded results.

Imports ONLY pydantic_monty. No server code, no database, no network.

Each "step" starts the script from the top, answers every sub-agent call that
already has a recorded result, and stops when the script blocks on calls that
have none. That is the shape a Temporal activity would have: input = code +
recorded results, output = pending calls or the final value.
"""

from __future__ import annotations

import json
import time

import pydantic_monty as pm

CODE = '''
import asyncio

reports = await asyncio.gather(
    reviewer(task="Review auth.py"),
    reviewer(task="Review parser.py"),
)
summary = await summarizer(task="Summarize: " + " | ".join(reports))
{"summary": summary, "reviews": len(reports)}
'''

STUBS = '''
async def reviewer(*, task: str) -> str:
    raise NotImplementedError()

async def summarizer(*, task: str) -> str:
    raise NotImplementedError()
'''

CATALOG = {"reviewer", "summarizer"}
LIMITS = {"max_duration_secs": 2.0, "max_memory": 64 * 1024 * 1024}


def _external(name):
    def fn(*args, **kwargs):  # never called by us; Monty suspends instead
        raise RuntimeError("host function body must not run")
    fn.__name__ = name
    return fn


def step(code: str, recorded: dict[int, str], type_check: bool = True) -> dict:
    kwargs = {"type_check": True, "type_check_stubs": STUBS} if type_check else {}
    m = pm.Monty(code, **kwargs)
    out = pm.CollectString()
    p = m.start(limits=LIMITS, print_callback=out)
    seen: dict[int, dict] = {}
    kinds: list[str] = []
    while True:
        kinds.append(type(p).__name__)
        if isinstance(p, pm.MontyComplete):
            return {"status": "done", "output": p.output, "kinds": kinds, "calls": len(seen)}
        if isinstance(p, pm.NameLookupSnapshot):
            name = p.variable_name
            p = p.resume(value=_external(name)) if name in CATALOG else p.resume()
        elif isinstance(p, pm.FunctionSnapshot):
            seen[p.call_id] = {"name": str(p.function_name), "args": list(p.args), "kwargs": dict(p.kwargs)}
            p = p.resume({"future": ...})
        elif isinstance(p, pm.FutureSnapshot):
            ids = list(p.pending_call_ids)
            if all(i in recorded for i in ids):
                p = p.resume({i: {"return_value": recorded[i]} for i in ids})
            else:
                pending = {i: c for i, c in seen.items() if i not in recorded}
                return {"status": "pending", "pending": pending, "kinds": kinds}
        else:
            raise RuntimeError(f"unexpected snapshot {type(p).__name__}")


def main() -> None:
    print("pydantic_monty", pm.__version__)
    recorded: dict[int, str] = {}
    for n in range(1, 6):
        started = time.perf_counter()
        result = step(CODE, recorded)
        ms = (time.perf_counter() - started) * 1000
        print(f"step {n} ({ms:.1f} ms):", json.dumps(result, default=str))
        if result["status"] == "done":
            break
        for call_id, call in result["pending"].items():
            recorded[call_id] = f"<answer of {call['name']} to {call['kwargs'].get('task')!r}>"

    # Same recorded results, run again: must reach the same final value (determinism).
    again = step(CODE, recorded)
    print("re-run equal:", again["status"] == "done" and again["output"] == result["output"])

    # Static check: a misspelled function and a positional task must be refused before running.
    for label, bad in {
        "misspelled": 'await reviwer(task="x")',
        "positional": 'await reviewer("x")',
        "wrong type": "await reviewer(task=123)",
    }.items():
        try:
            step(bad, {})
            print(f"type check [{label}]: NOT refused")
        except pm.MontyTypingError as error:
            print(f"type check [{label}]: refused ->", str(error).strip().splitlines()[0][:160])
        except pm.MontyError as error:
            print(f"type check [{label}]: other error {type(error).__name__}: {str(error)[:160]}")

    # Runaway loop: the limit must stop it.
    started = time.perf_counter()
    try:
        step("while True:\n    pass\n", {}, type_check=False)
        print("runaway: NOT stopped")
    except pm.MontyError as error:
        ms = (time.perf_counter() - started) * 1000
        print(f"runaway: stopped after {ms:.0f} ms ->", f"{type(error).__name__}: {str(error)[:120]}")

    # Clock and randomness must not be reachable.
    for label, src in {"clock": "import time\ntime.time()", "random": "import random\nrandom.random()",
                       "datetime.now": "import datetime\ndatetime.datetime.now()"}.items():
        try:
            r = step(src, {}, type_check=False)
            print(f"{label}: returned {r}")
        except pm.MontyError as error:
            print(f"{label}: refused -> {type(error).__name__}: {str(error)[:120]}")
        except RuntimeError as error:
            print(f"{label}: host saw {str(error)[:120]}")


if __name__ == "__main__":
    main()
```

## The output

```text
pydantic_monty 0.0.18
step 1 (18.6 ms): {"status": "pending", "pending": {"0": {"name": "reviewer", "args": [], "kwargs": {"task": "Review auth.py"}}, "1": {"name": "reviewer", "args": [], "kwargs": {"task": "Review parser.py"}}}, "kinds": ["FunctionSnapshot", "FunctionSnapshot", "FutureSnapshot"]}
step 2 (0.3 ms): {"status": "pending", "pending": {"2": {"name": "summarizer", "args": [], "kwargs": {"task": "Summarize: <answer of reviewer to 'Review auth.py'> | <answer of reviewer to 'Review parser.py'>"}}}, "kinds": ["FunctionSnapshot", "FunctionSnapshot", "FutureSnapshot", "FunctionSnapshot", "FutureSnapshot"]}
step 3 (0.1 ms): {"status": "done", "output": {"summary": "<answer of summarizer to \"Summarize: <answer of reviewer to 'Review auth.py'> | <answer of reviewer to 'Review parser.py'>\">", "reviews": 2}, "kinds": ["FunctionSnapshot", "FunctionSnapshot", "FutureSnapshot", "FunctionSnapshot", "FutureSnapshot", "MontyComplete"], "calls": 3}
re-run equal: True
type check [misspelled]: refused -> error[unresolved-reference]: Name `reviwer` used when not defined
type check [positional]: refused -> error[missing-argument]: No argument provided for required parameter `task` of function `reviewer`
type check [wrong type]: refused -> error[invalid-argument-type]: Argument to function `reviewer` is incorrect
runaway: stopped after 2000 ms -> MontyRuntimeError: TimeoutError: time limit exceeded: 2.0000001s > 2s
clock: refused -> MontyRuntimeError: ModuleNotFoundError: No module named 'time'
random: refused -> MontyRuntimeError: ModuleNotFoundError: No module named 'random'
datetime.now: returned {'status': 'done', 'output': '<coroutine external_future(0)>', 'kinds': ['FunctionSnapshot', 'MontyComplete'], 'calls': 1}
```

The first step took longer because it includes the first type check; an earlier run of the same script measured 23.2 ms for it.
