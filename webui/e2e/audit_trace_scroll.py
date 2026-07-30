#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright, sync_playwright


DEFAULT_BASE_URL = "http://127.0.0.1:5173/e2e/audit-trace.html"


def metrics(page: Page) -> dict[str, object]:
    return page.locator('[data-testid="audit-timeline-viewport"]').evaluate(
        """element => {
          const visible = [...element.querySelectorAll('[data-event-id]')]
            .filter(row => {
              const rect = row.getBoundingClientRect();
              const viewport = element.getBoundingClientRect();
              return rect.bottom > viewport.top && rect.top < viewport.bottom;
            })
            .map(row => row.getAttribute('data-event-id'));
          const rect = element.getBoundingClientRect();
          const state = document.querySelector('[data-testid="fixture-state"]');
          return {
            rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
            clientHeight: element.clientHeight,
            scrollHeight: element.scrollHeight,
            scrollTop: element.scrollTop,
            firstVisible: visible[0] ?? null,
            lastVisible: visible.at(-1) ?? null,
            loadedCount: Number(state?.getAttribute('data-loaded-count') ?? 0),
            nextCursor: state?.getAttribute('data-next-cursor') ?? '',
          };
        }"""
    )


def scroll_to(page: Page, top: int) -> None:
    page.locator('[data-testid="audit-timeline-viewport"]').evaluate(
        "(element, value) => { element.scrollTop = value; element.dispatchEvent(new Event('scroll')); }",
        top,
    )
    page.wait_for_timeout(80)


def click_load_more(page: Page) -> tuple[dict[str, object], dict[str, object]]:
    before = metrics(page)
    page.get_by_role("button", name="加载更多 Event").click()
    page.wait_for_function(
        "previous => Number(document.querySelector('[data-testid=fixture-state]')?.dataset.loadedCount) > previous",
        arg=before["loadedCount"],
    )
    after = metrics(page)
    assert after["scrollTop"] > 0, "加载更多后不应跳回顶部"
    assert int(after["loadedCount"]) > int(before["loadedCount"])
    return before, after


def exercise_desktop(page: Page, engine: str, artifacts: Path, base_url: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    print(f"[{engine}] desktop: start", flush=True)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(base_url)
    page.get_by_text("Audit Trace 536 Event 浏览器夹具").wait_for()
    initial = metrics(page)
    assert initial["loadedCount"] == 200
    assert initial["nextCursor"] == "cursor-200"
    assert initial["firstVisible"] == "event-001"
    assert int(initial["scrollHeight"]) > int(initial["clientHeight"])
    records.append({"scenario": "desktop-initial", **initial})

    viewport = page.locator('[data-testid="audit-timeline-viewport"]')
    viewport.hover()
    page.mouse.wheel(0, 5_000)
    page.wait_for_timeout(120)
    large_delta = metrics(page)
    assert int(large_delta["scrollTop"]) > 0
    records.append({"scenario": "desktop-large-wheel", **large_delta})

    previous_top = int(large_delta["scrollTop"])
    for _ in range(12):
        page.mouse.wheel(0, 24)
    page.wait_for_timeout(120)
    small_delta = metrics(page)
    assert int(small_delta["scrollTop"]) > previous_top
    records.append({"scenario": "desktop-small-wheel", **small_delta})

    scroll_to(page, 10_000_000)
    at_200 = metrics(page)
    assert at_200["lastVisible"] == "event-200"
    records.append({"scenario": "desktop-event-200", **at_200})

    before_201, after_201 = click_load_more(page)
    assert after_201["loadedCount"] == 400
    assert int(after_201["scrollTop"]) >= int(before_201["scrollTop"]) - 2
    scroll_to(page, 200 * 42)
    at_201 = metrics(page)
    assert at_201["firstVisible"] <= "event-201" <= at_201["lastVisible"]
    records.append({"scenario": "desktop-event-201", **at_201})

    scroll_to(page, 10_000_000)
    _, after_536 = click_load_more(page)
    assert after_536["loadedCount"] == 536
    assert after_536["nextCursor"] == ""
    scroll_to(page, 10_000_000)
    at_536 = metrics(page)
    assert at_536["lastVisible"] == "event-536"
    records.append({"scenario": "desktop-event-536", **at_536})

    scroll_to(page, 0)
    assert metrics(page)["firstVisible"] == "event-001"

    page.get_by_role("button", name="最大化时间线").click()
    maximized = metrics(page)
    assert int(maximized["clientHeight"]) > int(initial["clientHeight"])
    records.append({"scenario": "desktop-maximized", **maximized})
    page.get_by_role("button", name="还原时间线高度").click()

    handle = page.get_by_role("button", name="拖拽调整时间线高度")
    box = handle.bounding_box()
    assert box
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] - 120, steps=5)
    page.mouse.up()
    dragged = metrics(page)
    assert int(dragged["clientHeight"]) > int(initial["clientHeight"])
    records.append({"scenario": "desktop-dragged", **dragged})
    page.screenshot(path=str(artifacts / f"{engine}-desktop.png"))
    print(f"[{engine}] desktop: passed", flush=True)
    return records


