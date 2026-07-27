# Agent Audit V1 Core Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the typed, credential-aware, append-only audit evidence library without wiring it into AgentRunner.

**Architecture:** Producers submit typed, redacted commit items to one per-process writer. The writer durably commits payloads before referencing events, records committed offsets in a chained process catalog, abandons uncertain segments, and exposes a committed-prefix reader and verifier.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, pathlib, hashlib/secrets/uuid, JSONL, filelock, pytest/pytest-asyncio.

---

## File Structure

| File | Responsibility |
|---|---|
| `nanobot/audit/ids.py` | UUIDv7 generation for all audit identities |
| `nanobot/audit/types.py` | Closed V1 enums and JSON value aliases |
| `nanobot/audit/schema.py` | Draft, persisted event, payload, and catalog contracts |
| `nanobot/audit/integrity.py` | Canonical serialization and record hash primitives |
| `nanobot/audit/redaction.py` | Recognized-secret redaction and classification reports |
| `nanobot/audit/segments.py` | Exclusive JSONL segment creation, append, fsync, and sealing |
| `nanobot/audit/catalog.py` | Per-process committed epochs and cross-segment lineage |
| `nanobot/audit/lease.py` | Mutable process liveness hint, separate from evidence |
| `nanobot/audit/writer.py` | The sole process writer, queue, batching, and durability order |
| `nanobot/audit/emitter.py` | Fail-open redaction, metadata mode, and degradation accounting |
| `nanobot/audit/reader.py` | Catalog-first reads of authoritative committed prefixes |
| `nanobot/audit/verify.py` | Hash, reference, lineage, and lifecycle verification |
| `nanobot/audit/__init__.py` | Stable public Phase 1 interfaces only |

Configuration changes remain in `nanobot/config/`; tests mirror each audit module under
`tests/audit/`. Keep storage mechanics out of producers so no caller can assign sequences, hashes,
epochs, or durable offsets.

### Task 1: Audit configuration and runtime path

**Files:**
- Create: `nanobot/audit/__init__.py`
- Modify: `nanobot/config/schema.py:403-417`
- Modify: `nanobot/config/paths.py:20-49`
- Modify: `nanobot/config/__init__.py:1-31`
- Test: `tests/config/test_audit_config.py`
- Test: `tests/config/test_config_paths.py`

- [ ] **Step 1: Write failing configuration tests**

```python
from pathlib import Path

import pytest

from nanobot.config.schema import AuditConfig, Config


def test_audit_config_defaults_to_full() -> None:
    config = Config()
    assert config.audit.mode == "full"
    assert config.audit.segment_max_bytes == 67_108_864
    assert config.audit.writer_queue_capacity == 4096
    assert config.audit.writer_queue_max_bytes == 268_435_456


def test_audit_config_accepts_camel_case() -> None:
    config = Config.model_validate({
        "audit": {"criticalAckTimeoutMs": 1500, "previewMaxChars": 800}
    })
    assert config.audit.critical_ack_timeout_ms == 1500
    assert config.audit.preview_max_chars == 800


def test_audit_config_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        AuditConfig(mode="raw")
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `pytest tests/config/test_audit_config.py -v`

Expected: collection fails because `AuditConfig` does not exist.

- [ ] **Step 3: Add the exact configuration model**

```python
class AuditConfig(Base):
    mode: Literal["full", "metadata_only", "off"] = "full"
    path: str | None = None
    segment_max_bytes: int = Field(default=67_108_864, ge=1_048_576)
    fsync_interval_seconds: float = Field(default=5.0, gt=0)
    fsync_record_interval: int = Field(default=100, ge=1)
    writer_queue_capacity: int = Field(default=4096, ge=1)
    writer_queue_max_bytes: int = Field(default=268_435_456, ge=1_048_576)
    enqueue_timeout_ms: int = Field(default=25, ge=0)
    critical_ack_timeout_ms: int = Field(default=2000, ge=1)
    preview_max_chars: int = Field(default=512, ge=64)
    index_enabled: bool = True
    warn_plaintext_payloads: bool = True
    additional_secret_keys: list[str] = Field(default_factory=list)
    additional_secret_patterns: list[str] = Field(default_factory=list)


class Config(BaseSettings):
    # existing fields stay unchanged
    audit: AuditConfig = Field(default_factory=AuditConfig)
```

Add `get_audit_dir` to `nanobot/config/paths.py`:

```python
def get_audit_dir(configured: str | None = None) -> Path:
    """Return the instance audit root outside the agent workspace."""
    if configured:
        return ensure_dir(Path(configured).expanduser())
    return ensure_dir(get_runtime_subdir("audit") / "v1")
