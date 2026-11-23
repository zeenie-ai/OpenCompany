"""Jev's System One API: at TypeSafe with a TypeSafe key, or the same API
through OpenRouter with an OpenRouter key.

``to_systemone`` and ``from_systemone`` are the only places the API's field
names are written or read. They follow docs.typesafe.ai/api/system-one.md,
docs.typesafe.ai/images.md and OpenRouter's System One reference
(openrouter.ai/docs/api/api-reference/systemone), read on 2026-11-23:

- A question is ``{type, instructions, criteria}``. A choice's criteria map
  each option to a description (here none); a score's criteria list its
  levels, lowest first; a noul and a box take none.
- An answer is ``{type, noul}``, ``{type, choice, confidence, probabilities}``,
  ``{type, score, confidence, legend, probabilities}`` with probabilities
  keyed by the level's index as a string, or ``{type, box: {top, bottom,
  left, right}, confidence}``. OpenRouter may answer ``{type: "refusal"}``,
  which is left out and so reported as a missing answer.
- Each image is an asset: ``{type: "image", format, data_base64}`` under a
  placeholder string that must appear in the request, here in ``state``.

The call is booked in ``api_usage_metrics`` under the provider's name,
priced per input token from ``config/pricing.json``, unless the reply says
what it cost (OpenRouter's ``usage.cost``). Output tokens are free.
"""

from __future__ import annotations

import asyncio
import base64
import io
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from services.judgment import (
    InvalidReply,
    JudgmentImage,
    JudgmentProviderSpec,
    JudgmentReply,
    JudgmentRequest,
    NotConnected,
    Question,
    register_judgment_provider,
)
from services.judgment import config as judgment_config
from services.plugin import NodeUserError

PATH = "/systemone"
#: The operation a call is booked under (``pricing.json`` ``operation_map``).
USAGE_ACTION = "judge"
_EDGES = ("top", "bottom", "left", "right")


def to_systemone(
    model: str, state: Any, questions: Mapping[str, Question], assets: Optional[Mapping[str, Dict[str, str]]] = None
) -> Dict[str, Any]:
    """The request body."""
    body: Dict[str, Any] = {
        "model": model,
        "state": state,
        "questions": {name: _question(question) for name, question in questions.items()},
    }
    if assets:
        body["assets"] = dict(assets)
    return body


def _question(question: Question) -> Dict[str, Any]:
    asked: Dict[str, Any] = {"type": question.type, "instructions": question.description}
    if question.type == "choice":
        asked["criteria"] = {option: None for option in question.options or []}
    elif question.type == "score":
        asked["criteria"] = list(question.levels or [])
    return asked


def from_systemone(questions: Mapping[str, Question], answers: Any) -> Dict[str, Dict[str, Any]]:
    """The reply's answers in the judgment protocol's shape. An answer of
    another type is left out; the service then reports it missing."""
    if not isinstance(answers, Mapping):
        raise InvalidReply("the reply has no answers")
    read: Dict[str, Dict[str, Any]] = {}
    for name, question in questions.items():
        raw = answers.get(name)
        if not isinstance(raw, Mapping) or raw.get("type") != question.type:
            continue
        confidence = raw.get("confidence")
        if question.type == "noul":
            read[name] = {"type": "noul", "probability": raw.get("noul"), "confidence": confidence}
        elif question.type == "choice":
            probabilities = raw.get("probabilities")
            read[name] = {
                "type": "choice",
                "choice": raw.get("choice"),
                "confidence": confidence,
                "probabilities": dict(probabilities) if isinstance(probabilities, Mapping) else None,
            }
        elif question.type == "score":
            read[name] = {
                "type": "score",
                "score": raw.get("score"),
                "confidence": confidence,
                "probabilities": _by_level(raw.get("probabilities"), len(question.levels or [])),
            }
        else:
            box = raw.get("box") if isinstance(raw.get("box"), Mapping) else {}
            read[name] = {"type": "box", "confidence": confidence, **{edge: box.get(edge) for edge in _EDGES}}
    return read


def _by_level(probabilities: Any, count: int) -> Optional[List[float]]:
    """A score's probabilities as a list, lowest level first, or None when
    the reply does not give one for every level."""
    if not isinstance(probabilities, Mapping):
        return None
    ordered = [probabilities.get(str(index)) for index in range(count)]
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in ordered):
        return [float(value) for value in ordered]
    return None


