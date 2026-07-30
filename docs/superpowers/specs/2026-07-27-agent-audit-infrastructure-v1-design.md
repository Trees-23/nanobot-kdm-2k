# Agent Audit Infrastructure V1 Design

**Status:** Revised draft pending user approval

**Date:** 2026-07-27

## 1. Purpose

nanobot needs a durable audit evidence layer that can answer what an agent actually did,
which inputs and runtime settings affected it, where failures occurred, why deterministic
runtime policies retried or stopped, and how the final answer relates to the execution.

This layer is the evidence foundation for later projects that will curate golden traces,
identify reusable trajectories, evaluate behavior, and turn proven patterns into skills.
Those downstream projects must consume audit data without redefining or mutating the audit
source of truth.

The audit layer is not additional terminal logging. It is a versioned, append-only,
queryable execution record with explicit identity, lifecycle, redaction, durability, and
integrity semantics.

## 2. Scope

### 2.1 V1 goals

V1 will:

- cover every `AgentRunner` execution source: user turns, sustained goals, subagents, cron,
  local triggers, and Python SDK calls;
- correlate sessions, logical tasks, input turns, runner executions, model calls, provider
  attempts, tool calls, checkpoints, and goal changes;
- preserve a lightweight sanitized event stream and a separate rich payload stream;
- permanently retain both streams unless an operator explicitly deletes them;
- irreversibly remove credentials matched by known structured fields, built-in patterns, and
  configured deployment patterns before audit content reaches disk;
- record public provider reasoning summaries without claiming access to hidden chain of
  thought;
- survive process interruption without rewriting valid prior evidence;
- detect modification, reordering, broken references, conflicting terminal states, truncated
  JSONL tails, and deletion of records or segments that remain represented in the available
  lineage catalog;
- support concurrent nanobot processes without a shared append lock;
- provide a stable Python query API and `nanobot audit` CLI;
- export sanitized views, full views, and verifiable evidence bundles;
- remain fail-open: audit failure must not change agent behavior or block a task.

### 2.2 V1 non-goals

V1 will not:

- create, score, approve, or version golden trajectories;
- automatically convert traces into skills;
- expose audit queries as an Agent tool;
- build an audit WebUI;
- store raw HTTP requests or response headers, or intentionally retain recognized credentials;
- infer hidden model reasoning or invent causal explanations not present in public reasoning
  summaries or deterministic runtime decisions;
- use Session, WebUI transcript, or RuntimeEventBus records as the audit source of truth;
- provide cryptographic signatures or protection against an attacker who can replace the
  evidence and all trusted hash anchors together.
- guarantee detection when an attacker deletes the entire audit root, the latest lineage tail,
  and all colocated catalog evidence;
- guarantee recognition of arbitrary opaque credentials embedded in unrestricted free text;
- provide legal per-Trace or per-segment deletion and tombstone semantics.

## 3. Existing System Findings

nanobot already persists several related data sets, but each has a different contract:

- Session JSONL is model replay state. It can be rewritten, forked, compacted, cleared, or
  truncated and therefore is not immutable audit evidence.
- WebUI transcript JSONL is presentation replay state. It contains browser-facing deltas and
  tool traces but does not cover all execution sources or runtime decisions.
- `runtime_checkpoint` is temporary recovery state. Successful completion deletes it.
- Daily token usage is an aggregate rather than a per-request record.
- Goal and Dream history preserve their own domain state, not a unified execution timeline.
- `AgentHook` already exposes most Runner lifecycle boundaries and isolates asynchronous hook
  failures, but it lacks exact model request attempt boundaries and guaranteed tool terminal
  callbacks on cancellation.
- RuntimeEventBus carries in-process UI/runtime notifications. Its delivery and persistence
  semantics are not strict enough to make it the audit evidence bus.

The design therefore maintains the separation:

```text
Session = conversation state replayed to the model
WebUI   = presentation events replayed to a client
Audit   = developer-facing execution evidence
```

## 4. Design Decisions

- Build the audit evidence foundation before golden trace or skill extraction systems.
- Cover all AgentRunner sources rather than only user-visible turns.
- Use a four-level identity model: Session, Trace, Turn, and Run.
- Correlate traces only through deterministic rules, never an LLM semantic guess.
- Use Hook-led collection with explicit boundary emitters for facts outside Runner.
- Store sanitized events and complete credential-scrubbed payloads in separate JSONL streams.
- Keep payloads as inline JSONL records rather than content-addressed files.
- Retain events and payloads permanently by default.
- Store payloads as plaintext with strict filesystem permissions.
- Redact recognized credentials in full payload mode while documenting opaque, unrecognized
  secrets as a residual local-storage risk.
- Record semantic model milestones and final content, not every streaming delta.
- Record tool side-effect evidence where a capability-specific adapter can verify it.
- Use per-segment hash chains and payload hashes for tamper detection.
- Use one serialized writer per process and coordinated payload-before-event durability epochs.
- Give each process its own event and payload segments.
- Link segments and process instances through append-only lineage catalogs while explicitly
  excluding externally anchored anti-deletion proof from V1.
- Keep JSONL as the source of truth and use a rebuildable SQLite query index.
- Default audit mode to `full`, with `metadata_only` and `off` alternatives.
- Provide Python API and CLI in V1; defer WebUI and Agent audit tools.

## 5. Identity Model

### 5.1 Definitions

```text
Session: the durable chat or automation conversation namespace
Trace:   one logical task and its complete execution family
Turn:    one externally meaningful input-processing-response boundary
Run:     one actual AgentRunner lifecycle
```

Example:

```text
Session S1
├── Trace T1: repository security audit
│   ├── Turn U1: user requests an audit
│   │   └── Run R1: main agent asks for scope
│   ├── Turn U2: user says "focus on security"
│   │   ├── Run R2: main agent
│   │   └── Run R3: security subagent, parent_run_id=R2
│   └── Turn U3: user sends /stop; this control turn may have no Run
└── Trace T2: unrelated weather request
    └── Turn U4
        └── Run R4
```

### 5.2 Deterministic trace inheritance

A new input inherits an existing `trace_id` only when one of these facts exists:

- an active sustained goal owns the input;
- a checkpoint restoration identifies the prior trace;
- a subagent is created with a parent Run context;
- an input is injected into an active Run;
- channel metadata contains a supported explicit reply or parent task reference;
- an operator or API caller explicitly links the new Turn to a prior Trace.

An ordinary input with none of those facts creates a new Trace. Sharing a `session_key` is not
sufficient evidence that two inputs are the same task. Explicit continuation appends a
`trace_linked` event that records the new Turn, existing Trace, link reason, and actor. It never
merges two existing Trace identities or rewrites prior events.

`/stop` creates a control Turn. If all targeted Runs share one Trace, the control Turn uses
that Trace. If it targets Runs from multiple Traces, it creates its own control Trace and
links each cancellation through target IDs and `caused_by_event_id`.

Checkpoint restoration creates a new `run_id`. The restoring Turn gets a new `turn_id` when
restoration is triggered by a new inbound input. The new Run retains the old `trace_id` and
uses `resumed_from_run_id` to identify the interrupted Run.

### 5.3 ID generation

Trace, Turn, Run, model call, provider attempt, tool call, payload, segment, and event IDs use
UUIDv7. UUIDv7 provides global generation and rough chronological locality, but causal order
must use explicit relationship fields rather than lexical ID or wall-clock order.

## 6. Architecture

```text
TraceContextResolver ─┐
Turn boundaries ──────┤
Runner AuditHook ─────┤
Checkpoint boundary ─┼──> AuditEmitter
Goal boundary ────────┤        │
/stop boundary ───────┤        ▼
Provider leaf calls ──┤  Validate and classify
Channel delivery ─────┘        │
                               ▼
                        Redact recognized secrets
                                  │
                                  ▼
                         Split event and payload
                                  │
                                  ▼
                       Per-process single writer
                                  │
                                  ▼
                    Coordinated durability epochs
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
             payloads JSONL                events JSONL
                    └─────────────┬─────────────┘
                                  ▼
                        lineage catalogs
                                  │
                                  ▼
                       rebuildable SQLite index
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
              Python query API           nanobot audit CLI
```

The durability arrow is directional: payload records become durable before referencing event
records are allowed to become durable. The writer never relies on `flush()` as proof of durable
cross-file ordering.