```

Export `get_audit_dir` from `nanobot/config/__init__.py`.

- [ ] **Step 4: Add the path test**

```python
def test_audit_dir_follows_instance_config(monkeypatch, tmp_path: Path) -> None:
    config_file = tmp_path / "instance" / "config.json"
    monkeypatch.setattr("nanobot.config.paths.get_config_path", lambda: config_file)

    assert get_audit_dir() == config_file.parent / "audit" / "v1"
    assert get_audit_dir(str(tmp_path / "custom")) == tmp_path / "custom"
```

- [ ] **Step 5: Run tests and lint**

Run: `pytest tests/config/test_audit_config.py tests/config/test_config_paths.py -v`

Expected: PASS.

Run: `ruff check nanobot/config tests/config/test_audit_config.py`

Expected: `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add nanobot/audit/__init__.py nanobot/config/schema.py nanobot/config/paths.py \
  nanobot/config/__init__.py tests/config/test_audit_config.py tests/config/test_config_paths.py
git commit -m "feat(audit): add audit configuration and runtime path"
```

### Task 2: UUIDv7 identifiers and closed enums

**Files:**
- Create: `nanobot/audit/ids.py`
- Create: `nanobot/audit/types.py`
- Test: `tests/audit/test_ids.py`
- Test: `tests/audit/test_types.py`

- [ ] **Step 1: Write failing UUID and enum tests**

```python
import uuid

from nanobot.audit.ids import new_audit_id
from nanobot.audit.types import EventType, IntegrityStatus, PayloadKind, RunStatus


def test_new_audit_id_is_uuid7() -> None:
    value = uuid.UUID(new_audit_id())
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_closed_enums_reject_unknown_values() -> None:
    assert RunStatus("succeeded") is RunStatus.SUCCEEDED
    assert IntegrityStatus("incomplete") is IntegrityStatus.INCOMPLETE
    assert PayloadKind("model_request") is PayloadKind.MODEL_REQUEST
    assert EventType("tool_finished") is EventType.TOOL_FINISHED
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_ids.py tests/audit/test_types.py -v`

Expected: import errors for the new audit modules.

- [ ] **Step 3: Implement UUIDv7 without a new dependency**

```python
from __future__ import annotations

import secrets
import time
import uuid


def new_audit_id(*, timestamp_ms: int | None = None) -> str:
    ts = int(time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms)
    if not 0 <= ts < 1 << 48:
        raise ValueError("UUIDv7 timestamp must fit in 48 bits")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    value = ts << 80
    value |= 0x7 << 76
    value |= random_a << 64
    value |= 0b10 << 62
    value |= random_b
    return str(uuid.UUID(int=value))
```

- [ ] **Step 4: Add the closed string enums**

In `nanobot/audit/types.py`, define `StrEnum` classes for every closed set in Design sections
7.2 and 7.3. The event enum must contain these exact values:

```python
class EventType(StrEnum):
    PROCESS_INSTANCE_STARTED = "process_instance_started"
    PROCESS_INSTANCE_CLOSED = "process_instance_closed"
    SEGMENT_STARTED = "segment_started"
    SEGMENT_CLOSED = "segment_closed"
    AUDIT_DEGRADED = "audit_degraded"
    AUDIT_RECOVERED = "audit_recovered"
    TRACE_CREATED = "trace_created"
    TRACE_LINKED = "trace_linked"
    TURN_STARTED = "turn_started"
    INPUT_INJECTED = "input_injected"
    CANCEL_REQUESTED = "cancel_requested"
    TURN_RESPONSE_PREPARED = "turn_response_prepared"
    TURN_FINISHED = "turn_finished"
    RETURNED_TO_CALLER = "returned_to_caller"
    DELIVERY_ATTEMPTED = "delivery_attempted"
    DELIVERY_RETRY_SCHEDULED = "delivery_retry_scheduled"
    DELIVERY_FINISHED = "delivery_finished"
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    ORPHAN_RUN_SUSPECTED = "orphan_run_suspected"
    ORPHAN_RUN_DETECTED = "orphan_run_detected"
    ORPHAN_MODEL_CALL_DETECTED = "orphan_model_call_detected"
    ORPHAN_TOOL_DETECTED = "orphan_tool_detected"
    ITERATION_STARTED = "iteration_started"
    ITERATION_FINISHED = "iteration_finished"
    MODEL_REQUEST_STARTED = "model_request_started"
    MODEL_FIRST_OUTPUT = "model_first_output"
    MODEL_RESPONSE_RECEIVED = "model_response_received"
    MODEL_REQUEST_FAILED = "model_request_failed"
    PROVIDER_ROUTE_DECISION = "provider_route_decision"
    MODEL_ATTEMPT_STARTED = "model_attempt_started"
    MODEL_ATTEMPT_FINISHED = "model_attempt_finished"
    RETRY_SCHEDULED = "retry_scheduled"
    REASONING_SUMMARY_RECEIVED = "reasoning_summary_received"
    TOOL_STARTED = "tool_started"
    TOOL_FINISHED = "tool_finished"
    POLICY_BLOCKED = "policy_blocked"
    CONTINUATION_REQUESTED = "continuation_requested"
    FINALIZATION_REQUESTED = "finalization_requested"
    CHECKPOINT_WRITTEN = "checkpoint_written"
    CHECKPOINT_RESTORED = "checkpoint_restored"
    CHECKPOINT_CLEARED = "checkpoint_cleared"
    GOAL_CREATED = "goal_created"
    GOAL_UPDATED = "goal_updated"
    GOAL_COMPLETED = "goal_completed"
    GOAL_BLOCKED = "goal_blocked"
    GOAL_CANCELLED = "goal_cancelled"
