# Agent Audit V1 Runtime Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Phase 1 evidence core into every Agent execution, Provider route, tool lifecycle, checkpoint, Goal, SDK return, and channel delivery boundary.

**Architecture:** AgentLoop resolves deterministic Trace/Turn context and owns prepared responses; AgentRunner automatically composes an internal AuditHook; observed leaf Provider calls emit actual attempts; explicit boundary emitters cover `/stop`, checkpoints, Goals, SDK returns, and ChannelManager delivery.

**Tech Stack:** Existing nanobot asyncio runtime, AgentHook, AgentRunner, Provider interfaces, MessageBus, ChannelManager, Phase 1 `nanobot.audit` package, pytest/pytest-asyncio.

---

## File Structure

| File | Responsibility |
|---|---|
| `nanobot/audit/context.py` | Deterministic Trace/Turn/Run inheritance and child links |
| `nanobot/audit/runtime.py` | Writer/emitter startup, lease heartbeat, and durable shutdown |
| `nanobot/audit/hook.py` | Internal Runner hook and typed event-draft factories |
| `nanobot/audit/side_effects.py` | Capability-specific verified side-effect snapshots |
| `nanobot/providers/observed_call.py` | Exactly-once observation around real Provider API calls |
| `nanobot/agent/hook.py` | Shared typed model, decision, and guaranteed tool-terminal hooks |
| `nanobot/agent/runner.py` | Run, iteration, logical model, tool, and policy boundaries |
| `nanobot/agent/loop.py` | Turn identity, prepared response, checkpoint, and Goal ownership |
| `nanobot/providers/base.py` | Retry and image-strip attempt boundaries without raw error logs |
| `nanobot/providers/fallback_provider.py` | Fallback/circuit route decisions and leaf attempt delegation |
| `nanobot/channels/manager.py` | Actual channel delivery attempts and terminal adapter outcome |
| `nanobot/nanobot.py` | SDK returned-to-caller evidence and explicit shutdown |

Command, subagent, automation, and long-task modules emit only facts they author. They depend on
the shared runtime/emitter and do not open evidence files directly.

### Task 1: Trace, Turn, and Run context resolver

**Files:**
- Create: `nanobot/audit/context.py`
- Test: `tests/audit/test_context.py`

- [ ] **Step 1: Write deterministic inheritance tests**

```python
def test_ordinary_message_creates_new_trace() -> None:
    resolver = TraceContextResolver()
    first = resolver.resolve_turn(turn_input(session_key="s1"))
    second = resolver.resolve_turn(turn_input(session_key="s1"))
    assert first.trace_id != second.trace_id


def test_goal_and_checkpoint_inherit_trace() -> None:
    resolver = TraceContextResolver()
    goal = resolver.resolve_turn(turn_input(session_key="s1", active_goal_trace_id="t1"))
    restored = resolver.resolve_turn(turn_input(session_key="s1", checkpoint_trace_id="t2"))
    assert goal.trace_id == "t1"
    assert restored.trace_id == "t2"


def test_child_run_shares_trace_and_turn() -> None:
    parent = AuditRunContext(trace_id="t", turn_id="u", run_id="r1")
    child = parent.child_run(source_type="subagent")
    assert child.trace_id == "t"
    assert child.turn_id == "u"
    assert child.parent_run_id == "r1"
    assert child.run_id != "r1"
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_context.py -v`

Expected: import failure.

- [ ] **Step 3: Implement immutable context DTOs**

```python
@dataclass(frozen=True, slots=True)
class AuditTurnContext:
    trace_id: str
    turn_id: str
    session_key: str
    source_type: str
    link_reason: str

    def new_run(self, *, source_type: str = "agent") -> AuditRunContext:
        return AuditRunContext(
            trace_id=self.trace_id,
            turn_id=self.turn_id,
            run_id=new_audit_id(),
            parent_run_id=None,
            resumed_from_run_id=None,
            source_type=source_type,
        )


@dataclass(frozen=True, slots=True)
class AuditRunContext:
    trace_id: str
    turn_id: str
    run_id: str
    parent_run_id: str | None = None
    resumed_from_run_id: str | None = None
    source_type: str = "agent"

    def child_run(self, *, source_type: str) -> AuditRunContext:
        return replace(
            self,
            run_id=new_audit_id(),
            parent_run_id=self.run_id,
            resumed_from_run_id=None,
            source_type=source_type,
        )
```

