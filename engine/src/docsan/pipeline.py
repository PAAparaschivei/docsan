"""Run a batch: read, sanitize, review, stage, verify and release.

Each file ends in one of three states: ``ready`` (released), ``needs_review``
(unfamiliar words must be classified, or an image's masks confirmed, first)
or ``blocked``. Any failure, including cancellation, leaves the shareable
folder without partial files. The replacement mapping exists only in memory
and is gone when the batch ends.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from . import steps
from . import verify as verifier
from .docx.model import Document, Figure, figures, preview_lines
from .docx.pages import write_pages
from .docx.read import read_docx
from .docx.write import write_docx
from .gate import ReleaseRefused, release
from .image.encode import clean_jpeg, incomplete, sniff, write_png
from .image.helper import HelperError, call, disk_cache
from .image.logo import Print, fingerprint, same
from .image.regions import (
    continuation_masks,
    field_masks,
    footer_masks,
    reviewable_lines,
    visual_masks,
)
from .image.text import (
    drawing_lines,
    fit_to_ink,
    owner_masks,
    plan_digest,
    plan_masks,
    read_text,
    unmasked,
    visible_crop_lines,
)
from .image.vector import VectorError
from .image.vector import render as render_drawing
from .match import Labeler, Matcher
from .pdf.plan import PagePlan, plan_page
from .pdf.read import read_pdf
from .pdf.write import OutPage, expected_text, png_rows, text_layer, write_pdf
from .profile import add_logos, load_profile
from .review import Lexicon, ReviewItem, glossary_words, unfamiliar_words
from .sanitize import plain_text, review_text, sanitize
from .text import fold_word

WORD_SUFFIXES = frozenset({".docx", ".docm", ".dotx", ".dotm"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
PDF_SUFFIX = ".pdf"
PAGE_QUALITY = 0.85  # JPEG quality of a flattened PDF page
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass
class Hooks:
    """Failure-injection points for tests."""

    after_stage: Callable[[Path], None] | None = None
    before_release: Callable[[Path], None] | None = None


@dataclass
class FileResult:
    source: str
    status: str
    reasons: list[str] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    output: str | None = None
    sha256: str | None = None
    replacements: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    preview_original: list[dict] = field(default_factory=list)
    preview_sanitized: list[dict] = field(default_factory=list)
    kind: str = "word"  # word, image or pdf
    image: dict | None = None  # size, masks, recognized lines and the digest to confirm
    images: list[dict] = field(default_factory=list)  # the same for each picture in a Word file, or each page of a PDF
    pages: str | None = None  # the sanitized file's pages as HTML, masks as proposed, for the app; never released
    pages_original: str | None = None  # the original's pages, drawn the same way
    pdf_folder: str | None = None  # where a PDF's pages are drawn, inside the private folder


@dataclass
class BatchResult:
    batch_id: str
    files: list[FileResult]
    report_path: Path
    summary_path: Path | None


def _inputs(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir() and not p.is_symlink():
            out.extend(sorted(f for f in p.rglob("*") if not any(part.startswith(".") for part in f.relative_to(p).parts)))
        else:
            out.append(p)
    return [p for p in out if not p.is_dir()]


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _next_name(out_dir: Path, stem: str = "document", ext: str = ".docx") -> str:
    n = 1
    while (out_dir / f"{stem}-{n:02d}{ext}").exists():
        n += 1
    return f"{stem}-{n:02d}{ext}"


def _review(names: list[str], texts: list[str], lexicon: Lexicon) -> list[ReviewItem]:
    review: list[ReviewItem] = []
    seen: set[str] = set()
    for name in names:
        if fold_word(name) not in seen and fold_word(name) not in lexicon.words:
            seen.add(fold_word(name))
            review.append(ReviewItem(name, "name that may be a domain or a file"))
    return review + unfamiliar_words(texts, lexicon, skip=names)


def _create_private(path: Path) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)


def _paragraph_texts(root: ET.Element) -> list[str]:
    """Each paragraph's own text in document order; a text box's paragraphs
    follow the paragraph that holds the text box."""
    out: list[list[str]] = []

    def walk(el: ET.Element, current: list[str] | None) -> None:
        for child in el:
            if child.tag in (_W + "pPr", _W + "rPr"):
                continue  # tab stops are not tabs
            if child.tag == _W + "p":
                entry: list[str] = []
                out.append(entry)
                walk(child, entry)
                continue
            if current is not None:
                if child.tag == _W + "t":
                    current.append(child.text or "")
                elif child.tag in (_W + "tab", _W + "ptab"):
                    current.append("\t")
                elif child.tag == _W + "br":
                    current.append("\n")
            walk(child, current)

    walk(root, None)
    return ["".join(e) for e in out]


def _output_paragraphs(path: Path) -> list[str]:
    """The document's paragraphs, then each header's and footer's, in part order."""
    # Parses the file this process just wrote, which cannot contain a DTD.
    with zipfile.ZipFile(path) as zf:
        names = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"word/(header|footer)\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n).group()),
        )
        roots = [ET.fromstring(zf.read(n)) for n in ["word/document.xml", *names]]
    return [text for root in roots for text in _paragraph_texts(root)]


