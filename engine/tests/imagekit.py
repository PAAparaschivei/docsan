"""Build and run the Swift tools the image tests need.

``helper()`` builds the app's Vision helper from ``app/Helpers``,
``renderer()`` the test-only image renderer and ``pdf_maker()`` the
test-only PDF maker. Builds are cached by source hash in the system
temporary folder.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from pngio import Pixels, read_png

ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "app" / "Helpers" / "docsan-vision"
RENDERER_SOURCE = Path(__file__).resolve().parent / "tools" / "render-image.swift"
PDF_MAKER_SOURCE = Path(__file__).resolve().parent / "tools" / "make-pdf.swift"


def _build(source: Path | list[Path], name: str, extra: list[str] = ()) -> Path:
    sources = source if isinstance(source, list) else [source]
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest()[:16]
    # The program keeps its name: macOS caches Vision's compiled models by
    # program name, and recompiling them can take many minutes.
    out = Path(tempfile.gettempdir()) / "docsan-test-build" / digest / name
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        subprocess.run(
            ["xcrun", "swiftc", "-O", "-target", "arm64-apple-macos26.0", "-o", str(tmp), *map(str, sources), *extra],
            check=True,
            capture_output=True,
        )
        tmp.rename(out)
    return out


@functools.cache
def helper() -> Path:
    path = _build(sorted(HELPER_DIR.glob("*.swift")), "docsan-vision")
    os.environ["DOCSAN_VISION"] = str(path)
    return path


@functools.cache
def renderer() -> Path:
    return _build(RENDERER_SOURCE, "render-image")


@functools.cache
def pdf_maker() -> Path:
    return _build(PDF_MAKER_SOURCE, "make-pdf")


def make_pdf(spec: dict) -> list[list[dict]]:
    """Write a fixture PDF; returns each page's words with their ink boxes in points from the top left."""
    proc = subprocess.run([str(pdf_maker())], input=json.dumps(spec).encode(), check=True, capture_output=True)
    return [page["words"] for page in json.loads(proc.stdout)["pages"]]


def render(spec: dict) -> list[dict]:
    """Render a fixture; returns the ink box of every word."""
    proc = subprocess.run([str(renderer())], input=json.dumps(spec).encode(), check=True, capture_output=True)
    return json.loads(proc.stdout)["words"]


def pixels(path: Path) -> Pixels:
    """Decoded pixels of a PNG or JPEG; JPEGs are converted by macOS ``sips``."""
    data = path.read_bytes()
    if data.startswith(b"\x89PNG"):
        return read_png(data)
    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "decoded.png"
        subprocess.run(["/usr/bin/sips", "-s", "format", "png", str(path), "--out", str(png)], check=True, capture_output=True)
        return read_png(png.read_bytes())
