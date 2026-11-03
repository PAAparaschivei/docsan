"""Read a PDF: account for every part, draw every page and take its text layer.

Apple's PDF parser does the reading, through the Vision helper's
``pdf-read`` command (``app/Helpers/docsan-vision/PDF.swift``): PDFKit draws
each page as a viewer shows it and reads its text with the box of every
character, and Core Graphics walks the document's structure. The rules for
each part are here, and follow ``docs/pdf-component-contract.md``. A part
without a rule blocks the file.

The output is a fresh, flattened PDF (D-008), so a part that is only
"removed" never needs undoing: it is simply not carried. What matters is what
the pages show, and that the reader can draw them completely.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..image.helper import HelperError, call, helper_build
from ..image.text import Box

DPI = 200.0
MAX_PAGE_PIXELS = 36_000_000  # a large page is drawn at a lower resolution
OCR_DPI = 400.0  # each page is drawn again, sharper, for reading only: diagram labels can be a few pixels high at DPI
MAX_OCR_PIXELS = 100_000_000
MAX_PAGES = 500
MAX_BYTES = 512 * 1024 * 1024

READ = "read"  # used to draw the pages
REMOVED = "removed"  # inspected and not carried into the output


def _each(names: str, rule: str) -> dict[str, str]:
    """The same rule for every name in a space-separated list."""
    return dict.fromkeys(names.split(), rule)


# Every key of the document catalog. A string is the reason the key blocks.
CATALOG: dict[str, str] = {
    "Type": READ, "Pages": READ, "AcroForm": READ, "OCProperties": READ,
    **_each(
        "Version Extensions PageLabels Names Dests ViewerPreferences PageLayout PageMode Outlines Threads OpenAction AA URI "
        "Metadata StructTreeRoot MarkInfo Lang SpiderInfo OutputIntents PieceInfo Perms Legal Requirements DSS AF DPartRoot",
        REMOVED,
    ),
    "Collection": "it is a PDF portfolio, whose content is in attached files",
    "NeedsRendering": "it is an XFA form, which only Adobe software can draw",
}
# The name dictionary: named destinations, appearances, scripts, page templates, web capture, attached files, media.
NAMES: dict[str, str] = _each(
    "Dests AP JavaScript Pages Templates IDS URLS EmbeddedFiles AlternatePresentations Renditions", REMOVED
)
ACROFORM: dict[str, str] = {
    **_each("Fields NeedAppearances SigFlags CO DR DA Q", READ),
    "XFA": "it is an XFA form, which only Adobe software can draw",
}
PAGE: dict[str, str] = {
    **_each("Type Parent Kids Count Resources MediaBox CropBox Rotate Contents Group Annots UserUnit", READ),
    **_each(
        "LastModified BleedBox TrimBox ArtBox BoxColorInfo Thumb B Dur Trans AA Metadata PieceInfo StructParents ID PZ "
        "SeparationInfo Tabs TemplateInstantiated PresSteps VP AF OutputIntents DPart ProcSet",
        REMOVED,
    ),
}
RESOURCES: dict[str, str] = _each("ExtGState ColorSpace Pattern Shading XObject Font ProcSet Properties", READ)
XOBJECTS: dict[str, str] = {
    "Image": READ,
    "Form": READ,
    "PS": "it holds a PostScript drawing, which macOS does not draw",
}
_EXTERNAL = "it holds content stored in another file"
IMAGE: dict[str, str] = {
    **_each(
        "Type Subtype Width Height ColorSpace BitsPerComponent Intent ImageMask Mask Decode Interpolate SMask SMaskInData "
        "Name StructParent ID Length Filter DecodeParms DL OC Measure PtData",
        READ,
    ),
    **_each("Metadata OPI Alternates AF", REMOVED),
    **_each("F FFilter FDecodeParms", _EXTERNAL),
}
FORM: dict[str, str] = {
    **_each(
        "Type Subtype FormType BBox Matrix Resources Group Name StructParent StructParents OC Length Filter DecodeParms DL "
        "Measure PtData",
        READ,
    ),
    **_each("Metadata PieceInfo LastModified OPI AF", REMOVED),
    **_each("F FFilter FDecodeParms", _EXTERNAL),
    "Ref": "it holds a drawing that refers to another file",
}
# Annotations are drawn as a viewer shows them; their dictionaries (comments,
# authors, links, attached files, media) are not carried.
ANNOTATIONS: dict[str, str] = {
    **_each(
        "Text Link FreeText Line Square Circle Polygon PolyLine Highlight Underline Squiggly StrikeOut Caret Stamp Ink Popup "
        "FileAttachment Sound Movie Screen Widget PrinterMark TrapNet Watermark 3D Projection RichMedia",
        READ,
    ),
    "Redact": "it has redaction marks that were never applied",
}


@dataclass
class TextLine:
    text: str
    box: Box
    chars: list[Box | None]  # one per character
    ink: list[float]  # per character: the share of its box that differs from the background


@dataclass
class Page:
    number: int
    image: Path  # the page drawn as PNG
    width: int  # pixels
    height: int
    points: tuple[float, float]  # the page as shown, in points
    lines: list[TextLine]
    ocr_image: Path | None = None  # the page drawn at OCR_DPI, for reading only
    ocr_width: int = 0


@dataclass
class PdfRead:
    pages: list[Page] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)


def damage(data: bytes) -> str | None:
    """Why the file cannot be read completely, or None. A viewer quietly
    repairs a damaged PDF and may draw less than the original holds."""
    if len(data) > MAX_BYTES:
        return "the PDF is larger than 512 MB"
    if b"%PDF-" not in data[:1024]:
        return "not a PDF file"
    tail = data[-1024:]
    if b"%%EOF" not in tail:
        return "the PDF file is cut short"
    found = re.findall(rb"startxref\s+(\d+)", tail)
    if not found:
        return "the PDF has no cross-reference table"
    at = int(found[-1]) + data[:1024].index(b"%PDF-")
    head = data[at : at + 32].lstrip()
    if not (head.startswith(b"xref") or re.match(rb"\d+\s+\d+\s+obj\b", head)):
        return "the PDF's cross-reference table is damaged"
    return None


def _rules(found: list[str], rules: dict[str, str], where: str, blocking: list[str]) -> None:
    for key in found:
        # A producer's own data, under its registered prefix ("PTEX.FileName"),
        # is never drawn: removed like the rest of the structure.
        rule = rules.get(key, REMOVED if re.fullmatch(r"[A-Za-z0-9]{2,8}\.[A-Za-z0-9.]+", key) else None)
        if rule is None:
            blocking.append(f"{where} has a part DocSan does not know (/{key})")
        elif rule not in (READ, REMOVED):
            blocking.append(f"{where}: {rule}")


def account(structure: dict) -> tuple[list[str], dict[str, int]]:
    """Apply the contract to the helper's inventory: reasons to block, and what is removed."""
    blocking: list[str] = []
    if structure.get("encrypted"):
        return ["the PDF is encrypted or password-protected; remove the protection and add it again"], {}
    _rules(structure.get("catalog", []), CATALOG, "the document", blocking)
    _rules(structure.get("names", []), NAMES, "the document's names", blocking)
    _rules(structure.get("acroform", []), ACROFORM, "the form", blocking)
    _rules(structure.get("page_tree", []), PAGE, "the page tree", blocking)
    _rules(structure.get("page", []), PAGE, "a page", blocking)
    _rules(structure.get("resources", []), RESOURCES, "a page's resources", blocking)
    _rules(list(structure.get("xobjects", {})), XOBJECTS, "a page", blocking)
    _rules(structure.get("image", []), IMAGE, "a picture", blocking)
    _rules(structure.get("form", []), FORM, "a drawing", blocking)
    _rules(list(structure.get("annotations", {})), ANNOTATIONS, "a page", blocking)
    if int(structure.get("page_count", 0)) > MAX_PAGES:
        blocking.append(f"the PDF has more than {MAX_PAGES} pages; split it first")
    if int(structure.get("page_count", 0)) < 1:
        blocking.append("the PDF has no pages")
    catalog = set(structure.get("catalog", []))
    dropped: dict[str, int] = {}

    def drop(what: str, n: int) -> None:
        if n > 0:
            dropped[what] = dropped.get(what, 0) + n

    drop("document information", len(structure.get("info", [])))
    drop("XMP metadata", int("Metadata" in catalog))
    drop("bookmarks", int(structure.get("outline", 0)))
    drop("attached files", int(structure.get("embedded_files", 0)) + int(structure.get("annotations", {}).get("FileAttachment", 0)))
    drop("scripts and opening actions", int(structure.get("javascript", 0)) + int("OpenAction" in catalog) + int("AA" in catalog))
    drop("annotations, comments and links", sum(int(n) for n in structure.get("annotations", {}).values()))
    drop("form fields", int(structure.get("fields", 0)))
    drop("page labels", int("PageLabels" in catalog))
    drop("tags and alternative text", int("StructTreeRoot" in catalog))
    return blocking, dropped


