"""Docker WebUI build-context regression tests."""

from pathlib import Path


def test_webui_builder_copies_channel_plugins_before_vite_build() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    copy_plugins = dockerfile.index("COPY nanobot/channels/ /app/nanobot/channels/")
    build_webui = dockerfile.index("RUN mkdir -p /app/nanobot/web && npm run build")

    assert copy_plugins < build_webui