def _preserved(expected: list[str], staged: Path) -> bool:
    def norm(items: list[str]) -> list[str]:
        return [" ".join(t.split()) for t in items if t.strip()]

    return norm(expected) == norm(_output_paragraphs(staged))


def _pages(doc: Document, folder: Path, pictures: list[tuple[Figure, bytes]]) -> str:
    """The document's pages described for the app, with each picture as a file beside them."""
    shutil.rmtree(folder, ignore_errors=True)
    folder = _private_dir(folder)
    names: dict[int, str] = {}
    for n, (fig, data) in enumerate(pictures, 1):
        if data and not data.startswith((b"<", b"\x01\x00\x00\x00")):  # vector data that was never drawn has no picture
            name = f"image-{n}{_source_ext(data)}"
            (folder / name).write_bytes(data)
            names[id(fig)] = name
    return str(write_pages(doc, folder, names))


def _source_ext(data: bytes) -> str:
    for sig, ext in ((b"\xff\xd8", ".jpg"), (b"\x89PNG", ".png"), (b"GIF8", ".gif"), (b"BM", ".bmp")):
        if data.startswith(sig):
            return ext
    return ".tif"


def _process(
    src: Path,
    staging: Path,
    out_dir: Path | None,
    profile_path: Path,
    matcher: Matcher,
    labeler: Labeler,
    lexicon: Lexicon,
    remove_images: bool,
    label_unreadable: bool,
    hooks: Hooks,
    release_files: bool,
    choices: dict[str, dict],
    figure_dir: Path,
    page_dir: Path,
    logos: list[Print],
) -> FileResult:
    result = FileResult(source=str(src), status="blocked")
    if src.is_symlink() or not src.is_file():
        result.reasons.append("not a regular file")
        return result
    if src.suffix.lower() not in WORD_SUFFIXES:
        result.reasons.append(f"{src.suffix or 'this'} files are not supported yet")
        return result

    read = read_docx(src, remove_images=remove_images, label_unreadable=label_unreadable)
    result.dropped = read.dropped
    if read.document is None:
        result.reasons.extend(read.blocking)
        return result

    # Vector drawings become pictures first, so both views of the pages show them.
    drawn: dict[int, list[dict]] = {}
    for f in list(read.document.figures()):
        if f.kind == "vector":
            # A vector drawing becomes a picture first; its own text is kept.
            try:
                drawing = render_drawing(f.data, staging)
            except VectorError as exc:
                if label_unreadable:
                    read.document.replace_figure(f, "[IMAGE-REMOVED]")
                    result.dropped["drawing DocSan cannot draw"] = result.dropped.get("drawing DocSan cannot draw", 0) + 1
                    continue
                result.reasons.append(f"image {f.index}: {exc}")
                return result
            f.data, f.kind = drawing.png, "png"
            drawn[f.index] = drawing.texts
    pages = _private_dir(page_dir / hashlib.sha256(str(src).encode()).hexdigest()[:16])
    result.pages_original = _pages(read.document, pages / "original", [(f, f.data) for f in read.document.figures()])
    result.preview_original = preview_lines(read.document)
    source_texts = plain_text(read.document)
    matcher = matcher.with_context(source_texts)
    report = sanitize(read.document, matcher, labeler)
    result.replacements = report.by_label
    result.preview_sanitized = preview_lines(read.document)
    figs = list(read.document.figures())
    texts = plain_text(read.document)  # labels may have replaced drawings
    plans: list[_ImagePlan] = []
    names = list(report.review)
    image_texts: list[str] = []
    if figs:
        folder = _private_dir(figure_dir / hashlib.sha256(str(src).encode()).hexdigest()[:16])
        margins = {id(f) for part in read.document.parts for f in figures(part.blocks)}
        placed: list[tuple[Figure, _ImagePlan]] = []
        for position, fig in enumerate(figs, 1):
            _progress(staging, "Reading and masking Word pictures", position, len(figs))
            key = f"{src}#image-{fig.index}"
            path = folder / f"image-{fig.index}{_source_ext(fig.data)}"
            path.write_bytes(fig.data)
            try:
                plan = _plan_image(path, fig.data, fig.kind, matcher, choices.get(key, {}), fig.crop, drawn.get(fig.index, ()))
                plan.print = fingerprint(path, fig.crop)
            except (HelperError, ValueError) as exc:
                if label_unreadable:
                    read.document.replace_figure(fig, "[IMAGE-REMOVED]")
                    result.dropped["picture DocSan cannot read"] = result.dropped.get("picture DocSan cannot read", 0) + 1
                    continue
                result.reasons.append(f"image {fig.index}: cannot read it: {exc}")
                return result
            plan.info.update({"key": key, "index": fig.index, "source": str(path), "margin": id(fig) in margins})
            if plan.print:
                plan.info.update({"print": plan.print.hex, "aspect": plan.print.aspect})
            placed.append((fig, plan))
        _choose_removals([p for _, p in placed], choices, logos)
        for fig, plan in placed:
            if plan.info["removed"]:
                # A logo goes whole: nothing of it reaches the output, so there is nothing to confirm.
                read.document.replace_figure(fig, "")
                result.dropped["picture removed whole"] = result.dropped.get("picture removed whole", 0) + 1
                continue
            plans.append(plan)
            names += plan.names
            image_texts += plan.reviewable
            for m in plan.masks:
                if m.source == "text":
                    result.replacements[m.label] = result.replacements.get(m.label, 0) + 1
        result.images = [p.info for _, p in placed]
        figs = list(read.document.figures())  # without pictures removed or replaced by a label
        texts = plain_text(read.document)

    render_error: Exception | None = None
    try:
        for fig, plan in zip(figs, plans):
            fig.output = _render_image(Path(plan.info["source"]), plan, fig.kind, staging, fig.crop)
    except (HelperError, ValueError, KeyError) as exc:
        render_error = exc
    else:
        # The pages as they stand, masks as proposed, so the owner sees them before
        # confirming. Private storage only; never verified or released.
        result.pages = _pages(read.document, pages / "sanitized", [(f, f.output or b"") for f in figs])

    review = _review(names, [matcher.reviewable(t) for t in review_text(read.document)] + image_texts, lexicon)
    result.review = review
    if review:
        # Nothing can be released until every word has a decision, so
        # verification waits for the rerun after classification.
        result.status = "needs_review"
        result.reasons.append(f"{len(review)} unfamiliar words need a decision")
        return result
    pending = sum(1 for p in plans if not p.info["confirmed"])
    if pending:
        result.status = "needs_review"
        result.reasons.append(f"confirm the masks on {'one image' if pending == 1 else f'{pending} images'}")
        return result
    if render_error is not None:
        result.reasons.append(f"cannot write a masked image: {render_error}")
        return result

    staged = staging / f"{secrets.token_hex(8)}.docx"
    _create_private(staged)
    try:
        write_docx(read.document, staged)
        if not _preserved(texts, staged):
            result.reasons.append("content preservation check failed")
            return result
        if hooks.after_stage:
            hooks.after_stage(staged)
        _progress(staging, "Verifying saved Word document")
        check = verifier.verify_docx(staged, profile_path, source_texts=source_texts)
        result.sha256 = check.sha256
        if check.status != "pass":
            label = "verifier found references" if check.status == "findings" else "inspection incomplete"
            result.reasons.append(label)
            result.reasons.extend(check.findings + check.problems)
            # Media parts are numbered in document order, like the pictures kept.
            for n, info in enumerate((p.info for p in plans), 1):
                for name, regions in check.media_regions.items():
                    if name.startswith(f"word/media/image{n}."):
                        info["found"] = regions
            return result
        if not release_files:
            result.status = "ready"  # verified; nothing released in a dry run
            return result
        if hooks.before_release:
            hooks.before_release(staged)
        assert out_dir is not None
        name = _next_name(out_dir)
        try:
            release(staged, check.sha256, out_dir, name)
        except ReleaseRefused as exc:
            result.reasons.append(f"release refused: {exc}")
            return result
        result.status = "ready"
        result.output = name
        return result
    finally:
        staged.unlink(missing_ok=True)


