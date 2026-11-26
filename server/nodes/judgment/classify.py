"""The Jev Classifier (type ``classify``): typed questions about a text,
answered with probabilities.

Named for the model it is built for, with TypeSafe's mark (``icon.svg``),
so the owner can tell it apart on the canvas and in the palette; the type
and the tool name stay ``classify``, since saved workflows keep the type.

One node, one input, and a named map of questions answered in one call by
the judgment service (``services/judgment``): TypeSafe's Jev with the
owner's TypeSafe or OpenRouter key, else the owner's chat model answering
as JSON. Edges branch on the answers with no new code, for example
``result.answers.urgent.probability`` ``gte`` 0.8 or
``result.answers.topic.choice`` ``eq`` billing.

Box questions are refused here: they need an image, and this node judges
text.

``questions`` is a JSON editor field, since the panel has no widget for a
map of objects, and ``coerce_blank_params`` parses what is typed there. No
Params field is named ``model`` or ``api_key``: a parameter-panel effect
keyed on those literal names rewrites them.
"""

from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel, Field, field_validator, model_validator

from services.judgment import Judgment, Question, check_questions, judge
from services.plugin import (
    ActionNode,
    NodeContext,
    NodeUserError,
    Operation,
    TaskQueue,
    coerce_blank_params,
)

from ..model._credentials import OpenRouterCredential
from ._credentials import TypeSafeCredential

from . import _providers  # noqa: F401  isort:skip

TOOL_DESCRIPTION = (
    "Answer typed questions about a text, with probabilities: whether a statement is true "
    '("noul"), which option fits ("choice"), or where it sits on ordered levels ("score"). '
    "Much cheaper than reasoning it out; use it for sorting, routing and yes-or-no checks."
)

EXAMPLE_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "urgent": {"type": "noul", "description": "The message needs a reply today."},
    "topic": {
        "type": "choice",
        "description": "What the message is about.",
        "options": ["billing", "support", "sales", "other"],
    },
}


class ClassifyParams(BaseModel):
    tool_name: str = Field(
        default="classify",
        description="Override name shown to the LLM when used as a tool.",
    )
    tool_description: str = Field(
        default=TOOL_DESCRIPTION,
        description="Override description shown to the LLM when used as a tool.",
        json_schema_extra={"rows": 3},
    )
    text: str = Field(
        ...,
        min_length=1,
        description="The text to judge.",
        json_schema_extra={"rows": 4},
    )
    questions: Dict[str, Question] = Field(
        default=EXAMPLE_QUESTIONS,
        validate_default=True,
        description=(
            'Named questions, as JSON. Each has a "type" and a "description": "noul" for a '
            'statement that is true or false, "choice" with its "options", or "score" with its '
            '"levels" from lowest to highest. Answers come back under the same names, as '
            "result.answers.<name>.probability, .choice or .score."
        ),
        json_schema_extra={"editor": "json", "rows": 8},
    )

    model_config = {"extra": "ignore"}

    @model_validator(mode="before")
    @classmethod
    def _coerce_panel_blanks(cls, values: Any) -> Any:
        return coerce_blank_params(cls, values, object_fields=("questions",))

    @field_validator("questions")
    @classmethod
    def _questions(cls, value: Dict[str, Question]) -> Dict[str, Question]:
        asked = check_questions(value)
        for name, question in asked.items():
            if question.type == "box":
                raise ValueError(
                    f"{name!r} is a box question, which needs an image; the Jev Classifier judges text, "
                    "so ask a choice, score or noul"
                )
        return asked


class ClassifyOutput(BaseModel):
    answers: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    provider: str = ""
    judge_model: str = ""
    calibrated: bool = False
    cost_usd: float = 0.0

    model_config = {"extra": "allow"}


def _unavailable(judgment: Judgment) -> NodeUserError:
    if judgment.reason == "not_connected":
        return NodeUserError(
            "No judgment model is connected.",
            hint="Add a TypeSafe or OpenRouter key, or connect an AI model, in Credentials.",
            requires_user_action=True,
        )
    if judgment.reason == "timeout":
        return NodeUserError(f"{judgment.provider} did not answer in time: {judgment.detail}.")
    if judgment.reason == "invalid_reply":
        return NodeUserError(f"{judgment.provider}'s answer could not be used: {judgment.detail}.")
    return NodeUserError(f"The judgment failed: {judgment.detail}")


class ClassifyNode(ActionNode):
    type = "classify"
    display_name = "Jev Classifier"
    subtitle = "TypeSafe"
    group = ("ai",)
    description = "TypeSafe's Jev answers yes-or-no, multiple-choice and scale questions about a text, with probabilities"
    component_kind = "square"
    tool_name = "classify"
    tool_description = TOOL_DESCRIPTION
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (TypeSafeCredential, OpenRouterCredential)
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.REST_API
    usable_as_tool = True
    # `usable_as_tool` would otherwise hide both main handles; the declared
    # `handles` keep the node wirable on the canvas.
    hide_input_handle = False
    hide_output_handle = False

    Params = ClassifyParams
    Output = ClassifyOutput

    @Operation("classify")
    async def classify(self, ctx: NodeContext, params: ClassifyParams) -> ClassifyOutput:
        judgment = await judge(
            params.text,
            params.questions,
            node_id=ctx.node_id,
            workflow_id=ctx.workflow_id,
            session_id=ctx.raw.get("session_id", "default"),
            principal=ctx.raw.get("user_id"),
        )
        if not judgment.available:
            raise _unavailable(judgment)
        return ClassifyOutput(
            answers={name: answer.model_dump(exclude_none=True) for name, answer in judgment.answers.items()},
            provider=judgment.provider,
            judge_model=judgment.model,
            calibrated=judgment.calibrated,
            cost_usd=judgment.cost_usd,
        )
