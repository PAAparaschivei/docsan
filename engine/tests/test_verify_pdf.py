"""The PDF verifier on its own: crafted outputs it must pass, flag or refuse."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import imagekit
import imagematrix as im
import leakmatrix as lm
import pytest
from docsan import verify
from docsan.image.encode import clean_jpeg
from docsan.pdf import write
from docsan.pdf.plan import OutLine
from docsan.pdf.write import OutPage, pdf_bytes, png_rows

SIZE = (1000, 300)  # pixels of each page's picture
POINTS = (360.0, 108.0)
CLEAN_PICTURE = [im.line("Integration overview", 30)]


def page_jpeg(tmp: Path, items: list[dict]) -> bytes:
    path = tmp / "page.jpg"
    imagekit.render({"output": str(path), "format": "jpeg", "width": SIZE[0], "height": SIZE[1], "items": items})
    return clean_jpeg(path.read_bytes())


def released(tmp: Path, lines=("Integration overview",), items=CLEAN_PICTURE, pages: int = 1, **patches) -> bytes:
    """A file exactly as the writer makes it, with ``patches`` applied to the writer's constants."""
    jpeg = page_jpeg(tmp, items)
    out = [OutPage(jpeg, *SIZE, POINTS, [OutLine(t, (40, 40 + 60 * i, 600, 40)) for i, t in enumerate(lines)])] * pages
    with mock.patch.multiple(write, **patches) if patches else contextlib.nullcontext():
        return pdf_bytes(out)


def page_rows(tmp: Path, items: list[dict]) -> tuple[int, int, int, bytes]:
    """A page picture as the writer stores it without loss: the helper's PNG, as rows."""
    path, png = tmp / "page.png", tmp / "page-out.png"
    imagekit.render({"output": str(path), "format": "png", "width": SIZE[0], "height": SIZE[1], "items": items})
    subprocess.run([str(imagekit.helper())], check=True, capture_output=True, input=json.dumps(
        {"command": "mask", "input": str(path), "output": str(png), "format": "png", "boxes": []}).encode())
    return png_rows(png.read_bytes())


def lossless(tmp: Path, lines=("Integration overview",), items=CLEAN_PICTURE, edit=None) -> bytes:
    width, height, colors, rows = page_rows(tmp, items)
    if edit:
        rows = edit(rows)
    page = OutPage(rows, width, height, POINTS, [OutLine(t, (40, 40 + 60 * i, 600, 40)) for i, t in enumerate(lines)],
                   "flate", colors)
    return pdf_bytes([page])


def unknown_filter(rows: bytes) -> bytes:
    raw = bytearray(zlib.decompress(rows))
    raw[0] = 7
    return zlib.compress(bytes(raw))


def with_exif(tmp: Path) -> bytes:
    jpeg = page_jpeg(tmp, CLEAN_PICTURE)
    tagged = jpeg[:2] + b"\xff\xe1\x00\x12Exif\x00\x00Aurelan\x00\x00\x00" + jpeg[2:]
    return pdf_bytes([OutPage(tagged, *SIZE, POINTS, [OutLine("Integration overview", (40, 40, 600, 40))])])


def other_id(tmp: Path) -> bytes:
    data = released(tmp)
    return re.sub(rb"/ID \[<([0-9A-F]{32})><\1>\]", b"/ID [<" + b"A" * 32 + b"><" + b"A" * 32 + b">]", data)


def barcode_pdf(tmp: Path) -> bytes:
    code = tmp / "code.png"
    maker = imagekit._build(Path(__file__).parent / "tools" / "make-code.swift", "make-code")
    subprocess.run([str(maker), str(code)], check=True)
    w, h, colors, rows = png_rows(code.read_bytes())
    return pdf_bytes([OutPage(rows, w, h, (300, 300), [], "flate", colors)])


@dataclass
class Case:
    name: str
    build: object  # (tmp) -> bytes
    expect: str
    ocr: str = "real"  # real, blind or wrong-size
    extractor: str = "real"  # real, leaky or broken


_real_content = write.content