@dataclass
class _ImagePlan:
    """What DocSan proposes for one image, and what the app shows the owner."""

    masks: list
    names: list[str]  # names the matcher wants reviewed
    visible: list[str]  # each line's text that no mask covers
    info: dict
    width: int
    height: int
    choice: dict = field(default_factory=dict)
    print: Print | None = None
    reviewable: list[str] = field(default_factory=list)


# Why a picture in a Word file is removed whole: the owner chose it, it sits in
# a header or footer (the template's logos and bands), it matches a logo the
# profile remembers, or it matches another picture removed from this file.
REMOVED = ("chosen", "margin", "profile", "same")


def _choose_removals(plans: list[_ImagePlan], choices: dict[str, dict], logos: list[Print]) -> None:
    """Set each plan's ``removed`` reason, or None to mask it. The owner's
    "keep" or "remove" wins over every rule."""
    for plan in plans:
        pick = choices.get(plan.info["key"], {}).get("picture")
        reason = None
        if pick == "remove":
            reason = "chosen"
        elif pick != "keep":
            if plan.info["margin"] and steps.enabled("remove-margin-pictures"):
                reason = "margin"
            elif plan.print and steps.enabled("match-logos") and any(same(plan.print, lg) for lg in logos):
                reason = "profile"
        plan.info["removed"] = reason
    changed = steps.enabled("match-logos")
    while changed:
        changed = False
        removed = [p.print for p in plans if p.info["removed"] and p.print]
        for plan in plans:
            pick = choices.get(plan.info["key"], {}).get("picture")
            if plan.info["removed"] or pick == "keep" or not plan.print:
                continue
            if any(same(plan.print, other) for other in removed):
                plan.info["removed"] = "same"
                changed = True
    for plan in plans:
        if plan.info["removed"]:
            plan.info["confirmed"] = True


