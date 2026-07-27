from nanobot.audit.catalog import EpochCommit, ProcessCatalog


def test_catalog_committed_prefix_comes_only_from_epoch_record(tmp_path) -> None:
    catalog = ProcessCatalog.create(tmp_path, process_instance_id="p1")
    catalog.register_segment(
        stream_kind="event", segment_id="e1", path_token="events/e1.jsonl"
    )
    catalog.register_segment(
        stream_kind="payload", segment_id="d1", path_token="payloads/d1.jsonl"
    )
    assert catalog.last_committed_prefix() is None

    catalog.commit_epoch(
        EpochCommit(
            durability_epoch=1,
            event_segment_id="e1",
            event_durable_offset=100,
            event_final_hash="sha256:event",
            event_record_count=2,
            payload_segment_id="d1",
            payload_durable_offset=200,
            payload_final_hash="sha256:payload",
            payload_record_count=1,
        )
    )
    prefix = catalog.last_committed_prefix()
    assert prefix is not None
    assert prefix.event_offset == 100
    assert prefix.payload_offset == 200
    assert catalog.records[-1].catalog_record_type == "epoch_committed"


def test_catalog_records_form_a_hash_chain(tmp_path) -> None:
    catalog = ProcessCatalog.create(tmp_path, process_instance_id="p1")
    catalog.register_segment(
        stream_kind="event", segment_id="e1", path_token="events/e1.jsonl"
    )
    assert catalog.records[0].previous_catalog_hash is None
    assert catalog.records[1].previous_catalog_hash == catalog.records[0].catalog_record_hash


def test_catalog_rotation_links_previous_segment(tmp_path) -> None:
    catalog = ProcessCatalog.create(tmp_path, process_instance_id="p1")
    previous_segment_id = catalog.segment_id
    previous_hash = catalog.records[-1].catalog_record_hash
    previous_count = len(catalog.records)

    catalog.rotate_catalog_segment()

    lineage = catalog.records[-1]
    assert catalog.segment_id != previous_segment_id
    assert lineage.catalog_sequence == 1
    assert lineage.previous_catalog_hash == previous_hash
    assert lineage.stream_kind == "catalog"
    assert lineage.previous_segment_id == previous_segment_id
    assert lineage.previous_segment_hash == previous_hash
    assert lineage.previous_segment_record_count == previous_count


def test_catalog_rejects_unsafe_path_token(tmp_path) -> None:
    catalog = ProcessCatalog.create(tmp_path, process_instance_id="p1")
    try:
        catalog.register_segment(stream_kind="event", segment_id="e1", path_token="../escape")
    except ValueError as error:
        assert "path_token" in str(error)
    else:
        raise AssertionError("unsafe path token accepted")
