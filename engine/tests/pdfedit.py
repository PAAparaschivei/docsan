"""Test-only PDF editing: add parts to a PDF by an incremental update.

The fixture PDFs come from Quartz (``tools/make-pdf.swift``), which writes a
classic cross-reference table and plain object dictionaries. An incremental
update appends new and replaced objects, a new cross-reference section and a
trailer that points back to the previous one, the way PDF editors save
changes. That is how annotations, form fields, embedded files, JavaScript and
metadata are planted in a fixture.
"""

from __future__ import annotations

import re
import zlib


def literal(text: str) -> bytes:
    """A PDF literal string; non-ASCII text is written in UTF-16 with a byte order mark."""
    try:
        raw = text.encode("ascii")
    except UnicodeEncodeError:
        raw = b"\xfe\xff" + text.encode("utf-16-be")
    out = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + out + b")"


def stream(dictionary: bytes, data: bytes, compress: bool = True) -> bytes:
    if compress:
        data = zlib.compress(data)
        dictionary = dictionary + b" /Filter /FlateDecode"
    return b"<< " + dictionary + b" /Length " + str(len(data)).encode() + b" >>\nstream\n" + data + b"\nendstream"


class PdfEdit:
    def __init__(self, data: bytes):
        self.data = data
        start = int(re.findall(rb"startxref\s+(\d+)", data)[-1])
        self.offsets: dict[int, int] = {}
        self._read_xref(start)
        trailer = data[data.rfind(b"trailer") :]
        self.root = int(re.search(rb"/Root (\d+) 0 R", trailer).group(1))
        info = re.search(rb"/Info (\d+) 0 R", trailer)
        self.info = int(info.group(1)) if info else None
        self.size = int(re.search(rb"/Size (\d+)", trailer).group(1))
        self.prev = start
        self.changed: dict[int, bytes] = {}

    def _read_xref(self, start: int) -> None:
        """Read a cross-reference section, then the earlier ones its trailer points to."""
        pos = start
        if not self.data.startswith(b"xref", pos):
            raise ValueError("only classic cross-reference tables are supported")
        pos += 4
        while True:
            m = re.compile(rb"\s*(\d+) (\d+)\s*\n").match(self.data, pos)
            if not m:
                break
            first, count = int(m.group(1)), int(m.group(2))
            pos = m.end()
            for k in range(count):
                entry = self.data[pos + 20 * k : pos + 20 * k + 20]
                if entry[17:18] == b"n":
                    self.offsets.setdefault(first + k, int(entry[:10]))
            pos += 20 * count
        trailer = self.data[pos : self.data.find(b">>", pos)]
        prev = re.search(rb"/Prev (\d+)", trailer)
        if prev:
            self._read_xref(int(prev.group(1)))

    def get(self, num: int) -> bytes:
        if num in self.changed:
            return self.changed[num]
        at = self.offsets[num]
        m = re.compile(rb"\d+ \d+ obj\s*").match(self.data, at)
        end = self.data.index(b"endobj", m.end())
        return self.data[m.end() : end].strip()

    def set(self, num: int, body: bytes) -> None:
        self.changed[num] = body

    def add(self, body: bytes) -> int:
        num = self.size
        self.size += 1
        self.changed[num] = body
        return num

    def add_to_dict(self, num: int, entries: bytes) -> None:
        """Append entries before the closing ``>>`` of a plain dictionary object."""
        body = self.get(num)
        if not body.endswith(b">>"):
            raise ValueError(f"object {num} is not a plain dictionary")
        self.set(num, body[:-2].rstrip() + b" " + entries + b" >>")

    def page(self, index: int) -> int:
        """The object number of a page, counted from 0 (a flat page tree, as Quartz writes)."""
        pages = int(re.search(rb"/Pages (\d+) 0 R", self.get(self.root)).group(1))
        kids = re.search(rb"/Kids \[([^\]]*)\]", self.get(pages)).group(1)
        return [int(n) for n in re.findall(rb"(\d+) 0 R", kids)][index]

    def save(self) -> bytes:
        out = bytearray(self.data)
        if not out.endswith(b"\n"):
            out += b"\n"
        offsets: dict[int, int] = {}
        for num, body in sorted(self.changed.items()):
            offsets[num] = len(out)
            out += f"{num} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n"
        for num in sorted(offsets):
            out += f"{num} 1\n{offsets[num]:010d} 00000 n \n".encode()
        info = f" /Info {self.info} 0 R" if self.info is not None else ""
        out += f"trailer\n<< /Size {self.size} /Root {self.root} 0 R{info} /Prev {self.prev} >>\n".encode()
        out += f"startxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)
