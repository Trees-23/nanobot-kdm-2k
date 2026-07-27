# Agent Audit V1 Query and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add indexed query, causal Trace reconstruction, verification, evidence export, CLI operations, crash reconciliation, and full V1 acceptance coverage.

**Architecture:** Cataloged JSONL prefixes remain authoritative. One cross-process-locked SQLite index writer builds a disposable read model; typed query reconstructs TraceView by causal IDs; exports preserve provenance; reconciliation appends orphan evidence without fabricating outcomes.

**Tech Stack:** Python 3.11+, sqlite3/WAL, filelock, Pydantic v2, Typer/Rich, Phase 1 reader/verifier, Phase 2 runtime events, pytest.

---

## File Structure

| File | Responsibility |
|---|---|
| `nanobot/audit/query.py` | Paginated filters and causal TraceView reconstruction |
| `nanobot/audit/index_schema.py` | Disposable SQLite V1 schema and migrations |
| `nanobot/audit/index.py` | Locked indexing of cataloged prefixes and coverage metadata |
| `nanobot/audit/reconcile.py` | Conservative process liveness and orphan evidence emission |
| `nanobot/audit/export.py` | Streaming sanitized, full, and evidence-bundle exports |
| `nanobot/cli/audit.py` | Read-oriented `nanobot audit` command surface |
| `tests/fixtures/audit_v1/` | Immutable writer-generated schema and behavior fixtures |

The index and CLI never become evidence authorities. Query, export, and reconciliation return to
cataloged JSONL whenever integrity or index coverage is uncertain.

### Task 1: Typed query filters and TraceView reconstruction

**Files:**
- Create: `nanobot/audit/query.py`
- Test: `tests/audit/test_query.py`

- [ ] **Step 1: Write failing causal reconstruction tests**

```python
def test_trace_view_builds_parent_and_resume_tree(tmp_path: Path) -> None:
    store_fixture(
        tmp_path,
        events=[
            event("run_started", trace="t1", turn="u1", run="r1"),
            event("run_started", trace="t1", turn="u1", run="r2", parent="r1"),
            event("run_finished", trace="t1", turn="u1", run="r2", status="failed"),
            event("run_started", trace="t1", turn="u2", run="r3", resumed_from="r1"),
        ],
    )
    view = AuditQuery.from_root(tmp_path).load_trace("t1")
    assert view.run_tree.roots[0].run_id == "r1"
    assert view.run_tree.roots[0].children[0].run_id == "r2"
    assert view.run_tree.resumptions[0].run_id == "r3"


def test_query_does_not_load_payloads_by_default(tmp_path: Path) -> None:
    query = AuditQuery.from_root(fixture_with_secret_payload(tmp_path))
    view = query.load_trace("t1")
    assert view.payloads is None
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_query.py -v`

Expected: import failure.

- [ ] **Step 3: Implement typed read models**

```python
@dataclass(frozen=True, slots=True)
class TraceFilter:
    since: datetime | None = None
    until: datetime | None = None
    session_key: str | None = None
    source_type: str | None = None
    run_status: RunStatus | None = None
    model: str | None = None
    tool: str | None = None
    limit: int = 50
    cursor: str | None = None


class TraceSummary(BaseModel):
    first_seen: datetime
    last_seen: datetime
    turn_count: int
    run_count: int
    terminal_run_statuses: list[RunStatus]
    integrity_status: IntegrityStatus


class TurnView(BaseModel):
    turn_id: str
    session_key: str | None
    source_type: str | None
    event_ids: list[str]


class RunNode(BaseModel):
    run_id: str
    parent_run_id: str | None
    resumed_from_run_id: str | None
    status: RunStatus | None
    children: list[RunNode] = Field(default_factory=list)


class RunTree(BaseModel):
    roots: list[RunNode]
    resumptions: list[RunNode]


class TraceView(BaseModel):
    trace_id: str
    summary: TraceSummary
    turns: list[TurnView]
    run_tree: RunTree
    timeline: list[AuditEventBase]
    payloads: dict[str, AuditPayloadBase] | None = None
    decisions: list[AuditEventBase]
    integrity: VerificationReport
```

