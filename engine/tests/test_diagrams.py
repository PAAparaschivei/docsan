"""Diagrams in Word files: vector drawings, Visio objects, charts and SmartArt.

EMF drawings are made at test time by LibreOffice from an SVG (outside the
app, as a test tool only); DocSan draws them itself and must mask them,
using the drawing's own text as well as OCR. WMF and SVG-only pictures are
not drawn yet and must block. Visio and other embedded objects come out as
their masked preview picture with the object data gone. Charts become a
table of their data and SmartArt a nested list, both sanitized.
"""

from __future__ import annotations

import functools
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import imagematrix as im
import leakmatrix as lm
import pytest
from harness import leaks, output_paragraphs
from pngio import read_png
from test_docx_images import (
    confirm_all,
    image_rel,
    media,
    picture,
    rendered,
    run,
    word_file,
)

from docsan.pipeline import run_batch

DIAGRAM_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="800" height="300" viewBox="0 0 800 300">
<rect width="800" height="300" fill="white"/>
<rect x="30" y="40" width="300" height="90" rx="8" fill="#e3ecfb" stroke="#1553a8" stroke-width="2"/>
<text x="50" y="95" font-family="Helvetica" font-size="26">Aurelan Group</text>
<rect x="480" y="40" width="280" height="90" rx="8" fill="#e0f2e9" stroke="#0b6b41" stroke-width="2"/>
<text x="500" y="95" font-family="Helvetica" font-size="26">Integration layer</text>
<line x1="330" y1="85" x2="480" y2="85" stroke="#333" stroke-width="3"/>
</svg>"""

SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
needs_office = pytest.mark.skipif(not os.access(SOFFICE, os.X_OK), reason="LibreOffice makes the test drawings")


@functools.cache
def vector(fmt: str, svg: str = DIAGRAM_SVG) -> bytes:
    """A drawing as EMF or WMF, made once by LibreOffice."""
    work = Path(tempfile.mkdtemp(prefix="docsan-vector-"))
    (work / "diagram.svg").write_text(svg)
    subprocess.run([str(SOFFICE), "--headless", "--norestore", f"-env:UserInstallation={(work / 'profile').as_uri()}",
                    "--convert-to", fmt, "--outdir", str(work), str(work / "diagram.svg")], check=True, capture_output=True, timeout=300)
    return (work / f"diagram.{fmt}").read_bytes()


def assert_masked_diagram(tmp_path: Path, src: Path) -> None:
    first, final = confirm_all(src, tmp_path)
    assert first.images and first.images[0]["masks"], "the company name in the drawing must be masked"
    assert final.status == "ready", final.reasons
    out = tmp_path / "out" / final.output
    assert leaks(out) == []
    parts = media(out)
    assert list(parts) == ["word/media/image1.png"]
    px = read_png(parts["word/media/image1.png"])
    assert px.width > 1000, "the drawing is rendered at high resolution"


@needs_office
def test_emf_diagram_is_drawn_and_masked(tmp_path):
    src = word_file(tmp_path, lm.text_para("Architecture diagram") + lm.para(picture("rIdImg")),
                    {"word/media/image1.emf": vector("emf")}, [image_rel("rIdImg", "media/image1.emf")])
    assert_masked_diagram(tmp_path, src)


TINY_SVG = DIAGRAM_SVG.replace('<line x1="330"', '<text x="40" y="250" font-family="Helvetica" font-size="5">Aurelan</text><line x1="330"')


@needs_office
def test_drawing_text_masks_what_ocr_cannot_read(tmp_path):
    """A 5-point label is too small for OCR; the drawing's own text still masks it."""
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.emf": vector("emf", TINY_SVG)},
                    [image_rel("rIdImg", "media/image1.emf")])
    first = run(src, tmp_path, out=False)
    lines = [line for line in first.images[0]["lines"] if line["text"] == "Aurelan"]
    assert lines, "the drawing reports its tiny label"
    box = lines[0]["box"]
    covered = [m for m in first.images[0]["masks"]
               if m["box"][0] <= box[0] and m["box"][1] <= box[1]
               and m["box"][0] + m["box"][2] >= box[0] + box[2] and m["box"][1] + m["box"][3] >= box[1] + box[3]]
    assert covered, "a mask covers the tiny label"