def _plan_image(path: Path, data: bytes, kind: str, matcher: Matcher, choice: dict, crop=None, drawn=()) -> _ImagePlan:
    """Read the text, plan and fit masks, add the owner's own masks and work
    out the digest the owner confirms. ``drawn`` is the text a vector drawing
    contains, matched exactly alongside what OCR reads."""
    text = read_text(path, crop)
    if crop and any(crop):
        text.lines += visible_crop_lines(read_text(path), crop, text.width, text.height)
    text.lines = drawing_lines(list(drawn)) + text.lines
    matcher = matcher.with_context([r.text for line in text.lines for r in line.readings])
    found, names = plan_masks(text, matcher)
    found += continuation_masks(text)
    found = fit_to_ink(path, found, text.width, text.height, crop)
    best = text.best()
    found += field_masks(text, lines=best)
    found += visual_masks(path, text, found, lines=best, crop=crop)
    found += footer_masks(text, found, lines=best)
    masks = found + owner_masks(choice.get("masks", []), text.width, text.height)
    digest = plan_digest(hashlib.sha256(data + repr(crop).encode()).hexdigest(), masks)
    best = text.best()
    visible = [unmasked(line, masks) for line in best]
    words = sum(len(line.readings[0].text.split()) for line in best)
    info = {
        "width": text.width,
        "height": text.height,
        "format": kind,
        "frames": text.frames,
        "masks": [{"box": list(m.box), "label": m.label, "source": m.source} for m in masks],
        "lines": [{"box": list(line.box), "text": line.readings[0].text} for line in best],
        "visible": visible,  # for search
        "digest": digest,
        "confirmed": choice.get("confirmed") == digest,
        "found": [],
        # A reference with little else around it is most likely a logo: the app
        # suggests removing the picture whole.
        "logo": any(m.source == "text" for m in found) and words <= 8,
        "removed": None,
    }
    return _ImagePlan(masks, names, visible, info, text.width, text.height, choice,
                      reviewable=reviewable_lines(text,masks,matcher))


