"""Typed questions about one input, and their answers.

A judgment asks named questions about a single input, the *state*, and gets
typed answers with probabilities. It never writes text.

- ``choice``: which of 2 to 255 options fits. The answer names the most
  likely option and gives each option's probability.
- ``score``: where the input sits on 2 to 10 ordered levels, lowest first.
  The answer is the expected level, counted from 0 and possibly fractional
  (1.7 lies between the second and third level), with each level's
  probability.
- ``noul``: how likely a statement about the input is to be true, from 0 to 1.
- ``box``: where something is in the image, as fractions of the image's
  height (``top``, ``bottom``) and width (``left``, ``right``), measured from
  its top-left corner. A box is returned even for something that is not
  there, so a box question is asked together with a noul.

Answers come back under their question's name. A name is an identifier, so
an edge condition can read ``result.answers.<name>.probability``:
``services/execution/conditions.py`` splits a field on dots and reads a part
made only of digits as a list index.

A provider answers a :class:`JudgmentRequest` with a :class:`JudgmentReply`
and books what the call cost itself, as every plugin books its paid calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Protocol, Tuple, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

QuestionType = Literal["choice", "score", "noul", "box"]
QUESTION_TYPES: Tuple[str, ...] = ("choice", "score", "noul", "box")

#: Why a judgment has no answers.
Reason = Literal["not_connected", "timeout", "provider_error", "invalid_reply"]

MIN_OPTIONS, MAX_OPTIONS = 2, 255
MIN_LEVELS, MAX_LEVELS = 2, 10

IMAGE_FORMATS: Tuple[str, ...] = ("png", "jpg")


def _labels(values: List[str], what: str) -> List[str]:
    labels = [str(value).strip() for value in values]
    if any(not label for label in labels):
        raise ValueError(f"{what} must not be empty")
    if len(set(labels)) != len(labels):
        raise ValueError(f"{what} must all be different")
    return labels


class Question(BaseModel):
    """One question. ``options`` belong to a choice, ``levels`` to a score."""

    model_config = ConfigDict(extra="forbid")

    type: QuestionType
    description: str = Field(description="What the question asks, in plain words.")
    options: Optional[List[str]] = Field(default=None, description="A choice question's options.")
    levels: Optional[List[str]] = Field(default=None, description="A score question's levels, lowest first.")

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a question needs a description")
        return value

    @model_validator(mode="after")
    def _shape(self) -> "Question":
        if self.type == "choice":
            if self.options is None or not MIN_OPTIONS <= len(self.options) <= MAX_OPTIONS:
                raise ValueError(f"a choice question needs {MIN_OPTIONS} to {MAX_OPTIONS} options")
            self.options = _labels(self.options, "options")
        elif self.options is not None:
            raise ValueError("only a choice question has options")
        if self.type == "score":
            if self.levels is None or not MIN_LEVELS <= len(self.levels) <= MAX_LEVELS:
                raise ValueError(f"a score question needs {MIN_LEVELS} to {MAX_LEVELS} levels")
            self.levels = _labels(self.levels, "levels")
        elif self.levels is not None:
            raise ValueError("only a score question has levels")
        return self


class Answer(BaseModel):
    """One question's answer. Only the fields of its type are set."""

    model_config = ConfigDict(extra="forbid")

    type: QuestionType
    #: choice: the most likely option.
    choice: Optional[str] = None
    #: score: the expected level, counted from 0; may be fractional.
    score: Optional[float] = None
    #: noul: the probability that the statement is true.
    probability: Optional[float] = None
    #: choice: each option's probability; score: each level's, lowest first.
    probabilities: Optional[Union[Dict[str, float], List[float]]] = None
    #: How sure the provider is of a choice, score or box, from 0 to 1.
    confidence: Optional[float] = None
    #: box: the edges, as fractions of the image's height (top, bottom) and
    #: width (left, right), measured from its top-left corner.
    top: Optional[float] = None
    bottom: Optional[float] = None
    left: Optional[float] = None
    right: Optional[float] = None


class Judgment(BaseModel):
    """What :func:`services.judgment.judge` found.

    ``available`` is false when nothing answered: ``reason`` says why,
    ``detail`` says it in words (never the input), and ``answers`` is empty.
    ``calibrated`` means the probabilities were measured on real outcomes
    (Jev), not guessed by a chat model.
    """

    available: bool
    answers: Dict[str, Answer] = Field(default_factory=dict)
    provider: str = ""
    model: str = ""
    calibrated: bool = False
    cost_usd: float = 0.0
    reason: Optional[Reason] = None
    detail: str = ""