`TraceContextResolver.resolve_turn()` applies only the deterministic rules approved in Design
section 5.2 and returns the selected `link_reason` for `trace_linked`.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_context.py -v`

Expected: PASS.

```bash
git add nanobot/audit/context.py tests/audit/test_context.py
git commit -m "feat(audit): resolve deterministic trace context"
```

### Task 2: Audit runtime service lifecycle

**Files:**
- Create: `nanobot/audit/runtime.py`
- Modify: `nanobot/agent/loop.py:270-420`
- Modify: `nanobot/agent/loop.py:448-590` (`from_config` construction area)
- Test: `tests/audit/test_runtime.py`
- Test: `tests/agent/test_loop_runner_integration.py`

- [ ] **Step 1: Write disabled/full lifecycle tests**

```python
async def test_disabled_runtime_is_noop(tmp_path: Path) -> None:
    runtime = AuditRuntime.from_config(AuditConfig(mode="off"), root=tmp_path)
    await runtime.start()
    result = await runtime.emitter.emit(candidate_event())
    await runtime.close()
    assert result.disabled is True
    assert list(tmp_path.rglob("*.jsonl")) == []


async def test_full_runtime_starts_and_closes_writer(tmp_path: Path) -> None:
    runtime = AuditRuntime.from_config(AuditConfig(mode="full"), root=tmp_path)
    await runtime.start()
    await runtime.close()
    assert read_catalog(tmp_path).cleanly_closed is True
```

- [ ] **Step 2: Implement `AuditRuntime`**

```python
class AuditRuntime:
    def __init__(
        self,
        *,
        writer: AuditWriter | None,
        emitter: AuditEmitter,
        lease: ProcessLease | None = None,
    ) -> None:
        self.writer = writer
        self.emitter = emitter
        self.lease = lease
        self.context_resolver = TraceContextResolver()

    @classmethod
    def from_config(cls, config: AuditConfig, *, root: Path) -> AuditRuntime:
        if config.mode == "off":
            return cls(writer=None, emitter=DisabledAuditEmitter())
        writer = AuditWriter.from_config(root=root, config=config)
        lease = ProcessLease(root / "state" / "process-leases" / f"{writer.process_id}.json")
        redactor = AuditRedactor(
            additional_keys=config.additional_secret_keys,
            additional_patterns=config.additional_secret_patterns,
        )
        return cls(
            writer=writer,
            emitter=AuditEmitter(writer=writer, redactor=redactor, mode=config.mode),
            lease=lease,
        )

    async def start(self) -> None:
        if self.writer is not None:
            await self.writer.start()
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def close(self) -> None:
        await self._stop_heartbeat()
        if self.writer is not None:
            await self.writer.close()
```

Make `start()` idempotent under an `asyncio.Lock` and expose it as `ensure_started()`. AgentLoop
awaits `ensure_started()` at the start of direct and bus-driven message processing, covering SDK
usage where no gateway startup hook runs.

`_heartbeat_loop()` atomically refreshes the mutable `ProcessLease` every five seconds. Shutdown
stops and awaits the heartbeat task before committing `process_closed`; heartbeat failure marks
audit health degraded but never substitutes a lease write for catalog evidence.

When mode is `full` and `warn_plaintext_payloads` is true, startup logs one warning that payloads
are permanent plaintext and recognized-secret redaction is not a guarantee for opaque strings.
The warning contains no configuration values.

- [ ] **Step 3: Inject but do not yet emit from AgentLoop**

Add optional `audit_runtime: AuditRuntime | None` to `AgentLoop.__init__`; `from_config` constructs
it from `config.audit` and `get_audit_dir(config.audit.path)`. Existing direct test constructors
receive a disabled runtime by default. Gateway/SDK shutdown paths call `await audit_runtime.close()`
after accepting no new turns and before process exit.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_runtime.py tests/agent/test_loop_runner_integration.py -v`

