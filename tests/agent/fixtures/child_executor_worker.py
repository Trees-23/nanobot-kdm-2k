"""Deterministic pipe worker used only by process supervisor tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _send(start: dict, message_type: str, **extra: object) -> None:
    print(json.dumps({
        "schema_version": start["schema_version"],
        "type": message_type,
        "executor_id": start["executor_id"],
        "process_instance_id": start["process_instance_id"],
        **extra,
    }), flush=True)


def main() -> None:
    start = json.loads(sys.stdin.readline())
    behavior = start["payload"]["behavior"]
    descendant = None
    if behavior == "descendant":
        descendant = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(300)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    _send(start, "lifecycle", state="started")
    if behavior == "exit":
        _send(start, "result", result={"status": "ok"})
        return
    if behavior == "echo_env":
        _send(start, "result", result={"secret": os.environ.get("CHILD_EXECUTOR_SECRET")})
        return
    for line in sys.stdin:
        command = json.loads(line)
        if command.get("type") != "cancel":
            continue
        if behavior == "cooperative":
            _send(start, "result", result={"status": "cancelled", "stop_reason": "cancelled"})
            return
        if behavior == "late_success":
            time.sleep(0.1)
            _send(start, "result", result={
                "final_content": "stale success",
                "stop_reason": "completed",
            })
            return
        while True:
            time.sleep(1)
    if descendant is not None:
        descendant.wait()


if __name__ == "__main__":
    main()