Import `IntegrityStatus` and `RunStatus` from `nanobot.audit.types` and `VerificationReport` from
`nanobot.audit.verify`; Query must not define parallel status enums. `RunTree.roots` supports valid
Traces with multiple independent root Runs; tests address roots by stable list position.

- [ ] **Step 4: Implement scan-backed query first**

`AuditQuery` uses `AuditReader` directly before the SQLite index exists. `load_trace` orders by
parent/resume/cause edges, iteration, process/segment sequence, then wall clock for unrelated
events. Detect causal cycles and mark the view invalid rather than guessing.

- [ ] **Step 5: Add pagination tests**

Assert `limit` is mandatory and bounded, cursors are stable for identical committed evidence,
and no API returns the entire history by default.

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/audit/test_query.py -v`

Expected: PASS.

```bash
git add nanobot/audit/query.py tests/audit/test_query.py
git commit -m "feat(audit): reconstruct causal trace views"
```

### Task 2: Disposable SQLite index schema and single writer lock

**Files:**
- Create: `nanobot/audit/index.py`
- Create: `nanobot/audit/index_schema.py`
- Test: `tests/audit/test_index.py`
- Test: `tests/audit/test_index_lock.py`

- [ ] **Step 1: Write failing schema and lock tests**

```python
def test_index_uses_wal_and_schema_version(tmp_path: Path) -> None:
    index = AuditIndex.open(tmp_path / "index.sqlite")
    assert index.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert index.schema_version == 1


def test_second_index_writer_cannot_acquire_lock(tmp_path: Path) -> None:
    first = AuditIndexWriter.acquire(tmp_path)
    with pytest.raises(IndexWriterBusy):
        AuditIndexWriter.acquire(tmp_path, timeout=0)
    first.close()
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/audit/test_index.py tests/audit/test_index_lock.py -v`

Expected: import failures.

- [ ] **Step 3: Define the exact SQLite schema**

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE segment_cursors (
  process_instance_id TEXT NOT NULL,
  stream_kind TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  durable_offset INTEGER NOT NULL,
  final_hash TEXT NOT NULL,
  durability_epoch INTEGER NOT NULL,
  PRIMARY KEY (process_instance_id, stream_kind, segment_id)
);
CREATE TABLE events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  trace_id TEXT,
  turn_id TEXT,
  run_id TEXT,
  parent_run_id TEXT,
  resumed_from_run_id TEXT,
  caused_by_event_id TEXT,
  model_call_id TEXT,
  attempt_id TEXT,
  tool_call_id TEXT,
  checkpoint_id TEXT,
  goal_id TEXT,
  delivery_id TEXT,
  session_key TEXT,
  source_type TEXT,
  iteration INTEGER,
  status TEXT,
  stop_reason TEXT,
  provider TEXT,
  model TEXT,
  tool_name TEXT,
  elapsed_ms INTEGER,
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  payload_id TEXT,
  process_instance_id TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  segment_sequence INTEGER NOT NULL
);
CREATE INDEX events_trace_order ON events(trace_id, occurred_at, event_id);
CREATE INDEX events_session_time ON events(session_key, occurred_at);
CREATE INDEX events_tool_time ON events(tool_name, occurred_at);
CREATE INDEX events_model_time ON events(model, occurred_at);
CREATE INDEX events_status_time ON events(status, occurred_at);
```

The index stores no payload content. Set `meta.schema_version=1` and `meta.source_format=audit-v1`.
Represent a missing/new segment cursor explicitly:

```python
@dataclass(frozen=True, slots=True)
class SegmentCursor:
    process_instance_id: str
    stream_kind: str
    segment_id: str
    durable_offset: int
    final_hash: str | None
    durability_epoch: int

    @classmethod
    def zero(
        cls,
        *,
        process_instance_id: str,
        stream_kind: str,
        segment_id: str,
    ) -> SegmentCursor:
        return cls(process_instance_id, stream_kind, segment_id, 0, None, 0)
```

- [ ] **Step 4: Implement platform-safe writer locking**

Use existing `filelock.FileLock` at `state/index.lock`. One writer holds it for update/rebuild;
readers open SQLite independently. Never infer ownership from a stale text lock file.

- [ ] **Step 5: Implement migration/rebuild policy**

Supported additive migrations run in a transaction. Unknown/newer versions raise
`IndexRebuildRequired`; the caller renames the derived DB to `.invalid-<timestamp>`, builds a new
DB, and never edits JSONL.

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/audit/test_index.py tests/audit/test_index_lock.py -v`

Expected: PASS.

```bash
git add nanobot/audit/index.py nanobot/audit/index_schema.py \
  tests/audit/test_index.py tests/audit/test_index_lock.py
git commit -m "feat(audit): add rebuildable audit index"
```

### Task 3: Incremental indexing of committed prefixes

**Files:**
- Modify: `nanobot/audit/index.py`
- Test: `tests/audit/test_index_incremental.py`

- [ ] **Step 1: Write failing stable-prefix test**

Create an open segment containing one cataloged event and one complete but uncataloged JSON line.
Run the indexer and assert only the cataloged event is present.

- [ ] **Step 2: Implement catalog-driven incremental scanning**

```python
class AuditIndexer:
    def update(self) -> IndexUpdateReport:
        with AuditIndexWriter.acquire(self.root):
            prefixes = self.reader.committed_prefixes()
            for prefix in prefixes:
                cursor = self.index.get_cursor(prefix.segment_id) or SegmentCursor.zero(
                    process_instance_id=prefix.process_instance_id,
                    stream_kind=prefix.stream_kind,
                    segment_id=prefix.segment_id,
                )
                if cursor.durable_offset > prefix.durable_offset:
                    raise IndexRebuildRequired("source prefix moved backwards")
                self._scan_range(prefix, start=cursor.durable_offset)
            self.index.connection.commit()
        return self._report()
```

Index only bytes up to catalog offset and verify the expected final hash before advancing the
cursor. On hash or lineage failure, stop that process instance and mark index coverage incomplete.

- [ ] **Step 3: Add idempotency and rebuild tests**

Run update twice and assert row counts remain stable. Delete the DB, rebuild, and assert query
results match the original index exactly.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_index_incremental.py -v`

Expected: PASS.

```bash
git add nanobot/audit/index.py tests/audit/test_index_incremental.py
git commit -m "feat(audit): index committed evidence prefixes"
```

### Task 4: Index-backed query and aggregate statistics

**Files:**
- Modify: `nanobot/audit/query.py`
- Test: `tests/audit/test_query_indexed.py`
- Test: `tests/audit/test_stats.py`

- [ ] **Step 1: Write parity tests**

For the same fixture, compare scan-backed and index-backed `find_traces` results, ordering,
cursors, TraceView, and integrity. They must be identical.

- [ ] **Step 2: Implement parameterized SQL filters**

Use SQL parameters only. Query candidate Trace IDs through SQLite, then load authoritative events
and payloads through `AuditReader`; never reconstruct payload truth from index rows.

```python
rows = connection.execute(
    """
    SELECT DISTINCT trace_id, MAX(occurred_at) AS last_seen
    FROM events
    WHERE trace_id IS NOT NULL AND occurred_at >= ?
      AND (? IS NULL OR EXISTS (
        SELECT 1 FROM events run_terminal
        WHERE run_terminal.trace_id = events.trace_id
          AND run_terminal.event_type = 'run_finished'
          AND run_terminal.status = ?
      ))
      AND (? IS NULL OR tool_name = ?)
    GROUP BY trace_id
    ORDER BY last_seen DESC, trace_id DESC
    LIMIT ?
    """,
    (since, status, status, tool, tool, limit),
).fetchall()
```

