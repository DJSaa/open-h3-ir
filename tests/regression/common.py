# SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
# SPDX-License-Identifier: Apache-2.0
"""Portable case loading and technical-result classification; no service access."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def owned(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("path escapes the regression package")
    return path


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def asset_path(meta, root=ROOT):
    path = owned(root, meta["path"])
    if not path.is_file():
        raise FileNotFoundError(meta["sha256"])
    if path.stat().st_size != meta["bytes"] or digest(path) != meta["sha256"]:
        raise ValueError("asset checksum mismatch: " + meta["sha256"])
    return path


def classify(case, http, body, transport):
    """Technical pass is separate from human semantic/video quality assessment."""
    if not isinstance(body, dict):
        return "FAIL_PROTOCOL"
    if case["negative"]:
        if http in (400, 422):
            return "PASS_REJECTION"
        if transport == "direct" and http in (200, 201) and body.get("status") == "needs_input" and body.get("question"):
            return "PASS_CLARIFICATION"
        return "FAIL_NEGATIVE_INPUT"
    if http not in (200, 201):
        return "FAIL_HTTP"
    if transport == "gateway":
        if body.get("status") != "completed":
            return "FAIL_TASK"
        prompt = body.get("content", {}).get("prompt")
        degraded = any(w.get("code") == "context_ir_degraded_result"
                       for w in body.get("warnings", []) if isinstance(w, dict))
    else:
        prompt = body.get("ir", {}).get("prompt")
        degraded = body.get("status") == "degraded" or body.get("source") == "draft" or bool(body.get("fallback_reason"))
        if not degraded and (body.get("status"), body.get("source")) != ("ready", "written"):
            return "FAIL_PROTOCOL"
    if not isinstance(prompt, str) or not prompt.strip():
        return "FAIL_EMPTY_PROMPT"
    return "DEGRADED" if degraded else "PASS_TECHNICAL"


def select_cases(profile=None, ids=None, suite=None, root=ROOT):
    cases = load(Path(root) / "cases/index.json")["cases"]
    wanted = set(ids or [])
    if profile:
        profiles = load(Path(root) / "profiles.json")
        if profile not in profiles:
            raise ValueError("unknown profile: " + profile)
        wanted.update(profiles[profile])
    known = {c["id"] for c in cases}
    if wanted - known:
        raise ValueError("unknown case IDs: " + ", ".join(sorted(wanted - known)))
    result = [c for c in cases if (not wanted or c["id"] in wanted) and (not suite or c["suite"] == suite)]
    if not result:
        raise ValueError("no cases selected")
    return result
