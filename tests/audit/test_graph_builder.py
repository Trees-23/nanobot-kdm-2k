from datetime import UTC, datetime, timedelta

from nanobot.audit.graph import AuditGraphBuilder
from nanobot.audit.schema import audit_event_adapter


def _event(sequence: int, event_type: str, **extra):
    raw = {
        "schema_version": 1,
        "event_id": f"e{sequence}",
        "event_type": event_type,
        "occurred_at": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=sequence),
        "monotonic_ns": sequence,
        "trace_id": "trace-1",
        "turn_id": "turn-1",
        "run_id": "run-1",
        "parent_run_id": None,
        "resumed_from_run_id": None,
        "caused_by_event_id": None,
        "model_call_id": None,
        "attempt_id": None,
        "tool_call_id": None,
        "checkpoint_id": None,
        "goal_id": None,
        "delivery_id": None,
        "session_key": "websocket:chat-1",
        "source_type": "websocket",
        "source_metadata": {},
        "iteration": 1,
        "process_instance_id": "process-1",
        "segment_id": "segment-1",
        "segment_sequence": sequence,
        "durability_epoch": sequence,
        "previous_event_hash": None,
        "payload_id": None,
        "payload_sha256": None,
        "event_hash": f"hash-{sequence}",
        **extra,
    }
    return audit_event_adapter.validate_python(raw)


def _task_event(
    sequence: int,
    event_type: str,
    *,
    revision: int,
    task_id: str = "task-a",
    run_id: str = "child-a",
    tool_call_id: str = "spawn-a",
    **extra,
):
    source_metadata = extra.pop("source_metadata", {})
    fields = {
        "task_status": "running",
        "task_phase": "running_model",
        "termination_state": "none",
        "delivery_phase": "not_ready",
        "required_task": True,
        "legacy_inferred": False,
        **extra,
    }
    return _event(
        sequence,
        event_type,
        run_id=run_id,
        parent_run_id="run-1",
        tool_call_id=tool_call_id,
        iteration=None,
        source_type="subagent_task",
        source_metadata=source_metadata,
        subagent_task_id=task_id,
        task_revision=revision,
        idempotency_key=f"{task_id}:{revision}:{event_type}",
        **fields,
    )