def exercise_mobile(page: Page, engine: str, artifacts: Path, base_url: str) -> list[dict[str, object]]:
    print(f"[{engine}] mobile: start", flush=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(base_url)
    page.get_by_text("Audit Trace 536 Event 浏览器夹具").wait_for()
    initial = metrics(page)
    assert int(initial["clientHeight"]) > 700
    print(f"[{engine}] mobile: initial metrics", flush=True)
    viewport = page.locator('[data-testid="audit-timeline-viewport"]')
    viewport.hover()
    page.mouse.wheel(0, 10_000)
    page.wait_for_timeout(120)
    scrolled = metrics(page)
    assert int(scrolled["scrollTop"]) > 0
    print(f"[{engine}] mobile: wheel", flush=True)
    scroll_to(page, 10_000_000)
    click_load_more(page)
    print(f"[{engine}] mobile: loaded 400", flush=True)
    scroll_to(page, 10_000_000)
    click_load_more(page)
    print(f"[{engine}] mobile: loaded 536", flush=True)
    scroll_to(page, 10_000_000)
    final = metrics(page)
    assert final["lastVisible"] == "event-536"
    page.screenshot(path=str(artifacts / f"{engine}-mobile.png"))
    print(f"[{engine}] mobile: passed", flush=True)
    return [
        {"scenario": "mobile-initial", **initial},
        {"scenario": "mobile-wheel", **scrolled},
        {"scenario": "mobile-event-536", **final},
    ]


def run_engine(
    playwright: Playwright,
    engine: str,
    artifacts: Path,
    base_url: str,
) -> dict[str, object]:
    browser_type = getattr(playwright, engine)
    browser: Browser = browser_type.launch(headless=True)
    console_errors: list[str] = []
    try:
        desktop = browser.new_page(viewport={"width": 1440, "height": 900})
        desktop.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        records = exercise_desktop(desktop, engine, artifacts, base_url)
        desktop.close()

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        records.extend(exercise_mobile(mobile, engine, artifacts, base_url))
        mobile.close()
        assert not console_errors, f"浏览器 console error: {console_errors}"
        return {"engine": engine, "records": records, "consoleErrors": console_errors}
    finally:
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="append", choices=("chromium", "firefox", "webkit"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    engines = args.browser or ["chromium", "firefox", "webkit"]
    with tempfile.TemporaryDirectory(prefix="nanobot-audit-e2e-") as directory:
        artifacts = Path(directory)
        with sync_playwright() as playwright:
            results = [run_engine(playwright, engine, artifacts, args.base_url) for engine in engines]
        print(json.dumps({"results": results, "screenshots": sorted(path.name for path in artifacts.iterdir())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
