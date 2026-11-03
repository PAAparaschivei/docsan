"""Write a fresh DOCX package from the content model.

The package holds content types, package relationships, the document and its
relationships, styles, numbering and settings, then one part per header and
footer and one media part per figure. Package relationships are fixed bytes;
the styles part is fixed apart from the number of neutral paragraph styles
(Style1, Style2, ...) it declares. Formatting is written as direct formatting
from the model's allowlisted properties (decision D-023). Figures are inline
pictures named "Picture N" with no description. ZIP timestamps are fixed so
the same content gives the same bytes and no processing time leaks into the
file.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .format import CELL, LEVEL_PARAGRAPH, PARAGRAPH, ROW, RUN, SECTION, TABLE, to_xml
from .model import (
    Anchor,
    Block,
    Document,
    Figure,
    Fmt,
    HeaderFooter,
    Level,
    Numbering,
    Paragraph,
    Run,
    Section,
    Settings,
    Table,
    TextBox,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WPS_NS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = "application/vnd.openxmlformats-officedocument.wordprocessingml."
NAMESPACES = f'xmlns:w="{W_NS}" xmlns:wp="{WP_NS}" xmlns:a="{A_NS}" xmlns:pic="{PIC_NS}" xmlns:wps="{WPS_NS}" xmlns:r="{R_NS}"'
MAX_EXTENT_EMU = 20_000_000  # about 55 cm: larger sizes are not real
_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

PACKAGE_RELS = (
    _DECL
    + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def part_name(index: int, part: HeaderFooter) -> str:
    """A header or footer is named by its place in Document.parts: header1, footer2, ..."""
    return f"{part.kind}{index + 1}.xml"


def content_types(parts: list[HeaderFooter]) -> str:
    overrides = [("document.xml", "document.main+xml"), ("styles.xml", "styles+xml"), ("numbering.xml", "numbering+xml"),
                 ("settings.xml", "settings+xml")]
    overrides += [(part_name(i, p), f"{p.kind}+xml") for i, p in enumerate(parts)]
    return (
        _DECL
        + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Default Extension="jpg" ContentType="image/jpeg"/>'
        + "".join(f'<Override PartName="/word/{name}" ContentType="{CT}{kind}"/>' for name, kind in overrides)
        + "</Types>"
    )


def media_name(n: int, figure: Figure) -> str:
    return f"media/image{n}.{'jpg' if figure.kind == 'jpeg' else 'png'}"


def _relationships(rels: list[tuple[str, str, str]]) -> str:
    return (
        _DECL
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(f'<Relationship Id="{i}" Type="{REL}{t}" Target="{target}"/>' for i, t, target in rels)
        + "</Relationships>"
    )


def document_rels(images: list[tuple[int, Figure]], parts: list[HeaderFooter] = ()) -> str:  # type: ignore[assignment]
    rels = [("rId1", "styles", "styles.xml"), ("rId2", "numbering", "numbering.xml"), ("rId3", "settings", "settings.xml")]
    rels += [(f"rId{'Hdr' if p.kind == 'header' else 'Ftr'}{i + 1}", p.kind, part_name(i, p)) for i, p in enumerate(parts)]
    rels += [(f"rIdImg{n}", "image", media_name(n, f)) for n, f in images]
    return _relationships(rels)


def _heading_style(level: int) -> str:
    return (
        f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
        f'<w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
        f'<w:pPr><w:outlineLvl w:val="{level - 1}"/></w:pPr></w:style>'
    )


# Neutral styles carry no formatting: every paragraph and run states its own.
STYLES_HEAD = (
    _DECL
    + f'<w:styles xmlns:w="{W_NS}">'
    '<w:docDefaults><w:rPrDefault><w:rPr/></w:rPrDefault><w:pPrDefault><w:pPr/></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
    + "".join(_heading_style(level) for level in range(1, 7))
    + '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
    '<w:tblPr><w:tblCellMar><w:left w:w="108" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
)
STYLES_TAIL = "</w:styles>"


def styles_xml(count: int) -> str:
    """The fixed styles, then Style1..StyleN: one neutral style per original
    paragraph style, so Word still treats paragraphs of one style alike."""
    extra = "".join(f'<w:style w:type="paragraph" w:styleId="Style{i}"><w:name w:val="Style {i}"/></w:style>' for i in range(1, count + 1))
    return STYLES_HEAD + extra + STYLES_TAIL


def settings_xml(s: Settings) -> str:
    return (
        _DECL
        + f'<w:settings xmlns:w="{W_NS}">'
        + ("<w:mirrorMargins/>" if s.mirror_margins else "")
        + f'<w:defaultTabStop w:val="{s.tab_stop}"/>'
        + ("<w:autoHyphenation/>" if s.hyphenation else "")
        + ("<w:evenAndOddHeaders/>" if s.even_and_odd_headers else "")
        + '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" '
        f'w:val="{s.compatibility}"/></w:compat></w:settings>'
    )


_INVALID_XML = re.compile("[\x00-\x08\x0b\x0e-\x1b￾￿]")


def _clean(text: str) -> str:
    return _INVALID_XML.sub("", text.replace("\x0b", "\n"))


def _attr(text: str) -> str:
    return escape(_clean(text).replace("\f", "").replace("\x1c", "").replace("\x1d", "").replace("\x1e", "").replace("\x1f", ""), {'"': "&quot;"})


def _fixed_levels(fmt: str) -> str:
    out = []
    for i in range(9):
        text = "•" if fmt == "bullet" else f"%{i + 1}."
        out.append(
            f'<w:lvl w:ilvl="{i}"><w:start w:val="1"/><w:numFmt w:val="{fmt}"/><w:lvlText w:val="{text}"/>'
            f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{720 * (i + 1)}" w:hanging="360"/></w:pPr></w:lvl>'
        )
    return "".join(out)


def _level_xml(lvl: Level) -> str:
    out = f'<w:lvl w:ilvl="{lvl.ilvl}"><w:start w:val="{lvl.start}"/><w:numFmt w:val="{lvl.format}"/>'
    if lvl.restart is not None:
        out += f'<w:lvlRestart w:val="{lvl.restart}"/>'
    if lvl.legal:
        out += "<w:isLgl/>"
    out += f'<w:suff w:val="{lvl.suffix}"/><w:lvlText w:val="{_attr(lvl.text)}"/><w:lvlJc w:val="{lvl.justify}"/>'
    if lvl.paragraph:
        out += f"<w:pPr>{to_xml(lvl.paragraph, LEVEL_PARAGRAPH)}</w:pPr>"
    if lvl.run:
        out += f"<w:rPr>{to_xml(lvl.run, RUN)}</w:rPr>"
    return out + "</w:lvl>"


def numbering_xml(numbering: Numbering) -> str:
    """DocSan's own bullets (1) and numbers (2), then the document's lists (3 on)."""
    abstract = [
        f'<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>{_fixed_levels("bullet")}</w:abstractNum>',
        f'<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>{_fixed_levels("decimal")}</w:abstractNum>',
    ]
    for i, d in enumerate(numbering.definitions):
        levels = "".join(_level_xml(lvl) for lvl in d.levels)
        abstract.append(f'<w:abstractNum w:abstractNumId="{i + 2}"><w:multiLevelType w:val="{d.multi}"/>{levels}</w:abstractNum>')
    nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>', '<w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>']
    for i, inst in enumerate(numbering.instances):
        overrides = ""
        for ilvl, start, lvl in inst.overrides:
            body = (f'<w:startOverride w:val="{start}"/>' if start is not None else "") + (_level_xml(lvl) if lvl else "")
            overrides += f'<w:lvlOverride w:ilvl="{ilvl}">{body}</w:lvlOverride>'
        nums.append(f'<w:num w:numId="{i + 3}"><w:abstractNumId w:val="{inst.definition + 2}"/>{overrides}</w:num>')
    return _DECL + f'<w:numbering xmlns:w="{W_NS}">' + "".join(abstract) + "".join(nums) + "</w:numbering>"


# --- Content --------------------------------------------------------------------

_SPECIAL = re.compile("(\t|\n|\f|\x1c|\x1d|\x1e|\x1f)")
_SPECIAL_XML = {
    "\t": "<w:tab/>",
    "\n": "<w:br/>",
    "\f": '<w:br w:type="page"/>',
    "\x1d": '<w:br w:type="column"/>',
    "\x1c": '<w:ptab w:relativeTo="margin" w:alignment="left" w:leader="none"/>',
    "\x1e": '<w:ptab w:relativeTo="margin" w:alignment="center" w:leader="none"/>',
    "\x1f": '<w:ptab w:relativeTo="margin" w:alignment="right" w:leader="none"/>',
}


def _run_xml(run: Run) -> str:
    props = to_xml(run.fmt, RUN)
    parts: list[str] = []
    for piece in _SPECIAL.split(_clean(run.text)):
        if piece in _SPECIAL_XML:
            parts.append(_SPECIAL_XML[piece])
        elif piece:
            parts.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
    return f"<w:r>{f'<w:rPr>{props}</w:rPr>' if props else ''}{''.join(parts)}</w:r>" if parts else ""


def _runs_xml(runs: list[Run]) -> str:
    """Runs, with each live field's runs inside a simple field."""
    out: list[str] = []
    i = 0
    while i < len(runs):
        if runs[i].field:
            j = i
            while j < len(runs) and runs[j].field == runs[i].field:
                j += 1
            out.append(f'<w:fldSimple w:instr="{_attr(runs[i].field)}">{"".join(_run_xml(r) for r in runs[i:j])}</w:fldSimple>')
            i = j
        else:
            out.append(_run_xml(runs[i]))
            i += 1
    return "".join(out)