@needs_office
@pytest.mark.parametrize("fmt,data", [("wmf", None), ("svg", DIAGRAM_SVG.encode())])
def test_wmf_and_svg_only_pictures_block(fmt, data, tmp_path):
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {f"word/media/image1.{fmt}": data or vector(fmt)},
                    [image_rel("rIdImg", f"media/image1.{fmt}")])
    result = run(src, tmp_path, out=False)
    assert result.status == "blocked" and any("cannot be read yet" in r for r in result.reasons), result.reasons


def test_visio_object_becomes_its_masked_preview(tmp_path):
    preview, words = rendered(tmp_path, [im.line("Aurelan Group", 30, 40), im.line("Integration layer", 110)])
    body = lm.para('<w:r><w:object><v:shape style="width:300pt;height:75pt"><v:imagedata r:id="rIdPrev"/></v:shape>'
                   '<o:OLEObject ProgID="Visio.Drawing.15" r:id="rIdOle"/></w:object></w:r>')
    rels = [image_rel("rIdPrev", "media/image1.png"),
            f'<Relationship Id="rIdOle" Type="{lm.REL_NS}/package" Target="embeddings/Drawing1.vsdx"/>']
    src = word_file(tmp_path, body, {"word/media/image1.png": preview, "word/embeddings/Drawing1.vsdx": b"Aurelan Group secret"}, rels)
    _, final = confirm_all(src, tmp_path)
    assert final.status == "ready", final.reasons
    out = tmp_path / "out" / final.output
    assert leaks(out) == [] and list(media(out)) == ["word/media/image1.png"]
    px = read_png(media(out)["word/media/image1.png"])
    for w in (w for w in words if w["text"] in ("Aurelan", "Group")):
        assert im.unmasked(px, w["box"], 0) == 0, w["text"]
    assert final.dropped.get("embedded object data") == 1


CHART = (
    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><c:chart>'
    '<c:title><c:tx><c:rich><a:p><a:r><a:t>Aurelan revenue</a:t></a:r></a:p></c:rich></c:tx></c:title>'
    "<c:plotArea><c:barChart><c:ser>"
    '<c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>Aurelan Group</c:v></c:pt></c:strCache></c:strRef></c:tx>'
    '<c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>Q1</c:v></c:pt><c:pt idx="1"><c:v>Q2</c:v></c:pt></c:strCache></c:strRef></c:cat>'
    '<c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>12</c:v></c:pt><c:pt idx="1"><c:v>15</c:v></c:pt></c:numCache></c:numRef></c:val>'
    "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
)


def test_chart_becomes_a_sanitized_table(tmp_path):
    body = lm.para(lm.drawing('<c:chart r:id="rIdChart"/>', lm.CHART_URI))
    rels = [f'<Relationship Id="rIdChart" Type="{lm.REL_NS}/chart" Target="charts/chart1.xml"/>']
    src = word_file(tmp_path, body, {"word/charts/chart1.xml": CHART.encode(),
                                     "word/embeddings/Workbook1.xlsx": b"Aurelan Group figures"}, rels)
    result = run(src, tmp_path)
    assert result.status == "ready", result.reasons
    out = tmp_path / "out" / result.output
    text = "\\n".join(output_paragraphs(out))
    assert leaks(out) == []
    for expected in ("Chart: [CLIENT] revenue", "[CLIENT]", "Q1", "Q2", "12", "15"):
        assert expected in text, expected


SMARTART = (
    '<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><dgm:ptLst>'
    '<dgm:pt modelId="0" type="doc"/>'
    '<dgm:pt modelId="1"><dgm:t><a:p><a:r><a:t>Aurelan portal</a:t></a:r></a:p></dgm:t></dgm:pt>'
    '<dgm:pt modelId="2"><dgm:t><a:p><a:r><a:t>Billing</a:t></a:r></a:p></dgm:t></dgm:pt>'
    '<dgm:pt modelId="3"><dgm:t><a:p><a:r><a:t>Audit</a:t></a:r></a:p></dgm:t></dgm:pt>'
    '<dgm:pt modelId="9" type="parTrans"/></dgm:ptLst><dgm:cxnLst>'
    '<dgm:cxn modelId="10" srcId="0" destId="1" srcOrd="0"/>'
    '<dgm:cxn modelId="11" srcId="1" destId="2" srcOrd="0"/>'
    '<dgm:cxn modelId="12" srcId="1" destId="3" srcOrd="1"/>'
    "</dgm:cxnLst></dgm:dataModel>"
)