def reported_cost(usage: Mapping[str, Any]) -> Optional[float]:
    """What the reply says the call cost, in USD, or None when it does not.

    TypeSafe's replies carry no cost; OpenRouter's carry ``usage.cost``. The
    rule is the one ``services/llm/providers/openrouter.py`` documents for
    chat calls: on a call paid with OpenRouter credits ``cost`` is the
    charge; on a bring-your-own-key call it is only OpenRouter's fee, so the
    upstream charge is added, and only there.
    """
    cost = usage.get("cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        return None
    if usage.get("is_byok") is not True:
        return float(cost)
    details = usage.get("cost_details")
    upstream = details.get("upstream_inference_cost") if isinstance(details, Mapping) else None
    if isinstance(upstream, (int, float)) and not isinstance(upstream, bool):
        return float(cost) + float(upstream)
    return float(cost)


def fit_sides(image: JudgmentImage, low: int, high: int) -> Tuple[bytes, str]:
    """The image scaled uniformly so that its width and height both lie from
    ``low`` to ``high`` pixels; unchanged when they already do.

    A uniform scale keeps a box's fractions true for the original image. An
    image no uniform scale can fit (a very long strip) is refused.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(image.data)) as picture:
            width, height = picture.size
            if not low or not high or (low <= min(width, height) and max(width, height) <= high):
                return image.data, image.format
            scale = high / max(width, height) if max(width, height) > high else low / min(width, height)
            size = [round(width * scale), round(height * scale)]
            if any(not low - 1 <= side <= high + 1 for side in size):
                raise NodeUserError(
                    f"An image of {width} by {height} pixels cannot be scaled so that both sides are "
                    f"{low} to {high} pixels. Crop it closer to a square."
                )
            target = tuple(min(high, max(low, side)) for side in size)
            resized = picture.resize(target, Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            if image.format == "png":
                resized.save(buffer, format="PNG")
            else:
                resized.convert("RGB").save(buffer, format="JPEG", quality=90)
            return buffer.getvalue(), image.format
    except UnidentifiedImageError as exc:
        raise NodeUserError("An image could not be read as PNG or JPEG.") from exc


def _assets(provider: str, images: Iterable[JudgmentImage]) -> Dict[str, Dict[str, str]]:
    """Each image as an asset under its own placeholder, scaled into the
    provider's side limits."""
    low, high = judgment_config.image_side_limits(provider)
    assets: Dict[str, Dict[str, str]] = {}
    for image in images:
        data, kind = fit_sides(image, low, high)
        assets[f"image-{uuid.uuid4().hex}"] = {
            "type": "image",
            "format": kind,
            "data_base64": base64.b64encode(data).decode("ascii"),
        }
    return assets


def _error_text(response: Any) -> str:
    """What the error reply says, without the request's own content (a 422
    lists the input it refused, which may be the owner's text)."""
    try:
        data = response.json()
    except ValueError:
        return str(response.reason_phrase or "")
    if isinstance(data, Mapping):
        error = data.get("error")
        if isinstance(error, Mapping) and error.get("message"):
            return str(error["message"])[:200]
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail[:200]
        if isinstance(detail, list):
            messages = [str(item["msg"]) for item in detail if isinstance(item, Mapping) and item.get("msg")]
            return "; ".join(messages)[:200]
    return str(response.reason_phrase or "")


class SystemOneProvider:
    """One System One call, to the base URL the provider's config names."""

    def __init__(self, api_key: Optional[str] = None, *, provider_name: str):
        if not api_key:
            raise NotConnected()
        self.api_key = api_key
        self.provider_name = provider_name

    async def judge(self, request: JudgmentRequest) -> JudgmentReply:
        import httpx

        name = self.provider_name
        model = judgment_config.model(name, images=bool(request.images))
        headers = {"Authorization": f"Bearer {self.api_key}"}
        state: Any = request.state
        assets: Dict[str, Dict[str, str]] = {}
        if request.images:
            assets = await asyncio.to_thread(_assets, name, request.images)
            headers.update(judgment_config.image_headers(name))
            state = {"input": request.state, "images": list(assets)}

        async with httpx.AsyncClient(timeout=judgment_config.timeout_seconds(name)) as client:
            response = await client.post(
                judgment_config.base_url(name) + PATH,
                json=to_systemone(model, state, request.questions, assets),
                headers=headers,
            )
        if response.is_error:
            raise httpx.HTTPStatusError(
                f"{name} answered {response.status_code}: {_error_text(response)}",
                request=response.request,
                response=response,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise InvalidReply("the reply is not JSON") from exc
        if not isinstance(data, Mapping):
            raise InvalidReply("the reply is not a JSON object")
        cost = await self._book(request, data.get("usage"))
        return JudgmentReply(
            answers=from_systemone(request.questions, data.get("answers")),
            model=str(data.get("model") or model),
            cost_usd=cost,
        )

    async def _book(self, request: JudgmentRequest, usage: Any) -> float:
        """Book the call. Done before its answers are read, since it was paid
        for whatever they say."""
        from services.usage import record_api_usage

        usage = usage if isinstance(usage, Mapping) else {}
        tokens = usage.get("input_tokens")
        count = int(tokens) if isinstance(tokens, (int, float)) and not isinstance(tokens, bool) else 0
        return await record_api_usage(
            self.provider_name,
            USAGE_ACTION,
            count,
            node_id=request.node_id,
            session_id=request.session_id,
            workflow_id=request.workflow_id,
            endpoint="systemone",
            cost=reported_cost(usage),
        )


for _name in ("typesafe", "openrouter"):
    register_judgment_provider(
        JudgmentProviderSpec(
            name=_name,
            factory=SystemOneProvider,
            sdk_exception_refs=("httpx:HTTPError",),
            client_kwargs={"provider_name": _name},
        )
    )