_EARLY = ("keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl")  # before numPr in the schema


def _frame(anchor: Anchor | None, cx: int, cy: int, doc_id: int, name: str, graphic: str) -> str:
    """The wp:inline or wp:anchor around a picture or a text box."""
    common = f'<wp:extent cx="{cx}" cy="{cy}"/>'
    if anchor is None:
        return f'<wp:inline>{common}<wp:docPr id="{doc_id}" name="{name}"/>{graphic}</wp:inline>'
    attrs = "".join(f' {k}="{v}"' for k, v in anchor.attrs)
    position = "".join(
        f'<wp:position{axis} relativeFrom="{rel}"><wp:{kind}>{value}</wp:{kind}></wp:position{axis}>'
        for axis, (rel, kind, value) in (("H", anchor.horizontal), ("V", anchor.vertical))
    )
    effect = "".join(f' {k}="{v}"' for k, v in zip("ltrb", anchor.effect)) if anchor.effect else ""
    wrap_name, wrap_attrs = anchor.wrap
    wrap = f"<wp:{wrap_name}{''.join(f' {k}={chr(34)}{v}{chr(34)}' for k, v in wrap_attrs)}/>"
    return (
        f'<wp:anchor{attrs}><wp:simplePos x="0" y="0"/>{position}{common}'
        + (f"<wp:effectExtent{effect}/>" if effect else "")
        + f'{wrap}<wp:docPr id="{doc_id}" name="{name}"/>{graphic}</wp:anchor>'
    )