```text
Collection-to-writer detail:

TraceContextResolver ─┐
Turn boundaries ──────┤
Runner AuditHook ─────┤
Checkpoint boundary ─┼──> AuditEmitter
Goal boundary ────────┤        │
/stop boundary ───────┘        ▼
                         Validate and classify
                                  │
                                  ▼
                           Redact recognized secrets
                                  │
                                  ▼
                         Split event and payload
                                  │
                                  ▼
                        Queue immutable commit item
                                  │
                                  ▼
                     Single writer commits epoch
```

### 6.1 Collection ownership

Each fact has one authoritative producer:

| Owner | Facts |
|---|---|
| TraceContextResolver | Trace creation, deterministic inheritance, Turn identity |
| AgentLoop and Command Router | Turn start/end, `turn_response_prepared`, command-only turns, `/stop` request and targets |
| Runner AuditHook | Run, iteration, logical model call, reasoning summary, tool lifecycle, Run terminal outcome |
| Leaf Provider call boundary | Every actual API attempt, actual provider/model, retry result |
| Fallback Provider | Route decisions, fallback selection, circuit skip, stream recovery |
| Checkpoint persistence boundary | Checkpoint written, restored, or cleared after the operation succeeds |
| Goal persistence boundary | Goal created, updated, completed, blocked, or cancelled |
| ChannelManager | Delivery attempt, retry, adapter return, and final local delivery outcome |
| SDK boundary | `returned_to_caller`; never a fictional channel delivery |
| AuditEmitter | Schema validation, timestamps, redaction, split, hash, sequence, persistence, degradation accounting |

Loop does not reconstruct model details or claim remote delivery. Runner does not guess whether
a Turn has completed. ChannelManager reports only local adapter outcomes unless a channel
explicitly returns a remote receipt. Query code does not infer states from human-readable log
strings.

### 6.2 Internal Hook composition

When audit is enabled, every AgentRunner composes an internal AuditHook automatically. Audit
coverage must not depend on an SDK caller, subagent, or automation coordinator remembering to
attach a hook. Callers pass an `AuditRunContext`; child executions derive a child context with
the same Trace and Turn, a new Run, and `parent_run_id`.

The existing Hook surface gains narrow lifecycle callbacks for:

- logical model request start, response, and error;
- provider attempt start and finish;
- runtime retry or finalization decisions;
- guaranteed tool completion after success, error, cancellation, timeout, or policy block.

The audit hook does not modify messages, tool parameters, model content, or finalized output.

## 7. Event Schema

### 7.1 Common event envelope

```json
{
  "schema_version": 1,
  "event_id": "01982...",
  "event_type": "tool_finished",
  "occurred_at": "2026-07-27T08:31:22.123456Z",
  "monotonic_ns": 445577889900,
  "trace_id": "01982...",
  "turn_id": "01982...",
  "run_id": "01982...",
  "parent_run_id": null,
  "resumed_from_run_id": null,
  "caused_by_event_id": null,
  "model_call_id": null,
  "attempt_id": null,
  "tool_call_id": null,
  "checkpoint_id": null,
  "goal_id": null,
  "delivery_id": null,
  "session_key": "websocket:abc",
  "source_type": "websocket",
  "source_metadata": {},
  "iteration": 2,
  "process_instance_id": "01982...",
  "segment_id": "01982...",
  "segment_sequence": 42,
  "durability_epoch": 7,
  "previous_event_hash": "sha256:...",
  "status": "ok",
  "outcome_reason": null,
  "stop_reason": null,
  "payload_id": "01982...",
  "payload_sha256": "sha256:...",
  "event_hash": "sha256:..."
}
```

`occurred_at` is UTC RFC 3339 with microsecond precision. `monotonic_ns` is process-local and
supports duration and order calculations within one process; it is not compared across
processes.

`source_metadata` is allowlisted and sanitized. It must not copy arbitrary inbound metadata.

Identifiers that do not apply to an event are JSON `null`; they are not omitted. Event-specific
models below define which identifiers and fields are required. Required means non-null after
write-time validation.

### 7.2 Event types and per-event contracts

| Domain | V1 events |
|---|---|
| Process and segment | `process_instance_started`, `process_instance_closed`, `segment_started`, `segment_closed` |
| Audit health | `audit_degraded`, `audit_recovered` |
| Trace | `trace_created`, `trace_linked` |
| Turn | `turn_started`, `input_injected`, `cancel_requested`, `turn_response_prepared`, `turn_finished`, `returned_to_caller` |
| Delivery | `delivery_attempted`, `delivery_retry_scheduled`, `delivery_finished` |
| Run | `run_started`, `run_finished`, `orphan_run_suspected`, `orphan_run_detected`, `orphan_model_call_detected`, `orphan_tool_detected` |
| Iteration | `iteration_started`, `iteration_finished` |
| Model | `model_request_started`, `model_first_output`, `model_response_received`, `model_request_failed` |
| Provider attempt | `provider_route_decision`, `model_attempt_started`, `model_attempt_finished`, `retry_scheduled` |
| Reasoning | `reasoning_summary_received` |
| Tool | `tool_started`, `tool_finished` |
| Runtime decision | `policy_blocked`, `continuation_requested`, `finalization_requested` |
| Checkpoint | `checkpoint_written`, `checkpoint_restored`, `checkpoint_cleared` |
| Goal | `goal_created`, `goal_updated`, `goal_completed`, `goal_blocked`, `goal_cancelled` |

Runtime decision events report only deterministic nanobot decisions. A changed tool choice after
an error is visible in the timeline, but the audit layer does not claim why the model changed
strategy unless a public reasoning summary says so.

The tables below are the schema v1 contract. `T`, `U`, `R`, `M`, `A`, `C`, `K`, `G`, and `D`
mean non-null Trace, Turn, Run, model call, provider attempt, tool call, checkpoint, Goal, and
delivery IDs. Every row also requires the common schema, timestamp, process, segment, sequence,
epoch, and hash fields.

Event-specific fields listed below are typed top-level fields in a discriminated Pydantic union
keyed by `event_type`; V1 does not use an untyped `details` dictionary. Fields not required by a
row are nullable only when its model explicitly declares them nullable. Readers preserve unknown
future fields for round-trip export, while V1 writers emit only schema v1 fields.

