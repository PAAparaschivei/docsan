"""Image fixtures: synthetic PNG and JPEG files with planted references.

Each fixture renders text for the fictional company "Aurelan Group" with
macOS fonts, sometimes rotated, tiny, in a script face, behind transparency
or hidden in metadata. After the owner confirms the proposed masks, every
pixel of every planted word must be masked, and the words that must stay
must remain readable. Some fixtures depend on how Apple Vision reads a
particular font; their comments say which pass they prove.
"""

from __future__ import annotations

import json
import math
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import imagekit
import leakmatrix as lm
from pngio import Pixels, read_png, write_png

from docsan.pipeline import run_batch
from docsan.review import Lexicon

H = "Helvetica"


def line(text: str, y: float, size: float = 28, font: str = H, x: float = 40, **extra) -> dict:
    return {"text": text, "x": x, "y": y, "size": size, "font": font, **extra}


@dataclass
class ImageFixture:
    name: str
    items: list[dict]
    expect: str = "ready"  # ready, needs_review or blocked
    format: str = "png"
    size: tuple[int, int] = (1000, 160)
    background: list[float] = field(default_factory=lambda: [1, 1, 1])
    orientation: int = 1
    masked: tuple[str, ...] = ()  # words whose every ink pixel must be masked
    visible: tuple[str, ...] = ()  # words that must stay unmasked
    review: tuple[str, ...] = ()  # words the review must ask about
    reason: str = ""  # part of the blocking reason
    owner_masks: list[list[float]] = field(default_factory=list)
    absent: tuple[bytes, ...] = ()  # bytes that must not occur in the output
    edit: Callable[[bytes], bytes] | None = None  # change the rendered file
    profile: dict = field(default_factory=dict)

    @property
    def file(self) -> str:
        return f"{self.name}.{'png' if self.format == 'png' else 'jpg'}"


# --- File edits ------------------------------------------------------------------


