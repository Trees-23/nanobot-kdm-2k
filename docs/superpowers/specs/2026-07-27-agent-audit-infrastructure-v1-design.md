# Agent Audit Infrastructure V1 Design

**Status:** Approved design

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
- preserve a lightweight sanitized event stream and a separate full payload stream;
- permanently retain both streams unless an operator explicitly deletes them;
- irreversibly remove credentials before any audit content reaches disk;
- record public provider reasoning summaries without claiming access to hidden chain of
  thought;
- survive process interruption without rewriting valid prior evidence;
- detect deletion, modification, reordering, broken references, conflicting terminal
  states, and truncated JSONL tails;
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
- store raw HTTP requests, response headers, or credentials;
- infer hidden model reasoning or invent causal explanations not present in public reasoning
  summaries or deterministic runtime decisions;
- use Session, WebUI transcript, or RuntimeEventBus records as the audit source of truth;
- provide cryptographic signatures or protection against an attacker who can replace the
  evidence and all trusted hash anchors together.

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

## 4. Approved Decisions

- Build the audit evidence foundation before golden trace or skill extraction systems.
- Cover all AgentRunner sources rather than only user-visible turns.
- Use a four-level identity model: Session, Trace, Turn, and Run.
- Correlate traces only through deterministic rules, never an LLM semantic guess.
- Use Hook-led collection with explicit boundary emitters for facts outside Runner.
- Store sanitized events and complete credential-scrubbed payloads in separate JSONL streams.
- Keep payloads as inline JSONL records rather than content-addressed files.
- Retain events and payloads permanently by default.
- Store payloads as plaintext with strict filesystem permissions.
- Never persist credentials, even in full payload mode.
- Record semantic model milestones and final content, not every streaming delta.
- Record tool side-effect evidence where a capability-specific adapter can verify it.
- Use per-segment hash chains and payload hashes for tamper detection.
- Use append and flush per record, with fsync at critical boundaries and periodically.
- Give each process its own event and payload segments.
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
/stop boundary ───────┘        ▼
                         Validate and classify
                                  │
                                  ▼
                           Redact credentials
                                  │
                                  ▼
                         Split event and payload
                                  │
                                  ▼
                       Hash and append to segments
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
             events JSONL                payloads JSONL
                    └─────────────┬─────────────┘
                                  ▼
                       rebuildable SQLite index
                                  │
                    ┌─────────────┴─────────────┐
                    ▼                           ▼
              Python query API           nanobot audit CLI
