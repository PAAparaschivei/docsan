"""Leak-matrix generator: synthetic Word files with planted references.

Each fixture plants the fictional company "Aurelan Group" (or one of its
people, systems, labels, links or phone numbers) in one location of a Word
package, often in disguise. The matrix states the expected outcome: the file
is released with every reference gone, it needs review, or it is blocked.
All names here are invented for testing.

Run ``uv run python tests/leakmatrix.py OUT_DIR`` to write the files for
manual inspection.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures"
PROFILE = FIXTURES / "profile.json"
LEXICON = FIXTURES / "lexicon"

# Text that must never survive in a released file, after removing case,
# accents, look-alike letters and every separator.
# System names such as "Vireo" are kept by default (decision D-011), so they
# are checked per fixture rather than here.
SECRETS = ("aurelan", "marchetti", "c2restricted", "internaluseonly")

NS = (
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
    'mc:Ignorable="wps"'
)
W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

CT = "application/vnd.openxmlformats-officedocument.wordprocessingml"
DOC_MAIN = f"{CT}.document.main+xml"
MACRO_MAIN = "application/vnd.ms-word.document.macroEnabled.main+xml"
TYPES = {
    "word/styles.xml": f"{CT}.styles+xml",
    "word/numbering.xml": f"{CT}.numbering+xml",
    "word/footnotes.xml": f"{CT}.footnotes+xml",
    "word/endnotes.xml": f"{CT}.endnotes+xml",
    "word/comments.xml": f"{CT}.comments+xml",
    "word/settings.xml": f"{CT}.settings+xml",
    "word/webSettings.xml": f"{CT}.webSettings+xml",
    "word/fontTable.xml": f"{CT}.fontTable+xml",
    "word/people.xml": f"{CT}.people+xml",
    "word/header1.xml": f"{CT}.header+xml",
    "word/footer1.xml": f"{CT}.footer+xml",
    "word/glossary/document.xml": f"{CT}.document.glossary+xml",
    "word/theme/theme1.xml": "application/vnd.openxmlformats-officedocument.theme+xml",
    "docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
    "docProps/app.xml": "application/vnd.openxmlformats-officedocument.extended-properties+xml",
    "docProps/custom.xml": "application/vnd.openxmlformats-officedocument.custom-properties+xml",
    "word/vbaProject.bin": "application/vnd.ms-office.vbaProject",
}


# --- XML helpers ----------------------------------------------------------------


def run(text: str, props: str = "") -> str:
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(*runs: str, style: str | None = None, numid: int | None = None, ilvl: int = 0) -> str:
    ppr = ""
    if style or numid:
        inner = f'<w:pStyle w:val="{style}"/>' if style else ""
        if numid:
            inner += f'<w:numPr><w:ilvl w:val="{ilvl}"/><w:numId w:val="{numid}"/></w:numPr>'
        ppr = f"<w:pPr>{inner}</w:pPr>"
    return f"<w:p>{ppr}{''.join(runs)}</w:p>"


def text_para(text: str, **kw: object) -> str:
    return para(run(text), **kw)  # type: ignore[arg-type]


def table(rows: list[list[str]]) -> str:
    body = "".join(
        "<w:tr>" + "".join(f"<w:tc><w:tcPr/>{cell}</w:tc>" for cell in row) + "</w:tr>" for row in rows
    )
    return f"<w:tbl><w:tblPr/><w:tblGrid/>{body}</w:tbl>"


def word_field(instr: str, result: str) -> str:
    return (
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        f'<w:r><w:instrText xml:space="preserve">{escape(instr)}</w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        f"{run(result)}"
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    )


def drawing(inner: str, uri: str, descr: str = "") -> str:
    return (
        '<w:r><w:drawing><wp:inline><wp:extent cx="914400" cy="914400"/>'
        f'<wp:docPr id="1" name="Shape 1" descr={quoteattr(descr)}/>'
        f'<a:graphic><a:graphicData uri="{uri}">{inner}</a:graphicData></a:graphic>'
        "</wp:inline></w:drawing></w:r>"
    )


PIC_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WPS_URI = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM_URI = "http://schemas.openxmlformats.org/drawingml/2006/diagram"

IMAGE_INNER = (
    '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="logo.png"/><pic:cNvPicPr/></pic:nvPicPr>'
    '<pic:blipFill><a:blip r:embed="rIdImg"/></pic:blipFill><pic:spPr/></pic:pic>'
)

def png_bytes(width: int = 8, height: int = 8, text: bytes = b"Author\x00Aurelan Group") -> bytes:
    """A small white PNG with a text chunk naming the company."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    rows = b"".join(b"\x00" + b"\xff" * (width * 3) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", text)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PNG = png_bytes()


