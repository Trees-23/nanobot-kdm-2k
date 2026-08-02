#!/usr/bin/env python3
"""Safely fill and submit one exact multiline audit-evaluation prompt."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SECTION_HEADING = "## 发送给被测 Agent 的唯一输入"
TEXT_BLOCK_PATTERN = re.compile(r"```text\n(?P<prompt>.*?)\n```", re.DOTALL)
COMPOSER_SELECTOR = (
    'textarea[aria-label="消息输入框"], '
    'textarea[aria-label="Message input"]'
)
SEND_SELECTOR = (
    'button[aria-label="发送消息"], '
    'button[aria-label="Send message"]'
)
USER_MESSAGE_SELECTOR = "[data-user-prompt-id]"


class PromptIntegrityError(RuntimeError):
    """Raised when the prompt cannot be proven to be one exact message."""


@dataclass(frozen=True)
class PromptFingerprint:
    chars: int
    utf8_bytes: int
    sha256: str


def fingerprint(text: str) -> PromptFingerprint:
    encoded = text.encode("utf-8")
    return PromptFingerprint(
        chars=len(text),
        utf8_bytes=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def extract_prompt(exam_path: Path) -> str:
    source = exam_path.read_text(encoding="utf-8")
    heading_count = source.count(SECTION_HEADING)
    if heading_count != 1:
        raise PromptIntegrityError(
            f"expected one prompt section, found {heading_count}: {SECTION_HEADING}"
        )
    section = source.split(SECTION_HEADING, 1)[1]
    next_heading = re.search(r"\n## ", section)
    if next_heading:
        section = section[: next_heading.start()]
    blocks = list(TEXT_BLOCK_PATTERN.finditer(section))
    if len(blocks) != 1:
        raise PromptIntegrityError(f"expected one text block in prompt section, found {len(blocks)}")
    prompt = blocks[0].group("prompt")
    if not prompt.strip() or prompt != prompt.strip("\n"):
        raise PromptIntegrityError("prompt block must be non-empty without boundary blank lines")
    return prompt


def load_expected(manifest_path: Path) -> PromptFingerprint:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise PromptIntegrityError("unsupported prompt manifest schema_version")
    try:
        return PromptFingerprint(
            chars=int(data["prompt"]["chars"]),
            utf8_bytes=int(data["prompt"]["utf8_bytes"]),
            sha256=str(data["prompt"]["sha256"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PromptIntegrityError("invalid prompt manifest") from exc


def verify_prompt(prompt: str, expected: PromptFingerprint) -> PromptFingerprint:
    actual = fingerprint(prompt)
    if actual != expected:
        raise PromptIntegrityError(
            "prompt fingerprint mismatch: "
            f"expected={asdict(expected)} actual={asdict(actual)}"
        )
    return actual


def _message_text(message: Any) -> str:
    paragraphs = message.locator("p")
    if paragraphs.count() != 1:
        raise PromptIntegrityError(
            f"expected exactly one text paragraph in submitted user message, found {paragraphs.count()}"
        )
    text = paragraphs.first.text_content()
    if text is None:
        raise PromptIntegrityError("submitted user message has no text content")
    return text


def send_exact_prompt(
    page: Any,
    prompt: str,
    expected: PromptFingerprint,
    *,
    timeout_ms: int = 30_000,
) -> dict[str, Any]:
    """Fill without key presses, click once, and prove exactly one UI message was added."""

    verify_prompt(prompt, expected)
    if "#/new" not in page.url:
        raise PromptIntegrityError(f"expected fresh #/new route before send, got: {page.url}")
    composer = page.locator(COMPOSER_SELECTOR)
    composer.wait_for(state="visible", timeout=timeout_ms)
    if composer.count() != 1:
        raise PromptIntegrityError(f"expected one visible composer, found {composer.count()}")

    messages = page.locator(USER_MESSAGE_SELECTOR)
    before_count = messages.count()
    if before_count != 0:
        raise PromptIntegrityError(
            f"expected no existing user messages in fresh thread, found {before_count}"
        )

    # fill() sets the complete textarea value without generating Enter key events.
    composer.fill(prompt)
    filled = composer.input_value()
    filled_fingerprint = verify_prompt(filled, expected)

    send_button = page.locator(SEND_SELECTOR)
    send_button.wait_for(state="visible", timeout=timeout_ms)
    if send_button.count() != 1:
        raise PromptIntegrityError(f"expected one visible send button, found {send_button.count()}")
    send_button.click()

    page.wait_for_function(
        "([selector, count]) => document.querySelectorAll(selector).length === count + 1",
        arg=[USER_MESSAGE_SELECTOR, before_count],
        timeout=timeout_ms,
    )
    after_count = messages.count()
    if after_count != before_count + 1:
        raise PromptIntegrityError(
            f"expected one new user message, before={before_count} after={after_count}"
        )

    submitted = _message_text(messages.nth(after_count - 1))
    submitted_fingerprint = verify_prompt(submitted, expected)
    return {
        "message_count_before": before_count,
        "message_count_after": after_count,
        "filled": asdict(filled_fingerprint),
        "submitted": asdict(submitted_fingerprint),
        "page_url_after_send": page.url,
        "integrity_passed": True,
    }


def run_self_test(prompt: str, expected: PromptFingerprint) -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    html = """
    <!doctype html><html><body>
      <textarea aria-label="消息输入框"></textarea>
      <button aria-label="发送消息">send</button>
      <section id="messages"></section>
      <script>
        const input = document.querySelector('textarea');
        const send = () => {
          const wrapper = document.createElement('div');
          wrapper.setAttribute('data-user-prompt-id', String(document.querySelectorAll('[data-user-prompt-id]').length + 1));
          const paragraph = document.createElement('p');
          paragraph.textContent = input.value;
          wrapper.appendChild(paragraph);
          document.querySelector('#messages').appendChild(wrapper);
          input.value = '';
        };
        input.addEventListener('keydown', event => {
          if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            send();
          }
        });
        document.querySelector('button').addEventListener('click', send);
      </script>
    </body></html>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")
            page.evaluate("location.hash = '/new'")
            return send_exact_prompt(page, prompt, expected)
        finally:
            browser.close()


def main() -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exam", type=Path, default=script_dir / "试卷.md")
    parser.add_argument("--manifest", type=Path, default=script_dir / "prompt-manifest.json")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the exact-send protocol against a local Chromium fixture",
    )
    args = parser.parse_args()

    try:
        prompt = extract_prompt(args.exam)
        expected = load_expected(args.manifest)
        actual = verify_prompt(prompt, expected)
        result: dict[str, Any] = {"prompt": asdict(actual), "integrity_passed": True}
        if args.self_test:
            result["chromium_self_test"] = run_self_test(prompt, expected)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, PromptIntegrityError) as exc:
        print(json.dumps({"integrity_passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
