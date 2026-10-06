"""F4.B infrastructure tests for ``AgentWorkflow`` + agent activities.

Smoke-level coverage so the worker bootstraps cleanly and the activity
shapes match what ``AgentWorkflow`` expects to schedule. Full
end-to-end testing of the agent loop (LLM step → tool dispatch →
persist → compaction) requires a Temporal test cluster + real plugin
classes — that lives in test_agent_workflow_integration.py once the
canary agent migration lands. This file locks the static contracts:

- AgentWorkflow class is decorated with ``@workflow.defn``.
- Three activities are decorated with ``@activity.defn`` and carry the
  expected ``node`` names.
- ``collect_agent_activities()`` returns them in a stable order.
- The orchestrator's worker registration imports both without error.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

class TestAgentWorkflowDefinition:
    """``AgentWorkflow`` must be a valid Temporal workflow definition
    so workers can register it."""

    def test_class_is_workflow_defn(self):
        from services.temporal.agent_workflow import AgentWorkflow

        # ``@workflow.defn`` attaches metadata as ``__temporal_workflow_definition``.
        defn = getattr(AgentWorkflow, "__temporal_workflow_definition", None)
        assert defn is not None, "AgentWorkflow missing @workflow.defn"
        assert defn.name == "AgentWorkflow"

    def test_detached_delegation_runner_is_workflow_defn(self):
        from services.temporal.agent_workflow import DelegatedTaskWorkflow

        defn = getattr(DelegatedTaskWorkflow, "__temporal_workflow_definition", None)
        assert defn is not None
        assert defn.name == "DelegatedTaskWorkflow"

    def test_class_is_sandboxed_false(self):
        """Workflow needs to import frozen registry dicts deterministically
        (for tool type → activity name resolution). Sandboxing must be off
        — same as MachinaWorkflow."""
        from services.temporal.agent_workflow import AgentWorkflow

        defn = getattr(AgentWorkflow, "__temporal_workflow_definition")
        assert defn.sandboxed is False, "AgentWorkflow must be sandboxed=False so it can read " "services.node_registry deterministically"

    def test_delegated_result_exposes_response_not_workflow_envelope(self):
        from services.temporal.agent_workflow import _normalise_delegated_result

        response, summary = _normalise_delegated_result({
            "success": True,
            "result": {"response": "child answer", "thinking": "private"},
            "usage": {"total_tokens": 12},
            "provider": "test",
        })

        assert response == "child answer"
        assert summary == {
            "response": "child answer",
            "usage": {"total_tokens": 12},
            "provider": "test",
        }


class TestDurableTeamDelegationContract:
    """Regression coverage for team-handle Temporal delegation."""

    def test_prepare_payload_expands_team_handle(self):
        import inspect

        from services.temporal.agent_activities import prepare_agent_payload

        source = inspect.getsource(prepare_agent_payload)
        assert "collect_teammate_connections" in source
        assert '"input-tools"' in source
        assert "get_or_create_execution_team" in source
        assert '"team_id": execution_team_id' in source
        assert '"outputs": context.get("outputs") or context.get("inputs")' in source
        assert "format_task_context(trigger_task_data)" in source
        assert "and not trigger_task_data" in source
        assert 'team_execution_id = (' in source

    def test_agent_result_carries_owning_team_identity(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert '"team_id": payload.get("team_id") or context.get("team_id")' in source
        assert '"execution_id": context.get("execution_id")' in source


class TestConversationIdentity:
    """Every firing continues one conversation and saves each turn once."""

    def test_conversation_key_is_workflow_generation_agent(self):
        """The conversation key is (workflow_id, generation, agent_node_id).

        The journal design keyed threads per firing/session, so a taskTrigger
        review resolved a different thread from the chat that started the
        work and the lead came back amnesiac (messages=2). One key per agent
        per generation makes every firing continue the same conversation.
        """
        import inspect

        from services.temporal import agent_activities

        source = inspect.getsource(agent_activities.prepare_agent_payload)
        assert '"workflow_id"' in source
        assert '"generation"' in source
        assert '"agent_node_id"' in source
        assert "conversation_key" in source

    def test_no_local_import_shadows_a_name_after_use(self):
        """A function-local import makes its name local for the WHOLE
        function, so any use above the import raises UnboundLocalError at
        runtime — invisible to py_compile. A mid-function ``import json``
        below the conversation size guard failed every Context-connected
        run at ``prepare_agent_payload`` (attempt 3, ConversationLoadFailed
        never even reached: the crash was the guard itself).
        """
        import ast
        import inspect

        from services.temporal import agent_activities

        tree = ast.parse(inspect.getsource(agent_activities))
        offenders: list[str] = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            imported_at: dict[str, int] = {}
            used_at: dict[str, int] = {}
            for node in ast.walk(func):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        name = alias.asname or alias.name.split(".")[0]
                        line = imported_at.get(name)
                        imported_at[name] = (
                            node.lineno if line is None else min(line, node.lineno)
                        )
                elif isinstance(node, ast.Name) and isinstance(
                    node.ctx, ast.Load
                ):
                    if node.id not in used_at:
                        used_at[node.id] = node.lineno
            for name, import_line in imported_at.items():
                use_line = used_at.get(name)
                if use_line is not None and use_line < import_line:
                    offenders.append(
                        f"{func.name}: '{name}' used at line {use_line} "
                        f"but locally imported at line {import_line}"
                    )
        assert not offenders, (
            "local imports shadowing an earlier use (UnboundLocalError at "
            f"runtime): {offenders}"
        )

    def test_compaction_preserves_the_system_prompt_and_summary_survival(
        self,
    ):
        """Compaction rebuilds as [original system, user(summary+request)].

        Two rules are locked. (1) The system prompt is never modified or
        duplicated: it is the agent's contract and must stay byte-stable
        (provider prompt caching, behavioral consistency). (2) The summary
        must NOT ride ``role="system"`` — the next firing's seeding drops
        stored system messages so policy changes take effect, which once
        made the summary vanish on the next chat message while the noisy
        tool tail (non-system) survived it. As a user message, the
        compacted knowledge crosses firings with the conversation.
        """
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "## Compacted summary:" not in source, (
            "the old system-role summary marker is retired"
        )
        start = source.index('summary = compact_result.get("summary"')
        end = source.index("context_usage_total = {}", start)
        rebuild = source[start:end]
        assert rebuild.count('role="system"') == 1, (
            "compaction must keep exactly ONE system message — the "
            "original, verbatim"
        )
        assert 'role="user"' in rebuild, (
            "the summary must ride a user message so it survives the next "
            "firing's stored-system filter"
        )
        # Observability: both the trigger and the applied swap are logged.
        assert "compaction triggered at iteration" in source
        assert "compaction applied at iteration" in source

    def test_resumed_run_continues_from_the_carried_transcript(self):
        """continue_as_new carries the live transcript itself.

        The journal-replay design reconstructed the conversation from the
        Context store on resume, and its journal was missing every tool
        result — so the replayed transcript ended on an assistant tool-call
        turn with no answers, which every provider rejects (Gemini 400:
        "Requests ending with a model turn are not supported"). Carrying
        the messages directly makes that bug unrepresentable.
        """
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert 'resume.get("transcript")' in source, (
            "a resumed AgentWorkflow must continue from the transcript "
            "carried across continue_as_new"
        )
        assert "agent.reconstruct_context_messages" not in source, (
            "journal replay on resume was retired; the transcript crosses "
            "the boundary directly"
        )
        carried_at = source.index('resume.get("transcript")')
        loop_at = source.index("agent.execute_llm_step")
        assert carried_at < loop_at, (
            "the carried transcript must be adopted before the first LLM "
            "step of the resumed run"
        )

    def test_rollover_guards_transcript_size(self):
        """The CAN argument shares Temporal's 2 MiB payload error limit.

        An oversized transcript must degrade to the opening prompt with a
        warning instead of failing the rollover itself (which would kill
        the run at exactly the moment it tried to survive).
        """
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "_CAN_TRANSCRIPT_MAX_BYTES" in source
        guard_at = source.index("_CAN_TRANSCRIPT_MAX_BYTES")
        can_at = source.index("workflow.continue_as_new(")
        assert guard_at < can_at, (
            "the size guard must run before continue_as_new is issued"
        )

    def test_workflow_never_claims_the_activity_rebuilds_the_request(self):
        """Guards the comment, not the code.

        Two comments used to state that the LLM activity reconstructs from the
        store rather than from ``messages``. That was false, and it is exactly
        the sentence a future reader would implement to reintroduce the
        original bug.
        """
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        for claim in (
            "reconstructs\n",
            "source of truth for the transcript",
            "rather than from `messages`",
        ):
            assert claim not in source, (
                f"stale claim {claim!r} in AgentWorkflow.run -- the activity "
                f"always builds its request from `messages`"
            )

    def test_llm_step_has_a_single_implementation(self):
        """A Context node observes execution; it must not steer it.

        ``context_ref`` used to select a second, journal-backed LLM
        implementation that rebuilt the request from the store instead of
        sending ``messages``. That transcript did not carry the user's prompt,
        so merely connecting a Context node made the agent answer an empty
        question.
        """
        import inspect

        from services.temporal import agent_activities

        source = inspect.getsource(agent_activities)
        assert "_execute_context_llm_step" not in source, (
            "a second LLM implementation selected by context_ref is exactly "
            "how attaching a Context node changed the agent's request"
        )
        step = inspect.getsource(agent_activities.execute_llm_step)
        assert "if payload.get(\"context_ref\")" not in step

    def test_save_failure_cannot_fail_the_run(self):
        """Persistence must never break execution.

        The conversation save happens after the provider has been called and
        billed, so raising there fails the turn over a bookkeeping write — and
        for a team lead, stalls its next delegation.
        """
        import ast
        import inspect

        from services.temporal import agent_activities

        fn = ast.parse(
            inspect.getsource(agent_activities._save_conversation)
        ).body[0]
        handlers = [
            handler
            for node in ast.walk(fn)
            if isinstance(node, ast.Try)
            for handler in node.handlers
        ]
        assert handlers, (
            "_save_conversation must not let a write failure escape"
        )
        assert any(
            h.type is None
            or (isinstance(h.type, ast.Name) and h.type.id == "Exception")
            for h in handlers
        )

    def test_conversation_records_the_exact_request_never_a_reconstruction(
        self,
    ):
        """The store must record what was sent, not rebuild it.

        The retired ``prepare_context`` activity wrote journal entries
        assembled from configuration before the request existed — that is
        how the journal came to hold a fabricated request. The turn is saved
        from the exact list handed to ``ChatUnifier.chat``, after the call.
        """
        import inspect

        from services.temporal import agent_activities

        assert not hasattr(agent_activities, "prepare_context"), (
            "prepare_context journalled configuration before a request "
            "existed; the plain store saves only what was sent"
        )

        native = inspect.getsource(agent_activities._execute_native_llm_step)
        sent = native.index("_save_conversation")
        called = native.index("run_native_llm_step(")
        assert called < sent, "save the request only after it was sent"

    def test_root_execution_identity_is_initialized_before_agent_loop(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        initialization = source.index("root_execution_id = str(")
        tool_loop = source.index("for iteration in range(iteration_offset, max_iterations)")
        result_payload = source.index('"root_execution_id": root_execution_id')
        assert initialization < tool_loop < result_payload

    def test_same_turn_delegations_start_before_ordered_await(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "workflow.start_child_workflow" in source
        assert "max_concurrent_subagents" in source
        assert "delegation_handles[call_index]" in source
        assert "tool_result = await handle" in source

    def test_task_manager_assignment_returns_after_detached_start(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow, DelegatedTaskWorkflow

        lead_source = inspect.getsource(AgentWorkflow._run_impl)
        runner_source = inspect.getsource(DelegatedTaskWorkflow.run)
        assert '"DelegatedTaskWorkflow"' in lead_source
        assert "ParentClosePolicy.ABANDON" in lead_source
        assert 'return {"status": "queued"' in lead_source
        assert '"agent.finish_delegation"' in runner_source
        assert '"agent.release_subagent_permit"' in runner_source

    def test_queued_work_does_not_block_lead_final_response(self):
        import inspect

        from services.temporal.agent_activities import prepare_agent_payload
        from services.temporal.agent_workflow import AgentWorkflow

        workflow_source = inspect.getsource(AgentWorkflow._run_impl)
        prepare_source = inspect.getsource(prepare_agent_payload)
        assert "Team finalization is blocked" not in workflow_source
        assert "must not force this lead" in workflow_source
        assert "do not poll or wait in this run" in prepare_source

    def test_child_invocation_has_isolated_trace_envelope(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        for field in (
            "root_execution_id",
            "parent_node_id",
            "delegation_depth",
            "team_id",
            "team_task_id",
            "trace_id",
            "invocation",
        ):
            assert f'"{field}"' in source

    def test_durable_assignment_precedes_child_start(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        queue_at = source.index('"agent.queue_delegation"')
        acquire_at = source.index('"agent.acquire_subagent_permit"')
        claim_at = source.index('"agent.begin_delegation"')
        start_at = source.index("workflow.start_child_workflow")
        assert queue_at < acquire_at < claim_at < start_at
        assert '"agent.finish_delegation"' in source
        assert '"agent.release_subagent_permit"' in source
        assert "assignment_event_id" in source
        assert "terminal_event_id" in source

    def test_task_manager_assignment_uses_existing_delegation_lifecycle(self):
        """A persisted assign_task envelope must start real child work."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert 'tool_info["node_type"] == "taskManager"' in source
        assert 'tool_result.get("delegation_request")' in source
        assert "_run_task_manager_delegation" in source
        assert 'task_id = str(request.get("team_task_id")' in source
        assert 'delegate_name = str(request.get("delegate_name")' in source
        assert 'str(delegate.get("tool_node_id") or "") != assignee_id' in source

    def test_task_manager_bridge_is_bounded_and_retry_safe(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow, DelegatedTaskWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        queue = source.index('activity_id=f"queue-task-manager-')
        detached = source.index('"DelegatedTaskWorkflow"', queue)
        assert queue < detached
        runner = inspect.getsource(DelegatedTaskWorkflow.run)
        permit = runner.index('"agent.acquire_subagent_permit"')
        claim = runner.index('"agent.begin_delegation"')
        child = runner.index('"AgentWorkflow"', claim)
        finish = runner.index('"agent.finish_delegation"', child)
        release = runner.index('"agent.release_subagent_permit"', finish)
        assert permit < claim < child < finish < release
        assert '"permit_id": task_id' in runner
        assert '"team_task_id": task_id' in source

    def test_task_manager_child_lead_yields_own_permit(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "yield-own-permit-task-manager" in source
        assert "if own_permit_id and not yielded_own_permit" in source

    def test_same_turn_task_manager_assignments_preflight_and_run_concurrently(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        start_activity = source.index("workflow.start_activity(")
        gather = source.index("await asyncio.gather(")
        create_children = source.index("asyncio.create_task(", gather)
        ordered_loop = source.index("for call_index, call in enumerate(calls):", create_children)
        ordered_await = source.index(
            "await task_manager_delegation_tasks.pop(call_index)", ordered_loop
        )
        assert start_activity < gather < create_children < ordered_loop < ordered_await
        assert "task_manager_preflight_results[call_index]" in source
        assert "task-manager-preflight-" in source
        assert "return_exceptions=True" in source


class TestAgentActivities:
    """The three agent activities must register under stable names so
    ``AgentWorkflow`` can schedule them by string."""

    def test_execute_llm_step_registered(self):
        from services.temporal.agent_activities import execute_llm_step

        defn = getattr(execute_llm_step, "__temporal_activity_definition", None)
        assert defn is not None
        assert defn.name == "agent.execute_llm_step"

    def test_persist_agent_turn_registered(self):
        from services.temporal.agent_activities import persist_agent_turn

        defn = getattr(persist_agent_turn, "__temporal_activity_definition")
        assert defn.name == "agent.persist_turn"

    def test_compact_context_registered(self):
        from services.temporal.agent_activities import compact_context

        defn = getattr(compact_context, "__temporal_activity_definition")
        assert defn.name == "agent.compact_context"

    def test_collect_returns_all_agent_activities(self):
        """Every activity the AgentWorkflow loop schedules by name must
        register here. The single-standard cleanup retired the journal
        replay surface (reconstruct_context_messages / append_context /
        compact_memory) — the transcript now crosses continue_as_new
        directly and compaction summarizes the live conversation."""
        from services.temporal.agent_activities import collect_agent_activities

        activities = collect_agent_activities()
        names = sorted(getattr(a, "__temporal_activity_definition").name for a in activities)
        assert names == [
            "agent.acquire_subagent_permit",
            "agent.begin_delegation",
            "agent.broadcast_progress",
            "agent.cancel_delegation",
            "agent.compact_context",
            "agent.execute_llm_step",
            "agent.finalize_team",
            "agent.finish_delegation",
            "agent.persist_turn",
            "agent.prepare_payload",
            "agent.queue_delegation",
            "agent.refresh_tools",
            "agent.register_task_execution",
            "agent.release_subagent_permit",
            "agent.skill.clear",
            "agent.skill.invoke",
            "agent.store_output",
            "controller.queue.read",
            "controller.queue.spill",
            "employee.job.deliver",
            "employee.job.failed",
            "employee.job.resolve_delivery",
        ]

    def test_prepare_payload_registered(self):
        from services.temporal.agent_activities import prepare_agent_payload

        defn = getattr(prepare_agent_payload, "__temporal_activity_definition")
        assert defn.name == "agent.prepare_payload"

    def test_broadcast_progress_registered(self):
        from services.temporal.agent_activities import broadcast_agent_progress

        defn = getattr(broadcast_agent_progress, "__temporal_activity_definition")
        assert defn.name == "agent.broadcast_progress"

    async def test_temporal_tool_progress_preserves_visible_tool_name(self, monkeypatch):
        """A phase-only Temporal event must still update the agent card.

        AgentWorkflow intentionally omits ``status`` for intermediate tool
        phases.  The activity therefore owns translating a tool-bearing
        progress payload into an executing node status; otherwise the
        frontend receives only the generic phase and cannot render
        ``tool <name>`` for normal AI agents.
        """
        import services.status_broadcaster as broadcaster_module
        from services.temporal.agent_activities import broadcast_agent_progress

        broadcaster = MagicMock()
        broadcaster.update_node_status = AsyncMock()
        broadcaster.broadcast_agent_progress = AsyncMock()
        broadcaster.broadcast_agent_capability = AsyncMock()
        monkeypatch.setattr(
            broadcaster_module,
            "get_status_broadcaster",
            lambda: broadcaster,
        )

        await broadcast_agent_progress(
            {
                "node_id": "normal-agent",
                "workflow_id": "7",
                "iteration": 1,
                "max_iterations": 20,
                "phase": "executing_tool",
                "tool_name": "write_todos",
                "tool_node_id": "7:writeTodos:1",
            }
        )

        broadcaster.update_node_status.assert_awaited_once_with(
            "normal-agent",
            "executing",
            {
                "agent_type": "temporal",
                "phase": "executing_tool",
                "tool_name": "write_todos",
                "tool_node_id": "7:writeTodos:1",
            },
            workflow_id="7",
        )
        broadcaster.broadcast_agent_capability.assert_awaited_once()
        capability = broadcaster.broadcast_agent_capability.await_args
        assert capability.args == ("normal-agent",)
        assert capability.kwargs["capability_kind"] == "tool"
        assert capability.kwargs["capability_name"] == "write_todos"
        assert capability.kwargs["state"] == "started"
        assert capability.kwargs["target_node_id"] == "7:writeTodos:1"
        assert capability.kwargs["event_id"].startswith("agent-capability-")

    def test_llm_failure_clears_skills_and_emits_terminal_error(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        failure = source.index("except Exception as e:", source.index('"agent.execute_llm_step"'))
        terminal_return = source.index('"error_type": "LLMStepError"', failure)
        cleanup = source.index('activity_id="clear-active-skills-failed"', failure)
        error_phase = source.index('phase="failed"', cleanup)
        assert failure < cleanup < error_phase < terminal_return

    async def test_child_capability_metadata_is_not_mirrored_to_parent(self, monkeypatch):
        """Only the agent that invoked a capability may display its name."""
        import services.temporal.agent_workflow as workflow_module
        from services.temporal.agent_workflow import AgentWorkflow

        execute_activity = AsyncMock(return_value=None)
        monkeypatch.setattr(workflow_module.workflow, "execute_activity", execute_activity)

        agent = AgentWorkflow()
        agent._parent_node_id = "lead-agent"
        await agent._emit_phase(
            "child-agent",
            "7",
            2,
            20,
            phase="executing_tool",
            extra={"tool_name": "child_search", "tool_node_id": "7:search:1"},
        )

        child_payload = execute_activity.await_args_list[0].kwargs["args"][0]
        parent_payload = execute_activity.await_args_list[1].kwargs["args"][0]
        assert child_payload["node_id"] == "child-agent"
        assert child_payload["tool_name"] == "child_search"
        assert parent_payload == {
            "node_id": "lead-agent",
            "workflow_id": "7",
            "iteration": 2,
            "max_iterations": 20,
            "phase": "delegating",
        }


class TestWorkerWiring:
    """Worker registration must include AgentWorkflow + activities so the
    orchestrator can schedule them once the flag flips on. We can't
    spin up a real Temporal client here, but we can verify the
    registration list is built without import errors."""

    def test_agent_workflow_importable_from_worker(self):
        """The worker module imports AgentWorkflow at registration time.
        If that import fails (circular dep, missing symbol, etc.) the
        whole Temporal worker bootstrap dies — catch it here."""
        # Just importing is enough; ImportError would surface in the test
        # output.
        from services.temporal.worker import TemporalWorkerManager  # noqa: F401
        from services.temporal.agent_workflow import AgentWorkflow  # noqa: F401
        from services.temporal.agent_activities import collect_agent_activities  # noqa: F401


class TestPayloadShape:
    """Static checks on the workflow's payload contract — keeps the
    seams visible to anyone refactoring the input pipeline. If a
    required key disappears, this test surfaces it before runtime."""

    REQUIRED_KEYS = (
        "node_id",
        "node_type",
        "provider",
        "model",
        "system_message",
        "user_prompt",
        "tools",
        "max_iterations",
    )

    def test_required_keys_documented(self):
        """The README-style payload comment in ``AgentWorkflow.run``'s
        docstring must list every required key. Drift = unreadable
        docs + broken callers. Cross-check against an explicit
        constant here so the docstring can't quietly shrink."""
        from services.temporal.agent_workflow import AgentWorkflow

        docstring = AgentWorkflow._run_impl.__doc__ or ""
        missing = [k for k in self.REQUIRED_KEYS if f'"{k}"' not in docstring]
        assert not missing, (
            f"AgentWorkflow.run docstring missing payload keys: {missing}. "
            "If you renamed a field, update both the docstring and the body."
        )


class TestDelegationToolDispatch:
    """Regression: when the LLM emits a ``delegate_to_<child>`` tool
    call inside ``AgentWorkflow``'s tool-dispatch loop, the resulting
    activity payload MUST:

    1. Remap ``args.task → node_data.system_message`` and
       ``args.context → node_data.prompt`` so the child agent's
       ``Params`` model picks them up. Pre-fix the workflow merged
       ``call.args`` (``{task, context}``) into ``node_data`` as-is —
       ``SpecializedAgentParams`` doesn't have those fields, so the
       child got empty prompt/system_message and Gemini failed with
       ``contents are required``.
    2. Carry the full canvas (``nodes`` + ``edges``) so the child's
       ``collect_agent_connections`` edge walk finds its connected
       skills / memory / tools. Pre-fix this was ``[]`` / ``[]`` for
       every tool call — fine for regular tools but broken for
       delegation.

    Source-introspection invariant — runtime test against the live
    workflow body needs a Temporal WorkflowEnvironment which is too
    heavy for unit tests. The source check is enough to lock the
    behaviour against regression.
    """

    def test_dispatch_remaps_delegation_args(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)

        # Detection: must check for the ``delegate_to_`` tool-name prefix.
        assert "delegate_to_" in src, (
            "AgentWorkflow tool dispatch lost the ``delegate_to_`` "
            "detection branch. Without it, delegation tool calls take "
            "the regular-tool path which leaves the child agent's "
            "``prompt`` + ``system_message`` empty and Gemini fails "
            "with ``contents are required``."
        )
        # Remapping: task → system_message, context → prompt.
        assert "system_message" in src and "task" in src and "prompt" in src, (
            "AgentWorkflow tool dispatch must map the LLM's "
            "``{task, context}`` args to the child agent's "
            "``{system_message, prompt}`` Params. Same mapping the "
            "legacy ``_execute_delegated_agent`` applies."
        )

    def test_dispatch_passes_canvas_for_delegation(self):
        """Delegation tool calls must pass the parent's ``nodes`` +
        ``edges`` to the child agent's activity so the child's edge
        walk can find its skills/memory/tools. Regular tool calls
        keep the empty-canvas optimisation."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)

        # The fix uses ``context.get("nodes")`` / ``context.get("edges")``
        # inside the delegation branch.
        assert 'context.get("nodes")' in src, (
            "AgentWorkflow tool dispatch must read ``context.get('nodes')`` "
            "to pass the canvas to delegation tool calls. Without it, "
            "the child agent's edge walk sees an empty graph and can't "
            "resolve its connected skills / memory / tools."
        )
        assert 'context.get("edges")' in src, (
            "AgentWorkflow tool dispatch must read ``context.get('edges')`` "
            "for the same reason — both are needed by "
            "``collect_agent_connections``."
        )


class TestAutoRebindTools:
    """Mid-run tool rebind after canvas-mutating tools return
    ``operations`` (workflow_ops batch). The flag is read once in
    ``prepare_agent_payload``, forwarded into every tool's payload, and
    surfaced into ``ctx.raw["auto_rebind_tools"]`` so agentBuilder's
    summary text reflects the user's preference. The rebind itself
    happens in ``AgentWorkflow.run`` via a new
    ``agent.refresh_tools.v1`` activity.
    """

    def test_refresh_tools_activity_registered(self):
        from services.temporal.agent_activities import refresh_agent_tools

        defn = getattr(refresh_agent_tools, "__temporal_activity_definition", None)
        assert defn is not None, "refresh_agent_tools missing @activity.defn"
        assert defn.name == "agent.refresh_tools"

    def test_refresh_tools_in_collect(self):
        """Worker registration must include the new activity so
        AgentWorkflow can schedule it."""
        from services.temporal.agent_activities import collect_agent_activities, refresh_agent_tools

        names = {getattr(a, "__temporal_activity_definition").name for a in collect_agent_activities()}
        assert "agent.refresh_tools" in names
        assert refresh_agent_tools in collect_agent_activities()

    async def test_refresh_tools_runs_without_nameerror(self, monkeypatch):
        """Smoke test: the activity body must import ``container`` and
        ``get_node_class`` so it doesn't NameError on first invocation.
        Mirrors the rest of the agent_activities.py pattern (lazy import
        inside each activity body)."""
        from services.temporal.agent_activities import refresh_agent_tools
        from core.container import container

        monkeypatch.setattr(container, "ai_service", MagicMock(return_value=MagicMock()))

        # Pass empty operations so the activity short-circuits before
        # any plugin lookup — we just want to confirm the imports + the
        # ``container.ai_service()`` call don't raise NameError.
        result = await refresh_agent_tools({"operations": []})
        assert result == {"tools": [], "graph_snapshot": {}, "parameter_updates": {}}

    def test_workflow_calls_refresh_after_ops(self):
        """AgentWorkflow.run must schedule ``agent.refresh_tools.v1``
        when a tool result carries an ``operations`` field."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)
        assert '"agent.refresh_tools"' in src, (
            "AgentWorkflow tool dispatch must schedule agent.refresh_tools.v1 "
            "when a tool result returns workflow_ops operations."
        )
        # The rebind branch must extend `tools` and `tool_index` so the
        # next execute_llm_step iteration sees the new tools.
        assert "tools.append" in src or "tools.extend" in src, (
            "AgentWorkflow must extend its tools list after refresh."
        )
        assert "tool_index[" in src, "AgentWorkflow must extend tool_index after refresh."

    def test_prepare_payload_surfaces_auto_rebind_flag(self):
        """prepare_agent_payload reads the UserSettings flag and includes
        ``auto_rebind_tools`` in its returned payload so AgentWorkflow
        + the tool dispatch see the user's preference."""
        import inspect

        from services.temporal.agent_activities import prepare_agent_payload

        src = inspect.getsource(prepare_agent_payload)
        assert "auto_rebind_tools_after_canvas_change" in src, (
            "prepare_agent_payload must read the user setting."
        )
        assert '"auto_rebind_tools"' in src, (
            "prepare_agent_payload return must include the resolved flag."
        )

    def test_tool_payload_forwards_auto_rebind(self):
        """The per-tool activity payload must forward
        ``auto_rebind_tools`` so the F4.A wrapper can land it into
        ctx.raw for agentBuilder's summary text."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)
        assert '"auto_rebind_tools"' in src, (
            "AgentWorkflow tool_payload must include auto_rebind_tools "
            "so the per-tool activity surfaces it into ctx.raw."
        )


class TestExecutionIdPropagation:
    """A stable per-run ``execution_id`` must flow into every tool-call
    activity. Session-keyed nodes (browser) derive their session name
    from it — without propagation, ``NodeExecutor.execute`` mints a
    fresh uuid per call and every browser tool call spawns a NEW Chrome
    instance instead of reusing the run's browser.

    Source-introspection invariants — a live run needs a Temporal
    WorkflowEnvironment, too heavy for unit tests (same rationale as
    ``TestDelegationToolDispatch``).
    """

    def test_agent_workflow_tool_payload_carries_execution_id(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)
        assert '"execution_id"' in src, (
            "AgentWorkflow tool_payload must include execution_id so "
            "session-keyed tools (browser) reuse one instance per run."
        )
        assert "workflow.info().run_id" in src, (
            "AgentWorkflow must fall back to the deterministic "
            "workflow.info().run_id when the input omits execution_id."
        )

    def test_as_activity_forwards_execution_id(self):
        import inspect

        from services.plugin.base import BaseNode

        src = inspect.getsource(BaseNode.as_activity)
        assert 'execution_id=context.get("execution_id")' in src, (
            "BaseNode.as_activity must pass execution_id through to "
            "workflow_service.execute_node — otherwise NodeExecutor "
            "mints a fresh uuid per tool call."
        )

    def test_opencompany_workflow_threads_execution_id(self):
        import inspect

        from services.temporal.workflow import MachinaWorkflow

        src = inspect.getsource(MachinaWorkflow.run)
        assert '"execution_id"' in src, (
            "MachinaWorkflow per-node context must carry execution_id."
        )
        assert "workflow.info().workflow_id" in src, (
            "MachinaWorkflow must fall back to its own workflow id "
            "(identical to the executor-minted execution_id by construction)."
        )

    def test_temporal_executor_passes_execution_id_in_input(self):
        import inspect

        from services.temporal.executor import TemporalExecutor

        src = inspect.getsource(TemporalExecutor.execute_workflow)
        assert '"execution_id": execution_id' in src, (
            "TemporalExecutor must thread the minted execution_id into "
            "the MachinaWorkflow input dict, not only the workflow id."
        )


class TestDelegationInvocationContract:
    """Regression: the delegated task must survive the child's config
    resolution. Pre-fix the parent remapped ``{task, context}`` into the
    child's ``node_data`` (configuration channel) and
    ``prepare_agent_payload`` merged ``{**node_data, **db_params}`` —
    the child node's persisted ``prompt: ""`` (the Pydantic default the
    frontend saves on drop) clobbered the delegated task, the child's
    message list ended up system-only, and Gemini rejected it with
    ``contents are required`` (3 wasted retries per call).

    Post-fix the delegation travels as the child workflow input's
    ``invocation`` field (Temporal input-vs-config separation; see
    docs.temporal.io/develop/python/workflows single-object input
    guidance) and ``prepare_agent_payload`` applies it AFTER the config
    merge — mirroring the legacy working path
    (``handlers.tools._execute_delegated_agent`` applies its remap after
    loading DB params, so it always wins).

    Source-introspection invariants — a live run needs a Temporal
    WorkflowEnvironment, too heavy for unit tests (same rationale as
    ``TestDelegationToolDispatch``).
    """

    def test_child_context_carries_invocation_field(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)
        assert '"invocation"' in src, (
            "AgentWorkflow delegation spawn must pass the per-invocation "
            "{task, context} as the child workflow input's 'invocation' "
            "field. Smuggling it through node_data lets the child's "
            "persisted empty prompt clobber the delegated task."
        )

    def test_empty_task_rejected_before_spawn(self):
        """A delegate_to_* call with neither task nor context must be
        rejected at the call boundary (tool-error message back to the
        LLM) instead of spawning a child workflow that cannot run."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)
        assert "non-empty 'task'" in src, (
            "AgentWorkflow delegation branch must validate the invocation "
            "(task/context both empty -> tool error, no child spawn)."
        )

    def test_prepare_payload_applies_invocation_after_config_merge(self):
        """The invocation override must run AFTER the
        ``{**node_data, **db_params}`` config merge — order is the whole
        fix. If someone 'simplifies' it back into the merge, the DB's
        empty prompt wins again."""
        import inspect

        from services.temporal.agent_activities import prepare_agent_payload

        src = inspect.getsource(prepare_agent_payload)
        assert 'context.get("invocation")' in src, (
            "prepare_agent_payload must read the child workflow input's "
            "'invocation' field."
        )
        assert src.index("**db_params") < src.index('context.get("invocation")'), (
            "Invocation must be applied AFTER the node_data/db_params "
            "config merge so stored parameters can never clobber the "
            "delegated task."
        )


class TestEmptyPromptGuard:
    """``execute_llm_step`` must fail fast — attempt 1, non-retryable —
    when the filtered message list has no invokable content, instead of
    letting Gemini raise an opaque retryable ``ValueError: contents are
    required`` that burns the full retry budget on a deterministic
    failure. Uses Temporal's documented mechanism for business-rule
    failures: ``ApplicationError(..., non_retryable=True)``."""

    def test_raises_non_retryable_on_system_only_list(self):
        import pytest
        from temporalio.exceptions import ApplicationError

        from services.llm.protocol import Message
        from services.temporal.agent_activities import _ensure_llm_contents

        with pytest.raises(ApplicationError) as excinfo:
            _ensure_llm_contents([Message(role="system", content="you are helpful")])
        assert excinfo.value.non_retryable is True
        assert excinfo.value.type == "EmptyAgentPrompt"

    def test_raises_on_empty_list(self):
        import pytest
        from temporalio.exceptions import ApplicationError

        from services.temporal.agent_activities import _ensure_llm_contents

        with pytest.raises(ApplicationError):
            _ensure_llm_contents([])

    def test_passes_with_human_message(self):
        from services.llm.protocol import Message
        from services.temporal.agent_activities import _ensure_llm_contents

        _ensure_llm_contents(
            [Message(role="system", content="sys"), Message(role="user", content="hi")]
        )

    def test_passes_with_tool_message(self):
        """Mid-loop turns may legitimately be tool-result-only."""
        from services.llm.protocol import Message
        from services.temporal.agent_activities import _ensure_llm_contents

        _ensure_llm_contents(
            [
                Message(role="system", content="sys"),
                Message(role="tool", content="42", tool_call_id="c1"),
            ]
        )

    def test_guard_runs_after_empty_message_filter(self):
        """The guard must see the POST-filter list — a whitespace-only
        HumanMessage passes the workflow's truthiness check but gets
        stripped by ``filter_empty_messages``, so guarding pre-filter
        would miss exactly the failing case."""
        import inspect

        from services.temporal.agent_activities import _execute_native_llm_step

        src = inspect.getsource(_execute_native_llm_step)
        assert "_ensure_llm_contents(messages)" in src
        assert src.index("filter_empty_messages(") < src.index(
            "_ensure_llm_contents(messages)"
        )


class TestNeedsCanvasDispatch:
    """Regression: regular (non-delegation) tools opt into canvas
    propagation via the ``BaseNode.needs_canvas`` ClassVar. The F4.B
    tool-dispatch path must read the flag via
    ``services.node_registry.get_node_class`` rather than hardcoding
    per-plugin type strings. Locks the principled fix for the
    agentBuilder ``nodes=0 edges=0`` bug.
    """

    def test_dispatch_uses_get_node_class_lookup(self):
        """The non-delegation branch must look the plugin class up at
        dispatch time so ``cls.needs_canvas`` decides canvas
        propagation. A hardcoded type-string check would silently break
        for any future canvas-aware tool."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        src = inspect.getsource(AgentWorkflow._run_impl)
        assert "get_node_class(" in src, (
            "AgentWorkflow tool dispatch must call ``get_node_class("
            "tool_info['node_type'])`` so it can read the plugin's "
            "``needs_canvas`` ClassVar. Hardcoded type-string checks "
            "are forbidden — they don't compose for future canvas-"
            "aware tools."
        )
        assert "needs_canvas" in src, (
            "AgentWorkflow tool dispatch must read the resolved "
            "``plugin_cls.needs_canvas`` flag. Without it the canvas "
            "never reaches agentBuilder and ``_resolve_caller`` falls "
            "back to self-as-caller."
        )

    def test_get_node_class_imported_at_module_level(self):
        """The helper must be importable from the workflow module
        — Temporal's ``@workflow.defn(sandboxed=False)`` lets us touch
        ``services.node_registry`` deterministically."""
        from services.temporal import agent_workflow

        assert hasattr(agent_workflow, "get_node_class"), (
            "agent_workflow.py must import ``get_node_class`` at module "
            "level so the workflow body can resolve plugin classes by "
            "type string."
        )


class TestWorkerRegistrationParity:
    """The three Worker constructions must expose the same surface.

    They previously hand-maintained duplicate workflow lists, which is how
    ``agent.cancel_delegation.v1`` ended up scheduled-but-unregistered and
    how the V2 activity spread drifted between them.
    """

    def test_framework_workflow_list_is_single_sourced(self):
        import inspect

        from services.temporal import worker as worker_module

        names = [cls.__name__ for cls in worker_module._framework_workflows()]
        assert names == sorted(set(names), key=names.index), "duplicate workflow class"
        assert "MachinaWorkflow" in names
        assert "AgentWorkflow" in names

        source = inspect.getsource(worker_module)
        # Every construction goes through the helper; none re-lists classes.
        assert source.count("workflows=_framework_workflows()") == 3
        assert "workflows=[" not in source

    def test_every_scheduled_agent_activity_is_registered(self):
        """Guards the class of bug that left cancel_delegation unregistered."""
        import inspect
        import re

        from services.temporal import agent_workflow as wf_module
        from services.temporal.agent_activities import collect_agent_activities

        registered = {
            getattr(a, "__temporal_activity_definition").name
            for a in collect_agent_activities()
        }
        scheduled = set(
            re.findall(r'"(agent\.[a-z_.]+\.v\d)"', inspect.getsource(wf_module))
        )
        missing = scheduled - registered
        assert not missing, f"scheduled but not registered on any worker: {sorted(missing)}"


class TestCompactionPauseIsNotAnAnswer:
    """A provider stop-to-compact must never surface as the agent's answer.

    A compaction stop carries no tool calls, so `kind` is "final" and the
    truncated content would be returned to the user verbatim.
    """

    def test_native_payload_requests_the_finish_reason(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        # Without this the workflow cannot distinguish the two stop kinds.
        assert '"include_finish_reason": True' in source

    def test_workflow_continues_instead_of_finalising_on_compaction(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert 'finish_reason == "compaction"' in source
        # The guard must sit BEFORE the final-answer branch, or the answer
        # is already returned by the time it runs.
        assert source.index('finish_reason == "compaction"') < source.index('if kind == "final":')

    def test_activity_only_emits_finish_reason_when_asked(self):
        """Opt-in keeps the legacy payload shapes byte-identical."""
        import inspect

        from services.temporal.agent_activities import _execute_native_llm_step

        source = inspect.getsource(_execute_native_llm_step)
        assert 'payload.get("include_finish_reason")' in source


class TestDelegatedChildrenInheritScope:
    """A subagent must resolve its own context, not silently none.

    The scope keys are injected into a ROOT node's context by
    MachinaWorkflow. Delegated children are started by the agent workflow
    itself, so without explicit inheritance they carry none of them and
    every subagent turn goes unjournalled with no error.
    """

    def test_inherited_scope_forwards_only_present_keys(self):
        from services.temporal.agent_workflow import _inherited_scope

        got = _inherited_scope(
            {"generation": 4, "graphVersion": 2, "user_id": "u1", "unrelated": "x"}
        )
        assert got == {"generation": 4, "graphVersion": 2, "user_id": "u1"}
        # Absent keys must not materialise as None — `generation: None`
        # would fail the int() coercion downstream.
        assert _inherited_scope({}) == {}

    def test_both_delegation_sites_inherit_scope(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert source.count("**_inherited_scope(context)") == 2

    def test_inherited_scope_never_overrides_explicit_child_keys(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        for block in source.split("child_context = {")[1:]:
            spread = block.index("**_inherited_scope(context)")
            node_id = block.index('"node_id"')
            assert spread < node_id, "spread must come first so explicit keys win"


class TestAgentContinueAsNew:
    """The agent loop carries a transcript and a growing tool list, so it
    must roll over before Temporal's ~51,200-event hard terminate."""

    def test_rollover_exists_and_carries_the_essentials(self):
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "workflow.continue_as_new(" in source
        # The transcript, usage totals, refs and counters cross the
        # boundary. The transcript is size-guarded (see
        # test_rollover_guards_transcript_size) so the CAN argument stays
        # under Temporal's payload error limit.
        carried = source[source.index("_RESUME_MARKER:") :][:700]
        # The conversation key is deliberately NOT carried: prepare_payload
        # recomputes it deterministically from the descriptor on every run,
        # resumed or fresh.
        for required in (
            '"transcript"',
            '"usage"',
            '"context_usage"',
            '"iteration"',
            '"execution_id"',
        ):
            assert required in carried, f"{required} must cross the rollover"

    def test_rollover_is_not_gated_on_a_context_node(self):
        """Every agent must roll over under history pressure.

        The journal-replay design gated the rollover on ``context_ref``,
        so an agent without a Context node grew until Temporal's hard
        history terminate. With the transcript carried directly there is
        no reason to require a Context node.
        """
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "if _history_pressure(_AGENT_HISTORY_SOFT_CAP):" in source
        assert "if context_ref and _history_pressure" not in source

    def test_execution_id_survives_the_rollover(self):
        """run_id changes on continue-as-new.

        execution_id falls back to run_id[:8], so a resumed run would mint a
        different id and break browser-session reuse and permit scoping.
        """
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert 'resume.get("execution_id")' in source
        assert source.index('resume.get("execution_id")') < source.index(
            'workflow.info().run_id[:8]'
        ), "the carried id must take precedence over the run_id fallback"

    def test_iteration_continues_rather_than_resetting(self):
        """iteration is baked into delegation child ids and team_task_id;
        resetting it to 0 collides with the pre-rollover run."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        assert "for iteration in range(iteration_offset, max_iterations)" in source
        assert '"iteration": iteration + 1' in source

    def test_rollover_refused_while_a_delegation_is_live(self):
        """delegation_handles holds ChildWorkflowHandle objects and the
        Task-Manager map holds asyncio.Tasks — neither is serializable."""
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        source = inspect.getsource(AgentWorkflow._run_impl)
        guard = "delegation_handles or task_manager_delegation_tasks"
        assert guard in source
        assert source.index(guard) < source.index("workflow.continue_as_new(")


class TestTranscriptPressureReplaySafety:
    """The recorded payload, not a code edit, selects the pressure rules.

    Whether ``agent.compact_context`` is scheduled depends on these rules, so
    a history recorded before ``context_pressure_version`` existed must replay
    through the original cumulative gate. Behavior is locked in
    ``test_agent_workflow_pressure.py``; these guard the selection itself.
    """

    @staticmethod
    def _source() -> str:
        import inspect

        from services.temporal.agent_workflow import AgentWorkflow

        return inspect.getsource(AgentWorkflow._run_impl)

    def test_the_rules_are_selected_by_the_recorded_payload(self):
        source = self._source()

        assert 'payload.get("context_pressure_version")' in source
        assert 'payload.get("tool_result_max_chars")' in source
        assert 'payload.get("transcript_budget_bytes")' in source
        assert source.count("if pressure_version >= 1:") == 2

    def test_the_original_cumulative_gate_is_kept_for_old_histories(self):
        source = self._source()
        section = source[source.index("# ---- Transcript pressure and compaction") :]

        assert 'context_usage_total.get("total_tokens")' in section
        assert "compact_source = messages" in section

    def test_only_external_results_outside_delegation_are_cut(self):
        source = self._source()
        start = source.index("tool_content = _serialise_tool_result(tool_result)")
        cap = source[start : start + 600]

        assert "not is_delegation" in cap
        assert 'tool_output_is_capped(tool_info["node_type"])' in cap
        assert "bound_tool_output(" in cap
