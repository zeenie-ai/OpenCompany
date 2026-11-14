"""What the model reads when a tool call fails on Temporal (agent_tool_errors)."""

from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    ChildWorkflowError,
    RetryState,
    TimeoutError,
    TimeoutType,
)

from services.temporal.agent_tool_errors import tool_failure_text


def _activity_error(cause: BaseException) -> ActivityError:
    error = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="node.httpRequest.v1",
        activity_id="tool-1",
        retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
    )
    error.__cause__ = cause
    return error


def test_the_text_names_the_failure_the_tool_raised():
    cause = ApplicationError("Server error '503 Service Unavailable'", type="HTTPStatusError")

    assert tool_failure_text(_activity_error(cause)) == "HTTPStatusError: Server error '503 Service Unavailable'"


def test_a_delegated_agents_failure_is_found_under_both_wrappers():
    """A teammate's run fails in one of its activities: the lead sees
    ChildWorkflowError around ActivityError around the failure itself."""
    cause = ApplicationError("Stored conversation is 1200000 bytes", type="ConversationTooLarge")
    child = ChildWorkflowError(
        "Child Workflow execution failed",
        namespace="default",
        workflow_id="lead-agent-teammate",
        run_id="run-1",
        workflow_type="AgentWorkflow",
        initiated_event_id=5,
        started_event_id=6,
        retry_state=RetryState.NON_RETRYABLE_FAILURE,
    )
    child.__cause__ = _activity_error(cause)

    assert tool_failure_text(child) == "ConversationTooLarge: Stored conversation is 1200000 bytes"


def test_a_timeout_is_named_by_its_class():
    cause = TimeoutError("activity StartToClose timeout", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[])

    assert tool_failure_text(_activity_error(cause)) == "TimeoutError: activity StartToClose timeout"


def test_a_failure_without_a_cause_keeps_its_own_text():
    assert tool_failure_text(ValueError("bad input")) == "ValueError: bad input"