def _render_image(path: Path, plan: _ImagePlan | PagePlan, kind: str, staging: Path, crop=None, quality: float | None = None) -> bytes:
    """Burn the masks into the pixels and rebuild the file with pixels only."""
    raw = staging / f"{secrets.token_hex(8)}.raw"
    try:
        request = {
            "command": "mask",
            "input": str(path),
            "output": str(raw),
            "format": "raw" if kind == "png" else "jpeg",
            "boxes": [list(m.box) for m in plan.masks],
        }
        if quality is not None:
            request["quality"] = quality
        if not steps.enabled("ocr-orientation"):
            request["orientation"] = False
        if crop and any(crop):
            request["crop"] = list(crop)
        answer = call(request)
        if (answer["width"], answer["height"]) != (plan.width, plan.height):
            raise ValueError("the masked image has a different size")
        produced = raw.read_bytes()
        return write_png(produced, plan.width, plan.height, bool(answer["alpha"])) if kind == "png" else clean_jpeg(produced)
    finally:
        raw.unlink(missing_ok=True)


def _process_image(
    src: Path,
    staging: Path,
    out_dir: Path | None,
    profile_path: Path,
    matcher: Matcher,
    lexicon: Lexicon,
    choice: dict,
    hooks: Hooks,
    release_files: bool,
) -> FileResult:
    """Find text, plan masks, wait for the owner to confirm them, then mask,
    rebuild and verify. The owner confirms the digest of this exact image
    with exactly these masks; any change asks again."""
    result = FileResult(source=str(src), status="blocked", kind="image")
    if src.is_symlink() or not src.is_file():
        result.reasons.append("not a regular file")
        return result
    data = src.read_bytes()
    kind = sniff(data)
    if kind is None:
        result.reasons.append("not a PNG or JPEG image")
        return result
    damage = incomplete(data, kind)
    if damage:
        result.reasons.append(f"cannot read the image: {damage}")
        return result
    try:
        plan = _plan_image(src, data, kind, matcher, choice)
    except HelperError as exc:
        result.reasons.append(f"cannot read the image: {exc}")
        return result
    except ValueError as exc:
        result.reasons.append(str(exc))
        return result
    if plan.info["frames"] > 1:
        result.dropped["extra image frames"] = plan.info["frames"] - 1
    for m in plan.masks:
        if m.source == "text":
            result.replacements[m.label] = result.replacements.get(m.label, 0) + 1
    result.image = plan.info
    result.preview_original = [{"kind": "body", "level": 0, "text": line["text"]} for line in plan.info["lines"]]
    result.preview_sanitized = [{"kind": "body", "level": 0, "text": " ".join(v.split())} for v in plan.visible]
    result.review = _review(plan.names, plan.reviewable, lexicon)
    if result.review:
        result.status = "needs_review"
        result.reasons.append(f"{len(result.review)} unfamiliar words need a decision")
        return result
    if not plan.info["confirmed"]:
        result.status = "needs_review"
        result.reasons.append("confirm the masks on this image")
        return result

    ext = ".png" if kind == "png" else ".jpg"
    staged = staging / f"{secrets.token_hex(8)}{ext}"
    try:
        out = _render_image(src, plan, kind, staging)
        _create_private(staged)
        staged.write_bytes(out)
        if hooks.after_stage:
            hooks.after_stage(staged)
        check = verifier.verify_image(staged, profile_path)
        result.sha256 = check.sha256
        if check.status != "pass":
            label = "verifier found references" if check.status == "findings" else "inspection incomplete"
            result.reasons.append(label)
            result.reasons.extend(check.findings + check.problems)
            result.image["found"] = check.regions
            return result
        if not release_files:
            result.status = "ready"
            return result
        if hooks.before_release:
            hooks.before_release(staged)
        assert out_dir is not None
        name = _next_name(out_dir, "image", ext)
        try:
            release(staged, check.sha256, out_dir, name)
        except ReleaseRefused as exc:
            result.reasons.append(f"release refused: {exc}")
            return result
        result.status = "ready"
        result.output = name
        return result
    except (HelperError, ValueError, KeyError) as exc:
        result.reasons.append(f"cannot write the masked image: {exc}")
        return result
    finally:
        staged.unlink(missing_ok=True)