def test_smartart_becomes_a_sanitized_list(tmp_path):
    body = lm.para(lm.drawing('<dgm:relIds r:dm="rIdDm" r:lo="rIdLo" r:qs="rIdQs" r:cs="rIdCs"/>', lm.DGM_URI))
    rels = [f'<Relationship Id="rIdDm" Type="{lm.REL_NS}/diagramData" Target="diagrams/data1.xml"/>']
    src = word_file(tmp_path, body, {"word/diagrams/data1.xml": SMARTART.encode()}, rels)
    result = run(src, tmp_path)
    assert result.status == "ready", result.reasons
    out = tmp_path / "out" / result.output
    assert leaks(out) == []
    with zipfile.ZipFile(out) as zf:
        document = zf.read("word/document.xml").decode()
    assert output_paragraphs(out)[-3:] == ["[CLIENT] portal", "Billing", "Audit"]
    assert document.count('<w:ilvl w:val="1"/>') == 2, "Billing and Audit sit under the portal"


def chart_space(plot: str, title: str = "", extra: str = "") -> bytes:
    head = f'<c:title><c:tx><c:rich><a:p><a:r><a:t>{title}</a:t></a:r></a:p></c:rich></c:tx></c:title>' if title else '<c:autoTitleDeleted val="1"/>'
    return (
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><c:chart>'
        f"{head}<c:plotArea>{plot}</c:plotArea></c:chart>{extra}</c:chartSpace>"
    ).encode()


def cache(kind: str, values: list, code: str = "General") -> str:
    tag = "numCache" if kind == "num" else "strCache"
    fmt = f"<c:formatCode>{code}</c:formatCode>" if kind == "num" else ""
    pts = "".join(f'<c:pt idx="{i}"><c:v>{v}</c:v></c:pt>' for i, v in enumerate(values) if v is not None)
    return f"<c:{kind}Ref><c:f>Sheet1!A1</c:f><c:{tag}>{fmt}<c:ptCount val=\"{len(values)}\"/>{pts}</c:{tag}></c:{kind}Ref>"


def series(name: str, cats: str, vals: str, extra: str = "") -> str:
    return f'<c:ser><c:tx><c:v>{name}</c:v></c:tx>{extra}<c:cat>{cats}</c:cat><c:val>{vals}</c:val></c:ser>'


def chart_text(tmp: Path, chart: bytes, parts: dict[str, bytes] | None = None, chart_rels: str = "") -> list[str]:
    body = lm.para(lm.drawing('<c:chart r:id="rIdChart"/>', lm.CHART_URI))
    rels = [f'<Relationship Id="rIdChart" Type="{lm.REL_NS}/chart" Target="charts/chart1.xml"/>']
    files = {"word/charts/chart1.xml": chart, **(parts or {})}
    if chart_rels:
        files["word/charts/_rels/chart1.xml.rels"] = chart_rels.encode()
    # The fixture's own words are kept, as an owner would keep them.
    words = re.findall(r"[A-Za-z]{3,}", " ".join(re.findall(r">([^<>]+)<", b"".join(files.values()).decode())))
    lm.write_profile(tmp / "profile.json", {"safe_words": sorted(set(words) - {"Aurelan"})})
    result = run(word_file(tmp, body, files, rels), tmp)
    assert result.status == "ready", result.reasons
    out = tmp / "out" / result.output
    assert leaks(out) == []
    return output_paragraphs(out)


