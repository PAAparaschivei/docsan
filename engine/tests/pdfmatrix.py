"""PDF fixtures: synthetic PDFs with planted references, and the checks they must pass.

Each fixture plants the fictional company "Aurelan Group" (or its people,
labels, links or phone numbers) in one place of a PDF: the text layer, a
scanned page, a picture, a name split across lines, the metadata, an
annotation, a form field, an attached file, a script, a hidden layer. Quartz
writes the base file (``tools/make-pdf.swift``) and reports where it drew
every word; ``pdfedit`` adds the parts Quartz does not write. After the owner
confirms every page, the released file is checked by test-only oracles that
share nothing with the sanitizer or the verifier:

- every pixel of every planted word is black, and words that must stay are not masked;
- Apple Vision reads no planted name anywhere in the released pages;
- no planted name is in the file's bytes, with every compressed stream inflated;
- PDFKit's text extraction holds the expected text and no planted name.

All names are invented for testing.
"""

from __future__ import annotations

import json
import math
import re
import struct
import subprocess
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import imagekit
import leakmatrix as lm
from docsan.pipeline import FileResult, run_batch
from docsan.review import Lexicon
from harness import skeleton
from imagematrix import mostly_masked
from pdfedit import PdfEdit, stream
from pngio import Pixels

A4 = [595, 842]


def text(t: str, y: float, size: float = 12, x: float = 72, **extra) -> dict:
    return {"text": t, "x": x, "y": y, "size": size, **extra}


def picture(lines: list[tuple[str, float, float]], box: list[float], px: tuple[int, int]) -> dict:
    """Text drawn into a picture placed at ``box`` (points); each line is (text, y, size) in pixels."""
    return {"image": {"width": px[0], "height": px[1], "items": [{"text": t, "x": 20, "y": y, "size": s} for t, y, s in lines]},
            "box": box}


@dataclass
class PdfFixture:
    name: str
    pages: list[list[dict]]  # each page's items
    expect: str = "ready"  # ready, needs_review or blocked
    edit: Callable[[bytes], bytes] | None = None
    password: str | None = None
    masked: tuple[str, ...] = ()  # words whose every ink pixel must be masked
    masked_regions: tuple[tuple[float, float, float, float], ...] = ()
    visible: tuple[str, ...] = ()  # words that must stay unmasked
    text_has: tuple[str, ...] = ()  # text the output's text layer must hold
    text_not: tuple[str, ...] = ()  # text it must not hold
    review: tuple[str, ...] = ()  # words the review must ask about
    reason: str = ""  # part of the blocking reason
    dropped: tuple[str, ...] = ()  # kinds of parts the result must list as removed
    profile: dict = field(default_factory=dict)
    rotate: int = 0  # the /Rotate the edit sets on the first page

    @property
    def file(self) -> str:
        return f"{self.name}.pdf"


# --- Edits: parts Quartz does not write ------------------------------------------------------

def _font(e: PdfEdit) -> int:
    return e.add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")


def _resources(e: PdfEdit, page: int) -> int:
    return int(re.search(rb"/Resources (\d+) 0 R", e.get(page)).group(1))


def _add_resource(e: PdfEdit, page: int, kind: bytes, entry: bytes) -> None:
    res = _resources(e, page)
    body = e.get(res)
    if b"/" + kind + b" <<" in body:
        body = body.replace(b"/" + kind + b" <<", b"/" + kind + b" << " + entry, 1)
    else:
        body = body[:-2].rstrip() + b" /" + kind + b" << " + entry + b" >> >>"
    e.set(res, body)


def _add_content(e: PdfEdit, page: int, data: bytes) -> None:
    extra = e.add(stream(b"", data))
    body = e.get(page)
    m = re.search(rb"/Contents (\d+) 0 R", body)
    e.set(page, body.replace(m.group(0), b"/Contents [" + m.group(1) + b" 0 R %d 0 R]" % extra))