- [ ] **Step 3: Implement stats without payload access**

Support counts, error rates, cancellation rates, iteration counts, token totals, and average
latency grouped by tool/model/source/status. Return coverage metadata so partial index data is not
presented as global truth.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_query_indexed.py tests/audit/test_stats.py -v`

Expected: PASS.

```bash
git add nanobot/audit/query.py tests/audit/test_query_indexed.py tests/audit/test_stats.py
git commit -m "feat(audit): query indexed traces and statistics"
```

### Task 5: Crash reconciliation and orphan evidence

**Files:**
- Create: `nanobot/audit/reconcile.py`
- Test: `tests/audit/test_reconcile.py`

- [ ] **Step 1: Write failing confirmed-dead and uncertain-owner tests**

```python
def test_uncertain_owner_only_emits_suspected(tmp_path: Path) -> None:
    fixture = open_run_fixture(tmp_path, lease_state="uncertain")
    result = AuditReconciler(fixture.runtime).reconcile()
    assert result.emitted_types == ["orphan_run_suspected"]


def test_confirmed_dead_emits_nested_orphans_without_results(tmp_path: Path) -> None:
    fixture = open_tool_fixture(tmp_path, lease_state="dead")
    result = AuditReconciler(fixture.runtime).reconcile()
    assert result.emitted_types == ["orphan_run_detected", "orphan_tool_detected"]
    assert result.events[-1].status is None
    assert result.events[-1].payload_id is None
```

- [ ] **Step 2: Implement conservative liveness classification**

Classify `alive`, `dead`, or `uncertain` from catalog clean close, lease expiry, host fingerprint,
boot ID, and same-host PID checks. Never treat lease expiry alone as conclusive for a remote host.

- [ ] **Step 3: Reconcile only the cataloged prefix**

Enumerate started Runs/models/attempts/tools lacking committed terminals. Emit suspected or
detected facts into the current process's writer. Never append into the dead process's segments
and never promote uncataloged tails.

- [ ] **Step 4: Add cancelled-checkpoint test**

Assert restoring from a Run already terminal as `cancelled` creates a resumed Run but no orphan
event.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/audit/test_reconcile.py -v`

Expected: PASS.

```bash
git add nanobot/audit/reconcile.py tests/audit/test_reconcile.py
git commit -m "feat(audit): reconcile crashed process evidence"
```

### Task 6: Sanitized, full, and evidence-bundle exports

**Files:**
- Create: `nanobot/audit/export.py`
- Test: `tests/audit/test_export.py`

- [ ] **Step 1: Write failing export-mode tests**

Assert sanitized output has no payload content, full output contains recognized-secret-redacted
payloads, and evidence-bundle output contains selected records, catalog epochs, lineage manifest,
chain witnesses, exporter version, and verification report.

- [ ] **Step 2: Implement streaming exporters**

```python
class AuditExporter:
    def export_trace(self, trace_id: str, *, mode: ExportMode, output: BinaryIO) -> ExportReport:
        view = self.query.load_trace(trace_id, include_payloads=mode is ExportMode.FULL)
        if mode is ExportMode.SANITIZED:
            return self._write_sanitized(view, output)
        if mode is ExportMode.FULL:
            return self._write_full(view, output)
        return self._write_evidence_bundle(trace_id, output)
```

Use incremental JSON encoding and zip streaming for bundles. Do not load all historical payloads
or unrelated segment content into memory. Bundle manifests state that hashes are local and
unsigned.

- [ ] **Step 3: Add large-payload test**