class _Writer:
    def __init__(self, doc: Document):
        self.doc = doc
        self.numbers = {id(f): n for n, f in enumerate(doc.figures(), 1)}
        self.images: list[tuple[int, Figure]] = []  # pictures of the part being written
        self.boxes = 0

    def ppr(self, fmt: Fmt, style: str = "", num: tuple[int, int] | None = None, mark: Fmt = (), section: Section | None = None) -> str:
        early = tuple(e for e in fmt if e[0] in _EARLY)
        late = tuple(e for e in fmt if e[0] not in _EARLY)
        inner = f'<w:pStyle w:val="{style}"/>' if style else ""
        inner += to_xml(early, PARAGRAPH)
        if num:
            inner += f'<w:numPr><w:ilvl w:val="{min(max(num[1], 0), 8)}"/><w:numId w:val="{num[0]}"/></w:numPr>'
        inner += to_xml(late, PARAGRAPH)
        marks = to_xml(mark, RUN)
        if marks:
            inner += f"<w:rPr>{marks}</w:rPr>"
        if section is not None:
            inner += self.section(section)
        return f"<w:pPr>{inner}</w:pPr>" if inner else ""

    def paragraph(self, p: Paragraph) -> str:
        if p.kind == "title":
            style = "Title"
        elif p.kind == "heading":
            style = f"Heading{min(max(p.level, 1), 6)}"
        else:
            style = f"Style{p.style}" if p.style else ""
        num = p.num
        if num is None and p.kind == "list":
            num = (2 if p.numbered else 1, p.level)
        drawings = "".join(self.picture(d) if isinstance(d, Figure) else self.text_box(d) for d in p.drawings)
        return f"<w:p>{self.ppr(p.fmt, style, num, p.mark, p.section)}{drawings}{_runs_xml(p.runs)}</w:p>"

    def text_box(self, box: TextBox) -> str:
        self.boxes += 1
        n = self.boxes
        fill = f'<a:solidFill><a:srgbClr val="{box.fill}"/></a:solidFill>' if box.fill else "<a:noFill/>"
        line = (f'<a:ln w="{box.line_width}"><a:solidFill><a:srgbClr val="{box.line}"/></a:solidFill></a:ln>' if box.line
                else "<a:ln><a:noFill/></a:ln>")
        body = "".join(f' {k}="{v}"' for k, v in box.body)
        inner = self.blocks(box.blocks)
        if not box.blocks or not isinstance(box.blocks[-1], (Paragraph, Figure)):
            inner += "<w:p/>"
        graphic = (
            f'<a:graphic><a:graphicData uri="{WPS_NS}"><wps:wsp><wps:cNvSpPr txBox="1"/>'
            f'<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{box.width_emu}" cy="{box.height_emu}"/></a:xfrm>'
            f'<a:prstGeom prst="{box.shape}"><a:avLst/></a:prstGeom>{fill}{line}</wps:spPr>'
            f"<wps:txbx><w:txbxContent>{inner}</w:txbxContent></wps:txbx>"
            f'<wps:bodyPr{body}>{"<a:spAutoFit/>" if box.autofit else "<a:noAutofit/>"}</wps:bodyPr>'
            "</wps:wsp></a:graphicData></a:graphic>"
        )
        return f"<w:r><w:drawing>{_frame(box.anchor, box.width_emu, box.height_emu, 10000 + n, f'Text Box {n}', graphic)}</w:drawing></w:r>"

    def section(self, sec: Section) -> str:
        refs = ""
        for kind, which in (("header", sec.headers), ("footer", sec.footers)):
            for t in ("default", "first", "even"):
                if t in which:
                    refs += f'<w:{kind}Reference w:type="{t}" r:id="rId{"Hdr" if kind == "header" else "Ftr"}{which[t] + 1}"/>'
        props = to_xml(sec.fmt, SECTION)
        if not any(k == "pgSz" for k, _ in sec.fmt):
            props = ('<w:pgSz w:w="11906" w:h="16838"/>'
                     '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>'
                     + props)
        return f"<w:sectPr>{refs}{props}</w:sectPr>"

    def table(self, t: Table) -> str:
        if not any(t.rows):
            return ""
        spans = [sum(max(int(dict(dict(c.fmt).get("gridSpan", ())).get("val", "1") or 1), 1) for c in row) for row in t.rows]
        cols = max([len(t.grid), *spans, 1])
        grid = t.grid if len(t.grid) == cols and all(t.grid) else [9000 // cols] * cols
        rows = []
        for i, row in enumerate(t.rows):
            if not row:
                continue
            cells = []
            for cell in row:
                inner = self.blocks(cell.blocks)
                if not cell.blocks or not isinstance(cell.blocks[-1], (Paragraph, Figure)):
                    inner += "<w:p/>"
                props = to_xml(cell.fmt, CELL)
                cells.append(f"<w:tc>{f'<w:tcPr>{props}</w:tcPr>' if props else ''}{inner}</w:tc>")
            row_props = to_xml(t.row_fmts[i], ROW) if i < len(t.row_fmts) else ""
            rows.append(f"<w:tr>{f'<w:trPr>{row_props}</w:trPr>' if row_props else ''}{''.join(cells)}</w:tr>")
        props = to_xml(t.fmt, TABLE)
        if not any(k == "tblW" for k, _ in t.fmt):
            props = '<w:tblW w:w="0" w:type="auto"/>' + props
        gridcols = "".join(f'<w:gridCol w:w="{w}"/>' for w in grid)
        return f"<w:tbl><w:tblPr>{props}</w:tblPr><w:tblGrid>{gridcols}</w:tblGrid>{''.join(rows)}</w:tbl>"

    def picture(self, f: Figure) -> str:
        """A picture as a run: in line with text, or floating where it was."""
        n = self.numbers[id(f)]
        if f.output is None:
            raise ValueError(f"figure {f.index} has no masked image")
        self.images.append((n, f))
        cx, cy = f.width_emu, f.height_emu
        if cx <= 0 or cy <= 0:
            cx, cy = 3 * 914400, 2 * 914400  # no size recorded: 3 by 2 inches
        if cx > MAX_EXTENT_EMU:
            cx, cy = MAX_EXTENT_EMU, round(cy * MAX_EXTENT_EMU / cx)
        cy = min(cy, MAX_EXTENT_EMU)
        graphic = (
            f'<a:graphic><a:graphicData uri="{PIC_NS}"><pic:pic>'
            f'<pic:nvPicPr><pic:cNvPr id="{n}" name="Picture {n}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="rIdImg{n}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic>"
        )
        return f"<w:r><w:drawing>{_frame(f.anchor, cx, cy, n, f'Picture {n}', graphic)}</w:drawing></w:r>"

    def figure(self, f: Figure) -> str:
        return f"<w:p>{self.ppr(f.fmt)}{self.picture(f)}</w:p>"

    def blocks(self, blocks: list[Block]) -> str:
        out: list[str] = []
        for i, b in enumerate(blocks):
            if isinstance(b, Paragraph):
                out.append(self.paragraph(b))
            elif isinstance(b, Figure):
                out.append(self.figure(b))
            else:
                xml = self.table(b)
                if xml:
                    out.append(xml)
                    if i + 1 == len(blocks) or not isinstance(blocks[i + 1], (Paragraph, Figure)):
                        out.append("<w:p/>")
        return "".join(out)

    def document(self) -> str:
        self.images = []
        body = self.blocks(self.doc.blocks)
        return _DECL + f"<w:document {NAMESPACES}><w:body>{body}{self.section(self.doc.section)}</w:body></w:document>"

    def part(self, part: HeaderFooter) -> str:
        self.images = []
        tag = "hdr" if part.kind == "header" else "ftr"
        body = self.blocks(part.blocks)
        if not part.blocks or not isinstance(part.blocks[-1], (Paragraph, Figure)):
            body += "<w:p/>"
        return _DECL + f"<w:{tag} {NAMESPACES}>{body}</w:{tag}>"


def document_xml(doc: Document) -> str:
    return _Writer(doc).document()


def package_bytes(doc: Document) -> dict[str, bytes]:
    writer = _Writer(doc)
    parts: dict[str, bytes] = {
        "[Content_Types].xml": content_types(doc.parts).encode("utf-8"),
        "_rels/.rels": PACKAGE_RELS.encode("utf-8"),
        "word/document.xml": writer.document().encode("utf-8"),
    }
    parts["word/_rels/document.xml.rels"] = document_rels(writer.images, doc.parts).encode("utf-8")
    parts["word/styles.xml"] = styles_xml(doc.styles).encode("utf-8")
    parts["word/numbering.xml"] = numbering_xml(doc.numbering).encode("utf-8")
    parts["word/settings.xml"] = settings_xml(doc.settings).encode("utf-8")
    for i, p in enumerate(doc.parts):
        name = part_name(i, p)
        parts[f"word/{name}"] = writer.part(p).encode("utf-8")
        if writer.images:
            parts[f"word/_rels/{name}.rels"] = _relationships([(f"rIdImg{n}", "image", media_name(n, f)) for n, f in writer.images]).encode("utf-8")
    for n, f in enumerate(doc.figures(), 1):
        assert f.output is not None
        parts[f"word/{media_name(n, f)}"] = f.output
    return parts


def write_docx(doc: Document, path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in package_bytes(doc).items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
