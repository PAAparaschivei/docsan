"""Read a Word package into the allowed content model.

Default deny: every package part and every element must match a rule in the
DOCX component contract (docs/docx-component-contract.md). Anything else is
recorded as a blocking reason, and the pipeline refuses to export the file.
"""

from __future__ import annotations

import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from lxml import etree

from .. import steps
from ..image.vector import vector_kind
from .diagram import read_chart, read_smartart
from .format import (
    CELL,
    PARAGRAPH,
    ROW,
    RUN,
    TABLE,
    CellContext,
    Formatting,
    ParaFormat,
    cell_types,
    freeze,
)
from .model import (
    LIVE_FIELDS,
    Anchor,
    Block,
    Cell,
    Document,
    Figure,
    Fmt,
    HeaderFooter,
    Paragraph,
    Run,
    Section,
    Settings,
    Table,
    TextBox,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
V = "{urn:schemas-microsoft-com:vml}"
O = "{urn:schemas-microsoft-com:office:office}"
CHART = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"
WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
WPG = "{http://schemas.microsoft.com/office/word/2010/wordprocessingGroup}"
WPC = "{http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
STRICT_MARKERS = ("http://purl.oclc.org/ooxml/",)

MAX_ENTRIES = 5000
MAX_PART_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_RATIO = 200

CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

MAIN_TYPES = frozenset(
    {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "application/vnd.ms-word.document.macroEnabled.main+xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
        "application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
    }
)

# Parts that are inspected and deliberately not carried into the output.
REMOVED_PARTS = [
    re.compile(p)
    for p in (
        r"docprops/.*",
        r"docmetadata/.*",
        r"customxml/.*",
        r"word/comments[a-z]*\.xml",
        r"word/people\.xml",
        r"word/settings\.xml",
        r"word/websettings\.xml",
        r"word/fonttable\.xml",
        r"word/theme/.*",
        r"word/(header|footer)\d*\.xml",
        r"word/glossary/.*",
        r"word/styleswitheffects\.xml",
        r"word/vbaproject\.bin",
        r"word/vbadata\.xml",
        r"word/fonts/.*",
        r"word/printersettings/.*",
        r"word/customizations\.xml",
        r"word/attachedtoolbars\.bin",
        r"word/keymapcustomizations\.xml",
        r"word/media/.*",
        r"word/embeddings/.*",
        r"word/charts/.*",
        r"word/diagrams/.*",
        r"word/ink/.*",
        r"word/activex/.*",
        r"word/webextensions/.*",
        r"word/intelligence\d*\.xml",
        r"customui/.*",
        r"word/_rels/.*\.rels",
    )
]

def image_kind(data: bytes) -> str | None:
    """"jpeg" or "png" for raster images DocSan can mask (other raster formats
    are converted to PNG), None for vector or unknown formats."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")):
        return "png"
    return None


def _length_emu(style: str, key: str) -> int:
    """A VML length such as "width:120pt" in English Metric Units."""
    m = re.search(rf"(?:^|;)\s*{key}\s*:\s*([\d.]+)\s*(pt|in|cm|mm|px)?", style)
    if not m:
        return 0
    value = float(m.group(1))
    per = {"pt": 12700, "in": 914400, "cm": 360000, "mm": 36000, "px": 9525, None: 12700}[m.group(2)]
    return int(value * per)


# Elements that carry no content and are dropped wherever they appear.
MARKERS = frozenset(
    W + t
    for t in (
        "bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "proofErr",
        "permStart", "permEnd", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
        "moveToRangeEnd", "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart",
        "customXmlDelRangeEnd", "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
        "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
    )
)
PROPERTIES = frozenset(
    W + t
    for t in (
        "pPr", "rPr", "sectPr", "tblPr", "tblGrid", "tblPrEx", "trPr", "tcPr", "sdtPr", "sdtEndPr",
        "customXmlPr", "smartTagPr",
    )
)
IGNORED_RUN_CONTENT = frozenset(
    W + t
    for t in (
        "lastRenderedPageBreak", "footnoteRef", "endnoteRef", "separator", "continuationSeparator",
        "annotationRef", "commentReference", "pgNum", "dayLong", "dayShort", "monthLong", "monthShort",
        "yearLong", "yearShort",
    )
)
KNOWN_GRAPHICS = frozenset(
    {
        "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
        "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
        "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
        "http://schemas.openxmlformats.org/drawingml/2006/chart",
        "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    }
)

_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    dtd_validation=False,
    huge_tree=False,
    remove_comments=True,
    remove_pis=True,
)


@dataclass
class ReadResult:
    document: Document | None
    blocking: list[str] = field(default_factory=list)
    inventory: dict[str, str] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)


class _Blocked(Exception):
    pass


def _local(tag: object) -> str:
    return tag.split("}", 1)[-1] if isinstance(tag, str) else str(tag)


def _on(el: etree._Element | None) -> bool:
    if el is None:
        return False
    return el.get(W + "val", "true").lower() not in ("0", "false", "off", "none")


def _first_choice(alt: etree._Element) -> etree._Element | None:
    return alt.find(MC + "Choice")


def _merge(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if not r.text:
            continue
        if out and out[-1].same_format(r):
            out[-1] = Run(out[-1].text + r.text, r.fmt, r.field, r.ours)
        else:
            out.append(r)
    return out


# Breaks and positional tabs, as characters in run text. Each counts as
# white space, so text checks treat them like a line break or a tab.
BREAKS = {"page": "\f", "column": "\x1d"}
POSITIONAL_TABS = {"left": "\x1c", "center": "\x1e", "right": "\x1f"}


def _live_field(instr: str) -> str:
    """PAGE, NUMPAGES or SECTIONPAGES when the field is one of those, with at
    most a number-style switch; anything else keeps only its result."""
    words = instr.split()
    if not words or words[0].upper() not in LIVE_FIELDS:
        return ""
    rest = " ".join(words[1:]).replace("\\* MERGEFORMAT", "").strip()
    if rest and not re.fullmatch(r"\\\* (Arabic|roman|ROMAN|alphabetic|ALPHABETIC)", rest):
        return ""
    return " ".join([words[0].upper(), rest]).strip()


_H_FROM = frozenset("character column insideMargin leftMargin margin outsideMargin page rightMargin".split())  # noqa: SIM905
_V_FROM = frozenset("bottomMargin insideMargin line margin outsideMargin page paragraph topMargin".split())  # noqa: SIM905
_H_ALIGN = frozenset("left right center inside outside".split())  # noqa: SIM905
_V_ALIGN = frozenset("top bottom center inside outside".split())  # noqa: SIM905
_WRAPS = {"wrapNone": "wrapNone", "wrapSquare": "wrapSquare", "wrapTight": "wrapSquare", "wrapThrough": "wrapSquare",
          "wrapTopAndBottom": "wrapTopAndBottom"}
SHAPES = frozenset("rect roundRect ellipse snip1Rect snip2SameRect round1Rect round2SameRect flowChartProcess "  # noqa: SIM905
                   "flowChartAlternateProcess wedgeRectCallout wedgeRoundRectCallout".split())
_SCHEME = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}


def _digits(value: str | None, signed: bool = False) -> bool:
    return bool(value) and re.fullmatch(r"-?\d{1,10}" if signed else r"\d{1,10}", value.strip()) is not None


def read_anchor(el: etree._Element) -> Anchor | None:
    """A wp:anchor's position and wrapping, or None when it uses something not kept."""
    attrs = [(a, el.get(a, "0") if _digits(el.get(a)) else "0") for a in ("distT", "distB", "distL", "distR", "relativeHeight")]
    attrs += [(a, "1" if el.get(a, "0").lower() in ("1", "true") else "0")
              for a in ("simplePos", "behindDoc", "locked", "layoutInCell", "allowOverlap")]
    positions: list[tuple[str, str, str]] = []
    for tag, froms, aligns in ((WP + "positionH", _H_FROM, _H_ALIGN), (WP + "positionV", _V_FROM, _V_ALIGN)):
        p = el.find(tag)
        if p is None or p.get("relativeFrom") not in froms:
            return None
        align, offset = p.find(WP + "align"), p.find(WP + "posOffset")
        if align is not None and (align.text or "").strip() in aligns:
            positions.append((p.get("relativeFrom"), "align", align.text.strip()))
        elif offset is not None and _digits(offset.text, signed=True):
            positions.append((p.get("relativeFrom"), "posOffset", str(int(offset.text.strip()))))
        else:
            return None
    wrap: tuple[str, tuple[tuple[str, str], ...]] = ("wrapNone", ())
    for child in el:
        name = _WRAPS.get(_local(child.tag))
        if name:
            keep = ("distT", "distB") if name == "wrapTopAndBottom" else ("distT", "distB", "distL", "distR") if name == "wrapSquare" else ()
            wattrs = [(a, child.get(a)) for a in keep if _digits(child.get(a))]
            if name == "wrapSquare":
                text = child.get("wrapText", "bothSides")
                wattrs.insert(0, ("wrapText", text if text in ("bothSides", "left", "right", "largest") else "bothSides"))
            wrap = (name, tuple(wattrs))
            break
    effect = el.find(WP + "effectExtent")
    sides = [effect.get(k, "0") for k in ("l", "t", "r", "b")] if effect is not None else []
    extent = tuple(int(v) for v in sides) if sides and all(_digits(v, signed=True) for v in sides) else None
    return Anchor(tuple(attrs), positions[0], positions[1], wrap, extent)  # type: ignore[arg-type]


def _resolve(base_dir: str, target: str) -> str:
    if target.startswith("/"):
        path = PurePosixPath(target.lstrip("/"))
    else:
        path = PurePosixPath(base_dir) / target
    parts: list[str] = []
    for p in path.parts:
        if p == "..":
            if parts:
                parts.pop()
        elif p not in ("", "."):
            parts.append(p)
    return "/".join(parts)


class _Reader:
    def __init__(self, zf: zipfile.ZipFile, names: dict[str, str], remove_images: bool, label_unreadable: bool = False):
        self.zf = zf
        self.names = names  # lower-case name -> actual entry name
        self.remove_images = remove_images  # every visual becomes a label
        self.label_unreadable = label_unreadable  # only visuals DocSan cannot read become labels
        # Relationships of the part being read: the main document, a header or a footer.
        self.image_rels: dict[str, str] = {}  # relationship id -> image part
        self.chart_rels: dict[str, str] = {}  # relationship id -> chart part
        self.diagram_rels: dict[str, str] = {}  # relationship id -> SmartArt data part
        self.layout_rels: dict[str, str] = {}  # relationship id -> SmartArt layout part
        self.linked_images: set[str] = set()  # relationship ids of images outside the package
        self.part_rels: dict[str, tuple[str, str]] = {}  # header and footer relationship ids -> (kind, part)
        self.media_read: set[str] = set()
        self.figure_count = 0
        self.blocking: list[str] = []
        self.dropped: Counter[str] = Counter()
        self.where = ""
        self.styles: dict[str, tuple[str, str | None, int | None]] = {}
        self.fields: list[list[str]] = []  # open fields: [state, instruction, live field name]
        self.notes: dict[str, dict[str, etree._Element]] = {"footnote": {}, "endnote": {}}
        self.note_queue: list[tuple[str, str]] = []
        self.note_numbers: dict[tuple[str, str], int] = {}
        self.parts: list[HeaderFooter] = []
        self.part_index: dict[str, int] = {}
        self.part_names: list[str] = []
        self.drawings: list[Figure | TextBox] = []  # floating pictures and text boxes of the paragraph being read
        self.setup(None, None, None, None)

    def setup(self, styles, theme, fonts, numbering) -> None:
        self.fmt = Formatting(styles, theme, fonts, numbering, self.dropped)
        self.para: ParaFormat = self.fmt.paragraph(None)
        self.plain = self.para  # formatting for text DocSan writes itself
        self.cell: CellContext | None = None

    # -- helpers ---------------------------------------------------------
    def block(self, why: str) -> None:
        self.blocking.append(f"{self.where}: {why}" if self.where else why)

    def part(self, name: str) -> etree._Element:
        actual = self.names.get(name.lower())
        if actual is None:
            raise _Blocked(f"missing part {name}")
        info = self.zf.getinfo(actual)
        if info.file_size > MAX_PART_BYTES:
            raise _Blocked(f"{name}: part too large")
        try:
            data = self.zf.read(actual)
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
            raise _Blocked(f"{name}: unreadable ({exc})") from exc
        try:
            root = etree.fromstring(data, _PARSER)
        except etree.XMLSyntaxError as exc:
            raise _Blocked(f"{name}: malformed XML ({exc})") from exc
        if root.getroottree().docinfo.doctype:
            raise _Blocked(f"{name}: declares a DTD")
        if any(m in (root.tag or "") for m in STRICT_MARKERS):
            raise _Blocked(f"{name}: Strict Open XML is not supported")
        return root

    def media(self, name: str) -> bytes:
        actual = self.names.get(name.lower())
        if actual is None:
            raise _Blocked(f"missing image part {name}")
        if self.zf.getinfo(actual).file_size > MAX_PART_BYTES:
            raise _Blocked(f"{name}: image too large")
        try:
            data = self.zf.read(actual)
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError) as exc:
            raise _Blocked(f"{name}: unreadable ({exc})") from exc
        self.media_read.add(name.lower())
        return data

    def relationships(self, part: str) -> list[tuple[str, str, str, bool]]:
        """(id, type, target, external) for each relationship of a part."""
        folder = str(PurePosixPath(part).parent)
        rels = f"{folder}/_rels/{PurePosixPath(part).name}.rels"
        if rels.lower() not in self.names:
            return []
        saved, self.where = self.where, rels
        try:
            root = self.part(rels)
        finally:
            self.where = saved
        self.media_read.add(rels.lower())
        out = []
        for rel in root.iter(PKG_REL + "Relationship"):
            rtype = rel.get("Type") or ""
            if any(m in rtype for m in STRICT_MARKERS):
                raise _Blocked("Strict Open XML is not supported")
            external = rel.get("TargetMode") == "External"
            target = rel.get("Target") or ""
            out.append((rel.get("Id") or "", rtype.rsplit("/", 1)[-1], target if external else _resolve(folder, target), external))
        return out

    def use_relationships(self, rels: list[tuple[str, str, str, bool]]) -> None:
        self.image_rels, self.chart_rels, self.diagram_rels, self.layout_rels = {}, {}, {}, {}
        self.linked_images = set()
        for rid, rtype, target, external in rels:
            if external:
                if rtype == "image":
                    self.linked_images.add(rid)
                continue
            if rtype == "image":
                self.image_rels[rid] = target
            elif rtype == "chart":
                self.chart_rels[rid] = target
            elif rtype == "diagramData":
                self.diagram_rels[rid] = target
            elif rtype == "diagramLayout":
                self.layout_rels[rid] = target

    def plain_runs(self, text: str | list[tuple[str, bool]], bold: bool = False) -> list[Run]:
        """Runs in the document's base formatting. ``text`` is the document's own
        words, or a list of (text, written by DocSan) pieces."""
        props = dict(self.plain.run)
        if bold:
            props["b"] = {"val": "1"}
        fmt = freeze(props, RUN)
        pieces = [(text, False)] if isinstance(text, str) else text
        return [Run(t, fmt, ours=ours) for t, ours in pieces if t]

    def plain_paragraph(self, text: str | list[tuple[str, bool]], kind: str = "body", level: int = 0, *,
                        bold: bool = False, numbered: bool = False) -> Paragraph:
        return Paragraph(self.plain_runs(text, bold), kind, level, numbered, freeze(self.plain.props, PARAGRAPH),
                         freeze(self.plain.mark, RUN))

    # -- styles --------------------------------------------------------------
    def load_styles(self, root: etree._Element) -> None:
        for st in root.iter(W + "style"):
            if st.get(W + "type") != "paragraph":
                continue
            sid = st.get(W + "styleId")
            if not sid:
                continue
            name_el = st.find(W + "name")
            name = (name_el.get(W + "val") or "").casefold() if name_el is not None else ""
            based_el = st.find(W + "basedOn")
            based = based_el.get(W + "val") if based_el is not None else None
            outline_el = st.find(f"{W}pPr/{W}outlineLvl")
            outline = None
            if outline_el is not None and (outline_el.get(W + "val") or "").isdigit():
                outline = int(outline_el.get(W + "val"))
            self.styles[sid] = (name, based, outline)

    def _style_kind(self, sid: str | None, depth: int = 0) -> tuple[str, int]:
        if not sid or sid not in self.styles or depth > 10:
            return "body", 0
        name, based, outline = self.styles[sid]
        if name == "title":
            return "title", 0
        m = re.fullmatch(r"heading (\d)", name)
        if m:
            return "heading", int(m.group(1))
        if outline is not None and outline <= 8:
            return "heading", outline + 1
        return self._style_kind(based, depth + 1)

    def _paragraph_kind(self, ppr: etree._Element | None, pf: ParaFormat) -> tuple[str, int, bool]:
        style = ppr.find(W + "pStyle") if ppr is not None else None
        kind, level = self._style_kind(style.get(W + "val") if style is not None else None)
        outline = ppr.find(W + "outlineLvl") if ppr is not None else None
        if outline is not None and (outline.get(W + "val") or "").isdigit() and int(outline.get(W + "val")) <= 8:
            kind, level = "heading", int(outline.get(W + "val")) + 1
        if kind in ("heading", "title"):
            return kind, min(level, 6), False
        if pf.num:
            return "list", pf.num[1], self.fmt.lists.numbered(*pf.num)
        return "body", 0, False

    # -- fields ------------------------------------------------------------
    def _in_code(self) -> bool:
        return any(f[0] == "instr" for f in self.fields)

    def _live(self) -> str:
        """The live field whose result is being read, if any."""
        return self.fields[-1][2] if self.fields and not self._in_code() else ""

    def _field_char(self, el: etree._Element) -> None:
        kind = el.get(W + "fldCharType")
        if kind == "begin":
            self.fields.append(["instr", "", ""])
        elif kind == "separate":
            if self.fields:
                self.fields[-1][0] = "result"
                self.fields[-1][2] = _live_field(self.fields[-1][1])
            else:
                self.block("field separator without a field")
        elif kind == "end":
            if self.fields:
                self.fields.pop()
            else:
                self.block("field end without a field")
        else:
            self.block(f"unknown field character type {kind!r}")

    # -- notes -------------------------------------------------------------
    def _note_marker(self, kind: str, note_id: str | None) -> str:
        if note_id is None:
            self.block(f"{kind} reference without an id")
            return ""
        key = (kind, note_id)
        if key not in self.note_numbers:
            self.note_numbers[key] = len(self.note_numbers) + 1
            self.note_queue.append(key)
        return f"[{self.note_numbers[key]}]"

    # -- sections and headers ------------------------------------------------
    def section(self, sectpr: etree._Element | None) -> Section:
        sec = Section(self.fmt.section(sectpr))
        for ref in sectpr if sectpr is not None else ():
            if ref.tag not in (W + "headerReference", W + "footerReference"):
                continue
            kind = "header" if ref.tag == W + "headerReference" else "footer"
            which = ref.get(W + "type", "default")
            target = self.part_rels.get(ref.get(R + "id") or "")
            if which not in ("default", "first", "even") or target is None or target[0] != kind:
                self.dropped[f"{kind} reference without its part"] += 1
                continue
            index = self.part_index.get(target[1].lower())
            if index is None:
                index = self.part_index[target[1].lower()] = len(self.parts)
                self.parts.append(HeaderFooter(kind))
                self.part_names.append(target[1])
            (sec.headers if kind == "header" else sec.footers)[which] = index
        return sec

    def read_parts(self) -> None:
        """Headers and footers, each with its own relationships, after the body."""
        for index, name in enumerate(self.part_names):
            self.use_relationships(self.relationships(name))
            self.where = name
            root = self.part(name)
            if root.tag not in (W + "hdr", W + "ftr"):
                raise _Blocked(f"{name}: not a header or footer")
            self.parts[index].blocks = self.blocks(root)
            if self.fields:
                self.block("field left open in a header or footer")
                self.fields = []
            self.media_read.add(name.lower())

    # -- content -----------------------------------------------------------
    def blocks(self, parent: etree._Element) -> list[Block]:
        out: list[Block] = []
        for child in parent:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == W + "p":
                out.extend(self.paragraph(child))
            elif tag == W + "tbl":
                out.append(self.table(child))
            elif tag == W + "sdt":
                self.dropped["content control properties"] += 1
                content = child.find(W + "sdtContent")
                if content is not None:
                    out.extend(self.blocks(content))
            elif tag in (W + "customXml", W + "ins", W + "moveTo"):
                out.extend(self.blocks(child))
            elif tag in (W + "del", W + "moveFrom"):
                if steps.enabled("drop-deleted-text"):
                    self.dropped["deleted text"] += 1
                else:
                    out.extend(self.blocks(child))
            elif tag in PROPERTIES or tag in MARKERS:
                continue
            elif tag == MC + "AlternateContent":
                choice = _first_choice(child)
                if choice is not None:
                    out.extend(self.blocks(choice))
            elif tag == W + "altChunk":
                self.block("imported chunk (altChunk)")
            elif tag in (M + "oMath", M + "oMathPara"):
                runs: list[Run] = []
                self.placeholder("equation", "[EQUATION-REMOVED]", runs)
                if runs:
                    out.append(Paragraph(runs, fmt=freeze(self.plain.props, PARAGRAPH)))
            else:
                self.block(f"unsupported element {_local(tag)}")
        return out

    def paragraph(self, p: etree._Element) -> list[Block]:
        ppr = p.find(W + "pPr")
        pf = self.fmt.paragraph(ppr, self.cell)
        kind, level, numbered = self._paragraph_kind(ppr, pf)
        runs: list[Run] = []
        extra: list[Block] = []
        saved, saved_drawings, self.para, self.drawings = self.para, self.drawings, pf, []
        try:
            self.inline(p, runs, extra)
            drawings = self.drawings
        finally:
            self.para, self.drawings = saved, saved_drawings
        sectpr = ppr.find(W + "sectPr") if ppr is not None else None
        para = Paragraph(
            _merge(runs), kind, level, numbered,
            fmt=freeze(pf.props, PARAGRAPH),
            mark=freeze(pf.mark, RUN),
            num=(self.fmt.lists.output(pf.num[0]), pf.num[1]) if pf.num else None,
            style=0 if kind in ("heading", "title") else self.fmt.output_style(pf.style),
            section=self.section(sectpr) if sectpr is not None else None,
            drawings=drawings,
        )
        for b in extra:
            if isinstance(b, Figure):
                b.fmt = para.fmt  # a picture keeps its paragraph's alignment and spacing
        # A paragraph that only held pictures becomes those pictures.
        if (extra and not para.text.strip() and para.section is None and para.num is None and not para.drawings
                and all(isinstance(b, Figure) for b in extra)):
            return extra
        return [para, *extra]

    def inline(self, parent: etree._Element, runs: list[Run], extra: list[Block]) -> None:
        for child in parent:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == W + "r":
                self.run(child, child, runs, extra)
            elif tag in (W + "hyperlink", W + "smartTag", W + "customXml", W + "dir", W + "bdo"):
                if tag == W + "hyperlink":
                    self.dropped["hyperlink target"] += 1
                self.inline(child, runs, extra)
            elif tag == W + "fldSimple":
                self.dropped["field code"] += 1
                instr = child.get(W + "instr", "")
                if not steps.enabled("drop-field-codes"):
                    runs.append(Run(instr))
                self.fields.append(["result", instr, _live_field(instr)])
                try:
                    self.inline(child, runs, extra)
                finally:
                    self.fields.pop()
            elif tag == W + "sdt":
                self.dropped["content control properties"] += 1
                content = child.find(W + "sdtContent")
                if content is not None:
                    self.inline(content, runs, extra)
            elif tag in (W + "ins", W + "moveTo"):
                self.inline(child, runs, extra)
            elif tag in (W + "del", W + "moveFrom"):
                if steps.enabled("drop-deleted-text"):
                    self.dropped["deleted text"] += 1
                else:
                    self.inline(child, runs, extra)
            elif tag in PROPERTIES or tag in MARKERS:
                continue
            elif tag == MC + "AlternateContent":
                choice = _first_choice(child)
                if choice is not None:
                    self.inline(choice, runs, extra)
            elif tag in (M + "oMath", M + "oMathPara"):
                self.placeholder("equation", "[EQUATION-REMOVED]", runs)
            elif tag == W + "subDoc":
                self.block("sub-document")
            else:
                self.block(f"unsupported element {_local(tag)}")

    def run(self, r: etree._Element, parent: etree._Element, runs: list[Run], extra: list[Block], fmt: Fmt | None = None) -> None:
        rpr = r.find(W + "rPr")
        hidden = rpr is not None and (_on(rpr.find(W + "vanish")) or _on(rpr.find(W + "specVanish")))
        if hidden:
            self.dropped["hidden text"] += 1
            if not steps.enabled("drop-hidden-text"):
                hidden = False
        if fmt is None:
            fmt = freeze(self.fmt.run(rpr, self.para), RUN)

        def add(text: str | None) -> None:
            if text:
                runs.append(Run(text, fmt, self._live()))

        for child in parent:
            tag = child.tag
            if not isinstance(tag, str) or tag == W + "rPr":
                continue
            if tag == W + "fldChar":
                self._field_char(child)
                continue
            visible = not hidden and not self._in_code()
            if tag == W + "t":
                if visible:
                    add(child.text)
            elif tag in (W + "instrText", W + "delInstrText"):
                if self.fields and self.fields[-1][0] == "instr":
                    self.fields[-1][1] += child.text or ""
                self.dropped["field code"] += 1
                if not steps.enabled("drop-field-codes") and not hidden:
                    runs.append(Run(child.text or "", fmt))
            elif tag == W + "delText":
                self.dropped["deleted text"] += 1
                if not steps.enabled("drop-deleted-text"):
                    add(child.text)
            elif tag == W + "tab":
                if visible:
                    add("\t")
            elif tag == W + "ptab":
                if visible:
                    add(POSITIONAL_TABS.get(child.get(W + "alignment", ""), "\t"))
            elif tag == W + "br":
                if visible:
                    add(BREAKS.get(child.get(W + "type", ""), "\n"))
            elif tag == W + "cr":
                if visible:
                    add("\n")
            elif tag == W + "noBreakHyphen":
                if visible:
                    add("-")
            elif tag == W + "softHyphen":
                continue
            elif tag == W + "sym":
                self.dropped["symbol"] += 1
            elif tag == W + "pgNum":
                if visible:
                    runs.append(Run("1", fmt, "PAGE"))
            elif tag in IGNORED_RUN_CONTENT:
                continue
            elif tag in (W + "footnoteReference", W + "endnoteReference"):
                kind = "footnote" if tag == W + "footnoteReference" else "endnote"
                marker = self._note_marker(kind, child.get(W + "id"))
                if visible:
                    add(marker)
            elif tag in (W + "drawing", W + "pict", W + "object"):
                if visible:
                    self.visual(child, runs, extra, fmt)
                else:
                    self.dropped["hidden drawing"] += 1
            elif tag == MC + "AlternateContent":
                choice = _first_choice(child)
                if choice is not None:
                    self.run(r, choice, runs, extra, fmt)
            elif tag == W + "ruby":
                self.block("ruby text")
            elif tag == W + "contentPart":
                if visible:
                    self.placeholder("ink content", "[DRAWING-REMOVED]", runs, fmt)
            else:
                self.block(f"unsupported run element {_local(tag)}")

    def placeholder(self, what: str, label: str, runs: list[Run], fmt: Fmt | None = None) -> None:
        """Visual content DocSan cannot read: a label when the owner chose labels, else it blocks."""
        if self.remove_images or self.label_unreadable:
            self.dropped[what] += 1
            runs.append(Run(label, fmt if fmt is not None else freeze(self.plain.run, RUN)))
        else:
            self.block(what)

    # The most specific placeholder wins when one drawing holds several kinds.
    _VISUAL_LABELS = (
        ("chart", "[CHART-REMOVED]"),
        ("SmartArt diagram", "[DIAGRAM-REMOVED]"),
        ("embedded object (OLE)", "[OBJECT-REMOVED]"),
        ("WordArt", "[DRAWING-REMOVED]"),
        ("unsupported drawing", "[DRAWING-REMOVED]"),
    )

    def visual(self, el: etree._Element, runs: list[Run], extra: list[Block], fmt: Fmt) -> None:
        if el.tag == W + "object":
            # An embedded object (a Visio drawing, a workbook): its data is
            # dropped, and the picture Word shows for it is masked instead.
            preview = next(el.iter(V + "imagedata"), None)
            if self.remove_images or preview is None:
                self.placeholder("embedded object (OLE)", "[OBJECT-REMOVED]", runs, fmt)
                return
            self.dropped["embedded object data"] += 1
            self.figure(el, preview, runs, extra, fmt)
            return
        image = False
        images: list[etree._Element] = []
        charts: list[etree._Element] = []
        diagrams: list[etree._Element] = []
        boxes: list[etree._Element] = []
        blockers: list[str] = []
        for kind, node in self._walk_visual(el):
            if kind == "textbox":
                boxes.append(node)
            elif kind == "image":
                image = True
                images.append(node)
            elif kind == "chart":
                charts.append(node)
            elif kind == "smartart":
                diagrams.append(node)
            else:
                blockers.append(str(node))
        if boxes:
            box = self.text_box(el, boxes)
            if box is not None:
                self.drawings.append(box)
            else:
                extra.extend(self.text_boxes(el, boxes))
        if self.remove_images:
            if charts:
                blockers.append("chart")
            if diagrams:
                blockers.append("SmartArt diagram")
        elif not (blockers and self.label_unreadable):
            for b in blockers:
                self.block(b)
            if not blockers:
                for node in charts:
                    extra.extend(self.chart(node))
                for node in diagrams:
                    extra.extend(self.smartart(node))
                for node in images:
                    self.figure(el, node, runs, extra, fmt)
            return
        for what, label in self._VISUAL_LABELS:
            if any(b.startswith(what) for b in blockers):
                self.dropped[what] += 1
                runs.append(Run(label, fmt))
                return
        if image:
            self.dropped["image"] += 1
            runs.append(Run("[IMAGE-REMOVED]", fmt))

    def text_boxes(self, el: etree._Element, boxes: list[etree._Element]) -> list[Block]:
        """Text in shapes, as paragraphs after the anchoring paragraph. When the
        shapes form a drawing (a group, a canvas or several shapes), a line says
        so, because the shapes and connectors themselves are not kept."""
        out: list[Block] = []
        saved_fields, saved_cell, self.fields, self.cell = self.fields, self.cell, [], None
        try:
            for node in boxes:
                out.extend(self.blocks(node))
                if self.fields:
                    self.block("field left open inside a text box")
                    self.fields = []
        finally:
            self.fields, self.cell = saved_fields, saved_cell
        shapes = sum(1 for _ in el.iter(WPS + "wsp", V + "shape", V + "rect", V + "roundrect", V + "oval"))
        drawing = any(True for _ in el.iter(WPG + "wgp", WPC + "wpc", V + "group", WPS + "cNvCnPr", V + "line")) or shapes > 1
        if drawing and any(isinstance(b, Paragraph) and b.text.strip() or not isinstance(b, Paragraph) for b in out):
            self.dropped["drawing shapes and connectors (text kept)"] += 1
            out.insert(0, self.plain_paragraph([("[Drawing: its text follows; the shapes and connectors are not kept]", True)], bold=True))
        return out

    def _box_blocks(self, node: etree._Element) -> list[Block]:
        saved_fields, saved_cell, self.fields, self.cell = self.fields, self.cell, [], None
        try:
            blocks = self.blocks(node)
            if self.fields:
                self.block("field left open inside a text box")
        finally:
            self.fields, self.cell = saved_fields, saved_cell
        return blocks

    def _color(self, el: etree._Element | None) -> str | None:
        """A DrawingML colour as RGB, with the brightness changes Word applies."""
        if el is None:
            return None
        value = None
        if el.tag == A + "srgbClr" and re.fullmatch(r"[0-9A-Fa-f]{6}", el.get("val", "")):
            value = el.get("val").upper()
        elif el.tag == A + "sysClr" and re.fullmatch(r"[0-9A-Fa-f]{6}", el.get("lastClr", "")):
            value = el.get("lastClr").upper()
        elif el.tag == A + "schemeClr":
            name = el.get("val", "")
            value = self.fmt.theme.colors.get(_SCHEME.get(name, name))
        if value is None:
            return None
        mod, off = el.find(A + "lumMod"), el.find(A + "lumOff")
        if mod is not None or off is not None:
            import colorsys

            r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
            h, lum, sat = colorsys.rgb_to_hls(r, g, b)
            m = int(mod.get("val", "100000")) / 100000 if mod is not None and _digits(mod.get("val")) else 1
            o = int(off.get("val", "0")) / 100000 if off is not None and _digits(off.get("val")) else 0
            r, g, b = colorsys.hls_to_rgb(h, min(max(lum * m + o, 0), 1), sat)
            value = "".join(f"{round(c * 255):02X}" for c in (r, g, b))
        return value

    def _fill(self, parent: etree._Element | None) -> tuple[bool, str | None]:
        """(specified, colour) for a shape's fill or outline."""
        if parent is None:
            return False, None
        if parent.find(A + "noFill") is not None:
            return True, None
        solid = parent.find(A + "solidFill")
        if solid is not None:
            return True, self._color(next((c for c in solid if isinstance(c.tag, str)), None))
        return False, None

    def text_box(self, el: etree._Element, boxes: list[etree._Element]) -> TextBox | None:
        """A single text box shape, kept with its place, size, fill and outline.
        Groups, canvases and anything with several shapes return None."""
        shapes = list(el.iter(WPS + "wsp"))
        if len(boxes) != 1 or len(shapes) != 1 or any(True for _ in el.iter(WPG + "wgp", WPC + "wpc")):
            return None
        container = next((c for c in el if c.tag in (WP + "inline", WP + "anchor")), None)
        if container is None:
            return None
        anchor = read_anchor(container) if container.tag == WP + "anchor" else None
        if container.tag == WP + "anchor" and anchor is None:
            return None
        extent = container.find(WP + "extent")
        cx, cy = (int(extent.get(k, "0") or 0) for k in ("cx", "cy")) if extent is not None else (0, 0)
        if cx <= 0 or cy <= 0:
            return None
        wsp = shapes[0]
        sppr = wsp.find(WPS + "spPr")
        geom = sppr.find(A + "prstGeom") if sppr is not None else None
        styled = wsp.find(WPS + "style") is not None
        has_fill, fill = self._fill(sppr)
        line_el = sppr.find(A + "ln") if sppr is not None else None
        has_line, line = self._fill(line_el)
        width = line_el.get("w", "") if line_el is not None else ""
        body = wsp.find(WPS + "bodyPr")
        body_attrs = []
        if body is not None:
            body_attrs += [(k, body.get(k)) for k in ("lIns", "tIns", "rIns", "bIns") if _digits(body.get(k))]
            if body.get("anchor") in ("t", "ctr", "b"):
                body_attrs.append(("anchor", body.get("anchor")))
            if body.get("vert") in ("horz", "vert", "vert270"):
                body_attrs.append(("vert", body.get("vert")))
            if body.get("wrap") in ("square", "none"):
                body_attrs.append(("wrap", body.get("wrap")))
        self.dropped["text box kept with its position"] += 1
        return TextBox(
            self._box_blocks(boxes[0]), cx, cy, anchor,
            shape=geom.get("prst") if geom is not None and geom.get("prst") in SHAPES else "rect",
            fill=fill if has_fill else ("FFFFFF" if styled else None),
            line=line if has_line else ("000000" if styled else None),
            line_width=int(width) if _digits(width) and int(width) <= 254000 else 9525,
            body=tuple(sorted(body_attrs)),
            autofit=body is not None and body.find(A + "spAutoFit") is not None,
        )

    def figure(self, drawing: etree._Element, node: etree._Element, runs: list[Run], extra: list[Block], fmt: Fmt) -> None:
        """An image to mask: kept as a figure after the paragraph it sits in."""
        rid = node.get(R + "embed") or node.get(R + "id")
        if node.get(R + "link") and not rid or rid in self.linked_images:
            self.dropped["linked image"] += 1  # its pixels are outside the file
            runs.append(Run("[IMAGE-REMOVED]", fmt))
            return
        part = self.image_rels.get(rid or "")
        if part is None:
            self.block(f"image {rid!r} has no image part")
            return
        data = self.media(part)
        kind = image_kind(data)
        if kind is None and vector_kind(data):
            kind = "vector"  # drawn by DocSan before it is read
        if kind is None:
            if self.label_unreadable:
                self.dropped["picture DocSan cannot read"] += 1
                runs.append(Run("[IMAGE-REMOVED]", fmt))
                return
            self.block("image in an unknown format")
            return
        width = height = 0
        extent = next(drawing.iter(WP + "extent"), None)
        if extent is not None:
            width, height = int(extent.get("cx", "0") or 0), int(extent.get("cy", "0") or 0)
        else:
            shape = node.getparent()
            style = shape.get("style", "") if shape is not None else ""
            width, height = _length_emu(style, "width"), _length_emu(style, "height")
        crop = (0.0, 0.0, 0.0, 0.0)
        fill = node.getparent()
        rect = fill.find(A + "srcRect") if fill is not None else None
        if rect is not None:
            crop = tuple(min(max(int(rect.get(k, "0") or 0), 0), 99000) / 100000 for k in ("l", "t", "r", "b"))  # type: ignore[assignment]
        if kind == "vector" and any(crop):
            # A drawing is cropped to what it draws; Word's crop would not line up.
            if self.label_unreadable:
                self.dropped["picture DocSan cannot read"] += 1
                runs.append(Run("[IMAGE-REMOVED]", fmt))
                return
            self.block("cropped vector drawing")
            return
        container = next(drawing.iter(WP + "anchor"), None)
        anchor = read_anchor(container) if container is not None else None
        if container is not None and anchor is None:
            self.dropped["floating picture placed in line"] += 1
        self.figure_count += 1
        figure = Figure(data, kind, width, height, crop, self.figure_count, anchor=anchor, run_fmt=fmt)
        if anchor is not None:
            self.drawings.append(figure)  # stays in its paragraph, where Word positions it from
        else:
            extra.append(figure)

    def _xml_part(self, name: str) -> etree._Element:
        self.media_read.add(name.lower())
        return self.part(name)

    def chart(self, node: etree._Element) -> list[Block]:
        """A chart becomes a table of the values it shows (decisions D-007 and D-022)."""
        part = self.chart_rels.get(node.get(R + "id") or "")
        if part is None:
            self.block("chart without its chart part")
            return []
        saved, self.where = self.where, part
        try:
            root = self._xml_part(part)
            shapes = [self._xml_part(t) for _, rtype, t, ext in self.relationships(part) if rtype == "chartUserShapes" and not ext]
        finally:
            self.where = saved
        data = read_chart(root, shapes)
        self.dropped["chart (kept as a table)"] += 1
        out: list[Block] = [self.plain_paragraph(data.heading, bold=True)]
        if data.axes:
            out.append(self.plain_paragraph(data.axes))
        for rows in data.tables:
            out.append(self.grid_table(rows))
        out += [self.plain_paragraph(note) for note in data.notes]
        return out

    def grid_table(self, rows: list[list[list[tuple[str, bool]]]]) -> Table:
        """A table DocSan writes itself, with plain borders."""
        border = {"val": "single", "sz": "4", "space": "0", "color": "auto"}
        props = {f"tblBorders/{side}": border for side in ("top", "left", "bottom", "right", "insideH", "insideV")}
        props["tblW"] = {"w": "0", "type": "auto"}
        header = dict(self.plain.run)
        header["b"] = {"val": "1"}

        def cell(pieces: list[tuple[str, bool]], bold: bool) -> Cell:
            p = self.plain_paragraph(pieces)
            fmt = freeze(header if bold else self.plain.run, RUN)
            p.runs = [Run(t, fmt, ours=o) for t, o in pieces if t]
            return Cell([p])

        return Table([[cell(t, r == 0) for t in row] for r, row in enumerate(rows)], freeze(props, TABLE))

    def smartart(self, node: etree._Element) -> list[Block]:
        """A SmartArt diagram becomes a list of its text, nested as drawn and
        numbered when its layout is a process or a cycle."""
        part = self.diagram_rels.get(node.get(R + "dm") or "")
        if part is None:
            self.block("SmartArt diagram without its data part")
            return []
        layout_part = self.layout_rels.get(node.get(R + "lo") or "")
        saved, self.where = self.where, part
        try:
            root = self._xml_part(part)
            layout = self._xml_part(layout_part) if layout_part and layout_part.lower() in self.names else None
        finally:
            self.where = saved
        diagram = read_smartart(root, layout)
        self.dropped["SmartArt (kept as a list)"] += 1
        if not diagram.items:
            return []
        numbered = diagram.category in ("process", "cycle")
        out: list[Block] = [self.plain_paragraph(diagram.heading, bold=True)]
        for depth, text in diagram.items:
            out.append(self.plain_paragraph(text, "list", min(depth, 8), numbered=numbered and depth == 0))
        out += [self.plain_paragraph(note) for note in diagram.notes]
        return out

    def _walk_visual(self, el: etree._Element):
        for child in el:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == MC + "AlternateContent":
                choice = _first_choice(child)
                if choice is not None:
                    yield from self._walk_visual(choice)
                continue
            if tag == W + "txbxContent":
                yield "textbox", child
                continue
            if tag in (A + "blip", V + "imagedata"):
                yield "image", child
            elif tag == CHART + "chart":
                yield "chart", child
            elif tag == DGM + "relIds":
                yield "smartart", child
            elif tag == A + "graphicData" and child.get("uri") not in KNOWN_GRAPHICS:
                yield "block", f"unsupported drawing ({child.get('uri')})"
            elif tag == O + "OLEObject":
                yield "block", "embedded object (OLE)"
            elif tag == V + "textpath":
                yield "block", "WordArt"
            yield from self._walk_visual(child)

    # -- tables --------------------------------------------------------------
    def table(self, tbl: etree._Element) -> Table:
        tblpr = tbl.find(W + "tblPr")
        ctx = self.fmt.table(tblpr)
        grid_el = tbl.find(W + "tblGrid")
        grid = []
        for col in grid_el.findall(W + "gridCol") if grid_el is not None else ():
            w = col.get(W + "w", "")
            grid.append(int(float(w)) if re.fullmatch(r"\d{1,7}(\.\d+)?", w) else 0)
        trs = list(self._row_elements(tbl))
        widths = [self._row_width(tr) for tr in trs]
        cols = max([len(grid), *widths, 1])
        rows: list[list[Cell]] = []
        row_fmts: list[Fmt] = []
        saved = self.cell
        try:
            for r, tr in enumerate(trs):
                trpr = tr.find(W + "trPr")
                row_types, _ = cell_types(ctx, r, len(trs), 0, cols - 1, cols)
                row_fmts.append(freeze(self.fmt.row(trpr, ctx, [t for t in row_types if not t.endswith(("Vert", "Col", "Cell"))]), ROW))
                col = self._grid_value(trpr, "gridBefore")
                cells: list[Cell] = []
                for tc in self._cell_elements(tr):
                    tcpr = tc.find(W + "tcPr")
                    span = max(self._grid_value(tcpr, "gridSpan"), 1)
                    types, edges = cell_types(ctx, r, len(trs), col, col + span - 1, cols)
                    self.cell = CellContext(ctx, types)
                    blocks = self.blocks(tc)
                    cells.append(Cell(blocks, freeze(self.fmt.cell(tcpr, self.cell, edges), CELL)))
                    col += span
                rows.append(cells)
        finally:
            self.cell = saved
        return Table(rows, freeze(ctx.props, TABLE), grid, row_fmts)

    @staticmethod
    def _grid_value(props: etree._Element | None, tag: str) -> int:
        el = props.find(W + tag) if props is not None else None
        v = el.get(W + "val", "") if el is not None else ""
        return min(int(v), 64) if v.isdigit() else 0

    def _row_width(self, tr: etree._Element) -> int:
        trpr = tr.find(W + "trPr")
        width = self._grid_value(trpr, "gridBefore") + self._grid_value(trpr, "gridAfter")
        for tc in self._cell_elements(tr, report=False):
            width += max(self._grid_value(tc.find(W + "tcPr"), "gridSpan"), 1)
        return width

    def _row_elements(self, parent: etree._Element):
        for child in parent:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == W + "tr":
                trpr = child.find(W + "trPr")
                if trpr is not None and trpr.find(W + "del") is not None and steps.enabled("drop-deleted-text"):
                    self.dropped["deleted row"] += 1
                    continue
                yield child
            elif tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    yield from self._row_elements(content)
            elif tag == W + "customXml":
                yield from self._row_elements(child)
            elif tag in PROPERTIES or tag in MARKERS:
                continue
            else:
                self.block(f"unsupported table element {_local(tag)}")

    def _cell_elements(self, parent: etree._Element, report: bool = True):
        for child in parent:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == W + "tc":
                yield child
            elif tag == W + "sdt":
                content = child.find(W + "sdtContent")
                if content is not None:
                    yield from self._cell_elements(content, report)
            elif tag == W + "customXml":
                yield from self._cell_elements(child, report)
            elif tag in PROPERTIES or tag in MARKERS:
                continue
            elif report:
                self.block(f"unsupported row element {_local(tag)}")

    def notes_section(self, parts: dict[str, str]) -> list[Block]:
        out: list[Block] = []
        loaded: set[str] = set()
        i = 0
        while i < len(self.note_queue):
            kind, note_id = self.note_queue[i]
            i += 1
            part_name = parts.get(kind)
            if part_name is None:
                self.block(f"{kind} {note_id} referenced but the {kind}s part is missing")
                continue
            if kind not in loaded:
                self.where = part_name
                root = self.part(part_name)
                for note in root.iter(W + kind):
                    self.notes[kind][note.get(W + "id", "")] = note
                loaded.add(kind)
            note = self.notes[kind].get(note_id)
            self.where = part_name
            if note is None:
                self.block(f"{kind} {note_id} is missing")
                continue
            blocks = self.blocks(note)
            first = next((b for b in blocks if isinstance(b, Paragraph)), None)
            marker = Run(f"[{self.note_numbers[(kind, note_id)]}] ", first.runs[0].fmt if first and first.runs else freeze(self.plain.run, RUN))
            if first is None:
                blocks.insert(0, Paragraph([marker], fmt=freeze(self.plain.props, PARAGRAPH)))
            else:
                first.runs.insert(0, marker)
                first.kind, first.level = "body", 0
            out.extend(blocks)
        if out:
            out.insert(0, self.plain_paragraph([("Notes", True)], "heading", 2, bold=True))
        return out

    def settings(self, root: etree._Element | None) -> Settings:
        if root is None:
            return Settings()
        tab = root.find(W + "defaultTabStop")
        value = tab.get(W + "val", "") if tab is not None else ""
        compat = next((c.get(W + "val", "") for c in root.iter(W + "compatSetting") if c.get(W + "name") == "compatibilityMode"), "")
        return Settings(
            tab_stop=int(value) if value.isdigit() and 0 < int(value) <= 31680 else 720,
            even_and_odd_headers=_on(root.find(W + "evenAndOddHeaders")),
            hyphenation=_on(root.find(W + "autoHyphenation")),
            mirror_margins=_on(root.find(W + "mirrorMargins")),
            compatibility=int(compat) if compat in ("11", "12", "14", "15") else 15,
        )


