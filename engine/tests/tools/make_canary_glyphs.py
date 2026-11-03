"""Render the verifier's self-test letters once: ``src/docsan/data/canary-glyphs.json``.

The verifier draws its random test name from these letters, so it needs no
font engine of its own. Run after changing the letter set:
``uv run python tests/tools/make_canary_glyphs.py``.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from imagekit import renderer
from pngio import read_png

LETTERS = "BCDFGHJKLMNPRTVWXZ"
OUT = HERE.parents[1] / "src" / "docsan" / "data" / "canary-glyphs.json"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        grays = {}
        for letter in LETTERS:
            path = Path(tmp) / f"{letter}.png"
            spec = {"output": str(path), "format": "png", "width": 80, "height": 72,
                    "items": [{"text": letter, "x": 12, "y": 8, "size": 44, "font": "Helvetica"}]}
            subprocess.run([str(renderer())], input=json.dumps(spec).encode(), check=True, capture_output=True)
            px = read_png(path.read_bytes())
            gray = bytes(min(px.data[i : i + px.channels][:3]) for i in range(0, len(px.data), px.channels))
            grays[letter] = (px.width, px.height, gray)
        ink_rows = [y for w, h, g in grays.values() for y in range(h) if min(g[y * w : (y + 1) * w]) < 250]
        top, bottom = max(0, min(ink_rows) - 2), max(ink_rows) + 3
        letters = {}
        for letter, (w, h, g) in grays.items():
            cols = [x for x in range(w) if min(g[y * w + x] for y in range(h)) < 250]
            left, right = max(0, cols[0] - 1), min(w, cols[-1] + 2)
            pixels = b"".join(g[y * w + left : y * w + right] for y in range(top, bottom))
            letters[letter] = {"width": right - left, "pixels": base64.b64encode(zlib.compress(pixels, 9)).decode()}
    OUT.write_text(json.dumps({"font": "Helvetica 44", "height": bottom - top, "letters": letters}, indent=1) + "\n")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