def _render_page(path: Path, plan: PagePlan, points: tuple[float, float], staging: Path) -> OutPage:
    """A page with its masks burned in, stored whichever way is smaller: JPEG,
    or without loss as a PNG's compressed rows (grey when it has no colour)."""
    jpeg = _render_image(path, plan, "jpeg", staging, quality=PAGE_QUALITY)
    png = staging / f"{secrets.token_hex(8)}.png"
    try:
        answer = call({"command": "mask", "input": str(path), "output": str(png), "format": "png",
                       "boxes": [list(m.box) for m in plan.masks]})
        width, height, colors, rows = png_rows(png.read_bytes())
    finally:
        png.unlink(missing_ok=True)
    if (width, height) != (plan.width, plan.height) or (answer["width"], answer["height"]) != (width, height):
        raise ValueError("the masked page has a different size")
    if len(rows) < len(jpeg):
        return OutPage(rows, width, height, points, plan.lines, "flate", colors)
    return OutPage(jpeg, plan.width, plan.height, points, plan.lines)


def _progress(staging: Path, stage: str, current: int = 0, total: int = 0) -> None:
    """A neutral, private status for the app; never source text or filenames."""
    target = staging.parent.parent / "progress.json"
    pending = target.with_suffix(".pending")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump({"stage": stage, "current": current, "total": total}, stream)
    os.replace(pending, target)


def _process_pdf(
    src: Path,
    staging: Path,
    out_dir: Path | None,
    profile_path: Path,
    matcher: Matcher,
    labeler: Labeler,
    lexicon: Lexicon,
    choices: dict[str, dict],
    hooks: Hooks,
    release_files: bool,
    pdf_dir: Path,
) -> FileResult:
    """Read and account for every part, plan each page, wait for every word's
    decision and every page's confirmation, then write the flattened PDF,
    verify it and release it (D-008). A page's choices are keyed
    "<path>#page-<n>", like a picture in a Word file."""
    result = FileResult(source=str(src), status="blocked", kind="pdf")
    if src.is_symlink() or not src.is_file():
        result.reasons.append("not a regular file")
        return result
    data = src.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    result.pdf_folder = digest[:24]
    _progress(staging, "Drawing PDF pages")
    read = read_pdf(src, data, pdf_dir / result.pdf_folder, digest)
    result.dropped = dict(read.dropped)
    if read.blocking:
        result.reasons.extend(read.blocking)
        return result
    plans: list[PagePlan] = []
    for page in read.pages:
        _progress(staging, "Reading and masking PDF pages", page.number, len(read.pages))
        key = f"{src}#page-{page.number}"
        try:
            plan = plan_page(page, digest, matcher, labeler, choices.get(key, {}))
        except HelperError as exc:
            result.reasons.append(f"page {page.number}: cannot read it: {exc}")
            return result
        except ValueError as exc:
            result.reasons.append(f"page {page.number}: {exc}")
            return result
        plan.info.update({"key": key, "index": page.number, "source": str(page.image), "margin": False})
        plans.append(plan)
    hidden = sum(p.info["hidden_words"] for p in plans)
    if hidden:
        result.dropped["words not visible on the page"] = hidden
    for plan in plans:
        for label, n in plan.replacements.items():
            result.replacements[label] = result.replacements.get(label, 0) + n
    result.images = [p.info for p in plans]
    for plan in plans:
        page_line = {"kind": "page", "level": plan.info["index"], "text": ""}
        result.preview_original += [page_line] + [{"kind": "body", "level": 0, "text": line["text"]} for line in plan.info["lines"]]
        result.preview_sanitized += [page_line] + [{"kind": "body", "level": 0, "text": v} for v in plan.info["visible"] if v]
    review = _review([n for p in plans for n in p.names], [t for p in plans for t in p.texts], lexicon)
    result.review = review
    if review:
        result.status = "needs_review"
        result.reasons.append(f"{len(review)} unfamiliar words need a decision")
        return result
    pending = sum(1 for p in plans if not p.info["confirmed"])
    if pending:
        result.status = "needs_review"
        result.reasons.append(f"confirm the masks on {'one page' if pending == 1 else f'{pending} pages'}")
        return result

    staged = staging / f"{secrets.token_hex(8)}.pdf"
    try:
        pages = [_render_page(page.image, plan, page.points, staging) for page, plan in zip(read.pages, plans)]
        _create_private(staged)
        write_pdf(pages, staged)
        if text_layer(staged.read_bytes()) != [expected_text(p) for p in pages]:
            result.reasons.append("content preservation check failed")
            return result
        if hooks.after_stage:
            hooks.after_stage(staged)
        _progress(staging, "Verifying saved PDF")
        check = verifier.verify_pdf(staged, profile_path)
        result.sha256 = check.sha256
        if check.status != "pass":
            label = "verifier found references" if check.status == "findings" else "inspection incomplete"
            result.reasons.append(label)
            result.reasons.extend(check.findings + check.problems)
            for info in result.images:
                info["found"] = check.media_regions.get(f"page-{info['index']}", [])
            return result
        if not release_files:
            result.status = "ready"
            return result
        if hooks.before_release:
            hooks.before_release(staged)
        assert out_dir is not None
        name = _next_name(out_dir, "document", ".pdf")
        try:
            release(staged, check.sha256, out_dir, name)
        except ReleaseRefused as exc:
            result.reasons.append(f"release refused: {exc}")
            return result
        result.status = "ready"
        result.output = name
        return result
    except (HelperError, ValueError, KeyError) as exc:
        result.reasons.append(f"cannot write the PDF: {exc}")
        return result
    finally:
        staged.unlink(missing_ok=True)