def plant_metadata(data: bytes) -> bytes:
    e = PdfEdit(data)
    e.info = e.add(b"<< /Title (Aurelan Group plan) /Author (Elena Marchetti) /Keywords (C2 Restricted) "
                   b"/Creator (Aurelan Word) /Producer (Aurelan PDF) >>")
    xmp = e.add(stream(b"/Type /Metadata /Subtype /XML",
                       b"<x:xmpmeta><dc:creator>Elena Marchetti</dc:creator><dc:title>Aurelan Group</dc:title></x:xmpmeta>",
                       compress=False))
    e.add_to_dict(e.root, b"/Metadata %d 0 R /Lang (en-US) /PageLabels << /Nums [0 << /P (Aurelan-) >>] >>" % xmp)
    return e.save()


def plant_comments(data: bytes) -> bytes:
    """A sticky note with its popup, a link and a bookmark, all naming the company."""
    e = PdfEdit(data)
    page = e.page(0)
    popup = e.add(b"<< /Type /Annot /Subtype /Popup /Rect [320 600 520 700] /Open false >>")
    note = e.add(b"<< /Type /Annot /Subtype /Text /Rect [300 700 320 720] /Contents (Aurelan Group comment) "
                 b"/T (Elena Marchetti) /Popup %d 0 R /F 4 >>" % popup)
    link = e.add(b"<< /Type /Annot /Subtype /Link /Rect [72 760 200 780] /Border [0 0 0] "
                 b"/A << /S /URI /URI (https://intranet.aurelan.lu/plan) >> >>")
    e.add_to_dict(page, b"/Annots [%d 0 R %d 0 R %d 0 R]" % (note, popup, link))
    outline = e.add(b"<< /Type /Outlines /Count 1 >>")
    item = e.add(b"<< /Title (Aurelan Group plan) /Parent %d 0 R /Dest [%d 0 R /Fit] >>" % (outline, page))
    e.set(outline, b"<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count 1 >>" % (item, item))
    e.add_to_dict(e.root, b"/Outlines %d 0 R /PageMode /UseOutlines" % outline)
    return e.save()


def plant_free_text(data: bytes) -> bytes:
    """A free text annotation whose appearance shows a name on the page."""
    e = PdfEdit(data)
    page = e.page(0)
    font = _font(e)
    ap = e.add(stream(b"/Type /XObject /Subtype /Form /BBox [0 0 260 30] /Resources << /Font << /F1 %d 0 R >> >>" % font,
                      b"BT /F1 16 Tf 4 9 Td (Note for Aurelan) Tj ET"))
    ft = e.add(b"<< /Type /Annot /Subtype /FreeText /Rect [72 500 332 530] /Contents (Note for Aurelan) /T (Elena Marchetti) "
               b"/DA (/Helv 16 Tf 0 g) /AP << /N %d 0 R >> /F 4 >>" % ap)
    e.add_to_dict(page, b"/Annots [%d 0 R]" % ft)
    return e.save()


def plant_bare_appearance(data: bytes) -> bytes:
    """A free text annotation whose appearance stream leaves out /Type and /Subtype, as some producers write it."""
    e = PdfEdit(data)
    page = e.page(0)
    font = _font(e)
    ap = e.add(stream(b"/BBox [0 0 260 30] /Resources << /Font << /F1 %d 0 R >> >>" % font,
                      b"BT /F1 16 Tf 4 9 Td (Note for Aurelan) Tj ET"))
    ft = e.add(b"<< /Type /Annot /Subtype /FreeText /Rect [72 500 332 530] /Contents (Note for Aurelan) "
               b"/DA (/Helv 16 Tf 0 g) /AP << /N %d 0 R >> /F 4 >>" % ap)
    e.add_to_dict(page, b"/Annots [%d 0 R]" % ft)
    return e.save()


