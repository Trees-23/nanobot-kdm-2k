from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

VERSION_DIR = (
    Path(__file__).resolve().parents[2]
    / "_other"
    / "评测"
    / "审计运行轨迹"
    / "V3"
)


def load_prompt_send():
    path = VERSION_DIR / "prompt_send.py"
    spec = importlib.util.spec_from_file_location("audit_trace_prompt_send", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeParagraph:
    def __init__(self, text: str):
        self._text = text
        self.first = self

    def count(self) -> int:
        return 1

    def text_content(self) -> str:
        return self._text


class FakeMessage:
    def __init__(self, text: str):
        self._text = text

    def locator(self, selector: str) -> FakeParagraph:
        assert selector == "p"
        return FakeParagraph(self._text)


class FakeMessages:
    def __init__(self, page: FakePage):
        self._page = page

    def count(self) -> int:
        return len(self._page.messages)

    def nth(self, index: int) -> FakeMessage:
        return FakeMessage(self._page.messages[index])


class FakeComposer:
    def __init__(self, page: FakePage):
        self._page = page

    def wait_for(self, **kwargs) -> None:
        self._page.calls.append(("composer.wait_for", kwargs))

    def count(self) -> int:
        return 1

    def fill(self, text: str) -> None:
        self._page.calls.append(("composer.fill", text))
        self._page.value = text

    def input_value(self) -> str:
        return self._page.value


class FakeSendButton:
    def __init__(self, page: FakePage):
        self._page = page

    def wait_for(self, **kwargs) -> None:
        self._page.calls.append(("send.wait_for", kwargs))

    def count(self) -> int:
        return 1

    def click(self) -> None:
        self._page.calls.append(("send.click", None))
        self._page.messages.append(self._page.value)
        self._page.value = ""


class FakePage:
    def __init__(self, module):
        self.module = module
        self.messages: list[str] = []
        self.value = ""
        self.calls: list[tuple[str, object]] = []
        self.url = "http://localhost/#/new"

    def locator(self, selector: str):
        if selector == self.module.COMPOSER_SELECTOR:
            return FakeComposer(self)
        if selector == self.module.SEND_SELECTOR:
            return FakeSendButton(self)
        if selector == self.module.USER_MESSAGE_SELECTOR:
            return FakeMessages(self)
        raise AssertionError(f"unexpected selector: {selector}")

    def wait_for_function(self, expression: str, *, arg, timeout: int) -> None:
        assert "querySelectorAll" in expression
        assert arg == [self.module.USER_MESSAGE_SELECTOR, 0]
        assert timeout == 30_000


def test_v3_prompt_matches_frozen_manifest() -> None:
    module = load_prompt_send()
    prompt = module.extract_prompt(VERSION_DIR / "试卷.md")
    expected = module.load_expected(VERSION_DIR / "prompt-manifest.json")

    assert module.verify_prompt(prompt, expected) == expected
    assert expected.chars == 861
    assert expected.utf8_bytes == 1429
    assert expected.sha256 == "c138844c0c0a496df2b4979249ade65f20e1260f8d63e3588613c241b9a31647"


def test_send_exact_prompt_fills_once_and_clicks_once_without_keyboard_events() -> None:
    module = load_prompt_send()
    prompt = module.extract_prompt(VERSION_DIR / "试卷.md")
    expected = module.load_expected(VERSION_DIR / "prompt-manifest.json")
    page = FakePage(module)

    result = module.send_exact_prompt(page, prompt, expected)

    action_names = [name for name, _ in page.calls]
    assert action_names.count("composer.fill") == 1
    assert action_names.count("send.click") == 1
    assert all("press" not in name and "type" not in name for name in action_names)
    assert page.messages == [prompt]
    assert result["message_count_before"] == 0
    assert result["message_count_after"] == 1
    assert result["integrity_passed"] is True


def test_send_exact_prompt_stops_before_send_on_manifest_mismatch() -> None:
    module = load_prompt_send()
    prompt = module.extract_prompt(VERSION_DIR / "试卷.md")
    expected = module.PromptFingerprint(chars=1, utf8_bytes=1, sha256="0" * 64)
    page = FakePage(module)

    with pytest.raises(module.PromptIntegrityError, match="prompt fingerprint mismatch"):
        module.send_exact_prompt(page, prompt, expected)

    assert page.calls == []
    assert page.messages == []


def test_send_exact_prompt_rejects_non_new_or_non_empty_thread() -> None:
    module = load_prompt_send()
    prompt = module.extract_prompt(VERSION_DIR / "试卷.md")
    expected = module.load_expected(VERSION_DIR / "prompt-manifest.json")
    page = FakePage(module)
    page.url = "http://localhost/#/chat/existing"

    with pytest.raises(module.PromptIntegrityError, match="fresh #/new route"):
        module.send_exact_prompt(page, prompt, expected)

    page.url = "http://localhost/#/new"
    page.messages.append("existing message")
    with pytest.raises(module.PromptIntegrityError, match="no existing user messages"):
        module.send_exact_prompt(page, prompt, expected)

    assert all(name != "send.click" for name, _ in page.calls)