```

### 6.1 Collection ownership

Each fact has one authoritative producer:

| Owner | Facts |
|---|---|
| TraceContextResolver | Trace creation, deterministic inheritance, Turn identity |
| AgentLoop and Command Router | Turn start/end, canonical outbound response, command-only turns, `/stop` request and targets |
| Runner AuditHook | Run, iteration, logical model call, reasoning summary, tool lifecycle, Run terminal outcome |
| Provider retry boundary | Provider attempts, retry classification, retry delay |
| Checkpoint persistence boundary | Checkpoint written, restored, or cleared after the operation succeeds |
| Goal persistence boundary | Goal created, updated, completed, blocked, or cancelled |
| AuditEmitter | Schema validation, timestamps, redaction, split, hash, sequence, persistence, degradation accounting |

Loop does not reconstruct model details. Runner does not guess whether a Turn has completed.
Query code does not infer states from human-readable log strings.

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
  "session_key": "websocket:abc",
  "source_type": "websocket",
  "source_metadata": {},
  "iteration": 2,
  "segment_id": "01982...",
  "segment_sequence": 42,
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

### 7.2 Event types

| Domain | V1 events |
|---|---|
| Segment | `segment_started`, `segment_closed` |
| Audit health | `audit_degraded`, `audit_recovered` |
| Trace | `trace_created`, `trace_linked` |
| Turn | `turn_started`, `input_injected`, `cancel_requested`, `turn_finished` |
| Run | `run_started`, `run_finished`, `orphan_run_suspected`, `orphan_run_detected` |
| Iteration | `iteration_started`, `iteration_finished` |
| Model | `model_request_started`, `model_first_output`, `model_response_received`, `model_request_failed` |
| Provider attempt | `model_attempt_started`, `model_attempt_finished`, `retry_scheduled` |
| Reasoning | `reasoning_summary_received` |
| Tool | `tool_started`, `tool_finished` |
| Runtime decision | `policy_blocked`, `continuation_requested`, `finalization_requested` |
| Checkpoint | `checkpoint_written`, `checkpoint_restored`, `checkpoint_cleared` |
| Goal | `goal_created`, `goal_updated`, `goal_completed`, `goal_blocked`, `goal_cancelled` |

Runtime decision events report only deterministic nanobot decisions. A changed tool choice after
an error is visible in the timeline, but the audit layer does not claim why the model changed
strategy unless a public reasoning summary says so.

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

- A Run has one `run_started` and at most one `run_finished`.
- An orphaned Run can remain open until reconciliation has sufficient evidence.
- `orphan_run_detected` is terminal reconciliation evidence for a Run that cannot write its own
  `run_finished`; a later `run_finished` for that Run is a lifecycle conflict.
- A started tool call has exactly one `tool_finished` terminal event.
- A logical model call has one request start and one response or request failure.
- Each provider attempt has one attempt start and one attempt finish.
- A Turn can have zero Runs, as with a command-only `/stop` Turn.
- A Turn can have multiple Runs, as with a main agent and subagents.
- A Trace is a correlation container and does not require a terminal event.
- Iteration, model, and tool execution events cannot appear after a Run terminal event.
  Turn-level response delivery and checkpoint cleanup may follow because AgentLoop owns those
  boundaries after Runner has returned.
- Payload references must identify an existing payload whose hash matches the event.
- Unknown optional fields are retained by readers. Unknown event types are surfaced rather
  than discarded, allowing forward-compatible inspection.

Validation occurs twice. Write-time validation checks schema and required fields. Query-time
verification checks cross-event lifecycle and relationship invariants.

`turn_finished` stores the canonical outbound response prepared for the channel, its delivery
status, and any provider message receipt IDs in a credential-scrubbed payload. This is the
authoritative audit evidence for the final user-visible answer. Automatic consistency scoring
between that answer and prior evidence is deferred to the later evaluation project.

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

### 8.2 Provider retries

A logical model request owns one or more provider attempts:

```text
model_request_started M1
model_attempt_started M1/A1
model_attempt_finished M1/A1, status=timeout
retry_scheduled M1, delay=2s, policy=provider_retry_v1
model_attempt_started M1/A2
model_attempt_finished M1/A2, status=ok
model_response_received M1
```

The full prompt is attached once to M1. Attempts store timing, error classification, provider
error metadata, and retry delay without duplicating the prompt payload.

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
- outbound message tools: sanitized target identity and provider receipt ID;
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

## 9. Redaction and Data Classification

### 9.1 Hard security rule

Credentials never reach disk in any mode. Full payload means full business content after
credential scrubbing, not literal raw input.

Fields are classified as:

```text
public    eligible for event display
internal  eligible for bounded event preview
sensitive stored in payload by default
secret    never persisted
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

If redaction fails, the candidate event and payload are dropped. The implementation must never
fall back to writing unredacted input.

### 9.3 Data access defaults

- Query and export default to sanitized events without payloads.
- Payload access requires `include_payloads=True` or `--include-payloads`/`--mode full`.
- Full access emits a warning that permanent plaintext payloads may contain business-private
  information.
- Application logs never print payloads, prompts, tool arguments, tool results, or failed
  redaction inputs.

## 10. Physical Storage

### 10.1 Directory layout