| Event | Required identifiers and fields | Status / payload / pairing |
|---|---|---|
| `process_instance_started` | `process_instance_id`, `host_fingerprint`, `pid`, `boot_id`, `writer_version` | payload `process`; first process event |
| `process_instance_closed` | `process_instance_id`, `last_committed_epoch`, `shutdown_reason` | `clean`; absent on crash; final catalog hash is recorded afterward by catalog `process_closed` |
| `segment_started` | `segment_id`, `stream_kind`; `previous_segment_id`, `previous_segment_hash`, `previous_segment_record_count` nullable only for first segment | first event in an event segment; catalog governs every stream |
| `segment_closed` | `segment_id`, `close_reason`, `pre_close_record_count`, `pre_close_hash` | `clean`; absent on partial/crash seal; actual final hash/count is recorded afterward by catalog `segment_closed` |
| `audit_degraded` | `process_instance_id`, `failure_started_at`, `failure_last_seen_at`, `lost_item_count`, `failure_reason`, `affected_trace_ids` | payload `audit_health` optional |
| `audit_recovered` | `process_instance_id`, `degraded_started_at`, `degraded_ended_at`, `last_committed_epoch` | closes a known degraded interval |
| `trace_created` | `T`, `actor_type`, `creation_reason` | payload `turn_input` optional |
| `trace_linked` | `T,U`, `actor_type`, `link_reason`, `linked_source_id` | never merges existing Trace IDs |
| `turn_started` | `T,U`, `session_key`, `source_type` | payload `turn_input` |
| `input_injected` | `T,U,R`, `injection_source`, `target_run_id` | payload `turn_input` |
| `cancel_requested` | `T,U`, `requested_by`, non-empty `target_run_ids` | payload none; cause for terminal events |
| `turn_response_prepared` | `T,U`, `response_kind` | payload `turn_output`; owned by Loop |
| `turn_finished` | `T,U`, `status` | `response_prepared`, `command_completed`, `suppressed`, `failed` |
| `returned_to_caller` | `T,U,R` nullable only for command-only SDK calls | `returned` or `error`; payload `turn_output` |
| `delivery_attempted` | `T,U,D`, `channel`, `attempt_ordinal` | payload `delivery`; owned by ChannelManager |
| `delivery_retry_scheduled` | `T,U,D`, `failed_attempt_ordinal`, `delay_ms`, `policy_name` | payload none |
| `delivery_finished` | `T,U,D`, `final_attempt_ordinal`, `status`; optional `remote_receipt_id` | `accepted_by_adapter`, `failed`, `cancelled`, `suppressed` |
| `run_started` | `T,U,R`; optional `parent_run_id`, `resumed_from_run_id` | payload `run_config` |
| `run_finished` | `T,U,R`, `status`, `stop_reason` | exactly one for in-process terminal paths |
| `orphan_run_suspected` | `T,U,R`, `owner_process_instance_id`, `evidence_kind`, `observed_at` | diagnostic, non-terminal |
| `orphan_run_detected` | `T,U,R`, `owner_process_instance_id`, `evidence_kind`, `observed_at` | terminal reconciliation evidence; no fabricated result |
| `orphan_model_call_detected` | `T,U,R,M`, `owner_process_instance_id`, `evidence_kind` | verification remains incomplete, not a model result |
| `orphan_tool_detected` | `T,U,R,C`, `owner_process_instance_id`, `evidence_kind` | verification remains incomplete, not a tool result |
| `iteration_started` | `T,U,R`, `iteration` | pairs with iteration finish on in-process paths |
| `iteration_finished` | `T,U,R`, `iteration`, `iteration_outcome` | `continued`, `completed`, `failed`, `cancelled` |
| `model_request_started` | `T,U,R,M`, `iteration`, `requested_provider`, `requested_model` | payload `model_request` |
| `model_first_output` | `T,U,R,M`, `output_kind`, `elapsed_ms` | one per content/reasoning kind at most |
| `model_response_received` | `T,U,R,M`, `finish_reason`, `usage` | `ok`; payload `model_response` |
| `model_request_failed` | `T,U,R,M`, `status`, `error_kind`, `attempt_count` | `error`, `timeout`, `cancelled`, `exhausted` |
| `provider_route_decision` | `T,U,R,M`, `route_action`, `provider`, `model`, `input_variant` | route action enum defined below |
| `model_attempt_started` | `T,U,R,M,A`, `attempt_ordinal`, `provider`, `model`, `input_variant` | every real provider API call |
| `model_attempt_finished` | `T,U,R,M,A`, `attempt_ordinal`, `provider`, `model`, `elapsed_ms`, `status` | `ok`, `error`, `timeout`, `cancelled` |
| `retry_scheduled` | `T,U,R,M`, `prior_attempt_id`, `delay_ms`, `policy_name` | applies to another real API attempt |
| `reasoning_summary_received` | `T,U,R,M`, `reasoning_source` | payload `reasoning_summary` |
| `tool_started` | `T,U,R,C`, `iteration`, `tool_name` | payload `tool_input` |
| `tool_finished` | `T,U,R,C`, `tool_name`, `elapsed_ms`, `status` | `ok`, `error`, `cancelled`, `timeout`, `blocked`; payload `tool_output` |
| `policy_blocked` | `T,U,R`, `policy_name`, `policy_version`, `threshold`, `observed_count`; optional `C` | payload none |
| `continuation_requested` | `T,U,R,M`, `continuation_reason`, `attempt_count`, `attempt_limit` | reason `length`, `goal`, `injection`, or `empty_response` |
| `finalization_requested` | `T,U,R`, `finalization_reason`, `remaining_iteration_budget` | payload none |
| `checkpoint_written` | `T,U,R,K`, `checkpoint_version`, `checkpoint_phase` | payload `checkpoint` |
| `checkpoint_restored` | `T,U,R,K`, `source_run_id`, `checkpoint_version` | payload `checkpoint` optional |
| `checkpoint_cleared` | `T,U,R,K`, `clear_reason` | payload none |
| `goal_created` | `T,U,G`, `actor_type`, `goal_version` | payload `goal_state` |
| `goal_updated` | `T,U,G`, `actor_type`, `previous_goal_version`, `goal_version` | payload `goal_state` |
| `goal_completed` | `T,U,G`, `actor_type`, `goal_version` | terminal Goal event |
| `goal_blocked` | `T,U,G`, `actor_type`, `blocker_kind`, `goal_version` | terminal Goal event |
| `goal_cancelled` | `T,U,G`, `actor_type`, `goal_version` | terminal Goal event |

Provider route actions are:

```text
primary_selected | fallback_selected | circuit_skipped | image_stripped_retry |
stream_recovery | failover_skipped_after_stream | fallback_exhausted
```

Payload kinds are closed in schema v1:

```text
process | audit_health | turn_input | turn_output | run_config | model_request |
model_response | reasoning_summary | tool_input | tool_output | checkpoint |
goal_state | delivery
```

Payload `content` is also a discriminated typed union, not arbitrary JSON:

| Payload kind | Required content fields |
|---|---|
| `process` | `runtime_version`, `python_version`, `platform`, `config_hash` |
| `audit_health` | `failure_reason`, `failure_window`, `lost_item_count`, `affected_trace_ids` |
| `turn_input` | `role`, `content`, `media_refs`, `source_message_id` |
| `turn_output` | `content`, `media_refs`, `response_kind` |
| `run_config` | `provider`, `model`, `generation_settings`, `context_limits`, `goal_snapshot` |
| `model_request` | `messages`, `tool_schemas`, `generation_settings`, `system_prompt_hash`, `context_governance_actions` |
| `model_response` | `content`, `tool_calls`, `finish_reason`, `usage`, `provider_metadata` |
| `reasoning_summary` | `content`, `reasoning_source`, `streamed` |
| `tool_input` | `tool_name`, `arguments`, `tool_schema_hash` |
| `tool_output` | `tool_name`, `result`, `normalized_error`, `side_effects` |
| `checkpoint` | `checkpoint_version`, `checkpoint_phase`, `checkpoint_content` |
| `goal_state` | `goal_version`, `goal_status`, `objective`, `budget`, `blocker` |
| `delivery` | `channel`, `content_fingerprint`, `byte_count`, `adapter_metadata` |

Fields that have no value use explicit `null` or an empty typed list according to their payload
model. `provider_metadata` and `adapter_metadata` are allowlisted provider/channel-specific maps;
arbitrary SDK objects, HTTP headers, and authentication fields are forbidden.

### 7.3 Lifecycle status

Tool terminal statuses are:

```text
ok | error | cancelled | timeout | blocked
```

Run terminal statuses are:

```text
succeeded | failed | cancelled | interrupted | exhausted
```

`stop_reason` refines a Run terminal status:

```text
completed
model_error
fatal_tool_error
empty_final_response
user_stop
system_cancel
shutdown
process_crash
orphan_reconciled
max_iterations
timeout_budget
internal_error
```

A failed tool does not imply a failed Run. If the model recovers and returns an answer, the tool
has `status=error` while the Run has `status=succeeded`. A policy block is also a tool outcome,
not automatically a Run failure.

### 7.4 Lifecycle invariants

Invariants distinguish in-process completion from abnormal process death.

For a process that closes cleanly, or a Run that ends through normal completion, handled error,
timeout, or in-process cancellation:

- a Run has one `run_started` and exactly one `run_finished`;
- a started tool call has exactly one `tool_finished`;
- a logical model call has exactly one response or request failure;
- every started provider attempt has exactly one attempt finish;
- every started iteration has exactly one iteration finish.

When the owning process dies abnormally, missing nested terminal events are allowed. Verification
marks the affected Run and Trace `incomplete`. Reconciliation may append
`orphan_model_call_detected` and `orphan_tool_detected` to identify open spans, but those events
do not invent finish reasons, tool results, usage, or provider outcomes.

Additional invariants are:

- a Run has at most one `run_finished`;
- an orphaned Run remains open while evidence is uncertain;
- `orphan_run_suspected` is diagnostic and non-terminal;
- `orphan_run_detected` is terminal reconciliation evidence for a Run that cannot write its own
  `run_finished`; a later `run_finished` for that Run is a lifecycle conflict;
- a Turn can have zero Runs, as with a command-only `/stop` Turn;
- a Turn can have multiple Runs, as with a main agent and subagents;
- a Trace is a correlation container and does not require a terminal event;
- iteration, model, and tool execution events cannot appear after a Run terminal event;
  Turn-level response delivery and checkpoint cleanup may follow because other owners control
  those boundaries after Runner has returned;
- payload references identify an available payload whose hash matches the event, unless the
  Trace is explicitly classified `incomplete` because its durability epoch did not commit;