def test_chart_values_appear_as_the_chart_shows_them(tmp_path):
    """Percentages, thousands and dates are formatted with the chart's own number format."""
    dates = cache("num", [45292, 45323], "mmm yy")
    plot = ("<c:barChart><c:barDir val=\"col\"/>"
            + series("Share", dates, cache("num", [0.25, 0.375], "0.0%"))
            + series("Volume", dates, cache("num", [1834567, 2500000], "#,##0"))
            + '<c:axId val="1"/><c:axId val="2"/></c:barChart>'
            '<c:catAx><c:axId val="1"/><c:title><c:tx><c:rich><a:p><a:r><a:t>Month</a:t></a:r></a:p></c:rich></c:tx></c:title></c:catAx>'
            '<c:valAx><c:axId val="2"/><c:dispUnits><c:builtInUnit val="millions"/></c:dispUnits></c:valAx>')
    text = chart_text(tmp_path, chart_space(plot, "Aurelan uptake"))
    joined = "\n".join(text)
    assert "Chart: [CLIENT] uptake (column chart, shown as a table of its values)" in text
    assert "Axes: categories: Month" in text
    for cell in ("Jan 24", "Feb 24", "25.0%", "37.5%", "1,834,567", "2,500,000"):
        assert cell in text, cell
    assert "0.375" not in joined and "45292" not in joined, "raw stored values never show"
    assert any("shows them in millions" in t for t in text)


def test_chart_gaps_and_missing_values_are_never_filled(tmp_path):
    cats = cache("str", ["North", "South", "East"])
    plot = ("<c:lineChart>" + series("Actual", cats, cache("num", [10, None, 30]))
            + '<c:ser><c:tx><c:v>Target</c:v></c:tx><c:cat>' + cats + '</c:cat><c:val><c:numRef><c:f>Sheet1!C1</c:f></c:numRef></c:val></c:ser>'
            + "</c:lineChart>")
    text = chart_text(tmp_path, chart_space(plot, "Sales"))
    rows = [t for t in text if t in ("North", "South", "East", "10", "30")]
    assert rows == ["North", "10", "South", "East", "30"], "the gap stays empty, values stay on their own rows"
    assert any("does not store the values of Target" in t for t in text)


def test_multi_level_categories_keep_both_levels(tmp_path):
    multi = ('<c:multiLvlStrRef><c:f>Sheet1!A1</c:f><c:multiLvlStrCache><c:ptCount val="4"/>'
             '<c:lvl><c:pt idx="0"><c:v>Q1</c:v></c:pt><c:pt idx="1"><c:v>Q2</c:v></c:pt><c:pt idx="2"><c:v>Q1</c:v></c:pt><c:pt idx="3"><c:v>Q2</c:v></c:pt></c:lvl>'
             '<c:lvl><c:pt idx="0"><c:v>2024</c:v></c:pt><c:pt idx="2"><c:v>2025</c:v></c:pt></c:lvl></c:multiLvlStrCache></c:multiLvlStrRef>')
    plot = "<c:barChart>" + series("Revenue", multi, cache("num", [1, 2, 3, 4])) + "</c:barChart>"
    text = chart_text(tmp_path, chart_space(plot, "Revenue"))
    assert [t for t in text if "/" in t] == ["2024 / Q1", "2024 / Q2", "2025 / Q1", "2025 / Q2"]


def test_scatter_chart_keeps_its_pairs(tmp_path):
    plot = ('<c:scatterChart><c:ser><c:tx><c:v>Latency</c:v></c:tx>'
            f'<c:xVal>{cache("num", [100, 200])}</c:xVal><c:yVal>{cache("num", [12.5, 30])}</c:yVal>'
            '<c:trendline><c:trendlineType val="linear"/></c:trendline></c:ser></c:scatterChart>')
    text = chart_text(tmp_path, chart_space(plot, "Load"))
    assert ["Latency", "100", "12.5", "Latency", "200", "30"] == [t for t in text if t in ("Latency", "100", "200", "12.5", "30")]
    assert any("also shows a trend line" in t for t in text)