def _retry_trace():
    return [
        _event(1, "run_started"),
        _event(
            2,
            "model_request_started",
            model_call_id="model-1",
            requested_provider="openai",
            requested_model="gpt-test",
        ),
        _event(
            3,
            "model_attempt_started",
            model_call_id="model-1",
            attempt_id="attempt-1",
            attempt_ordinal=1,
            provider="openai",
            model="gpt-test",
            input_variant="primary",
        ),
        _event(
            4,
            "model_attempt_finished",
            model_call_id="model-1",
            attempt_id="attempt-1",
            attempt_ordinal=1,
            provider="openai",
            model="gpt-test",
            elapsed_ms=900,
            status="timeout",
        ),
        _event(
            5,
            "retry_scheduled",
            model_call_id="model-1",
            prior_attempt_id="attempt-1",
            delay_ms=50,
            policy_name="bounded",
        ),
        _event(
            6,
            "model_attempt_started",
            model_call_id="model-1",
            attempt_id="attempt-2",
            attempt_ordinal=2,
            provider="openai",
            model="gpt-test",
            input_variant="retry",
        ),
        _event(
            7,
            "model_attempt_finished",
            model_call_id="model-1",
            attempt_id="attempt-2",
            attempt_ordinal=2,
            provider="openai",
            model="gpt-test",
            elapsed_ms=300,
            status="ok",
        ),
        _event(
            8,
            "model_response_received",
            model_call_id="model-1",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
        _event(9, "run_finished", status="succeeded", stop_reason="final_answer"),
    ]


def _unified_branch_trace():
    events = [
        _event(1, "run_started"),
        _event(
            2,
            "tool_started",
            tool_call_id="spawn-a",
            tool_name="spawn",
        ),
        _event(
            3,
            "tool_finished",
            tool_call_id="spawn-a",
            tool_name="spawn",
            elapsed_ms=1,
            status="ok",
        ),
        _event(
            4,
            "run_started",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
        ),
        _event(
            5,
            "tool_started",
            tool_call_id="spawn-rejected",
            tool_name="spawn",
        ),
        _event(
            6,
            "tool_finished",
            tool_call_id="spawn-rejected",
            tool_name="spawn",
            elapsed_ms=1,
            status="ok",
        ),
        _event(
            7,
            "model_request_started",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
            model_call_id="child-a-model",
            requested_provider="openai",
            requested_model="gpt-test",
        ),
        _event(
            8,
            "model_request_failed",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
            model_call_id="child-a-model",
            status="error",
            error_kind="provider",
            attempt_count=1,
        ),
        _event(
            9,
            "run_finished",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
            status="failed",
            stop_reason="model_error",
        ),
        _event(
            10,
            "input_injected",
            injection_source="subagent_result",
            target_run_id="run-1",
        ),
        _event(
            11,
            "tool_started",
            tool_call_id="spawn-b",
            tool_name="spawn",
        ),
        _event(
            12,
            "tool_finished",
            tool_call_id="spawn-b",
            tool_name="spawn",
            elapsed_ms=1,
            status="ok",
        ),
        _event(
            13,
            "run_started",
            run_id="child-b",
            parent_run_id="run-1",
            source_type="subagent",
        ),
        _event(14, "run_finished", status="succeeded", stop_reason="final_answer"),
        _event(
            15,
            "model_request_started",
            run_id="child-b",
            parent_run_id="run-1",
            source_type="subagent",
            model_call_id="child-b-model",
            requested_provider="openai",
            requested_model="gpt-test",
        ),
        _event(
            16,
            "model_response_received",
            run_id="child-b",
            parent_run_id="run-1",
            source_type="subagent",
            model_call_id="child-b-model",
            finish_reason="stop",
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        ),
        _event(
            17,
            "run_finished",
            run_id="child-b",
            parent_run_id="run-1",
            source_type="subagent",
            status="succeeded",
            stop_reason="final_answer",
        ),
        _event(
            18,
            "trace_linked",
            run_id=None,
            parent_run_id=None,
            source_type="subagent",
            actor_type="system",
            link_reason="active_run_injection",
            linked_source_id="child-b",
        ),
        _event(
            19,
            "run_started",
            run_id="continuation-b",
            parent_run_id="child-b",
            source_type="subagent",
        ),
        _event(
            20,
            "run_finished",
            run_id="continuation-b",
            parent_run_id="child-b",
            source_type="subagent",
            status="succeeded",
            stop_reason="final_answer",
        ),
    ]
    return events


def test_run_graph_is_deterministic_and_declares_attempt_expansion() -> None:
    builder = AuditGraphBuilder()
    first = builder.build(trace_id="trace-1", level="run", run_id="run-1", events=_retry_trace())
    second = builder.build(trace_id="trace-1", level="run", run_id="run-1", events=_retry_trace())

    assert first.model_dump_json() == second.model_dump_json()
    assert len(first.expansion_groups) == 1
    assert first.expansion_groups[0].default_expanded is True
    assert first.first_anomaly is not None
    assert first.first_anomaly.event_id == "e4"
    assert any(edge.type == "retry_of" for edge in first.edges)


def test_trace_full_collapses_single_successful_attempt_by_default() -> None:
    events = [
        _event(1, "run_started"),
        _event(
            2,
            "model_request_started",
            model_call_id="model-1",
            requested_provider="openai",
            requested_model="gpt-test",
        ),
        _event(
            3,
            "model_attempt_started",
            model_call_id="model-1",
            attempt_id="attempt-1",
            provider="openai",
            model="gpt-test",
            attempt_ordinal=1,
            input_variant="original",
        ),
        _event(
            4,
            "model_attempt_finished",
            model_call_id="model-1",
            attempt_id="attempt-1",
            attempt_ordinal=1,
            provider="openai",
            model="gpt-test",
            elapsed_ms=10,
            status="ok",
        ),
        _event(
            5,
            "model_response_received",
            model_call_id="model-1",
            finish_reason="stop",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        ),
        _event(6, "run_finished", status="succeeded", stop_reason="done"),
    ]

    graph = AuditGraphBuilder().build(trace_id="trace-1", level="trace_full", events=events)

    assert len(graph.expansion_groups) == 1
    assert graph.expansion_groups[0].default_expanded is False


def test_trace_full_uses_spawn_evidence_and_separates_continuation() -> None:
    events = _unified_branch_trace()
    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="trace_full", events=events
    )
    run_nodes = {node.run_id: node for node in graph.nodes if node.type == "run"}

    assert run_nodes["run-1"].run_kind == "main"
    assert run_nodes["child-a"].run_kind == "child_agent"
    assert run_nodes["child-b"].run_kind == "child_agent"
    assert run_nodes["continuation-b"].run_kind == "continuation"
    assert run_nodes["child-a"].spawn_tool_call_id == "spawn-a"
    assert run_nodes["child-b"].spawn_tool_call_id == "spawn-b"
    assert run_nodes["continuation-b"].continuation_of_run_id == "child-b"
    assert run_nodes["continuation-b"].spawn_tool_call_id is None
    assert [node.lane_order for node in run_nodes.values()].count(-1) == 1
    assert [node.lane_order for node in run_nodes.values()].count(1) == 2
    assert sum(edge.type == "spawn_branch" for edge in graph.edges) == 2
    assert sum(edge.type == "result_return" for edge in graph.edges) == 2
    assert all(edge.source in {node.id for node in graph.nodes} for edge in graph.edges)
    assert all(edge.target in {node.id for node in graph.nodes} for edge in graph.edges)
    assert len(graph.event_owners) + len(graph.ignored_event_ids) == len(events)