def plant_form_field(data: bytes) -> bytes:
    """A filled text field: the value shows on the page, and the field names the company."""
    e = PdfEdit(data)
    page = e.page(0)
    font = _font(e)
    ap = e.add(stream(b"/Type /XObject /Subtype /Form /BBox [0 0 260 28] /Resources << /Font << /F1 %d 0 R >> >>" % font,
                      b"/Tx BMC BT /F1 16 Tf 2 8 Td (Aurelan Group) Tj ET EMC"))
    field_ = e.add(b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (aurelan_company) /TU (Aurelan field) /V (Aurelan Group) "
                   b"/Rect [72 400 332 428] /AP << /N %d 0 R >> /F 4 /P %d 0 R /DA (/Helv 16 Tf 0 g) >>" % (ap, page))
    e.add_to_dict(page, b"/Annots [%d 0 R]" % field_)
    e.add_to_dict(e.root, b"/AcroForm << /Fields [%d 0 R] /DR << /Font << /Helv %d 0 R >> >> /DA (/Helv 0 Tf 0 g) >>" % (field_, font))
    return e.save()


def plant_attachment(data: bytes) -> bytes:
    """An attached file in the name tree and a file attachment annotation."""
    e = PdfEdit(data)
    page = e.page(0)
    ef = e.add(stream(b"/Type /EmbeddedFile /Subtype /text#2Fplain", b"Aurelan Group notes by Elena Marchetti, C2 Restricted"))
    spec = e.add(b"<< /Type /Filespec /F (aurelan-notes.txt) /UF (aurelan-notes.txt) /EF << /F %d 0 R >> "
                 b"/Desc (Aurelan notes) >>" % ef)
    annot = e.add(b"<< /Type /Annot /Subtype /FileAttachment /Rect [500 780 520 800] /FS %d 0 R "
                  b"/Contents (Aurelan attachment) /Name /PushPin /F 4 >>" % spec)
    e.add_to_dict(page, b"/Annots [%d 0 R]" % annot)
    e.add_to_dict(e.root, b"/Names << /EmbeddedFiles << /Names [(aurelan-notes.txt) %d 0 R] >> >>" % spec)
    return e.save()


def plant_script(data: bytes) -> bytes:
    e = PdfEdit(data)
    js = e.add(b"<< /S /JavaScript /JS (app.alert\\('Aurelan Group, Elena Marchetti'\\)) >>")
    e.add_to_dict(e.root, b"/OpenAction %d 0 R /Names << /JavaScript << /Names [(init) %d 0 R] >> >>" % (js, js))
    return e.save()


def plant_hidden_layer(data: bytes) -> bytes:
    """A layer hidden by default that holds a name and an unknown word."""
    e = PdfEdit(data)
    page = e.page(0)
    font = _font(e)
    oc = e.add(b"<< /Type /OCG /Name (Aurelan layer) >>")
    _add_resource(e, page, b"Properties", b"/oc1 %d 0 R" % oc)
    _add_resource(e, page, b"Font", b"/FH %d 0 R" % font)
    _add_content(e, page, b"/OC /oc1 BDC BT /FH 14 Tf 72 300 Td (Halvorsen Aurelan layer) Tj ET EMC")
    e.add_to_dict(e.root, b"/OCProperties << /OCGs [%d 0 R] /D << /OFF [%d 0 R] /Order [%d 0 R] >> >>" % (oc, oc, oc))
    return e.save()


def plant_producer_data(data: bytes) -> bytes:
    """A producer's private keys: an included figure's file name (pdfTeX) and page-piece data."""
    e = PdfEdit(data)
    page = e.page(0)
    figure = e.add(stream(b"/Type /XObject /Subtype /Form /BBox [0 0 10 10] /PTEX.FileName (aurelan-figure.pdf) "
                          b"/PTEX.PageNumber 1", b"0 0 m 10 10 l S"))
    _add_resource(e, page, b"XObject", b"/Fig1 %d 0 R" % figure)
    e.add_to_dict(page, b"/PieceInfo << /Aurelan << /LastModified (D:20260101) /Private (Elena Marchetti) >> >> "
                        b"/LastModified (D:20260101)")
    return e.save()