- unknown optional fields are retained by readers; unknown event types are surfaced rather than
  discarded, allowing forward-compatible inspection.

Validation occurs twice. Write-time validation checks schema and required fields. Query-time
verification checks cross-event lifecycle and relationship invariants.

`turn_response_prepared` stores the canonical outbound response prepared by AgentLoop. It does
not claim delivery. `delivery_finished` stores ChannelManager's local adapter outcome and an
optional remote receipt only when a channel explicitly returns one. `returned_to_caller` is the
SDK equivalent. These events distinguish generated content from observed delivery. Automatic
consistency scoring between the response and prior evidence is deferred to the later evaluation
project.

## 8. Model, Tool, and Decision Evidence

### 8.1 Model requests

V1 records the normalized semantic request sent from Runner to Provider:

- final message list after context governance;
- tool schemas exposed to the model;
- provider and model identifiers;
- generation settings and context limits;
- system prompt hash and, in full mode, credential-scrubbed prompt content;
- context governance actions, including compaction or dropped invalid calls;
- request timeout and retry mode.

V1 does not record raw HTTP headers or provider SDK wire objects by default.

Streaming records semantic milestones rather than every delta:

- request start;
- first content or reasoning output time;
- completed public reasoning summary;
- completed model response;
- usage and latency.

This keeps traces stable across provider chunking differences.

### 8.2 Provider attempts, retries, and fallback routes

A `model_call` is one logical request from Runner. A `provider_attempt` is one actual invocation
of a concrete Provider API method that may issue network or local inference work. Every attempt
has a UUIDv7 `attempt_id`, ordinal, actual provider, actual model, input variant, and terminal
outcome. Attempt ordinals are display metadata and do not replace `attempt_id`.

A logical model request owns one or more provider attempts:

```text
model_request_started M1
model_attempt_started M1/A1, provider=openai, model=gpt-x
model_attempt_finished M1/A1, status=timeout
retry_scheduled M1, prior_attempt_id=A1, delay=2s, policy=provider_retry_v1
model_attempt_started M1/A2, provider=openai, model=gpt-x
model_attempt_finished M1/A2, status=ok
model_response_received M1
```

The full prompt is attached once to M1. Attempts store timing, error classification, provider
error metadata, and retry delay without duplicating the prompt payload.

Instrumentation must surround leaf calls, not only the outer `LLMProvider._run_with_retry`.
The fallback wrapper can call a primary and several dynamically created fallback providers, and
the base retry path can make an image-stripped call. Each real call is a separate attempt.
`FallbackProvider` is classified as a routing wrapper and never emits an attempt for invoking its
own `chat`/`chat_stream`; it emits route decisions and delegates through the shared observed-leaf
call helper. Primary and dynamically created fallback providers use that same helper, preventing
both collapsed and double-counted attempts.
Non-call routing choices use `provider_route_decision`:

```text
provider_route_decision(circuit_skipped, primary provider/model)
provider_route_decision(fallback_selected, fallback provider/model)
provider_route_decision(image_stripped_retry, input_variant=without_images)
```

A circuit skip is not a provider attempt because no API call occurred. Fallback exhaustion and
stream recovery remain explicit route decisions. Verification compares attempt counts with route
decisions so a primary plus two fallbacks cannot be misrepresented as one attempt.

### 8.3 Tools and side effects

Every tool records:

- tool call ID and name;
- credential-scrubbed arguments;
- start and finish times;
- terminal status and normalized error category;
- full result in payload mode;
- bounded preview in the event;
- capability-specific side-effect evidence where available.

Side-effect adapters record evidence without rescanning the entire workspace:

- filesystem tools: affected paths, before/after content hashes, and bounded diff summary;
- `apply_patch`: all touched paths and patch result;
- shell: command classification, exit code, working directory identity, and declared artifacts;
- outbound message tools: sanitized target identity and optional provider receipt ID when the
  provider actually returns one;
- image or file generation: artifact path identity, size, media type, and hash.

The adapter reports observed evidence. It must not claim a side effect that cannot be verified.

### 8.4 Policy decisions

Deterministic decisions record:

- policy name and version;
- triggering event or call;
- bounded counters and thresholds;
- selected action;
- retry or continuation delay when applicable.

Examples include repeated external lookup blocking, repeated workspace boundary escalation,
empty response retry, length recovery, provider retry, goal continuation, and maximum iteration
finalization.

### 8.5 Response preparation and delivery

AgentLoop emits `turn_response_prepared` when it has constructed the canonical outbound content
and enqueued it. This event proves generation, not delivery. Trace and Turn IDs travel as internal
OutboundMessage metadata so ChannelManager can create a local `delivery_id` and emit one
`delivery_attempted` per real adapter call.

ChannelManager emits:

```text
delivery_attempted(attempt=1)
delivery_retry_scheduled(attempt=1, delay=...)
delivery_attempted(attempt=2)
delivery_finished(status=accepted_by_adapter | failed | cancelled | suppressed)
```

`accepted_by_adapter` means the channel coroutine returned without raising. It is not proof that
a remote human received or read the message. `remote_receipt_id` is nullable and valid only when
an adapter explicitly obtains one; V1 does not require changing every `BaseChannel.send()` return
type.

Duplicate suppression produces `delivery_finished(status=suppressed, final_attempt_ordinal=0)` without a
`delivery_attempted`, because no adapter call occurred. An unknown channel similarly produces
`delivery_finished(status=failed, final_attempt_ordinal=0, outcome_reason=unknown_channel)`. Every other
delivery terminal event requires at least one matching attempt.

For streamed channels, individual coalesced send attempts record delivery metadata, byte counts,
and timing without duplicating every content delta into payloads. The canonical final content
remains attached to `turn_response_prepared`; stream-segment delivery outcomes link to the same
Turn. Presentation-only progress and reasoning hints may be summarized by count unless they fail.

Python SDK execution emits `returned_to_caller` with the returned result or error and never emits
a fictional channel delivery.

## 9. Redaction and Data Classification

### 9.1 Recognized-secret rule and residual risk

Credentials matched by known structured fields, built-in patterns, capability-specific rules,
or configured deployment patterns never reach audit storage. Full payload otherwise preserves
free text to maximize future analysis value.

No generic redactor can determine whether every opaque substring in a prompt, Shell output, MCP
result, or provider error is a credential. V1 therefore does not promise that arbitrary unknown
credentials never reach disk. Operators choosing the default `full` mode accept this residual
local-storage risk and should configure deployment-specific secret keys and patterns.

Fields are classified as:

```text
public           eligible for event display
internal         eligible for bounded event preview
sensitive        stored in payload by default
recognized secret never persisted
```

### 9.2 Redaction pipeline

Before split, hashing, indexing, or logging, the emitter:

1. recursively removes values under known secret field names;
2. scans text for Authorization, Bearer, API key, PAT, Cookie, password, private key, and
   provider-specific credential patterns;
3. applies capability-specific rules for HTTP, Shell environment, MCP auth, message metadata,
   and provider errors;
4. applies configured additional secret keys and patterns;
5. produces a redaction report containing rule IDs and counts, never original values.

Replacement values use categorical markers such as `[REDACTED:CREDENTIAL]`. Original secret
hashes are not retained because hashes of low-entropy passwords can be reversed by enumeration.

If redaction processing itself fails, the candidate event and payload are dropped. The
implementation must never fall back to writing the unprocessed input.

Known application-log leaks in the touched execution paths are part of V1 hardening. Provider
retry and fallback logs must stop interpolating raw response content. Logging tests plant
recognized and configured canary values and assert that audit files, the derived index, exports,
and touched application logs do not contain them. This is not a proof about arbitrary opaque
strings in every third-party channel or provider log.

### 9.3 Data access defaults

- Query and export default to sanitized events without payloads.
- Payload access requires `include_payloads=True` or `--include-payloads`/`--mode full`.
- Full access emits a warning that permanent plaintext payloads may contain business-private
  information.
- Audit modules and execution paths modified by V1 never intentionally print payloads, prompts,
  tool arguments, tool results, or failed redaction inputs. Unmodified third-party integrations
  remain outside this logging guarantee.

## 10. Physical Storage

### 10.1 Directory layout

```text
runtime/audit/v1/
├── events/YYYY-MM-DD/<instance>-<pid>-<segment>.jsonl
├── payloads/YYYY-MM-DD/<instance>-<pid>-<segment>.jsonl
├── catalog/<process-instance>/<catalog-segment>.jsonl
└── state/
    ├── process-leases/
    ├── health.json
    └── index.sqlite
```

