"""One plan per page: the masks the owner confirms and the text the output keeps.

Each page has two readings: its text layer, exact but possibly hidden or
garbled, and what Apple Vision reads in the drawn page. The plan uses them
like this:

- The text layer is used when the words Vision reads over it agree with it.
  Otherwise, and on a page without one, Vision's reading is the page's text.
- Every word of a usable text layer is matched, seen or not, and every match
  is masked from the boxes of its characters: line by line, or character by
  character for text set at an angle, such as a diagonal watermark.
- Lines join up across line breaks, and a word hyphenated at a line end joins
  again, so a name split across two lines is still found.
- Vision's reading also finds text in pictures and drawn lettering: every
  reading of every pass is matched, as for an image file.
- A second look: the page with its masks burned in, the picture the output
  will hold, is read again enlarged twice, and anything still found is
  masked too. OCR misreads tiny lettering differently in each drawing, so a
  label missed on the sharp drawing is often read on the published one.

The output's hidden text layer holds only what the page shows: text layer
words that leave no ink on the page (a hidden layer, white on white, text
under an opaque box) are left out. Every match is replaced by its label and
every character under a mask is left out, so the text never holds what the
pixels hide.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from .. import steps
from ..image.helper import call
from ..image.regions import field_masks, footer_masks, visual_masks
from ..image.text import (
    Box,
    ImageText,
    Mask,
    _overlap,
    _union,
    fit_to_ink,
    neighbor_guards,
    owner_masks,
    plan_digest,
    plan_masks,
    read_text,
    whole_pixels,
)
from ..match import Labeler, Matcher
from ..sanitize import _replacements
from ..text import fold_word
from .read import DPI, Page, TextLine

INK = 0.02  # a word whose characters all have less ink than this is not on the page
LOOK_ZOOM = 2.0  # the second look reads the masked page enlarged this many times
AGREE = 0.5  # share of the words Vision reads over the text layer that must be in it
_WORD = re.compile(r"[^\W\d_]{3,}")
_LINE_END_HYPHEN = re.compile(r"[^\W\d_][-\u00ad\u2010\u2011]$")


@dataclass
class OutLine:
    """A line of the output's hidden text: sanitized text and its box in pixels."""

    text: str
    box: Box
    angle: int = 0  # 0 reads left to right; 90 reads downwards, 270 upwards


def _angle(box: Box, chars: list) -> int:
    """How a line runs, from where its first and last characters sit."""
    boxes = [b for b in chars if b]
    if box[3] <= 1.5 * box[2] or len(boxes) < 2:
        return 0
    first, last = boxes[0], boxes[-1]
    return 90 if last[1] + last[3] / 2 >= first[1] + first[3] / 2 else 270


@dataclass
class PagePlan:
    masks: list[Mask]
    names: list[str]  # names the matcher wants reviewed
    texts: list[str]  # the page's sanitized text, for the unfamiliar-word review
    lines: list[OutLine]  # the output's hidden text
    info: dict
    width: int
    height: int
    replacements: dict[str, int] = field(default_factory=dict)


@dataclass
class _Glyph:
    char: str
    line: int  # index of its line; -1 for a space DocSan put between lines
    box: Box | None
    seen: bool = True  # its word leaves ink on the page
    ink: float = 1.0  # the share of its box that differs from the background


def seen(line: TextLine) -> list[bool]:
    """Per character: whether its word leaves ink on the page. Spaces count as seen."""
    flags = [c.isspace() for c in line.text]
    for m in re.finditer(r"\S+", line.text):
        on = not steps.enabled("pdf-visible-text") or max(line.ink[m.start() : m.end()]) >= INK
        flags[m.start() : m.end()] = [on] * (m.end() - m.start())
    return flags


def _shown(line: TextLine, flags: list[bool]) -> tuple[str, Box] | None:
    """A line's words that leave ink, and their box; None when it leaves none."""
    words = [m for m in re.finditer(r"\S+", line.text) if flags[m.start()]]
    boxes = [b for m in words for b in line.chars[m.start() : m.end()] if b]
    if not words or not boxes:
        return None
    return " ".join(m.group() for m in words), _union(boxes)


def _covered(box: Box, layer: list[Box]) -> bool:
    return any(_overlap(box, b) > 0.5 for b in layer)


def text_layer_agrees(shown: list[tuple[str, Box]], ocr: ImageText) -> bool:
    """Whether the words Vision reads where the text layer shows are the text layer's own.
    A layer drawn with a font that has no character map reads as nonsense."""
    if not shown:
        return False
    if not steps.enabled("pdf-check-text-layer"):
        return True
    known = {fold_word(w) for text, _ in shown for w in _WORD.findall(text)}
    boxes = [box for _, box in shown]
    words = [fold_word(w) for line in ocr.best() if _covered(line.box, boxes) for w in _WORD.findall(line.readings[0].text)]
    if len(words) < 3:
        return True
    return sum(w in known for w in words) / len(words) >= AGREE