Expected: PASS with no behavioral changes while no producers are wired.

```bash
git add nanobot/audit/runtime.py nanobot/agent/loop.py tests/audit/test_runtime.py \
  tests/agent/test_loop_runner_integration.py
git commit -m "feat(audit): add audit runtime lifecycle"
```

### Task 3: Extend Hook model-request and decision boundaries

**Files:**
- Modify: `nanobot/agent/hook.py:12-250`
- Modify: `tests/agent/test_hook_composite.py`
- Modify: `tests/agent/test_runner_hooks.py`

- [ ] **Step 1: Add failing CompositeHook fan-out tests**

Create a recording hook and assert these new methods fan out in order and isolate exceptions:

```python
await composite.before_model_request(context, request)
await composite.after_model_response(context, response)
await composite.on_model_request_error(context, error)
await composite.on_runtime_decision(context, decision)
```

- [ ] **Step 2: Add typed callback DTOs and no-op methods**

```python
@dataclass(frozen=True, slots=True)
class ModelRequestSnapshot:
    model_call_id: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    runtime: LLMRuntime


@dataclass(frozen=True, slots=True)
class RuntimeDecision:
    decision_type: str
    fields: dict[str, Any]


class AgentHook:
    async def before_model_request(self, context, request: ModelRequestSnapshot) -> None:
        pass

    async def after_model_response(self, context, response: LLMResponse) -> None:
        pass

    async def on_model_request_error(self, context, error: BaseException) -> None:
        pass

    async def on_runtime_decision(self, context, decision: RuntimeDecision) -> None:
        pass
```

Add matching `CompositeHook` methods using `_for_each_hook_safe`.

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/agent/test_hook_composite.py tests/agent/test_runner_hooks.py -v`

Expected: PASS.

```bash
git add nanobot/agent/hook.py tests/agent/test_hook_composite.py \
  tests/agent/test_runner_hooks.py
git commit -m "feat(agent): expose auditable model lifecycle hooks"
```

### Task 4: Internal AuditHook and Runner run/iteration/model events

**Files:**
- Create: `nanobot/audit/hook.py`
- Modify: `nanobot/agent/runner.py:50-110`
- Modify: `nanobot/agent/runner.py:270-690`
- Test: `tests/agent/test_runner_audit.py`

- [ ] **Step 1: Write failing normal-run event order test**

```python
async def test_runner_emits_run_iteration_and_model_events() -> None:
    emitter = RecordingEmitter()
    result = await AgentRunner(audit_emitter=emitter).run(
        make_run_spec(audit_context=run_context(), responses=[LLMResponse(content="done")])
    )
    assert result.stop_reason == "completed"
    assert emitter.event_types == [
        "run_started",
        "iteration_started",
        "model_request_started",
        "model_response_received",
        "iteration_finished",
        "run_finished",
    ]
```

- [ ] **Step 2: Add audit context to `AgentRunSpec`**

```python
@dataclass(slots=True)
class AgentRunSpec:
    # existing fields unchanged
    audit_context: AuditRunContext | None = None