def _request(src: Path, folder: Path) -> dict:
    return {"command": "pdf-read", "input": str(src), "output": str(folder), "dpi": DPI, "max_pixels": MAX_PAGE_PIXELS,
            "ocr_dpi": OCR_DPI, "max_ocr_pixels": MAX_OCR_PIXELS}


def _helper_read(src: Path, folder: Path, digest: str) -> dict:
    """The helper's answer, kept beside the pages it drew: a batch is checked
    again after every decision, and drawing a long document takes time."""
    saved = folder / "read.json"
    request = _request(src, folder)
    try:
        key = {**request, "input": digest, "helper": helper_build()}
    except OSError as exc:
        raise HelperError("the image helper is not installed") from exc
    if saved.is_file():
        try:
            cached = json.loads(saved.read_text(encoding="utf-8"))
            pages = cached["answer"]["pages"]
            if cached.get("key") == key and all(Path(p["image"]).is_file() and Path(p.get("ocr_image", p["image"])).is_file()
                                                for p in pages):
                return cached["answer"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    folder.mkdir(parents=True, exist_ok=True)
    answer = call(request, timeout=3600)
    saved.write_text(json.dumps({"key": key, "answer": answer}), encoding="utf-8")
    return answer


def read_pdf(src: Path, data: bytes, folder: Path, digest: str) -> PdfRead:
    """Read ``src`` (whose bytes are ``data``); pages are drawn into ``folder``."""
    result = PdfRead()
    problem = damage(data)
    if problem:
        result.blocking.append(problem)
        return result
    try:
        answer = _helper_read(src, folder, digest)
    except HelperError as exc:
        result.blocking.append(f"cannot read the PDF: {exc}")
        return result
    blocking, dropped = account(answer.get("structure", {}))
    result.dropped = dropped
    if blocking:
        result.blocking = blocking
        return result
    pages = answer.get("pages", [])
    if len(pages) != int(answer["structure"].get("page_count", -1)):
        result.blocking.append("cannot read every page of the PDF")
        return result
    for n, p in enumerate(pages, 1):
        lines = [
            TextLine(
                line["text"],
                tuple(line["box"]),
                [tuple(c) if c else None for c in line["chars"]],
                [float(v) for v in line["ink"]],
            )
            for line in p["lines"]
        ]
        if any(len(line.chars) != len(line.text) or len(line.ink) != len(line.text) for line in lines):
            result.blocking.append(f"page {n}: the text layer could not be read")
            return result
        result.pages.append(Page(n, Path(p["image"]), int(p["width"]), int(p["height"]),
                                 (float(p["points"][0]), float(p["points"][1])), lines,
                                 Path(p["ocr_image"]) if p.get("ocr_image") else None, int(p.get("ocr_width", 0))))
    return result