def _png_chunk(kind: bytes, body: bytes) -> bytes:
    import zlib

    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def plant_png_metadata(data: bytes) -> bytes:
    at = data.rfind(b"IEND") - 4
    chunks = (
        _png_chunk(b"tEXt", b"Author\x00Aurelan Group")
        + _png_chunk(b"iTXt", b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta>Elena Marchetti</x:xmpmeta>")
        + _png_chunk(b"eXIf", b"MM\x00*Aurelan")
    )
    return data[:at] + chunks + data[at:] + b"Aurelan trailing bytes"


def plant_jpeg_metadata(data: bytes) -> bytes:
    def segment(marker: int, body: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body

    planted = segment(0xE1, b"Exif\x00\x00Artist Aurelan Group") + segment(0xFE, b"Elena Marchetti")
    return data[:2] + planted + data[2:] + b"Aurelan trailing bytes"


def hide_under_transparency(data: bytes) -> bytes:
    """Make the top half fully transparent while its colour still spells a name."""
    px = read_png(data)
    rgba = bytearray()
    for y in range(px.height):
        alpha = 0 if y < px.height // 2 else 255
        for x in range(px.width):
            rgba += bytes(px.at(x, y)[:3]) + bytes([alpha])
    return write_png(Pixels(px.width, px.height, 4, bytes(rgba)))


# --- Fixtures --------------------------------------------------------------------

REFERENCES = [
    line("Aurelan Group integration overview", 24, 40),
    line("Owner: Elena Marchetti", 100),
    line("Mail elena.marchetti@aurelan.lu", 160),
    line("Call +352 26 12 34 56 for support", 220),
    line("PATCH /orders updates an order", 280, font="Menlo"),
    line("Vireo gateway C2 Restricted", 340),
]
REFERENCE_WORDS = (
    "Aurelan", "Group", "Elena", "Marchetti", "elena.marchetti@aurelan.lu",
    "+352", "26", "12", "34", "56", "C2", "Restricted",
)
# Kept words include the neighbours of masked words: a mask must not bite them.
KEPT_WORDS = ("integration", "overview", "Owner:", "Mail", "Call", "for", "support", "PATCH", "/orders", "Vireo", "gateway")


def fixtures() -> list[ImageFixture]:
    return [
        ImageFixture("png-references", REFERENCES, size=(1100, 400), masked=REFERENCE_WORDS, visible=KEPT_WORDS),
        ImageFixture("jpeg-references", REFERENCES, format="jpeg", size=(1100, 400), masked=REFERENCE_WORDS, visible=KEPT_WORDS),
        # The pixels are stored on their side with an EXIF orientation tag.
        ImageFixture(
            "exif-orientation",
            [line("Aurelan platform report", 40, 36)],
            format="jpeg", orientation=6, size=(900, 140),
            masked=("Aurelan",), visible=("platform", "report"),
        ),
        # Vertical labels, read only by the turned passes.
        ImageFixture(
            "vertical-labels",
            [
                line("Integration layer", 40, 30, x=200),
                line("Aurelan Group", 600, 28, x=60, angle=90),
                line("Marchetti signed", 100, 28, x=800, angle=-90),
            ],
            size=(900, 700), masked=("Aurelan", "Group", "Marchetti"), visible=("Integration", "layer"),
        ),
        # A large image: Vision shrinks it before reading, and 10-pixel text is
        # lost unless the image is also read in tiles.
        ImageFixture(
            "small-text-large-image",
            [line("Integration overview", 60, 60), line("Approved by Aurelan for the release", 1600, 10, x=2400)],
            size=(4600, 3200), masked=("Aurelan",), visible=("Integration", "overview"),
        ),
        ImageFixture(
            "small-text",
            [line("Integration overview", 20, 30), line("data owner Marchetti", 90, 10)],
            size=(700, 120), masked=("Marchetti",), visible=("Integration",),
        ),
        # Found by tools/find_ocr_fixtures.py against the current Vision model;
        # rerun it if a macOS update changes how Vision reads these.
        # Only the uncorrected pass reads "AGL" in this script face.
        ImageFixture("script-code", [line("Ticker AGL closed", 17.6, 22, font="Snell Roundhand")], size=(400, 66), masked=("AGL",)),
        # Every pass's best reading is "AGLS"; only an alternate reading has "AGLs".
        ImageFixture("handwritten-plural", [line("AGLs stable", 20.8, 26, font="Bradley Hand")], size=(334, 78), masked=("AGLs",)),
        # Small capitals and drawn lettering lose letter case: a case-sensitive
        # code read as "Agl" is masked as well.
        ImageFixture("other-letter-case", [line("Ticker Agl closed", 30)], size=(500, 90), masked=("Agl",), visible=("Ticker", "closed")),
        ImageFixture(
            "png-metadata",
            [line("Integration overview", 30)],
            edit=plant_png_metadata, absent=(b"aurelan", b"marchetti"), visible=("Integration",),
        ),
        ImageFixture(
            "jpeg-metadata",
            [line("Integration overview", 30)],
            format="jpeg", edit=plant_jpeg_metadata, absent=(b"aurelan", b"marchetti"), visible=("Integration",),
        ),
        ImageFixture(
            "hidden-under-transparency",
            [line("Aurelan Group", 20, 40), line("Integration overview", 100)],
            size=(800, 160), edit=hide_under_transparency, visible=("Integration",),
        ),
        # The owner covers something recognition cannot judge, such as a logo.
        ImageFixture(
            "owner-mask",
            [line("Integration overview", 20), line("logo", 90, 40)],
            size=(800, 160), owner_masks=[[30, 80, 120, 70]], visible=("Integration",),
        ),
        ImageFixture("unknown-name", [line("Approved by Halvorsen", 30)], expect="needs_review", review=("Halvorsen",)),
        ImageFixture("kept-system-name", [line("Vireo platform runs nightly", 30)], visible=("Vireo",)),
        ImageFixture(
            "system-name-redacted",
            [line("Vireo platform runs nightly", 30)],
            masked=("Vireo",), visible=("platform",), profile={"redact_system_names": True},
        ),
        ImageFixture(
            "not-an-image", [line("Integration", 30)],
            edit=lambda data: b"plain text, not a picture", expect="blocked", reason="not a PNG or JPEG image",
        ),
        ImageFixture(
            "truncated", [line("Integration overview", 30)],
            edit=lambda data: data[: len(data) // 2], expect="blocked", reason="cannot read the image",
        ),
    ]


# --- Running a fixture -----------------------------------------------------------------


def build(f: ImageFixture, directory: Path) -> tuple[Path, list[dict], tuple[int, int]]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f.file
    spec = {
        "output": str(path), "format": f.format, "width": f.size[0], "height": f.size[1],
        "background": f.background, "orientation": f.orientation, "items": f.items,
    }
    words = imagekit.render(spec)
    if f.edit:
        path.write_bytes(f.edit(path.read_bytes()))
    return path, words, f.size


def _box_pixels(px: Pixels, box: list[float]):
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(px.width, math.ceil(box[0] + box[2])), min(px.height, math.ceil(box[1] + box[3]))
    for y in range(y0, y1):
        for x in range(x0, x1):
            yield px.at(x, y)


def unmasked(px: Pixels, box: list[float], dark: int) -> int:
    """Pixels inside the box that are not mask black."""
    return sum(1 for p in _box_pixels(px, box) if max(p[:3]) > dark)


def changed(before: Pixels, after: Pixels, box: list[float]) -> float:
    """Share of pixels in the box whose brightness changed noticeably."""
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(after.width, math.ceil(box[0] + box[2])), min(after.height, math.ceil(box[1] + box[3]))
    total = diff = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            a, b = before.at(x, y)[:3], after.at(x, y)[:3]
            total += 1
            diff += abs(sum(a) - sum(b)) > 3 * 64
    return diff / total if total else 0.0


def mostly_masked(px: Pixels, box: list[float], dark: int) -> bool:
    cells = list(_box_pixels(px, box))
    return bool(cells) and sum(1 for p in cells if max(p[:3]) <= dark) / len(cells) > 0.9


def check_image(f: ImageFixture, workdir: Path, lexicon: Lexicon) -> list[str]:
    """Every way this fixture's outcome differs from what the matrix expects."""
    imagekit.helper()
    src, words, upright = build(f, workdir / "in")
    profile = workdir / "profile.json"
    if not profile.exists():
        lm.write_profile(profile, f.profile)
    private = workdir / "private"
    first = run_batch([src], profile, None, private, lexicon=lexicon, release=False,
                      images={str(src): {"masks": f.owner_masks}}).files[0]
    if f.expect == "blocked":
        if first.status != "blocked":
            return [f"status {first.status}, expected blocked: {first.reasons[:3]}"]
        if f.reason and not any(f.reason in r for r in first.reasons):
            return [f"no reason mentioning {f.reason!r}: {first.reasons}"]
        return []
    if f.expect == "needs_review":
        got = {r.word.casefold() for r in first.review}
        missing = {w.casefold() for w in f.review} - got
        return [f"review lacks {sorted(missing)}; got {sorted(got)}"] if missing or first.status != "needs_review" else []
    if first.status != "needs_review" or first.review or "confirm the masks on this image" not in first.reasons:
        return [f"before confirmation: {first.status} {first.reasons[:3]} review {[r.word for r in first.review]}"]
    out_dir = workdir / "out"
    choice = {"masks": f.owner_masks, "confirmed": first.image["digest"]}
    final = run_batch([src], profile, out_dir, private, lexicon=lexicon, images={str(src): choice}).files[0]
    if final.status != "ready":
        return [f"after confirmation: {final.status} {final.reasons[:4]}"]
    released = sorted(out_dir.glob("image-*"))
    if len(released) != 1 or not re.fullmatch(r"image-\d\d\.(png|jpg)", released[0].name):
        return [f"unexpected output files {[p.name for p in released]}"]
    out = released[0]
    problems: list[str] = []
    px = imagekit.pixels(out)
    if (px.width, px.height) != upright:
        problems.append(f"output is {px.width}x{px.height}, expected {upright[0]}x{upright[1]}")
    dark = 0 if f.format == "png" else 60
    for word in f.masked:
        boxes = [w["box"] for w in words if w["text"] == word]
        if not boxes:
            problems.append(f"fixture has no word {word!r}")
        for box in boxes:
            left = unmasked(px, box, dark)
            if left:
                problems.append(f"{word!r}: {left} pixels not masked")
    source = imagekit.pixels(src) if f.orientation == 1 and f.edit is None else None
    for word in f.visible:
        for box in [w["box"] for w in words if w["text"] == word]:
            if source is not None:
                share = changed(source, px, box)
                if share > 0.02:
                    problems.append(f"{word!r} was partly masked ({share:.0%} of its pixels)")
            elif mostly_masked(px, box, dark):
                problems.append(f"{word!r} was masked")
    for box in f.owner_masks:
        if unmasked(px, box, dark):
            problems.append(f"owner mask {box} not applied")
    data = out.read_bytes().lower()
    problems += [f"output contains {s!r}" for s in f.absent if s in data]
    if px.channels == 4:
        hidden = sum(1 for i in range(0, len(px.data), 4) if px.data[i + 3] == 0 and any(px.data[i : i + 3]))
        if hidden:
            problems.append(f"{hidden} transparent pixels keep their colour")
    summary = sorted(out_dir.glob("summary-*.json"))
    if summary and any(s in summary[0].read_text().lower() for s in ("aurelan", "marchetti")):
        problems.append("summary names the company")
    return problems


def describe(f: ImageFixture) -> str:
    return json.dumps({"name": f.name, "expect": f.expect, "format": f.format})
