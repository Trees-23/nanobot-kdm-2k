import os

import pytest

from nanobot.audit.segments import JsonlSegment, SegmentSealedError


def test_segment_append_is_jsonl_and_never_rewrites(tmp_path) -> None:
    segment = JsonlSegment.create(tmp_path / "events.jsonl", mode=0o600)
    first = segment.append({"n": 1})
    second = segment.append({"n": 2})
    segment.fsync()

    assert first.start == 0
    assert second.start == first.end
    assert segment.path.read_text().splitlines() == ['{"n":1}', '{"n":2}']


def test_segment_is_private_on_posix(tmp_path) -> None:
    segment = JsonlSegment.create(tmp_path / "events.jsonl", mode=0o600)
    if os.name == "posix":
        assert segment.path.stat().st_mode & 0o777 == 0o600


def test_segment_cannot_append_after_seal(tmp_path) -> None:
    segment = JsonlSegment.create(tmp_path / "events.jsonl")
    segment.seal()
    with pytest.raises(SegmentSealedError):
        segment.append({"n": 1})


def test_segment_create_is_exclusive(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    JsonlSegment.create(path)
    with pytest.raises(FileExistsError):
        JsonlSegment.create(path)