```

Add optional `audit_emitter` to `AgentRunner.__init__`. When both emitter and context exist,
compose `RunnerAuditHook` before user hooks. The internal hook must not implement
`finalize_content`.

- [ ] **Step 3: Implement `RunnerAuditHook`**

The hook emits typed candidates for Run, iteration, logical model request/response/failure,
first output, public reasoning summary, and deterministic decision callbacks. `on_finally`
emits a Run terminal only when Runner has a real in-process outcome; process death emits nothing.

```python
class RunnerAuditHook(AgentHook):
    def __init__(self, emitter: AuditEmitter, run: AuditRunContext) -> None:
        super().__init__()
        self._emitter = emitter
        self._run = run

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._emitter.emit(run_started_candidate(self._run, context), critical=True)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._emitter.emit(iteration_started_candidate(self._run, context))
```

- [ ] **Step 4: Call new model hooks around the actual request**

In `_run_core`, create one `model_call_id` before `_request_model`; snapshot
`messages_for_model`, tool schemas, and runtime after context governance. Ensure retry/finalization
model calls each receive distinct logical IDs.

- [ ] **Step 5: Run focused Runner tests**

Run: `pytest tests/agent/test_runner_audit.py tests/agent/test_runner_core.py \
tests/agent/test_runner_errors.py tests/agent/test_runner_reasoning.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nanobot/audit/hook.py nanobot/agent/runner.py tests/agent/test_runner_audit.py
git commit -m "feat(audit): capture runner model lifecycle"
```

### Task 5: Tool terminals, policy decisions, and cancellation

**Files:**
- Modify: `nanobot/agent/runner.py:1060-1320`
- Modify: `nanobot/agent/hook.py:70-250`
- Modify: `nanobot/audit/hook.py`
- Test: `tests/agent/test_runner_audit.py`
- Test: `tests/agent/test_runner_tool_execution.py`
- Test: `tests/agent/test_runner_safety.py`
- Test: `tests/agent/test_hook_composite.py`

- [ ] **Step 1: Write failing paired-terminal tests**

Parameterize tool success, returned error, raised error, timeout, repeated lookup block, workspace
block, and task cancellation. For all in-process paths assert one `tool_started` and exactly one
`tool_finished` with the correct status.

- [ ] **Step 2: Restructure `_execute_tool` around one terminal emission**

Use one outcome variable and a `finally` path; never emit success and error terminals separately:

```python
outcome = ToolAuditOutcome(status="error", result=None, error_kind="internal_error")
await hook.before_execute_tool(context, tool_call, tool, params)
try:
    result = await tool.execute(**params)
    outcome = classify_tool_result(result)
    return build_tool_return(outcome)
except asyncio.CancelledError:
    outcome = ToolAuditOutcome(status="cancelled", result=None, error_kind="task_cancelled")
    raise
except Exception as exc:
    outcome = ToolAuditOutcome(status="error", result=None, error_kind=type(exc).__name__)
    raise
finally:
    await hook.after_execute_tool_terminal(context, tool_call, tool, params, outcome)
```

Add `after_execute_tool_terminal` to `AgentHook` and `CompositeHook`. Keep existing public hook
compatibility by invoking `after_execute_tool` or `on_execute_tool_error` from the classified
path, while the internal audit hook consumes only the new guaranteed terminal callback.

- [ ] **Step 3: Emit deterministic policy events at decision sites**

Emit `policy_blocked`, `continuation_requested`, and `finalization_requested` with exact policy
name/version, counters, thresholds, and reason. Do not infer model strategy changes.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/agent/test_runner_audit.py tests/agent/test_runner_tool_execution.py \
tests/agent/test_runner_safety.py tests/agent/test_hook_composite.py -v`

Expected: PASS.

```bash
git add nanobot/agent/runner.py nanobot/agent/hook.py nanobot/audit/hook.py \
  tests/agent/test_runner_audit.py tests/agent/test_runner_tool_execution.py \
  tests/agent/test_runner_safety.py tests/agent/test_hook_composite.py
git commit -m "feat(audit): close every in-process tool span"
```

### Task 6: Actual Provider attempts and safe retry logs

**Files:**
- Modify: `nanobot/providers/base.py:620-970`
- Modify: `nanobot/agent/runner.py:690-850`
- Modify: `nanobot/audit/hook.py`
- Create: `nanobot/providers/observed_call.py`
- Test: `tests/providers/test_audit_attempts.py`
- Modify: `tests/providers/test_provider_retry.py`

- [ ] **Step 1: Write failing real-attempt tests**

```python
async def test_transient_retry_emits_two_real_attempts() -> None:
    observer = RecordingAttemptObserver()
    provider = FakeProvider([error_response("timeout"), ok_response("done")])
    await provider.chat_with_retry(messages=[], model="m", attempt_observer=observer)
    assert [event.type for event in observer.events] == [
        "attempt_started", "attempt_finished", "retry_scheduled",
        "attempt_started", "attempt_finished",
    ]
    assert observer.attempt_ids[0] != observer.attempt_ids[1]
```

Add a test that an image-stripped retry creates another attempt with
`input_variant="without_images"`.

- [ ] **Step 2: Add the observer protocol**