def test_trace_full_keeps_parented_run_unknown_without_spawn_evidence() -> None:
    graph = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="trace_full",
        events=[
            _event(1, "run_started"),
            _event(2, "run_finished", status="succeeded", stop_reason="done"),
            _event(3, "run_started", run_id="unproven", parent_run_id="run-1"),
            _event(
                4,
                "run_finished",
                run_id="unproven",
                parent_run_id="run-1",
                status="succeeded",
                stop_reason="done",
            ),
        ],
    )

    unproven = next(node for node in graph.nodes if node.run_id == "unproven")
    assert unproven.run_kind == "unknown"
    assert not any(edge.type == "spawn_branch" for edge in graph.edges)


def test_trace_full_prefers_exact_spawn_task_child_metadata() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "tool_started", tool_call_id="spawn-a", tool_name="spawn"),
        _event(3, "tool_finished", tool_call_id="spawn-a", tool_name="spawn", elapsed_ms=1, status="ok"),
        _event(4, "tool_started", tool_call_id="spawn-b", tool_name="spawn"),
        _event(5, "tool_finished", tool_call_id="spawn-b", tool_name="spawn", elapsed_ms=1, status="ok"),
        _event(
            6,
            "run_started",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
            source_metadata={"spawn_tool_call_id": "spawn-a", "subagent_task_id": "task-a"},
        ),
        _event(7, "run_finished", run_id="child-a", parent_run_id="run-1", source_type="subagent", status="succeeded", stop_reason="done"),
        _event(8, "run_finished", status="succeeded", stop_reason="done"),
        _event(
            9,
            "run_started",
            run_id="continuation-a",
            source_type="continuation",
            source_metadata={"continuation_of_run_id": "child-a", "injection_source": "subagent_result"},
        ),
        _event(10, "run_finished", run_id="continuation-a", source_type="continuation", status="succeeded", stop_reason="done"),
    ]
    graph = AuditGraphBuilder().build(trace_id="trace-1", level="trace_full", events=events)
    runs = {node.run_id: node for node in graph.nodes if node.type == "run"}

    assert runs["child-a"].run_kind == "child_agent"
    assert runs["child-a"].spawn_tool_call_id == "spawn-a"
    assert runs["continuation-a"].run_kind == "continuation"
    assert runs["continuation-a"].parent_node_id is None
    assert runs["continuation-a"].continuation_of_run_id == "child-a"


