import pytest

from nanobot.audit.index import AuditIndexWriter, IndexWriterBusy


def test_second_index_writer_cannot_acquire_lock(tmp_path) -> None:
    first = AuditIndexWriter.acquire(tmp_path)
    try:
        with pytest.raises(IndexWriterBusy):
            AuditIndexWriter.acquire(tmp_path, timeout=0)
    finally:
        first.close()

    second = AuditIndexWriter.acquire(tmp_path, timeout=0)
    second.close()