def rotate_first_page(data: bytes) -> bytes:
    e = PdfEdit(data)
    e.add_to_dict(e.page(0), b"/Rotate 90")
    return e.save()


def plant_xfa(data: bytes) -> bytes:
    e = PdfEdit(data)
    xfa = e.add(stream(b"", b"<xdp:xdp><template>Aurelan</template></xdp:xdp>"))
    e.add_to_dict(e.root, b"/AcroForm << /Fields [] /XFA %d 0 R >>" % xfa)
    return e.save()


def plant_portfolio(data: bytes) -> bytes:
    e = PdfEdit(data)
    e.add_to_dict(e.root, b"/Collection << /Type /Collection /View /D >>")
    return e.save()


def plant_redaction(data: bytes) -> bytes:
    e = PdfEdit(data)
    redact = e.add(b"<< /Type /Annot /Subtype /Redact /Rect [72 760 300 780] /QuadPoints [72 780 300 780 72 760 300 760] >>")
    e.add_to_dict(e.page(0), b"/Annots [%d 0 R]" % redact)
    return e.save()


def plant_unknown_part(data: bytes) -> bytes:
    e = PdfEdit(data)
    e.add_to_dict(e.root, b"/AurelanData << /Owner (Elena Marchetti) >>")
    return e.save()


def plant_postscript(data: bytes) -> bytes:
    e = PdfEdit(data)
    ps = e.add(stream(b"/Type /XObject /Subtype /PS", b"/Helvetica findfont 12 scalefont setfont (Aurelan) show"))
    _add_resource(e, e.page(0), b"XObject", b"/PS1 %d 0 R" % ps)
    return e.save()


def plant_external_picture(data: bytes) -> bytes:
    e = PdfEdit(data)
    image = e.add(b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 "
                  b"/F (aurelan-logo.raw) /Length 0 >>\nstream\n\nendstream")
    _add_resource(e, e.page(0), b"XObject", b"/Ext1 %d 0 R" % image)
    return e.save()


def damage_xref(data: bytes) -> bytes:
    at = data.rindex(b"startxref")
    return data[:at] + b"startxref\n17\n%%EOF\n"


# --- Fixtures --------------------------------------------------------------------------------

CLEAN = [text("Integration overview", 72, 18), text("The platform handles the data migration.", 110)]
REFERENCES = [
    text("Aurelan Group integration overview", 72, 18),
    text("Owner Elena Marchetti signed the report.", 110),
    text("Mail elena.marchetti@aurelan.lu now.", 140),
    text("Call +352 26 12 34 56 for support.", 170),
    text("PATCH /orders updates an order.", 200, font="Menlo"),
    text("Vireo gateway C2 Restricted", 230),
]
REFERENCE_WORDS = ("Aurelan", "Group", "Elena", "Marchetti", "elena.marchetti@aurelan.lu", "+352", "26", "12", "34", "56", "C2",
                   "Restricted")
KEPT_WORDS = ("integration", "overview", "Owner", "signed", "Mail", "now.", "Call", "for", "support.", "PATCH", "/orders",
              "Vireo", "gateway")
SCAN = picture([("Aurelan Group integration overview", 40, 44), ("Owner Elena Marchetti signed the report", 120, 36)],
               [50, 80, 495, 110], (1350, 300))


