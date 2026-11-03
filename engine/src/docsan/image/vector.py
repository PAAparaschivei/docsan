"""Vector drawings in Word files: turned into pictures, then read like any picture.

Visio diagrams and many pasted drawings reach Word as EMF, which macOS cannot
draw, and LibreOffice cannot run inside the app's sandbox. The Vision helper
therefore replays the EMF drawing records itself (``EMF.swift``) and reports
every piece of text it drew with the box of each character. The engine
matches that text exactly, in addition to what OCR reads in the picture.
WMF and SVG-only pictures are not drawn yet; Word keeps a PNG copy of every
SVG it inserts, and that copy is used instead.

A drawing that would come out incomplete is refused rather than shown: one
that uses a record the helper cannot draw, or one stored only as EMF+ with a
placeholder in plain EMF. A diagram with a missing arrow or label would
misstate the original.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .helper import HelperError, call

DPI = 220.0


class VectorError(Exception):
    pass


# EMF record types by name, for messages.
RECORD_NAMES = {
    26: "OFFSETCLIPRGN", 53: "EXTFLOODFILL", 73: "INVERTRGN", 76: "BITBLT", 77: "STRETCHBLT", 78: "MASKBLT",
    79: "PLGBLT", 81: "STRETCHDIBITS", 93: "CREATEMONOBRUSH", 94: "CREATEDIBPATTERNBRUSHPT", 96: "POLYTEXTOUTA",
    97: "POLYTEXTOUTW", 102: "GLSRECORD", 103: "GLSBOUNDEDRECORD", 116: "TRANSPARENTBLT", 118: "GRADIENTFILL",
}


@dataclass
class Drawing:
    png: bytes
    texts: list[dict]  # {"text", "box", "chars"} in pixels of the picture


def vector_kind(data: bytes) -> str | None:
    """"emf", "wmf" or "svg" by content, else None."""
    if len(data) >= 44 and data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF":
        return "emf"
    if data[:4] == b"\xd7\xcd\xc6\x9a" or data[:6] in (b"\x01\x00\x09\x00\x00\x03", b"\x02\x00\x09\x00\x00\x03"):
        return "wmf"
    head = data[:1024].lstrip().lower()
    if head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head):
        return "svg"
    return None


def render(data: bytes, workdir: Path) -> Drawing:
    kind = vector_kind(data)
    if kind != "emf":
        raise VectorError(f"{(kind or 'vector').upper()} drawings cannot be read yet; use the placeholder option")
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        src, out = Path(tmp) / "drawing.emf", Path(tmp) / "drawing.png"
        src.write_bytes(data)
        try:
            answer = call({"command": "render-emf", "input": str(src), "output": str(out), "dpi": DPI})
        except HelperError as exc:
            raise VectorError(f"cannot draw the drawing: {exc}") from exc
        if answer.get("emf_plus_only"):
            raise VectorError("the drawing is stored as EMF+ only, which DocSan cannot draw yet; use the placeholder option")
        skipped = {int(k): v for k, v in answer.get("skipped", {}).items()}
        if skipped:
            parts = ", ".join(f"{RECORD_NAMES.get(k, f'record {k}')} ({v})" for k, v in sorted(skipped.items()))
            raise VectorError(f"part of the drawing cannot be drawn ({parts}), and DocSan does not show an incomplete diagram; "
                              "use the placeholder option")
        return Drawing(out.read_bytes(), answer.get("texts", []))