def textbox(text: str) -> str:
    choice = drawing(
        f'<wps:wsp><wps:spPr/><wps:txbx><w:txbxContent>{text_para(text)}</w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp>',
        WPS_URI,
    ).removeprefix("<w:r>").removesuffix("</w:r>")
    fallback = f'<w:pict><v:shape><v:textbox><w:txbxContent>{text_para(text)}</w:txbxContent></v:textbox></v:shape></w:pict>'
    return (
        f'<w:r><mc:AlternateContent><mc:Choice Requires="wps">{choice}</mc:Choice>'
        f"<mc:Fallback>{fallback}</mc:Fallback></mc:AlternateContent></w:r>"
    )


# --- Package builder --------------------------------------------------------------

STYLES = (
    DECL + f"<w:styles {W_NS}>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Titre"><w:name w:val="Title"/><w:basedOn w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/></w:style>'
    "{extra}</w:styles>"
)
NUMBERING = (
    DECL + f"<w:numbering {W_NS}>"
    '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="-"/></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    "</w:numbering>"
)
CORE = (
    DECL + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>{creator}</dc:creator><dc:title>{title}</dc:title>'
    "</cp:coreProperties>"
)
APP = (
    DECL + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<Company>{company}</Company></Properties>"
)


def package(
    body: str,
    *,
    parts: dict[str, str | bytes] | None = None,
    doc_rels: list[str] | None = None,
    sect: str = "",
    style_extra: str = "",
    main_type: str = DOC_MAIN,
    creator: str = "Writer",
    title: str = "Plan",
    company: str = "",
    document_xml: str | None = None,
) -> dict[str, bytes]:
    files: dict[str, str | bytes] = {
        "word/document.xml": document_xml
        or (DECL + f"<w:document {NS}><w:body>{body}<w:sectPr>{sect}<w:pgSz w:w=\"11906\" w:h=\"16838\"/></w:sectPr></w:body></w:document>"),
        "word/styles.xml": STYLES.format(extra=style_extra),
        "word/numbering.xml": NUMBERING,
        "docProps/core.xml": CORE.format(creator=escape(creator), title=escape(title)),
        "docProps/app.xml": APP.format(company=escape(company)),
        "word/settings.xml": DECL + f"<w:settings {W_NS}/>",
        "word/fontTable.xml": DECL + f'<w:fonts {W_NS}><w:font w:name="Calibri"/></w:fonts>',
    }
    files.update(parts or {})
    rels = [
        f'<Relationship Id="rId1" Type="{REL_NS}/styles" Target="styles.xml"/>',
        f'<Relationship Id="rId2" Type="{REL_NS}/numbering" Target="numbering.xml"/>',
        f'<Relationship Id="rId3" Type="{REL_NS}/settings" Target="settings.xml"/>',
        f'<Relationship Id="rId4" Type="{REL_NS}/fontTable" Target="fontTable.xml"/>',
        *(doc_rels or []),
    ]
    files["word/_rels/document.xml.rels"] = (
        DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(rels)
        + "</Relationships>"
    )
    files["_rels/.rels"] = (
        DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL_NS}/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        f'<Relationship Id="rId3" Type="{REL_NS}/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    overrides = [f'<Override PartName="/word/document.xml" ContentType="{main_type}"/>']
    for name in files:
        if name in TYPES:
            overrides.append(f'<Override PartName="/{name}" ContentType="{TYPES[name]}"/>')
    files["[Content_Types].xml"] = (
        DECL + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/><Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Default Extension="bin" ContentType="application/octet-stream"/>'
        + "".join(overrides)
        + "</Types>"
    )
    return {k: v.encode("utf-8") if isinstance(v, str) else v for k, v in files.items()}


