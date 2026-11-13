# SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
# SPDX-License-Identifier: Apache-2.0
"""Check frozen input integrity without calling a model or service."""
import argparse
from collections import Counter
import json
from pathlib import Path
from common import ROOT, asset_path, load, owned


def validate(root=ROOT, require_all=False):
    root = Path(root)
    catalog = load(root / "cases/index.json")
    manifest = load(root / "assets/manifest.json")
    cases = catalog["cases"]
    if len(cases) != 126 or len({c["id"] for c in cases}) != 126:
        raise ValueError("expected 126 unique frozen requests")
    if Counter(c["suite"] for c in cases) != {"comprehensive": 48, "prompt_quality": 78}:
        raise ValueError("suite counts changed")
    if sum(c["negative"] for c in cases) != 5:
        raise ValueError("expected five separately scored negative requests")
    missing = []
    for sha, meta in manifest.items():
        if meta["sha256"] != sha:
            raise ValueError("manifest hash key mismatch")
        try:
            asset_path(meta, root)
        except FileNotFoundError:
            if meta["bundled"] or require_all:
                raise ValueError("required asset missing: " + sha) from None
            missing.append(sha)
    for case in cases:
        request = load(owned(root, case["request_file"]))
        expected = load(owned(root, case["expectations_file"]))
        direct, gateway = request["direct"], request["gateway_fields"]
        if expected["case_id"] != case["id"]:
            raise ValueError("expectation points to another case")
        if owned(root, case["prompt_file"]).read_text() != direct["intent"] + "\n" or gateway["prompt"] != direct["intent"]:
            raise ValueError("frozen prompt mismatch: " + case["id"])
        if [a["sha256"] for a in direct["assets"]] != [f["asset_id"] for f in request["files"]]:
            raise ValueError("reference order mismatch")
        if any(f["asset_id"] not in manifest for f in request["files"]):
            raise ValueError("unregistered reference")
        if float(gateway["seconds"]) != direct["seconds"] or gateway["size"] != direct["aspect"].replace(":", "x"):
            raise ValueError("duration/canvas mismatch")
        options = json.loads(gateway["reference_options"])
        if len({o["index"] for o in options}) != len(options) or any(not 0 <= o["index"] < len(request["files"]) for o in options):
            raise ValueError("invalid reference options")
        for i, entry in enumerate(direct["assets"]):
            option = next((o for o in options if o["index"] == i), {})
            if option.get("role") != entry.get("role"):
                raise ValueError("explicit reference role/omission mismatch")
    if len(list((root / "fixtures").glob("V*.json"))) != 6:
        raise ValueError("expected six separately reviewed output fixtures")
    for profile in load(root / "profiles.json").values():
        if set(profile) - {c["id"] for c in cases}:
            raise ValueError("profile contains unknown cases")
    return {"cases": 126, "normal": 121, "negative": 5, "assets": len(manifest),
            "output_fixtures": 6, "missing_external_assets": missing, "network_requests": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-all-assets", action="store_true")
    args = parser.parse_args()
    print(json.dumps(validate(require_all=args.require_all_assets), indent=2))
