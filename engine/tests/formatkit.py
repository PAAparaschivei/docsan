"""A synthetic Word file with the formatting real documents use.

Styles with inheritance, a theme, a corporate font, a table style with a
header row and banded rows, merged cells, multilevel numbering, tabs with
leaders, borders and shading, a page break, a landscape section, and a header
and footer with page numbers. Everything is invented; "Aurelan Sans" stands
for a corporate font whose name names the company.

``formatting_docx(names=False)`` holds no profile term, so the sanitized file
should look exactly like the original; with ``names=True`` the company and a
person appear in the body, a header and a list number.
"""

from __future__ import annotations

import io
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
CT = "application/vnd.openxmlformats-officedocument.wordprocessingml."
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

THEME = (
    DECL + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"><a:themeElements>'
    '<a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
    '<a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4>'
    '<a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>'
    '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme>'
    '<a:fmtScheme name="Office"/></a:themeElements></a:theme>'
)

FONTS = (
    DECL + f'<w:fonts xmlns:w="{W_NS}"><w:font w:name="Calibri"><w:family w:val="swiss"/></w:font>'
    '<w:font w:name="Calibri Light"><w:family w:val="swiss"/></w:font><w:font w:name="Aurelan Sans"><w:family w:val="swiss"/></w:font>'
    '<w:font w:name="Symbol"><w:family w:val="roman"/></w:font></w:fonts>'
)

_BORDERS = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="8EAADB" w:themeColor="accent1" w:themeTint="99"/>'
                   for s in ("top", "left", "bottom", "right", "insideH", "insideV"))

STYLES = (
    DECL + f'<w:styles xmlns:w="{W_NS}">'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi" w:eastAsiaTheme="minorHAnsi" w:cstheme="minorBidi"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/></w:style>'
    '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/>'
    '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/><w:contextualSpacing/></w:pPr>'
    '<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:hAnsiTheme="majorHAnsi"/><w:spacing w:val="-10"/><w:kern w:val="28"/><w:sz w:val="56"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
    '<w:pPr><w:keepNext/><w:keepLines/><w:numPr><w:numId w:val="2"/></w:numPr><w:spacing w:before="240" w:after="0"/><w:outlineLvl w:val="0"/></w:pPr>'
    '<w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:hAnsiTheme="majorHAnsi"/><w:color w:val="2F5496" w:themeColor="accent1" w:themeShade="BF"/><w:sz w:val="32"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Heading1"/><w:next w:val="Normal"/>'
    '<w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="2"/></w:numPr><w:spacing w:before="40"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:sz w:val="26"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:customStyle="1" w:styleId="BrandBody"><w:name w:val="Brand Body"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:jc w:val="both"/><w:ind w:firstLine="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Aurelan Sans" w:hAnsi="Aurelan Sans"/><w:color w:val="404040"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:pBdr><w:left w:val="single" w:sz="18" w:space="8" w:color="4472C4"/></w:pBdr><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>'
    '<w:ind w:left="567" w:right="567"/></w:pPr><w:rPr><w:i/><w:color w:val="595959"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Header"><w:name w:val="header"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:tabs><w:tab w:val="center" w:pos="4513"/><w:tab w:val="right" w:pos="9026"/></w:tabs><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    '<w:rPr><w:sz w:val="18"/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="Strong"><w:name w:val="Strong"/><w:rPr><w:b/><w:bCs/></w:rPr></w:style>'
    '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/><w:rPr><w:color w:val="0563C1" w:themeColor="hyperlink"/><w:u w:val="single"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="GridTable4"><w:name w:val="Grid Table 4 Accent 1"/><w:basedOn w:val="TableNormal"/>'
    f'<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr><w:tblPr><w:tblStyleRowBandSize w:val="1"/><w:tblBorders>{_BORDERS}</w:tblBorders></w:tblPr>'
    '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:bCs/><w:color w:val="FFFFFF" w:themeColor="background1"/></w:rPr>'
    '<w:tcPr><w:tcBorders><w:top w:val="single" w:sz="4" w:space="0" w:color="4472C4"/><w:left w:val="single" w:sz="4" w:space="0" w:color="4472C4"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="4472C4"/><w:right w:val="single" w:sz="4" w:space="0" w:color="4472C4"/>'
    '<w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tcBorders><w:shd w:val="clear" w:color="auto" w:fill="4472C4"/></w:tcPr></w:tblStylePr>'
    '<w:tblStylePr w:type="band1Horz"><w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/></w:tcPr></w:tblStylePr>'
    '<w:tblStylePr w:type="firstCol"><w:rPr><w:b/><w:bCs/></w:rPr></w:tblStylePr></w:style>'
    "</w:styles>"
)

