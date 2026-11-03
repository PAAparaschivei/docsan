"""Write a flattened PDF (D-008): each page is one picture under an invisible text layer.

The file holds only what DocSan writes, in a fixed order:

1. the catalog, 2. the page tree, 3. one font, 4. the document information,
then for each page its page object, its picture and its content stream.

The font is Helvetica, which every reader has, so nothing is embedded; its
widths are all set to half an em, so the text layer's positions do not
depend on the reader's copy of the font. The document information names the
producer and nothing else. Each content stream draws the page's picture,
with the masks burned in, over the whole page, then writes each line of the
sanitized text at the line's place, in text rendering mode 3, which is
invisible but can be selected, searched and extracted. Characters outside Windows-1252 (the
encoding the text uses) lose their accents or become "?".

A picture is stored whichever way is smaller: as JPEG, which suits photos
and scans, or without loss as the compressed rows of a PNG (grey when the
page has no colour), which suits text: a dense text page is about a third
of its JPEG size that way, and its text stays sharp.

The verifier knows this exact form (``docsan.verify.verify_pdf``) and
refuses anything else.
"""

from __future__ import annotations

import hashlib
import re
import struct
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path

from .plan import OutLine

HEADER = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"
FONT = (
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding /FirstChar 32 /LastChar 255 /Widths ["
    + b" ".join([b"500"] * 224)
    + b"] >>"
)
INFO = b"<< /Producer (DocSan) >>"
GLYPH = 0.5  # every character is half an em wide
# Past this horizontal scaling a character advances more than 0.9 of the font
# size, and text extractors start to split a line into letters.
MAX_STRETCH = 180.0


@dataclass
class OutPage:
    picture: bytes  # the page with its masks burned in: a JPEG, or a PNG's compressed rows
    width: int  # pixels
    height: int
    points: tuple[float, float]  # page size in points
    lines: list[OutLine]  # sanitized text, boxes in pixels
    kind: str = "jpeg"  # "jpeg" or "flate"
    colors: int = 3  # 1 for grey, 3 for RGB


def png_rows(data: bytes) -> tuple[int, int, int, bytes]:
    """Width, height, colours and the compressed rows of an 8-bit grey or RGB PNG,
    which a PDF stores as they are (Flate with PNG predictors)."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    pos, header, rows = 8, None, []
    while pos + 12 <= len(data):
        length, name = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if len(body) != length or zlib.crc32(name + body) != struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]:
            raise ValueError("damaged PNG")
        if name == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif name == b"IDAT":
            rows.append(body)
        elif name == b"IEND":
            break
        pos += 12 + length
    if header is None or not rows:
        raise ValueError("PNG without pixels")
    width, height, depth, colour, method, filtering, interlace = header
    if depth != 8 or colour not in (0, 2) or method or filtering or interlace:
        raise ValueError("the page PNG is not 8-bit grey or RGB")
    return width, height, 1 if colour == 0 else 3, b"".join(rows)


def winansi(text: str) -> bytes:
    """The text in Windows-1252, the encoding of the font's text."""
    out = bytearray()
    for ch in text:
        if ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf"):
            ch = " " if ch.isspace() else ""
        try:
            raw = ch.encode("cp1252")
        except UnicodeEncodeError:
            plain = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            try:
                raw = plain.encode("cp1252") if plain else b"?"
            except UnicodeEncodeError:
                raw = b"?"
        out += raw
    return bytes(out)


def _escape(raw: bytes) -> bytes:
    out = bytearray()
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([b])
        elif 0x20 <= b <= 0x7E:
            out.append(b)
        else:
            out += f"\\{b:03o}".encode()
    return bytes(out)


def _num(v: float) -> str:
    text = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _pieces(page: OutPage) -> list[tuple[bytes, str]]:
    """Each line of text the page writes, with the operators that place it.
    A line is one run of text, which every reader keeps together; it is
    stretched to the line's width, but no character advances more than 0.9
    of the font size, so no reader splits it into letters."""
    pw, ph = page.points
    sx, sy = pw / page.width, ph / page.height
    out: list[tuple[bytes, str]] = []
    for line in page.lines:
        raw = winansi(line.text).strip()
        if not raw:
            continue
        x, y, w, h = line.box[0] * sx, line.box[1] * sy, line.box[2] * sx, line.box[3] * sy
        if line.angle == 0:
            size, length = max(1.0, 0.85 * h), w
            matrix = f"1 0 0 1 {_num(x)} {_num(ph - y - h + 0.2 * size)}"
        elif line.angle == 90:  # reads downwards
            size, length = max(1.0, 0.85 * w), h
            matrix = f"0 -1 1 0 {_num(x + 0.2 * size)} {_num(ph - y)}"
        else:  # reads upwards
            size, length = max(1.0, 0.85 * w), h
            matrix = f"0 1 -1 0 {_num(x + w - 0.2 * size)} {_num(ph - y - h)}"
        scale = min(MAX_STRETCH, max(1.0, 100 * length / (len(raw) * GLYPH * size)))
        out.append((raw, f"BT 3 Tr /F1 {_num(size)} Tf {_num(scale)} Tz {matrix} Tm"))
    return out


def content(page: OutPage) -> bytes:
    """The page's content stream: the picture, then each line of text at its place."""
    pw, ph = page.points
    out = [f"q\n{_num(pw)} 0 0 {_num(ph)} 0 0 cm\n/Im1 Do\nQ\n".encode()]
    out += [place.encode() + b" (" + _escape(raw) + b") Tj ET\n" for raw, place in _pieces(page)]
    return b"".join(out)


def _stream(dictionary: bytes, data: bytes) -> bytes:
    return dictionary[:-3] + b" /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


def pdf_bytes(pages: list[OutPage]) -> bytes:
    if not pages:
        raise ValueError("a PDF needs at least one page")
    objects: list[bytes] = [b""] * (4 + 3 * len(pages))
    kids = " ".join(f"{5 + 3 * k} 0 R" for k in range(len(pages)))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objects[2] = FONT
    objects[3] = INFO
    for k, page in enumerate(pages):
        n = 5 + 3 * k
        pw, ph = page.points
        objects[n - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_num(pw)} {_num(ph)}] "
            f"/Resources << /XObject << /Im1 {n + 1} 0 R >> /Font << /F1 3 0 R >> >> /Contents {n + 2} 0 R >>"
        ).encode()
        space = "/DeviceGray" if page.colors == 1 else "/DeviceRGB"
        if page.kind == "jpeg":
            encoding = "/Filter /DCTDecode"
        else:
            encoding = f"/Filter /FlateDecode /DecodeParms << /Predictor 15 /Colors {page.colors} /BitsPerComponent 8 /Columns {page.width} >>"
        objects[n] = _stream(
            f"<< /Type /XObject /Subtype /Image /Width {page.width} /Height {page.height} /ColorSpace {space} "
            f"/BitsPerComponent 8 {encoding} >>".encode(),
            page.picture,
        )
        objects[n + 1] = _stream(b"<< /Filter /FlateDecode >>", zlib.compress(content(page), 9))
    out = bytearray(HEADER)
    offsets: list[int] = []
    for num, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
    file_id = hashlib.sha256(bytes(out)).hexdigest()[:32].upper()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 4 0 R /ID [<{file_id}><{file_id}>] >>\n".encode()
    out += f"startxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def write_pdf(pages: list[OutPage], path: Path) -> None:
    Path(path).write_bytes(pdf_bytes(pages))


_CONTENT = re.compile(rb"(\d+) 0 obj\n<< /Filter /FlateDecode /Length (\d+) >>\nstream\n")
_SHOW = re.compile(rb"\(((?:[^()\\]|\\[0-7]{3}|\\[()\\])*)\) Tj ET\n")


def text_layer(data: bytes) -> list[list[str]]:
    """Each page's lines, read back from a file this module wrote, to check
    that the text layer holds exactly the text it was given."""
    pages: list[list[str]] = []
    for m in _CONTENT.finditer(data):
        stream = zlib.decompress(data[m.end() : m.end() + int(m.group(2))])
        lines = []
        for s in _SHOW.finditer(stream):
            raw = re.sub(rb"\\([0-7]{3}|[()\\])", lambda e: bytes([int(e.group(1), 8)]) if len(e.group(1)) == 3 else e.group(1), s.group(1))
            lines.append(raw.decode("cp1252"))
        pages.append(lines)
    return pages


def expected_text(page: OutPage) -> list[str]:
    """The lines ``text_layer`` must read back for this page."""
    return [raw.decode("cp1252") for raw, _ in _pieces(page)]