def test_trace_full_projects_recorded_task_between_spawn_child_and_continuation() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "tool_started", tool_call_id="spawn-a", tool_name="spawn"),
        _event(
            3,
            "tool_finished",
            tool_call_id="spawn-a",
            tool_name="spawn",
            elapsed_ms=1,
            status="ok",
        ),
        _task_event(
            4,
            "subagent_created",
            revision=1,
            task_status="created",
            task_label="检查一级目录",
        ),
        _event(
            5,
            "run_started",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
            source_metadata={"spawn_tool_call_id": "spawn-a", "subagent_task_id": "task-a"},
        ),
        _task_event(6, "subagent_admitted", revision=2),
        _event(
            7,
            "run_finished",
            run_id="child-a",
            parent_run_id="run-1",
            source_type="subagent",
            status="succeeded",
            stop_reason="done",
        ),
        _task_event(
            8,
            "subagent_terminal",
            revision=3,
            task_status="succeeded",
            task_phase="finished",
            delivery_phase="ready",
        ),
        _task_event(
            9,
            "subagent_result_delivered",
            revision=4,
            task_status="succeeded",
            task_phase="finished",
            delivery_phase="delivered",
        ),
        _event(
            10,
            "input_injected",
            injection_source="subagent_result",
            target_run_id="run-1",
            subagent_task_id="task-a",
        ),
        _event(11, "run_finished", status="succeeded", stop_reason="done"),
        _event(
            12,
            "run_started",
            run_id="continuation-a",
            source_type="continuation",
            source_metadata={
                "continuation_of_run_id": "child-a",
                "injection_source": "subagent_result",
            },
        ),
        _event(
            13,
            "run_finished",
            run_id="continuation-a",
            source_type="continuation",
            status="succeeded",
            stop_reason="done",
        ),
    ]

    graph = AuditGraphBuilder().build(trace_id="trace-1", level="trace_full", events=events)
    task = next(node for node in graph.nodes if node.type == "task")
    child = next(node for node in graph.nodes if node.run_id == "child-a" and node.type == "run")
    continuation = next(
        node for node in graph.nodes if node.run_id == "continuation-a" and node.type == "run"
    )
    spawn = next(node for node in graph.nodes if node.type == "tool_call")

    assert task.task_id == "task-a"
    assert task.label == "检查一级目录"
    assert task.summary.task_label == "检查一级目录"
    assert task.region_id == child.region_id
    assert task.id in next(region for region in graph.regions if region.id == child.region_id).member_node_ids
    assert not any(region.type == "task" and region.task_id == "task-a" for region in graph.regions)
    assert task.status == "succeeded"
    assert task.summary.delivery_phase == "delivered"
    assert task.summary.evidence_source == "recorded"
    assert {event.event_type for event in task.raw_events} == {
        "subagent_created",
        "subagent_admitted",
        "subagent_terminal",
        "subagent_result_delivered",
    }
    assert any(
        edge.type == "spawn_branch" and edge.source == spawn.id and edge.target == task.id
        for edge in graph.edges
    )
    assert any(
        edge.type == "task_execution" and edge.source == task.id and edge.target == child.id
        for edge in graph.edges
    )
    assert any(
        edge.type == "result_return"
        and edge.source == task.id
        and edge.target == continuation.id
        and edge.evidence_kind == "recorded_delivery_event"
        for edge in graph.edges
    )
    assert any(
        edge.type == "result_return"
        and edge.source == task.id
        and edge.evidence_kind == "recorded_injection_task_id"
        for edge in graph.edges
    )
    assert not any(
        edge.type == "spawn_branch" and edge.source == spawn.id and edge.target == child.id
        for edge in graph.edges
    )
    assert not any(
        node.type == "decision" and node.summary.decision_type.startswith("subagent_")
        for node in graph.nodes
    )


def test_trace_full_uses_task_id_fallback_for_legacy_lifecycle_events() -> None:
    graph = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="trace_full",
        events=[_task_event(1, "subagent_created", revision=1, task_id="legacy-task-id")],
    )

    task = next(node for node in graph.nodes if node.type == "task")
    assert task.label == "Task legacy-task-"
    assert task.summary.task_label is None