CASES = [
    Case("clean", lambda t: released(t), "pass"),
    Case("unknown-client-text", lambda t: released(t, ("Seabright Holdings Ltd",)), "findings"),
    Case("unknown-address-text", lambda t: released(t, ("23 RUE DES PINS",)), "findings"),
    Case("unknown-client-pixels", lambda t: released(t, lines=(), items=[im.line("Seabright Holdings Ltd", 30)]), "findings"),

    Case("barcode", barcode_pdf, "findings"),
    Case("two-pages", lambda t: released(t, pages=2), "pass"),
    Case("name-in-text", lambda t: released(t, ("Integration overview", "Aurelan approved it")), "findings"),
    Case("name-split-across-lines", lambda t: released(t, ("The platform is run by the Aure-", "lan team")), "findings"),
    Case("accented-name", lambda t: released(t, ("Réunion avec Aurélan",)), "findings"),
    Case("phone-in-text", lambda t: released(t, ("Call +352 26 12 34 56 now",)), "findings"),
    Case("kept-system-name", lambda t: released(t, ("Vireo platform runs nightly",)), "pass"),
    Case("exception-honoured", lambda t: released(t, ("PATCH /orders updates an order",)), "pass"),
    Case("name-in-picture", lambda t: released(t, items=[im.line("Aurelan approved it", 30)]), "findings"),
    Case("grey-page", lambda t: lossless(t), "pass"),
    Case("colour-page", lambda t: lossless(t, items=[im.line("Integration overview", 30, color=[0.8, 0.1, 0.1])]), "pass"),
    Case("name-in-grey-page", lambda t: lossless(t, items=[im.line("Aurelan approved it", 30)]), "findings"),
    # Rows with a filter the writer never uses: a structure finding.
    Case("unknown-row-filter", lambda t: lossless(t, edit=unknown_filter), "findings"),
    Case("metadata-with-name", lambda t: released(t, INFO=b"<< /Producer (DocSan) /Title (Aurelan Group plan) >>"), "findings"),
    Case("metadata-title", lambda t: released(t, INFO=b"<< /Producer (DocSan) /Title (Plan) >>"), "findings"),
    Case("trailing-bytes", lambda t: released(t) + b"Aurelan payload", "findings"),
    Case("other-font", lambda t: released(t, FONT=write.FONT.replace(b"/Helvetica", b"/Courier")), "findings"),
    Case("picture-metadata", with_exif, "findings"),
    Case("file-identifier", other_id, "findings"),
    Case("visible-text", lambda t: released(t, content=lambda page: _real_content(page).replace(b"BT 3 Tr", b"BT 0 Tr")), "findings"),
    Case("blind-ocr", lambda t: released(t, items=[im.line("Aurelan approved it", 30)]), "could_not_inspect", ocr="blind"),
    Case("wrong-size-ocr", lambda t: released(t), "could_not_inspect", ocr="wrong-size"),
    Case("extractor-only-leak", lambda t: released(t), "findings", extractor="leaky"),
    Case("broken-extractor", lambda t: released(t), "could_not_inspect", extractor="broken"),
    Case("not-a-pdf", lambda t: b"plain text", "could_not_inspect"),
]


def run_case(case: Case, tmp: Path, monkeypatch) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"{case.name}.pdf"
    path.write_bytes(case.build(tmp))
    real_ocr, real_text = verify.read_with_tesseract, verify.extract_pdf_with_pdfkit
    if case.ocr == "blind":
        monkeypatch.setattr(verify, "image_ocr", lambda p, layout: (real_ocr(p, layout)[0], []))
    elif case.ocr == "wrong-size":
        monkeypatch.setattr(verify, "image_ocr", lambda p, layout: ((1, 1), real_ocr(p, layout)[1]))
    else:
        monkeypatch.setattr(verify, "image_ocr", real_ocr)
    if case.extractor == "leaky":
        monkeypatch.setattr(verify, "pdf_extractor", lambda p: [t + ("" if p.name == "self-test.pdf" else "\nAurelan") for t in real_text(p)])
    elif case.extractor == "broken":
        monkeypatch.setattr(verify, "pdf_extractor", lambda p: ["" for _ in real_text(p)])
    else:
        monkeypatch.setattr(verify, "pdf_extractor", real_text)
    return verify.verify_pdf(path, lm.PROFILE).status


@pytest.fixture(autouse=True)
def helper():
    imagekit.helper()


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case, tmp_path, monkeypatch):
    assert run_case(case, tmp_path, monkeypatch) == case.expect


@pytest.mark.parametrize("step", verify.PDF_VERIFIER_STEPS)
def test_pdf_verifier_step_is_needed(step, tmp_path, monkeypatch):
    # The text views are tested without PDFKit, which would otherwise find the
    # same names; PDFKit and the self-test are tested as built.
    baseline = [] if step in ("pdf-second-extractor", "pdf-self-test") else ["pdf-second-extractor"]
    cases = [c for c in CASES if not (baseline and c.extractor != "real")]
    for off in baseline:
        verify._off.add(off)
    try:
        assert all(run_case(c, tmp_path / c.name, monkeypatch) == c.expect for c in cases), "baseline must pass"
        with verify.step_disabled(step):
            if any(run_case(c, tmp_path / f"off-{c.name}", monkeypatch) != c.expect for c in cases):
                return
    finally:
        for off in baseline:
            verify._off.discard(off)
    pytest.fail(f"no PDF verifier case changes with step {step!r} disabled")


def test_font_pin_matches_writer():
    assert verify.PDF_FONT_SHA256 == hashlib.sha256(write.FONT).hexdigest()


def test_findings_carry_page_regions(tmp_path):
    path = tmp_path / "name.pdf"
    path.write_bytes(released(tmp_path, items=[im.line("Aurelan approved it", 30)], pages=2))
    result = verify.verify_pdf(path, lm.PROFILE)
    assert result.status == "findings" and set(result.media_regions) == {"page-1", "page-2"}
    x, y, w, _ = result.media_regions["page-1"][0]
    assert 20 <= x <= 60 and 15 <= y <= 50 and w > 100


def test_verifier_reads_its_own_objects(tmp_path):
    """The writer's text comes back through the verifier's own parser, escapes included."""
    data = released(tmp_path, ("Prix: 12 € (net) \\ total", "Ligne deux"))
    pages, problems = verify._pdf_pages(verify._pdf_parse(data)[0])
    assert problems == [] and pages[0].lines == ["Prix: 12 € (net) \\ total", "Ligne deux"]


def test_grey_and_colour_pages_are_stored_without_loss(tmp_path):
    grey = page_rows(tmp_path, CLEAN_PICTURE)
    colour = page_rows(tmp_path, [im.line("Integration overview", 30, color=[0.8, 0.1, 0.1])])
    assert grey[2] == 1 and colour[2] == 3