```

Also define `PayloadKind`, `ToolStatus`, `RunStatus`, `IntegrityStatus`, `AuditMode`,
`ProviderRouteAction`, `DeliveryStatus`, and `CatalogRecordType` exactly as closed by the Design.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/audit/test_ids.py tests/audit/test_types.py -v`

Expected: PASS.

```bash
git add nanobot/audit/ids.py nanobot/audit/types.py tests/audit/test_ids.py \
  tests/audit/test_types.py
git commit -m "feat(audit): add audit identifiers and enums"
```

### Task 3: Typed event, payload, and catalog models

**Files:**
- Create: `nanobot/audit/schema.py`
- Test: `tests/audit/test_schema.py`

- [ ] **Step 1: Write schema contract tests before models**

```python
from pydantic import TypeAdapter, ValidationError
import pytest

from nanobot.audit.schema import AuditEvent, ToolFinishedEvent, audit_event_adapter
from nanobot.audit.types import EventType


def test_tool_finished_requires_tool_identity() -> None:
    with pytest.raises(ValidationError):
        ToolFinishedEvent.model_validate(base_event(event_type="tool_finished"))


def test_event_adapter_round_trips_unknown_future_field() -> None:
    raw = {
        **base_event(event_type="tool_finished"),
        "tool_call_id": "call-1",
        "tool_name": "exec",
        "elapsed_ms": 12,
        "status": "ok",
        "future_field": {"kept": True},
    }
    event = audit_event_adapter.validate_python(raw)
    assert event.model_dump()["future_field"] == {"kept": True}


def test_every_event_enum_has_a_model() -> None:
    assert set(EVENT_MODELS) == set(EventType)
```

Provide `base_event()` in the test with every common required field and all nullable domain IDs
set explicitly to `None`.

- [ ] **Step 2: Run and confirm failure**

Run: `pytest tests/audit/test_schema.py -v`

Expected: import failure for `nanobot.audit.schema`.

- [ ] **Step 3: Implement typed drafts separately from persisted envelopes**

```python
class AuditEventDraftBase(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal[1] = 1
    event_id: str
    event_type: EventType
    occurred_at: datetime
    monotonic_ns: int
    trace_id: str | None
    turn_id: str | None
    run_id: str | None
    parent_run_id: str | None
    resumed_from_run_id: str | None
    caused_by_event_id: str | None
    model_call_id: str | None
    attempt_id: str | None
    tool_call_id: str | None
    checkpoint_id: str | None
    goal_id: str | None
    delivery_id: str | None
    session_key: str | None
    source_type: str | None
    source_metadata: dict[str, JsonValue]
    iteration: int | None


class AuditEventBase(AuditEventDraftBase):
    model_config = ConfigDict(extra="allow", use_enum_values=True)
    process_instance_id: str
    segment_id: str
    segment_sequence: int
    durability_epoch: int
    previous_event_hash: str | None
    status: str | None
    outcome_reason: str | None
    stop_reason: str | None
    payload_id: str | None
    payload_sha256: str | None
    event_hash: str
```

Create one explicit `*Draft` subclass per event in Design section 7.2, with
`event_type: Literal[...]` and the exact required semantic fields from the table. Create a matching
persisted `*Event` model that adds Writer-owned persistence fields. Producers construct Drafts;
only the single Writer materializes persisted Events. Register persisted classes in
`EVENT_MODELS` and expose:

```python
AuditEvent = Annotated[
    ProcessInstanceStartedEvent
    | ProcessInstanceClosedEvent
    | SegmentStartedEvent
    | SegmentClosedEvent
    | AuditDegradedEvent
    | AuditRecoveredEvent
    | TraceCreatedEvent
    | TraceLinkedEvent
    | TurnStartedEvent
    | InputInjectedEvent
    | CancelRequestedEvent
    | TurnResponsePreparedEvent
    | TurnFinishedEvent
    | ReturnedToCallerEvent
    | DeliveryAttemptedEvent
    | DeliveryRetryScheduledEvent
    | DeliveryFinishedEvent
    | RunStartedEvent
    | RunFinishedEvent
    | OrphanRunSuspectedEvent
    | OrphanRunDetectedEvent
    | OrphanModelCallDetectedEvent
    | OrphanToolDetectedEvent
    | IterationStartedEvent
    | IterationFinishedEvent
    | ModelRequestStartedEvent
    | ModelFirstOutputEvent
    | ModelResponseReceivedEvent
    | ModelRequestFailedEvent
    | ProviderRouteDecisionEvent
    | ModelAttemptStartedEvent
    | ModelAttemptFinishedEvent
    | RetryScheduledEvent
    | ReasoningSummaryReceivedEvent
    | ToolStartedEvent
    | ToolFinishedEvent
    | PolicyBlockedEvent
    | ContinuationRequestedEvent
    | FinalizationRequestedEvent
    | CheckpointWrittenEvent
    | CheckpointRestoredEvent
    | CheckpointClearedEvent
    | GoalCreatedEvent
    | GoalUpdatedEvent
    | GoalCompletedEvent
    | GoalBlockedEvent
    | GoalCancelledEvent,
    Field(discriminator="event_type"),
]
audit_event_adapter = TypeAdapter(AuditEvent)
```

Expose a parallel `AuditEventDraft` discriminated union and `audit_event_draft_adapter`. Add
`materialize_event(draft, persistence_fields)` and test that Draft validation rejects
`segment_sequence`, `durability_epoch`, previous hash, and event hash supplied by a producer.

- [ ] **Step 4: Implement typed payload and catalog unions**

Use the same explicit-discriminator pattern for all payload kinds and catalog record types. A
payload Draft contains `payload_id`, `event_id`, `payload_kind`, and typed content. The persisted
payload envelope adds Writer-owned fields:

```python
class AuditPayloadBase(BaseModel):
    model_config = ConfigDict(extra="allow", use_enum_values=True)
    schema_version: Literal[1] = 1
    payload_id: str
    event_id: str
    payload_kind: PayloadKind
    process_instance_id: str
    payload_segment_id: str
    payload_segment_sequence: int
    previous_payload_hash: str | None
    payload_hash: str
```

Expose `AuditPayloadDraft`, `AuditPayload`, and `materialize_payload()` with the same ownership
rule. Catalog records are persisted-only because only the Writer creates them.

Implement the exact content fields from Design section 7.2 for `ProcessPayload`,
`AuditHealthPayload`, `TurnInputPayload`, `TurnOutputPayload`, `RunConfigPayload`,
`ModelRequestPayload`, `ModelResponsePayload`, `ReasoningSummaryPayload`, `ToolInputPayload`,
`ToolOutputPayload`, `CheckpointPayload`, `GoalStatePayload`, and `DeliveryPayload`.

The catalog base is:

```python
class CatalogRecordBase(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)
    catalog_version: Literal[1] = 1
    catalog_record_type: CatalogRecordType
    catalog_record_id: str
    process_instance_id: str
    catalog_segment_id: str
    catalog_sequence: int
    previous_catalog_hash: str | None
    occurred_at: datetime
    catalog_record_hash: str
```

Add explicit models for `process_started`, `segment_registered`, `segment_closed`,
`segment_abandoned`, `epoch_committed`, and `process_closed` using the required fields in Design
section 10.2.

- [ ] **Step 5: Add representative required/nullable tests and registry coverage**

Test at least one event per domain, every payload kind, every catalog record type, and a parameterized
registry test proving no enum value lacks a model.

Run: `pytest tests/audit/test_schema.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nanobot/audit/schema.py tests/audit/test_schema.py
git commit -m "feat(audit): define audit v1 schema contracts"
```

### Task 4: Canonical hashing and chain verification

**Files:**
- Create: `nanobot/audit/integrity.py`
- Test: `tests/audit/test_integrity.py`

- [ ] **Step 1: Write failing canonical-hash tests**

```python
from nanobot.audit.integrity import canonical_json_bytes, hash_record, verify_chain


def test_canonical_json_ignores_dict_insertion_order() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes({"a": 1, "b": 2})


def test_hash_record_excludes_own_hash_field() -> None:
    record = {"event_id": "e1", "event_hash": "old", "value": 3}
    assert hash_record(record, hash_field="event_hash") == hash_record(
        {**record, "event_hash": "different"}, hash_field="event_hash"
    )


def test_verify_chain_reports_deleted_middle_record() -> None:
    records = chained_records(3)
    report = verify_chain([records[0], records[2]], hash_field="event_hash")
    assert report.valid is False
    assert report.error_code == "previous_hash_mismatch"
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_integrity.py -v`

Expected: import failure.

- [ ] **Step 3: Implement canonical JSON and SHA-256**