def test_trace_full_deduplicates_retried_lifecycle_outbox_events() -> None:
    first = _task_event(2, "subagent_created", revision=1, task_status="created")
    duplicate = _task_event(3, "subagent_created", revision=1, task_status="created")
    graph = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="trace_full",
        events=[
            _event(1, "run_started"),
            first,
            duplicate,
            _event(4, "run_finished", status="succeeded", stop_reason="done"),
        ],
    )

    task = next(node for node in graph.nodes if node.type == "task")
    assert graph.trace.event_count == 4
    assert task.summary.lifecycle_event_count == 1
    assert task.raw_event_ids == [first.event_id]
    assert duplicate.event_id in graph.ignored_event_ids


def test_trace_full_connects_recorded_task_replacement_and_recovery() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "tool_started", tool_call_id="spawn-old", tool_name="spawn"),
        _event(3, "tool_finished", tool_call_id="spawn-old", tool_name="spawn", elapsed_ms=1, status="ok"),
        _task_event(
            4,
            "subagent_created",
            revision=1,
            task_id="task-old",
            run_id="child-old",
            tool_call_id="spawn-old",
        ),
        _event(
            5,
            "run_started",
            run_id="child-old",
            parent_run_id="run-1",
            source_type="subagent",
            source_metadata={"spawn_tool_call_id": "spawn-old", "subagent_task_id": "task-old"},
        ),
        _task_event(
            6,
            "subagent_recovered",
            revision=2,
            task_id="task-old",
            run_id="child-old",
            tool_call_id="spawn-old",
        ),
        _event(7, "run_finished", run_id="child-old", parent_run_id="run-1", source_type="subagent", status="failed", stop_reason="lost"),
        _event(8, "tool_started", tool_call_id="spawn-new", tool_name="spawn"),
        _event(9, "tool_finished", tool_call_id="spawn-new", tool_name="spawn", elapsed_ms=1, status="ok"),
        _task_event(
            10,
            "subagent_created",
            revision=1,
            task_id="task-new",
            run_id="child-new",
            tool_call_id="spawn-new",
            source_metadata={"replaces_task_id": "task-old"},
        ),
        _event(
            11,
            "run_started",
            run_id="child-new",
            parent_run_id="run-1",
            source_type="subagent",
            source_metadata={"spawn_tool_call_id": "spawn-new", "subagent_task_id": "task-new"},
        ),
        _event(12, "run_finished", run_id="child-new", parent_run_id="run-1", source_type="subagent", status="succeeded", stop_reason="done"),
        _event(13, "run_finished", status="succeeded", stop_reason="done"),
    ]

    graph = AuditGraphBuilder().build(trace_id="trace-1", level="trace_full", events=events)
    tasks = {node.task_id: node for node in graph.nodes if node.type == "task"}
    old_child = next(node for node in graph.nodes if node.run_id == "child-old" and node.type == "run")

    assert any(
        edge.type == "task_recovery"
        and edge.source == tasks["task-old"].id
        and edge.target == old_child.id
        for edge in graph.edges
    )
    assert any(
        edge.type == "task_replacement"
        and edge.source == tasks["task-old"].id
        and edge.target == tasks["task-new"].id
        and edge.evidence_kind == "recorded_replacement_id"
        for edge in graph.edges
    )


def test_trace_full_exposes_terminal_and_process_health_separately() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "tool_started", tool_call_id="tool-1", tool_name="exec"),
        _event(
            3,
            "tool_finished",
            tool_call_id="tool-1",
            tool_name="exec",
            elapsed_ms=5,
            status="error",
        ),
        _event(4, "run_finished", status="succeeded", stop_reason="final_answer"),
    ]
    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="trace_full", events=events
    )
    run = next(node for node in graph.nodes if node.type == "run")

    assert run.terminal_status == "succeeded"
    assert run.health_status == "warning"
    assert run.anomaly_count == 1


