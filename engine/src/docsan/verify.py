"""Independent verifier for emitted DOCX, PDF, PNG and JPEG files.

This module deliberately shares no code with the sanitizer. It imports only
the Python standard library, parses XML with ``xml.etree`` instead of lxml,
keeps its own normalization and patterns, reads the profile file itself and
uses macOS ``textutil`` as a second text extractor. Images are read with
Tesseract, a second OCR engine, while the sanitizer uses Apple Vision. PDFs
are parsed with the verifier's own grammar of what the writer emits, and
PDFKit extracts their text a second time. A defect in the sanitizer
therefore cannot also hide a leak from the verifier.

Outcomes are kept apart: ``pass`` (inspected, nothing found), ``findings``
(inspected, something found) and ``could_not_inspect`` (inspection incomplete).
Only ``pass`` allows release. Before a clean result counts, the verifier plants
a random test name in a copy of the file and must find it in every view.
"""

from __future__ import annotations

import array
import base64
import functools
import hashlib
import html
import io
import json
import math
import os
import re
import secrets
import struct
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

VERIFIER_STEPS: tuple[str, ...] = (
    "strip-invisible",
    "map-lookalikes",
    "fold-accents",
    "fold-width",
    "map-dashes",
    "map-spaces",
    "fold-case",
    "join-runs",
    "skeleton",
    "flex-separators",
    "camel-case",
    "scan-raw",
    "emails",
    "urls",
    "domains",
    "phones",
    "identifiers",
    "classification-labels",
    "package-bytes",
    "part-allowlist",
    "element-allowlist",
    "second-extractor",
    "relationships",
    "self-test",
)
IMAGE_VERIFIER_STEPS: tuple[str, ...] = (
    "image-structure",
    "transparent-pixels",
    "ocr-text",
    "ocr-loose-case",
    "image-self-test",
    "image-codes",
)
PDF_VERIFIER_STEPS: tuple[str, ...] = (
    "pdf-structure",
    "pdf-metadata",
    "pdf-text-layer",
    "pdf-join-lines",
    "pdf-page-ocr",
    "pdf-barcodes",
    "pdf-field-values",
    "pdf-ocr-zoom",
    "pdf-second-extractor",
    "pdf-self-test",
)
_ALL_STEPS = frozenset(VERIFIER_STEPS + IMAGE_VERIFIER_STEPS + PDF_VERIFIER_STEPS)
_off: set[str] = set()


def _on(step: str) -> bool:
    if step not in _ALL_STEPS:
        raise KeyError(f"unknown verifier step: {step}")
    return step not in _off


@contextmanager
def step_disabled(step: str) -> Iterator[None]:
    """Disable one verifier step for the duration of a test."""
    if step not in _ALL_STEPS:
        raise KeyError(f"unknown verifier step: {step}")
    _off.add(step)
    try:
        yield
    finally:
        _off.discard(step)


# --- What a valid output looks like -------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_XML = "{http://www.w3.org/XML/1998/namespace}"

EXPECTED_PARTS = frozenset(
    {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/_rels/document.xml.rels",
        "word/styles.xml",
        "word/numbering.xml",
        "word/settings.xml",
    }
)

# Masked images: word/media/image<n>.png or .jpg, one per picture.
MEDIA_PART = re.compile(r"word/media/image([1-9][0-9]{0,3})\.(png|jpg)")
# Headers and footers, numbered by their place among the document's parts, and their relationships.
HEADER_FOOTER_PART = re.compile(r"word/(header|footer)([1-9][0-9]{0,2})\.xml")
HEADER_FOOTER_RELS = re.compile(r"word/_rels/(header|footer)([1-9][0-9]{0,2})\.xml\.rels")

# SHA-256 of the fixed package relationships and of the fixed start of the
# styles part. Changing the writer's fixed parts must be a deliberate change
# here as well.
PINNED_PARTS = {
    "_rels/.rels": "6b1f432240d639e697da68ee65a70d6252b5eb7204e58daeae6514a0f85f2285",
}
STYLES_HEAD_SHA256 = "65347ea4ab0ceed9b60c741a5ae774ae32fbff4b3158897ec49c251b8e8868e8"

_WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PICTURE_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_WPS_URI = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_WPS = "{" + _WPS_URI + "}"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_IMAGE_REL = _REL + "image"


def _set(text: str) -> frozenset[str]:
    return frozenset(text.split())