Use a payload larger than the test writer's segment target and assert export reads it incrementally
and peak buffered chunk size stays under the configured test bound.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/audit/test_export.py -v`

Expected: PASS.

```bash
git add nanobot/audit/export.py tests/audit/test_export.py
git commit -m "feat(audit): export verified trace evidence"
```

### Task 7: `nanobot audit` CLI

**Files:**
- Create: `nanobot/cli/audit.py`
- Modify: `nanobot/cli/commands.py:2153-2165`
- Test: `tests/cli/test_audit_commands.py`

- [ ] **Step 1: Write failing CLI help and list tests**

```python
def test_audit_help_lists_commands() -> None:
    result = runner.invoke(app, ["audit", "--help"])
    assert result.exit_code == 0
    for command in ("list", "show", "verify", "export", "stats", "index", "doctor"):
        assert command in result.stdout


def test_show_defaults_to_sanitized(tmp_path: Path, monkeypatch) -> None:
    configure_audit_root(monkeypatch, tmp_path)
    write_trace_with_secret_payload(tmp_path)
    result = runner.invoke(app, ["audit", "show", "t1"])
    assert "SECRET_CANARY" not in result.stdout
```

- [ ] **Step 2: Confirm failure**

Run: `pytest tests/cli/test_audit_commands.py -v`

Expected: `audit` command is unknown.

- [ ] **Step 3: Create an isolated Typer app factory**

```python
def create_audit_app(*, console: Console) -> typer.Typer:
    audit_app = typer.Typer(help="Inspect durable Agent audit evidence")
    audit_app.command("list")(audit_list)
    audit_app.command("show")(audit_show)
    audit_app.command("verify")(audit_verify)
    audit_app.command("export")(audit_export)
    audit_app.command("stats")(audit_stats)
    audit_app.add_typer(create_index_app(console=console), name="index")
    audit_app.command("doctor")(audit_doctor)
    return audit_app
```

Register with `app.add_typer(create_audit_app(console=console), name="audit")` in `commands.py`.
Every command accepts `--config/-c` and optional `--audit-root`. Resolve the config path using the
same loader conventions as other inspection commands; when `--audit-root` is absent, use
`get_audit_dir(config.audit.path)`. Tests must prove a custom instance config does not read the
default instance's audit directory.

- [ ] **Step 4: Implement exact command defaults**

- `list`: default `--limit 50`, newest first, no payload reads.
- `show`: sanitized by default; `--include-payloads` prints an explicit plaintext warning.
- `verify`: one Trace, `--segment`, or `--all`; non-valid status exits nonzero.
- `export`: requires output path and mode; default `sanitized`.
- `stats`: requires a time range or explicit `--all`.
- `index status/rebuild`: lock-aware and reports source coverage.
- `doctor`: permissions, free space, catalog, leases, abandoned tails, index freshness, and mode.

- [ ] **Step 5: Add output and exit-code tests**

Test valid, degraded, incomplete, unknown, invalid, missing Trace, writer lock busy, and full-mode
warning cases. Never print payloads on an exception path.

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/cli/test_audit_commands.py -v`

Expected: PASS.

```bash
git add nanobot/cli/audit.py nanobot/cli/commands.py tests/cli/test_audit_commands.py
git commit -m "feat(cli): add audit evidence commands"
```

### Task 8: Fixed schema v1 fixtures and security/fault matrix

**Files:**
- Create: `tests/fixtures/audit_v1/normal/`
- Create: `tests/fixtures/audit_v1/failure_recovery/`
- Create: `tests/fixtures/audit_v1/cancel_resume/`
- Create: `tests/audit/test_v1_fixtures.py`
- Create: `tests/audit/test_security_matrix.py`
- Create: `tests/audit/test_fault_matrix.py`

- [ ] **Step 1: Generate fixtures through production writer**

Each fixture contains event segments, payload segments, catalog segments, expected sanitized
TraceView, expected full TraceView, and expected verification report. Generate once with a test
builder, inspect, then commit immutable fixture bytes. Do not hand-author hashes.

- [ ] **Step 2: Lock schema compatibility**

