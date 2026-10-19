#!/usr/bin/env python3
"""Build the installable zip.

Decky wants a flat archive containing main.py, plugin.json, package.json, dist/
and py_modules/. Node's output and __pycache__ do not belong in it, and the
template's stubs do not either: shipping the Rust helper it uses to exercise
native calls would be dead weight in a plugin that does not need it.

    python scripts/package.py
"""

import argparse
import pathlib
import shutil
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
NAME = "decky-capture"

# Copied verbatim. Anything here that is absent is not required.
WANTED_TOP = ["main.py", "plugin.json", "package.json", "README.md", "LICENSE"]
WANTED_DIRS = ["dist", "py_modules/decky_capture", "assets"]

# Never shipped, whatever they are.
EXCLUDE_DIRS = {"__pycache__", "node_modules", ".git", "bin", "obj", ".vscode"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".map", ".ts", ".tsx"}


def is_excluded(path: pathlib.Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return True
    return path.suffix in EXCLUDE_SUFFIX


def collect() -> list:
    files = []
    for name in WANTED_TOP:
        path = ROOT / name
        if path.is_file():
            files.append(path)
        else:
            print(f"  missing {name}, skipping", file=sys.stderr)

    for directory in WANTED_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            print(f"  missing {directory}/, skipping", file=sys.stderr)
            continue
        for path in base.rglob("*"):
            if path.is_file() and not is_excluded(path.relative_to(ROOT)):
                files.append(path)
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "out" / f"{NAME}.zip"))
    args = parser.parse_args()

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = collect()
    if not files:
        print("nothing to package; run the frontend build first", file=sys.stderr)
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, str(path.relative_to(ROOT)).replace("\\", "/"))

    print(out)
    with zipfile.ZipFile(out) as archive:
        for name in archive.namelist():
            print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

