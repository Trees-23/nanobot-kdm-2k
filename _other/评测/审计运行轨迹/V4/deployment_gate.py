#!/usr/bin/env python3
"""Verify the deployed gateway/WebUI pair without comparing unrelated host builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[3]
CONTAINER_DIST = Path("/app/nanobot/web/dist")
BACKEND_FILES = (
    Path("nanobot/audit/graph.py"),
    Path("nanobot/webui/audit_api.py"),
)
REQUIRED_BUNDLE_MARKERS = (
    "recovery_status",
    "当前 Graph 未提供原始 Event 导航信息",
    "定位首个异常",
    "执行上下文",
    "结构分支",
    "恢复链路",
    "5 页",
    "1000 Event",
    "10 秒",
)
SCRIPT_PATTERN = re.compile(rb'src="/?(?P<path>assets/index-[^" ]+\.js)"')


class GateError(RuntimeError):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def run(*args: str) -> bytes:
    try:
        return subprocess.run(args, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GateError(f"command failed: {' '.join(args)}") from exc


def http_get(url: str) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (OSError, urllib.error.URLError) as exc:
        raise GateError(f"request failed: {url}") from exc


def extract_main_bundle(index: bytes) -> str:
    match = SCRIPT_PATTERN.search(index)
    if not match:
        raise GateError("cannot find main JavaScript bundle in served index")
    return match.group("path").decode("ascii")


def container_file(container: str, path: Path) -> bytes:
    return run("docker", "exec", container, "cat", str(path))


def collect(container: str, base_url: str) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    health_status, _ = http_get(f"{base_url}/health")
    index_status, served_index = http_get(f"{base_url}/")
    audit_status, _ = http_get(f"{base_url}/api/audit/traces")
    bundle_path = extract_main_bundle(served_index)
    bundle_status, served_bundle = http_get(f"{base_url}/{bundle_path}")

    container_index = container_file(container, CONTAINER_DIST / "index.html")
    container_bundle = container_file(container, CONTAINER_DIST / bundle_path)
    inspect = json.loads(run("docker", "inspect", container))[0]

    backend: list[dict[str, Any]] = []
    for relative_path in BACKEND_FILES:
        host_content = (REPO_ROOT / relative_path).read_bytes()
        deployed_content = container_file(container, Path("/app") / relative_path)
        backend.append(
            {
                "path": relative_path.as_posix(),
                "host_sha256": sha256_bytes(host_content),
                "container_sha256": sha256_bytes(deployed_content),
            }
        )

    host_index_path = REPO_ROOT / "nanobot/web/dist/index.html"
    host_index = host_index_path.read_bytes() if host_index_path.is_file() else None
    return {
        "container": {
            "name": container,
            "id": inspect["Id"],
            "image_id": inspect["Image"],
            "status": inspect["State"]["Status"],
        },
        "http": {
            "base_url": base_url,
            "health_status": health_status,
            "index_status": index_status,
            "audit_status": audit_status,
            "bundle_status": bundle_status,
        },
        "served": {
            "index_sha256": sha256_bytes(served_index),
            "bundle_path": bundle_path,
            "bundle_sha256": sha256_bytes(served_bundle),
        },
        "container_dist": {
            "index_sha256": sha256_bytes(container_index),
            "bundle_sha256": sha256_bytes(container_bundle),
        },
        "backend_files": backend,
        "bundle_markers": {
            marker: marker.encode("utf-8") in served_bundle for marker in REQUIRED_BUNDLE_MARKERS
        },
        "host_dist_reference": {
            "present": host_index is not None,
            "index_sha256": sha256_bytes(host_index) if host_index is not None else None,
            "matches_deployed": host_index == served_index if host_index is not None else None,
            "gate_effect": "informational_only",
        },
    }


def evaluate(evidence: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "container_running": evidence["container"]["status"] == "running",
        "health_available": evidence["http"]["health_status"] == 200,
        "webui_available": evidence["http"]["index_status"] == 200,
        "audit_route_present": evidence["http"]["audit_status"] in (200, 401),
        "bundle_available": evidence["http"]["bundle_status"] == 200,
        "served_index_is_container_index": (
            evidence["served"]["index_sha256"]
            == evidence["container_dist"]["index_sha256"]
        ),
        "served_bundle_is_container_bundle": (
            evidence["served"]["bundle_sha256"]
            == evidence["container_dist"]["bundle_sha256"]
        ),
        "backend_files_match": all(
            item["host_sha256"] == item["container_sha256"]
            for item in evidence["backend_files"]
        ),
        "required_bundle_markers_present": all(evidence["bundle_markers"].values()),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "evidence": evidence,
        "policy": {
            "host_dist_hash_mismatch_is_failure": False,
            "reason": "浏览器实际 WebUI 只与运行中容器自身 dist 比较；宿主机独立构建不在部署请求路径中。",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", default="nanobot-gateway")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    try:
        result = evaluate(collect(args.container, args.base_url))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["passed"] else 1
    except (GateError, OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
