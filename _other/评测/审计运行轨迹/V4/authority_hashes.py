#!/usr/bin/env python3
"""Emit machine-checked SHA-256 values for all V4 authoritative assets."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

VERSION_DIR = Path(__file__).resolve().parent
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DEPENDENCIES = {
    "V2/fixture_stage.py": VERSION_DIR.parent / "V2" / "fixture_stage.py",
    "V3/prompt_send.py": VERSION_DIR.parent / "V3" / "prompt_send.py",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def authoritative_files() -> list[Path]:
    return sorted(
        path
        for path in VERSION_DIR.rglob("*")
        if path.is_file()
        and "运行记录" not in path.parts
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )


def build_report() -> dict[str, object]:
    files = {
        path.relative_to(VERSION_DIR).as_posix(): sha256_file(path)
        for path in authoritative_files()
    }
    dependencies = {name: sha256_file(path) for name, path in DEPENDENCIES.items()}
    digests = [*files.values(), *dependencies.values()]
    valid = bool(files) and all(SHA256_PATTERN.fullmatch(value) for value in digests)
    return {
        "schema_version": 1,
        "valid": valid,
        "algorithm": "sha256",
        "files": files,
        "dependencies": dependencies,
    }


def main() -> int:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