```text
runtime/audit/v1/
├── events/YYYY-MM-DD/<instance>-<pid>-<segment>.jsonl
├── payloads/YYYY-MM-DD/<instance>-<pid>-<segment>.jsonl
└── state/
    ├── process-instances/
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

### 10.3 Write protocol

For an event with a payload:

1. Build an in-memory candidate fact.
2. Validate required structure.
3. Redact credentials and classify fields.
4. Build and hash the payload record.
5. Append and flush the payload record.
6. Build the lightweight event with payload ID and hash.
7. Chain, append, and flush the event record.
8. Fsync when the event is a critical boundary or the periodic fsync deadline is due.

Payload-first order ensures a crash can create an orphan payload but not a valid event that
points to an unwritten payload. Orphan payloads are reported by verification and retained until
an explicit operator action outside automatic audit flows removes them. V1 does not include an
automatic purge path.

Critical fsync boundaries are:

- `segment_started` and `segment_closed`;
- `run_started` and `run_finished`;
- `tool_finished`;
- checkpoint write, restore, and clear;
- cancellation request and resulting Run terminal event;
- audit degradation and recovery.

Other records are fsynced at least every 5 seconds or 100 records, whichever occurs first.
These values are explicit configuration defaults.

### 10.4 Segment lifecycle and size

Each process writes independent event and payload segments. It rotates a segment at UTC date
change or 64 MiB target size. A single payload larger than 64 MiB is written to a dedicated
one-record segment without truncation and immediately closed and fsynced.

The writer never edits a closed segment. On a malformed trailing line after a crash, readers
preserve every prior valid record, report `truncated_tail`, and the next writer starts a new
segment instead of repairing the old file.

### 10.5 Integrity

Each segment has its own monotonically increasing sequence and SHA-256 chain. Hash input uses
canonical UTF-8 JSON with sorted keys and no insignificant whitespace, excluding only the
record's own hash field.

Per-process chains deliberately avoid a global sequence and cross-process lock. Cross-segment
causality is reconstructed through Trace, Turn, Run, parent, resume, and cause IDs.

Hash chains detect accidental or silent modification but are not signatures. Evidence bundle
manifests expose segment and record hashes so a later system can add trusted signatures without
changing schema v1 records.

## 11. Durability, Failure, and Recovery

### 11.1 Fail-open behavior

Audit failures do not propagate into Agent behavior. The audit hook never changes content and
its emitter catches all persistence failures.

On disk-full, permission, serialization, flush, or fsync failure:

- the current audit record is considered lost unless write completion is known;
- an in-memory health counter records reason, first failure time, last failure time, and count;
- application logging emits a throttled message containing no payload data;
- Agent execution continues;
- the next successful audit write emits `audit_degraded`, followed by `audit_recovered`, with
  loss counts and failure windows but no fabricated missing evidence.

Audit degradation is visible in Trace integrity status. A degraded Trace cannot silently qualify
as complete source evidence.

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
owner is confirmed dead or a checkpoint is explicitly restored, it writes
`orphan_run_detected`; a new Run receives `resumed_from_run_id`.

The old Run is classified as interrupted during the reconstructed view. The reconciler never
backdates or inserts a fictional event at the crash instant.

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
action outside normal Agent execution.

## 13. Query and Index Design

### 13.1 Rebuildable index

JSONL is authoritative. `state/index.sqlite` is a disposable derived index containing:

- segment scan cursors and integrity status;
- event identifiers and lightweight filter columns;
- Trace, Turn, Run, parent, resume, and cause relationships;
- model, tool, status, source, Session, and time fields;
- payload references and sizes, but not full payload content.

The indexer incrementally scans closed segments and the stable prefix of open segments. Index
loss or corruption triggers a rebuild from JSONL. Query can fall back to streaming scans and
must disclose when the index is incomplete or stale.

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
- `evidence-bundle`: original matching JSONL records, payload records, chain witness entries for
  omitted records, segment manifest and hashes, exporter version, and verification report.

A chain witness contains sequence, previous hash, and record hash without unrelated record
content. It lets the bundle preserve the selected records' positions in each source chain
without exporting unrelated sensitive payloads. Because V1 hashes are not externally signed,
the bundle proves internal consistency against its manifest, not third-party authorship.

An export is source material, not a golden trace. Later curation stores annotations, scores,
review decisions, and skill artifacts separately and references immutable evidence hashes.

## 14. Example Traces

Examples omit repeated envelope fields and some routine iteration/checkpoint events for
readability. Fixture files used by tests contain every emitted event and the complete schema.

### 14.1 Normal tool success

```jsonl
{"event_type":"trace_created","trace_id":"T1"}
{"event_type":"turn_started","trace_id":"T1","turn_id":"U1"}
{"event_type":"run_started","trace_id":"T1","turn_id":"U1","run_id":"R1"}
{"event_type":"iteration_started","run_id":"R1","iteration":0}
{"event_type":"model_request_started","run_id":"R1","model_call_id":"M1","payload_id":"P1"}
{"event_type":"model_attempt_started","model_call_id":"M1","attempt":1}
{"event_type":"model_attempt_finished","model_call_id":"M1","attempt":1,"status":"ok"}
{"event_type":"model_response_received","run_id":"R1","model_call_id":"M1","status":"ok","payload_id":"P2"}
{"event_type":"checkpoint_written","run_id":"R1","checkpoint_id":"K1","payload_id":"P4"}
{"event_type":"tool_started","run_id":"R1","tool_call_id":"C1","tool":"read_file","payload_id":"P3"}
{"event_type":"tool_finished","run_id":"R1","tool_call_id":"C1","tool":"read_file","status":"ok","payload_id":"P5"}
{"event_type":"iteration_finished","run_id":"R1","iteration":0,"status":"continued"}
{"event_type":"iteration_started","run_id":"R1","iteration":1}
{"event_type":"model_request_started","run_id":"R1","model_call_id":"M2","payload_id":"P6"}
{"event_type":"model_attempt_started","model_call_id":"M2","attempt":1}
{"event_type":"model_attempt_finished","model_call_id":"M2","attempt":1,"status":"ok"}
{"event_type":"model_response_received","run_id":"R1","model_call_id":"M2","status":"ok","payload_id":"P7"}
{"event_type":"iteration_finished","run_id":"R1","iteration":1,"status":"completed"}
{"event_type":"run_finished","run_id":"R1","status":"succeeded","stop_reason":"completed"}
{"event_type":"checkpoint_cleared","run_id":"R1","checkpoint_id":"K1"}
{"event_type":"turn_finished","turn_id":"U1","status":"responded","payload_id":"P8"}
```

Expected reconstruction: one Trace, one Turn, one successful Run, two model calls, one paired
tool call, valid payload references, and a valid integrity report.

### 14.2 Recoverable failures and policy block

```jsonl
{"event_type":"trace_created","trace_id":"T2"}
{"event_type":"turn_started","trace_id":"T2","turn_id":"U2"}
{"event_type":"run_started","trace_id":"T2","turn_id":"U2","run_id":"R2"}
{"event_type":"model_request_started","run_id":"R2","model_call_id":"M3"}
{"event_type":"model_attempt_started","model_call_id":"M3","attempt":1}
{"event_type":"model_attempt_finished","model_call_id":"M3","attempt":1,"status":"timeout"}
{"event_type":"retry_scheduled","model_call_id":"M3","delay_ms":2000,"policy":"provider_retry_v1"}
{"event_type":"model_attempt_started","model_call_id":"M3","attempt":2}
{"event_type":"model_attempt_finished","model_call_id":"M3","attempt":2,"status":"ok"}
{"event_type":"model_response_received","model_call_id":"M3","status":"ok"}
{"event_type":"tool_started","run_id":"R2","tool_call_id":"C2","tool":"web_search"}
{"event_type":"tool_finished","run_id":"R2","tool_call_id":"C2","status":"error","outcome_reason":"provider_error"}
{"event_type":"tool_started","run_id":"R2","tool_call_id":"C3","tool":"web_search"}
{"event_type":"tool_finished","run_id":"R2","tool_call_id":"C3","status":"error","outcome_reason":"provider_error"}
{"event_type":"tool_started","run_id":"R2","tool_call_id":"C4","tool":"web_search"}
{"event_type":"policy_blocked","run_id":"R2","tool_call_id":"C4","policy":"repeated_external_lookup_v1","count":3,"limit":2}
{"event_type":"tool_finished","run_id":"R2","tool_call_id":"C4","status":"blocked","outcome_reason":"repeated_external_lookup"}
{"event_type":"model_request_started","run_id":"R2","model_call_id":"M4"}
{"event_type":"model_attempt_started","model_call_id":"M4","attempt":1}
{"event_type":"model_attempt_finished","model_call_id":"M4","attempt":1,"status":"ok"}
{"event_type":"model_response_received","run_id":"R2","model_call_id":"M4","status":"ok"}
{"event_type":"run_finished","run_id":"R2","status":"succeeded","stop_reason":"completed"}
{"event_type":"turn_finished","turn_id":"U2","status":"responded"}
```

Expected reconstruction: Provider attempt retry, two failed tools, one deterministic block, and
a successful Run. Tool failure rate and Run success rate remain distinct.

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
{"event_type":"turn_finished","turn_id":"U5","status":"responded"}
```