NUMBERING = (
    DECL + f'<w:numbering xmlns:w="{W_NS}">'
    '<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val=""/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol" w:hint="default"/></w:rPr></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="o"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="1440" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Courier New" w:hAnsi="Courier New" w:hint="default"/></w:rPr></w:lvl>'
    "</w:abstractNum>"
    '<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="multilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="432" w:hanging="432"/></w:pPr></w:lvl>'
    '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="576" w:hanging="576"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:abstractNum w:abstractNumId="2"><w:multiLevelType w:val="hybridMultilevel"/>'
    '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/><w:lvlText w:val="{label}%1)"/><w:lvlJc w:val="left"/>'
    '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
    '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>'
    '<w:num w:numId="3"><w:abstractNumId w:val="2"/></w:num>'
    '<w:num w:numId="4"><w:abstractNumId w:val="2"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>'
    "</w:numbering>"
)


def _r(text: str, props: str = "") -> str:
    return f'<w:r>{f"<w:rPr>{props}</w:rPr>" if props else ""}<w:t xml:space="preserve">{text}</w:t></w:r>'


def _p(content: str, ppr: str = "") -> str:
    return f'<w:p>{f"<w:pPr>{ppr}</w:pPr>" if ppr else ""}{content}</w:p>'


def _cell(text: str, width: int, tcpr: str = "") -> str:
    return f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>{tcpr}</w:tcPr>{_p(_r(text))}</w:tc>'


