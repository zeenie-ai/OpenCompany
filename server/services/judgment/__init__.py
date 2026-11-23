"""System One judgments: typed questions about one input, answered with
probabilities instead of text.

``judge`` asks the first provider that can answer: TypeSafe's Jev with the
owner's TypeSafe key or their OpenRouter key, else a connected chat model
answering as JSON. It never raises for a provider's failure. The question and
answer types are in ``protocol``; the providers' order and settings are in
``config/judgment_defaults.json``. The providers themselves live in the
``nodes/judgment`` plugin, which registers them; nothing here imports it.
"""

from .protocol import (
    QUESTION_TYPES,
    Answer,
    InvalidReply,
    Judgment,
    JudgmentImage,
    JudgmentProvider,
    JudgmentReply,
    JudgmentRequest,
    NotConnected,
    Question,
    QuestionType,
    Reason,
    check_answers,
    check_questions,
)
from .registry import (
    JudgmentProviderSpec,
    get_judgment_provider,
    has_judgment_provider,
    judgment_providers,
    register_judgment_provider,
)
from .service import judge

__all__ = [
    "Answer",
    "InvalidReply",
    "Judgment",
    "JudgmentImage",
    "JudgmentProvider",
    "JudgmentProviderSpec",
    "JudgmentReply",
    "JudgmentRequest",
    "NotConnected",
    "QUESTION_TYPES",
    "Question",
    "QuestionType",
    "Reason",
    "check_answers",
    "check_questions",
    "get_judgment_provider",
    "has_judgment_provider",
    "judge",
    "judgment_providers",
    "register_judgment_provider",
]
