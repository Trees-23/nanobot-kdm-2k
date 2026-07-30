from pathlib import Path

from nanobot.agent.tools.apply_patch import ApplyPatchTool
from nanobot.agent.tools.filesystem import WriteFileTool
from nanobot.audit.side_effects import (
    capture_side_effect_after,
    capture_side_effect_before,
)


def test_write_file_captures_affected_path_hashes(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("before\n", encoding="utf-8")
    snapshot = capture_side_effect_before(
        call_id="call",
        tool_name="write_file",
        tool=WriteFileTool(workspace=tmp_path),
        params={"path": "note.txt", "content": "after\n"},
        workspace=tmp_path,
    )

    target.write_text("after\n", encoding="utf-8")
    evidence = capture_side_effect_after(snapshot, "ok")

    assert len(evidence) == 1
    assert evidence[0]["path"] == "note.txt"
    assert evidence[0]["before_sha256"] != evidence[0]["after_sha256"]
    assert evidence[0]["verification_scope"] == "affected_path_only"


def test_apply_patch_captures_each_known_path(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    first.write_text("old\n", encoding="utf-8")
    snapshot = capture_side_effect_before(
        call_id="call",
        tool_name="apply_patch",
        tool=ApplyPatchTool(workspace=tmp_path),
        params={
            "edits": [
                {"path": "first.txt", "action": "replace", "old_text": "old", "new_text": "new"},
                {"path": "second.txt", "action": "add", "new_text": "created"},
            ]
        },
        workspace=tmp_path,
    )

    first.write_text("new\n", encoding="utf-8")
    (tmp_path / "second.txt").write_text("created\n", encoding="utf-8")
    evidence = capture_side_effect_after(snapshot, "ok")

    assert {item["path"] for item in evidence} == {"first.txt", "second.txt"}
    created = next(item for item in evidence if item["path"] == "second.txt")
    assert created["before_exists"] is False
    assert created["after_exists"] is True


def test_exec_captures_exit_code_and_cwd_without_workspace_claim(tmp_path: Path) -> None:
    class ExecTool:
        _workspace = tmp_path

    snapshot = capture_side_effect_before(
        call_id="call",
        tool_name="exec",
        tool=ExecTool(),
        params={},
        workspace=tmp_path,
    )

    evidence = capture_side_effect_after(snapshot, "output\nExit code: 7")

    assert evidence == [
        {
            "kind": "process_execution",
            "cwd": tmp_path.resolve().as_posix(),
            "exit_code": 7,
            "verification_scope": "process_result_only",
        }
    ]
