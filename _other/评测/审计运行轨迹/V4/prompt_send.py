#!/usr/bin/env python3
"""Use the frozen exact-send implementation with V4-owned prompt assets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
V3_SCRIPT = SCRIPT_DIR.parent / "V3" / "prompt_send.py"

spec = importlib.util.spec_from_file_location("audit_trace_v4_prompt_send_impl", V3_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load exact-send implementation: {V3_SCRIPT}")
_impl = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = _impl
spec.loader.exec_module(_impl)

PromptIntegrityError = _impl.PromptIntegrityError
PromptFingerprint = _impl.PromptFingerprint
fingerprint = _impl.fingerprint
extract_prompt = _impl.extract_prompt
load_expected = _impl.load_expected
verify_prompt = _impl.verify_prompt
send_exact_prompt = _impl.send_exact_prompt
run_self_test = _impl.run_self_test


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exam", type=Path, default=SCRIPT_DIR / "试卷.md")
    parser.add_argument("--manifest", type=Path, default=SCRIPT_DIR / "prompt-manifest.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        prompt = extract_prompt(args.exam)
        expected = load_expected(args.manifest)
        actual = verify_prompt(prompt, expected)
        result = {"prompt": asdict(actual), "integrity_passed": True}
        if args.self_test:
            result["chromium_self_test"] = run_self_test(prompt, expected)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, json.JSONDecodeError, PromptIntegrityError) as exc:
        print(json.dumps({"integrity_passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
