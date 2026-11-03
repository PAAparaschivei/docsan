"""Write a masked image as a fresh file that holds pixels and nothing else.

PNG output is written here from the helper's raw rows: one header, the pixel
data with no row filters, and the end marker. JPEG output comes from the
helper's encoder and keeps only the segments a decoder needs; every
application segment (EXIF, XMP, IPTC, ICC profiles, thumbnails) and comment
is dropped.
"""

from __future__ import annotations

import struct
import zlib

from .. import steps

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_KEEP = frozenset({0xC0, 0xC1, 0xC2, 0xC4, 0xDB, 0xDD})  # frames, tables, restart interval


def sniff(data: bytes) -> str | None:
    """The image type by content, not by file name."""
    if data.startswith(PNG_SIGNATURE):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def incomplete(data: bytes, kind: str) -> str | None:
    """Why a file is cut short, or None. ImageIO decodes a truncated file and
    fills in the rest, so completeness is checked before it reads one."""
    if kind == "png":
        pos = len(PNG_SIGNATURE)
        while pos + 12 <= len(data):
            length, name = struct.unpack(">I4s", data[pos : pos + 8])
            end = pos + 12 + length
            if end > len(data):
                break
            if zlib.crc32(data[pos + 4 : end - 4]) != struct.unpack(">I", data[end - 4 : end])[0]:
                return f"PNG chunk {name!r} is damaged"
            if name == b"IEND":
                return None
            pos = end
        return "the PNG file is cut short"
    return None if data.rfind(b"\xff\xd9") > 2 else "the JPEG file is cut short"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def write_png(raw: bytes, width: int, height: int, alpha: bool) -> bytes:
    channels = 4 if alpha else 3
    stride = width * channels
    if width <= 0 or height <= 0 or len(raw) != stride * height:
        raise ValueError("raw pixel data does not match the image size")
    rows = b"".join(b"\x00" + raw[y * stride : (y + 1) * stride] for y in range(height))
    stream = zlib.compress(rows, 6)
    step = 1 << 20
    idat = b"".join(_chunk(b"IDAT", stream[i : i + step]) for i in range(0, len(stream), step))
    header = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return PNG_SIGNATURE + _chunk(b"IHDR", header) + idat + _chunk(b"IEND", b"")


def clean_jpeg(data: bytes) -> bytes:
    if not steps.enabled("rebuild-jpeg"):
        return data
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("not a JPEG file")
    out = [b"\xff\xd8"]
    i, n = 2, len(data)
    while True:
        if i + 2 > n or data[i] != 0xFF:
            raise ValueError("malformed JPEG")
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xD9:
            out.append(b"\xff\xd9")
            return b"".join(out)  # anything after the end marker is dropped
        if i + 4 > n:
            raise ValueError("truncated JPEG")
        end = i + 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
        if end > n:
            raise ValueError("truncated JPEG")
        if marker == 0xDA:  # a scan: header, then entropy-coded data up to the next marker
            j = end
            while True:
                j = data.find(b"\xff", j)
                if j < 0 or j + 1 >= n:
                    raise ValueError("truncated JPEG scan")
                if data[j + 1] == 0x00 or 0xD0 <= data[j + 1] <= 0xD7:
                    j += 2
                    continue
                break
            out.append(data[i:j])
            i = j
            continue
        if marker in _JPEG_KEEP:
            out.append(data[i:end])
        elif not (0xE0 <= marker <= 0xEF or marker == 0xFE):
            raise ValueError(f"unexpected JPEG segment {marker:#04x}")
        i = end
