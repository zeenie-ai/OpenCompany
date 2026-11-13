"""What the model reads when a tool call fails on Temporal (pure, workflow-safe).

A tool activity's exception reaches ``AgentWorkflow`` as ``ActivityError``
("Activity task failed") and a child workflow's as ``ChildWorkflowError``;
the failure the tool raised is their ``cause``. Runs recorded before
``TOOL_ERROR_VERSION`` gave the model the wrapper's own text,
``ActivityError: Activity task failed``, which names nothing a model can
correct, so a small model repeated the same call in its next run.

Replay contract: ``agent.prepare_payload`` records ``tool_error_version`` in
the run's history, and the recorded value selects the text. The text is part
of the transcript, whose size decides when ``agent.compact_context`` is
scheduled, so a run recorded without the key keeps the old text on replay and
any change to :func:`tool_failure_text` ships under a new version number.
"""

from __future__ import annotations

from temporalio.exceptions import ApplicationError

TOOL_ERROR_VERSION = 1


def tool_failure_text(error: BaseException) -> str:
    """``"<type>: <message>"`` of the failure the tool itself raised."""
    cause = getattr(error, "cause", None) or error
    if isinstance(cause, ApplicationError):
        return f"{cause.type or type(cause).__name__}: {cause.message}"
    return f"{type(cause).__name__}: {cause}"