```python
class ProviderAttemptObserver(Protocol):
    async def attempt_started(self, snapshot: ProviderAttemptSnapshot) -> None: ...
    async def attempt_finished(self, snapshot: ProviderAttemptResult) -> None: ...
    async def route_decision(self, decision: ProviderRouteDecision) -> None: ...
    async def retry_scheduled(self, retry: ProviderRetryDecision) -> None: ...
```

`observed_provider_call()` creates UUIDv7 attempt IDs, emits start/finish around each actual
`call(**kwargs)`, preserves `CancelledError`, and classifies the concrete provider/model.

- [ ] **Step 3: Wire the base retry path without counting the outer wrapper**

Add optional observer parameters to `chat_with_retry`, `chat_stream_with_retry`, and
`_run_with_retry`. Add `observes_leaf_attempts = False` to `LLMProvider`. For ordinary providers,
wrap each concrete `call(**kw)` and image-stripped `call(**retry_kw)` through
`observed_provider_call`. When `self.observes_leaf_attempts` is true, `_run_with_retry` passes the
observer only to the routing wrapper's `_safe_chat`/`_safe_chat_stream` call and does not create an
attempt for that outer call.

Extend `_safe_chat` and `_safe_chat_stream` with a keyword-only `attempt_observer` parameter. They
forward it only when `self.observes_leaf_attempts` is true; ordinary provider `chat` methods never
receive audit-only kwargs.

In `AgentRunner._request_model`, obtain a model-call-scoped observer from `RunnerAuditHook` and
pass it to `chat_with_retry`/`chat_stream_with_retry`. The observer closes over `model_call_id` and
emits typed attempt/route candidates through the same AuditEmitter.

- [ ] **Step 4: Remove raw response content from logs**

Replace logs at the retry give-up and transient retry sites with normalized fields only:

```python
logger.warning(
    "LLM transient error attempt={} status={} kind={} retry_in_s={}",
    attempt,
    response.error_status_code,
    response.error_kind or response.error_type or "provider_error",
    int(round(delay)),
)
```

Add a canary test proving response content is absent from captured logs.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/providers/test_audit_attempts.py tests/providers/test_provider_retry.py -v`

Expected: PASS.

```bash
git add nanobot/providers/base.py nanobot/providers/observed_call.py nanobot/agent/runner.py \
  nanobot/audit/hook.py tests/providers/test_audit_attempts.py \
  tests/providers/test_provider_retry.py
git commit -m "feat(audit): observe real provider attempts"
```

### Task 7: Fallback routing and circuit decisions

**Files:**
- Modify: `nanobot/providers/fallback_provider.py:110-290`
- Modify: `tests/agent/test_runner_fallback.py`
- Modify: `tests/providers/test_audit_attempts.py`

- [ ] **Step 1: Add failing fallback route test**

Simulate primary error, one fallback error, second fallback success. Assert three distinct actual
attempt IDs, actual provider/model per attempt, two `fallback_selected` route decisions, and no
attempt for the `FallbackProvider` wrapper itself.

- [ ] **Step 2: Pass the observer through dynamic providers**

Set `FallbackProvider.observes_leaf_attempts = True`. Its `chat` and `chat_stream` methods pop
`attempt_observer` before constructing child call kwargs, pass it separately to
`_try_with_fallback`, and emit route decisions through that observer. Every primary/fallback child
`p.chat(**kw)` or `p.chat_stream(**kw)` call is wrapped by `observed_provider_call` exactly once;
the observer is never forwarded inside `kw` to a concrete provider. Circuit-open skip emits
`circuit_skipped` without an attempt, and the `FallbackProvider` wrapper itself never receives an
attempt ID.

- [ ] **Step 3: Remove response content from fallback logs**

Log provider/model plus normalized error kind/status; do not interpolate `response.content` or
`fallback_response.content`.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/agent/test_runner_fallback.py tests/providers/test_audit_attempts.py -v`

Expected: PASS.

```bash
git add nanobot/providers/fallback_provider.py tests/agent/test_runner_fallback.py \
  tests/providers/test_audit_attempts.py
git commit -m "feat(audit): record fallback provider routes"
```

### Task 8: Turn, checkpoint, cancellation, Goal, and child Run boundaries