@functools.cache
def _fonts() -> frozenset[str]:
    path = Path(__file__).with_name("data") / "fonts.txt"
    return frozenset(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#"))


# Attribute values the writer can choose. A pattern is a full-match regular
# expression; a set lists the allowed words. Nothing here can hold free text
# except w:t elements and list number texts, which every scan reads.
_INT = re.compile(r"-?[0-9]{1,9}")
_UINT = re.compile(r"[0-9]{1,9}")
_ON = re.compile(r"[01]")
_COLOR = re.compile(r"[0-9A-F]{6}|auto")
_FONT = "font"
_ANY_TEXT = "text"
_BORDER_STYLES = _set(
    "nil none single thick double dotted dashed dotDash dotDotDash triple thinThickSmallGap thickThinSmallGap "
    "thinThickThinSmallGap thinThickMediumGap thickThinMediumGap thinThickThinMediumGap thinThickLargeGap "
    "thickThinLargeGap thinThickThinLargeGap wave doubleWave dashSmallGap dashDotStroked threeDEmboss "
    "threeDEngrave outset inset"
)
_SHADING = _set(
    "nil clear solid horzStripe vertStripe reverseDiagStripe diagStripe horzCross diagCross thinHorzStripe "
    "thinVertStripe thinReverseDiagStripe thinDiagStripe thinHorzCross thinDiagCross pct5 pct10 pct12 pct15 "
    "pct20 pct25 pct30 pct35 pct37 pct40 pct45 pct50 pct55 pct60 pct62 pct65 pct70 pct75 pct80 pct85 pct87 "
    "pct90 pct95"
)
_NUMBER_FORMATS = _set(
    "decimal upperRoman lowerRoman upperLetter lowerLetter ordinal cardinalText ordinalText hex chicago "
    "ideographDigital japaneseCounting aiueo iroha decimalFullWidth decimalHalfWidth japaneseLegal "
    "japaneseDigitalTenThousand decimalEnclosedCircle decimalFullWidth2 aiueoFullWidth irohaFullWidth "
    "decimalZero bullet ganada chosung decimalEnclosedFullstop decimalEnclosedParen decimalEnclosedCircleChinese "
    "ideographEnclosedCircle ideographTraditional ideographZodiac ideographZodiacTraditional taiwaneseCounting "
    "ideographLegalTraditional taiwaneseCountingThousand taiwaneseDigital chineseCounting chineseLegalSimplified "
    "chineseCountingThousand koreanDigital koreanCounting koreanLegal koreanDigital2 vietnameseCounting "
    "russianLower russianUpper none numberInDash hebrew1 hebrew2 arabicAlpha arabicAbjad hindiVowels "
    "hindiConsonants hindiNumbers hindiCounting thaiLetters thaiNumbers thaiCounting"
)
_WIDTH_TYPES = _set("nil pct dxa auto")
_BORDER = {"val": _BORDER_STYLES, "sz": _UINT, "space": _UINT, "color": _COLOR}
_MEASURE = {"w": _INT, "type": _WIDTH_TYPES}
_TOGGLE = {"val": _ON}
_FIELD = re.compile(r"(PAGE|NUMPAGES|SECTIONPAGES)( \\\* (Arabic|roman|ROMAN|alphabetic|ALPHABETIC))?")

_W_ELEMENTS: dict[str, dict[str, object]] = {
    **{t: {} for t in _set(
        "document body hdr ftr p pPr numPr pBdr tabs rPr r sectPr tbl tblPr tblBorders tblCellMar tblGrid tr trPr tc tcPr "
        "tcBorders tcMar drawing numbering txbxContent")},
    **{t: _TOGGLE for t in _set(
        "keepNext keepLines pageBreakBefore widowControl suppressAutoHyphens bidi contextualSpacing b bCs i iCs caps "
        "smallCaps strike dstrike rtl titlePg cantSplit tblHeader noWrap isLgl")},
    "pStyle": {"val": re.compile(r"Title|Heading[1-6]|Style[1-9][0-9]{0,3}")},
    "ilvl": {"val": re.compile(r"[0-8]")},
    "numId": {"val": _UINT},
    **{t: {**_BORDER, **_MEASURE} for t in ("top", "left", "bottom", "right")},
    **{t: _BORDER for t in ("between", "bar", "insideH", "insideV", "tl2br", "tr2bl")},
    "shd": {"val": _SHADING, "color": _COLOR, "fill": _COLOR},
    "tab": {"val": _set("left center right decimal bar num start end"), "leader": _set("none dot hyphen underscore heavy middleDot"), "pos": _INT},
    "spacing": {"before": _UINT, "after": _UINT, "line": _INT, "lineRule": _set("auto exact atLeast"),
                "beforeAutospacing": _ON, "afterAutospacing": _ON, "val": _INT},
    "ind": {"left": _INT, "right": _INT, "firstLine": _UINT, "hanging": _UINT},
    "jc": {"val": _set("left center right both distribute thaiDistribute highKashida lowKashida mediumKashida")},
    "t": {_XML + "space": _set("preserve")},
    "br": {"type": _set("page column")},
    "ptab": {"relativeTo": _set("margin"), "alignment": _set("left center right"), "leader": _set("none")},
    "fldSimple": {"instr": _FIELD},
    "rFonts": {"ascii": _FONT, "hAnsi": _FONT, "eastAsia": _FONT, "cs": _FONT},
    "color": {"val": _COLOR},
    "w": {"val": _UINT},
    "kern": {"val": _UINT},
    "position": {"val": _INT},
    "sz": {"val": _UINT},
    "szCs": {"val": _UINT},
    "highlight": {"val": _set("black blue cyan green magenta red yellow white darkBlue darkCyan darkGreen darkMagenta "
                              "darkRed darkYellow darkGray lightGray")},
    "u": {"val": _set("single words double thick dotted dottedHeavy dash dashedHeavy dashLong dashLongHeavy dotDash "
                      "dashDotHeavy dotDotDash dashDotDotHeavy wave wavyHeavy wavyDouble"), "color": _COLOR},
    "vertAlign": {"val": _set("superscript subscript")},
    "headerReference": {"type": _set("default first even"), _R + "id": re.compile(r"rIdHdr[1-9][0-9]{0,2}")},
    "footerReference": {"type": _set("default first even"), _R + "id": re.compile(r"rIdFtr[1-9][0-9]{0,2}")},
    "type": {"val": _set("nextPage continuous evenPage oddPage nextColumn")},
    "pgSz": {"w": _UINT, "h": _UINT, "orient": _set("portrait landscape"), "code": _UINT},
    "pgMar": {"top": _INT, "right": _UINT, "bottom": _INT, "left": _UINT, "header": _UINT, "footer": _UINT, "gutter": _UINT},
    "pgNumType": {"fmt": _NUMBER_FORMATS, "start": _UINT},
    "cols": {"num": _UINT, "space": _UINT, "equalWidth": _ON, "sep": _ON},
    "col": {"w": _UINT, "space": _UINT},
    "vAlign": {"val": _set("top center both bottom")},
    "tblW": _MEASURE,
    "tblCellSpacing": _MEASURE,
    "tblInd": _MEASURE,
    "tblLayout": {"type": _set("fixed autofit")},
    "gridCol": {"w": _INT},
    "gridBefore": {"val": _UINT},
    "gridAfter": {"val": _UINT},
    "wBefore": _MEASURE,
    "wAfter": _MEASURE,
    "trHeight": {"val": _UINT, "hRule": _set("auto exact atLeast")},
    "tcW": _MEASURE,
    "gridSpan": {"val": _UINT},
    "vMerge": {"val": _set("restart continue")},
    "textDirection": {"val": _set("lrTb tbRl btLr lrTbV tbRlV tbLrV tb rl lr tbV rlV lrV")},
    # Numbering
    "abstractNum": {"abstractNumId": _UINT},
    "multiLevelType": {"val": _set("singleLevel multilevel hybridMultilevel")},
    "lvl": {"ilvl": re.compile(r"[0-8]")},
    "start": {"val": _UINT},
    "numFmt": {"val": _NUMBER_FORMATS},
    "lvlRestart": {"val": _UINT},
    "suff": {"val": _set("tab space nothing")},
    "lvlText": {"val": _ANY_TEXT},
    "lvlJc": {"val": _set("left center right")},
    "num": {"numId": _UINT},
    "abstractNumId": {"val": _UINT},
    "lvlOverride": {"ilvl": re.compile(r"[0-8]")},
    "startOverride": {"val": _UINT},
}
_DRAWING_NAME = re.compile(r"Picture [1-9][0-9]{0,3}|Text Box [1-9][0-9]{0,3}")
_DISTANCE = {a: _UINT for a in ("distT", "distB", "distL", "distR")}
_DRAWING_ELEMENTS: dict[str, dict[str, object]] = {
    _WP + "inline": {},
    _WP + "anchor": {**_DISTANCE, "relativeHeight": _UINT, **{a: _ON for a in ("simplePos", "behindDoc", "locked", "layoutInCell", "allowOverlap")}},
    _WP + "simplePos": {"x": _set("0"), "y": _set("0")},
    _WP + "positionH": {"relativeFrom": _set("character column insideMargin leftMargin margin outsideMargin page rightMargin")},
    _WP + "positionV": {"relativeFrom": _set("bottomMargin insideMargin line margin outsideMargin page paragraph topMargin")},
    _WP + "align": {},
    _WP + "posOffset": {},
    _WP + "extent": {"cx": _UINT, "cy": _UINT},
    _WP + "effectExtent": {k: _INT for k in "ltrb"},
    _WP + "wrapNone": {},
    _WP + "wrapSquare": {"wrapText": _set("bothSides left right largest"), **_DISTANCE},
    _WP + "wrapTopAndBottom": {"distT": _UINT, "distB": _UINT},
    _WP + "docPr": {"id": _UINT, "name": _DRAWING_NAME},
    _A + "graphic": {},
    _A + "graphicData": {"uri": _set(f"{_PICTURE_URI} {_WPS_URI}")},
    _A + "blip": {_R + "embed": re.compile(r"rIdImg[1-9][0-9]{0,3}")},
    _A + "stretch": {},
    _A + "fillRect": {},
    _A + "xfrm": {},
    _A + "off": {"x": _set("0"), "y": _set("0")},
    _A + "ext": {"cx": _UINT, "cy": _UINT},
    _A + "prstGeom": {"prst": _set("rect roundRect ellipse snip1Rect snip2SameRect round1Rect round2SameRect flowChartProcess "
                                   "flowChartAlternateProcess wedgeRectCallout wedgeRoundRectCallout")},
    _A + "avLst": {},
    _A + "noFill": {},
    _A + "solidFill": {},
    _A + "srgbClr": {"val": re.compile(r"[0-9A-F]{6}")},
    _A + "ln": {"w": _UINT},
    _A + "spAutoFit": {},
    _A + "noAutofit": {},
    _PIC + "pic": {},
    _PIC + "nvPicPr": {},
    _PIC + "cNvPr": {"id": _UINT, "name": re.compile(r"Picture [1-9][0-9]{0,3}")},
    _PIC + "cNvPicPr": {},
    _PIC + "blipFill": {},
    _PIC + "spPr": {},
    _WPS + "wsp": {},
    _WPS + "cNvSpPr": {"txBox": _set("1")},
    _WPS + "spPr": {},
    _WPS + "txbx": {},
    _WPS + "bodyPr": {"lIns": _UINT, "tIns": _UINT, "rIns": _UINT, "bIns": _UINT, "anchor": _set("t ctr b"),
                      "vert": _set("horz vert vert270"), "wrap": _set("square none")},
}
# The only elements besides w:t that hold text: a floating drawing's position.
_ELEMENT_TEXT = {_WP + "align": _set("left right center inside outside top bottom"), _WP + "posOffset": _INT}
ALLOWED_ELEMENTS: dict[str, dict[str, object]] = {
    **{_W + t: {(_W + a if not a.startswith("{") else a): v for a, v in spec.items()} for t, spec in _W_ELEMENTS.items()},
    **_DRAWING_ELEMENTS,
}
_IMAGE_REL_ID = re.compile(r"rIdImg([1-9][0-9]{0,3})")
_ROOTS = {"word/document.xml": "document", "word/numbering.xml": "numbering"}

# Namespace and type identifiers that legitimately appear in every output.
KNOWN_IDENTIFIERS = (
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://schemas.openxmlformats.org/package/2006/content-types",
    "http://schemas.openxmlformats.org/package/2006/relationships",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "http://schemas.openxmlformats.org/drawingml/2006/main",
    "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "http://schemas.microsoft.com/office/word",
    "image/png",
    "image/jpeg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
    "application/vnd.openxmlformats-package.relationships+xml",
    "application/xml",
)

# --- Verifier's own normalization ---------------------------------------------

_FILLERS = frozenset("͏ᅟᅠ឴឵᠎ㅤﾠ")
_LOOKALIKE_PAIRS = (
    "аa вb еe ёe кk мm нh оo рp сc тt уy хx ѕs іi їi јj ԁd ԛq ԝw һh ӏl ɡg "
    "АA ВB ЕE ЁE КK МM НH ОO РP СC ТT УY ХX ЅS ІI ЇI ЈJ ԀD ԚQ ԜW ҺH ӀI "
    "αa βb εe ιi κk νv οo ρp τt υu χx ΑA ΒB ΕE ΖZ ΗH ΙI ΚK ΜM ΝN ΟO ΡP ΤT ΥY ΧX "
    "ıi ȷj łl ŁL øo ØO đd ĐD ħh ĦH ŧt ŦT ℓl"
)
_LOOKALIKES = {pair[0]: pair[1] for pair in _LOOKALIKE_PAIRS.split()}


def _vnorm(text: str, fold: bool = True) -> str:
    out: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if _on("strip-invisible") and (cat == "Cf" or ch in _FILLERS or 0xFE00 <= ord(ch) <= 0xFE0F):
            continue
        if _on("map-lookalikes"):
            ch = _LOOKALIKES.get(ch, ch)
        if _on("fold-width"):
            pieces = unicodedata.normalize("NFKD", ch)
        elif _on("fold-accents"):
            pieces = unicodedata.normalize("NFD", ch)
        else:
            pieces = ch
        for c in pieces:
            cat = unicodedata.category(c)
            if _on("fold-accents") and cat in ("Mn", "Me"):
                continue
            if _on("map-lookalikes"):
                c = _LOOKALIKES.get(c, c)
            if _on("map-dashes") and cat == "Pd":
                c = "-"
            if _on("map-spaces") and (cat in ("Zs", "Zl", "Zp") or c in "\t\n\r\x0b\x0c"):
                c = " "
            for d in c.casefold() if (fold and _on("fold-case")) else c:
                if d == " " and out and out[-1] == " ":
                    continue
                out.append(d)
    return "".join(out)


# --- Rules ----------------------------------------------------------------------


class _Uninspectable(Exception):
    pass


@dataclass
class _Rule:
    text: str
    regex: re.Pattern[str]
    case_sensitive: bool
    skeleton: str | None
    exceptions: list[re.Pattern[str]] = field(default_factory=list)
    any_case: re.Pattern[str] | None = None  # a case-sensitive term in any case, for text read in pictures


def _build_rule(text: str, case_sensitive: bool, whole_word: bool, exceptions: list[str]) -> _Rule:
    nterm = _vnorm(text, fold=not case_sensitive).strip()
    alnum = sum(c.isalnum() for c in nterm)
    words = [w for w in re.split(r"[\W_]+", nterm) if w]
    if not words:
        raise _Uninspectable(f"profile term {text!r} has no letters or digits")
    if _on("flex-separators"):
        letter_gap = r"[\W_]?" if alnum >= 4 else ""
        body = r"[\W_]{0,3}".join(letter_gap.join(re.escape(c) for c in w) for w in words)
    else:
        body = re.escape(nterm)
    if whole_word or case_sensitive or alnum < 5:
        left, right = r"(?<![^\W_])", r"(?![^\W_])"
        if case_sensitive and _on("camel-case") and nterm[:1].isupper():
            # Compound names such as "MyAGL" or "AGLConnect".
            left = rf"(?:{left}|(?<=[a-z]))"
            right = rf"(?:{right}|(?=[A-Z][a-z])|(?=s\b))"
        body = left + body + right
    skeleton = "".join(words)
    use_skeleton = not case_sensitive and not whole_word and not exceptions and len(skeleton) >= 5
    return _Rule(
        text=text,
        regex=re.compile(body),
        case_sensitive=case_sensitive,
        skeleton=skeleton if use_skeleton else None,
        exceptions=[re.compile(e) for e in exceptions],
        any_case=_build_rule(text, False, True, exceptions).regex if case_sensitive else None,
    )


def _glossary() -> set[str]:
    path = Path(__file__).with_name("data") / "technical-glossary.txt"
    words: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.add(_vnorm(line))
    return words


def load_rules(profile_path: Path) -> tuple[list[_Rule], set[str]]:
    """Read the profile independently of ``docsan.profile``."""
    try:
        data = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _Uninspectable(f"cannot read profile: {exc}") from exc
    allowed = {"format", "version", "name", "policy", "terms", "exceptions", "safe_words", "redact_system_names", "person_style",
               "logos"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise _Uninspectable("profile has unexpected structure")
    if data.get("format") != "docsan-profile" or data.get("version") != 1:
        raise _Uninspectable("profile format or version not recognised")
    terms = data.get("terms")
    if not isinstance(terms, list) or not terms:
        raise _Uninspectable("profile has no terms")
    by_text: dict[str, list[str]] = {}
    for e in data.get("exceptions", []):
        by_text.setdefault(str(e.get("text")), []).extend(str(p) for p in e.get("followed_by", []))
    redact_systems = data.get("redact_system_names", False)
    if not isinstance(redact_systems, bool):
        raise _Uninspectable("redact_system_names must be true or false")
    person_style = data.get("person_style", "first_name_initial")
    if person_style not in ("first_name_initial", "label"):
        raise _Uninspectable("person_style not recognised")
    rules: list[_Rule] = []
    for t in terms:
        if not isinstance(t, dict) or not isinstance(t.get("text"), str) or not t["text"].strip():
            raise _Uninspectable("profile term without text")
        if t.get("label") == "SYSTEM" and not redact_systems:
            continue  # system names are kept by policy
        allowed_after = list(by_text.get(t["text"].strip(), []))
        if t.get("label") == "PERSON" and person_style == "first_name_initial" and len(t["text"].split()) == 1:
            # A first name may stay when the family-name initial follows it: "Paul A."
            allowed_after.append(r"\s+[^\W\d_]\.(?![^\W_])")
        rules.append(
            _build_rule(
                t["text"].strip(),
                bool(t.get("case_sensitive", False)),
                bool(t.get("whole_word", False)),
                allowed_after,
            )
        )
    safe = {_vnorm(w) for w in data.get("safe_words", []) if isinstance(w, str)} | _glossary()
    if not redact_systems:
        safe |= {_vnorm(t["text"]) for t in data["terms"] if t.get("label") == "SYSTEM"}
    return rules, safe


# --- Fixed patterns ---------------------------------------------------------------

_CC = set(
    "ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo bq br bs bt bw "  # noqa: SIM905
    "by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg er es et eu fi "
    "fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in "
    "io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me "
    "mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu nz om pa pe pf pg "
    "ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sk sl sm sn so sr ss st su sv sx "
    "sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc ve vg vi vn vu wf ws ye yt "
    "za zm zw".split()
)
_GENERIC = set(
    "com net org info biz name pro mobi edu gov mil int arpa aero asia cat coop jobs museum tel travel "  # noqa: SIM905
    "app dev page cloud tech online site website store shop blog xyz top club live news email digital "
    "solutions services systems network group global agency company consulting media studio design software "
    "tools today world zone link click space host bank insurance legal law finance capital ventures partners "
    "holdings international europe brussels paris amsterdam berlin london swiss bayern wien zip mov local "
    "internal corp lan intra intranet home localdomain example test invalid localhost".split()
)
_AMBIGUOUS = set("py md sh rs pl cc so ai ps ml sc tf mk am in pm mo ac gd cr do es zip mov app".split())  # noqa: SIM905

_V_EMAIL = re.compile(r"[\w.+%\-]+@[\w\-]+(?:\.[\w\-]+)+")
_V_URL = re.compile(r"(?:https?|ftp|file|smb)://\S+|\bwww\.\S+|\\\\[\w.\-]+\\\S*")
_V_DOMAIN = re.compile(r"(?<![\w@./\\\-])\.?((?:[\w\-]+\.)+)([a-z]{2,63})(?![\w\-])")
_V_PHONE_INTL = re.compile(r"(?<![\w+])(?:\+|00)[\d\s().\-/]{7,}\d")
_V_PHONE_LOCAL = re.compile(r"(?<![\w.,+])\d{2,4}(?:[ .\-]\d{2,4}){3,5}(?!\w)")


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def _ipv4(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts)


def _numeric_example(view: str, start: int, end: int) -> bool:
    candidate = view[start:end].strip()
    # A day/month/year beside a plus-shaped icon is not an international phone.
    date = re.fullmatch(r"\+?\s*(\d{1,2})[/.-](\d{1,2})[/.-]((?:19|20)\d{2})", candidate)
    if date and 1 <= int(date[1]) <= 31 and 1 <= int(date[2]) <= 12:
        return True
    decimal_cells = re.fullmatch(r"\d+[.,]\d{2}(?:\s+\d+[.,]\d{2}){2,}", candidate)
    if decimal_cells and re.search(r"\b(?:unit price|prix unitaire|montant|subtotal|total|amount)\b", view):
        return True
    around = view[max(0, start-5):end+5]
    if re.search(r"[€$£]|\b(?:eur|usd|gbp)\b", around):
        return True
    groups = re.findall(r"\d+", view[start:end])
    if len(groups) < 3 or len({len(g) for g in groups}) != 1:
        return False
    sequence = list(map(int, groups))
    differences = {sequence[i] - sequence[i-1] for i in range(1, len(sequence))}
    return len(differences) == 1 and next(iter(differences)) > 0


_V_CLASSIFICATION = re.compile(r"(?<!\w)c[0-4][\s:-]*(?:internal|interne|confidential|confidentiel(?:le)?|restricted|restreinte?|secret)(?!\w)")

_V_IP = re.compile(r"(?<![a-z0-9_.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![a-z0-9_.])")
_V_HOST = re.compile(r"\b[a-z]{4,16}[padt][o0]?[0-9]{1,3}\b")

_V_TICKET_REFERENCE = re.compile(r"(?<!\w)#[ \u00a0]*[0-9](?:[ \u00a0]?[0-9]){6,14}(?!\w)")
_V_PUBLIC_SOFTWARE = re.compile(r"(?<!\w)postgres(?:ql)?(?!\w)", re.IGNORECASE)

_V_IDENTIFIER = re.compile(r"(?<![\w.,])(?:[a-z]{1,12}[-/]?[0-9]{5,}|[0-9]{4}/[0-9]{2}-[0-9]{3,}|[0-9]{8,})(?!\w|[.,]\d)")


def _source_host_names(texts: list[str], safe: set[str]) -> set[str]:
    """Derive source network facts independently, never from sanitizer hits."""
    names = set()
    for text in texts:
        text = _vnorm(text)
        for m in re.finditer(r"(?<![\w-])[a-z][a-z0-9_-]{3,62}(?![\w-])", text):
            token = m.group()
            letters = "".join(c for c in token if c.isalpha())
            if (len(letters) < 4 or not re.search(r"[0-9]", token) or letters in safe
                    or re.fullmatch(r"(?:odoo|postgres(?:ql)?|python|windows|http|rfc|iso|tls|sha|utf|ipv)[_-]?[0-9]+", token)):
                continue
            prefix = text[max(0,m.start()-90):m.start()]
            context = re.search(r"\b(?:servers?|serveurs?|host(?:name)?|hote|snapshots?|sftp|ssh|mft|agent|password|vms?|machines?|backup|db|database|production|prod)\b", prefix)
            prompt = re.search(r"\w[\w-]*@$", prefix) and re.match(r"[\]\s:#$/]|$", text[m.end():])
            if context or prompt:
                names.add(token)
    return names


_V_VARIABLES = tuple("system."+k for k in (
    "caller.id caller.project carriageReturn currentDate currentTime currentTimestamp docroot domain emptyString "
    "environment installDir java.vendor java.version job.error job.id job.log job.name job.workspace lineFeed "
    "os.arch os.name os.version project.location project.name systemName tab user.email"
).lower().split())


def _public_variable_fragment(token: str) -> bool:
    compact = re.sub(r"[^a-z0-9]","",token)
    if len(token)>=4 and "." in token and any(token in v for v in _V_VARIABLES):
        return True
    if len(compact)>=4 and "." in token and any(compact in re.sub(r"[^a-z0-9]","",v) for v in _V_VARIABLES):
        return True
    # Tiny OCR glyphs can confuse two letters in a complete public variable.
    # This runs only inside a typed System Variables column, never globally.
    if len(compact)<8 or "." not in token:
        return False
    for candidate in _V_VARIABLES:
        for value in (candidate,candidate.split(".",1)[1]):
            if token.rsplit(".",1)[-1] != value.rsplit(".",1)[-1]:
                continue
            public = re.sub(r"[^a-z0-9]","",value)
            if abs(len(compact)-len(public))>2:
                continue
            row=list(range(len(public)+1))
            for i,char in enumerate(compact,1):
                current=[i]
                for j,other in enumerate(public,1):
                    current.append(min(current[-1]+1,row[j]+1,row[j-1]+(char!=other)))
                row=current
            if row[-1]<=2:
                return True
    return False


def _code_domain(view: str, start: int, end: int, system_variables: bool, log_context: bool = False,
                 xml_code_context: bool = False) -> bool:
    if xml_code_context:
        # XML attribute delimiters and prose apostrophes are not SQL quotes.
        # Permit code syntax only within one emitted Word text element. Values
        # in attributes still receive the raw domain check.
        for part in re.finditer(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", view, re.DOTALL):
            a, b = part.span(1)
            if a <= start < end <= b:
                return _code_domain(view[a:b], start-a, end-a, system_variables, log_context)
        return False
    token = view[start:end].lstrip(".")
    if system_variables and (re.fullmatch(r"system\.[a-z_][\w.]*", token) or _public_variable_fragment(token)):
        return True
    quoted = [m.span() for m in re.finditer(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", view)]
    if re.fullmatch(r"(?:ir_cron|cron_log)\.[a-z_]\w*",token) and not any(a<=start<b for a,b in quoted):
        return True
    if ((log_context or re.search(r"\b(?:info|debug|warning|error|logger|traceback|starting|cron)\b", view))
            and re.fullmatch(r"(?:odoo\.)?(?:addons\.)?base\.models\.[a-z_][\w.]*", token)):
        return True
    if not re.search(r"\bselect\b", view):
        return False
    tables = {m.group(1) for m in re.finditer(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", view)}
    if token.split(".", 1)[0] not in tables or not re.fullmatch(r"[a-z_]\w*\.[a-z_]\w*", token):
        return False
    quoted = [m.span() for m in re.finditer(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", view)]
    return not any(a <= start < b for a,b in quoted)


def _pattern_findings(view: str, safe: set[str], phones: bool, *, system_variables: bool = False, log_context: bool = False, xml_code_context: bool = False) -> list[str]:
    found: list[str] = []
    if _on("emails"):
        found += [f"email {m.group()!r}" for m in _V_EMAIL.finditer(view) if "." in m.group().split("@")[-1]]
    if _on("urls"):
        found += [f"link {m.group()!r}" for m in _V_URL.finditer(view)]
    if _on("domains"):
        for m in _V_DOMAIN.finditer(view):
            name, tld = m.group(), m.group(2)
            if _code_domain(view, m.start(), m.end(), system_variables, log_context, xml_code_context):
                continue
            if name.lstrip(".") in safe or (tld not in _CC and tld not in _GENERIC):
                continue
            if tld in _AMBIGUOUS and name.lstrip(".").count(".") == 1:
                found.append(f"unclassified name {name!r}")
            else:
                found.append(f"domain {name!r}")
    if phones and _on("phones"):
        found += [f"phone {m.group()!r}" for m in _V_PHONE_INTL.finditer(view) if 8 <= _digits(m.group()) <= 15 and not _numeric_example(view, m.start(), m.end())]
        found += [
            f"phone {m.group()!r}"
            for m in _V_PHONE_LOCAL.finditer(view)
            if 8 <= _digits(m.group()) <= 12 and not _ipv4(m.group()) and not _numeric_example(view, m.start(), m.end())
        ]
    if _on("classification-labels"):
        found += [f"classification label {m.group()!r}" for m in _V_CLASSIFICATION.finditer(view)]
    if phones and _on("identifiers"):
        for m in _V_IP.finditer(view):
            if max(map(int,m.group().split(".")))<=255 and not re.search(r"(?:version|release|v)\s*$",view[max(0,m.start()-16):m.start()]):
                found.append(f"network address {m.group()!r}")
        for m in _V_HOST.finditer(view):
            if re.search(r"\b(?:servers?|serveurs?|hostname|host|hote|snapshots?|sftp|ssh|mft|agent|password|vms?|machines?|backup|db|database|production|prod)\b",view[max(0,m.start()-90):m.start()]):
                found.append(f"host identifier {m.group()!r}")
        found += [f"ticket reference {m.group()!r}" for m in _V_TICKET_REFERENCE.finditer(view)]
        found += [f"identifier {m.group()!r}" for m in _V_IDENTIFIER.finditer(view)
                  if not _numeric_example(view, m.start(), m.end())]
    return found


def _term_findings(segment: str, rules: list[_Rule], any_case: bool = False) -> list[str]:
    """With ``any_case`` (text read in pictures, where OCR gets letter case
    wrong) a case-sensitive term also counts in any case but all lower case."""
    folded = _vnorm(segment, fold=True)
    cased = _vnorm(segment, fold=False)
    skeleton = "".join(c for c in folded if c.isalnum()) if _on("skeleton") else ""
    found: list[str] = []
    for rule in rules:
        view = cased if rule.case_sensitive else folded
        for m in rule.regex.finditer(view):
            if any(a <= m.start() < m.end() <= b and (a, b) != m.span()
                   for a, b in (word.span() for word in _V_PUBLIC_SOFTWARE.finditer(view))):
                continue
            after = view[m.end() : m.end() + 60]
            if any(e.match(after) for e in rule.exceptions):
                continue
            found.append(f"term {rule.text!r} as {m.group()!r}")
            break
        else:
            if rule.skeleton and rule.skeleton in skeleton:
                found.append(f"term {rule.text!r} hidden by separators")
            elif any_case and rule.any_case is not None and _on("ocr-loose-case"):
                # The cased view keeps letter case at the same places as the folded one.
                upper = cased if len(cased) == len(folded) else None
                for m in rule.any_case.finditer(folded):
                    if upper is not None and upper[m.start() : m.end()] == m.group():
                        continue  # all lower case: the ordinary word
                    if any(e.match(folded[m.end() : m.end() + 60]) for e in rule.exceptions):
                        continue
                    found.append(f"term {rule.text!r} in other letter case")
                    break
    return found


# --- Views --------------------------------------------------------------------------

_BREAK_TAGS = re.compile(r"<(?:/w:(?:p|tc|tr)|w:(?:br|cr)\s*/)>")
_SPACE_TAGS = re.compile(r"<w:tab\s*/>")
_ANY_TAG = re.compile(r"<[^>]*>")
# A floating drawing's position is not text a reader sees; _ELEMENT_TEXT already
# restricts it to an integer or an alignment keyword. Left in the text view,
# neighbouring offsets join into long numbers that read as identifiers.
_POSITION_TEXT = re.compile(r"<wp:(posOffset|align)>[^<]*</wp:\1>")


def _decode(data: bytes, name: str) -> str:
    try:
        if data.startswith(b"\xef\xbb\xbf"):
            return data[3:].decode("utf-8")
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Uninspectable(f"{name}: cannot decode ({exc})") from exc


def _raw_view(text: str) -> str:
    view = html.unescape(text)
    for ident in KNOWN_IDENTIFIERS:
        view = view.replace(ident, " ")
    return view


def _joined_segments(text: str) -> list[str]:
    """Text with tags removed. Runs join up; paragraphs, cells and breaks separate."""
    text = _POSITION_TEXT.sub("", text)
    if not _on("join-runs"):
        text = _ANY_TAG.sub(" ", text)
        return [html.unescape(text)]
    text = _BREAK_TAGS.sub("\n", text)
    text = _SPACE_TAGS.sub(" ", text)
    text = _ANY_TAG.sub("", text)
    return [html.unescape(s) for s in text.split("\n")]


def _scan_part(name: str, text: str, rules: list[_Rule], safe: set[str]) -> list[str]:
    found: list[str] = []
    if _on("scan-raw"):
        raw = _raw_view(text)
        found += [f"{name} (raw): {f}" for f in _term_findings(raw, rules)]
        found += [f"{name} (raw): {f}" for f in _pattern_findings(_vnorm(raw), safe, phones=False, xml_code_context=True)]
    for seg in _joined_segments(text):
        if not seg.strip():
            continue
        found += [f"{name} (text): {f}" for f in _term_findings(seg, rules)]
        found += [f"{name} (text): {f}" for f in _pattern_findings(_vnorm(seg), safe, phones=True)]
    return found


def _scan_extracted(text: str, rules: list[_Rule], safe: set[str]) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        if line.strip():
            found += [f"extracted text: {f}" for f in _term_findings(line, rules)]
            found += [f"extracted text: {f}" for f in _pattern_findings(_vnorm(line), safe, phones=True)]
    return found


# --- Structure ----------------------------------------------------------------------


def _package_byte_problems(data: bytes) -> list[str]:
    """Every byte of the file must belong to a ZIP header or an entry's data."""
    problems: list[str] = []
    if not data.startswith(b"PK\x03\x04"):
        return ["file does not start with a ZIP entry"]
    if len(data) < 22 or data[-22:-18] != b"PK\x05\x06":
        return ["archive comment or trailing bytes after the ZIP directory"]
    _, _, _, _, count, cd_size, cd_offset, comment_len = struct.unpack("<IHHHHIIH", data[-22:])
    if comment_len or cd_offset + cd_size + 22 != len(data):
        problems.append("unexplained bytes around the ZIP directory")
    entries: list[tuple[int, int, int]] = []
    pos = cd_offset
    for _ in range(count):
        if data[pos : pos + 4] != b"PK\x01\x02":
            return problems + ["malformed ZIP directory"]
        fields = struct.unpack("<IHHHHHHIIIHHHHHII", data[pos : pos + 46])
        flags, csize, nlen, elen, clen, offset = fields[3], fields[8], fields[10], fields[11], fields[12], fields[16]
        if elen or clen:
            problems.append("extra data or comment on a ZIP entry")
        entries.append((offset, csize, flags))
        pos += 46 + nlen + elen + clen
    if pos != cd_offset + cd_size:
        problems.append("ZIP directory size mismatch")
    cursor = 0
    for offset, csize, flags in sorted(entries):
        if offset != cursor or data[offset : offset + 4] != b"PK\x03\x04":
            problems.append("unexplained bytes between ZIP entries")
            break
        nlen, elen = struct.unpack("<HH", data[offset + 26 : offset + 30])
        if elen or flags & 0x08:
            problems.append("unexpected local ZIP header fields")
        cursor = offset + 30 + nlen + elen + csize
    if entries and cursor != cd_offset:
        problems.append("unexplained bytes before the ZIP directory")
    return problems


def _parse(text: str, name: str) -> ET.Element | None:
    # Entity attacks (external entities, billion laughs) need a DTD. Refusing
    # any DTD before parsing keeps the standard-library parser safe here.
    if "<!DOCTYPE" in text.upper():
        return None
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise _Uninspectable(f"{name}: malformed XML ({exc})") from exc


def _allowed_value(rule: object, value: str) -> bool:
    if rule == _FONT:
        return value in _fonts()
    if rule == _ANY_TEXT:
        return len(value) <= 64
    if isinstance(rule, frozenset):
        return value in rule
    return bool(rule.fullmatch(value))  # type: ignore[union-attr]


def _element_problems(text: str, name: str, root_tag: str) -> list[str]:
    """Only the elements and attribute values the writer emits, with text only in w:t."""
    root = _parse(text, name)
    if root is None:
        return [f"{name} declares a DTD"]
    problems: list[str] = []
    if root.tag != _W + root_tag:
        problems.append(f"unexpected root element {root.tag}")
    for el in root.iter():
        spec = ALLOWED_ELEMENTS.get(el.tag)
        if spec is None:
            problems.append(f"unexpected element {el.tag}")
            continue
        for attr, value in el.attrib.items():
            rule = spec.get(attr)
            if rule is None:
                problems.append(f"unexpected attribute {attr} on {el.tag}")
            elif not _allowed_value(rule, value):
                problems.append(f"unexpected value {value!r} for {attr} on {el.tag}")
        if el.tag in _ELEMENT_TEXT:
            if not _allowed_value(_ELEMENT_TEXT[el.tag], (el.text or "").strip()):
                problems.append(f"unexpected position {el.text!r} in {el.tag}")
        elif el.tag != _W + "t" and el.text and el.text.strip():
            problems.append(f"text outside a text element in {el.tag}")
        if el.tail and el.tail.strip():
            problems.append(f"text between elements after {el.tag}")
    return problems


def _expected_content_types(parts: list[tuple[str, int]]) -> str:
    ct = "application/vnd.openxmlformats-officedocument.wordprocessingml."
    overrides = [("document.xml", "document.main+xml"), ("styles.xml", "styles+xml"), ("numbering.xml", "numbering+xml"),
                 ("settings.xml", "settings+xml")]
    overrides += [(f"{kind}{n}.xml", f"{kind}+xml") for kind, n in parts]
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        + "".join(f'<Override PartName="/word/{n}" ContentType="{ct}{k}"/>' for n, k in overrides)
        + "</Types>"
    )


_NEUTRAL_STYLE = re.compile(r'<w:style w:type="paragraph" w:styleId="Style([1-9][0-9]{0,3})"><w:name w:val="Style \1"/></w:style>')
_SETTINGS = re.compile(
    r'<\?xml version="1\.0" encoding="UTF-8" standalone="yes"\?>\n'
    r'<w:settings xmlns:w="http://schemas\.openxmlformats\.org/wordprocessingml/2006/main">'
    r'(?:<w:mirrorMargins/>)?<w:defaultTabStop w:val="[1-9][0-9]{0,4}"/>(?:<w:autoHyphenation/>)?(?:<w:evenAndOddHeaders/>)?'
    r'<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas\.microsoft\.com/office/word" w:val="1[1245]"/>'
    r"</w:compat></w:settings>"
)


def _fixed_part_problems(entries: dict[str, bytes], parts: list[tuple[str, int]]) -> list[str]:
    """Parts whose content the writer fixes: package relationships, content
    types, styles (a fixed start plus Style1..StyleN) and settings."""
    problems: list[str] = []
    for name, pin in PINNED_PARTS.items():
        if name in entries and hashlib.sha256(entries[name]).hexdigest() != pin:
            problems.append(f"{name} differs from the fixed part")
    if "[Content_Types].xml" in entries and entries["[Content_Types].xml"].decode("utf-8", "replace") != _expected_content_types(parts):
        problems.append("[Content_Types].xml differs from the parts in the package")
    styles = entries.get("word/styles.xml", b"").decode("utf-8", "replace")
    first = styles.find('<w:style w:type="paragraph" w:styleId="Style')
    head = styles[:first] if first >= 0 else styles.removesuffix("</w:styles>")
    rest = styles[len(head):].removesuffix("</w:styles>")
    count = len(_NEUTRAL_STYLE.findall(rest))
    expected = "".join(f'<w:style w:type="paragraph" w:styleId="Style{i}"><w:name w:val="Style {i}"/></w:style>' for i in range(1, count + 1))
    if not styles.endswith("</w:styles>") or hashlib.sha256(head.encode()).hexdigest() != STYLES_HEAD_SHA256 or rest != expected:
        problems.append("word/styles.xml differs from the fixed styles")
    if "word/settings.xml" in entries and not _SETTINGS.fullmatch(entries["word/settings.xml"].decode("utf-8", "replace")):
        problems.append("word/settings.xml differs from the settings the writer emits")
    return problems


_FIXED_RELATIONSHIPS = {
    ("rId1", _REL + "styles", "styles.xml"),
    ("rId2", _REL + "numbering", "numbering.xml"),
    ("rId3", _REL + "settings", "settings.xml"),
}
_PART_REL_ID = re.compile(r"rId(Hdr|Ftr)([1-9][0-9]{0,2})")


def _relationships(entries: dict[str, bytes], name: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    text = _decode(entries.get(name, b""), name)
    root = _parse(text, name)
    if root is None:
        return [], [f"{name} declares a DTD"]
    rels: list[tuple[str, str, str]] = []
    problems: list[str] = []
    for el in root:
        if el.tag != "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship" or set(el.attrib) != {"Id", "Type", "Target"}:
            problems.append(f"unexpected relationship {el.tag} {sorted(el.attrib)}")
            continue
        rels.append((el.get("Id", ""), el.get("Type", ""), el.get("Target", "")))
    if len({r[0] for r in rels}) != len(rels):
        problems.append("duplicate relationships")
    return rels, problems


def _relationship_problems(entries: dict[str, bytes], media: dict[int, str], parts: dict[int, str]) -> list[str]:
    """The document's relationships are styles, numbering, settings, one per
    header and footer, and its pictures; a header's or footer's are only its
    pictures. Every media part has exactly one relationship, to itself."""
    problems: list[str] = []
    images: Counter[int] = Counter()

    def image(rel: tuple[str, str, str]) -> bool:
        m = _IMAGE_REL_ID.fullmatch(rel[0])
        if m and rel[1] == _IMAGE_REL and int(m.group(1)) in media and rel[2] == media[int(m.group(1))]:
            images[int(m.group(1))] += 1
            return True
        return False

    rels, found = _relationships(entries, "word/_rels/document.xml.rels")
    problems += found
    fixed: set[tuple[str, str, str]] = set()
    linked: set[int] = set()
    for rel in rels:
        m = _PART_REL_ID.fullmatch(rel[0])
        if rel in _FIXED_RELATIONSHIPS:
            fixed.add(rel)
        elif m:
            kind, n = ("header" if m.group(1) == "Hdr" else "footer"), int(m.group(2))
            if parts.get(n) != kind or rel[1] != _REL + kind or rel[2] != f"{kind}{n}.xml":
                problems.append(f"unexpected relationship {rel[0]!r} to {rel[2]!r}")
            linked.add(n)
        elif not image(rel):
            problems.append(f"unexpected relationship {rel[0]!r} to {rel[2]!r}")
    if fixed != _FIXED_RELATIONSHIPS:
        problems.append("styles, numbering or settings relationship missing")
    problems += [f"{parts[n]}{n}.xml has no relationship" for n in sorted(set(parts) - linked)]
    for n, kind in sorted(parts.items()):
        name = f"word/_rels/{kind}{n}.xml.rels"
        if name in entries:
            rels, found = _relationships(entries, name)
            problems += [f"{name}: {p}" for p in found]
            problems += [f"{name}: unexpected relationship {rel[0]!r} to {rel[2]!r}" for rel in rels if not image(rel)]
    for n in sorted(media):
        if images[n] != 1:
            problems.append(f"media part image{n} has {'no relationship' if not images[n] else 'more than one relationship'}")
    return problems


def _code_regions(path: Path) -> list[list[int]]:
    proc = subprocess.run([_vision_helper()], input=json.dumps({"command": "regions", "input": str(path)}).encode(),
                          capture_output=True, timeout=120, check=False)
    observed = json.loads(proc.stdout)
    if proc.returncode or not observed.get("ok"):
        raise _Uninspectable("cannot inspect image code regions")
    return observed["barcodes"] + observed["code_candidates"]


def _media_checks(name: str, data: bytes, rules: list[_Rule], safe: set[str], workdir: Path) -> tuple[list[str], list[list[int]]]:
    """A picture inside the document gets the same checks as a released image."""
    findings: list[str] = []
    kind = "png" if data.startswith(_PNG_SIGNATURE) else "jpeg" if data.startswith(b"\xff\xd8") else None
    if kind is None or kind != ("png" if name.endswith(".png") else "jpeg"):
        return [f"{name}: not a {name.rsplit('.', 1)[-1].upper()} image"], []
    picture, structure = (_png_structure if kind == "png" else _jpeg_structure)(data)
    if _on("image-structure"):
        findings += [f"{name}: {p}" for p in structure]
    if _on("transparent-pixels"):
        findings += [f"{name}: {p}" for p in _transparent_problems(picture)]
    regions: list[list[int]] = []
    path = workdir / name.replace("/", "_")
    path.write_bytes(data)
    if _on("image-codes"):
        regions += _code_regions(path)
        if regions:
            findings.append(f"{name}: machine-readable or code-like region remains")
    target, expected, factor = _zoomed(path, (picture.width,picture.height), workdir) if any(_on(s) for s,_ in _LAYOUTS) else (path,(picture.width,picture.height),1.0)
    for step, layout in _LAYOUTS:
        if not _on(step):
            continue
        size, lines = image_ocr(target, layout)
        if size != expected:
            raise _Uninspectable(f"{name}: OCR read a {size[0]}x{size[1]} image, expected {expected[0]}x{expected[1]}")
        found, where = _scan_lines(lines, rules, safe, f"{name}, OCR layout {layout}", picture, factor)
        findings += found
        regions += [[round(v/factor) for v in box] for box in where]
    return findings, regions


# --- Second extractor ------------------------------------------------------------------


def extract_with_textutil(path: Path) -> str:
    try:
        proc = subprocess.run(
            ["/usr/bin/textutil", "-convert", "txt", "-encoding", "UTF-8", "-stdout", str(path)],
            capture_output=True,
            timeout=120,
            env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"},
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Uninspectable(f"second extractor failed: {exc}") from exc
    if proc.returncode != 0:
        raise _Uninspectable(f"second extractor failed: {proc.stderr.decode(errors='replace').strip()}")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Uninspectable(f"second extractor output not UTF-8: {exc}") from exc


# The extractor can be swapped by tests; production uses textutil.
extractor = extract_with_textutil


# --- Entry point -------------------------------------------------------------------------


@dataclass
class VerifyResult:
    status: str  # pass, findings or could_not_inspect
    sha256: str
    findings: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    regions: list[list[int]] = field(default_factory=list)  # where OCR found something, for images
    media_regions: dict[str, list[list[int]]] = field(default_factory=dict)  # the same per picture in a document

    @property
    def passed(self) -> bool:
        return self.status == "pass"


def _read_entries(data: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [i.filename for i in zf.infolist()]
            if len(names) != len(set(names)):
                raise _Uninspectable("duplicate ZIP entries")
            return {n: zf.read(n) for n in names}
    except (zipfile.BadZipFile, OSError, EOFError, RuntimeError) as exc:
        raise _Uninspectable(f"cannot open package: {exc}") from exc


def _write_entries(entries: dict[str, bytes], path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def _scan_entries(entries: dict[str, bytes], rules: list[_Rule], safe: set[str]) -> list[str]:
    found: list[str] = []
    for name, data in entries.items():
        if not MEDIA_PART.fullmatch(name):  # pictures are checked as images
            found += _scan_part(name, _decode(data, name), rules, safe)
    return found


def _self_test(entries: dict[str, bytes], workdir: Path) -> list[str]:
    """Plant a random name in a copy; every view must find it."""
    canary = "zq" + "".join(secrets.choice("bcdfghjkmnpqrstvwxz") for _ in range(10))
    rule = _build_rule(canary, False, False, [])
    planted: dict[str, bytes] = {}
    for name, data in entries.items():
        if MEDIA_PART.fullmatch(name):
            planted[name] = data
            continue
        text = _decode(data, name)
        decl_end = text.find("?>") + 2 if text.startswith("<?xml") else 0
        text = f"{text[:decl_end]}<!--{canary}-->{text[decl_end:]}"
        if name == "word/document.xml":
            para = f"<w:p><w:r><w:t>{canary[:5]}</w:t></w:r><w:r><w:t>{canary[5:]}</w:t></w:r></w:p>"
            at = text.rfind("<w:sectPr")
            if at < 0:
                at = text.rfind("</w:body>")
            if at < 0:
                return ["self-test could not plant a test name in the document"]
            text = text[:at] + para + text[at:]
        planted[name] = text.encode("utf-8")
    missing: list[str] = []
    for name, data in planted.items():
        if MEDIA_PART.fullmatch(name):
            continue
        found = _scan_part(name, _decode(data, name), [rule], set())
        if not any(f.startswith(f"{name} (raw)") for f in found):
            missing.append(f"{name} raw view")
        if name == "word/document.xml" and not any(f.startswith(f"{name} (text)") for f in found):
            missing.append("document text view")
    if _on("second-extractor"):
        copy = workdir / "self-test.docx"
        _write_entries(planted, copy)
        if not _scan_extracted(extractor(copy), [rule], set()):
            missing.append("second extractor")
    return [f"self-test missed the planted name in: {', '.join(missing)}"] if missing else []


def verify_docx(path: Path, profile_path: Path, *, source_texts: list[str] = ()) -> VerifyResult:
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    findings: list[str] = []
    media_regions: dict[str, list[list[int]]] = {}
    try:
        rules, safe = load_rules(profile_path)
        if _on("identifiers"):
            rules += [_build_rule(name, False, True, []) for name in _source_host_names(source_texts, safe)]
        if _on("package-bytes"):
            findings += [f"package: {p}" for p in _package_byte_problems(data)]
        entries = _read_entries(data)
        media = {int(m.group(1)): f"media/image{m.group(1)}.{m.group(2)}" for n in entries if (m := MEDIA_PART.fullmatch(n))}
        parts = {int(m.group(2)): m.group(1) for n in entries if (m := HEADER_FOOTER_PART.fullmatch(n))}
        if _on("part-allowlist"):
            allowed = set(EXPECTED_PARTS) | {f"word/{k}{n}.xml" for n, k in parts.items()} | {f"word/_rels/{k}{n}.xml.rels" for n, k in parts.items()}
            names = {n for n in entries if not MEDIA_PART.fullmatch(n)}
            findings += [f"package: unexpected part {n}" for n in sorted(names - allowed)]
            findings += [f"package: missing part {n}" for n in sorted(EXPECTED_PARTS - names)]
        if _on("relationships"):
            findings += [f"word/_rels: {p}" for p in _relationship_problems(entries, media, parts)]
            findings += [f"package: {p}" for p in _fixed_part_problems(entries, [(k, n) for n, k in sorted(parts.items())])]
        if _on("element-allowlist"):
            roots = {**_ROOTS, **{f"word/{k}{n}.xml": ("hdr" if k == "header" else "ftr") for n, k in parts.items()}}
            for name, root_tag in sorted(roots.items()):
                if name in entries:
                    findings += [f"{name}: {p}" for p in _element_problems(_decode(entries[name], name), name, root_tag)]
        findings += _scan_entries(entries, rules, safe)
        with tempfile.TemporaryDirectory(prefix="docsan-verify-") as tmp:
            for name in sorted(n for n in entries if MEDIA_PART.fullmatch(n)):
                found, where = _media_checks(name, entries[name], rules, safe, Path(tmp))
                findings += found
                if where:
                    media_regions[name] = where
            if _on("second-extractor"):
                findings += _scan_extracted(extractor(path), rules, safe)
            problems = _self_test(entries, Path(tmp)) if _on("self-test") else []
            media_names = sorted(n for n in entries if MEDIA_PART.fullmatch(n))
            if media_names and _on("image-self-test"):
                first = entries[media_names[0]]
                kind = "png" if first.startswith(_PNG_SIGNATURE) else "jpeg"
                layouts = [layout for step, layout in _LAYOUTS if _on(step)]
                problems += _image_self_test(first, kind, Path(tmp), layouts)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append("file changed during verification")
    except _Uninspectable as exc:
        return VerifyResult("could_not_inspect", digest, findings, [str(exc)], media_regions=media_regions)
    except Exception as exc:  # noqa: BLE001 - any unexpected failure is an incomplete inspection
        return VerifyResult("could_not_inspect", digest, findings, [f"verifier error: {exc!r}"], media_regions=media_regions)
    if problems:
        return VerifyResult("could_not_inspect", digest, findings, problems, media_regions=media_regions)
    return VerifyResult("findings" if findings else "pass", digest, findings, [], media_regions=media_regions)


# --- Images ----------------------------------------------------------------------------
#
# A released image holds pixels and nothing else. PNG: a header, unfiltered
# 8-bit RGB or RGBA rows, the end marker. JPEG: tables, one frame, scans and
# the end marker, with no application segments or comments. Text is read
# with Tesseract's sparse-text layout, which in tests found every name its
# automatic layout found and more, and every line goes through the same term
# and pattern rules as document text.

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_ALLOWED = frozenset({0xC0, 0xC1, 0xC2, 0xC4, 0xDB, 0xDD})


@dataclass
class _Picture:
    width: int
    height: int
    rgba: bytes | None = None  # pixel rows of a PNG with transparency
    channels: int = 0  # colour components of the decoded picture
    pixels: bytes | None = None  # independently decoded PNG pixels, without row markers


def _png_structure(data: bytes) -> tuple[_Picture, list[str]]:
    problems: list[str] = []
    chunks: list[tuple[bytes, bytes]] = []
    pos = len(_PNG_SIGNATURE)
    while pos < len(data):
        if pos + 12 > len(data):
            problems.append("truncated PNG chunk")
            break
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        end = pos + 12 + length
        if end > len(data):
            problems.append("truncated PNG chunk")
            break
        body = data[pos + 8 : pos + 8 + length]
        if zlib.crc32(kind + body) != struct.unpack(">I", data[end - 4 : end])[0]:
            problems.append(f"PNG chunk {kind!r} has a bad checksum")
        chunks.append((kind, body))
        pos = end
        if kind == b"IEND":
            break
    if pos != len(data):
        problems.append("bytes after the end of the PNG")
    kinds = [k for k, _ in chunks]
    for k in sorted(set(kinds) - {b"IHDR", b"IDAT", b"IEND"}):
        problems.append(f"unexpected PNG chunk {k.decode('latin-1')!r}")
    core = [k for k in kinds if k in (b"IHDR", b"IDAT", b"IEND")]
    if len(core) < 3 or core[0] != b"IHDR" or core[-1] != b"IEND" or set(core[1:-1]) != {b"IDAT"}:
        raise _Uninspectable("PNG chunks missing or out of order")
    if kinds.index(b"IEND") - kinds.index(b"IHDR") != len(core) - 1 or len(chunks[0][1]) != 13:
        problems.append("PNG chunks interleaved with others")
    width, height, depth, colour, method, filtering, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if depth != 8 or colour not in (2, 6) or method or filtering or interlace:
        problems.append("PNG is not 8-bit, non-interlaced RGB or RGBA")
        return _Picture(width, height), problems
    channels = 4 if colour == 6 else 3
    stream = zlib.decompressobj()
    try:
        rows = stream.decompress(b"".join(b for k, b in chunks if k == b"IDAT"), (1 + width * channels) * height + 1)
    except zlib.error as exc:
        raise _Uninspectable(f"cannot decompress the PNG pixels: {exc}") from exc
    stride = 1 + width * channels
    if len(rows) != stride * height or not stream.eof or stream.unused_data:
        problems.append("PNG pixel data does not match its size")
        return _Picture(width, height), problems
    if rows[::stride].count(0) != height:
        problems.append("PNG rows use filters the writer never emits")
        return _Picture(width, height), problems
    pixels = b"".join(rows[y * stride + 1 : (y + 1) * stride] for y in range(height))
    return _Picture(width, height, pixels if channels == 4 else None, channels, pixels), problems


def _jpeg_structure(data: bytes) -> tuple[_Picture, list[str]]:
    problems: list[str] = []
    width = height = frames = channels = 0
    i, n, ended = 2, len(data), False
    while i < n:
        if data[i] != 0xFF or i + 1 >= n:
            raise _Uninspectable("malformed JPEG")
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker == 0xD9:
            ended, i = True, i + 2
            break
        if i + 4 > n:
            raise _Uninspectable("truncated JPEG")
        end = i + 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
        if end > n:
            raise _Uninspectable("truncated JPEG")
        if marker in (0xC0, 0xC1, 0xC2):
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            frames += 1
            channels = data[i + 9]
            if data[i + 9] not in (1, 3):
                problems.append("JPEG frame has an unexpected number of colour components")
        elif marker == 0xDA:
            j = end
            while True:
                j = data.find(b"\xff", j)
                if j < 0 or j + 1 >= n:
                    raise _Uninspectable("truncated JPEG scan")
                if data[j + 1] == 0x00 or 0xD0 <= data[j + 1] <= 0xD7:
                    j += 2
                    continue
                break
            i = j
            continue
        elif marker not in _JPEG_ALLOWED:
            what = (
                "application data such as EXIF, XMP or a colour profile"
                if 0xE0 <= marker <= 0xEF
                else "a comment" if marker == 0xFE else f"marker {marker:#04x}"
            )
            problems.append(f"unexpected JPEG segment: {what}")
        i = end
    if not ended:
        problems.append("JPEG has no end marker")
    elif i != n:
        problems.append("bytes after the end of the JPEG")
    if frames != 1 or not width or not height:
        problems.append("JPEG must hold exactly one frame")
    return _Picture(width, height, channels=channels), problems


def _transparent_problems(picture: _Picture) -> list[str]:
    """Fully transparent pixels must hold no colour: it would be invisible but recoverable."""
    if picture.rgba is None:
        return []
    hidden = picture.rgba[3::4].count(0)
    if not hidden:
        return []
    cells = array.array("I")
    if cells.itemsize != 4:
        raise _Uninspectable("unexpected machine word size")
    cells.frombytes(picture.rgba)
    cleared = cells.count(0)
    return [f"{hidden - cleared} fully transparent pixels still hold colour"] if cleared != hidden else []


# --- Tesseract -----------------------------------------------------------------------------

_BUNDLE = Path(__file__).resolve().parents[2]  # <engine>/site/docsan/verify.py


@dataclass
class OcrLine:
    text: str
    box: list[int]  # x, y, width, height


@functools.cache
def _tesseract() -> tuple[str, str, dict[str, str]]:
    found = os.environ.get("DOCSAN_TESSERACT")
    if not found:
        for candidate in (_BUNDLE / "bin" / "tesseract", Path("/opt/homebrew/bin/tesseract"), Path("/usr/local/bin/tesseract")):
            if os.access(candidate, os.X_OK):
                found = str(candidate)
                break
    if not found:
        raise _Uninspectable("Tesseract is not installed, so images cannot be checked")
    env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "OMP_THREAD_LIMIT": "1"}
    for key in ("HOME", "TMPDIR"):  # the app container's, inside the sandbox
        if os.environ.get(key):
            env[key] = os.environ[key]
    data = os.environ.get("DOCSAN_TESSDATA") or (str(_BUNDLE / "tessdata") if (_BUNDLE / "tessdata").is_dir() else "")
    if data:
        env["TESSDATA_PREFIX"] = data
    try:
        listed = subprocess.run([found, "--list-langs"], capture_output=True, timeout=60, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Uninspectable(f"Tesseract failed: {exc}") from exc
    langs = set(listed.stdout.decode(errors="replace").split())
    if "eng" not in langs:
        raise _Uninspectable("Tesseract has no English language data")
    return found, "eng+fra" if "fra" in langs else "eng", env


def read_with_tesseract(path: Path, layout: int) -> tuple[tuple[int, int], list[OcrLine]]:
    """The image size Tesseract read, and its lines of text with their boxes."""
    tesseract, langs, env = _tesseract()
    try:
        proc = subprocess.run(
            # Settings rather than the "tsv" config file, which the bundle does not ship.
            [tesseract, str(path), "stdout", "-l", langs, "--psm", str(layout),
             "-c", "tessedit_create_tsv=1", "-c", "tessedit_create_txt=0"],
            capture_output=True,
            timeout=900,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Uninspectable(f"OCR failed: {exc}") from exc
    if proc.returncode != 0:
        raise _Uninspectable(f"OCR failed: {proc.stderr.decode(errors='replace').strip()[:200]}")
    size: tuple[int, int] | None = None
    words: dict[tuple[str, ...], list[tuple[str, list[int]]]] = {}
    for row in proc.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or not cols[0].isdigit():
            continue
        box = [int(v) for v in cols[6:10]]
        if cols[0] == "1":
            size = (box[2], box[3])
        elif cols[0] == "5" and cols[11].strip():
            words.setdefault(tuple(cols[1:5]), []).append((cols[11], box))
    if size is None:
        raise _Uninspectable("OCR did not report the image size")
    lines = []
    for group in words.values():
        x0 = min(b[0] for _, b in group)
        y0 = min(b[1] for _, b in group)
        x1 = max(b[0] + b[2] for _, b in group)
        y1 = max(b[1] + b[3] for _, b in group)
        lines.append(OcrLine(" ".join(t for t, _ in group), [x0, y0, x1 - x0, y1 - y0]))
    return size, lines


# The OCR reader can be swapped by tests; production uses Tesseract.
image_ocr = read_with_tesseract
_LAYOUTS = (("ocr-text", 11),)  # Tesseract page segmentation mode 11: sparse text


def _flat_ocr_artifact(line: OcrLine, picture: _Picture | None, factor: float) -> bool:
    """A one/two-character OCR guess over a tiny, flat patch has no glyph ink.

    Check original PNG pixels, including a one-pixel border. Missing pixels,
    transparency, larger regions and even faint real strokes retain the finding.
    """
    if picture is None or picture.pixels is None or len(line.text.strip()) > 2 or factor <= 0:
        return False
    x, y, width, height = [v / factor for v in line.box]
    if width <= 0 or height <= 0 or width > 4 or height > 4:
        return False
    x0, y0 = math.floor(x) - 1, math.floor(y) - 1
    x1, y1 = math.ceil(x + width) + 1, math.ceil(y + height) + 1
    if x0 < 0 or y0 < 0 or x1 > picture.width or y1 > picture.height:
        return False
    values = [[], [], []]
    for py in range(y0, y1):
        for px in range(x0, x1):
            offset = (py * picture.width + px) * picture.channels
            pixel = picture.pixels[offset:offset + picture.channels]
            if len(pixel) != picture.channels or picture.channels not in (3, 4) or (picture.channels == 4 and pixel[3] != 255):
                return False
            for channel in range(3):
                values[channel].append(pixel[channel])
    return all(max(channel) - min(channel) <= 1 for channel in values)


def _ocr_rows(lines: list[OcrLine]) -> list[OcrLine]:
    """Rejoin nearby pieces of one physical row split into OCR blocks."""
    rows=[]
    for line in sorted(lines,key=lambda l:(l.box[0],l.box[1])):
        x,y,w,h=line.box
        candidates=[r for r in rows if abs(r.box[1]+r.box[3]/2-y-h/2)<max(h,r.box[3])*.45
                    and 0<=x-r.box[0]-r.box[2]<3*max(h,r.box[3])]
        if not candidates:
            rows.append(OcrLine(line.text,list(line.box)));continue
        previous=min(candidates,key=lambda r:x-r.box[0]-r.box[2])
        gap=x-previous.box[0]-previous.box[2]
        join="" if (previous.text.endswith(".") or line.text.startswith(".")
                    or ("." in previous.text and "." in line.text and gap<max(h,previous.box[3]))) else " "
        previous.text+=join+line.text
        left,top=previous.box[:2];bottom=max(top+previous.box[3],y+h)
        previous.box=[left,min(top,y),x+w-left,bottom-min(top,y)]
    return rows


def _scan_lines(lines: list[OcrLine], rules: list[_Rule], safe: set[str], view: str,
                picture: _Picture | None = None, factor: float = 1.0) -> tuple[list[str], list[list[int]]]:
    findings: list[str] = []
    regions: list[list[int]] = []
    original_lines = lines
    lines = _ocr_rows(lines)
    log_context = sum(bool(re.search(r"\b(?:info|debug|warning|error)\b",l.text,re.IGNORECASE)) for l in lines)>=3
    system_headers = []
    for line in original_lines+lines:
        value = _vnorm(line.text)
        if re.sub(r"[^a-z]","",value)=="systemvariables":
            system_headers.append(line.box)
        for match in re.finditer(r"\bsystem\s*variables\b",value):
            x,y,w,h = line.box
            system_headers.append([x+w*match.start()/len(value),y,w*(match.end()-match.start())/len(value),h])
    transports = [l.box for l in lines if re.search(r"\bsftp\b", l.text, re.IGNORECASE)]
    session_bands=[]
    if picture is not None:
        height=picture.height*factor
        menu=[l for l in lines if l.box[1]<height*.08 and re.search(r"\b(?:sessions|tools|settings|macros)\b",l.text,re.IGNORECASE)]
        command=[l for l in lines if l.box[1]<height*.15 and re.search(r"\b(?:detach tab|set connection password|disconnect|fullscreen)\b",l.text,re.IGNORECASE)]
        known_menu=set(re.findall(r"\b(?:sessions|tools|settings|macros)\b"," ".join(l.text.lower() for l in menu)))
        known_command=set(re.findall(r"\b(?:detach tab|set connection password|disconnect|fullscreen)\b"," ".join(l.text.lower() for l in command)))
        if (len(known_menu)>=2 and len(known_command)>=2) or (known_menu and len(known_command)>=3):
            top=min(l.box[1] for l in menu); bottom=max(l.box[1]+l.box[3] for l in menu); toolbar=min(l.box[1] for l in command)
            if top<bottom<toolbar:
                session_bands=[(0,top-2*factor),(bottom+2*factor,toolbar-2*factor)]
    for line in lines:
        if _flat_ocr_artifact(line, picture, factor):
            continue
        x,y,_,h = line.box
        variables = any(hy+hh <= y <= hy+120*hh and hx-2*hh <= x <= hx+max(hw,12*hh)
                        for hx,hy,hw,hh in system_headers)
        found = _term_findings(line.text, rules, any_case=True) + _pattern_findings(
            _vnorm(line.text), safe, phones=True, system_variables=variables, log_context=log_context)
        candidates = re.findall(r"\b[a-z][a-z0-9_-]{4,}[0-9]{1,3}\b", line.text.lower())
        if candidates and picture is not None and y >= picture.height*factor*.85:
            aligned = [b for b in transports if 0 < y-b[1] < 24*max(h,b[3]) and -2*b[3] <= x-b[0] <= 7*b[3]]
            if len(aligned) >= 2 and any(not re.fullmatch(r"(?:version|release|postgres(?:ql)?|python|odoo)\d+", t) for t in candidates):
                found.append("cropped transport-field identifier remains")
        if line.text.strip() and any(y>=a and y+h<=b for a,b in session_bands):
            found.append("remote-session identity remains")
        if found:
            findings += [f"image text ({view}): {f}" for f in found]
            regions.append(line.box)
    return findings, regions


# --- Image self-test ----------------------------------------------------------------------------

# Letters Tesseract reads reliably on their own. L (a phantom letter after
# its foot) and F (read as E) are left out.
_CANARY_LETTERS = "BCDGHKMNPRTVWXZ"


@functools.cache
def _glyphs() -> tuple[int, dict[str, tuple[int, bytes]]]:
    """Letters rendered once in Helvetica, so the test name needs no font engine."""
    data = json.loads((Path(__file__).with_name("data") / "canary-glyphs.json").read_text(encoding="utf-8"))
    letters = {k: (v["width"], zlib.decompress(base64.b64decode(v["pixels"]))) for k, v in data["letters"].items()}
    return int(data["height"]), letters


def _gray_png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return _PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + r for r in rows))) + chunk(b"IEND", b"")


def _canary_png(text: str) -> bytes:
    height, letters = _glyphs()
    margin, gap = 24, 4
    width = 2 * margin + sum(letters[c][0] for c in text) + gap * (len(text) - 1)
    rows = [b"\xff" * width] * margin
    for y in range(height):
        row = bytearray(b"\xff" * margin)
        for k, c in enumerate(text):
            w, pixels = letters[c]
            row += pixels[y * w : (y + 1) * w]
            if k < len(text) - 1:
                row += b"\xff" * gap
        row += b"\xff" * margin
        rows.append(bytes(row))
    rows += [b"\xff" * width] * margin
    return _gray_png(width, len(rows), rows)


def _planted_metadata(data: bytes, kind: str, canary: str) -> bytes:
    if kind == "png":
        at = data.rfind(b"IEND") - 4
        body = b"Comment\x00" + canary.encode()
        chunk = struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body))
        return data[:at] + chunk + data[at:]
    body = canary.encode()
    return data[:2] + b"\xff\xfe" + struct.pack(">H", len(body) + 2) + body + data[2:]


def _canary() -> tuple[str, _Rule]:
    text = "".join(secrets.choice(_CANARY_LETTERS) for _ in range(9))
    return text, _build_rule(text, False, False, [])


def _image_self_test(data: bytes, kind: str, workdir: Path, layouts: list[int]) -> list[str]:
    """Plant a random name in each view; every view must report it. OCR gets
    a second, fresh name when it misreads the first, so a rare misreading
    does not block a file, while a broken reader still misses both."""
    missing: list[str] = []
    if _on("image-structure"):
        canary, _ = _canary()
        structure = _png_structure if kind == "png" else _jpeg_structure
        if not structure(_planted_metadata(data, kind, canary))[1]:
            missing.append("file structure")
    path = workdir / "self-test.png"
    for layout in layouts:
        for _attempt in range(2):
            canary, rule = _canary()
            path.write_bytes(_canary_png(canary))
            _, lines = image_ocr(path, layout)
            if any(_term_findings(line.text, [rule]) for line in lines):
                break
        else:
            missing.append(f"OCR layout {layout}")
    return [f"self-test missed the planted name in: {', '.join(missing)}"] if missing else []


def verify_image(path: Path, profile_path: Path) -> VerifyResult:
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    findings: list[str] = []
    regions: list[list[int]] = []
    problems: list[str] = []
    try:
        rules, safe = load_rules(profile_path)
        if data.startswith(_PNG_SIGNATURE):
            kind, (picture, structure) = "png", _png_structure(data)
        elif data.startswith(b"\xff\xd8"):
            kind, (picture, structure) = "jpeg", _jpeg_structure(data)
        else:
            raise _Uninspectable("not a PNG or JPEG image")
        if _on("image-structure"):
            findings += [f"image: {p}" for p in structure]
        if _on("transparent-pixels"):
            findings += [f"image: {p}" for p in _transparent_problems(picture)]
        if _on("image-codes"):
            regions += _code_regions(path)
            if regions:
                findings.append("image: machine-readable or code-like region remains")
        layouts = [layout for step, layout in _LAYOUTS if _on(step)]
        with tempfile.TemporaryDirectory(prefix="docsan-image-ocr-") as tmp:
            target,expected,factor=_zoomed(path,(picture.width,picture.height),Path(tmp)) if layouts else (path,(picture.width,picture.height),1.0)
            for layout in layouts:
                size, lines = image_ocr(target, layout)
                if size != expected:
                    problems.append(f"OCR read a {size[0]}x{size[1]} image, expected {expected[0]}x{expected[1]}")
                found, where = _scan_lines(lines, rules, safe, f"OCR layout {layout}", picture, factor)
                findings += found
                regions += [[round(v/factor) for v in box] for box in where]
        if _on("image-self-test"):
            with tempfile.TemporaryDirectory(prefix="docsan-verify-") as tmp:
                problems += _image_self_test(data, kind, Path(tmp), layouts)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append("file changed during verification")
    except _Uninspectable as exc:
        return VerifyResult("could_not_inspect", digest, findings, [str(exc)], regions)
    except Exception as exc:  # noqa: BLE001 - any unexpected failure is an incomplete inspection
        return VerifyResult("could_not_inspect", digest, findings, [f"verifier error: {exc!r}"], regions)
    if problems:
        return VerifyResult("could_not_inspect", digest, findings, problems, regions)
    return VerifyResult("findings" if findings else "pass", digest, findings, [], regions)


# --- PDF -----------------------------------------------------------------------------------
#
# A released PDF holds exactly what docsan.pdf.write emits, which the
# verifier knows by heart: a fixed header; objects numbered 1, 2, 3... in
# order (catalog, page tree, font, document information, then a page, its
# picture and its content stream for each page); a cross-reference table
# that matches the objects' offsets; and a trailer whose file identifier is
# the hash of everything before it, so it cannot carry anything either. Each
# content stream draws the page's picture over the whole page, then writes
# invisible lines of text. Every byte belongs to one of these, or the file
# has findings. The text layer is decoded and scanned line by line and with
# the lines joined; each page's picture gets the image checks and is read by
# Tesseract; PDFKit extracts the text a second time.

_PDF_HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
_PDF_CATALOG = b"<< /Type /Catalog /Pages 2 0 R >>"
_PDF_INFO = b"<< /Producer (DocSan) >>"
# SHA-256 of the writer's font object: Helvetica, Windows-1252, every width 500.
PDF_FONT_SHA256 = "6b108f6154ec8c2202c97324bc765dea3b7aca038d15684e37bcad5cc726c8ba"
_NUM = rb"-?[0-9]{1,6}(?:\.[0-9]{1,2})?"
_PDF_PAGE = re.compile(
    rb"<< /Type /Page /Parent 2 0 R /MediaBox \[0 0 (" + _NUM + rb") (" + _NUM + rb")\] "
    rb"/Resources << /XObject << /Im1 ([0-9]+) 0 R >> /Font << /F1 3 0 R >> >> /Contents ([0-9]+) 0 R >>"
)
# A page's picture: a JPEG, or the compressed rows of an 8-bit PNG (Flate with PNG predictors).
_PDF_IMAGE = re.compile(
    rb"<< /Type /XObject /Subtype /Image /Width ([0-9]{1,6}) /Height ([0-9]{1,6}) /ColorSpace /Device(Gray|RGB) "
    rb"/BitsPerComponent 8 (?:/Filter /DCTDecode|/Filter /FlateDecode /DecodeParms << /Predictor 15 /Colors ([13]) "
    rb"/BitsPerComponent 8 /Columns ([0-9]{1,6}) >>) /Length ([0-9]{1,10}) >>"
)
_PDF_CONTENT = re.compile(rb"<< /Filter /FlateDecode /Length ([0-9]{1,10}) >>")
_PDF_TEXT_LINE = re.compile(
    rb"BT 3 Tr /F1 (" + _NUM + rb") Tf (" + _NUM + rb") Tz (1 0 0 1|0 -1 1 0|0 1 -1 0) (" + _NUM + rb") (" + _NUM
    + rb") Tm \(((?:[^()\\\x00-\x1f\x7f-\xff]|\\[0-7]{3}|\\[()\\])*)\) Tj ET\n"
)
_PDF_ESCAPE = re.compile(rb"\\([0-7]{3}|[()\\])")
_PDF_MAX_STREAM = 64 * 1024 * 1024


@dataclass
class _PdfObject:
    body: bytes  # the dictionary, one line
    stream: bytes | None = None


@dataclass
class _PdfPage:
    number: int
    width: bytes  # as written in the MediaBox
    height: bytes
    picture: bytes  # a JPEG, or a PNG made from the stored rows, for OCR
    kind: str  # "jpeg" or "png"
    size: tuple[int, int]  # the picture's pixels
    lines: list[str]
    other: list[str]  # content lines outside the writer's grammar, scanned as they are


def _png_from_rows(width: int, height: int, colors: int, rows: bytes) -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", width, height, 8, 0 if colors == 1 else 2, 0, 0, 0)
    return _PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", rows) + chunk(b"IEND", b"")


def _pdf_picture(k: int, im: re.Match[bytes], data: bytes) -> tuple[bytes, str, list[str]]:
    """A page picture ready for OCR, and every way it differs from what the writer stores."""
    width, height, space = int(im.group(1)), int(im.group(2)), im.group(3)
    if im.group(4) is None:  # JPEG
        picture, structure = _jpeg_structure(data)
        problems = [f"page {k}: picture: {p}" for p in structure]
        if (picture.width, picture.height) != (width, height):
            problems.append(f"page {k}: the picture's size differs from its object")
        if space != b"RGB" or picture.channels != 3:
            problems.append(f"page {k}: the JPEG picture is not RGB")
        return data, "jpeg", problems
    colors, columns = int(im.group(4)), int(im.group(5))
    problems = []
    if columns != width or colors != (1 if space == b"Gray" else 3):
        problems.append(f"page {k}: the picture's predictor does not match the picture")
    stride = 1 + width * colors
    stream = zlib.decompressobj()
    try:
        rows = stream.decompress(data, stride * height + 1)
    except zlib.error as exc:
        raise _Uninspectable(f"page {k}: cannot decompress the picture: {exc}") from exc
    if len(rows) != stride * height or not stream.eof or stream.unused_data:
        problems.append(f"page {k}: the picture's data does not match its size")
    elif any(f > 4 for f in rows[::stride]):
        problems.append(f"page {k}: the picture's rows use an unknown filter")
    return _png_from_rows(width, height, colors, data), "png", problems


def _pdf_parse(data: bytes) -> tuple[list[_PdfObject], list[str]]:
    """The objects in order, and every way the file's layout differs from the writer's."""
    problems: list[str] = []
    if not data.startswith(_PDF_HEADER):
        raise _Uninspectable("not a PDF written by DocSan (unexpected header)")
    objects: list[_PdfObject] = []
    offsets: list[int] = []
    pos = len(_PDF_HEADER)
    while not data.startswith(b"xref\n", pos):
        head = re.compile(rb"([0-9]{1,7}) 0 obj\n").match(data, pos)
        if not head or int(head.group(1)) != len(objects) + 1:
            raise _Uninspectable("PDF objects are missing or out of order")
        offsets.append(pos)
        line_end = data.find(b"\n", head.end())
        if line_end < 0:
            raise _Uninspectable("truncated PDF object")
        body = data[head.end() : line_end]
        pos = line_end + 1
        stream = None
        if data.startswith(b"stream\n", pos):
            length = re.search(rb"/Length ([0-9]{1,10}) >>$", body)
            if not length or int(length.group(1)) > _PDF_MAX_STREAM:
                raise _Uninspectable("PDF stream without a usable length")
            start = pos + len(b"stream\n")
            stream = data[start : start + int(length.group(1))]
            pos = start + len(stream)
            if not data.startswith(b"\nendstream", pos):
                raise _Uninspectable("PDF stream length does not match its data")
            pos += len(b"\nendstream")
            if data.startswith(b"\n", pos):
                pos += 1
        if not data.startswith(b"endobj\n", pos):
            raise _Uninspectable("PDF object is not closed where the writer closes it")
        pos += len(b"endobj\n")
        objects.append(_PdfObject(body, stream))
    xref = pos
    expected = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(
        f"{off:010d} 00000 n \n".encode() for off in offsets
    )
    if not data.startswith(expected, xref):
        problems.append("the cross-reference table does not match the objects")
    pos = xref + len(expected)
    trailer = re.compile(
        rb"trailer\n<< /Size ([0-9]+) /Root 1 0 R /Info 4 0 R /ID \[<([0-9A-F]{32})><\2>\] >>\nstartxref\n([0-9]+)\n%%EOF\n"
    ).match(data, pos)
    if not trailer:
        problems.append("the trailer differs from the writer's")
    else:
        if int(trailer.group(1)) != len(objects) + 1 or int(trailer.group(3)) != xref:
            problems.append("the trailer does not match the objects")
        if trailer.group(2).decode() != hashlib.sha256(data[:xref]).hexdigest()[:32].upper():
            problems.append("the file identifier is not the writer's")
        if trailer.end() != len(data):
            problems.append("bytes after the end of the PDF")
    return objects, problems


def _pdf_string(raw: bytes) -> str:
    unescaped = _PDF_ESCAPE.sub(lambda m: bytes([int(m.group(1), 8)]) if len(m.group(1)) == 3 else m.group(1), raw)
    try:
        return unescaped.decode("cp1252")
    except UnicodeDecodeError as exc:
        raise _Uninspectable(f"text layer is not Windows-1252 text: {exc}") from exc


def _pdf_pages(objects: list[_PdfObject]) -> tuple[list[_PdfPage], list[str]]:
    """Each page's picture and text, and every object that differs from the writer's form.
    The document information (object 4) is the metadata check's."""
    problems: list[str] = []
    n = (len(objects) - 4) // 3
    if len(objects) < 7 or (len(objects) - 4) % 3:
        raise _Uninspectable("the PDF does not hold the writer's objects")
    if objects[0].body != _PDF_CATALOG or objects[0].stream is not None:
        problems.append("the catalog differs from the writer's")
    kids = " ".join(f"{5 + 3 * k} 0 R" for k in range(n))
    if objects[1].body != f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode() or objects[1].stream is not None:
        problems.append("the page tree differs from the writer's")
    if hashlib.sha256(objects[2].body).hexdigest() != PDF_FONT_SHA256 or objects[2].stream is not None:
        problems.append("the font differs from the writer's")
    pages: list[_PdfPage] = []
    for k in range(n):
        num = 5 + 3 * k
        page, image, content = objects[num - 1], objects[num], objects[num + 1]
        m = _PDF_PAGE.fullmatch(page.body)
        if not m or page.stream is not None or int(m.group(3)) != num + 1 or int(m.group(4)) != num + 2:
            problems.append(f"page {k + 1}: the page object differs from the writer's")
            continue
        width, height = m.group(1), m.group(2)
        im = _PDF_IMAGE.fullmatch(image.body)
        if not im or image.stream is None:
            problems.append(f"page {k + 1}: the picture object differs from the writer's")
            continue
        picture, kind, found = _pdf_picture(k + 1, im, image.stream)
        problems += found
        cm = _PDF_CONTENT.fullmatch(content.body)
        if not cm or content.stream is None:
            problems.append(f"page {k + 1}: the content object differs from the writer's")
            continue
        stream = zlib.decompressobj()
        try:
            text = stream.decompress(content.stream, _PDF_MAX_STREAM)
        except zlib.error as exc:
            raise _Uninspectable(f"page {k + 1}: cannot decompress the content: {exc}") from exc
        if not stream.eof or stream.unused_data or stream.unconsumed_tail:
            problems.append(f"page {k + 1}: the content stream has data after its end")
        head = b"q\n" + width + b" 0 0 " + height + b" 0 0 cm\n/Im1 Do\nQ\n"
        if not text.startswith(head):
            problems.append(f"page {k + 1}: the content does not draw the picture over the page")
            continue
        lines: list[str] = []
        other: list[str] = []
        pos = len(head)
        while pos < len(text):
            line = _PDF_TEXT_LINE.match(text, pos)
            if line:
                lines.append(_pdf_string(line.group(6)))
                pos = line.end()
                continue
            end = text.find(b"\n", pos)
            end = len(text) if end < 0 else end + 1
            other.append(text[pos:end].decode("latin-1"))
            pos = end
        if other:
            problems.append(f"page {k + 1}: the content holds something other than invisible text lines")
        pages.append(_PdfPage(k + 1, width, height, picture, kind, (int(im.group(1)), int(im.group(2))), lines, other))
    return pages, problems


def _pdf_joined(lines: list[str]) -> str:
    """A page's lines as one text; a word hyphenated at a line end joins again."""
    out = ""
    for line in (line.strip() for line in lines):
        if out and re.search(r"[^\W\d_][-\u00ad]$", out) and line[:1].isalpha():
            out += line
        else:
            out += (" " if out else "") + line
    return out


def _pdf_field_findings(text: str) -> list[str]:
    if not _on("pdf-field-values"):
        return []
    text = _vnorm(text).strip()
    found = []
    if (len(re.sub(r"\W", "", text)) >= 7
            and re.search(r"\b(?:s\.?\s*a\.?|s\.?a\.?r\.?l\.?|ltd\.?|gmbh|inc\.?)\s*$", text)):
        found.append("unredacted legal entity")
    if re.search(r"(?:^|\s)\d{1,4}\s*,?\s*(?:rue|boulevard|bd\.?|avenue|av\.?|street|road)\b|\bL[- ]?\d{4}\b", text, re.IGNORECASE):
        found.append("unredacted postal address")
    return found


def _pdf_text_findings(pages: list[_PdfPage], rules: list[_Rule], safe: set[str]) -> list[str]:
    found: list[str] = []
    if not _on("pdf-text-layer"):
        return found
    for page in pages:
        for line in page.lines + page.other:
            found += [f"page {page.number} text: {f}" for f in _pdf_field_findings(line)]
            found += [f"page {page.number} text: {f}" for f in _term_findings(line, rules)]
            found += [f"page {page.number} text: {f}" for f in _pattern_findings(_vnorm(line), safe, phones=True)]
        if _on("pdf-join-lines") and len(page.lines) > 1:
            joined = _pdf_joined(page.lines)
            found += [f"page {page.number} text across lines: {f}" for f in _term_findings(joined, rules)]
            found += [f"page {page.number} text across lines: {f}" for f in _pattern_findings(_vnorm(joined), safe, phones=True)]
    return found


def _pdf_metadata_findings(info: _PdfObject, rules: list[_Rule], safe: set[str]) -> list[str]:
    if not _on("pdf-metadata"):
        return []
    if info.body == _PDF_INFO and info.stream is None:
        return []
    text = (info.body + (info.stream or b"")).decode("latin-1")
    found = ["metadata: the document information holds more than the producer"]
    found += [f"metadata: {f}" for f in _term_findings(text, rules)]
    found += [f"metadata: {f}" for f in _pattern_findings(_vnorm(text), safe, phones=True)]
    return found


_ZOOM = 2.0
_MAX_ZOOM_PIXELS = 60_000_000


def _zoomed(path: Path, size: tuple[int, int], workdir: Path) -> tuple[Path, tuple[int, int], float]:
    """A page picture at twice its size, for Tesseract, which reads text a few
    pixels high far better enlarged. macOS ``sips`` resamples it."""
    w, h = size
    factor = min(_ZOOM, math.sqrt(_MAX_ZOOM_PIXELS / (w * h)))
    if factor <= 1.0:
        return path, size, 1.0
    zw, zh = round(w * factor), round(h * factor)
    out = workdir / f"{path.stem}-zoomed.png"
    env = {"PATH": "/usr/bin:/bin"} | {k: os.environ[k] for k in ("HOME", "TMPDIR") if os.environ.get(k)}
    try:
        proc = subprocess.run(["/usr/bin/sips", "--resampleHeightWidth", str(zh), str(zw), "-s", "format", "png", str(path),
                               "--out", str(out)], capture_output=True, timeout=300, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Uninspectable(f"cannot enlarge a page for OCR: {exc}") from exc
    if proc.returncode != 0 or not out.is_file():
        raise _Uninspectable(f"cannot enlarge a page for OCR: {proc.stderr.decode(errors='replace').strip()[:200]}")
    return out, (zw, zh), factor


def _pdf_ocr(pages: list[_PdfPage], rules: list[_Rule], safe: set[str], workdir: Path) -> tuple[list[str], dict[str, list[list[int]]]]:
    findings: list[str] = []
    regions: dict[str, list[list[int]]] = {}
    if not _on("pdf-page-ocr"):
        return findings, regions
    for page in pages:
        path = workdir / f"page-{page.number}.{'jpg' if page.kind == 'jpeg' else 'png'}"
        path.write_bytes(page.picture)
        if _on("pdf-barcodes"):
            codes = _code_regions(path)
            if codes:
                findings.append(f"page {page.number}: machine-readable or code-like region remains")
                regions.setdefault(f"page-{page.number}", []).extend(codes)
        target, expected, factor = _zoomed(path, page.size, workdir) if _on("pdf-ocr-zoom") else (path, page.size, 1.0)
        for _step, layout in _LAYOUTS:
            size, lines = image_ocr(target, layout)
            if size != expected:
                raise _Uninspectable(f"page {page.number}: OCR read a {size[0]}x{size[1]} image, the page is {expected[0]}x{expected[1]}")
            found, where = _scan_lines(lines, rules, safe, f"page {page.number}, OCR layout {layout}")
            for line in lines:
                extra = _pdf_field_findings(line.text)
                if extra:
                    found += [f"page {page.number} pixels: {value}" for value in extra]
                    where.append(line.box)
            findings += found
            if where:
                regions.setdefault(f"page-{page.number}", []).extend([[round(v / factor) for v in box] for box in where])
    return findings, regions


@functools.cache
def _vision_helper() -> str:
    found = os.environ.get("DOCSAN_VISION") or str(_BUNDLE / "bin" / "docsan-vision")
    if not os.access(found, os.X_OK):
        raise _Uninspectable("PDFKit text extraction is not available")
    return found


def extract_pdf_with_pdfkit(path: Path) -> list[str]:
    """Each page's text as PDFKit, Apple's PDF reader, extracts it."""
    env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"}
    for key in ("HOME", "CFFIXED_USER_HOME", "APP_SANDBOX_CONTAINER_ID", "TMPDIR"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    try:
        proc = subprocess.run([_vision_helper()], input=json.dumps({"command": "pdf-text", "input": str(path)}).encode(),
                              capture_output=True, timeout=600, env=env, check=False)
        answer = json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise _Uninspectable(f"second extractor failed: {exc}") from exc
    if not answer.get("ok") or not isinstance(answer.get("pages"), list):
        raise _Uninspectable(f"second extractor failed: {answer.get('error', 'no answer')}")
    return [str(p) for p in answer["pages"]]


# The PDF extractor can be swapped by tests; production uses PDFKit.
pdf_extractor = extract_pdf_with_pdfkit


def _pdf_extracted_findings(texts: list[str], rules: list[_Rule], safe: set[str]) -> list[str]:
    found: list[str] = []
    for n, text in enumerate(texts, 1):
        found += [f"page {n} (second extractor): {f}" for f in _scan_extracted(text, rules, safe)]
    return found


def _pdf_serialize(objects: list[_PdfObject]) -> bytes:
    """The objects written again as a complete file, for the self-test's planted copy."""
    out = bytearray(_PDF_HEADER)
    offsets: list[int] = []
    for num, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + obj.body + b"\n"
        if obj.stream is not None:
            out += b"stream\n" + obj.stream + b"\nendstream\n"
        out += b"endobj\n"
    file_id = hashlib.sha256(bytes(out)).hexdigest()[:32].upper()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R /ID [<{file_id}><{file_id}>] >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _pdf_self_test(objects: list[_PdfObject], pages: list[_PdfPage], workdir: Path) -> list[str]:
    """Plant random names in a copy: in a text line, split across two lines and
    in the document information. Every view must find them; Tesseract must
    read a planted name in a picture."""
    canary = "zq" + "".join(secrets.choice("bcdfghjkmnpqrstvwxz") for _ in range(10))
    rule = _build_rule(canary, False, False, [])
    # Case-sensitive, so separators matter: only rejoining the hyphenated word finds it.
    split_rule = _build_rule(canary, True, False, [])
    planted = list(objects)
    content = objects[6]
    stream = zlib.decompress(content.stream or b"")
    stream += (f"BT 3 Tr /F1 10 Tf 100 Tz 1 0 0 1 10 10 Tm ({canary}) Tj ET\n"
               f"BT 3 Tr /F1 10 Tf 100 Tz 1 0 0 1 10 30 Tm (zz {canary[:6]}-) Tj ET\n"
               f"BT 3 Tr /F1 10 Tf 100 Tz 1 0 0 1 10 20 Tm ({canary[6:]} zz) Tj ET\n").encode()
    packed = zlib.compress(stream)
    planted[6] = _PdfObject(f"<< /Filter /FlateDecode /Length {len(packed)} >>".encode(), packed)
    planted[3] = _PdfObject(b"<< /Producer (DocSan) /Title (" + canary.encode() + b") >>")
    copy = _pdf_serialize(planted)
    missing: list[str] = []
    parsed, layout = _pdf_parse(copy)
    if layout:
        return [f"self-test could not write a planted copy: {layout[0]}"]
    planted_pages, _ = _pdf_pages(parsed)
    if _on("pdf-text-layer"):
        if not any(f.startswith("page 1 text: ") for f in _pdf_text_findings(planted_pages[:1], [rule], set())):
            missing.append("text layer")
        if _on("pdf-join-lines"):
            split = [_PdfPage(1, p.width, p.height, p.picture, p.kind, p.size, [line for line in p.lines if canary not in line], [])
                     for p in planted_pages[:1]]
            if not any(f.startswith("page 1 text across lines: ") for f in _pdf_text_findings(split, [split_rule], set())):
                missing.append("text joined across lines")
    if _on("pdf-metadata") and not any(canary in f for f in _pdf_metadata_findings(parsed[3], [rule], set())):
        missing.append("metadata")
    if _on("pdf-second-extractor"):
        path = workdir / "self-test.pdf"
        path.write_bytes(copy)
        if not _pdf_extracted_findings(pdf_extractor(path), [rule], set()):
            missing.append("second extractor")
    problems = [f"self-test missed the planted name in: {', '.join(missing)}"] if missing else []
    if _on("pdf-page-ocr"):
        problems += _image_self_test(pages[0].picture, pages[0].kind, workdir, [layout for _step, layout in _LAYOUTS])
    return problems


def verify_pdf(path: Path, profile_path: Path) -> VerifyResult:
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    findings: list[str] = []
    regions: dict[str, list[list[int]]] = {}
    try:
        rules, safe = load_rules(profile_path)
        objects, layout = _pdf_parse(data)
        pages, structure = _pdf_pages(objects)
        if _on("pdf-structure"):
            findings += [f"PDF structure: {p}" for p in layout + structure]
        findings += _pdf_metadata_findings(objects[3], rules, safe)
        findings += _pdf_text_findings(pages, rules, safe)
        with tempfile.TemporaryDirectory(prefix="docsan-verify-") as tmp:
            found, regions = _pdf_ocr(pages, rules, safe, Path(tmp))
            findings += found
            if _on("pdf-second-extractor"):
                texts = pdf_extractor(path)
                if len(texts) != len(pages):
                    raise _Uninspectable(f"the second extractor read {len(texts)} pages, the file has {len(pages)}")
                findings += _pdf_extracted_findings(texts, rules, safe)
            problems = _pdf_self_test(objects, pages, Path(tmp)) if _on("pdf-self-test") else []
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            problems.append("file changed during verification")
    except _Uninspectable as exc:
        return VerifyResult("could_not_inspect", digest, findings, [str(exc)], media_regions=regions)
    except Exception as exc:  # noqa: BLE001 - any unexpected failure is an incomplete inspection
        return VerifyResult("could_not_inspect", digest, findings, [f"verifier error: {exc!r}"], media_regions=regions)
    if problems:
        return VerifyResult("could_not_inspect", digest, findings, problems, media_regions=regions)
    return VerifyResult("findings" if findings else "pass", digest, findings, [], media_regions=regions)
