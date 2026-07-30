# Agent Audit Infrastructure V1 Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved Agent Audit V1 design as three independently testable, sequential implementation plans.

**Architecture:** JSONL events and payloads remain the source of truth. A per-process writer commits payloads, events, and catalog lineage in coordinated durability epochs; runtime instrumentation emits typed facts; a rebuildable SQLite index powers read-only query, verification, export, and CLI surfaces.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, JSONL, SHA-256, sqlite3, filelock, Typer, pytest/pytest-asyncio.

---

## Required Reading

- Design: `docs/superpowers/specs/2026-07-27-agent-audit-infrastructure-v1-design.md`
- Architecture constraints: `.agent/design.md`
- Security boundaries: `.agent/security.md`
- Repository gotchas: `.agent/gotchas.md`

## Plan Order

- [ ] **Phase 1: Evidence core**

  Execute `docs/superpowers/plans/2026-07-27-agent-audit-v1-core-evidence.md`.

  Exit condition: typed schema, recognized-secret redaction, integrity primitives, process catalog,
  coordinated durability writer, committed-prefix reader, and verifier all pass fault-injection
  tests without touching AgentRunner.

- [ ] **Phase 2: Runtime instrumentation**

  Execute `docs/superpowers/plans/2026-07-27-agent-audit-v1-runtime-instrumentation.md` only after
  Phase 1 is green.

  Exit condition: user, SDK, subagent, goal, automation, Provider fallback, tool, checkpoint,
  cancellation, and channel delivery paths emit schema-valid events into the Phase 1 store.

- [ ] **Phase 3: Query and acceptance**

  Execute `docs/superpowers/plans/2026-07-27-agent-audit-v1-query-acceptance.md` only after Phase 2
  is green.

  Exit condition: SQLite indexing, TraceView reconstruction, verification, export, CLI, crash
  reconciliation, fixed fixtures, security tests, and end-to-end acceptance scenarios pass.

## Global Execution Rules

- [ ] Start each phase from a clean branch or isolated worktree created at execution time.
- [ ] Never stage unrelated existing worktree changes.
- [ ] Run `ruff check` only; do not run `ruff format`.
- [ ] Keep audit failures fail-open and never use unredacted content as an error fallback.
- [ ] Treat only cataloged durability epochs as committed evidence.
- [ ] Preserve the default `audit.mode=full` and permanent plaintext payload decision.
- [ ] Do not add WebUI, Agent audit tools, automatic retention, or golden-trace curation.
- [ ] Do not proceed to the next phase until the current phase's focused suite and full regression
  suite pass.

## Design Coverage Map

| Design requirement | Implementation task |
|---|---|
| Configuration and audit root | Core Task 1 |
| UUIDv7, event/payload/catalog schema | Core Tasks 2-3 |
| Hash chains and recognized-secret redaction | Core Tasks 4-5 |
| Segment lineage and process catalog | Core Task 6 |
| Single writer and durability epochs | Core Task 7 |
| Fail-open degradation | Core Task 8 |
| Committed-prefix reads and lifecycle verification | Core Task 9 |
| Deterministic Trace/Turn/Run identity | Runtime Task 1 |
| Automatic Runner Hook and model lifecycle | Runtime Tasks 2-4 |
| Tool terminals and policy decisions | Runtime Task 5 |
| Real Provider attempts, fallback, and safe logs | Runtime Tasks 6-7 |
| Turn, checkpoint, `/stop`, Goal, child and automation facts | Runtime Task 8 |
| SDK return and channel delivery | Runtime Task 9 |
| Tool side-effect evidence | Runtime Task 10 |
| TraceView and causal reconstruction | Query Task 1 |
| SQLite lock, migration, committed-prefix indexing | Query Tasks 2-4 |
| Crash reconciliation | Query Task 5 |
| Sanitized/full/evidence-bundle export | Query Task 6 |
| `nanobot audit` CLI | Query Task 7 |
| Fixed fixtures, security, faults, scale, docs | Query Tasks 8-9 |

## Final Verification

- [ ] Run the focused audit suite:

```bash
pytest tests/audit tests/agent/test_runner_audit.py tests/providers/test_audit_attempts.py \
  tests/channels/test_audit_delivery.py tests/cli/test_audit_commands.py -v
```

Expected: all audit-focused tests pass.

- [ ] Run the full Python suite:

```bash
pytest -q
```

Expected: exit code 0.

- [ ] Run lint on changed packages:

```bash
ruff check nanobot/audit nanobot/agent nanobot/providers nanobot/channels nanobot/cli \
  nanobot/config tests/audit
```

Expected: `All checks passed!`.

- [ ] Confirm the worktree contains no runtime evidence:

```bash
git status --short
git check-ignore runtime/audit
```

Expected: only intended source/test/doc changes are present, and `runtime/audit` is ignored.
