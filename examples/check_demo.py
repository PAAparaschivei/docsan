#!/usr/bin/env python3
"""Check the invented references and useful text in the synthetic demo output."""

from pathlib import Path
import sys
import zipfile


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: check_demo.py DEMO_EXPORT_DIRECTORY")
    files = list(Path(sys.argv[1]).glob("*.docx"))
    if len(files) != 1:
        raise SystemExit("Expected exactly one exported Word file.")
    with zipfile.ZipFile(files[0]) as package:
        data = b"\n".join(package.read(name) for name in package.namelist()).lower()
    if b"aurelan" in data or b"marchetti" in data:
        raise SystemExit("A planted reference remains in the demo output.")
    if not all(word in data for word in [b"integration", b"platform", b"approved", b"plan"]):
        raise SystemExit("The demo lost expected non-identifying text.")
    print("Demo passed: planted references removed; expected context preserved.")


if __name__ == "__main__":
    main()