The audit root is outside the Agent workspace and is not an allowed filesystem-tool root.
It must be excluded from Git, public image build contexts, and public backup/export flows.
Container deployments mount it as a dedicated persistent volume.

On POSIX, directories are created with mode `0700` and files with `0600` under a restrictive
umask. Windows uses a current-user-private location and supported ACLs. If the platform cannot
establish the requested protection, startup reports a high-visibility warning.

### 10.2 Event and payload records

Events contain lightweight facts, previews, relationships, hashes, and payload references.
Payloads contain complete credential-scrubbed model inputs, responses, reasoning summaries,
tool arguments, tool results, checkpoints, and side-effect evidence.

Payload records include:

```json
{
  "schema_version": 1,
  "payload_id": "01982...",
  "event_id": "01982...",
  "payload_kind": "tool_io",
  "content": {},
  "payload_segment_id": "01982...",
  "payload_segment_sequence": 18,
  "previous_payload_hash": "sha256:...",
  "payload_hash": "sha256:..."
}
```

The event stores the same `payload_id` and `payload_sha256=payload_hash`.

Catalog records use a separate closed schema:

```json
{
  "catalog_version": 1,
  "catalog_record_type": "epoch_committed",
  "catalog_record_id": "01982...",
  "process_instance_id": "01982...",
  "catalog_segment_id": "01982...",
  "catalog_sequence": 12,
  "previous_catalog_hash": "sha256:...",
  "durability_epoch": 7,
  "event_segment_id": "01982...",
  "event_durable_offset": 88210,
  "event_final_hash": "sha256:...",
  "payload_segment_id": "01982...",
  "payload_durable_offset": 934455,
  "payload_final_hash": "sha256:...",
  "catalog_record_hash": "sha256:..."
}
```

Catalog record types are `process_started`, `segment_registered`, `segment_closed`,
`segment_abandoned`, `epoch_committed`, and `process_closed`. Each type has a dedicated model;
unknown catalog record types are surfaced and stop committed-prefix advancement. Mutable lease
files help determine liveness but are not audit evidence and cannot replace catalog records.

| Catalog record | Required type-specific fields |
|---|---|
| `process_started` | `host_fingerprint`, `pid`, `boot_id`, `writer_version`, `started_at` |
| `segment_registered` | `stream_kind`, `segment_id`, `previous_segment_id`, `previous_segment_hash`, `previous_segment_record_count`, `path_token` |
| `segment_closed` | `stream_kind`, `segment_id`, `final_offset`, `final_hash`, `record_count`, `byte_size` |
| `segment_abandoned` | `stream_kind`, `segment_id`, `last_committed_offset`, `last_committed_hash`, `abandon_reason` |
| `epoch_committed` | `durability_epoch`, event/payload segment IDs, durable offsets, final hashes, and record counts |
| `process_closed` | `last_committed_epoch`, `shutdown_reason`, event/payload lineage heads, `closed_at`; its own `catalog_record_hash` becomes the catalog head |

`path_token` is a relative, validated identifier under the configured audit root, never an
arbitrary absolute path. Nullable predecessor fields are permitted only for the first segment of
a stream or first catalog segment of a process instance.

### 10.3 Single writer and coordinated durability epochs

Each process owns exactly one audit writer. Concurrent tools and callbacks submit immutable,
already-validated and already-redacted commit items through a bounded queue. Only the writer can
assign segment sequences, previous hashes, durability epochs, and file offsets.

The queue is bounded by item count and estimated serialized bytes. It holds references to
immutable content rather than duplicating large strings. A single item above the normal byte
budget may use an exclusive slot when the queue is otherwise empty; it is never silently
truncated. If it cannot be accepted within the enqueue bound, audit degrades under the same
fail-open rules as any queue-full condition.

The queue is FIFO. A critical item forces the current batch to commit but cannot overtake older
items, preserving causal emission order. Once accepted, a commit item is owned by the writer;
cancellation of the submitting Agent task does not cancel it. Critical acknowledgement waits are
shielded from caller cancellation and bounded by configuration. Graceful process shutdown drains
accepted items up to a bounded deadline, commits a final epoch, and then writes clean segment and
process closure records.

The writer commits one or more items as a durability epoch:

1. Build and validate candidate facts in memory.
2. Apply recognized-secret redaction and field classification.
3. Split payload and lightweight event records and compute their hashes.
4. Append every payload record in the epoch to the current payload segment.
5. Flush and `fsync(payload_fd)`; if a payload segment was created, also fsync its directory.
6. Append every referencing event record to the event segment only after step 5 succeeds.
7. Flush and `fsync(event_fd)`; if an event segment was created, also fsync its directory.
8. Append a catalog commit containing epoch ID, durable event/payload offsets, final hashes,
   record counts, and segment IDs.
9. Flush and `fsync(catalog_fd)`; acknowledge the epoch only after this step succeeds.

Before a newly created data segment accepts records, the writer creates and fsyncs the file and
parent directory, appends `segment_registered` with the prior segment's final committed hash and
count to the process catalog, and fsyncs that catalog record. Closing a segment first commits its
`segment_closed` event, then appends and fsyncs the corresponding catalog `segment_closed` record.
Thus an accidentally missing data segment remains discoverable through catalog lineage.
Creating a new catalog segment likewise fsyncs the catalog directory and starts with the previous
catalog segment ID, final hash, and record count.

An epoch containing no payload records skips the payload fsync phase. `flush()` alone is never
treated as durable. Fsync of the event file never implies durability of the payload file.

Critical items request a bounded durability acknowledgement. They include segment boundaries,
Run start and terminal evidence, tool terminal evidence, checkpoint mutations, cancellation,
delivery terminal evidence, and audit degradation/recovery. Noncritical items may be grouped, but
their epoch uses the same payload-fsync-before-event-fsync order. The default grouping deadline
is 5 seconds or 100 records.

If enqueue or acknowledgement exceeds its configured bound, Agent execution continues under the
fail-open rule, while the process and known Trace IDs enter degraded state. No timeout is treated
as successful persistence.

If any append, flush, or fsync has a partial or uncertain result, the writer closes the file
descriptor, marks the current segment abandoned in the next writable catalog record, and never
appends to that segment again. It starts a new segment and lineage branch. Readers accept only
the cataloged durable prefix and report the abandoned tail.

Payload-first durable ordering ensures a committed event cannot reference an uncommitted payload.
A crash can leave durable orphan payloads or durable but uncataloged event records between steps;
those records are `uncertain` rather than committed evidence until reconciliation. V1 retains
orphan payloads and does not include an automatic purge path.

### 10.4 Segment lifecycle and size

Each process writes independent event, payload, and catalog segments. It rotates a data segment
at UTC date change or 64 MiB target size. A single payload larger than 64 MiB is written to a
dedicated one-record segment without truncation and committed through its own durability epoch.

The writer never edits a closed, abandoned, or uncertain segment. On a malformed trailing line
after a crash, readers preserve the cataloged durable prefix, report `truncated_tail`, and the
next writer starts a new segment instead of repairing the old file.

### 10.5 Integrity

Each segment has its own monotonically increasing sequence and SHA-256 chain. Hash input uses
canonical UTF-8 JSON with sorted keys and no insignificant whitespace, excluding only the
record's own hash field.

`segment_started` includes the prior segment ID, final hash, and record count for the same process
and stream. The in-segment `segment_closed` event announces closure without self-referential final
values; the subsequent catalog `segment_closed` record stores the actual final hash and count.
The process catalog chains segment creation, closure, abandonment, and committed durability
epochs. Catalog segments also link to the previous catalog segment. This lineage detects a
missing or altered segment as long as the corresponding catalog or successor lineage evidence
remains available.

Per-process chains deliberately avoid a global sequence and cross-process write lock.
Cross-process causality is reconstructed through Trace, Turn, Run, parent, resume, and cause IDs.

These hashes are not signatures and have no external immutable anchor. V1 cannot prove that an
attacker did not delete the entire audit root, an entire process instance including its catalog,
or the newest data and catalog tails together. It guarantees internal consistency and detects
deletion of cataloged evidence, not externally trusted non-repudiation. Evidence bundle manifests
expose lineage and record hashes so a later system can add signed remote anchors without changing
schema v1 records.