def _report_entry(f: FileResult) -> dict:
    """The private report keeps outcomes and reasons, not document text."""
    entry = asdict(f)
    entry.pop("preview_original")
    entry.pop("preview_sanitized")
    entry.pop("pages", None)
    entry.pop("pages_original", None)
    entry.pop("pdf_folder", None)
    image = entry.pop("image")
    if image is not None:
        entry["masks"] = len(image["masks"])
    pictures = entry.pop("images")
    if pictures:
        entry["pictures"] = [{"masks": len(p["masks"]), "confirmed": p["confirmed"], "removed": p["removed"]} for p in pictures]
    return entry


def run_batch(
    inputs: list[Path],
    profile_path: Path,
    out_dir: Path | None,
    private_dir: Path,
    *,
    lexicon: Lexicon,
    remove_images: bool = False,
    label_unreadable: bool = False,
    hooks: Hooks | None = None,
    release: bool = True,
    images: dict[str, dict] | None = None,
) -> BatchResult:
    """Process a batch. With ``release=False`` (a dry run) files are verified
    but nothing is written to ``out_dir``, which may then be None. ``images``
    maps an image's path to the owner's choices: extra ``masks`` and the
    ``confirmed`` digest; a picture in a Word file is keyed "<path>#image-<n>"
    and a page of a PDF "<path>#page-<n>"."""
    hooks = hooks or Hooks()
    profile = load_profile(profile_path)
    if release:
        if out_dir is None:
            raise ValueError("an output folder is required to release files")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    private_dir = _private_dir(Path(private_dir))
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)
    staging = _private_dir(private_dir / "staging" / batch_id)
    reports = _private_dir(private_dir / "reports")
    # Pictures taken out of Word files, for the app to show; replaced on every run.
    figure_dir = private_dir / "figures"
    shutil.rmtree(figure_dir, ignore_errors=True)
    figure_dir = _private_dir(figure_dir)
    page_dir = private_dir / "pages"  # page previews of the sanitized files, replaced each batch
    shutil.rmtree(page_dir, ignore_errors=True)
    page_dir = _private_dir(page_dir)
    # Each PDF's pages as drawn, kept while the batch uses them: the app shows
    # them, and drawing a long document again after every decision takes time.
    pdf_dir = _private_dir(private_dir / "pdf")

    matcher = Matcher(profile, glossary_words())
    logos = [Print(int(bits, 16), aspect) for bits, aspect in profile.logos]
    labeler = Labeler()  # the replacement mapping; never written to disk
    # Every word the profile classifies is settled: kept system names, and
    # first names that the person style keeps ("Paul A.").
    known = Lexicon(
        lexicon.words
        | {fold_word(w) for w in profile.safe_words}
        | {fold_word(t.text) for t in profile.terms}
        | {fold_word(w) for t in profile.terms for w in re.findall(r"[^\W_]+", t.text)}
    )
    files: list[FileResult] = []
    status = "complete"
    sources = _inputs(inputs)
    try:
        with disk_cache(private_dir / "cache"):
            _process_all(files, sources, staging, out_dir, Path(profile_path), matcher, labeler, known, remove_images,
                         label_unreadable, hooks, release, images or {}, figure_dir, page_dir, pdf_dir, logos)
    except BaseException:
        status = "cancelled"
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        # Drawn pages of PDFs this batch no longer holds.
        current = {f.pdf_folder for f in files if f.pdf_folder}
        for folder in pdf_dir.iterdir():
            if folder.name not in current:
                shutil.rmtree(folder, ignore_errors=True)
        report_path = reports / f"{batch_id}.json"
        report_path.write_text(
            json.dumps({"batch": batch_id, "status": status, "files": [_report_entry(f) for f in files]}, indent=2),
            encoding="utf-8",
        )
        os.chmod(report_path, 0o600)
    return _finish(batch_id, files, report_path, profile, profile_path, matcher, out_dir)


