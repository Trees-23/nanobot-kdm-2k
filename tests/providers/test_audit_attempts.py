from io import StringIO

from loguru import logger

from nanobot.providers.base import LLMResponse
from nanobot.providers.observed_call import ProviderAttemptObserver
from tests.providers.test_provider_retry import ScriptedProvider


class RecordingAttemptObserver(ProviderAttemptObserver):
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def attempt_started(self, snapshot) -> None:
        self.events.append(("attempt_started", snapshot))

    async def attempt_finished(self, snapshot) -> None:
        self.events.append(("attempt_finished", snapshot))

    async def route_decision(self, decision) -> None:
        self.events.append(("route_decision", decision))

    async def retry_scheduled(self, retry) -> None:
        self.events.append(("retry_scheduled", retry))


async def test_transient_retry_emits_two_real_attempts(monkeypatch) -> None:
    observer = RecordingAttemptObserver()
    provider = ScriptedProvider(
        [
            LLMResponse(content="timeout", finish_reason="error", error_kind="timeout"),
            LLMResponse(content="done"),
        ]
    )

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", no_sleep)
    await provider.chat_with_retry(messages=[], model="m", attempt_observer=observer)

    assert [event[0] for event in observer.events] == [
        "attempt_started",
        "attempt_finished",
        "retry_scheduled",
        "attempt_started",
        "attempt_finished",
    ]
    starts = [event[1] for event in observer.events if event[0] == "attempt_started"]
    assert starts[0].attempt_id != starts[1].attempt_id
    assert [start.attempt_ordinal for start in starts] == [1, 2]


async def test_image_stripped_retry_is_another_attempt() -> None:
    observer = RecordingAttemptObserver()
    provider = ScriptedProvider(
        [
            LLMResponse(content="invalid image", finish_reason="error"),
            LLMResponse(content="done"),
        ]
    )
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
            ],
        }
    ]
    await provider.chat_with_retry(
        messages=messages,
        model="m",
        attempt_observer=observer,
    )

    starts = [event[1] for event in observer.events if event[0] == "attempt_started"]
    assert [start.input_variant for start in starts] == ["original", "without_images"]
    assert any(
        event[0] == "route_decision" and event[1].action == "image_stripped_retry"
        for event in observer.events
    )


async def test_retry_logs_do_not_contain_response_canary(monkeypatch) -> None:
    provider = ScriptedProvider(
        [
            LLMResponse(content="SECRET_CANARY timeout", finish_reason="error"),
            LLMResponse(content="done"),
        ]
    )
    output = StringIO()
    sink = logger.add(output, format="{message}")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("nanobot.providers.base.asyncio.sleep", no_sleep)
    try:
        await provider.chat_with_retry(messages=[])
    finally:
        logger.remove(sink)
    assert "SECRET_CANARY" not in output.getvalue()