def test_text_placed_on_a_chart_is_kept_and_sanitized(tmp_path):
    shapes = ('<c:userShapes xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
              'xmlns:cdr="http://schemas.openxmlformats.org/drawingml/2006/chartDrawing" '
              'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><cdr:relSizeAnchor><cdr:sp><cdr:txBody>'
              '<a:p><a:r><a:t>Aurelan target</a:t></a:r></a:p></cdr:txBody></cdr:sp></cdr:relSizeAnchor></c:userShapes>')
    plot = "<c:barChart>" + series("Revenue", cache("str", ["Q1"]), cache("num", [5])) + "</c:barChart>"
    rels = (lm.DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{lm.REL_NS}/chartUserShapes" Target="../drawings/drawing1.xml"/></Relationships>')
    text = chart_text(tmp_path, chart_space(plot, "Revenue", '<c:userShapes r:id="rId1"/>'),
                      {"word/drawings/drawing1.xml": shapes.encode()}, rels)
    assert "Text on the chart: [CLIENT] target" in text


SMARTART_LAYOUT = (
    '<dgm:layoutDef xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
    'uniqueId="urn:microsoft.com/office/officeart/2005/8/layout/process1"><dgm:catLst><dgm:cat type="process" pri="1000"/></dgm:catLst></dgm:layoutDef>'
)


def test_smartart_process_is_numbered_and_named(tmp_path):
    body = lm.para(lm.drawing('<dgm:relIds r:dm="rIdDm" r:lo="rIdLo" r:qs="rIdQs" r:cs="rIdCs"/>', lm.DGM_URI))
    rels = [f'<Relationship Id="rIdDm" Type="{lm.REL_NS}/diagramData" Target="diagrams/data1.xml"/>',
            f'<Relationship Id="rIdLo" Type="{lm.REL_NS}/diagramLayout" Target="diagrams/layout1.xml"/>']
    src = word_file(tmp_path, body, {"word/diagrams/data1.xml": SMARTART.encode(), "word/diagrams/layout1.xml": SMARTART_LAYOUT.encode()}, rels)
    result = run(src, tmp_path)
    assert result.status == "ready", result.reasons
    out = tmp_path / "out" / result.output
    assert "Diagram (process, shown as a list)" in output_paragraphs(out)
    with zipfile.ZipFile(out) as zf:
        document = zf.read("word/document.xml").decode()
    portal = re.search(r"<w:p>(?:(?!</w:p>).)*\[CLIENT\] portal", document).group()
    assert '<w:numId w:val="2"/>' in portal, "the steps of a process are numbered"


SHAPES_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="900" height="420" viewBox="0 0 900 420">
<defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="#1553a8"/><stop offset="1" stop-color="#e3ecfb"/></linearGradient>
<marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#333"/></marker></defs>
<rect width="900" height="420" fill="white"/>
<rect x="30" y="30" width="220" height="90" rx="14" fill="url(#g)" stroke="#0b3d91" stroke-width="3"/>
<text x="50" y="85" font-family="Helvetica" font-size="24" fill="white">Order service</text>
<ellipse cx="460" cy="75" rx="120" ry="50" fill="#e0f2e9" stroke="#0b6b41" stroke-width="2"/>
<text x="395" y="83" font-family="Times" font-size="22">Billing</text>
<line x1="250" y1="75" x2="335" y2="75" stroke="#333" stroke-width="3" marker-end="url(#arrow)"/>
<path d="M 580 75 C 650 20, 700 140, 780 75" fill="none" stroke="#a81f16" stroke-width="3" stroke-dasharray="10,6"/>
<polygon points="700,200 860,200 820,300 660,300" fill="#fbeedb" stroke="#8a4f00" stroke-width="2"/>
<text x="690" y="258" font-family="Helvetica" font-size="18">Data lake</text>
<rect x="30" y="200" width="300" height="160" fill="none" stroke="#5f5b57" stroke-width="2" stroke-dasharray="4,4"/>
<text x="45" y="235" font-family="Courier" font-size="16">Integration layer</text>
<text x="45" y="265" font-family="Helvetica" font-size="12">REST, events and batch files</text>
<text transform="translate(380 380) rotate(-30)" font-family="Helvetica" font-size="20">Rotated label</text>
<circle cx="560" cy="300" r="40" fill="#7cc0f5"/>
</svg>"""


@needs_office
def test_emf_drawing_renders_like_libreoffice(tmp_path):
    """DocSan's own EMF renderer against LibreOffice's, on one drawing with
    gradients, curves, dashes, arrows, polygons and rotated text: the ink
    must agree within a pixel, so nothing is missing, added or moved."""
    from docsan.image.vector import render

    emf = vector("emf", SHAPES_SVG)
    drawing = render(emf, tmp_path)
    ours = tmp_path / "ours.png"
    ours.write_bytes(drawing.png)
    src = tmp_path / "shapes.emf"
    src.write_bytes(emf)
    subprocess.run([str(SOFFICE), "--headless", "--norestore", f"-env:UserInstallation={(tmp_path / 'lo').as_uri()}",
                    "--convert-to", "pdf", "--outdir", str(tmp_path), str(src)], check=True, capture_output=True, timeout=300)
    tool = tmp_path / "pdf-compare"
    subprocess.run(["xcrun", "swiftc", "-O", "-o", str(tool), str(Path(__file__).parent / "tools" / "pdf-compare.swift")],
                   check=True, capture_output=True, timeout=600)
    result = subprocess.run([str(tool), str(ours), str(tmp_path / "shapes.pdf"), "110"], check=True, capture_output=True, text=True)
    score = __import__("json").loads(result.stdout)["scores"][0]
    assert score >= 0.98, score
    words = " ".join(t["text"] for t in drawing.texts)
    for label in ("Order service", "Billing", "Data lake", "Integration layer", "Rotated label"):
        assert label in words, label


def emf(*records: bytes) -> bytes:
    """A minimal EMF: a header, the records, the end record."""
    import struct

    body = b"".join(records)
    eof = struct.pack("<IIIII", 14, 20, 0, 16, 20)
    # iType, nSize, bounds, frame (0.01 mm), signature, version, nBytes, nRecords,
    # nHandles, reserved, description, palette, device pixels, device millimetres.
    header = struct.pack("<II4i4iIIIIHHIII2i2i", 1, 88, 0, 0, 400, 200, 0, 0, 10583, 5291, 0x464D4520, 0x10000,
                         88 + len(body) + 20, 3, 1, 0, 0, 0, 0, 1920, 1080, 508, 286)
    return header + body + eof


def test_drawing_with_records_docsan_cannot_draw_is_refused(tmp_path):
    """A flood fill DocSan cannot replay would leave the diagram incomplete: refuse it."""
    import struct

    flood = struct.pack("<II2iIi", 53, 24, 10, 10, 0x0000FF, 1)
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.emf": emf(flood)}, [image_rel("rIdImg", "media/image1.emf")])
    result = run(src, tmp_path, out=False)
    assert result.status == "blocked"
    assert any("EXTFLOODFILL" in r and "incomplete" in r for r in result.reasons), result.reasons


def test_drawing_kept_only_as_emf_plus_is_refused(tmp_path):
    """Without the "dual" flag the plain EMF records are a stub: drawing them would show an empty diagram."""
    import struct

    plus = struct.pack("<HHIIIIII", 0x4001, 0, 28, 16, 0xDBC01002, 0, 96, 96)
    comment = struct.pack("<III", 70, 12 + 4 + len(plus), 4 + len(plus)) + struct.pack("<I", 0x2B464D45) + plus
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.emf": emf(comment)}, [image_rel("rIdImg", "media/image1.emf")])
    result = run(src, tmp_path, out=False)
    assert result.status == "blocked"
    assert any("EMF+ only" in r for r in result.reasons), result.reasons


def test_unreadable_visuals_can_become_labels_while_the_rest_is_kept(tmp_path):
    """The middle choice: a WMF picture becomes a label, the chart and the EMF diagram stay."""
    body = (lm.para(picture("rIdWmf")) + lm.para(lm.drawing('<c:chart r:id="rIdChart"/>', lm.CHART_URI))
            + "<w:p>" + lm.run("Formula: ") + "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath></w:p>")
    rels = [image_rel("rIdWmf", "media/image1.wmf"), f'<Relationship Id="rIdChart" Type="{lm.REL_NS}/chart" Target="charts/chart1.xml"/>']
    files = {"word/media/image1.wmf": b"\xd7\xcd\xc6\x9a" + b"\x00" * 40, "word/charts/chart1.xml": CHART}
    src = word_file(tmp_path, body, files, rels)
    assert run(src, tmp_path, out=False).status == "blocked"
    profile = tmp_path / "profile.json"
    result = run_batch([src], profile, tmp_path / "out", tmp_path / "private", lexicon=lm_lexicon(), label_unreadable=True).files[0]
    assert result.status == "ready", result.reasons
    text = output_paragraphs(tmp_path / "out" / result.output)
    assert "[IMAGE-REMOVED]" in text and "Formula: [EQUATION-REMOVED]" in text
    assert "Chart: [CLIENT] revenue (bar chart, shown as a table of its values)" in text, "the chart is still kept"


def lm_lexicon():
    from docsan.review import Lexicon

    return Lexicon.load(lm.LEXICON)