def _process_all(files, sources, staging, out_dir, profile_path, matcher, labeler, known, remove_images, label_unreadable,
                 hooks, release, images, figure_dir, page_dir, pdf_dir, logos) -> None:
    """Process each file in turn, adding its result to ``files``."""
    for src in sources:
        if src.suffix.lower() in IMAGE_SUFFIXES:
            files.append(_process_image(src, staging, out_dir, profile_path, matcher, known, images.get(str(src), {}), hooks, release))
        elif src.suffix.lower() == PDF_SUFFIX:
            files.append(_process_pdf(src, staging, out_dir, profile_path, matcher, labeler, known, images, hooks, release, pdf_dir))
        else:
            files.append(_process(src, staging, out_dir, profile_path, matcher, labeler, known, remove_images, label_unreadable,
                                  hooks, release, images, figure_dir, page_dir, logos))


def _finish(batch_id, files, report_path, profile, profile_path, matcher, out_dir) -> BatchResult:
    """Remember removed logos and write the shareable summary."""
    summary_path = None
    released = [f for f in files if f.status == "ready" and f.output]
    # Pictures exported removed are remembered, so the same logo goes next time too.
    learned = [(i["print"], i["aspect"]) for f in released for i in f.images if i["removed"] and i.get("print")]
    if learned:
        add_logos(Path(profile_path), learned)
    if released and out_dir is not None:
        policy = profile.policy
        for hit in reversed(matcher.find(policy).hits):
            policy = policy[: hit.start] + f"[{hit.label}]" + policy[hit.end :]
        summary = {
            "tool": "DocSan",
            "coverage": "DOCX component contract; image component contract; PDF component contract",
            "policy": policy,
            "profile_sha256": hashlib.sha256(Path(profile_path).read_bytes()).hexdigest(),
            "processing": "local deterministic rules and OCR; no external model",
            "batch": batch_id,
            "files": [
                {
                    "name": f.output,
                    "sha256": f.sha256,
                    "input_sha256": hashlib.sha256(Path(f.source).read_bytes()).hexdigest(),
                    "components": len(f.images) if f.kind == "pdf" else None,
                    "mask_sources": dict(Counter(m["source"] for i in f.images for m in i["masks"])),
                    "checks": {"inspection": "complete", "verifier": "pass", "review": "complete"}
                    | ({"masks": "confirmed by the owner"} if f.kind in ("image", "pdf") else {}),
                }
                for f in released
            ],
        }
        summary_path = out_dir / f"summary-{batch_id}.json"
        summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return BatchResult(batch_id, files, report_path, summary_path)
