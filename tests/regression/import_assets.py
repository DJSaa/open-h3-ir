# SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
# SPDX-License-Identifier: Apache-2.0
"""Import external assets by SHA256; never substitute visually similar files."""
import argparse
from pathlib import Path
import shutil
from common import ROOT, digest, load, owned


def import_assets(source, root=ROOT):
    manifest = load(Path(root) / "assets/manifest.json")
    targets = {sha: meta for sha, meta in manifest.items() if not meta["bundled"]}
    source = Path(source)
    if not source.is_dir():
        raise ValueError("source must be a local directory")
    sizes = {m["bytes"] for m in targets.values()}
    for file in source.rglob("*"):
        if not file.is_file() or file.is_symlink() or file.stat().st_size not in sizes:
            continue
        sha = digest(file)
        if sha not in targets:
            continue
        destination = owned(root, targets[sha]["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copyfile(file, destination)
        if digest(destination) != sha:
            raise ValueError("existing local asset differs from expected bytes")
        targets.pop(sha)
    return sorted(targets)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    missing = import_assets(parser.parse_args().source)
    print("External assets still missing:", len(missing))
    raise SystemExit(bool(missing))