def body(names: bool) -> str:
    company = "Aurelan Group" if names else "the operator"
    person = "Elena Marchetti" if names else "the architect"
    table = (
        '<w:tbl><w:tblPr><w:tblStyle w:val="GridTable4"/><w:tblW w:w="9026" w:type="dxa"/>'
        '<w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>'
        '<w:tblGrid><w:gridCol w:w="3005"/><w:gridCol w:w="3005"/><w:gridCol w:w="3016"/></w:tblGrid>'
        '<w:tr><w:trPr><w:tblHeader/></w:trPr>' + _cell("Capability", 3005) + _cell("Owner", 3005) + _cell("Status", 3016) + "</w:tr>"
        "<w:tr>" + _cell("Billing", 3005) + _cell("Finance", 3005) + _cell("Live", 3016, '<w:shd w:val="clear" w:color="auto" w:fill="E2EFD9"/>') + "</w:tr>"
        "<w:tr>" + _cell("Identity", 3005, '<w:vMerge w:val="restart"/>') + _cell("Security and platform teams", 6021, '<w:gridSpan w:val="2"/>') + "</w:tr>"
        "<w:tr>" + _cell("", 3005, "<w:vMerge/>") + _cell("Platform", 3005) + _cell("Planned", 3016) + "</w:tr>"
        "<w:tr>" + _cell("Reporting", 3005) + _cell("Data", 3005) + _cell("Pilot", 3016) + "</w:tr>"
        "</w:tbl>"
    )
    return (
        _p(_r("Target architecture"), '<w:pStyle w:val="Title"/>')
        + _p(_r("Context"), '<w:pStyle w:val="Heading1"/>')
        + _p(_r("This document describes the platform that ") + _r(company, '<w:rStyle w:val="Strong"/>')
             + _r(" runs today and the changes ") + _r("planned", "<w:i/>") + _r(" for next year. It is maintained by ")
             + _r(person, '<w:u w:val="single"/>') + _r("."), '<w:pStyle w:val="BrandBody"/>')
        + _p(_r("Scope"), '<w:pStyle w:val="Heading2"/>')
        + _p(_r("Customer channels"), '<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
        + _p(_r("Web and mobile"), '<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr>')
        + _p(_r("Back-office systems"), '<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
        + _p(_r("Decisions"), '<w:pStyle w:val="Heading2"/>')
        + _p(_r("Keep the integration layer"), '<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>')
        + _p(_r("Retire the batch interfaces"), '<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr>')
        + _p(_r("A new list starts again"), '<w:pStyle w:val="ListParagraph"/><w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/></w:numPr>')
        + _p(_r("Stability before features."), '<w:pStyle w:val="Quote"/>')
        + _p(_r("Throughput") + "<w:r><w:tab/></w:r>" + _r("40,000 per second"), '<w:tabs><w:tab w:val="right" w:leader="dot" w:pos="9026"/></w:tabs>')
        + _p(_r("Colour, ") + _r("highlight", '<w:highlight w:val="yellow"/>') + _r(", size ", "") + _r("14 pt", '<w:sz w:val="28"/>')
             + _r(", H") + _r("2", '<w:vertAlign w:val="subscript"/>') + _r("O and E=mc") + _r("2", '<w:vertAlign w:val="superscript"/>')
             + _r(" in ") + _r("red", '<w:color w:val="C00000"/>') + _r(" and ") + _r("small caps", "<w:smallCaps/>") + _r("."),
             '<w:jc w:val="center"/>')
        + _p(_r("Capabilities"), '<w:pStyle w:val="Heading1"/>')
        + table
        + _p('<w:r><w:br w:type="page"/></w:r>' + _r("Roadmap on a new page."))
        + _p(_r("The roadmap table follows in landscape."), '<w:sectPr><w:headerReference w:type="default" r:id="rIdH1"/>'
             '<w:footerReference w:type="default" r:id="rIdF1"/><w:pgSz w:w="11906" w:h="16838"/>'
             '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
        + _p(_r("Roadmap"), '<w:pStyle w:val="Heading1"/>')
        + _p(_r("Phase one migrates billing; phase two moves identity; phase three closes the data centre."), '<w:pStyle w:val="BrandBody"/>')
        + '<w:sectPr><w:headerReference w:type="default" r:id="rIdH1"/><w:footerReference w:type="default" r:id="rIdF1"/>'
        '<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>'
        '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr>'
    )


def formatting_docx(names: bool = False, brand_font: str = "Aurelan Sans") -> bytes:
    company = "Aurelan Group" if names else "Platform programme"
    header = (DECL + f'<w:hdr xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
              + _p(_r(f"{company} — Target architecture") + '<w:r><w:tab/></w:r><w:r><w:tab/></w:r>' + _r("Internal"),
                   '<w:pStyle w:val="Header"/>') + "</w:hdr>")
    footer = (DECL + f'<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}">'
              + _p(_r("Page ") + '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE   \\* MERGEFORMAT </w:instrText></w:r>'
                   '<w:r><w:fldChar w:fldCharType="separate"/></w:r>' + _r("1") + '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
                   + _r(" of ") + f'<w:fldSimple w:instr=" NUMPAGES ">{_r("2")}</w:fldSimple>', '<w:pStyle w:val="Header"/><w:jc w:val="right"/>')
              + "</w:ftr>")
    files = {
        "[Content_Types].xml": DECL + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'<Override PartName="/word/document.xml" ContentType="{CT}document.main+xml"/>'
        f'<Override PartName="/word/styles.xml" ContentType="{CT}styles+xml"/>'
        f'<Override PartName="/word/numbering.xml" ContentType="{CT}numbering+xml"/>'
        f'<Override PartName="/word/settings.xml" ContentType="{CT}settings+xml"/>'
        f'<Override PartName="/word/fontTable.xml" ContentType="{CT}fontTable+xml"/>'
        f'<Override PartName="/word/header1.xml" ContentType="{CT}header+xml"/>'
        f'<Override PartName="/word/footer1.xml" ContentType="{CT}footer+xml"/>'
        '<Override PartName="/word/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>',
        "_rels/.rels": DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL}officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL}styles" Target="styles.xml"/><Relationship Id="rId2" Type="{REL}numbering" Target="numbering.xml"/>'
        f'<Relationship Id="rId3" Type="{REL}settings" Target="settings.xml"/><Relationship Id="rId4" Type="{REL}fontTable" Target="fontTable.xml"/>'
        f'<Relationship Id="rId5" Type="{REL}theme" Target="theme/theme1.xml"/><Relationship Id="rIdH1" Type="{REL}header" Target="header1.xml"/>'
        f'<Relationship Id="rIdF1" Type="{REL}footer" Target="footer1.xml"/></Relationships>',
        "word/document.xml": DECL + f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>{body(names)}</w:body></w:document>',
        "word/styles.xml": STYLES.replace("Aurelan Sans", brand_font),
        "word/numbering.xml": NUMBERING.replace("{label}", "Aurelan " if names else ""),
        "word/settings.xml": DECL + f'<w:settings xmlns:w="{W_NS}"><w:defaultTabStop w:val="720"/>'
        '<w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>',
        "word/fontTable.xml": FONTS.replace("Aurelan Sans", brand_font),
        "word/theme/theme1.xml": THEME,
        "word/header1.xml": header,
        "word/footer1.xml": footer,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()