```python
def canonical_json_bytes(value: JsonValue) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def hash_record(record: Mapping[str, JsonValue], *, hash_field: str) -> str:
    payload = {key: value for key, value in record.items() if key != hash_field}
    return "sha256:" + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
```

Pydantic records must call `model_dump(mode="json")` before hashing so datetime and enum values
have stable JSON representations. Add a timezone-aware datetime test and reject naive datetimes
at schema validation.

Implement `verify_chain` with explicit error codes for sequence gap, previous-hash mismatch,
record-hash mismatch, duplicate sequence, and invalid first predecessor.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_integrity.py -v`

Expected: PASS.

```bash
git add nanobot/audit/integrity.py tests/audit/test_integrity.py
git commit -m "feat(audit): add canonical integrity chains"
```

### Task 5: Recognized-secret redaction

**Files:**
- Create: `nanobot/audit/redaction.py`
- Test: `tests/audit/test_redaction.py`

- [ ] **Step 1: Write failing structured and text tests**

```python
from nanobot.audit.redaction import AuditRedactor


def test_redacts_nested_structured_credentials() -> None:
    redactor = AuditRedactor()
    cleaned, report = redactor.redact({
        "headers": {"Authorization": "Bearer secret"},
        "nested": [{"api_key": "sk-test-123"}],
    })
    assert cleaned["headers"]["Authorization"] == "[REDACTED:CREDENTIAL]"
    assert cleaned["nested"][0]["api_key"] == "[REDACTED:CREDENTIAL]"
    assert report.replacement_count == 2


def test_preserves_unknown_opaque_text() -> None:
    value = "opaque-business-value-123"
    cleaned, report = AuditRedactor().redact(value)
    assert cleaned == value
    assert report.replacement_count == 0


def test_configured_pattern_redacts_deployment_secret() -> None:
    redactor = AuditRedactor(additional_patterns=[r"ACME_[A-Z0-9]{16}"])
    cleaned, _ = redactor.redact("token=ACME_1234567890ABCDEF")
    assert cleaned == "token=[REDACTED:CREDENTIAL]"
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_redaction.py -v`

Expected: import failure.

- [ ] **Step 3: Implement recursive redaction and reports**

```python
@dataclass(frozen=True, slots=True)
class RedactionReport:
    rule_counts: dict[str, int]

    @property
    def replacement_count(self) -> int:
        return sum(self.rule_counts.values())


class AuditRedactor:
    def __init__(
        self,
        *,
        additional_keys: Iterable[str] = (),
        additional_patterns: Iterable[str] = (),
    ) -> None:
        self._secret_keys = {normalize_key(key) for key in (*KNOWN_SECRET_KEYS, *additional_keys)}
        self._patterns = compile_patterns((*BUILTIN_SECRET_PATTERNS, *additional_patterns))

    def redact(self, value: JsonValue) -> tuple[JsonValue, RedactionReport]:
        counts: Counter[str] = Counter()
        return self._walk(value, counts), RedactionReport(dict(counts))
```

Implement `_walk` for dictionaries, lists, strings, JSON scalars, and `None`. Compile configured
regexes at startup and raise `ValueError` for an invalid pattern. Never hash original secrets.

- [ ] **Step 4: Add failure-safety test**

Patch a regex substitution to raise and assert the caller receives `RedactionError` containing
only the rule ID, never the candidate input.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/audit/test_redaction.py -v`

Expected: PASS.

```bash
git add nanobot/audit/redaction.py tests/audit/test_redaction.py
git commit -m "feat(audit): redact recognized credentials"
```

### Task 6: Segment files and process catalog

**Files:**
- Create: `nanobot/audit/segments.py`
- Create: `nanobot/audit/catalog.py`
- Create: `nanobot/audit/lease.py`
- Test: `tests/audit/test_segments.py`
- Test: `tests/audit/test_catalog.py`
- Test: `tests/audit/test_lease.py`

- [ ] **Step 1: Write failing permission, append, and lineage tests**

```python
def test_segment_append_is_jsonl_and_never_rewrites(tmp_path: Path) -> None:
    segment = JsonlSegment.create(tmp_path / "events.jsonl", mode=0o600)
    first = segment.append({"n": 1})
    second = segment.append({"n": 2})
    assert first.start == 0
    assert second.start > first.end
    assert segment.path.read_text().splitlines() == ['{"n":1}', '{"n":2}']


def test_catalog_committed_prefix_comes_only_from_epoch_record(tmp_path: Path) -> None:
    catalog = ProcessCatalog.create(tmp_path, process_instance_id="p1")
    catalog.commit_epoch(epoch_fixture(event_offset=100, payload_offset=200))
    prefix = catalog.last_committed_prefix()
    assert prefix.event_offset == 100
    assert prefix.payload_offset == 200
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_segments.py tests/audit/test_catalog.py -v`

Expected: import failures.

