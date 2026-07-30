import pytest

from nanobot.config.schema import AuditConfig, Config


def test_audit_config_defaults_to_full() -> None:
    config = Config()
    assert config.audit.mode == "full"
    assert config.audit.segment_max_bytes == 67_108_864
    assert config.audit.writer_queue_capacity == 4096
    assert config.audit.writer_queue_max_bytes == 268_435_456


def test_audit_config_accepts_camel_case() -> None:
    config = Config.model_validate(
        {"audit": {"criticalAckTimeoutMs": 1500, "previewMaxChars": 800}}
    )
    assert config.audit.critical_ack_timeout_ms == 1500
    assert config.audit.preview_max_chars == 800


def test_audit_config_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError):
        AuditConfig(mode="raw")