def _package_problems(path: Path) -> tuple[zipfile.ZipFile | None, list[str]]:
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError as exc:
        return None, [f"cannot read file: {exc}"]
    if head.startswith(CFB_MAGIC):
        return None, ["encrypted or legacy binary Word file (conversion arrives in a later milestone)"]
    if not head.startswith(ZIP_MAGIC):
        return None, ["not a Word package"]
    try:
        zf = zipfile.ZipFile(path)
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError) as exc:
        return None, [f"unreadable or truncated package ({exc})"]
    problems: list[str] = []
    if len(infos) > MAX_ENTRIES:
        problems.append("too many package entries")
    seen: set[str] = set()
    total = 0
    for info in infos:
        low = info.filename.lower()
        if low in seen:
            problems.append(f"duplicate entry {info.filename}")
        seen.add(low)
        if info.flag_bits & 0x1:
            problems.append(f"encrypted entry {info.filename}")
        total += info.file_size
        if info.file_size > 1024 * 1024 and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            problems.append(f"suspicious compression ratio in {info.filename}")
    if total > MAX_TOTAL_BYTES:
        problems.append("package too large when uncompressed")
    if problems:
        zf.close()
        return None, problems
    return zf, []


def read_docx(path: Path, *, remove_images: bool = False, label_unreadable: bool = False) -> ReadResult:
    """``remove_images`` turns every visual into a label; ``label_unreadable``
    keeps every visual DocSan can read and labels only the others."""
    zf, problems = _package_problems(path)
    if zf is None:
        return ReadResult(None, problems)
    with zf:
        names = {i.filename.lower(): i.filename for i in zf.infolist() if not i.filename.endswith("/")}
        reader = _Reader(zf, names, remove_images, label_unreadable)
        inventory: dict[str, str] = {}
        try:
            reader.where = "[Content_Types].xml"
            types = reader.part("[Content_Types].xml")
            overrides = {
                (o.get("PartName") or "").lstrip("/").lower(): o.get("ContentType") or ""
                for o in types.iter(CT + "Override")
            }
            reader.where = "_rels/.rels"
            rels = reader.part("_rels/.rels")
            main = None
            for rel in rels.iter(PKG_REL + "Relationship"):
                rtype = rel.get("Type") or ""
                if any(m in rtype for m in STRICT_MARKERS):
                    raise _Blocked("Strict Open XML is not supported")
                if rtype == REL_BASE + "officeDocument":
                    main = _resolve("", rel.get("Target") or "")
            if not main:
                raise _Blocked("no main document relationship")
            if overrides.get(main.lower()) not in MAIN_TYPES:
                raise _Blocked(f"unexpected main document type {overrides.get(main.lower())!r}")
            inventory.update({"[content_types].xml": "read", "_rels/.rels": "read", main.lower(): "read"})

            main_rels = reader.relationships(main)
            related: dict[str, str] = {}
            for rid, rtype, target, external in main_rels:
                if external:
                    continue
                if rtype in ("styles", "numbering", "footnotes", "endnotes", "settings", "fontTable", "theme"):
                    related[rtype] = target
                elif rtype in ("header", "footer"):
                    reader.part_rels[rid] = (rtype, target)
            reader.use_relationships(main_rels)

            loaded: dict[str, etree._Element | None] = {}
            for key in ("styles", "numbering", "theme", "fontTable", "settings"):
                loaded[key] = None
                if key in related and related[key].lower() in names:
                    reader.where = related[key]
                    loaded[key] = reader.part(related[key])
                    inventory[related[key].lower()] = "read"
            reader.setup(loaded["styles"], loaded["theme"], loaded["fontTable"], loaded["numbering"])
            if loaded["styles"] is not None:
                reader.load_styles(loaded["styles"])

            reader.where = main
            root = reader.part(main)
            body = root.find(W + "body")
            if root.tag != W + "document" or body is None:
                raise _Blocked(f"{main}: not a Word document body")
            blocks = reader.blocks(body)
            if reader.fields:
                reader.block("field left open at the end of the document")
            final = reader.section(body.find(W + "sectPr"))
            note_parts = {k.rstrip("s"): v for k, v in related.items() if k in ("footnotes", "endnotes")}
            blocks.extend(reader.notes_section(note_parts))
            for k in ("footnotes", "endnotes"):
                if k in related:
                    inventory[related[k].lower()] = "read"
            reader.read_parts()
            settings = reader.settings(loaded["settings"])
        except _Blocked as exc:
            return ReadResult(None, [*reader.blocking, str(exc)])

        referenced = set(overrides)
        try:
            for low, actual in names.items():
                if low.endswith(".rels"):
                    base = "" if low == "_rels/.rels" else str(PurePosixPath(actual).parent.parent)
                    root = reader.part(actual)
                    for rel in root.iter(PKG_REL + "Relationship"):
                        if rel.get("TargetMode") != "External":
                            referenced.add(_resolve(base, unquote(rel.get("Target") or "")).lower())
        except _Blocked as exc:
            return ReadResult(None, [*reader.blocking, str(exc)])
        for low in reader.media_read:
            inventory[low] = "read"
        for low, actual in names.items():
            if low in inventory:
                continue
            if re.fullmatch(r"\[trash\]/[0-9]{4}\.dat", low) and low not in referenced:
                inventory[low] = "removed"
                reader.dropped["unreferenced package trash"] += 1
            elif any(p.fullmatch(low) for p in REMOVED_PARTS):
                inventory[low] = "removed"
            else:
                inventory[low] = "blocked"
                reader.blocking.append(f"unknown package part {actual}")

    if reader.fmt.fonts.replaced:
        reader.dropped["font replaced by a common font"] = len(reader.fmt.fonts.replaced)
    if reader.blocking:
        return ReadResult(None, reader.blocking, inventory, dict(reader.dropped))
    doc = Document(blocks, final, reader.parts, reader.fmt.lists.model(), len(reader.fmt.output_styles), settings)
    return ReadResult(doc, [], inventory, dict(reader.dropped))
