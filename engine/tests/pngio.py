"""Test-only PNG reading (8-bit, any row filter) for the image oracle.

Kept separate from the engine and the verifier, like the leak oracle.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass


@dataclass
class Pixels:
    width: int
    height: int
    channels: int  # 1 gray, 3 RGB, 4 RGBA
    data: bytes  # unfiltered rows, no filter bytes

    def at(self, x: int, y: int) -> tuple[int, ...]:
        i = (y * self.width + x) * self.channels
        return tuple(self.data[i : i + self.channels])


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if pa <= pb and pa <= pc else b if pb <= pc else c


def read_png(data: bytes) -> Pixels:
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), "not a PNG"
    pos, idat, header = 8, [], None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        pos += 12 + length
    width, height, depth, colour, _, _, interlace = header
    assert depth == 8 and interlace == 0, "only 8-bit, non-interlaced PNGs"
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    out = bytearray()
    prev = bytearray(stride)
    for y in range(height):
        f = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)])
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if f == 1:
                row[i] = (row[i] + a) & 255
            elif f == 2:
                row[i] = (row[i] + b) & 255
            elif f == 3:
                row[i] = (row[i] + (a + b) // 2) & 255
            elif f == 4:
                row[i] = (row[i] + _paeth(a, b, c)) & 255
        out += row
        prev = row
    return Pixels(width, height, channels, bytes(out))


def write_png(px: Pixels, *, row_filter: int = 0, extra: list[tuple[bytes, bytes]] = ()) -> bytes:
    """Encode 8-bit pixels. Only filter types 0 (none) and 1 (sub) are written;
    ``extra`` chunks go before the pixel data."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    colour = {1: 0, 3: 2, 4: 6}[px.channels]
    stride = px.width * px.channels
    rows = bytearray()
    for y in range(px.height):
        row = px.data[y * stride : (y + 1) * stride]
        if row_filter == 1:
            row = bytes((row[i] - (row[i - px.channels] if i >= px.channels else 0)) & 255 for i in range(stride))
        rows += bytes([row_filter]) + row
    header = struct.pack(">IIBBBBB", px.width, px.height, 8, colour, 0, 0, 0)
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    for kind, body in extra:
        out += chunk(kind, body)
    return out + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")


def without_alpha(px: Pixels) -> Pixels:
    if px.channels == 3:
        return px
    rgb = bytearray()
    for i in range(0, len(px.data), 4):
        rgb += px.data[i : i + 3]
    return Pixels(px.width, px.height, 3, bytes(rgb))
