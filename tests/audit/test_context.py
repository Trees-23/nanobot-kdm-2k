from nanobot.audit.context import (
    AuditRunContext,
    TraceContextResolver,
    TraceTurnInput,
)


def test_ordinary_message_creates_new_trace() -> None:
    resolver = TraceContextResolver()
    first = resolver.resolve_turn(TraceTurnInput(session_key="s1"))
    second = resolver.resolve_turn(TraceTurnInput(session_key="s1"))
    assert first.trace_id != second.trace_id
    assert first.link_reason == "created"


def test_goal_and_checkpoint_inherit_trace() -> None:
    resolver = TraceContextResolver()
    goal = resolver.resolve_turn(
        TraceTurnInput(session_key="s1", active_goal_trace_id="t1")
    )
    restored = resolver.resolve_turn(
        TraceTurnInput(session_key="s1", checkpoint_trace_id="t2")
    )
    assert goal.trace_id == "t1"
    assert goal.link_reason == "active_goal"
    assert restored.trace_id == "t2"
    assert restored.link_reason == "checkpoint_restored"


def test_explicit_link_does_not_depend_on_session() -> None:
    context = TraceContextResolver().resolve_turn(
        TraceTurnInput(
            session_key="new-session",
            explicit_trace_id="trace-existing",
            explicit_link_source_id="operator-request-1",
        )
    )
    assert context.trace_id == "trace-existing"
    assert context.linked_source_id == "operator-request-1"


def test_stop_reuses_only_one_shared_target_trace() -> None:
    resolver = TraceContextResolver()
    shared = resolver.resolve_turn(
        TraceTurnInput(session_key="s1", stop_target_trace_ids=("t1", "t1"))
    )
    split = resolver.resolve_turn(
        TraceTurnInput(session_key="s1", stop_target_trace_ids=("t1", "t2"))
    )
    assert shared.trace_id == "t1"
    assert shared.link_reason == "stop_shared_trace"
    assert split.trace_id not in {"t1", "t2"}
    assert split.link_reason == "control_trace_created"


def test_child_run_shares_trace_and_turn() -> None:
    parent = AuditRunContext(trace_id="t", turn_id="u", run_id="r1")
    child = parent.child_run(source_type="subagent")
    assert child.trace_id == "t"
    assert child.turn_id == "u"
    assert child.parent_run_id == "r1"
    assert child.run_id != "r1"


def test_resumed_run_gets_new_identity() -> None:
    turn = TraceContextResolver().resolve_turn(
        TraceTurnInput(
            session_key="s1",
            checkpoint_trace_id="t1",
            checkpoint_run_id="old-run",
        )
    )
    run = turn.new_run(resumed_from_run_id="old-run")
    assert run.trace_id == "t1"
    assert run.run_id != "old-run"
    assert run.resumed_from_run_id == "old-run"
