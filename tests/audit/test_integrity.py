from datetime import UTC, datetime

from nanobot.audit.integrity import canonical_json_bytes, hash_record, verify_chain


def test_canonical_json_ignores_dict_insertion_order() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes({"a": 1, "b": 2})


def test_canonical_json_serializes_datetime_as_utc_json() -> None:
    encoded = canonical_json_bytes({"at": datetime(2026, 7, 27, tzinfo=UTC)})
    assert encoded == b'{"at":"2026-07-27T00:00:00Z"}'


def test_hash_record_excludes_own_hash_field() -> None:
    record = {"event_id": "e1", "event_hash": "old", "value": 3}
    assert hash_record(record, hash_field="event_hash") == hash_record(
        {**record, "event_hash": "different"}, hash_field="event_hash"
    )


def _chained_records(count: int) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    previous: str | None = None
    for sequence in range(1, count + 1):
        record: dict[str, object] = {
            "segment_sequence": sequence,
            "previous_event_hash": previous,
            "value": sequence,
        }
        record["event_hash"] = hash_record(record, hash_field="event_hash")
        previous = str(record["event_hash"])
        result.append(record)
    return result


def test_verify_chain_reports_deleted_middle_record() -> None:
    records = _chained_records(3)
    report = verify_chain([records[0], records[2]], hash_field="event_hash")
    assert report.valid is False
    assert report.error_code == "sequence_gap"


def test_verify_chain_reports_mutation() -> None:
    records = _chained_records(2)
    records[1]["value"] = 99
    report = verify_chain(records, hash_field="event_hash")
    assert report.valid is False
    assert report.error_code == "record_hash_mismatch"