```python
@pytest.mark.parametrize("name", ["normal", "failure_recovery", "cancel_resume"])
def test_v1_fixture_remains_readable(name: str) -> None:
    root = FIXTURE_ROOT / name
    report = AuditVerifier(root).verify_all()
    assert report.status == "valid"
    assert AuditQuery.from_root(root).load_all_traces() == load_expected_views(root)
```

- [ ] **Step 3: Add recognized-secret matrix**

Plant canaries in nested keys, Authorization, Cookie, Bearer, provider errors, Shell output, MCP
result, checkpoint, and configured custom pattern. Assert absence from JSONL, SQLite, exports,
CLI output, and touched logs. Add an opaque unconfigured value and assert full mode preserves it,
documenting the intended residual risk.

- [ ] **Step 4: Add durability fault matrix**

Parameterize queue full, payload append/fsync, event append/fsync, catalog append/fsync, entire
cataloged segment deletion, partial line, degradation then immediate process death, and index
corruption. Assert committed prefixes and conservative integrity status.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/audit/test_v1_fixtures.py tests/audit/test_security_matrix.py \
tests/audit/test_fault_matrix.py -v`

Expected: PASS.

```bash
git add tests/fixtures/audit_v1 tests/audit/test_v1_fixtures.py \
  tests/audit/test_security_matrix.py tests/audit/test_fault_matrix.py
git commit -m "test(audit): lock v1 evidence fixtures"
```

### Task 9: End-to-end acceptance and scale benchmark

**Files:**
- Create: `tests/audit/test_end_to_end.py`
- Create: `tests/audit/test_scale.py`
- Modify: `pyproject.toml` (`tool.pytest.ini_options` markers)
- Modify: `docs/configuration.md`
- Modify: `docs/cli-reference.md`
- Modify: `.gitignore`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Implement the Design acceptance matrix**

Add end-to-end tests for ordinary chat, successful tool, tool recovery, policy block, `/stop`, max
iterations, Provider retry/fallback/circuit/image-strip, delivery failure, process kill/reconcile,
Goal lifecycle, reasoning variants, concurrent subagents, cron/local trigger/ephemeral/SDK, queue
full, and partial append abandonment.

- [ ] **Step 2: Add the one-million-event index benchmark**

Generate one million lightweight events outside the repository, build the index, run paginated
filters, and record timings as test diagnostics. Mark the test `@pytest.mark.slow`; assert result
correctness and index use, not a hardware-fragile wall-clock threshold.

Register the marker in `pyproject.toml`:

```toml
markers = [
    "slow: large deterministic audit scale tests excluded from the default focused loop",
]
```

- [ ] **Step 3: Document configuration and operational risk**

Document `full` as the default, permanent plaintext payloads, recognized-secret limits, custom
patterns, modes, dedicated volume, CLI examples, integrity threat model, and lack of automatic
deletion. Do not claim external non-repudiation.

- [ ] **Step 4: Ensure runtime evidence is excluded and persisted**

Add `runtime/audit/` to `.gitignore` if the broader `runtime/` rule is not already present. Add a
dedicated audit volume in Compose without changing unrelated user deployment settings.

- [ ] **Step 5: Run complete verification**

```bash
pytest tests/audit tests/agent/test_runner_audit.py tests/providers/test_audit_attempts.py \
  tests/channels/test_audit_delivery.py tests/cli/test_audit_commands.py -v
pytest -q
ruff check nanobot/audit nanobot/agent nanobot/providers nanobot/channels nanobot/cli \
  nanobot/config tests/audit
```

Expected: all tests pass and Ruff prints `All checks passed!`.

- [ ] **Step 6: Commit**

```bash
git add tests/audit pyproject.toml docs/configuration.md docs/cli-reference.md \
  .gitignore docker-compose.yml
git commit -m "feat(audit): complete audit v1 acceptance"
```

Phase 3 and Agent Audit V1 are complete only after the roadmap's final verification passes.
