"""Visible formatting survives sanitizing (decision D-023).

The rich fixture in ``formatkit`` uses styles with inheritance, a theme, a
table style with conditional formats, merged cells, multilevel numbering,
tabs, borders, shading, sections, a header and a footer with page numbers.
The output must state the same formatting directly, keep live page numbers,
replace the corporate font and the names, and (with LibreOffice installed)
render page for page like the original.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import formatkit
import leakmatrix as lm
import pytest
from harness import leaks, output_paragraphs

from docsan.pipeline import run_batch
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
COMPARE = Path(__file__).parent / "tools" / "pdf-compare.swift"


def sanitized(tmp: Path, data: bytes, profile_changes: dict | None = None) -> Path:
    """Run the file through the pipeline, keeping every unfamiliar word, and return the output."""
    (tmp / "in").mkdir(parents=True, exist_ok=True)
    src = tmp / "in" / "plan.docx"
    src.write_bytes(data)
    profile = lm.write_profile(tmp / "profile.json", profile_changes)
    result = run_batch([src], profile, tmp / "out", tmp / "private", lexicon=LEXICON).files[0]
    if result.review:
        changes = {**(profile_changes or {}), "safe_words": sorted({r.word for r in result.review})}
        profile = lm.write_profile(tmp / "profile.json", changes)
        result = run_batch([src], profile, tmp / "out", tmp / "private", lexicon=LEXICON).files[0]
    assert result.status == "ready", result.reasons
    return tmp / "out" / result.output


def parts(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n).decode("utf-8") for n in zf.namelist() if n.endswith(".xml")}


def paragraph_with(xml: str, text: str) -> str:
    for m in re.finditer(r"<w:p>.*?</w:p>", xml):
        if text in m.group():
            return m.group()
    raise AssertionError(f"no paragraph with {text!r}")


@pytest.fixture(scope="module")
def plain(tmp_path_factory) -> dict[str, str]:
    return parts(sanitized(tmp_path_factory.mktemp("plain"), formatkit.formatting_docx(names=False, brand_font="Calibri")))


def test_heading_keeps_its_style_formatting_and_numbering(plain):
    p = paragraph_with(plain["word/document.xml"], "Context")
    assert '<w:pStyle w:val="Heading1"/>' in p
    assert re.search(r'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="\d+"/></w:numPr>', p)
    assert 'w:ascii="Calibri Light"' in p and '<w:color w:val="2F5496"/>' in p and '<w:sz w:val="32"/>' in p
    assert "<w:keepNext/>" in p and 'w:before="240"' in p


def test_runs_keep_their_formatting(plain):
    doc = plain["word/document.xml"]
    p = paragraph_with(doc, "Colour, ")
    for expected in ('<w:highlight w:val="yellow"/>', '<w:vertAlign w:val="subscript"/>', '<w:vertAlign w:val="superscript"/>',
                     "<w:smallCaps/>", '<w:color w:val="C00000"/>', '<w:sz w:val="28"/>', '<w:jc w:val="center"/>'):
        assert expected in p, expected
    body = paragraph_with(doc, "This document describes")
    assert '<w:jc w:val="both"/>' in body and 'w:firstLine="360"' in body and '<w:color w:val="404040"/>' in body
    assert re.search(r"<w:b/>.*the operator", body), "the Strong character style makes the run bold"
    quote = paragraph_with(doc, "Stability before features.")
    assert '<w:pBdr><w:left w:color="4472C4" w:space="8" w:sz="18" w:val="single"/></w:pBdr>' in quote
    assert 'w:fill="F2F2F2"' in quote and "<w:i/>" in quote
    tabs = paragraph_with(doc, "Throughput")
    assert '<w:tab w:leader="dot" w:pos="9026" w:val="right"/>' in tabs


def test_table_style_becomes_cell_formatting(plain):
    table = re.search(r"<w:tbl>.*?</w:tbl>", plain["word/document.xml"]).group()
    rows = re.findall(r"<w:tr>.*?</w:tr>", table)
    header, first, second = rows[0], rows[1], rows[2]
    assert header.count('w:fill="4472C4"') == 3 and header.count('<w:color w:val="FFFFFF"/>') >= 3 and "<w:tblHeader/>" in header
    assert first.count('w:fill="D9E2F3"') == 2 and 'w:fill="E2EFD9"' in first, "banded row, and a cell's own shading wins"
    assert "D9E2F3" not in second, "the second band has no shading"
    assert re.search(r"<w:b/>.*Billing", first), "the first column is bold"
    assert '<w:vMerge w:val="restart"/>' in table and '<w:vMerge w:val="continue"/>' in table
    assert '<w:gridSpan w:val="2"/>' in table
    assert table.count('<w:gridCol w:w="3005"/>') == 2 and '<w:tblW w:type="dxa" w:w="9026"/>' in table
    assert 'w:color="8EAADB"' in table, "table borders come from the style, theme colour resolved"


def test_lists_keep_their_numbering(plain):
    numbering = plain["word/numbering.xml"]
    assert '<w:lvlText w:val="%1.%2"/>' in numbering and '<w:lvlText w:val=""/>' in numbering
    assert 'w:ascii="Symbol"' in numbering and '<w:numFmt w:val="lowerLetter"/>' in numbering
    assert re.search(r'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>', numbering)
    doc = plain["word/document.xml"]
    restart = re.search(r'<w:numId w:val="(\d+)"/>', paragraph_with(doc, "A new list starts again")).group(1)
    before = re.search(r'<w:numId w:val="(\d+)"/>', paragraph_with(doc, "Retire the batch interfaces")).group(1)
    assert restart != before, "a restarted list keeps its own numbering instance"


def test_sections_headers_and_page_numbers(plain):
    doc = plain["word/document.xml"]
    assert doc.count("<w:sectPr>") == 2
    assert '<w:pgSz w:h="11906" w:orient="landscape" w:w="16838"/>' in doc
    assert plain["word/header1.xml"].count("Target architecture") == 1
    footer = plain["word/footer2.xml"]
    assert '<w:fldSimple w:instr="PAGE">' in footer and '<w:fldSimple w:instr="NUMPAGES">' in footer
    assert '<w:headerReference w:type="default" r:id="rIdHdr1"/>' in doc


def test_names_fonts_and_list_text_are_sanitized(tmp_path):
    out = sanitized(tmp_path, formatkit.formatting_docx(names=True))
    assert leaks(out) == []
    xml = parts(out)
    assert "Aurelan Sans" not in xml["word/styles.xml"] + xml["word/document.xml"]
    assert 'w:ascii="Calibri"' in paragraph_with(xml["word/document.xml"], "This document describes")
    assert '<w:lvlText w:val="[CLIENT] %1)"/>' in xml["word/numbering.xml"]
    assert any(t.startswith("[CLIENT]") for t in output_paragraphs(out)), "the header is sanitized, not removed"


FLOATING = (
    '<w:r><w:drawing><wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" relativeHeight="251659264" '
    'behindDoc="0" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
    '<wp:positionH relativeFrom="page"><wp:posOffset>5400000</wp:posOffset></wp:positionH>'
    '<wp:positionV relativeFrom="page"><wp:align>top</wp:align></wp:positionV><wp:extent cx="1800000" cy="360000"/>'
    '<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:wrapNone/><wp:docPr id="7" name="Text Box 7" descr="Aurelan label"/>'
    '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp>'
    '<wps:cNvSpPr txBox="1"/><wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1800000" cy="360000"/></a:xfrm>'
    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:schemeClr val="accent1"/></a:solidFill><a:ln><a:noFill/></a:ln></wps:spPr>'
    '<wps:txbx><w:txbxContent>' + lm.text_para("Aurelan Internal") + "</w:txbxContent></wps:txbx>"
    '<wps:bodyPr lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr"><a:noAutofit/></wps:bodyPr></wps:wsp></a:graphicData></a:graphic>'
    "</wp:anchor></w:drawing></w:r>"
)


def test_floating_text_box_keeps_its_place(tmp_path):
    theme = '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements><a:clrScheme name="x"><a:accent1><a:srgbClr val="4472C4"/></a:accent1></a:clrScheme></a:themeElements></a:theme>'
    header = lm.DECL + f"<w:hdr {lm.NS}>{lm.para(FLOATING)}{lm.text_para('Plan')}</w:hdr>"
    data = lm.zip_bytes(lm.package(
        lm.text_para("Body text."), sect='<w:headerReference w:type="default" r:id="rIdH"/>',
        parts={"word/header1.xml": header, "word/theme/theme1.xml": lm.DECL + theme},
        doc_rels=[f'<Relationship Id="rIdH" Type="{lm.REL_NS}/header" Target="header1.xml"/>',
                  f'<Relationship Id="rIdT" Type="{lm.REL_NS}/theme" Target="theme/theme1.xml"/>']))
    out = sanitized(tmp_path, data)
    assert leaks(out) == []
    head = parts(out)["word/header1.xml"]
    assert '<wp:positionH relativeFrom="page"><wp:posOffset>5400000</wp:posOffset></wp:positionH>' in head
    assert '<wp:positionV relativeFrom="page"><wp:align>top</wp:align></wp:positionV>' in head
    assert '<a:srgbClr val="4472C4"/>' in head and 'name="Text Box 1"' in head and "descr" not in head
    assert "[CLIENT] Internal" in output_paragraphs(out)


@pytest.mark.slow
@pytest.mark.skipif(not os.access(SOFFICE, os.X_OK), reason="LibreOffice renders both files")
def test_output_renders_like_the_original(tmp_path):
    """Original and sanitized file, both rendered by LibreOffice: every page
    must match, ink within one pixel at 60 dots per inch."""
    data = formatkit.formatting_docx(names=False, brand_font="Calibri")
    out = sanitized(tmp_path, data)
    original = tmp_path / "original.docx"
    original.write_bytes(data)
    pdfs = []
    for path in (original, out):
        subprocess.run([str(SOFFICE), "--headless", "--norestore", f"-env:UserInstallation={(tmp_path / 'lo').as_uri()}",
                        "--convert-to", "pdf", "--outdir", str(tmp_path / "pdf"), str(path)], check=True, capture_output=True, timeout=300)
        pdfs.append(tmp_path / "pdf" / (path.stem + ".pdf"))
    tool = tmp_path / "pdf-compare"
    subprocess.run(["xcrun", "swiftc", "-O", "-o", str(tool), str(COMPARE)], check=True, capture_output=True, timeout=600)
    result = json.loads(subprocess.run([str(tool), *map(str, pdfs), "60"], check=True, capture_output=True, text=True).stdout)
    assert result["pages"][0] == result["pages"][1] >= 3
    assert min(result["scores"]) >= 0.99, result["scores"]
