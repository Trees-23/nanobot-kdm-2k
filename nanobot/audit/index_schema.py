"""SQLite schema for the disposable audit V1 read model."""

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS segment_cursors (
  process_instance_id TEXT NOT NULL,
  stream_kind TEXT NOT NULL,
  segment_id TEXT NOT NULL,
  durable_offset INTEGER NOT NULL,
  final_hash TEXT,
  durability_epoch INTEGER NOT NULL,
  PRIMARY KEY (process_instance_id, stream_kind, segment_id)
);
CREATE TABLE IF NOT EXISTS events (
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
CREATE INDEX IF NOT EXISTS events_trace_order
  ON events(trace_id, occurred_at, event_id);
CREATE INDEX IF NOT EXISTS events_session_time
  ON events(session_key, occurred_at);
CREATE INDEX IF NOT EXISTS events_tool_time
  ON events(tool_name, occurred_at);
CREATE INDEX IF NOT EXISTS events_model_time
  ON events(model, occurred_at);
CREATE INDEX IF NOT EXISTS events_status_time
  ON events(status, occurred_at);
"""
