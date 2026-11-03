"""The page description the app draws while the owner reviews (docsan.docx.pages)."""

from __future__ import annotations

import formatkit
import leakmatrix as lm

from docsan.docx.pages import number_text, page_model
from docsan.docx.read import read_docx


def model(tmp_path, data: bytes) -> dict:
    src = tmp_path / "plan.docx"
    src.write_bytes(data)
    return page_model(read_docx(src).document, {})


def texts(blocks: list[dict]) -> list[str]:
    return ["".join(r["text"] for r in b.get("runs", [])) for b in blocks if b["t"] == "p"]


def test_list_numbers_are_counted_as_word_counts_them(tmp_path):
    blocks = model(tmp_path, formatkit.formatting_docx(names=False, brand_font="Calibri"))["sections"][0]["blocks"]
    labels = {"".join(r["text"] for r in b["runs"]): b["label"]["text"] for b in blocks if b.get("label")}
    assert labels["Context"] == "1" and labels["Scope"] == "1.1" and labels["Decisions"] == "1.2" and labels["Capabilities"] == "2"
    assert labels["Customer channels"] == "•" and labels["Web and mobile"] == "◦"
    assert labels["Keep the integration layer"] == "a)" and labels["Retire the batch interfaces"] == "b)"
    assert labels["A new list starts again"] == "a)", "a restarted list starts again"


def test_tables_keep_merges_borders_and_shading(tmp_path):
    blocks = model(tmp_path, formatkit.formatting_docx(names=False, brand_font="Calibri"))["sections"][0]["blocks"]
    table = next(b for b in blocks if b["t"] == "table")
    header, identity = table["rows"][0], table["rows"][2]
    assert header["header"] and all(c["fill"] == "4472C4" for c in header["cells"])
    assert identity["cells"][0]["rowspan"] == 2 and identity["cells"][1]["span"] == 2
    assert len(table["rows"][3]["cells"]) == 2, "the merged cell below is not repeated"
    assert table["columns"] == [150.25, 150.25, 150.8]
    assert identity["cells"][0]["borders"]["left"]["color"] == "8EAADB"


def test_sections_headers_and_page_numbers(tmp_path):
    m = model(tmp_path, formatkit.formatting_docx(names=False, brand_font="Calibri"))
    first, last = m["sections"]
    assert first["page"]["w"] < first["page"]["h"] and last["page"]["w"] > last["page"]["h"]
    assert texts(last["headers"]["default"]) == texts(first["headers"]["default"]), "the landscape section inherits its header"
    footer = last["footers"]["default"][0]["runs"]
    assert [r.get("field") for r in footer if r.get("field")] == ["PAGE", "NUMPAGES"]


def test_floating_drawings_keep_their_anchor(tmp_path):
    from test_formatting import FLOATING

    header = lm.DECL + f"<w:hdr {lm.NS}>{lm.para(FLOATING)}</w:hdr>"
    data = lm.zip_bytes(lm.package(lm.text_para("Body text."), sect='<w:headerReference w:type="default" r:id="rIdH"/>',
                                   parts={"word/header1.xml": header},
                                   doc_rels=[f'<Relationship Id="rIdH" Type="{lm.REL_NS}/header" Target="header1.xml"/>']))
    box = model(tmp_path, data)["sections"][0]["headers"]["default"][0]["drawings"][0]
    assert box["kind"] == "box" and box["anchor"]["hf"] == "page" and box["anchor"]["hv"] == "5400000"
    assert texts(box["blocks"]) == ["Aurelan Internal"]


def test_number_styles():
    assert [number_text(n, "lowerRoman") for n in (1, 4, 9, 14)] == ["i", "iv", "ix", "xiv"]
    assert [number_text(n, "upperLetter") for n in (1, 26, 27)] == ["A", "Z", "AA"]
    assert number_text(3, "decimalZero") == "03" and number_text(12, "ordinal") == "12th" and number_text(22, "ordinal") == "22nd"
