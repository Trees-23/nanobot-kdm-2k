import pytest

from nanobot.audit.redaction import AuditRedactor, RedactionError


def test_redacts_nested_structured_credentials() -> None:
    redactor = AuditRedactor()
    cleaned, report = redactor.redact(
        {
            "headers": {"Authorization": "Bearer secret"},
            "nested": [{"api_key": "sk-test-123"}],
        }
    )
    assert cleaned["headers"]["Authorization"] == "[REDACTED:CREDENTIAL]"
    assert cleaned["nested"][0]["api_key"] == "[REDACTED:CREDENTIAL]"
    assert report.replacement_count == 2


def test_redacts_builtin_bearer_in_free_text() -> None:
    cleaned, report = AuditRedactor().redact("request used Bearer abc.def-123")
    assert cleaned == "request used [REDACTED:CREDENTIAL]"
    assert report.replacement_count == 1


def test_preserves_unknown_opaque_text() -> None:
    value = "opaque-business-value-123"
    cleaned, report = AuditRedactor().redact(value)
    assert cleaned == value
    assert report.replacement_count == 0


def test_configured_pattern_redacts_deployment_secret() -> None:
    redactor = AuditRedactor(additional_patterns=[r"ACME_[A-Z0-9]{16}"])
    cleaned, _ = redactor.redact("tokenish=ACME_1234567890ABCDEF")
    assert cleaned == "tokenish=[REDACTED:CREDENTIAL]"


def test_additional_secret_key_is_normalized() -> None:
    cleaned, _ = AuditRedactor(additional_keys=["deployment credential"]).redact(
        {"deploymentCredential": "value"}
    )
    assert cleaned["deploymentCredential"] == "[REDACTED:CREDENTIAL]"


def test_invalid_custom_pattern_does_not_echo_pattern() -> None:
    with pytest.raises(ValueError, match="custom_pattern_0") as error:
        AuditRedactor(additional_patterns=["SECRET_VALUE_("])
    assert "SECRET_VALUE" not in str(error.value)


def test_substitution_failure_does_not_echo_candidate(monkeypatch) -> None:
    redactor = AuditRedactor()

    def fail(*_args, **_kwargs):
        raise RuntimeError("engine failure")

    monkeypatch.setattr(redactor, "_substitute", fail)
    with pytest.raises(RedactionError, match="builtin_bearer") as error:
        redactor.redact("Bearer DO_NOT_ECHO")
    assert "DO_NOT_ECHO" not in str(error.value)