@dataclass(frozen=True)
class JudgmentImage:
    """An image the questions are about, as PNG or JPEG bytes."""

    data: bytes
    format: str

    def __post_init__(self) -> None:
        if self.format not in IMAGE_FORMATS:
            raise ValueError(f"an image must be one of {', '.join(IMAGE_FORMATS)}, not {self.format!r}")
        if not self.data:
            raise ValueError("an image needs its bytes")


@dataclass(frozen=True)
class JudgmentRequest:
    """What a provider is asked. ``node_id``, ``workflow_id`` and
    ``session_id`` say where the call's usage row goes; ``principal`` is the
    owner whose model a keyless provider uses."""

    state: Any
    questions: Mapping[str, Question]
    images: Tuple[JudgmentImage, ...] = ()
    node_id: str = ""
    workflow_id: Optional[str] = None
    session_id: str = "default"
    principal: Optional[str] = None


@dataclass(frozen=True)
class JudgmentReply:
    """A provider's answers, before the service checks them against the
    questions. ``model`` is the model that answered, as the provider reports
    it; ``cost_usd`` is what the provider booked for the call."""

    answers: Mapping[str, Any]
    model: str
    cost_usd: float = 0.0


class NotConnected(Exception):
    """The provider has nothing to call with (no key, no model). Nothing was
    sent, so the service tries the next provider."""


class InvalidReply(ValueError):
    """The provider answered, but not every question in a usable form."""


@runtime_checkable
class JudgmentProvider(Protocol):
    async def judge(self, request: JudgmentRequest) -> JudgmentReply: ...


def _problems(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error.get("loc") or ())
        message = str(error.get("msg") or "").removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)


def check_questions(questions: Mapping[str, Any]) -> Dict[str, Question]:
    """The questions, each validated. Raises ``ValueError`` naming what is wrong.

    A name must be an identifier: letters, digits and underscores, not
    starting with a digit. A value is a :class:`Question` or its fields.
    """
    if not isinstance(questions, Mapping) or not questions:
        raise ValueError("ask at least one question")
    checked: Dict[str, Question] = {}
    for name, question in questions.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(
                f"question name {name!r} must be letters, digits and underscores, not starting with a digit"
            )
        if isinstance(question, Question):
            checked[name] = question
            continue
        try:
            checked[name] = Question.model_validate(question)
        except ValidationError as exc:
            raise ValueError(f"question {name!r}: {_problems(exc)}") from exc
    return checked


def _unit(value: Optional[float]) -> bool:
    return value is not None and 0.0 <= value <= 1.0


def _answer_problem(question: Question, answer: Answer) -> str:
    if question.type == "noul":
        return "" if _unit(answer.probability) else "has no probability from 0 to 1"
    if question.type == "choice":
        return "" if answer.choice in (question.options or []) else "is not one of the options"
    if question.type == "score":
        highest = len(question.levels or []) - 1
        if answer.score is not None and 0.0 <= answer.score <= highest:
            return ""
        return f"is not a level from 0 to {highest}"
    if all(_unit(edge) for edge in (answer.top, answer.bottom, answer.left, answer.right)):
        return ""
    return "has no box inside the image"


def check_answers(questions: Mapping[str, Question], answers: Mapping[str, Any]) -> Dict[str, Answer]:
    """Each question's answer, checked against its question.

    Raises :class:`InvalidReply` when an answer is missing, of another type,
    or out of range. Answers to questions that were not asked are left out.
    """
    if not isinstance(answers, Mapping):
        raise InvalidReply("the reply has no answers")
    checked: Dict[str, Answer] = {}
    for name, question in questions.items():
        raw = answers.get(name)
        if raw is None:
            raise InvalidReply(f"no answer to {name!r}")
        try:
            answer = raw if isinstance(raw, Answer) else Answer.model_validate(raw)
        except ValidationError as exc:
            raise InvalidReply(f"the answer to {name!r} is not usable") from exc
        if answer.type != question.type:
            raise InvalidReply(f"the answer to {name!r} is a {answer.type}, not a {question.type}")
        problem = _answer_problem(question, answer)
        if problem:
            raise InvalidReply(f"the answer to {name!r} {problem}")
        checked[name] = answer
    return checked


__all__ = [
    "Answer",
    "IMAGE_FORMATS",
    "InvalidReply",
    "Judgment",
    "JudgmentImage",
    "JudgmentProvider",
    "JudgmentReply",
    "JudgmentRequest",
    "MAX_LEVELS",
    "MAX_OPTIONS",
    "MIN_LEVELS",
    "MIN_OPTIONS",
    "NotConnected",
    "QUESTION_TYPES",
    "Question",
    "QuestionType",
    "Reason",
    "check_answers",
    "check_questions",
]