- [ ] **Step 3: Implement low-level JSONL segment ownership**

```python
@dataclass(frozen=True, slots=True)
class AppendReceipt:
    start: int
    end: int


class JsonlSegment:
    def append(self, record: Mapping[str, JsonValue]) -> AppendReceipt:
        if self._sealed:
            raise SegmentSealedError(str(self.path))
        raw = canonical_json_bytes(dict(record)) + b"\n"
        start = self._file.tell()
        written = self._file.write(raw)
        if written != len(raw):
            self._sealed = True
            raise UncertainAppendError(str(self.path))
        return AppendReceipt(start=start, end=start + written)

    def flush(self) -> None:
        self._file.flush()

    def fsync(self) -> None:
        self.flush()
        os.fsync(self._file.fileno())
```

Use exclusive file creation, restrictive POSIX permissions, and a helper that fsyncs parent
directories while tolerating only platform-specific `EINVAL`/unsupported directory fsync.

- [ ] **Step 4: Implement chained catalog records**

`ProcessCatalog` must create/rotate catalog segments, assign sequence/hash, fsync every catalog
record, and expose typed methods:

```python
register_segment(...)
close_segment(...)
abandon_segment(...)
commit_epoch(...)
close_process(...)
last_committed_prefix()
```

Every method validates through the catalog TypeAdapter before append. `commit_epoch` returns only
after `catalog_fd` fsync. Catalog segments link to their predecessor.

- [ ] **Step 5: Add deletion and malformed-tail tests**

Delete a registered segment and assert catalog verification reports `missing_cataloged_segment`.
Append half a JSON line and assert readers return the committed prefix plus `truncated_tail`.

- [ ] **Step 6: Implement mutable process leases separately from evidence**

```python
@dataclass(frozen=True, slots=True)
class ProcessLeaseState:
    process_instance_id: str
    host_fingerprint: str
    boot_id: str
    pid: int
    started_at: datetime
    heartbeat_at: datetime


class ProcessLease:
    def refresh(self, state: ProcessLeaseState) -> None:
        atomic_write_json(self.path, asdict(state), file_mode=0o600, fsync=True)
```

Use a temp file, flush/fsync, `os.replace`, and parent-directory fsync. Lease constants are
`HEARTBEAT_INTERVAL_S = 5` and `STALE_AFTER_S = 30`. Lease files are mutable liveness hints and
must never be included in hash verification as evidence.

- [ ] **Step 7: Run tests and commit**

Run: `pytest tests/audit/test_segments.py tests/audit/test_catalog.py \
tests/audit/test_lease.py -v`

Expected: PASS.

```bash
git add nanobot/audit/segments.py nanobot/audit/catalog.py nanobot/audit/lease.py \
  tests/audit/test_segments.py tests/audit/test_catalog.py tests/audit/test_lease.py
git commit -m "feat(audit): add chained audit segments and catalog"
```

### Task 7: Per-process writer and durability epochs

**Files:**
- Create: `nanobot/audit/writer.py`
- Test: `tests/audit/test_writer.py`
- Test: `tests/audit/test_writer_faults.py`

- [ ] **Step 1: Write failing payload-before-event durability test**

```python
async def test_epoch_fsyncs_payload_before_event_and_catalog(tmp_path: Path) -> None:
    calls: list[str] = []
    writer = writer_with_recording_segments(tmp_path, calls)
    await writer.start()
    await writer.submit(commit_item_with_payload(critical=True))
    await writer.close()

    assert calls.index("payload.fsync") < calls.index("event.append")
    assert calls.index("event.fsync") < calls.index("catalog.epoch_committed")
    assert calls.index("catalog.fsync") > calls.index("catalog.epoch_committed")
```

- [ ] **Step 2: Write failing concurrency and cancellation tests**

Submit 100 items concurrently and assert event sequences are exactly `1..100`, every previous
hash matches, and cancelling submitter tasks does not cancel accepted commit items.

- [ ] **Step 3: Confirm failure**

Run: `pytest tests/audit/test_writer.py tests/audit/test_writer_faults.py -v`

Expected: import failure.

- [ ] **Step 4: Implement writer lifecycle and bounded queue**

```python
@dataclass(frozen=True, slots=True)
class CommitReceipt:
    process_instance_id: str
    durability_epoch: int
    catalog_record_id: str
    event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommitItem:
    event: AuditEventDraftBase
    payload: AuditPayloadDraftBase | None
    estimated_bytes: int
    critical: bool


@dataclass(slots=True)
class _QueuedCommit:
    item: CommitItem
    acknowledgement: asyncio.Future[CommitReceipt] | None


class AuditWriter:
    async def submit(self, item: CommitItem) -> CommitReceipt | None:
        acknowledgement = (
            asyncio.get_running_loop().create_future() if item.critical else None
        )
        queued = _QueuedCommit(item=item, acknowledgement=acknowledgement)
        await self._capacity.acquire(item.estimated_bytes, timeout=self.enqueue_timeout)
        await asyncio.wait_for(self._queue.put(queued), timeout=self.enqueue_timeout)
        if acknowledgement is None:
            return None
        return await asyncio.wait_for(
            asyncio.shield(acknowledgement),
            timeout=self.critical_ack_timeout,
        )
```