def zip_bytes(files: dict[str, bytes], duplicate: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        if duplicate:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                zf.writestr(duplicate, files[duplicate])
    return buf.getvalue()


# --- The matrix ------------------------------------------------------------------------


@dataclass
class Fixture:
    name: str
    expect: str  # ready, needs_review or blocked
    data: bytes
    filename: str = ""
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    reason: str = ""  # expected substring of a blocking reason
    remove_images: bool = False
    profile: dict = field(default_factory=dict)  # changes to the test profile

    @property
    def file(self) -> str:
        return self.filename or f"{self.name}.docx"


BASE = text_para("The platform handles 40,000 requests per second.")


def doc(body: str, **kw: object) -> bytes:
    return zip_bytes(package(BASE + body, **kw))  # type: ignore[arg-type]


def fixtures() -> list[Fixture]:
    F = Fixture
    out: list[Fixture] = []

    # Text locations: the reference must be replaced.
    out += [
        F("body", "ready", doc(text_para("The migration plan for Aurelan was approved in March.")),
          must_contain=["The migration plan for [CLIENT] was approved in March."]),
        F("heading", "ready", doc(text_para("Aurelan target architecture", style="Heading1")),
          must_contain=["[CLIENT] target architecture"]),
        F("title-style", "ready", doc(text_para("Aurelan plan", style="Titre")), must_contain=["[CLIENT] plan"]),
        F("list-item", "ready", doc(text_para("Review the Aurelan plan", numid=2)),
          must_contain=["Review the [CLIENT] plan"]),
        F("table-cell", "ready", doc(table([[text_para("Owner"), text_para("Aurelan")],
                                             [text_para("Region"), table([[text_para("Aurelan Group")]])]])),
          must_contain=["Owner", "Region"]),
        F("split-runs", "ready", doc(para(run("The plan for "), run("Aure", "<w:b/>"), run("lan"), run(" was approved."))),
          must_contain=["The plan for [CLIENT] was approved."]),
        F("split-three", "ready", doc(para(run("Au", "<w:i/>"), run("re", "<w:b/>"), run("lan", "<w:u w:val=\"single\"/>")))),
        F("inserted-revision", "ready", doc(
            '<w:p><w:ins w:id="1" w:author="Elena Marchetti" w:date="2026-01-01T00:00:00Z">'
            + run("Approved by Aurelan") + "</w:ins></w:p>"),
          must_contain=["Approved by [CLIENT]"]),
        F("hyperlink", "ready", doc(
            f'<w:p><w:hyperlink r:id="rIdLink">{run("Aurelan portal")}</w:hyperlink></w:p>',
            doc_rels=[f'<Relationship Id="rIdLink" Type="{REL_NS}/hyperlink" Target="https://portal.aurelan.example/" TargetMode="External"/>']),
          must_contain=["[CLIENT] portal"]),
        F("field-hyperlink", "ready", doc(para(word_field(' HYPERLINK "https://portal.aurelan.example/" ', "Aurelan portal"))),
          must_contain=["[CLIENT] portal"], must_not_contain=["HYPERLINK"]),
        F("field-simple", "ready", doc(
            f'<w:p><w:fldSimple w:instr=" DOCPROPERTY Company ">{run("Aurelan Group")}</w:fldSimple></w:p>'),
          must_contain=["[CLIENT]"], must_not_contain=["DOCPROPERTY"]),
        F("content-control", "ready", doc(
            '<w:sdt><w:sdtPr><w:alias w:val="Aurelan client"/><w:tag w:val="aurelan-tag"/></w:sdtPr>'
            f"<w:sdtContent>{text_para('Client: Aurelan')}</w:sdtContent></w:sdt>"),
          must_contain=["Client: [CLIENT]"]),
        F("inline-wrappers", "ready", doc(
            f'<w:p><w:smartTag w:element="AurelanTag">{run("Aurelan")}</w:smartTag>'
            f'<w:customXml w:element="aurelan">{run(" office")}</w:customXml></w:p>'),
          must_contain=["[CLIENT] office"]),
        F("text-box", "ready", doc(para(run("See the diagram."), textbox("Aurelan data centre"))),
          must_contain=["See the diagram.", "[CLIENT] data centre"]),
        F("footnote", "ready", doc(
            para(run("The forecast is stable."), '<w:r><w:footnoteReference w:id="2"/></w:r>'),
            parts={"word/footnotes.xml": DECL + f'<w:footnotes {W_NS}><w:footnote w:type="separator" w:id="0"><w:p/></w:footnote>'
                   f'<w:footnote w:id="2">{text_para("Source: Aurelan annual plan.")}</w:footnote></w:footnotes>'},
            doc_rels=[f'<Relationship Id="rIdFn" Type="{REL_NS}/footnotes" Target="footnotes.xml"/>']),
          must_contain=["The forecast is stable.[1]", "Notes", "[1] Source: [CLIENT] annual plan."]),
        F("endnote", "ready", doc(
            para(run("The forecast is stable."), '<w:r><w:endnoteReference w:id="3"/></w:r>'),
            parts={"word/endnotes.xml": DECL + f'<w:endnotes {W_NS}><w:endnote w:id="3">{text_para("Aurelan audit.")}</w:endnote></w:endnotes>'},
            doc_rels=[f'<Relationship Id="rIdEn" Type="{REL_NS}/endnotes" Target="endnotes.xml"/>']),
          must_contain=["[1] [CLIENT] audit."]),
        F("split-by-tab", "ready", doc(para(run("Aure"), "<w:r><w:tab/></w:r>", run("lan")))),
        F("soft-hyphen-element", "ready", doc(para(run("Aure"), "<w:r><w:softHyphen/></w:r>", run("lan")))),
        F("no-break-hyphen", "ready", doc(para(run("Aure"), "<w:r><w:noBreakHyphen/></w:r>", run("lan")))),
        F("short-code-split", "ready", doc(para(run("Ticker AG"), run("L", "<w:b/>"), run(" closed higher."))),
          must_contain=["Ticker [CLIENT] closed higher."]),
        F("label-nbsp", "ready", doc(text_para("C2\u00a0Restricted applies.")), must_contain=["[LABEL] applies."]),
        F("phone-nbsp", "ready", doc(text_para("Call 26\u00a012\u00a034\u00a056 now.")), must_contain=["Call [PHONE-1] now."]),
    ]

    # Disguises in body text.
    disguises = {
        "upper": "AURELAN", "lower": "aurelan", "zero-width": "Aure\u200blan", "soft-hyphen": "Aure­lan",
        "bidi-control": "Aure\u202elan", "cyrillic": "Аurelаn", "greek": "Aurelαn",
        "accent": "Aurélan", "combining": "Aurélan", "dotted": "A.u.r.e.l.a.n", "spaced": "A u r e l a n",
        "fullwidth": "Ａｕｒｅｌａｎ", "nbsp-group": "Aurelan Group",
        "compound": "AurelanCloud", "possessive": "Aurelan’s", "hyphenated": "Aure-lan",
        "en-dash": "Aure–lan", "underscore": "aurelan_group",
    }
    out += [F(f"disguise-{k}", "ready", doc(text_para(f"Report from {v} today."))) for k, v in disguises.items()]

    # Locations that are removed entirely.
    out += [
        F("header", "ready", doc(text_para("Body text."), sect='<w:headerReference w:type="default" r:id="rIdH"/>',
            parts={"word/header1.xml": DECL + f"<w:hdr {W_NS}>{text_para('Aurelan Confidential')}</w:hdr>"},
            doc_rels=[f'<Relationship Id="rIdH" Type="{REL_NS}/header" Target="header1.xml"/>'])),
        F("footer", "ready", doc(text_para("Body text."), sect='<w:footerReference w:type="default" r:id="rIdF"/>',
            parts={"word/footer1.xml": DECL + f"<w:ftr {W_NS}>{text_para('Aurelan Group, C2 Restricted')}</w:ftr>"},
            doc_rels=[f'<Relationship Id="rIdF" Type="{REL_NS}/footer" Target="footer1.xml"/>'])),
        F("comment", "ready", doc(
            '<w:p><w:commentRangeStart w:id="0"/>' + run("The plan is final.") + '<w:commentRangeEnd w:id="0"/>'
            '<w:r><w:commentReference w:id="0"/></w:r></w:p>',
            parts={"word/comments.xml": DECL + f'<w:comments {W_NS}><w:comment w:id="0" w:author="Elena Marchetti">'
                   f'{text_para("Check with Aurelan.")}</w:comment></w:comments>'},
            doc_rels=[f'<Relationship Id="rIdC" Type="{REL_NS}/comments" Target="comments.xml"/>']),
          must_contain=["The plan is final."]),
        F("deleted-revision", "ready", doc(
            '<w:p>' + run("The plan was approved") + '<w:del w:id="2" w:author="Elena Marchetti">'
            '<w:r><w:delText xml:space="preserve"> by Aurelan</w:delText></w:r></w:del>' + run(".") + "</w:p>"),
          must_contain=["The plan was approved."]),
        F("moved-from", "ready", doc(
            "<w:p>" + run("Kept text.") + "<w:moveFrom w:id=\"3\" w:author=\"Writer\">" + run(" Aurelan") + "</w:moveFrom></w:p>"),
          must_contain=["Kept text."], must_not_contain=["[CLIENT]"]),
        F("hidden-text", "ready", doc(para(run("Visible text."), run(" Aurelan", "<w:vanish/>"))),
          must_contain=["Visible text."], must_not_contain=["[CLIENT]"]),
        F("bookmark", "ready", doc('<w:p><w:bookmarkStart w:id="0" w:name="Aurelan_section"/>' + run("Section text.")
                                   + '<w:bookmarkEnd w:id="0"/></w:p>'), must_contain=["Section text."]),
        F("deleted-row", "ready", doc(
            '<w:tbl><w:tblPr/><w:tblGrid/><w:tr><w:tc>' + text_para("Kept cell") + "</w:tc></w:tr>"
            '<w:tr><w:trPr><w:del w:id="4" w:author="Writer"/></w:trPr><w:tc>' + text_para("Aurelan row") + "</w:tc></w:tr></w:tbl>"),
          must_contain=["Kept cell"]),
        F("properties", "ready", doc(text_para("Body text."), creator="Elena Marchetti", title="Aurelan plan",
            company="Aurelan Group", parts={"docProps/custom.xml": DECL + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties">'
            '<property name="MSIP_Label_1_Name"><vt:lpwstr xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">Aurelan Confidential</vt:lpwstr></property></Properties>'})),
        F("label-metadata", "ready", doc(text_para("Body text."), parts={
            "docMetadata/LabelInfo.xml": DECL + "<labelList><label name=\"Aurelan Confidential\"/></labelList>"})),
        F("custom-xml", "ready", doc(text_para("Body text."), parts={
            "customXml/item1.xml": DECL + "<client><name>Aurelan Group</name></client>"})),
        F("attached-template", "ready", doc(text_para("Body text."), parts={
            "word/settings.xml": DECL + f'<w:settings {W_NS} xmlns:r="{REL_NS}"><w:attachedTemplate r:id="rIdT"/></w:settings>',
            "word/_rels/settings.xml.rels": DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rIdT" Type="{REL_NS}/attachedTemplate" Target="file:///\\\\fs.aurelan.example\\templates\\Aurelan.dotx" TargetMode="External"/></Relationships>'})),
        F("style-name", "ready", doc(text_para("Styled heading", style="AurelanHeading"),
            style_extra='<w:style w:type="paragraph" w:styleId="AurelanHeading"><w:name w:val="Aurelan Heading"/>'
                        '<w:basedOn w:val="Heading1"/></w:style>'),
          must_contain=["Styled heading"]),
        F("corporate-font", "ready", doc(para(run("Body text in the house font.", '<w:rFonts w:ascii="Aurelan Sans" w:hAnsi="Aurelan Sans"/>')),
            parts={"word/fontTable.xml": DECL + f'<w:fonts {W_NS}><w:font w:name="Aurelan Sans"><w:family w:val="roman"/></w:font></w:fonts>'}),
          must_contain=["Body text in the house font."]),
        F("font-and-theme", "ready", doc(text_para("Body text."), parts={
            "word/fontTable.xml": DECL + f'<w:fonts {W_NS}><w:font w:name="Aurelan Sans"/></w:fonts>',
            "word/theme/theme1.xml": DECL + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Aurelan Theme"/>'})),
        F("glossary", "ready", doc(text_para("Body text."), parts={
            "word/glossary/document.xml": DECL + f"<w:glossaryDocument {W_NS}><w:docParts>{text_para('Aurelan boilerplate')}</w:docParts></w:glossaryDocument>"})),
        F("people", "ready", doc(text_para("Body text."), parts={
            "word/people.xml": DECL + '<w15:people xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"><w15:person w15:author="Elena Marchetti"/></w15:people>'})),
        F("web-settings", "ready", doc(text_para("Body text."), parts={
            "word/webSettings.xml": DECL + f'<w:webSettings {W_NS}><w:encoding w:val="aurelan"/></w:webSettings>'})),
        F("thumbnail", "ready", doc(text_para("Body text."), parts={"docProps/thumbnail.jpeg": b"\xff\xd8\xffAurelan Group\xff\xd9"})),
        F("macros", "ready", doc(text_para("Body text."), main_type=MACRO_MAIN, parts={"word/vbaProject.bin": b"Attribute VB_Name = \"Aurelan\""}),
          filename="macros.docm"),
        F("image-removed", "ready", doc(para(run("Logo: "), drawing(IMAGE_INNER, PIC_URI, descr="Aurelan logo")),
            parts={"word/media/image1.png": PNG},
            doc_rels=[f'<Relationship Id="rIdImg" Type="{REL_NS}/image" Target="media/image1.png"/>']),
          must_contain=["Logo: [IMAGE-REMOVED]"], remove_images=True),
        F("company-filename", "ready", doc(text_para("Body text.")), filename="aurelan-group-plan.docx"),
    ]

    # Fixed patterns and profile terms.
    out += [
        F("email", "ready", doc(text_para("Write to elena.marchetti@aurelan.example for access.")),
          must_contain=["Write to [EMAIL-1] for access."]),
        F("urls", "ready", doc(text_para("Open https://portal.aurelan.example/login or www.aurelan.lu today.")),
          must_contain=["Open [URL-1] or [URL-2] today."]),
        F("domains", "ready", doc(text_para("Mail moved from aurelan-group.lu to mail.example.nl and files.corp.local.")),
          must_contain=["Mail moved from [DOMAIN-1] to [DOMAIN-2] and [DOMAIN-3]."]),
        F("unc-path", "ready", doc(text_para("Files live on \\\\fs01.corp\\share\\plans now.")),
          must_contain=["Files live on [URL-1] now."]),
        F("coded-classification", "ready", doc(text_para("C3 - Confidentiel")), must_contain=["[LABEL]"]),
        F("account-identifiers", "ready", doc(text_para("Account 9876543210 and SUB1234567.")),
          must_contain=["Account [ID-1] and [ID-2]."], profile={"safe_words": ["Account"]}),
        F("phones", "ready", doc(text_para("Call +352 26 12 34 56, 00352 2612 3456, +31 (0)20 123 4567 or 26 12 34 56.")),
          must_contain=["Call [PHONE-1], [PHONE-2], [PHONE-3] or [PHONE-4]."]),
        F("labels", "ready", doc(text_para("Aurelan Confidential. C2 Restricted. Internal Use Only.")),
          must_contain=["[LABEL]. [LABEL]. [LABEL]."]),
        F("person", "ready", doc(text_para("Elena Marchetti approved it; MARCHETTI signed it.")),
          must_contain=["[PERSON-1] approved it; [PERSON-1] signed it."]),
        F("system-kept", "ready", doc(text_para("The billing platform Vireo runs nightly.")),
          must_contain=["The billing platform Vireo runs nightly."]),
        F("system-redacted", "ready", doc(text_para("The billing platform Vireo runs nightly.")),
          must_contain=["The billing platform [SYSTEM-1] runs nightly."], must_not_contain=["Vireo"],
          profile={"redact_system_names": True}),
        F("company-inside-names", "ready", doc(text_para("Customers sign in with the MyAurelan app and pay with AurelanPay.")),
          must_contain=["Customers sign in with the My[CLIENT] app and pay with [CLIENT]Pay."]),
        F("short-code-compound", "ready", doc(text_para("Staff use the MyAGL portal, the AGLConnect hub and two AGLs.")),
          must_contain=["Staff use the My[CLIENT] portal, the [CLIENT]Connect hub and two [CLIENT]s."]),
    ]

    # People as first name and family-name initial (decision D-021).
    initials = {"person_style": "first_name_initial"}
    tokens = {**initials, "terms": base_terms() + [
        {"text": w, "label": "PERSON"} for w in ("Paul", "Andrei", "Aparaschivei")]}
    out += [
        F("person-initials", "ready", doc(text_para("Elena Marchetti approved it; Marchetti signed it.")),
          must_contain=["Elena M. approved it; M. signed it."], profile=initials),
        F("person-initials-tokens", "ready", doc(text_para("Owner Paul Andrei Aparaschivei signed it today; call Paul.")),
          must_contain=["Owner Paul A. signed it today; call P."], must_not_contain=["Andrei", "Aparaschivei"],
          profile=tokens),
        F("person-initials-capitals", "ready", doc(text_para("APARASCHIVEI Paul signed it today.")),
          must_contain=["Paul A. signed it today."], must_not_contain=["APARASCHIVEI"], profile=tokens),
    ]

    # Technical content that must survive.
    out += [
        F("technical-terms", "ready", doc(
            text_para("Send a PATCH /v2/orders request, then GET /v2/orders.")
            + text_para("Use the PATCH method on the endpoint.")
            + text_para("PATCH Networks runs the data centre.")
            + text_para("Microsoft Azure and Kubernetes host the API at 10.20.30.40.")),
          must_contain=["Send a PATCH /v2/orders request, then GET /v2/orders.", "Use the PATCH method on the endpoint.",
                        "[CLIENT] Networks runs the data centre.", "Microsoft Azure and Kubernetes host the API at 10.20.30.40.",
                        "The platform handles 40,000 requests per second."]),
        F("structure", "ready", doc(
            text_para("Overview", style="Heading1") + text_para("First point", numid=1) + text_para("Second point", numid=1)
            + table([[text_para("Metric"), text_para("Value")], [text_para("Latency"), text_para("120 ms")]])
            + text_para("Summary", style="Heading2")),
          must_contain=["Overview", "First point", "Second point", "Metric", "Value", "Latency", "120 ms", "Summary"]),
    ]

    # Components that block release.
    chart = drawing('<c:chart r:id="rIdChart"/>', CHART_URI)
    smartart = drawing('<dgm:relIds r:dm="rId9" r:lo="rId10" r:qs="rId11" r:cs="rId12"/>', DGM_URI)
    ole = '<w:r><w:object><v:shape/><o:OLEObject ProgID="Excel.Sheet.12" r:id="rIdOle"/></w:object></w:r>'
    out += [
        # A picture waits for the owner to confirm its masks.
        F("image", "needs_review", doc(para(drawing(IMAGE_INNER, PIC_URI)), parts={"word/media/image1.png": PNG},
            doc_rels=[f'<Relationship Id="rIdImg" Type="{REL_NS}/image" Target="media/image1.png"/>'])),
        F("chart", "blocked", doc(para(chart)), reason="chart"),
        F("smartart", "blocked", doc(para(smartart)), reason="SmartArt"),
        F("ole-object", "blocked", doc(para(ole)), reason="embedded object"),
        F("equation", "blocked", doc("<w:p><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>"), reason="equation"),
        F("alt-chunk", "blocked", doc('<w:altChunk r:id="rIdAlt"/>'), reason="altChunk"),
        F("wordart", "blocked", doc('<w:p><w:r><w:pict><v:shape><v:textpath string="Aurelan"/></v:shape></w:pict></w:r></w:p>'), reason="WordArt"),
        F("sub-document", "blocked", doc('<w:p><w:subDoc r:id="rIdSub"/></w:p>'), reason="sub-document"),
        F("unknown-element", "blocked", doc('<w:p><w:mysteryElement/></w:p>'), reason="unsupported element"),
        F("unknown-part", "blocked", doc(text_para("Body text."), parts={"word/mystery.xml": DECL + "<x>Aurelan</x>"}),
          reason="unknown package part"),
        F("unbalanced-field", "blocked", doc('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>' + run("text") + "</w:p>"),
          reason="field left open"),
        F("dtd", "blocked", zip_bytes(package("", document_xml=DECL + '<!DOCTYPE w:document [<!ENTITY x "Aurelan">]>'
            f"<w:document {NS}><w:body>{text_para('Body &x;')}</w:body></w:document>")), reason="DTD"),
        F("strict", "blocked", zip_bytes(package("", document_xml=DECL + '<w:document xmlns:w="http://purl.oclc.org/ooxml/wordprocessingml/main">'
            "<w:body/></w:document>")), reason="Strict"),
        F("encrypted", "blocked", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504 + "EncryptedPackage Aurelan".encode("utf-16-le"),
          reason="encrypted"),
        F("truncated", "blocked", doc(text_para("Body text."))[:300], reason="unreadable"),
        F("not-a-package", "blocked", b"Aurelan Group plan in plain text", reason="not a Word package"),
        F("duplicate-entry", "blocked", zip_bytes(package(BASE), duplicate="word/document.xml"), reason="duplicate"),
        F("zip-bomb", "blocked", doc(text_para("Body text."), parts={"word/media/big.bin": b"\x00" * (4 * 1024 * 1024)}),
          reason="compression ratio"),
        F("chart-removed", "ready", doc(para(run("Revenue: "), chart)), must_contain=["Revenue: [CHART-REMOVED]"], remove_images=True),
        F("smartart-removed", "ready", doc(para(run("Flow: "), smartart)), must_contain=["Flow: [DIAGRAM-REMOVED]"], remove_images=True),
        F("ole-removed", "ready", doc(para(run("Visio: "), ole)), must_contain=["Visio: [OBJECT-REMOVED]"], remove_images=True),
        F("equation-removed", "ready", doc("<w:p>" + run("Formula: ") + "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>"),
          must_contain=["Formula: [EQUATION-REMOVED]"], remove_images=True),
        F("add-in-parts", "ready", doc(text_para("Body text."), parts={
            "word/webextensions/webextension1.xml": DECL + "<we:webextension xmlns:we=\"http://schemas.microsoft.com/office/webextensions/webextension/2010/11\"/>",
            "word/intelligence2.xml": DECL + "<int2:intelligence xmlns:int2=\"http://schemas.microsoft.com/office/intelligence/2020/intelligence\"/>"})),
        F("legacy-doc", "blocked", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504, filename="legacy.doc", reason="not supported yet"),
    ]

    # Words that need a decision.
    out += [
        F("unknown-word", "needs_review", doc(text_para("The Halden data centre is ready."))),
        F("file-like-name", "needs_review", doc(text_para("Run setup.py before the release."))),
    ]
    return out


def base_terms() -> list[dict]:
    return json.loads(PROFILE.read_text(encoding="utf-8"))["terms"]


def write_profile(path: Path, changes: dict | None = None) -> Path:
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    data.update(changes or {})
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def build(fixture: Fixture, directory: Path) -> Path:
    path = directory / fixture.file
    path.write_bytes(fixture.data)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "leak-matrix")
    target.mkdir(parents=True, exist_ok=True)
    for f in fixtures():
        build(f, target)
    (target / "expected.json").write_text(json.dumps({f.file: f.expect for f in fixtures()}, indent=2))
    print(f"wrote {len(fixtures())} fixtures to {target}")
