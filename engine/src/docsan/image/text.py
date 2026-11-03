"""Text that Apple Vision finds in an image, and the masks it calls for.

Vision reads each image in up to four passes: upright with and without
language correction (correction can turn a code into a word), and turned a
quarter each way for vertical labels. Vision shrinks a large image before
reading it, so a large image is also read in overlapping tiles of at most
1536 pixels, with the same four passes each: labels a few pixels high in a
diagram or a screenshot are then read at their own size. Each line keeps up
to three readings.
Every reading of every pass goes through the same matcher as document text,
so a name is masked when any reading contains it. The one exception: when a
pass's best reading shows a word is an allowed use (``PATCH /orders``), a
weaker reading that lost the context (``PATCH orders``) does not mask it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .. import steps
from ..match import Matcher
from .helper import call

Box = tuple[float, float, float, float]  # x, y, width, height; pixels from the top left

TILE = 1536  # the largest image Vision reads whole; larger ones are also read in tiles this size
OVERLAP = 384  # neighbouring tiles overlap by this much, so a word up to this long is whole in one


@dataclass
class Reading:
    text: str
    confidence: float
    chars: list[Box | None]  # one per character: the box of the word it belongs to


@dataclass
class Line:
    box: Box
    readings: list[Reading]
    rotation: int
    corrected: bool
    exact: bool = False  # text a drawing contains, not read by OCR
    tile: bool = False  # read in a tile rather than in the whole image
    cut: bool = False  # it touches the edge of its tile inside the image, so it may be part of a longer line
    context_only: bool = False  # an alternate for matching, never a preview-vote


@dataclass
class ImageText:
    width: int
    height: int
    frames: int
    orientation: int
    alpha: bool
    lines: list[Line]

    def best(self) -> list[Line]:
        """One line per place, for review and preview: the corrected upright
        lines, plus lines from the turned passes where nothing upright was read.
        A line read whole in a tile comes before the whole image's reading of
        the same place, which was read smaller; a line a tile edge cuts does not."""
        exact = [line for line in self.lines if line.exact]
        chosen = list(exact)
        # Candidate comparisons are local. Dense screenshots can have thousands
        # of tiled readings; comparing every reading with every other reading
        # made unchanged plans spend minutes in Python after OCR had finished.
        def cells(box: Box):
            x, y, w, h = box
            for cy in range(math.floor(y / 256), max(math.floor(y / 256), math.floor((y+h) / 256)) + 1):
                for cx in range(math.floor(x / 256), max(math.floor(x / 256), math.floor((x+w) / 256)) + 1):
                    yield cx, cy

        placed: dict[tuple, dict[int, Line]] = {}
        same: dict[tuple, dict[int, Line]] = {}
        def put(index, line, prefix=()):
            for cell in cells(line.box):
                index.setdefault((*prefix, *cell), {})[id(line)] = line

        def nearby(index, line, prefix=()):
            found = {}
            for cell in cells(line.box):
                found.update(index.get((*prefix, *cell), {}))
            return found.values()

        for line in chosen:
            put(placed, line)
        for line in self.lines:
            if not line.context_only:
                put(same, line, (line.readings[0].text,))

        def free(line: Line) -> bool:
            return not any(_overlap(line.box, other.box) > 0.3 for other in nearby(placed, line))

        scores: dict[int, int] = {}
        def agreement(line: Line) -> int:
            key = id(line)
            if key not in scores:
                scores[key] = sum(1 for other in nearby(same, line, (line.readings[0].text,))
                                  if _overlap(line.box, other.box) > 0.5)
            return scores[key]

        tiles = sorted((line for line in self.lines if line.corrected and line.rotation == 0 and line.tile and not line.cut),
                       key=lambda line: (-agreement(line), -line.readings[0].confidence))
        for line in tiles:
            if free(line):
                chosen.append(line)
                put(placed, line)
        upright = [line for line in self.lines if line.corrected and line.rotation == 0 and not line.exact
                   and not line.tile and free(line)]
        chosen += upright
        for line in upright:
            put(placed, line)
        turned = [line for line in self.lines if line.corrected and line.rotation != 0 and not line.cut]
        for line in sorted(turned, key=lambda line: (not line.tile, -agreement(line))):
            if free(line):
                chosen.append(line)
                put(placed, line)
        return chosen


@dataclass(frozen=True)
class Mask:
    box: Box
    label: str
    source: str  # "text" (found by recognition) or "owner" (drawn by the owner)
    reach: float = field(default=0.0, compare=False)  # how far past the box its ink may run
    vertical: bool = field(default=False, compare=False)  # the line runs top to bottom
    guards: tuple[Box, ...] = field(default=(), compare=False)  # neighboring unmatched words


Crop = tuple[float, float, float, float]  # left, top, right, bottom fractions


def drawing_lines(texts: list[dict]) -> list[Line]:
    """Text a vector drawing contains, with exact character boxes, as lines."""
    lines: list[Line] = []
    for t in texts:
        chars = [tuple(c) if c else None for c in t.get("chars", [])]
        lines.append(Line(tuple(t["box"]), [Reading(t["text"], 1.0, chars)], 0, True, exact=True))
    return lines



def visible_crop_lines(full: ImageText, crop: Crop, width: int, height: int) -> list[Line]:
    """Extra full-image OCR, restricted to lines wholly inside the visible crop.

    Cropping a narrow margin can change OCR recognition elsewhere. These
    alternatives help matching but never displace the cropped preview reading.
    """
    left,top = (math.floor(full.width*crop[0]+.5), math.floor(full.height*crop[1]+.5))
    expected = (full.width-left-math.floor(full.width*crop[2]+.5),
                full.height-top-math.floor(full.height*crop[3]+.5))
    if expected != (width,height):
        raise ValueError("full-image OCR geometry does not match the visible crop")
    out = []
    def moved(box):
        return (box[0]-left,box[1]-top,box[2],box[3])
    for line in full.lines:
        x,y,w,h = moved(line.box)
        if x < 0 or y < 0 or x+w > width or y+h > height:
            continue
        readings = [Reading(r.text,r.confidence,[moved(b) if b else None for b in r.chars]) for r in line.readings]
        out.append(replace(line, box=(x,y,w,h), readings=readings, corrected=False, exact=False, context_only=True))
    return out


def _cut(box: Box, tile: Box, width: int, height: int) -> bool:
    """Whether a line reaches an edge of its tile that lies inside the image.
    Vision drops a letter the edge cuts in half, so a line that stops within
    about a letter of the edge may go on in the next tile."""
    x, y, w, h = box
    tx, ty, tw, th = tile
    near = max(4.0, 0.8 * min(w, h))
    return ((tx > 0 and x <= tx + near) or (ty > 0 and y <= ty + near)
            or (tx + tw < width and x + w >= tx + tw - near) or (ty + th < height and y + h >= ty + th - near))


def read_text(path: Path, crop: Crop | None = None, scale: float = 1.0, zoom: float = 1.0) -> ImageText:
    """Read an image's text. ``scale`` maps boxes from the image read to the
    image they stand for, such as a PDF page drawn at twice its resolution
    for reading (0.5). ``zoom`` reads the image enlarged that many times."""
    request: dict = {"command": "ocr", "input": str(path)}
    if crop and any(crop):
        request["crop"] = list(crop)
    if zoom > 1:
        request["zoom"] = zoom
    request["rotations"] = [90, 270] if steps.enabled("ocr-rotations") else []
    if steps.enabled("ocr-tiles"):
        request["tile"], request["overlap"] = TILE, OVERLAP
    if not steps.enabled("ocr-orientation"):
        request["orientation"] = False
    answer = call(request)
    width, height = int(answer["width"]), int(answer["height"])

    def sized(b) -> Box:
        return tuple(round(v * scale, 1) for v in b) if scale != 1.0 else tuple(b)

    lines: list[Line] = []
    for p in answer["passes"]:
        if not p["correction"] and not steps.enabled("ocr-uncorrected"):
            continue
        tile = tuple(p["tile"]) if p.get("tile") else None
        for item in p["lines"]:
            readings = [
                Reading(r["text"], float(r["confidence"]), [sized(b) if b else None for b in r["chars"]])
                for r in item["candidates"]
            ]
            if not steps.enabled("ocr-alternates"):
                readings = readings[:1]
            if readings:
                cut = tile is not None and _cut(tuple(item["box"]), tile, width, height)
                lines.append(Line(sized(item["box"]), readings, int(p["rotation"]), bool(p["correction"]),
                                  tile=tile is not None, cut=cut))
    return ImageText(
        round(width * scale), round(height * scale), int(answer["frames"]),
        int(answer["orientation"]), bool(answer["alpha"]), lines,
    )


def _union(boxes: list[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def whole_pixels(box: Box, width: int, height: int, pad: tuple[float, float] = (0.0, 0.0)) -> Box:
    """Grow a box by ``pad`` (across, down), round it outward to whole pixels and clip it."""
    x0 = max(0, math.floor(box[0] - pad[0]))
    y0 = max(0, math.floor(box[1] - pad[1]))
    x1 = min(width, math.ceil(box[0] + box[2] + pad[0]))
    y1 = min(height, math.ceil(box[1] + box[3] + pad[1]))
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def _padding(box: Box) -> tuple[float, float]:
    """A margin for when masks are not fitted to the ink: Vision's word boxes
    can miss a few pixels of anti-aliased edges, descenders and overhangs."""
    if not steps.enabled("mask-padding"):
        return (0.0, 0.0)
    pad = max(3.0, 0.2 * min(box[2], box[3]))  # the short side is the text height
    return (pad, pad)


def _overlap(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    smaller = min(a[2] * a[3], b[2] * b[3])
    return ix * iy / smaller if smaller else 0.0


def _merge(masks: list[Mask]) -> list[Mask]:
    """Readings of the same word from different passes become one mask covering all of them."""
    merged = list(masks)
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                a, b = merged[i], merged[j]
                if a.source == b.source and _overlap(a.box, b.box) > 0.5:
                    merged[i] = Mask(_union([a.box, b.box]), a.label, a.source, max(a.reach, b.reach), a.vertical, tuple(set(a.guards+b.guards)))
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


def _span_box(line: Line, reading: Reading, start: int, end: int) -> Box:
    boxes = [b for b in reading.chars[start:end] if b]
    return _union(boxes) if boxes else line.box  # no word boxes: the whole line


def system_variable_headers(text: ImageText) -> list[Box]:
    return [l.box for l in text.best() if re.fullmatch(r"system\s+variables", l.readings[0].text.strip(), re.IGNORECASE)]


def in_system_column(box: Box, headers: list[Box], height: int) -> bool:
    x,y,_,_ = box
    return any(hy+hh <= y <= hy+height*.6 and hx-2*hh <= x <= hx+max(hw,12*hh) for hx,hy,hw,hh in headers)


def plan_masks(text: ImageText, matcher: Matcher) -> tuple[list[Mask], list[str]]:
    """Masks for every match in every reading, and names the matcher wants reviewed."""
    system_headers = system_variable_headers(text)
    allowed: list[Box] = []
    for line in text.lines:
        best = line.readings[0]
        for start, end, _ in matcher.find(best.text, loose_case=True).exempt:
            allowed.append(_span_box(line, best, start, end))
    money: list[Box] = []
    headers = [line.box for line in text.lines if line.rotation == 0 and line.corrected
               and re.search(r"\b(?:EUR|USD|GBP|prix|price|montant)\b", line.readings[0].text, re.IGNORECASE)]
    for line in text.lines:
        best = line.readings[0]
        for decimal in re.finditer(r"(?<![\w+])\d+(?:[.,]\d{2,4})(?!\w)", best.text):
            box = _span_box(line, best, decimal.start(), decimal.end())
            x, y, w, h = box
            labeled = re.search(r"[€$£]|\b(?:EUR|USD|GBP|total|TVA|abonnement|options|amount|prix|price)\b", best.text, re.IGNORECASE)
            column = any(abs((hx+hw/2)-(x+w/2)) < max(hw, w, 4*h)
                         and 0 <= y-hy < text.height*.6 for hx,hy,hw,_ in headers)
            if labeled or column:
                money.append(box)
    masks: list[Mask] = []
    review: list[str] = []
    for line in text.lines:
        for rank, reading in enumerate(line.readings):
            found = matcher.find(reading.text, loose_case=True, system_variables=in_system_column(line.box, system_headers, text.height))
            if rank == 0:
                review += found.review
            for hit in found.hits:
                box = _span_box(line, reading, hit.start, hit.end)
                if rank > 0 and any(_overlap(box, a) > 0.5 for a in allowed):
                    continue
                # A weaker reading may drop a decimal separator and turn an
                # amount into an eight-digit account number. The best reading
                # and its financial column must agree before excluding it.
                if rank > 0 and hit.label in ("ID", "PHONE") and any(_overlap(box, b) > 0.5 for b in money):
                    continue
                size = min(box[2], box[3])  # the text height, also for vertical labels
                # Script swashes run up to about half the text height past a word.
                guards=[]
                for word in re.finditer(r"\S+",reading.text):
                    if any(word.start()<h.end and word.end()>h.start for h in found.hits):
                        continue
                    other=_span_box(line,reading,word.start(),word.end())
                    if _overlap(other,box)<.2:
                        guards.append(other)
                masks.append(Mask(whole_pixels(box, text.width, text.height), hit.label, "text",
                                  reach=0.6 * size, vertical=line.box[3] > line.box[2],guards=tuple(guards)))
    return _merge(masks), review


def neighbor_guards(text: ImageText, matcher: Matcher) -> list[Box]:
    guards=[]
    for line in text.best():
        reading=line.readings[0]
        hits=matcher.find(reading.text,loose_case=True).hits
        for word in re.finditer(r"\S+",reading.text):
            if not any(word.start()<h.end and word.end()>h.start for h in hits):
                guards.append(_span_box(line,reading,word.start(),word.end()))
    return guards


def fit_to_ink(path: Path, masks: list[Mask], width: int, height: int, crop: Crop | None = None, guards: list[Box] | tuple[Box, ...] = ()) -> list[Mask]:
    """Fit each recognized mask to the ink of its word (see the helper's
    ``fit``): tight around the word, past the box where a swash runs on,
    clear of neighbouring words, plus a two-pixel margin for anti-aliasing."""
    found = [m for m in masks if m.source == "text"]
    if not found:
        return masks
    if steps.enabled("mask-fit-ink"):
        request: dict = {
            "command": "fit",
            "input": str(path),
            "margin": 2 if steps.enabled("mask-padding") else 0,
            "boxes": [[*m.box, m.reach, 1 if m.vertical else 0] for m in found],
            "guards": [[list(b) for b in (*m.guards,*guards) if _edge_guard(b,m)
                        and abs((b[1]+b[3]/2)-(m.box[1]+m.box[3]/2)) < max(b[3],m.box[3])]
                       for m in found],
        }
        if not steps.enabled("ocr-orientation"):
            request["orientation"] = False
        if crop and any(crop):
            request["crop"] = list(crop)
        boxes = [tuple(b) for b in call(request)["boxes"]]
        fitted = [whole_pixels(b, width, height) for b in boxes]
    else:
        fitted = [whole_pixels(m.box, width, height, _padding(m.box)) for m in found]
    fitted_masks = [Mask(b, m.label, m.source, m.reach, m.vertical, m.guards) for m, b in zip(found, fitted)]
    return _merge(fitted_masks + [m for m in masks if m.source != "text"])


def _edge_guard(box: Box, mask: Mask) -> bool:
    """An oversized OCR box may contain a whole unmatched neighboring word.

    Accept guards near its ends, while excluding a guard over its center.
    The native fitter still requires an actual ink gap before trimming.
    """
    if _overlap(box, mask.box) < .2:
        return True
    axis, length = (1, 3) if mask.vertical else (0, 2)
    center = mask.box[axis] + mask.box[length] / 2
    other = box[axis] + box[length] / 2
    return abs(other - center) > mask.box[length] / 4


def unmasked(line: Line, masks: list[Mask]) -> str:
    """A line's best reading with every masked word blanked, for review:
    a word already under a mask needs no decision."""
    reading = line.readings[0]
    chars = list(reading.text)
    for i, box in enumerate(reading.chars[: len(chars)]):
        if box and any(_overlap(box, m.box) > 0.5 for m in masks):
            chars[i] = " "
    return "".join(chars)


def owner_masks(boxes: list, width: int, height: int) -> list[Mask]:
    out: list[Mask] = []
    for b in boxes:
        if not (isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)):
            raise ValueError("an owner mask must be [x, y, width, height]")
        box = whole_pixels(tuple(float(v) for v in b), width, height)
        if box[2] > 0 and box[3] > 0:
            out.append(Mask(box, "MASK", "owner"))
    return out


def plan_digest(source_sha256: str, masks: list[Mask]) -> str:
    """What the owner confirms: this exact image with exactly these masks."""
    payload = {"image": source_sha256, "masks": sorted([*m.box, m.source] for m in masks)}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()[:32]