Expected reconstruction: the control Turn caused cancellation of R3, R4 resumed from R3 under
the same Trace, and each Run has an independent terminal outcome.

## 15. Testing and Acceptance

### 15.1 Unit tests

- schema validation and forward-compatible unknown fields;
- deterministic Trace inheritance rules;
- recursive and textual credential redaction;
- event/payload split and payload-first write protocol;
- canonical hashes and chain verification;
- append, flush, critical fsync, periodic fsync, and rotation;
- truncated-tail reading without rewriting evidence;
- lifecycle verification for duplicate or missing terminal events;
- index incremental update, stale detection, and rebuild;
- TraceView causal reconstruction and pagination;
- sanitized, full, and evidence-bundle export.

### 15.2 Security and fault injection

Canary credentials are planted in nested JSON, free text, HTTP headers, Shell commands and
environment, MCP output, provider errors, checkpoints, and source metadata. The original value
must not appear in events, payloads, SQLite, exports, or application logs.

Fault injection covers ENOSPC, EACCES, partial writes, serialization failure, flush failure,
fsync failure, redactor failure, deleted records, modified payloads, reordered sequences,
truncated tails, multiple writers, parallel tools, subagents, and process kill.

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
8. Process kill during a tool followed by checkpoint restoration.
9. Goal create, update, complete, block, and cancel.
10. No reasoning, one-shot reasoning summary, and streamed reasoning summary.
11. Main agent with concurrent subagents.
12. Cron, local trigger, ephemeral, and SDK execution sources.

