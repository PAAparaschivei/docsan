#!/usr/bin/env python3
"""Generate one small, invented Word document using the synthetic test builder."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine" / "tests"))
import leakmatrix as lm


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: make_demo.py NEW_OUTPUT_DIRECTORY")
    target = Path(sys.argv[1]).resolve()
    target.mkdir(parents=True, exist_ok=True)
    output = target / "input.docx"
    if output.exists():
        raise SystemExit("The example input already exists; choose a new output directory.")
    output.write_bytes(lm.doc(
        lm.text_para("Aurelan Group runs the integration platform.")
        + lm.text_para("Elena Marchetti approved the plan.")
    ))
    print("Created input.docx with invented company and person names.")


if __name__ == "__main__":
    main()