Implement count and byte capacity, one exclusive oversized slot, FIFO batching, critical batch
flush, graceful bounded drain, and one writer task. If queue insertion fails, release the byte/count
permit and cancel the unqueued acknowledgement. Once `_queue.put()` succeeds, Writer ownership is
final: caller cancellation may stop waiting but must not cancel the queued item or its Future.

- [ ] **Step 5: Implement the exact epoch commit order**

The writer's `_commit_epoch()` must execute this code shape without reordering:

```python
persisted_payloads = self._materialize_payloads(items)
payload_receipts = self._append_payloads(persisted_payloads)
self._payload_segment.fsync()

persisted_events = self._materialize_events(items, payload_receipts)
event_receipts = self._append_events(persisted_events)
self._event_segment.fsync()

catalog_receipt = self._catalog.commit_epoch(
    build_epoch_record(items, payload_receipts, event_receipts)
)
self._resolve_acknowledgements(items, catalog_receipt)
```

If no payload exists, skip only the payload phase. Do not acknowledge before catalog fsync.

- [ ] **Step 6: Implement uncertain-write abandonment**

On any data append/flush/fsync exception, close both active data segments, append
`segment_abandoned` from their last cataloged offsets to the current writable catalog, start fresh
data segments, and fail all epoch acknowledgements. Never append to a data segment after an
uncertain operation.

On catalog append/flush/fsync uncertainty, seal the current catalog segment too. Create and fsync
a new catalog segment linked from the last *committed* catalog hash/count, record the abandoned
catalog and data segment IDs there, then register fresh data segments. Never append recovery facts
to the catalog segment whose tail is uncertain, and never treat its uncommitted record as the
lineage head.

- [ ] **Step 7: Add queue-full, partial-write, fsync, and crash-boundary tests**

Inject each failure at payload append, payload fsync, event append, event fsync, catalog append,
and catalog fsync.
Assert no referencing event becomes committed before its payload, the failed segment is never
reused, the catalog segment is replaced after catalog uncertainty, and the committed prefix
remains unchanged.

- [ ] **Step 8: Run tests and commit**

Run: `pytest tests/audit/test_writer.py tests/audit/test_writer_faults.py -v`

Expected: PASS.

```bash
git add nanobot/audit/writer.py tests/audit/test_writer.py tests/audit/test_writer_faults.py
git commit -m "feat(audit): commit coordinated durability epochs"
```

### Task 8: Fail-open emitter and health accounting

**Files:**
- Create: `nanobot/audit/emitter.py`
- Test: `tests/audit/test_emitter.py`

- [ ] **Step 1: Write failing fail-open tests**

```python
async def test_emitter_never_falls_back_to_unredacted_content() -> None:
    writer = AsyncMock()
    redactor = Mock(side_effect=RedactionError("rule_failed"))
    emitter = AuditEmitter(writer=writer, redactor=redactor)

    event, payload = draft_with_secret("KNOWN_SECRET")
    result = await emitter.emit(event, payload=payload)

    assert result.committed is False
    writer.submit.assert_not_awaited()
    assert "KNOWN_SECRET" not in repr(result)


async def test_recovery_emits_loss_window() -> None:
    writer = writer_failing_once_then_succeeding()
    emitter = AuditEmitter(writer=writer, redactor=AuditRedactor())
    await emitter.emit(event_draft(trace_id="t1"))
    await emitter.emit(event_draft(trace_id="t1"))
    assert writer.submitted_event_types[-2:] == ["audit_degraded", "audit_recovered"]
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_emitter.py -v`

Expected: import failure.

- [ ] **Step 3: Implement candidate-to-commit conversion**

```python
class AuditEmitter:
    def __init__(self, *, writer: AuditWriter, redactor: AuditRedactor, mode: str = "full") -> None:
        self._writer = writer
        self._redactor = redactor
        self._mode = mode

    async def emit(
        self,
        event: AuditEventDraftBase,
        *,
        payload: AuditPayloadDraftBase | None = None,
        critical: bool = False,
    ) -> EmitResult:
        try:
            redacted_event, event_report = self._redact_event(event)
            redacted_payload, payload_report = self._redact_payload(payload)
            if self._mode == "metadata_only":
                redacted_payload = None
            item = self._build_commit_item(
                redacted_event,
                redacted_payload,
                event_report,
                payload_report,
                critical=critical,
            )
            receipt = await self._writer.submit(item)
        except Exception as exc:
            self._health.record_failure(event.trace_id, classify_audit_error(exc))
            self._log_throttled_failure(event.event_type, exc)
            return EmitResult(committed=False, degraded=True)
        await self._emit_recovery_if_needed()
        return EmitResult(committed=critical and receipt is not None, degraded=False)
```