def test_explicit_recovery_distinguishes_three_config_paths() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "tool_started", tool_call_id="absolute-runtime", tool_name="read_file"),
        _event(
            3,
            "tool_finished",
            tool_call_id="absolute-runtime",
            tool_name="read_file",
            elapsed_ms=1,
            status="error",
            error_type="FileNotFoundError",
            error_code="file_not_found",
            error_summary="File not found (path=<outside-workspace>)",
            safe_input_summary="path=<outside-workspace>",
            resource_key="sha256:absolute-runtime",
            resource_correction_keys=["sha256:absolute-config"],
            recovery_of_tool_call_ids=[],
        ),
        _event(4, "tool_started", tool_call_id="relative", tool_name="read_file"),
        _event(
            5,
            "tool_finished",
            tool_call_id="relative",
            tool_name="read_file",
            elapsed_ms=1,
            status="error",
            error_type="FileNotFoundError",
            error_code="file_not_found",
            error_summary="File not found (path=config.json)",
            safe_input_summary="path=config.json",
            resource_key="sha256:workspace-config",
            resource_correction_keys=[],
            recovery_of_tool_call_ids=[],
        ),
        _event(6, "tool_started", tool_call_id="absolute-config", tool_name="read_file"),
        _event(
            7,
            "tool_finished",
            tool_call_id="absolute-config",
            tool_name="read_file",
            elapsed_ms=1,
            status="ok",
            safe_input_summary="path=<outside-workspace>",
            resource_key="sha256:absolute-config",
            resource_correction_keys=["sha256:absolute-runtime"],
            recovery_of_tool_call_ids=["absolute-runtime"],
        ),
        _event(8, "run_finished", status="succeeded", stop_reason="completed"),
    ]
    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="trace_full", events=events
    )
    tools = {
        node.summary.identifier: node
        for node in graph.nodes
        if node.type == "tool_call"
    }

    recovered = tools["absolute-runtime"].summary
    unresolved = tools["relative"].summary
    assert recovered.recovery_status == "recovered"
    assert recovered.recovered_by_event_id == "e7"
    assert recovered.impact == "run_continued"
    assert unresolved.recovery_status == "continued"
    assert unresolved.recovered_by_event_id is None
    assert unresolved.impact == "run_continued"
    recovery_edges = [edge for edge in graph.edges if edge.type == "tool_recovery"]
    assert len(recovery_edges) == 1
    assert recovery_edges[0].anchor is not None
    assert recovery_edges[0].anchor.source_event_id == "e3"
    assert recovery_edges[0].anchor.target_event_id == "e7"
    assert recovery_edges[0].source == tools["absolute-runtime"].id
    assert recovery_edges[0].target == tools["absolute-config"].id
    assert recovery_edges[0].evidence_count == 1


def test_explicit_tool_retry_continuation_and_recovery_are_distinct() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "tool_started", tool_call_id="failed", tool_name="exec"),
        _event(
            3,
            "tool_finished",
            tool_call_id="failed",
            tool_name="exec",
            elapsed_ms=1,
            status="timeout",
            error_message="Error: command timed out",
            error_summary="command timed out",
            error_type="TimeoutError",
            error_code="tool_timeout",
            error_source="timeout",
            retryability="retryable",
        ),
        _event(4, "tool_started", tool_call_id="retry", tool_name="exec"),
        _event(
            5,
            "tool_finished",
            tool_call_id="retry",
            tool_name="exec",
            elapsed_ms=1,
            status="error",
            retry_of_tool_call_ids=["failed", "dangling"],
        ),
        _event(6, "tool_started", tool_call_id="continue", tool_name="write_stdin"),
        _event(
            7,
            "tool_finished",
            tool_call_id="continue",
            tool_name="write_stdin",
            elapsed_ms=1,
            status="ok",
            continuation_of_tool_call_ids=["failed"],
        ),
        _event(8, "tool_started", tool_call_id="recovered", tool_name="exec"),
        _event(
            9,
            "tool_finished",
            tool_call_id="recovered",
            tool_name="exec",
            elapsed_ms=1,
            status="ok",
            recovery_of_tool_call_ids=["failed"],
            recovery_evidence_kind="process_exit_zero",
        ),
        _event(10, "run_finished", status="succeeded", stop_reason="completed"),
    ]

    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="trace_full", events=events
    )
    relations = {edge.type: edge for edge in graph.edges if edge.type.startswith("tool_")}

    assert set(relations) == {"tool_retry", "tool_continuation", "tool_recovery"}
    assert relations["tool_retry"].anchor.source_event_id == "e3"
    assert relations["tool_continuation"].anchor.target_event_id == "e7"
    assert relations["tool_recovery"].anchor.target_event_id == "e9"
    assert relations["tool_recovery"].evidence_kind == "process_exit_zero"
    failed = next(
        node for node in graph.nodes
        if node.type == "tool_call" and node.summary.identifier == "failed"
    )
    assert failed.summary.error_message == "Error: command timed out"
    assert failed.summary.error_source == "timeout"
    assert failed.summary.retryability == "retryable"
    assert failed.summary.recovery_status == "recovered"
    assert failed.summary.recovery_evidence_kind == "process_exit_zero"