Each scenario must be reconstructable through one Trace ID and must expose the correct final Run
status and stop reason.

### 15.4 Compatibility and scale

- POSIX and Windows path and permission behavior is covered.
- Writer memory is bounded by the current event or payload, not historical audit size.
- A one-million-event synthetic corpus uses the index for paginated queries.
- Performance benchmarks are recorded, but fragile absolute wall-clock thresholds are not hard
  CI gates across different hardware.
- Schema v1 fixtures remain readable in later versions.

### 15.5 V1 completion gate

V1 is complete only when:

- every acceptance scenario reconstructs from one Trace ID;
- verifier detects deletion, modification, reorder, missing payloads, conflicting terminals,
  dangling relationships, and truncated tails;
- canary credential values have zero persistence leakage;
- kill tests preserve prior valid evidence and correctly link resumed Runs;
- audit failure never changes Agent behavior;
- Python API and CLI can query, verify, and export the resulting evidence;
- the three fixed schema v1 fixtures produce their expected TraceView and verification report.

## 16. Minimal Change Surface

New focused modules should live under `nanobot/audit/`:

```text
nanobot/audit/schema.py       event and payload models, enums, validation
nanobot/audit/context.py      Trace/Turn/Run context and deterministic resolver
nanobot/audit/redaction.py    classification and credential removal
nanobot/audit/integrity.py    canonical hashing and verification primitives
nanobot/audit/store.py        process segments, write protocol, rotation, durability
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
- `nanobot/providers/base.py`: provider attempt and retry observation contract;
- `nanobot/agent/loop.py`: Trace/Turn context, `/stop` target propagation, checkpoint boundaries;
- `nanobot/command/builtin.py`: cancellation request fact;
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
`nanobot/agent/hook.py`, `nanobot/providers/base.py`, `nanobot/config/schema.py`, and
`nanobot/command/builtin.py`. They are active core paths and should receive small commits with
focused tests rather than one broad implementation diff.

Risk controls:

- add the isolated audit package and tests before wiring core call sites;
- extend lifecycle interfaces additively;
- avoid changing existing Session, WebUI transcript, or RuntimeEventBus schemas;
- avoid provider-specific edits unless a provider bypasses the shared attempt boundary;
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
- reject or explicitly flag `degraded`, `invalid`, or `incomplete` evidence;
- never write curation results back into existing audit events;
- treat public reasoning summaries as optional evidence and never require hidden chain of
  thought;
- preserve provenance when a selected trajectory becomes a skill or reusable template.

This contract makes audit evidence stable while allowing downstream curation methods to evolve.