### 10.6 Deletion semantics

V1 provides no legal per-Trace, per-segment, or retention deletion operation and emits no
deletion tombstone. Events and payloads remain permanent by default. A missing cataloged segment
or payload is an integrity failure. Wholesale operator removal of the audit root is outside V1
and starts a new unlinked audit history; it cannot be proven from evidence that was removed with
the root.

## 11. Durability, Failure, and Recovery

### 11.1 Fail-open behavior

Audit failures do not propagate into Agent behavior. The audit hook never changes content and
its emitter catches all persistence failures. This choice means V1 cannot simultaneously promise
that every execution has durable audit evidence.

On queue-full, acknowledgement timeout, disk-full, permission, serialization, partial append,
flush, fsync, or catalog failure:

- the item is committed only if its cataloged durability epoch completed;
- the current process and all known affected Trace IDs become degraded in memory;
- the last cataloged epoch remains the authoritative durable prefix;
- an in-memory health counter records reason, first failure time, last failure time, count, and
  known affected Trace IDs;
- application logging emits a throttled message containing no payload data;
- Agent execution continues;
- the next successful audit write emits `audit_degraded`, followed by `audit_recovered`, with
  loss counts and failure windows but no fabricated missing evidence.

If the process crashes before it can persist a loss marker, its absent clean-close record and
last committed epoch conservatively mark open Runs and the remaining process time window
`unknown/incomplete`. A Run whose `run_started` never became durable may not have a recoverable
Trace ID; the system can report only a process-level audit gap for that interval. It must not
invent a missing Trace.

Audit degradation is visible in Trace or process-window integrity status. Degraded, unknown, or
incomplete evidence cannot silently qualify as a complete golden source.

### 11.2 Cancellation

`/stop` records a control Turn and `cancel_requested` before cancelling target tasks. Target Run
and tool terminal events link back through `caused_by_event_id`.

```text
Turn U2: cancel_requested(requested_by=user, target_run_ids=[R1])
Run R1:  tool_finished(status=cancelled)
Run R1:  run_finished(status=cancelled, stop_reason=user_stop)
Turn U2: turn_finished(status=command_completed)
```

System shutdown and internal cancellation use different `requested_by` and `stop_reason` values.

### 11.3 Crash reconciliation

Each process instance has a unique ID, startup record, periodically refreshed lease, and clean
shutdown marker. A reconciler does not mark another process's open Run interrupted unless the
owner is confirmed dead through an expired lease plus platform-appropriate process or instance
evidence.

When evidence is uncertain, it writes `orphan_run_suspected` and leaves the Run open. When the
owner is confirmed dead, or a checkpoint from an open Run owned by a confirmed-dead process is
explicitly restored, it writes `orphan_run_detected`; a new Run receives
`resumed_from_run_id`. Resuming a checkpoint from a Run already terminated as `cancelled` does
not produce an orphan event.

The old Run is classified as interrupted during the reconstructed view. The reconciler never
backdates or inserts a fictional event at the crash instant.

For each confirmed orphan Run, reconciliation enumerates model calls, provider attempts, tools,
and iterations that started in committed epochs without committed terminal evidence. It may
append `orphan_model_call_detected` and `orphan_tool_detected`, carrying only the observed start
IDs and process-death evidence. The reconstructed spans remain `incomplete`; these events never
claim an error, cancellation, result, usage, or exact stop time.

Records beyond the last committed durability epoch remain `uncertain` even when their JSON and
hashes appear complete. Reconciliation does not promote them to committed evidence because their
cross-file durability order was never acknowledged. It records the last committed prefix, seals
the remainder as an abandoned tail, and starts a new lineage segment.

## 12. Configuration

Configuration is declared in `nanobot/config/schema.py` and follows existing camelCase aliases.

```json
{
  "audit": {
    "mode": "full",
    "path": null,
    "segmentMaxBytes": 67108864,
    "fsyncIntervalSeconds": 5,
    "fsyncRecordInterval": 100,
    "writerQueueCapacity": 4096,
    "writerQueueMaxBytes": 268435456,
    "enqueueTimeoutMs": 25,
    "criticalAckTimeoutMs": 2000,
    "previewMaxChars": 512,
    "indexEnabled": true,
    "warnPlaintextPayloads": true,
    "additionalSecretKeys": [],
    "additionalSecretPatterns": []
  }
}
```

Modes are:

- `full`: sanitized events plus complete credential-scrubbed payloads; default;
- `metadata_only`: events, hashes, counters, and bounded previews without full payloads;
- `off`: no audit collection, with an explicit startup warning.

There is no automatic retention or time-based deletion in V1. Deletion is an explicit operator
action outside normal Agent execution and is not represented as a valid V1 tombstone.

## 13. Query and Index Design

### 13.1 Rebuildable index

JSONL is authoritative. `state/index.sqlite` is a disposable derived index containing:

- segment scan cursors and integrity status;
- event identifiers and lightweight filter columns;
- Trace, Turn, Run, parent, resume, and cause relationships;
- model, tool, status, source, Session, and time fields;
- payload references and sizes, but not full payload content.

Exactly one index writer holds `state/index.lock` across processes. SQLite runs in WAL mode for
concurrent readers, but no second process may mutate the index. If the writer lock is unavailable,
another process may query the existing index or stream JSONL; it must not start a competing
index update.

The stable prefix of an open segment is not "all complete newline-terminated JSON." It is the
byte offset and final hash recorded by the latest committed durability epoch in that process
catalog. The indexer never indexes uncataloged tail bytes as committed evidence.

The index has its own schema version. Additive migrations run transactionally. A failed or
unsupported migration discards and rebuilds the derived index rather than modifying JSONL.
Index loss, corruption, or staleness triggers a rebuild from cataloged durable prefixes. Query
can fall back to streaming scans and must disclose when the index is incomplete or stale.

### 13.2 Python API

The stable API exposes typed results rather than storage paths:

```python
query.find_traces(
    since="24h",
    run_status="failed",
    tool="exec",
    source_type="websocket",
    limit=50,
)

query.load_trace("trace-id", include_payloads=False)
query.verify_trace("trace-id")
query.export_trace("trace-id", mode="sanitized", output=path)
```

`TraceView` contains:

```text
summary
turns[]
run_tree
timeline[]
payloads{} (only when explicitly requested)
decisions[]
integrity
```

Integrity status is conservative:

```text
valid       all required lifecycle evidence is present in committed epochs
degraded    a persisted loss/degradation marker overlaps the Trace
incomplete  abnormal process death left started spans without real terminal evidence
unknown     process-level audit gap exists but affected Trace identity is not fully recoverable
invalid     hash, lineage, schema, reference, or lifecycle verification failed
```

Statuses are not automatically promoted. A Trace can move from `incomplete` to `valid` only when
later committed evidence genuinely supplies the required facts; orphan detection alone preserves
`incomplete` because it cannot supply an execution result.

Trace reconstruction prioritizes explicit causal edges, iteration, and segment sequence.
Wall-clock time is used for display and deterministic ordering of otherwise unrelated events,
not as proof of causality.

All list APIs are paginated. Payload and export APIs support streaming so data volume does not
grow process memory with audit history size.

### 13.3 CLI

```text
nanobot audit list
nanobot audit show <trace-id>
nanobot audit verify [<trace-id> | --segment <id> | --all]
nanobot audit export <trace-id> --mode <sanitized|full|evidence-bundle>
nanobot audit stats
nanobot audit index status
nanobot audit index rebuild
nanobot audit doctor
```

`doctor` checks permissions, disk space, writer health, process leases, segment tails, index
freshness, and configured audit mode without exposing payload content.

### 13.4 Export modes

- `sanitized`: normalized TraceView without full payloads; default.
- `full`: normalized TraceView with credential-scrubbed payloads inlined or streamed.
- `evidence-bundle`: original matching JSONL records, payload records, relevant process catalog
  records and committed epochs, chain witness entries for omitted records, segment manifest and
  hashes, exporter version, and verification report.

A chain witness contains sequence, previous hash, and record hash without unrelated record
content. It lets the bundle preserve the selected records' positions in each source chain
without exporting unrelated sensitive payloads. Because V1 hashes are not externally signed,
the bundle proves internal consistency against its manifest, not third-party authorship.

An export is source material, not a golden trace. Later curation stores annotations, scores,
review decisions, and skill artifacts separately and references immutable evidence hashes.

## 14. Example Traces

