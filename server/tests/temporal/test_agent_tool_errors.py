"""What the model reads when a tool call fails on Temporal (agent_tool_errors)."""

from temporalio.exceptions import ActivityError, ApplicationError, RetryState, TimeoutError, TimeoutType

from services.temporal.agent_tool_errors import tool_failure_text


def _activity_error(cause: BaseException) -> ActivityError:
    error = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="agent.skill.invoke",
        activity_id="tool-1",
        retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
    )
    error.__cause__ = cause
    return error


def test_the_text_names_the_failure_the_tool_raised():
    cause = ApplicationError("Skill 'x' is not enabled and connected to this agent.", type="SkillRuntimeError")

    assert tool_failure_text(_activity_error(cause)) == (
        "SkillRuntimeError: Skill 'x' is not enabled and connected to this agent."
    )


def test_a_timeout_is_named_by_its_class():
    cause = TimeoutError("activity StartToClose timeout", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[])

    assert tool_failure_text(_activity_error(cause)) == "TimeoutError: activity StartToClose timeout"


def test_a_failure_without_a_cause_keeps_its_own_text():
    assert tool_failure_text(ValueError("bad input")) == "ValueError: bad input"
