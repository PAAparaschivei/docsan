"""The allowed content model. Anything a reader cannot express here is dropped or blocks.

Formatting is held as ``Fmt``: the kept properties of one element, resolved
from styles and checked against the allowlist in ``docsan.docx.format``, in
the order the writer emits them. Each entry is a property name (with a
container, such as ``pBdr/top``) and its attributes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

Fmt = tuple[tuple[str, tuple[tuple[str, str], ...]], ...]

# Live fields kept in the output, so that page numbers stay right on every page.
LIVE_FIELDS = ("PAGE", "NUMPAGES", "SECTIONPAGES")


@dataclass
class Run:
    text: str
    fmt: Fmt = ()
    field: str = ""  # a live field from LIVE_FIELDS, with its last result as text
    ours: bool = False  # words DocSan wrote itself, such as "shown as a table"; never reviewed

    def same_format(self, other: Run) -> bool:
        return (self.fmt, self.field, self.ours) == (other.fmt, other.field, other.ours)


@dataclass
class Section:
    """Page setup that ends at a paragraph, and the headers and footers it shows."""

    fmt: Fmt = ()
    headers: dict[str, int] = field(default_factory=dict)  # default, first or even -> index in Document.parts
    footers: dict[str, int] = field(default_factory=dict)


@dataclass
class Anchor:
    """Where a floating picture or text box sits, and how text wraps around it."""

    attrs: tuple[tuple[str, str], ...]  # distances, z-order, behind or in front of text
    horizontal: tuple[str, str, str]  # relative to, "align" or "posOffset", value
    vertical: tuple[str, str, str]
    wrap: tuple[str, tuple[tuple[str, str], ...]]  # wrapNone, wrapSquare or wrapTopAndBottom, and its attributes
    effect: tuple[int, int, int, int] | None = None


@dataclass
class TextBox:
    """A text box shape, kept with its position, size, fill and outline."""

    blocks: list[Block]
    width_emu: int
    height_emu: int
    anchor: Anchor | None = None  # None: in line with text
    shape: str = "rect"
    fill: str | None = None  # RGB, None for no fill
    line: str | None = None
    line_width: int = 9525
    body: tuple[tuple[str, str], ...] = ()  # insets, vertical anchoring
    autofit: bool = False


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)
    kind: str = "body"  # body, heading, title or list
    level: int = 0  # heading level 1-6, or list level 0-8
    numbered: bool = False
    fmt: Fmt = ()  # paragraph properties
    mark: Fmt = ()  # run properties of the paragraph mark
    num: tuple[int, int] | None = None  # written numbering instance and level
    style: int = 0  # neutral style number, 0 for Normal
    section: Section | None = None  # a section ends with this paragraph
    drawings: list[Figure | TextBox] = field(default_factory=list)  # floating pictures and text boxes held here

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


@dataclass
class Figure:
    """An image from the document, masked before it is written again."""

    data: bytes  # the original image part
    kind: str  # "jpeg", or "png" for every other raster format (converted)
    width_emu: int
    height_emu: int
    crop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # left, top, right, bottom fractions
    index: int = 0  # 1-based, in document order
    output: bytes | None = None  # the masked, rebuilt image once confirmed
    fmt: Fmt = ()  # properties of the paragraph that held it, such as its alignment
    anchor: Anchor | None = None  # floating: written inside its paragraph, where it was
    run_fmt: Fmt = ()  # formatting of the run that held it, for a label that replaces it


@dataclass
class Cell:
    blocks: list[Block] = field(default_factory=list)
    fmt: Fmt = ()


@dataclass
class Table:
    rows: list[list[Cell]] = field(default_factory=list)
    fmt: Fmt = ()
    grid: list[int] = field(default_factory=list)  # column widths in twentieths of a point
    row_fmts: list[Fmt] = field(default_factory=list)


Block = Paragraph | Table | Figure


@dataclass
class HeaderFooter:
    kind: str  # header or footer
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Level:
    ilvl: int
    start: int
    format: str
    text: str  # the number's text, such as "%1." or a bullet
    justify: str
    restart: int | None
    legal: bool
    suffix: str
    paragraph: Fmt
    run: Fmt


@dataclass
class ListDefinition:
    levels: list[Level]
    multi: str


@dataclass
class ListInstance:
    definition: int  # index into Numbering.definitions
    overrides: list[tuple[int, int | None, Level | None]]  # level, restart value, replacement level


@dataclass
class Numbering:
    definitions: list[ListDefinition] = field(default_factory=list)
    instances: list[ListInstance] = field(default_factory=list)  # written as numbering instances 3, 4, ...


@dataclass
class Settings:
    tab_stop: int = 720
    even_and_odd_headers: bool = False
    hyphenation: bool = False
    mirror_margins: bool = False
    compatibility: int = 15


@dataclass
class Document:
    blocks: list[Block] = field(default_factory=list)
    section: Section = field(default_factory=Section)  # the last section
    parts: list[HeaderFooter] = field(default_factory=list)
    numbering: Numbering = field(default_factory=Numbering)
    styles: int = 0  # neutral paragraph styles Style1..StyleN
    settings: Settings = field(default_factory=Settings)

    def all_blocks(self) -> Iterator[list[Block]]:
        """The body, then each header and footer."""
        yield self.blocks
        for part in self.parts:
            yield part.blocks

    def paragraphs(self) -> Iterator[Paragraph]:
        for blocks in self.all_blocks():
            yield from paragraphs(blocks)

    def figures(self) -> Iterator[Figure]:
        for blocks in self.all_blocks():
            yield from figures(blocks)

    def replace_figure(self, target: Figure, label: str) -> None:
        """Put a label where a picture was, for a picture DocSan cannot read;
        with an empty label, remove the picture and leave nothing (a logo)."""
        runs = [Run(label, target.run_fmt)] if label else []

        def walk(blocks: list[Block]) -> bool:
            for i, b in enumerate(blocks):
                if b is target:
                    blocks[i] = Paragraph(list(runs), fmt=target.fmt)
                    return True
                if isinstance(b, Paragraph):
                    for j, d in enumerate(b.drawings):
                        if d is target:
                            del b.drawings[j]
                            b.runs[0:0] = runs
                            return True
                        if isinstance(d, TextBox) and walk(d.blocks):
                            return True
                elif isinstance(b, Table) and any(walk(cell.blocks) for row in b.rows for cell in row):
                    return True
            return False

        for blocks in self.all_blocks():
            if walk(blocks):
                return


def paragraphs(blocks: list[Block]) -> Iterator[Paragraph]:
    """Every paragraph in document order, including those inside tables and
    text boxes (after the paragraph that holds the text box)."""
    for b in blocks:
        if isinstance(b, Paragraph):
            yield b
            for d in b.drawings:
                if isinstance(d, TextBox):
                    yield from paragraphs(d.blocks)
        elif isinstance(b, Table):
            for row in b.rows:
                for cell in row:
                    yield from paragraphs(cell.blocks)


def figures(blocks: list[Block]) -> Iterator[Figure]:
    """Every figure in document order, including those inside tables and text boxes."""
    for b in blocks:
        if isinstance(b, Figure):
            yield b
        elif isinstance(b, Paragraph):
            for d in b.drawings:
                if isinstance(d, Figure):
                    yield d
                else:
                    yield from figures(d.blocks)
        elif isinstance(b, Table):
            for row in b.rows:
                for cell in row:
                    yield from figures(cell.blocks)


# Page and column breaks and positional tabs, as the preview shows them.
_SHOWN = str.maketrans({"\f": "\n", "\x1d": "\n", "\x1c": "\t", "\x1e": "\t", "\x1f": "\t"})


def _lines(blocks: list[Block], out: list[dict[str, object]], limit: int, kind: str | None = None) -> None:
    for b in blocks:
        if len(out) >= limit:
            return
        if isinstance(b, Paragraph):
            out.append({"kind": kind or b.kind, "level": b.level, "text": b.text.translate(_SHOWN)})
            for d in b.drawings:
                if isinstance(d, Figure):
                    out.append({"kind": "image", "level": d.index, "text": ""})
                else:
                    _lines(d.blocks, out, limit, kind)
        elif isinstance(b, Figure):
            out.append({"kind": "image", "level": b.index, "text": ""})
        else:
            for row in b.rows:
                cells = [" ".join(p.text.translate(_SHOWN) for p in paragraphs(c.blocks)).strip() for c in row]
                out.append({"kind": "row", "level": 0, "text": " | ".join(cells)})


def preview_lines(doc: Document, limit: int = 100_000) -> list[dict[str, object]]:
    """Paragraphs for the on-screen preview and search; each table row becomes
    one line, and headers and footers follow the body."""
    out: list[dict[str, object]] = []
    _lines(doc.blocks, out, limit)
    for part in doc.parts:
        _lines(part.blocks, out, limit, part.kind)
    return out[:limit]