Examples are human-readable projections that omit common required fields and some routine
iteration/checkpoint events. They are not valid writer inputs. Fixture files used by tests contain
every emitted event, every required field, catalog epochs, payload records, and the complete
schema.

### 14.1 Normal tool success

```jsonl
{"event_type":"trace_created","trace_id":"T1"}
{"event_type":"turn_started","trace_id":"T1","turn_id":"U1"}
{"event_type":"run_started","trace_id":"T1","turn_id":"U1","run_id":"R1"}
{"event_type":"iteration_started","run_id":"R1","iteration":0}
{"event_type":"model_request_started","run_id":"R1","model_call_id":"M1","payload_id":"P1"}
{"event_type":"model_attempt_started","model_call_id":"M1","attempt_id":"A1","attempt_ordinal":1,"provider":"openai","model":"gpt-x"}
{"event_type":"model_attempt_finished","model_call_id":"M1","attempt_id":"A1","attempt_ordinal":1,"status":"ok"}
{"event_type":"model_response_received","run_id":"R1","model_call_id":"M1","status":"ok","payload_id":"P2"}
{"event_type":"checkpoint_written","run_id":"R1","checkpoint_id":"K1","payload_id":"P4"}
{"event_type":"tool_started","run_id":"R1","tool_call_id":"C1","tool":"read_file","payload_id":"P3"}
{"event_type":"tool_finished","run_id":"R1","tool_call_id":"C1","tool":"read_file","status":"ok","payload_id":"P5"}
{"event_type":"iteration_finished","run_id":"R1","iteration":0,"status":"continued"}
{"event_type":"iteration_started","run_id":"R1","iteration":1}
{"event_type":"model_request_started","run_id":"R1","model_call_id":"M2","payload_id":"P6"}
{"event_type":"model_attempt_started","model_call_id":"M2","attempt_id":"A2","attempt_ordinal":1,"provider":"openai","model":"gpt-x"}
{"event_type":"model_attempt_finished","model_call_id":"M2","attempt_id":"A2","attempt_ordinal":1,"status":"ok"}
{"event_type":"model_response_received","run_id":"R1","model_call_id":"M2","status":"ok","payload_id":"P7"}
{"event_type":"iteration_finished","run_id":"R1","iteration":1,"status":"completed"}
{"event_type":"run_finished","run_id":"R1","status":"succeeded","stop_reason":"completed"}
{"event_type":"checkpoint_cleared","run_id":"R1","checkpoint_id":"K1"}
{"event_type":"turn_response_prepared","turn_id":"U1","payload_id":"P8"}
{"event_type":"turn_finished","turn_id":"U1","status":"response_prepared"}
{"event_type":"delivery_attempted","turn_id":"U1","delivery_id":"D1","channel":"websocket","attempt_ordinal":1}
{"event_type":"delivery_finished","turn_id":"U1","delivery_id":"D1","final_attempt_ordinal":1,"status":"accepted_by_adapter"}
```

Expected reconstruction: one Trace, one Turn, one successful Run, two model calls, one paired
tool call, valid payload references, and a valid integrity report.

### 14.2 Recoverable failures and policy block

```jsonl
{"event_type":"trace_created","trace_id":"T2"}
{"event_type":"turn_started","trace_id":"T2","turn_id":"U2"}
{"event_type":"run_started","trace_id":"T2","turn_id":"U2","run_id":"R2"}
{"event_type":"model_request_started","run_id":"R2","model_call_id":"M3"}
{"event_type":"provider_route_decision","model_call_id":"M3","route_action":"primary_selected","provider":"openai","model":"gpt-x"}
{"event_type":"model_attempt_started","model_call_id":"M3","attempt_id":"A3","attempt_ordinal":1,"provider":"openai","model":"gpt-x"}
{"event_type":"model_attempt_finished","model_call_id":"M3","attempt_id":"A3","attempt_ordinal":1,"status":"timeout"}
{"event_type":"retry_scheduled","model_call_id":"M3","prior_attempt_id":"A3","delay_ms":2000,"policy_name":"provider_retry_v1"}
{"event_type":"model_attempt_started","model_call_id":"M3","attempt_id":"A4","attempt_ordinal":2,"provider":"openai","model":"gpt-x"}
{"event_type":"model_attempt_finished","model_call_id":"M3","attempt_id":"A4","attempt_ordinal":2,"status":"error"}
{"event_type":"provider_route_decision","model_call_id":"M3","route_action":"fallback_selected","provider":"anthropic","model":"claude-y"}
{"event_type":"model_attempt_started","model_call_id":"M3","attempt_id":"A5","attempt_ordinal":3,"provider":"anthropic","model":"claude-y"}
{"event_type":"model_attempt_finished","model_call_id":"M3","attempt_id":"A5","attempt_ordinal":3,"status":"ok"}
{"event_type":"model_response_received","model_call_id":"M3","status":"ok"}
{"event_type":"tool_started","run_id":"R2","tool_call_id":"C2","tool":"web_search"}
{"event_type":"tool_finished","run_id":"R2","tool_call_id":"C2","status":"error","outcome_reason":"provider_error"}
{"event_type":"tool_started","run_id":"R2","tool_call_id":"C3","tool":"web_search"}
{"event_type":"tool_finished","run_id":"R2","tool_call_id":"C3","status":"error","outcome_reason":"provider_error"}
{"event_type":"tool_started","run_id":"R2","tool_call_id":"C4","tool":"web_search"}
{"event_type":"policy_blocked","run_id":"R2","tool_call_id":"C4","policy_name":"repeated_external_lookup","policy_version":"v1","observed_count":3,"threshold":2}
{"event_type":"tool_finished","run_id":"R2","tool_call_id":"C4","status":"blocked","outcome_reason":"repeated_external_lookup"}
{"event_type":"model_request_started","run_id":"R2","model_call_id":"M4"}
{"event_type":"model_attempt_started","model_call_id":"M4","attempt_id":"A6","attempt_ordinal":1,"provider":"anthropic","model":"claude-y"}
{"event_type":"model_attempt_finished","model_call_id":"M4","attempt_id":"A6","attempt_ordinal":1,"status":"ok"}
{"event_type":"model_response_received","run_id":"R2","model_call_id":"M4","status":"ok"}
{"event_type":"run_finished","run_id":"R2","status":"succeeded","stop_reason":"completed"}
{"event_type":"turn_response_prepared","turn_id":"U2","payload_id":"P-final"}
{"event_type":"turn_finished","turn_id":"U2","status":"response_prepared"}
```

Expected reconstruction: two primary attempts, one explicit fallback selection, one successful
fallback attempt, two failed tools, one deterministic block, and a successful Run. Tool failure
rate and Run success rate remain distinct.

### 14.3 User cancellation and checkpoint resume

```jsonl
{"event_type":"trace_created","trace_id":"T3"}
{"event_type":"turn_started","trace_id":"T3","turn_id":"U3"}
{"event_type":"run_started","trace_id":"T3","turn_id":"U3","run_id":"R3"}
{"event_type":"tool_started","run_id":"R3","tool_call_id":"C5","tool":"exec"}
{"event_type":"checkpoint_written","run_id":"R3","checkpoint_id":"K3"}
{"event_type":"turn_started","trace_id":"T3","turn_id":"U4","source_type":"control"}
{"event_type":"cancel_requested","event_id":"E_STOP","turn_id":"U4","requested_by":"user","target_run_ids":["R3"]}
{"event_type":"tool_finished","run_id":"R3","tool_call_id":"C5","status":"cancelled","caused_by_event_id":"E_STOP"}
{"event_type":"run_finished","run_id":"R3","status":"cancelled","stop_reason":"user_stop","caused_by_event_id":"E_STOP"}
{"event_type":"turn_finished","turn_id":"U4","status":"command_completed"}
{"event_type":"turn_started","trace_id":"T3","turn_id":"U5"}
{"event_type":"run_started","trace_id":"T3","turn_id":"U5","run_id":"R4","resumed_from_run_id":"R3"}
{"event_type":"checkpoint_restored","run_id":"R4","checkpoint_id":"K3"}
{"event_type":"run_finished","run_id":"R4","status":"succeeded","stop_reason":"completed"}
{"event_type":"checkpoint_cleared","run_id":"R4","checkpoint_id":"K3"}
{"event_type":"turn_response_prepared","turn_id":"U5","payload_id":"P-resumed-final"}
{"event_type":"turn_finished","turn_id":"U5","status":"response_prepared"}
```

