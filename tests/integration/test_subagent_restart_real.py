"""S05-S13: durable subagent state across real interpreter restarts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DRIVER = "tests.integration.subagent_restart_driver"


def _start(root: Path, action: str) -> tuple[subprocess.Popen[str], dict[str, Any]]:
    process = subprocess.Popen(
        [sys.executable, "-m", DRIVER, str(root), action, "--hold"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    if not line:
        assert process.stderr is not None
        raise AssertionError(process.stderr.read())
    return process, json.loads(line)


def _run(root: Path, action: str) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", DRIVER, str(root), action],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return json.loads(completed.stdout)


def _crash(process: subprocess.Popen[str]) -> None:
    process.kill()
    return_code = process.wait(timeout=5)
    if sys.platform == "win32":
        assert return_code != 0
    else:
        assert return_code < 0


def test_s05_s07_s11_waiting_child_restart_is_lost_without_new_deadline(tmp_path) -> None:
    first, seeded = _start(tmp_path, "seed-running")
    deadline = seeded["deadline_at"]
    _crash(first)

    recovered = _run(tmp_path, "recover")

    assert recovered["first"] == 1
    assert recovered["second"] == 0
    assert recovered["record"]["deadline_at"] == deadline
    assert recovered["record"]["status"] == "lost"
    assert recovered["record"]["termination_state"] == "termination_failed"
    assert recovered["record"]["termination_evidence"]["executor_present"] is False


def test_s06_s10_finished_before_restart_is_claimed_once(tmp_path) -> None:
    first, seeded = _start(tmp_path, "seed-finished")
    assert seeded["result"]["delivery_phase"] == "unclaimed"
    _crash(first)

    claimed = _run(tmp_path, "claim")
    repeated = _run(tmp_path, "claim")

    assert claimed["first"] is True
    assert claimed["second"] is False
    assert repeated["first"] is False
    assert repeated["second"] is False
    assert repeated["record"]["result"]["delivery_phase"] == "claimed_pending_delivery"


def test_s08_expired_deadline_is_not_reset_after_restart(tmp_path) -> None:
    first, seeded = _start(tmp_path, "seed-expired")
    deadline = seeded["deadline_at"]
    _crash(first)

    recovered = _run(tmp_path, "recover")

    assert recovered["record"]["deadline_at"] == deadline
    assert recovered["record"]["deadline_expired"] is True
    assert recovered["record"]["status"] == "lost"


def test_s09_corrupt_goal_state_is_preserved_and_not_rewritten(tmp_path) -> None:
    result = _run(tmp_path, "corrupt-goal")

    assert result == {
        "changed": 0,
        "preserved": True,
        "value": "corrupt-goal-state",
    }


def test_s12_pending_delivery_survives_restart_and_finishes_once(tmp_path) -> None:
    first, seeded = _start(tmp_path, "seed-claimed")
    assert seeded["result"]["delivery_phase"] == "claimed_pending_delivery"
    _crash(first)

    delivered = _run(tmp_path, "deliver")
    repeated = _run(tmp_path, "claim")

    assert delivered["first"] is False
    assert delivered["second"] is False
    assert delivered["record"]["result"]["delivery_phase"] == "delivered"
    assert repeated["first"] is False
    assert repeated["record"]["result"]["delivery_phase"] == "delivered"