def test_fatal_tool_timeout_drives_run_diagnostics_without_payload_content() -> None:
    events = [
        _event(1, "run_started", run_id="child"),
        _event(
            2,
            "tool_started",
            run_id="child",
            tool_call_id="search",
            tool_name="web_search",
        ),
        _event(
            3,
            "tool_finished",
            run_id="child",
            tool_call_id="search",
            tool_name="web_search",
            elapsed_ms=30_000,
            status="timeout",
            error_type="TimeoutError",
            error_code="web_search_timeout",
            error_summary="DuckDuckGo search timed out after 30s",
            effective_timeout_ms=30_000,
            provider="duckduckgo",
            safe_input_summary="query omitted; provider=duckduckgo",
            recovery_of_tool_call_ids=[],
        ),
        _event(
            4,
            "run_finished",
            run_id="child",
            status="failed",
            stop_reason="tool_error",
            fatal_event_id="e3",
            failure_policy="fail_on_tool_error",
            fail_on_tool_error=True,
        ),
    ]
    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="trace_full", events=events
    )
    tool = next(node for node in graph.nodes if node.type == "tool_call")
    run = next(node for node in graph.nodes if node.type == "run")

    assert tool.status == "failed"
    assert tool.summary.failure_kind == "tool_error"
    assert tool.summary.impact == "run_failed"
    assert tool.summary.recovery_status == "unrecovered"
    assert tool.summary.error_summary == "DuckDuckGo search timed out after 30s"
    assert run.summary.failure_kind == "tool_error"
    assert run.summary.fatal_event_id == "e3"
    assert run.summary.failure_policy == "fail_on_tool_error"
    assert run.summary.fail_on_tool_error is True
    assert "secret query" not in graph.model_dump_json().lower()


def test_region_membership_and_event_ownership_are_bidirectional() -> None:
    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="run", run_id="run-1", events=_retry_trace()
    )
    region_members = {member for region in graph.regions for member in region.member_node_ids}

    assert region_members == {node.id for node in graph.nodes}
    assert len(graph.event_owners) + len(graph.ignored_event_ids) == len(_retry_trace())
    assert set(graph.event_owners).isdisjoint(graph.ignored_event_ids)


def test_trace_graph_uses_parent_and_resume_edges_without_time_inference() -> None:
    events = [
        _event(1, "run_started"),
        _event(2, "run_finished", status="interrupted", stop_reason="cancel"),
        _event(
            3,
            "run_started",
            run_id="run-2",
            resumed_from_run_id="run-1",
            parent_run_id="run-1",
        ),
        _event(
            4,
            "run_finished",
            run_id="run-2",
            resumed_from_run_id="run-1",
            parent_run_id="run-1",
            status="succeeded",
            stop_reason="final_answer",
        ),
    ]
    graph = AuditGraphBuilder().build(trace_id="trace-1", level="trace", events=events)

    assert {edge.type for edge in graph.edges} == {"parent_run", "resumed_from"}
    assert all(edge.source.endswith("run-1") for edge in graph.edges)