Expected reconstruction: the control Turn caused cancellation of R3, R4 resumed from R3 under
the same Trace, and each Run has an independent terminal outcome.

## 15. Testing and Acceptance

### 15.1 Unit tests

- schema validation and forward-compatible unknown fields;
- deterministic Trace inheritance rules;
- recursive and textual credential redaction;
- event/payload split, single-writer queue, and coordinated durability epochs;
- canonical hashes and chain verification;
- payload/event/catalog fsync ordering, critical acknowledgements, batching, and rotation;
- process and cross-segment lineage, abandonment, and committed-prefix selection;
- truncated-tail reading without rewriting evidence;
- lifecycle verification for duplicate or missing terminal events;
- index incremental update, stale detection, and rebuild;
- TraceView causal reconstruction and pagination;
- sanitized, full, and evidence-bundle export.

### 15.2 Security and fault injection

Recognized and configured canary credentials are planted in nested JSON, free text, HTTP headers,
Shell commands and environment, MCP output, provider errors, checkpoints, and source metadata.
The original value must not appear in events, payloads, SQLite, exports, or touched application
logs. Arbitrary unconfigured opaque strings are outside this guarantee and remain a documented
risk of full mode.

Fault injection covers ENOSPC, EACCES, queue saturation, acknowledgement timeout, partial and
uncertain append, serialization failure, flush failure, payload/event/catalog fsync failure,
redactor failure, deleted records, an entire cataloged segment deletion, modified payloads,
reordered sequences, truncated tails, parallel tools, subagents, and process kill immediately
after degradation.

The Agent result, model messages, tool parameters, retry decisions, and final answer must remain
unchanged when audit is off or when audit persistence fails.

### 15.3 End-to-end scenarios

1. Normal conversation without tools.
2. One successful tool call with side-effect evidence.
3. Tool error followed by a successful alternative.
4. Third repeated external lookup blocked by policy.
5. User `/stop` while a tool runs.
6. Maximum iteration exhaustion and finalization attempt.
7. Provider error, retry success, and exhausted retry failure.
8. Primary failure, circuit skip, image-stripped retry, multiple fallbacks, and fallback exhaustion.
9. Channel delivery retry, final adapter failure, duplicate suppression, and SDK return.
10. Process kill during a tool followed by checkpoint restoration.
11. Writer queue full followed by recovery, and degradation followed immediately by process kill.
12. Partial append followed by mandatory segment abandonment and new segment creation.
13. Goal create, update, complete, block, and cancel.
14. No reasoning, one-shot reasoning summary, and streamed reasoning summary.
15. Main agent with concurrent subagents.
16. Cron, local trigger, ephemeral, and SDK execution sources.

Behavioral scenarios with a committed `trace_created` must be reconstructable through one Trace
ID and expose the correct final Run status and stop reason. Fault scenarios in which even
`trace_created` or `run_started` could not commit must instead produce a process-window audit gap;
the design does not require an impossible missing Trace ID.

### 15.4 Compatibility and scale

- POSIX and Windows path and permission behavior is covered.
- Writer queue memory is bounded by both item count and estimated serialized bytes, plus one
  active item, and never grows with historical audit size. A single oversized payload uses an
  exclusive queue slot; inability to accept it within the bound degrades audit rather than
  truncating it or blocking Agent execution indefinitely.
- A one-million-event synthetic corpus uses the index for paginated queries.
- Performance benchmarks are recorded, but fragile absolute wall-clock thresholds are not hard
  CI gates across different hardware.
- Schema v1 fixtures remain readable in later versions.

### 15.5 V1 completion gate

V1 is complete only when:

- every behavioral scenario with a committed Trace reconstructs from its Trace ID, while
  pre-Trace persistence failures produce an explicit process-window audit gap;
- verifier detects modification, reorder, missing cataloged segments or payloads, conflicting
  terminals, dangling relationships, abandoned tails, and truncated tails within the documented
  local lineage threat model;
- recognized and configured canary credential values have zero leakage in audited storage,
  exports, the derived index, and touched logging paths;
- kill tests preserve prior valid evidence and correctly link resumed Runs;
- audit failure never changes Agent outcome, while affected evidence is conservatively marked
  degraded, unknown, or incomplete;
- Python API and CLI can query, verify, and export the resulting evidence;
- the three fixed schema v1 fixtures produce their expected TraceView and verification report.

## 16. Minimal Change Surface

New focused modules should live under `nanobot/audit/`:

```text
nanobot/audit/schema.py       event and payload models, enums, validation
nanobot/audit/context.py      Trace/Turn/Run context and deterministic resolver
nanobot/audit/redaction.py    classification and credential removal
nanobot/audit/integrity.py    canonical hashing and verification primitives
nanobot/audit/store.py        segment files, rotation, durable-prefix readers
nanobot/audit/writer.py       per-process queue and coordinated durability epochs
nanobot/audit/catalog.py      process/segment lineage, leases, committed epochs
nanobot/audit/emitter.py      fail-open collection gateway and health accounting
nanobot/audit/hook.py         internal Runner AuditHook
nanobot/audit/index.py        disposable SQLite index and rebuild
nanobot/audit/query.py        typed filters and TraceView reconstruction
nanobot/audit/verify.py       hash, reference, lifecycle, and tail reports
nanobot/audit/export.py       sanitized, full, and evidence-bundle streams
nanobot/cli/audit.py          audit command group
```

Existing files receive narrow changes:

- `nanobot/config/schema.py`: explicit `AuditConfig`;
- `nanobot/agent/hook.py`: model/attempt/decision lifecycle callbacks;
- `nanobot/agent/runner.py`: internal AuditHook composition and guaranteed terminal callbacks;
- `nanobot/providers/base.py`: leaf attempt and retry observation contract; remove raw response
  content from retry logs;
- `nanobot/providers/fallback_provider.py`: fallback/circuit route events and safe logs;
- `nanobot/agent/loop.py`: Trace/Turn context, `/stop` target propagation, checkpoint boundaries;
- `nanobot/command/builtin.py`: cancellation request fact;
- `nanobot/channels/manager.py`: delivery attempts, retries, suppression, and final adapter outcome;
- `nanobot/agent/tools/long_task.py`: Goal persistence facts;
- `nanobot/agent/subagent.py`: child Run context propagation;
- automation turn coordinators: source identity and context propagation;
- SDK construction path: source identity and optional parent context;
- CLI registration: `nanobot audit` command group;
- `.gitignore` and deployment examples: exclude/persist the audit root correctly.

Tests mirror these ownership boundaries under `tests/audit/` plus focused Runner, Loop, command,
provider retry, subagent, automation, SDK, CLI, and configuration integration tests.

## 17. Upstream Synchronization Risk

The highest-conflict files are `nanobot/agent/runner.py`, `nanobot/agent/loop.py`,
`nanobot/agent/hook.py`, `nanobot/providers/base.py`, `nanobot/providers/fallback_provider.py`,
`nanobot/channels/manager.py`, `nanobot/config/schema.py`, and `nanobot/command/builtin.py`. They
are active core paths and should receive small commits with focused tests rather than one broad
implementation diff.

Risk controls:

- add the isolated audit package and tests before wiring core call sites;
- extend lifecycle interfaces additively;
- avoid changing existing Session, WebUI transcript, or RuntimeEventBus schemas;
- instrument leaf Provider calls and fallback routing without duplicating attempt events at
  outer retry wrappers;
- keep audit disabled-path behavior byte-for-byte equivalent where practical;
- split configuration, storage, Runner instrumentation, Loop boundaries, query/index, and CLI
  into independently reviewable changes;
- rebase core-path commits early when syncing upstream;
- preserve schema v1 fixtures across refactors.

## 18. Downstream Contract

Golden trajectory, reusable trajectory, evaluation, and skill-mining systems consume verified
TraceView or evidence bundles. They must:

- reference source Trace IDs, event IDs, payload hashes, schema version, and exporter version;
- store labels, scores, reviewer decisions, normalization, and derived artifacts outside the
  append-only audit source;
- reject or explicitly flag `degraded`, `invalid`, `incomplete`, or `unknown` evidence;
- never write curation results back into existing audit events;
- treat public reasoning summaries as optional evidence and never require hidden chain of
  thought;
- preserve provenance when a selected trajectory becomes a skill or reusable template.

This contract makes audit evidence stable while allowing downstream curation methods to evolve.
