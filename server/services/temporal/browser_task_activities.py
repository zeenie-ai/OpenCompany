"""Private owner operations and safe Workspace lifecycle projections."""

from temporalio import activity


@activity.defn(name="workspace_tasks.admit_record")
async def admit_task_record(payload: dict) -> dict:
    from services.plugin.deps import get_database
    from services.workspace_task_history import admit
    from temporalio.exceptions import ApplicationError
    try:
        return await admit(get_database(), payload)
    except ValueError:
        raise ApplicationError("Submission ID was already used for a different task",
                               type="WorkspaceSubmissionConflict", non_retryable=True) from None


@activity.defn(name="workspace_tasks.update_record")
async def update_task_record(payload: dict) -> None:
    from services.plugin.deps import get_database
    from services.workspace_task_history import transition
    await transition(get_database(), payload)


@activity.defn(name="browser_tasks.claim")
async def claim_browser_task_activity(payload: dict) -> dict:
    from services.browser_owners import claim_browser_task
    from services.plugin.deps import get_database
    return await claim_browser_task(get_database(), payload["binding"], payload["principal"], payload["task_id"])


@activity.defn(name="browser_tasks.release")
async def release_browser_task_activity(payload: dict) -> dict:
    from services.browser_owners import cleanup_browser_task
    from services.plugin.deps import get_database
    return await cleanup_browser_task(get_database(), payload["binding"], payload["task_id"])


HISTORY_ACTIVITIES = [admit_task_record, update_task_record]
BROWSER_OWNER_ACTIVITIES = [claim_browser_task_activity, release_browser_task_activity]