def test_duplicate_lifecycle_start_is_the_first_anomaly() -> None:
    graph = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="trace",
        events=[
            _event(1, "run_started"),
            _event(2, "run_started"),
            _event(3, "run_finished", status="failed", stop_reason="model_error"),
        ],
    )

    assert graph.first_anomaly is not None
    assert graph.first_anomaly.event_id == "e2"
    assert graph.first_anomaly.category == "lifecycle_mismatch"
    assert graph.nodes[0].summary.subtype == "lifecycle_mismatch"


def test_checkpoint_transitions_merge_without_downgrading_successful_run() -> None:
    events = [
        _event(1, "run_started"),
        _event(
            2,
            "checkpoint_written",
            checkpoint_id="cp-1",
            checkpoint_version=1,
            checkpoint_phase="final_response",
            iteration=None,
        ),
        _event(
            3,
            "checkpoint_restored",
            checkpoint_id="cp-1",
            checkpoint_version=1,
            source_run_id="run-1",
            iteration=None,
        ),
        _event(
            4,
            "checkpoint_cleared",
            checkpoint_id="cp-1",
            clear_reason="turn_completed",
            iteration=None,
        ),
        _event(5, "run_finished", status="succeeded", stop_reason="final_answer"),
    ]
    graph = AuditGraphBuilder().build(
        trace_id="trace-1", level="run", run_id="run-1", events=events
    )

    checkpoints = [node for node in graph.nodes if node.type == "checkpoint"]
    assert len(checkpoints) == 1
    assert checkpoints[0].status == "succeeded"
    assert checkpoints[0].raw_event_ids == ["e2", "e3", "e4"]
    assert checkpoints[0].summary.checkpoint_restored is True
    assert checkpoints[0].summary.checkpoint_cleared is True
    assert graph.trace.display_status == "succeeded"
    assert graph.trace.event_count == 5


def test_delivery_suppression_supports_legacy_webui_evidence_without_hiding_unknowns() -> None:
    start = _event(1, "run_started")
    finish = _event(4, "run_finished", status="succeeded", stop_reason="final_answer")
    expected = _event(
        2,
        "delivery_finished",
        delivery_id="d1",
        final_attempt_ordinal=0,
        status="suppressed",
        suppression_reason="webui_stream_already_delivered",
        iteration=None,
    )
    legacy_webui = _event(
        3,
        "delivery_finished",
        delivery_id="d2",
        final_attempt_ordinal=0,
        status="suppressed",
        iteration=None,
    )
    non_webui_start = _event(1, "run_started", source_type="slack", session_key="slack:chat-1")
    non_webui_finish = _event(
        4,
        "run_finished",
        status="succeeded",
        stop_reason="final_answer",
        source_type="slack",
        session_key="slack:chat-1",
    )
    unknown = _event(
        3,
        "delivery_finished",
        delivery_id="d3",
        final_attempt_ordinal=0,
        status="suppressed",
        iteration=None,
        source_type="delivery",
        session_key="slack:chat-1",
    )

    successful = AuditGraphBuilder().build(
        trace_id="trace-1", level="run", run_id="run-1", events=[start, expected, finish]
    )
    legacy = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="run",
        run_id="run-1",
        events=[start, legacy_webui, finish],
    )
    warning = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="run",
        run_id="run-1",
        events=[non_webui_start, unknown, non_webui_finish],
    )
    assert successful.trace.display_status == "succeeded"
    assert next(node for node in successful.nodes if node.type == "delivery").status == "succeeded"
    assert legacy.trace.display_status == "succeeded"
    assert next(node for node in legacy.nodes if node.type == "delivery").status == "succeeded"
    assert warning.trace.display_status == "warning"
    assert next(node for node in warning.nodes if node.type == "delivery").status == "warning"


def test_trace_title_prefers_inbound_source_over_delivery() -> None:
    graph = AuditGraphBuilder().build(
        trace_id="trace-1",
        level="trace",
        events=[
            _event(1, "run_started", source_type="delivery"),
            _event(2, "turn_started", source_type="websocket"),
            _event(3, "run_finished", status="succeeded", stop_reason="final_answer"),
        ],
    )

    assert graph.trace.title.startswith("websocket / ")