**Files:**
- Modify: `nanobot/agent/loop.py:1320-1705`
- Modify: `nanobot/agent/loop.py:1810-1925`
- Modify: `nanobot/command/builtin.py:170-205`
- Modify: `nanobot/agent/tools/long_task.py`
- Modify: `nanobot/agent/subagent.py`
- Modify: `nanobot/agent/automation_turns.py`
- Test: `tests/agent/test_loop_audit.py`
- Test: `tests/command/test_stop_audit.py`
- Test: `tests/agent/test_subagent_audit.py`

- [ ] **Step 1: Write failing Turn and checkpoint tests**

Assert an ordinary message emits `trace_created`, `turn_started`, one Run, checkpoint events only
after successful persistence, `turn_response_prepared`, and `turn_finished(response_prepared)`.
Assert restoration creates a new Run with `resumed_from_run_id`.

- [ ] **Step 2: Resolve context at `_process_message` entry**

Attach `AuditTurnContext` to `TurnContext`, pass a new `AuditRunContext` into `AgentRunSpec`, and
propagate IDs under reserved `_audit_context` OutboundMessage metadata. ChannelManager extracts
that field for delivery events and sends a copied message with `_audit_context` removed, so audit
routing data never reaches a channel adapter or user-visible content.

- [ ] **Step 3: Emit checkpoint facts after successful mutations**

`_set_runtime_checkpoint`, `_restore_runtime_checkpoint`, and `_clear_runtime_checkpoint` emit
only after Session metadata save succeeds. Store the credential-scrubbed checkpoint in payload
mode and checkpoint version/phase in the event.

- [ ] **Step 4: Record `/stop` before task cancellation**

In `cmd_stop`, collect target Run IDs from the active-task registry, durably request
`cancel_requested`, then invoke `_cancel_active_tasks`. Propagate its event ID into task cancellation
context so tool/Run terminals use `caused_by_event_id`.

- [ ] **Step 5: Emit Goal facts at persistence boundaries**

Create/update/complete/block/cancel events only after Goal state is saved. RuntimeEventBus remains
an independent UI notification and is not reused as audit evidence.

- [ ] **Step 6: Propagate child contexts**

Subagent child Runs use `parent_run_id`; cron/local trigger/SDK inputs set `source_type`; injected
messages retain the active Trace and Turn. Do not let ephemeral turns bypass the internal audit
hook.

- [ ] **Step 7: Run tests and commit**

Run: `pytest tests/agent/test_loop_audit.py tests/command/test_stop_audit.py \
tests/agent/test_subagent_audit.py tests/agent/test_loop_runner_integration.py -v`

Expected: PASS.

```bash
git add nanobot/agent/loop.py nanobot/command/builtin.py nanobot/agent/tools/long_task.py \
  nanobot/agent/subagent.py nanobot/agent/automation_turns.py \
  tests/agent/test_loop_audit.py tests/command/test_stop_audit.py \
  tests/agent/test_subagent_audit.py
git commit -m "feat(audit): capture turn and recovery boundaries"
```

### Task 9: SDK return and channel delivery evidence

**Files:**
- Modify: `nanobot/nanobot.py:80-220`
- Modify: `nanobot/channels/manager.py:650-915`
- Modify: `nanobot/cli/commands.py:1911-1929`
- Test: `tests/sdk/test_audit_sdk.py`
- Test: `tests/channels/test_audit_delivery.py`

- [ ] **Step 1: Write failing SDK return test**

Run `Nanobot.run` with a recording emitter and assert `returned_to_caller` exists while no
`delivery_attempted` or `delivery_finished` event exists.

- [ ] **Step 2: Write failing delivery retry tests**

Use a fake channel that raises once then returns. Assert two attempts and one
`delivery_finished(accepted_by_adapter)`. Add exhausted, cancelled, duplicate-suppressed, and
unknown-channel cases; suppressed/unknown use `final_attempt_ordinal=0` without an attempt.

- [ ] **Step 3: Emit SDK return after result construction**

`Nanobot.run` and streamed completion emit `returned_to_caller` with status and the canonical
returned `RunResult` payload. Errors emit status `error` before re-raising.

Add explicit SDK shutdown and async context-manager support:

```python
async def close(self) -> None:
    await self._loop.close_mcp()
    await self._loop.audit_runtime.close()

async def __aenter__(self) -> Nanobot:
    await self._loop.audit_runtime.ensure_started()
    return self

async def __aexit__(self, *_exc: object) -> None:
    await self.close()
```

Test `async with Nanobot.from_config(...)` writes a clean process catalog close. Existing callers
remain supported, but docs later recommend explicit `await bot.close()` for durable shutdown.

- [ ] **Step 4: Instrument ChannelManager ownership**

ChannelManager creates one `delivery_id` per outbound delivery lifecycle. `_send_with_retry`
emits one attempt around each real `_send_once`, retry schedule before sleep, and a final local
adapter status before every return/raise path. It never invents remote receipt IDs.

Add optional `audit_emitter` to `ChannelManager.__init__`, defaulting to a disabled emitter for
existing tests and standalone use. Gateway construction passes `agent.audit_runtime.emitter` so
Runner and delivery facts use the same process writer.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/sdk/test_audit_sdk.py tests/channels/test_audit_delivery.py -v`

Expected: PASS.

```bash
git add nanobot/nanobot.py nanobot/channels/manager.py nanobot/cli/commands.py \
  tests/sdk/test_audit_sdk.py tests/channels/test_audit_delivery.py
git commit -m "feat(audit): distinguish SDK return and channel delivery"
```

### Task 10: Side-effect evidence adapters

**Files:**
- Create: `nanobot/audit/side_effects.py`
- Modify: `nanobot/audit/hook.py`
- Reuse: `nanobot/utils/file_edit_events.py`
- Test: `tests/audit/test_side_effects.py`

- [ ] **Step 1: Write failing filesystem and shell evidence tests**

For `write_file` and `apply_patch`, assert affected paths and before/after hashes are captured.
For `exec`, assert exit code and working-directory identity are captured but no claim is made that
the whole workspace was snapshotted.

- [ ] **Step 2: Implement capability-specific adapters**

```python
class SideEffectAdapter(Protocol):
    def before(self, tool: Any, params: Any) -> SideEffectSnapshot: ...
    def after(self, before: SideEffectSnapshot, result: Any) -> dict[str, JsonValue]: ...
```

Use existing file path resolution from `nanobot/utils/file_edit_events.py`; do not introduce a
second path parser. Hash only known affected files and generated artifacts.

- [ ] **Step 3: Run tests and commit**

Run: `pytest tests/audit/test_side_effects.py tests/utils/test_file_edit_events.py -v`

Expected: PASS.

```bash
git add nanobot/audit/side_effects.py nanobot/audit/hook.py \
  tests/audit/test_side_effects.py
git commit -m "feat(audit): capture verified tool side effects"
```

### Task 11: Runtime instrumentation regression gate

**Files:**
- Test: `tests/audit/test_runtime_acceptance.py`

- [ ] **Step 1: Add source-coverage acceptance test**

Parameterize ordinary user, ephemeral, SDK, subagent, cron, local trigger, active Goal, and
checkpoint-resumed executions. Assert every real Runner has one Run context, child relationships
are correct, and no source bypasses internal audit composition.

- [ ] **Step 2: Add failure-path acceptance test**

Cover tool recovery, fail-on-tool-error, repeated policy block, Provider retry/fallback, `/stop`,
max iterations, and delivery failure. Assert local failures do not incorrectly make a recovered
Run fail.

- [ ] **Step 3: Run focused and regression suites**

```bash
pytest tests/audit/test_runtime_acceptance.py tests/agent/test_runner_audit.py \
  tests/providers/test_audit_attempts.py tests/channels/test_audit_delivery.py -v
pytest tests/agent tests/providers tests/channels -q
ruff check nanobot/audit nanobot/agent nanobot/providers nanobot/channels tests/audit
```

Expected: all commands exit 0 and Ruff prints `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add tests/audit/test_runtime_acceptance.py
git commit -m "test(audit): gate runtime instrumentation"
```

Phase 2 is complete only after every command above passes. Continue with
`2026-07-27-agent-audit-v1-query-acceptance.md`.