def fixtures() -> list[PdfFixture]:
    F = PdfFixture
    return [
        F("screenshot-fields", [[picture([("Account name: Seabright Holdings Ltd", 30, 28),
                                           ("23 RUE DES PINS", 90, 28), ("Invoice 9876543210", 150, 28),
                                           ("Amount 450 EUR", 210, 28)], [50, 150, 495, 150], (1000, 300))]],
          masked=("Seabright", "Holdings", "Ltd", "23", "RUE", "DES", "PINS", "9876543210"),
          visible=("Account", "name:", "Invoice", "Amount", "450", "EUR"), text_not=("Seabright", "9876543210"), profile={"safe_words": ["Amount", "EUR", "Invoice", "Account", "name"]}),
        F("brand-adjacent-mark", [[
            {"shape": "ellipse", "box": [25, 30, 40, 40], "color": [0.1, 0.6, 0.9], "stroke": 5},
            text("Aurelan", 42, 12, x=40), text("Integration overview", 42, 14, x=180)]],
          masked=("Aurelan",), masked_regions=((25, 30, 40, 40),), visible=("Integration", "overview"), text_not=("Aurelan",)),
        F("text-layer", [REFERENCES], masked=REFERENCE_WORDS, visible=KEPT_WORDS,
          text_has=("[CLIENT] integration overview", "PATCH /orders updates an order.", "Vireo gateway [LABEL]")),
        F("scanned-page", [[SCAN]], masked=("Aurelan", "Group", "Elena", "Marchetti"), visible=("integration", "overview", "Owner"),
          text_has=("integration overview",)),
        F("mixed-pages", [[text("Aurelan platform report", 72, 16), *CLEAN[1:]],
                          [picture([("Marchetti signed the release", 40, 44)], [50, 80, 495, 50], (1350, 140))]],
          masked=("Aurelan", "Marchetti"), visible=("platform", "report", "signed", "release"),
          text_has=("[CLIENT] platform report", "signed the release")),
        F("picture-on-text-page", [[*CLEAN, picture([("Aurelan Group portal", 40, 44)], [72, 300, 360, 60], (900, 150))]],
          masked=("Aurelan", "Group"), visible=("platform", "portal"), text_has=("platform handles the data", "portal")),
        # Labels a few pixels high in a picture, as in a dense diagram: read only on
        # the page drawn again at 400 dpi, in tiles.
        F("tiny-labels-in-picture", [[*CLEAN, picture([("Integration overview", 20, 40), ("Approved by Aurelan for the release", 100, 10)],
                                                      [50, 300, 495, 99], (3000, 600))]],
          masked=("Aurelan",), visible=("platform", "Integration")),
        F("split-across-lines", [[text("The platform is run by the Aure-", 72), text("lan team and Elena", 88),
                                  text("Marchetti signed the release.", 104)]],
          masked=("Aure-", "lan", "Elena", "Marchetti"), visible=("platform", "team", "signed"),
          text_has=("run by the [CLIENT]",), text_not=("Aure", "Elena")),
        F("tiny-text", [[*CLEAN, text("Approved by Aurelan for the release", 300, 2.4)]], masked=("Aurelan",),
          visible=("platform",)),
        F("rotated-page", [[text("Aurelan platform report", 72, 16), *CLEAN[1:]]], edit=rotate_first_page, rotate=90,
          masked=("Aurelan",), visible=("platform", "report"), text_has=("[CLIENT] platform report",)),
        F("metadata", [CLEAN], edit=plant_metadata, dropped=("document information", "XMP metadata", "page labels")),
        F("comments-and-links", [CLEAN], edit=plant_comments, dropped=("annotations, comments and links", "bookmarks")),
        F("free-text-annotation", [CLEAN], edit=plant_free_text, dropped=("annotations, comments and links",)),
        F("bare-appearance", [CLEAN], edit=plant_bare_appearance, dropped=("annotations, comments and links",)),
        # A light grey diagonal watermark: little ink, at an angle Tesseract does not read.
        F("light-watermark", [[*CLEAN, text("Aurelan Confidential", 520, 54, x=120, color=[0.86, 0.86, 0.86], angle=45)]],
          masked=("Aurelan", "Confidential"), visible=("platform",)),
        # Light grey small print: Vision alone leaves part of the name; the text layer masks all of it.
        F("light-small-print", [[*CLEAN, text("Approved by Aurelan for the release", 400, 5, color=[0.85, 0.85, 0.85])]],
          masked=("Aurelan",), visible=("platform",)),
        F("form-field", [CLEAN], edit=plant_form_field, dropped=("form fields",)),
        F("attached-file", [CLEAN], edit=plant_attachment, dropped=("attached files",)),
        F("script", [CLEAN], edit=plant_script, dropped=("scripts and opening actions",)),
        F("producer-data", [CLEAN], edit=plant_producer_data),
        # Text that leaves no ink: invisible, under an opaque box, or in a layer hidden by default.
        F("hidden-text", [[*CLEAN, text("Halden release notes", 300, mode="invisible"), text("Halvorsen signed", 340),
                           {"shape": "rect", "box": [60, 330, 200, 30], "color": [0, 0, 0]}]],
          edit=plant_hidden_layer, text_not=("Halden", "Halvorsen"), dropped=("words not visible on the page",)),
        # A text layer that does not say what the page shows: Vision's reading wins.
        F("mismatched-text-layer", [[picture([("Integration overview for the release", 40, 44)], [50, 80, 495, 50], (1350, 140)),
                                     text("lorem ipsum dolor sit amet", 92, 16, x=60, mode="invisible")]],
          text_has=("Integration overview for the release",), text_not=("lorem",)),
        F("kept-system-name", [[text("Vireo platform runs nightly.", 72)]], visible=("Vireo", "platform")),
        F("system-name-redacted", [[text("Vireo platform runs nightly.", 72)]], masked=("Vireo",), visible=("platform",),
          profile={"redact_system_names": True}),
        F("unknown-word", [[text("Approved by Halden for the release.", 72)]], expect="needs_review", review=("Halden",)),
        F("encrypted", [CLEAN], password="secret", expect="blocked", reason="encrypted"),
        F("truncated", [CLEAN], edit=lambda d: d[: len(d) // 2], expect="blocked", reason="cut short"),
        F("damaged-cross-reference", [CLEAN], edit=damage_xref, expect="blocked", reason="damaged"),
        F("not-a-pdf", [CLEAN], edit=lambda d: b"plain text, not a document", expect="blocked", reason="not a PDF"),
        F("xfa-form", [CLEAN], edit=plant_xfa, expect="blocked", reason="XFA"),
        F("portfolio", [CLEAN], edit=plant_portfolio, expect="blocked", reason="portfolio"),
        F("unapplied-redaction", [CLEAN], edit=plant_redaction, expect="blocked", reason="never applied"),
        F("unknown-part", [CLEAN], edit=plant_unknown_part, expect="blocked", reason="does not know (/AurelanData)"),
        F("postscript", [CLEAN], edit=plant_postscript, expect="blocked", reason="PostScript"),
        F("external-picture", [CLEAN], edit=plant_external_picture, expect="blocked", reason="another file"),
    ]


# --- Building and checking ---------------------------------------------------------------------

def build(f: PdfFixture, directory: Path) -> tuple[Path, list[list[dict]]]:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f.file
    spec: dict = {"output": str(path), "pages": [{"size": A4, "items": items} for items in f.pages]}
    if f.password:
        spec["password"] = f.password
    words = imagekit.make_pdf(spec)
    if f.edit:
        path.write_bytes(f.edit(path.read_bytes()))
    return path, words


_PAGE_PICTURE = re.compile(
    rb"/Subtype /Image /Width (\d+) /Height (\d+) /ColorSpace /Device(Gray|RGB) /BitsPerComponent 8 "
    rb"(/Filter /DCTDecode|/Filter /FlateDecode /DecodeParms << [^>]* >>) /Length (\d+) >>\nstream\n"
)


def page_pictures(data: bytes) -> list[tuple[str, bytes]]:
    """Each page's picture from a released file, as a file: (".jpg", JPEG) or (".png", PNG)."""
    out = []
    for m in _PAGE_PICTURE.finditer(data):
        body = data[m.end() : m.end() + int(m.group(5))]
        if b"DCTDecode" in m.group(4):
            out.append((".jpg", body))
            continue

        def chunk(kind: bytes, payload: bytes) -> bytes:
            return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

        header = struct.pack(">IIBBBBB", int(m.group(1)), int(m.group(2)), 8, 0 if m.group(3) == b"Gray" else 2, 0, 0, 0)
        out.append((".png", b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", body) + chunk(b"IEND", b"")))
    return out


def pixels(path: Path) -> Pixels:
    """Decoded pixels of a page picture, through macOS ``sips`` and an uncompressed BMP."""
    bmp = path.with_suffix(".bmp")
    subprocess.run(["/usr/bin/sips", "-s", "format", "bmp", str(path), "--out", str(bmp)], check=True, capture_output=True)
    data = bmp.read_bytes()
    offset = struct.unpack("<I", data[10:14])[0]
    _, width, height, _, bits, _ = struct.unpack("<IiiHHI", data[14:34])
    assert bits == 24, "sips wrote an unexpected BMP"
    stride = (width * 3 + 3) & ~3
    rows = range(abs(height)) if height < 0 else range(height - 1, -1, -1)
    rgb = bytearray()
    for y in rows:
        row = data[offset + y * stride : offset + y * stride + width * 3]
        out = bytearray(row)
        out[0::3], out[2::3] = row[2::3], row[0::3]
        rgb += out
    return Pixels(width, abs(height), 3, bytes(rgb))


def inflated(data: bytes) -> bytes:
    """The file with every compressed stream inflated beside it."""
    out = [data]
    for m in re.finditer(rb"stream\r?\n", data):
        try:
            out.append(zlib.decompressobj().decompress(data[m.end() : m.end() + 50_000_000]))
        except zlib.error:
            continue
    return b"\n".join(out)


def pdfkit_text(path: Path) -> list[str]:
    proc = subprocess.run([str(imagekit.helper())], input=json.dumps({"command": "pdf-text", "input": str(path)}).encode(),
                          capture_output=True, check=True, timeout=300)
    return json.loads(proc.stdout)["pages"]


def vision_readings(path: Path) -> list[str]:
    """Every reading of every pass Apple Vision makes of an image."""
    proc = subprocess.run([str(imagekit.helper())], capture_output=True, check=True, timeout=300,
                          input=json.dumps({"command": "ocr", "input": str(path), "rotations": [90, 270]}).encode())
    answer = json.loads(proc.stdout)
    return [c["text"] for p in answer["passes"] for line in p["lines"] for c in line["candidates"]]


def unmasked_ink(source: Pixels, out: Pixels, box: list[float], dark: int = 60) -> int:
    """Pixels inside the box that held ink on the drawn page and are not mask black in the output."""
    x0, y0 = max(0, math.floor(box[0])), max(0, math.floor(box[1]))
    x1, y1 = min(out.width, math.ceil(box[0] + box[2])), min(out.height, math.ceil(box[1] + box[3]))
    left = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if min(source.at(x, y)[:3]) < 245 and max(out.at(x, y)[:3]) > dark:
                left += 1
    return left


def _shown(box: list[float], rotate: int, size: tuple[float, float]) -> list[float]:
    """A box on the page as stored, on the page as shown (turned by /Rotate)."""
    x, y, w, h = box
    if rotate == 90:
        return [size[1] - y - h, x, h, w]
    return box


def run(f: PdfFixture, workdir: Path, lexicon: Lexicon, images: dict | None = None, release: bool = False) -> tuple[Path, FileResult]:
    src = workdir / "in" / f.file
    profile = workdir / "profile.json"
    if not profile.exists():
        lm.write_profile(profile, f.profile)
    batch = run_batch([src], profile, workdir / "out" if release else None, workdir / "private", lexicon=lexicon,
                      release=release, images=images or {})
    return profile, batch.files[0]


def check_pdf(f: PdfFixture, workdir: Path, lexicon: Lexicon) -> list[str]:
    """Every way this fixture's outcome differs from what the matrix expects."""
    imagekit.helper()
    _, words = build(f, workdir / "in")
    _, first = run(f, workdir, lexicon)
    missing = [d for d in f.dropped if d not in first.dropped]
    if missing:
        return [f"removed parts not listed: {missing}; got {first.dropped}"]
    if f.expect == "blocked":
        if first.status != "blocked":
            return [f"status {first.status}, expected blocked: {first.reasons[:3]}"]
        if f.reason and not any(f.reason in r for r in first.reasons):
            return [f"no reason mentioning {f.reason!r}: {first.reasons}"]
        return []
    if f.expect == "needs_review":
        got = {r.word.casefold() for r in first.review}
        lacking = {w.casefold() for w in f.review} - got
        return [f"review lacks {sorted(lacking)}; got {sorted(got)}"] if lacking or first.status != "needs_review" else []
    if first.status != "needs_review" or first.review or not any(r.startswith("confirm the masks on") for r in first.reasons):
        return [f"before confirmation: {first.status} {first.reasons[:3]} review {[r.word for r in first.review]}"]
    choices = {i["key"]: {"confirmed": i["digest"]} for i in first.images}
    _, final = run(f, workdir, lexicon, choices, release=True)
    if final.status != "ready":
        return [f"after confirmation: {final.status} {final.reasons[:4]}"]
    out_dir = workdir / "out"
    released = sorted(out_dir.glob("document-*"))
    if len(released) != 1 or not re.fullmatch(r"document-\d\d\.pdf", released[0].name):
        return [f"unexpected output files {[p.name for p in released]}"]
    out = released[0]
    data = out.read_bytes()
    problems: list[str] = []
    pictures = page_pictures(data)
    if len(pictures) != len(f.pages):
        return problems + [f"{len(pictures)} pages released, expected {len(f.pages)}"]
    for n, ((suffix, picture), page_words) in enumerate(zip(pictures, words), 1):
        path = workdir / f"released-page-{n}{suffix}"
        path.write_bytes(picture)
        px = pixels(path)
        rotate = f.rotate if n == 1 else 0
        size = (A4[1], A4[0]) if rotate in (90, 270) else (A4[0], A4[1])
        scale = px.width / size[0]

        def in_pixels(box: list[float], scale: float = scale, rotate: int = rotate) -> list[float]:
            return [v * scale for v in _shown(box, rotate, (A4[0], A4[1]))]

        source = pixels(Path(final.images[n - 1]["source"]))  # the page as drawn, before masks
        for word in f.masked:
            for w in page_words:
                if w["text"] == word:
                    left = unmasked_ink(source, px, in_pixels(w["box"]))
                    if left:
                        problems.append(f"page {n}: {word!r}: {left} pixels not masked")
        for region in f.masked_regions:
            if left := unmasked_ink(source, px, in_pixels(list(region))):
                problems.append(f"page {n}: visual identity region has {left} unmasked pixels")
        for word in f.visible:
            for w in page_words:
                if w["text"] == word and mostly_masked(px, in_pixels(w["box"]), 60):
                    problems.append(f"page {n}: {word!r} was masked")
        for reading in vision_readings(path):
            problems += [f"page {n}: Vision reads {s!r} in {reading!r}" for s in lm.SECRETS if s in skeleton(reading)]
    flat = skeleton(inflated(data).decode("latin-1"))
    problems += [f"file bytes hold {s!r}" for s in lm.SECRETS if s in flat]
    extracted = " ".join(" ".join(p.split()) for p in pdfkit_text(out))
    problems += [f"text layer holds {s!r}" for s in lm.SECRETS if s in skeleton(extracted)]
    problems += [f"text layer lacks {t!r}" for t in f.text_has if t not in extracted]
    problems += [f"text layer holds {t!r}" for t in f.text_not if t in extracted]
    summary = sorted(out_dir.glob("summary-*.json"))
    if summary and any(s in skeleton(summary[0].read_text()) for s in lm.SECRETS):
        problems.append("summary names the company")
    return problems
