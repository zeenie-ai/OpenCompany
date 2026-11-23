"""Answering typed questions with the first provider that can: ``judge``.

The providers are tried in the order ``config/judgment_defaults.json`` gives,
and the first one is used that

- is registered (its plugin registers it when it loads),
- answers every question type asked,
- is calibrated, when the caller asks for ``calibrated_only``,
- takes images, when there are some, and
- has a key. A ``keyless`` provider raises :class:`NotConnected` itself when
  it has nothing to call, and the next provider is tried.

That provider gets one attempt, under its own time limit or the caller's. A
failure is final: no other provider is tried and nothing is repeated, because
every caller has a safe way on without an answer (admit the event, wait for
the reviewer, report the element as not found).

``judge`` never raises for a provider's failure, and lets cancellation
through. It raises ``ValueError`` only for questions that cannot be asked,
which is the caller's mistake and is found before anything is sent.

Neither the input nor the questions are logged.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Iterable, Mapping, Optional, Set

from core.logging import get_logger
from services.plugin import NodeUserError

from . import config
from .protocol import (
    InvalidReply,
    Judgment,
    JudgmentImage,
    JudgmentRequest,
    NotConnected,
    Reason,
    check_answers,
    check_questions,
)
from .registry import JudgmentProviderSpec, get_judgment_provider, has_judgment_provider

logger = get_logger(__name__)


async def judge(
    state: Any,
    questions: Mapping[str, Any],
    *,
    node_id: str,
    workflow_id: Optional[str] = None,
    session_id: str = "default",
    principal: Optional[str] = None,
    images: Iterable[JudgmentImage] = (),
    calibrated_only: bool = False,
    timeout: Optional[float] = None,
) -> Judgment:
    """Answer ``questions`` about ``state``.

    ``node_id``, ``workflow_id`` and ``session_id`` say where the call's usage
    row goes. ``principal`` is the owner whose keys and model are used.
    ``timeout`` replaces the provider's own time limit, for a caller that has
    to finish sooner.
    """
    asked = check_questions(questions)
    pictures = tuple(images)
    if state is None:
        raise ValueError("a judgment needs something to judge")
    if any(question.type == "box" for question in asked.values()) and not pictures:
        raise ValueError("a box question needs an image")
    kinds = {question.type for question in asked.values()}
    request = JudgmentRequest(
        state=state,
        questions=asked,
        images=pictures,
        node_id=node_id,
        workflow_id=workflow_id,
        session_id=session_id,
        principal=principal,
    )

    for name in config.order():
        if not _eligible(name, kinds, images=bool(pictures), calibrated_only=calibrated_only):
            continue
        api_key: Optional[str] = None
        if not config.keyless(name):
            api_key = await _api_key(name, principal)
            if not api_key:
                continue
        judgment = await _attempt(name, api_key, request, timeout)
        if judgment is not None:
            return judgment
    return Judgment(available=False, reason="not_connected")


def _eligible(name: str, kinds: Set[str], *, images: bool, calibrated_only: bool) -> bool:
    if not has_judgment_provider(name):
        return False
    if not kinds <= set(config.question_types(name)):
        return False
    if images and not config.accepts_images(name):
        return False
    return config.calibrated(name) or not calibrated_only


async def _api_key(name: str, principal: Optional[str]) -> Optional[str]:
    """The provider's stored key, or None when there is none or it cannot be read."""
    from core.container import container

    try:
        return await container.auth_service().resolve_api_key(config.credential_id(name), principal=principal)
    except Exception as exc:
        logger.warning("Judgment key could not be read", provider=name, error=str(exc))
        return None


def _expected(exc: Exception, spec: JudgmentProviderSpec) -> bool:
    """A failure the provider is known to have (no traceback in the log)."""
    if isinstance(exc, NodeUserError):
        return True
    try:
        return isinstance(exc, spec.sdk_exception_types)
    except Exception:
        return False


async def _attempt(
    name: str, api_key: Optional[str], request: JudgmentRequest, timeout: Optional[float]
) -> Optional[Judgment]:
    """One call to the provider. None when it turned out not to be connected."""
    spec = get_judgment_provider(name)
    limit = timeout if timeout is not None else config.timeout_seconds(name)
    started = time.monotonic()
    try:
        provider = spec.factory(api_key=api_key, **spec.client_kwargs)
        reply = await asyncio.wait_for(provider.judge(request), timeout=limit)
        answers = check_answers(request.questions, reply.answers)
    except NotConnected:
        logger.debug("Judgment provider not connected", provider=name)
        return None
    except asyncio.TimeoutError:
        return _failed(name, "timeout", f"no answer within {limit:g} s", started)
    except InvalidReply as exc:
        return _failed(name, "invalid_reply", str(exc), started)
    except Exception as exc:
        if not _expected(exc, spec):
            logger.exception("Judgment provider failed unexpectedly", provider=name)
        return _failed(name, "provider_error", str(exc)[:300], started)
    logger.debug(
        "Judgment answered",
        provider=name,
        model=reply.model,
        questions=len(answers),
        elapsed_ms=_elapsed_ms(started),
    )
    return Judgment(
        available=True,
        answers=answers,
        provider=name,
        model=reply.model,
        calibrated=config.calibrated(name),
        cost_usd=reply.cost_usd,
    )


def _failed(name: str, reason: Reason, detail: str, started: float) -> Judgment:
    logger.warning("Judgment unavailable", provider=name, reason=reason, detail=detail, elapsed_ms=_elapsed_ms(started))
    return Judgment(available=False, provider=name, calibrated=config.calibrated(name), reason=reason, detail=detail)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


__all__ = ["judge"]
