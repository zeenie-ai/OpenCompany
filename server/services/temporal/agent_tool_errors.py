"""What the model reads when a tool call fails on Temporal (pure, workflow-safe).

A tool activity's exception reaches ``AgentWorkflow`` as ``ActivityError``
("Activity task failed"); a delegated agent's arrives as ``ChildWorkflowError``
("Child Workflow execution failed") around the ``ActivityError`` that ended
it. The failure the tool raised sits at the bottom of that chain. Runs
recorded before ``TOOL_ERROR_VERSION`` gave the model the top wrapper's own
text, which names nothing a model can correct.

Replay contract: ``agent.prepare_payload`` records ``tool_error_version`` in
the run's history, and ``AgentWorkflow`` picks the text by the recorded value.
The text is part of the transcript, whose size decides when
``agent.compact_context`` is scheduled, so a run recorded without the key
keeps the old text on replay and any change to :func:`tool_failure_text`
ships under a new version number. ``DelegatedTaskWorkflow`` uses it unversioned:
there the text is only an activity's input, which replay never compares.
"""

from __future__ import annotations

from temporalio.exceptions import ActivityError, ApplicationError, ChildWorkflowError

TOOL_ERROR_VERSION = 1


def tool_failure_text(error: BaseException) -> str:
    """``"<type>: <message>"`` of the failure the tool itself raised."""
    while isinstance(error, (ActivityError, ChildWorkflowError)) and isinstance(error.cause, BaseException):
        error = error.cause
    if isinstance(error, ApplicationError):
        return f"{error.type or type(error).__name__}: {error.message}"
    return f"{type(error).__name__}: {error}"