def _segments(lines: list[TextLine], flags: list[list[bool]]) -> list[list[_Glyph]]:
    """The text layer as text to match: one piece for the page with lines joined
    by a space, or with nothing after a word hyphenated at a line end."""
    pieces: list[list[_Glyph]] = []
    current: list[_Glyph] = []
    for n, line in enumerate(lines):
        if current:
            if not steps.enabled("pdf-join-lines"):
                pieces.append(current)
                current = []
            elif not (_LINE_END_HYPHEN.search(lines[n - 1].text.rstrip()) and line.text[:1].isalpha()):
                current.append(_Glyph(" ", -1, None))
        current += [_Glyph(c, n, b, f, k) for c, b, f, k in zip(line.text, line.chars, flags[n], line.ink)]
    if current:
        pieces.append(current)
    return pieces


def _diagonal(line: TextLine) -> bool:
    """Whether a line runs at an angle, so that the box around it covers far more than its characters."""
    boxes = [b for b in line.chars if b]
    if len(boxes) < 3:
        return False
    height = sorted(b[3] for b in boxes)[len(boxes) // 2]
    width = sorted(b[2] for b in boxes)[len(boxes) // 2]
    return line.box[3] > 3 * height and line.box[2] > 3 * width


def _under_mask(box: Box | None, masks: list[Mask]) -> bool:
    return box is not None and any(_overlap(box, m.box) > 0.5 for m in masks)


def _emit(glyphs: list, reps: list[tuple[int, int, str, str]]) -> tuple[list[str], list[int]]:
    """What each character becomes: itself, a replacement (on the first
    character of its match that sits on a line) or nothing; and which
    replacement it belongs to, or -1."""
    out = [g.char for g in glyphs]
    owner = [-1] * len(glyphs)
    for k, (start, end, replacement, _) in enumerate(reps):
        first = next((i for i in range(start, end) if getattr(glyphs[i], "line", 0) >= 0), start)
        for i in range(start, end):
            out[i] = ""
            owner[i] = k
        out[first] = replacement
    return out, owner


def _clean(text: str) -> str:
    return " ".join(text.split())


def second_look(page: Page, masks: list[Mask], matcher: Matcher, guards: list[Box] | tuple[Box, ...] = ()) -> list[Mask]:
    """Masks for what OCR still reads on the page with ``masks`` burned in,
    read enlarged: the picture the output will hold."""
    looked = page.image.with_name(f"{page.image.stem}.look.png")
    try:
        call({"command": "mask", "input": str(page.image), "output": str(looked), "format": "png",
              "boxes": [list(m.box) for m in masks]})
        again = read_text(looked, zoom=LOOK_ZOOM)
    finally:
        looked.unlink(missing_ok=True)
    more, _ = plan_masks(again, matcher)
    more = [m for m in more if not any(_overlap(m.box, kept.box) > 0.5 for kept in masks)]
    return fit_to_ink(page.image, more, page.width, page.height,
                      guards=neighbor_guards(again, matcher), trusted_guards=guards) if more else []


@dataclass
class _OcrChar:
    char: str
    box: Box | None


def plan_page(
    page: Page,
    source: str,
    matcher: Matcher,
    labeler: Labeler,
    choice: dict,
) -> PagePlan:
    """Plan one page. ``source`` identifies the file (its SHA-256); the page's
    digest binds the owner's confirmation to this page of this file with
    exactly these masks."""
    w, h = page.width, page.height
    style = "label"  # the pixels hide the complete name; the hidden layer must do so too
    if page.ocr_image and page.ocr_width and steps.enabled("pdf-sharp-ocr"):
        ocr = read_text(page.ocr_image, scale=page.width / page.ocr_width)
    else:
        ocr = read_text(page.image)
    lines = page.lines if steps.enabled("pdf-text-layer") else []
    flags = [seen(line) for line in lines]
    hidden = sum(1 for line, f in zip(lines, flags) for m in re.finditer(r"\S+", line.text) if not f[m.start()])
    shown = {n: s for n, s in ((n, _shown(line, f)) for n, (line, f) in enumerate(zip(lines, flags))) if s}
    usable = text_layer_agrees(list(shown.values()), ocr)
    if not usable:
        lines, flags, shown, hidden = [], [], {}, 0
    best = ocr.best()
    boxes = [box for _, box in shown.values()]
    extra = [line for line in best if not _covered(line.box, boxes)]  # text only Vision sees: pictures, drawings

    names: list[str] = []
    counts: dict[str, int] = {}
    found: list[Mask] = []
    emitted: list[tuple[list, list[str], list[int]]] = []
    layer_guards: list[Box] = []
    diagonal = [_diagonal(line) for line in lines]
    for piece in _segments(lines, flags):
        text = "".join(g.char for g in piece)
        result = matcher.find(text)
        names += result.review
        reps = _replacements(text, result.hits, labeler, style)
        # An agreeing PDF text layer supplies exact bounds for public words
        # that an OCR reading may have joined onto a sensitive token.
        for word in re.finditer(r"\S+", text):
            if any(word.start() < hit.end and word.end() > hit.start for hit in result.hits):
                continue
            by_line: dict[int, list[Box]] = {}
            for glyph in piece[word.start():word.end()]:
                if glyph.line >= 0 and glyph.seen and glyph.box:
                    by_line.setdefault(glyph.line, []).append(glyph.box)
            layer_guards.extend(_union(boxes) for boxes in by_line.values())
        for start, end, _, label in reps:
            counts[label] = counts.get(label, 0) + 1
            if not any(g.ink > 0 for g in piece[start:end] if g.line >= 0):
                continue  # nothing of it shows: a mask would only cover what is drawn there instead
            by_line: dict[int, list[Box]] = {}
            for g in piece[start:end]:
                if g.line >= 0 and g.box:
                    by_line.setdefault(g.line, []).append(g.box)
            for n, line_boxes in by_line.items():
                for box in line_boxes if diagonal[n] else [_union(line_boxes)]:
                    size = min(box[2], box[3])
                    vertical = box[3] > box[2] and len(line_boxes) > 1 and not diagonal[n]
                    found.append(Mask(whole_pixels(box, w, h), label, "text", reach=0.6 * size, vertical=vertical))
        emitted.append((piece, *_emit(piece, reps)))

    ocr_masks, _ = plan_masks(ocr, matcher)
    masks = fit_to_ink(page.image, found + ocr_masks, w, h,
                       guards=neighbor_guards(ocr,matcher), trusted_guards=layer_guards)
    if steps.enabled("pdf-sensitive-fields"):
        masks += field_masks(ocr, lines=best)
    if steps.enabled("pdf-visual-regions"):
        masks += visual_masks(page.ocr_image or page.image, ocr, masks, lines=best,
                              scale=w / page.ocr_width if page.ocr_image and page.ocr_width else 1.0)
    if steps.enabled("pdf-sensitive-fields"):
        masks += footer_masks(ocr, masks, lines=best)
    masks += owner_masks(choice.get("masks", []), w, h)
    masks += second_look(page, masks, matcher, layer_guards)

    # The output's text: what each line shows, as emitted, without what a mask covers.
    out_lines: list[OutLine] = []
    info_lines: list[dict] = []
    visible: list[str] = []
    per_line: dict[int, list[str]] = {}
    for piece, out, owner in emitted:
        for g, e, k in zip(piece, out, owner):
            if g.line >= 0:
                hide = not g.seen or (k < 0 and not g.char.isspace() and _under_mask(g.box or lines[g.line].box, masks))
                per_line.setdefault(g.line, []).append("" if hide else e)
    for n, (original, box) in shown.items():
        text = _clean("".join(per_line.get(n, [])))
        info_lines.append({"box": list(box), "text": original})
        visible.append(text)
        if text:
            out_lines.append(OutLine(text, box, _angle(box, [b for b, f in zip(lines[n].chars, flags[n]) if f])))
    for line in extra:
        reading = line.readings[0]
        result = matcher.find(reading.text, loose_case=True)
        names += result.review
        reps = _replacements(reading.text, result.hits, labeler, style)
        for *_, label in reps:
            counts[label] = counts.get(label, 0) + 1
        chars = [_OcrChar(c, (reading.chars[i] or line.box) if i < len(reading.chars) else line.box) for i, c in enumerate(reading.text)]
        out, owner = _emit(chars, reps)
        text = _clean("".join("" if k < 0 and not c.char.isspace() and _under_mask(c.box, masks) else e for c, e, k in zip(chars, out, owner)))
        info_lines.append({"box": list(line.box), "text": reading.text})
        visible.append(text)
        if text:
            out_lines.append(OutLine(text, line.box, _angle(line.box, reading.chars)))

    digest = plan_digest(hashlib.sha256(f"{source}#page-{page.number}@{DPI}".encode()).hexdigest(), masks)
    info = {
        "width": w,
        "height": h,
        "format": "pdf",
        "frames": 1,
        "masks": [{"box": list(m.box), "label": m.label, "source": m.source} for m in masks],
        "lines": info_lines,
        "visible": visible,
        "digest": digest,
        "confirmed": choice.get("confirmed") == digest,
        "found": [],
        "logo": False,
        "removed": None,
        "text_layer": usable,
        "hidden_words": hidden,
    }
    return PagePlan(masks, names, [line.text for line in out_lines], out_lines, info, w, h, counts)
