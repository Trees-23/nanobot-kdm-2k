import pytest

from nanobot.audit.types import (
    AuditMode,
    CatalogRecordType,
    DeliveryStatus,
    EventType,
    IntegrityStatus,
    PayloadKind,
    ProviderRouteAction,
    RunStatus,
    ToolStatus,
)


def test_closed_enums_accept_contract_values() -> None:
    assert RunStatus("succeeded") is RunStatus.SUCCEEDED
    assert IntegrityStatus("incomplete") is IntegrityStatus.INCOMPLETE
    assert PayloadKind("model_request") is PayloadKind.MODEL_REQUEST
    assert EventType("tool_finished") is EventType.TOOL_FINISHED
    assert ProviderRouteAction("circuit_skipped") is ProviderRouteAction.CIRCUIT_SKIPPED
    assert DeliveryStatus("accepted_by_adapter") is DeliveryStatus.ACCEPTED_BY_ADAPTER
    assert CatalogRecordType("epoch_committed") is CatalogRecordType.EPOCH_COMMITTED
    assert AuditMode("metadata_only") is AuditMode.METADATA_ONLY
    assert ToolStatus("blocked") is ToolStatus.BLOCKED


@pytest.mark.parametrize(
    "enum_type",
    [AuditMode, CatalogRecordType, DeliveryStatus, EventType, IntegrityStatus, PayloadKind],
)
def test_closed_enums_reject_unknown_values(enum_type: type) -> None:
    with pytest.raises(ValueError):
        enum_type("future-unknown")


def test_event_and_payload_contract_counts_are_locked() -> None:
    assert len(EventType) == 61
    assert len(PayloadKind) == 13