Logging includes only event type and normalized error class. Health state tracks failure window,
count, and known Trace IDs. A process crash before recovery is handled later by catalog closure
verification, not fabricated here.

Add a test proving `metadata_only` writes the sanitized event preview with `payload_id=None` and
does not enqueue an `AuditPayloadDraft`. `DisabledAuditEmitter` remains the only `off` behavior.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_emitter.py -v`

Expected: PASS.

```bash
git add nanobot/audit/emitter.py tests/audit/test_emitter.py
git commit -m "feat(audit): add fail-open audit emitter"
```

### Task 9: Committed-prefix reader and verifier

**Files:**
- Create: `nanobot/audit/reader.py`
- Create: `nanobot/audit/verify.py`
- Test: `tests/audit/test_reader.py`
- Test: `tests/audit/test_verify.py`

- [ ] **Step 1: Write failing committed-prefix tests**

```python
def test_reader_ignores_complete_but_uncataloged_tail(tmp_path: Path) -> None:
    fixture = audit_fixture_with_committed_epoch_and_extra_valid_line(tmp_path)
    result = AuditReader(fixture.root).read_process(fixture.process_id)
    assert [event.event_id for event in result.events] == fixture.committed_event_ids
    assert result.uncertain_tail is True


def test_verifier_reports_missing_cataloged_segment(tmp_path: Path) -> None:
    fixture = valid_audit_fixture(tmp_path)
    fixture.event_segment.unlink()
    report = AuditVerifier(fixture.root).verify_process(fixture.process_id)
    assert report.status == "invalid"
    assert "missing_cataloged_segment" in report.error_codes
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_reader.py tests/audit/test_verify.py -v`

Expected: import failures.

- [ ] **Step 3: Implement catalog-first reads**

`AuditReader` first verifies catalog chain, derives durable offsets, then reads data segments only
through those offsets. It returns typed records plus diagnostics for uncataloged bytes, malformed
tails, missing segments, orphan payloads, and unknown future records.

```python
@dataclass(frozen=True, slots=True)
class ProcessReadResult:
    events: tuple[AuditEventBase, ...]
    payloads: tuple[AuditPayloadBase, ...]
    diagnostics: tuple[AuditDiagnostic, ...]
    last_committed_epoch: int
    cleanly_closed: bool
```

- [ ] **Step 4: Implement lifecycle verification**

Verify normal/cancel paths require paired Run/iteration/model/attempt/tool terminals. For an
unclean process, missing terminals produce `incomplete` plus orphan candidates, not fake errors.
Verify payload hash/reference, parent/resume/cause relationships, and no execution events after a
Run terminal.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/audit/test_reader.py tests/audit/test_verify.py -v`

Expected: PASS.

```bash
git add nanobot/audit/reader.py nanobot/audit/verify.py \
  tests/audit/test_reader.py tests/audit/test_verify.py
git commit -m "feat(audit): read and verify committed evidence"
```

### Task 10: Core evidence regression gate

**Files:**
- Modify: `nanobot/audit/__init__.py`
- Test: `tests/audit/test_core_acceptance.py`

- [ ] **Step 1: Export only stable core interfaces**

```python
from nanobot.audit.emitter import AuditEmitter
from nanobot.audit.reader import AuditReader
from nanobot.audit.verify import AuditVerifier
from nanobot.audit.writer import AuditWriter

__all__ = ["AuditEmitter", "AuditReader", "AuditVerifier", "AuditWriter"]
```

- [ ] **Step 2: Add an end-to-end core fixture test**

Start a writer, emit one Trace/Turn/Run/model/tool/final response sequence with payloads, close it,
read it, and assert verifier status `valid`. Repeat with injected event fsync failure and assert
the old prefix remains valid while the process result is `degraded` or `incomplete`.

- [ ] **Step 3: Run the complete core suite**

Run: `pytest tests/audit tests/config/test_audit_config.py tests/config/test_config_paths.py -v`

Expected: PASS.

Run: `ruff check nanobot/audit nanobot/config tests/audit tests/config/test_audit_config.py`

Expected: `All checks passed!`.

- [ ] **Step 4: Commit**

```bash
git add nanobot/audit/__init__.py tests/audit/test_core_acceptance.py
git commit -m "test(audit): gate the evidence core"
```

Phase 1 is complete only after every command above passes. Continue with
`2026-07-27-agent-audit-v1-runtime-instrumentation.md`.
