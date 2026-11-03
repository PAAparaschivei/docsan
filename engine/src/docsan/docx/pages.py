"""The pages of a Word file, described for the app to draw.

The app shows these while the owner reviews: the original and the sanitized
file, both described the same way from the model the writer uses, so what
the owner sees is what the output holds. Formatting comes from the same
allowlisted properties (decision D-023), turned into plain values: points,
colours, alignments. List numbers are worked out here as Word counts them.

The app lays the description out with the macOS text system, which splits
it into pages of each section's size. It runs inside the sandbox with no
network access; a web view cannot, because WebKit needs network access there.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .model import (
    Anchor,
    Block,
    Document,
    Figure,
    Fmt,
    Level,
    Paragraph,
    Run,
    Section,
    Table,
    TextBox,
)

EMU_PER_PT = 12700

HIGHLIGHTS = {
    "black": "000000", "blue": "0000FF", "cyan": "00FFFF", "green": "00FF00", "magenta": "FF00FF", "red": "FF0000",
    "yellow": "FFFF00", "white": "FFFFFF", "darkBlue": "000080", "darkCyan": "008080", "darkGreen": "008000",
    "darkMagenta": "800080", "darkRed": "800000", "darkYellow": "808000", "darkGray": "808080", "lightGray": "C0C0C0",
}
# Symbol and Wingdings bullets are private-use characters; these are their look-alikes.
BULLETS = {"": "•", "": "▪", "": "➢", "": "❖", "": "✓", "": "◆", "": "■",
           "": "❑", "": "➔", "": "▶", "": "–", "o": "◦"}
SYMBOL_FONTS = {"symbol", "wingdings", "wingdings 2", "wingdings 3", "webdings"}


def _props(fmt: Fmt) -> dict[str, dict[str, str]]:
    return {k: dict(v) for k, v in fmt}


def _num(value: str | None, default: float = 0) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _color(value: str | None) -> str | None:
    return value if value and re.fullmatch(r"[0-9A-F]{6}", value) else None


def _border(attrs: dict[str, str] | None) -> dict | None:
    if not attrs or attrs.get("val") in (None, "nil", "none"):
        return None
    style = attrs["val"]
    width = max(_num(attrs.get("sz"), 4) / 8, 0.5)
    kind = "double" if style in ("double", "triple") else "dotted" if "dot" in style.lower() else "dashed" if "dash" in style.lower() else "solid"
    return {"w": round(max(width, 1.5) if kind == "double" else width, 2), "color": _color(attrs.get("color")) or "000000",
            "style": kind, "space": _num(attrs.get("space"))}


def run_style(fmt: Fmt) -> dict:
    """A run's formatting as plain values; absent keys keep the defaults."""
    p = _props(fmt)
    out: dict = {}
    fonts = p.get("rFonts", {})
    if fonts.get("ascii") or fonts.get("hAnsi"):
        out["font"] = fonts.get("ascii") or fonts.get("hAnsi")
    if "sz" in p:
        out["size"] = _num(p["sz"].get("val"), 20) / 2
    for key, prop in (("b", "b"), ("i", "i"), ("strike", "strike"), ("strike", "dstrike"), ("caps", "caps"), ("small", "smallCaps")):
        if prop in p:
            out[key] = True
    if "u" in p:
        out["u"] = p["u"].get("val", "single")
    if color := _color(p.get("color", {}).get("val")):
        out["color"] = color
    if (light := p.get("highlight", {}).get("val")) in HIGHLIGHTS:
        out["bg"] = HIGHLIGHTS[light]
    elif fill := _color(p.get("shd", {}).get("fill")):
        out["bg"] = fill
    align = p.get("vertAlign", {}).get("val")
    if align in ("superscript", "subscript"):
        out["va"] = "super" if align == "superscript" else "sub"
    if "spacing" in p:
        out["spacing"] = _num(p["spacing"].get("val")) / 20
    if "position" in p:
        out["rise"] = _num(p["position"].get("val")) / 2
    return out


def para_style(fmt: Fmt, *, top: bool = True, bottom: bool = True) -> dict:
    p = _props(fmt)
    spacing = p.get("spacing", {})
    out: dict = {
        "before": (14.0 if spacing.get("beforeAutospacing") == "1" else _num(spacing.get("before")) / 20) if top else 0,
        "after": (14.0 if spacing.get("afterAutospacing") == "1" else _num(spacing.get("after")) / 20) if bottom else 0,
    }
    if "line" in spacing:
        rule = spacing.get("lineRule", "auto")
        value = _num(spacing["line"])
        out["line"] = {"rule": rule, "value": value / 240 if rule == "auto" else value / 20}
    ind = p.get("ind", {})
    out["left"] = _num(ind.get("left")) / 20
    out["right"] = _num(ind.get("right")) / 20
    out["first"] = _num(ind.get("firstLine")) / 20 - _num(ind.get("hanging")) / 20
    jc = p.get("jc", {}).get("val")
    out["align"] = {"both": "justify", "distribute": "justify", "center": "center", "right": "right"}.get(jc or "", "left")
    if fill := _color(p.get("shd", {}).get("fill")):
        out["fill"] = fill
    borders = {s: b for s in ("top", "left", "bottom", "right") if (b := _border(p.get(f"pBdr/{s}")))}
    if borders:
        out["borders"] = borders
    tabs = []
    for k, attrs in p.items():
        if k.startswith("tabs/") and attrs.get("val") not in ("clear", "bar", "num"):
            tabs.append({"pos": _num(attrs.get("pos")) / 20, "align": {"end": "right", "start": "left"}.get(attrs.get("val", ""), attrs.get("val", "left")),
                         "leader": attrs.get("leader", "none")})
    if tabs:
        out["tabs"] = sorted(tabs, key=lambda t: t["pos"])
    if "pageBreakBefore" in p:
        out["page_break"] = True
    return out


# --- List numbers -----------------------------------------------------------------


def _roman(n: int) -> str:
    out = ""
    for value, letters in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                           (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += letters
            n -= value
    return out


def _letters(n: int) -> str:
    return chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1) if n > 0 else ""


def number_text(n: int, fmt: str) -> str:
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n).upper()
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n).upper()
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "ordinal":
        return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
    if fmt == "none":
        return ""
    return str(n)


def _fixed_level(i: int, fmt: str) -> Level:
    text = "•" if fmt == "bullet" else f"%{i + 1}."
    return Level(i, 1, fmt, text, "left", None, False, "tab", (("ind", (("hanging", "360"), ("left", str(720 * (i + 1))))),), ())


_FIXED = {1: [_fixed_level(i, "bullet") for i in range(9)], 2: [_fixed_level(i, "decimal") for i in range(9)]}


@dataclass
class _Counters:
    """Word's list counters: one set per list definition, and a separate one
    for an instance that restarts its numbering."""

    doc: Document
    values: dict[object, list[int | None]] = field(default_factory=dict)
    started: set[tuple[int, int]] = field(default_factory=set)

    def levels(self, num_id: int) -> tuple[object, dict[int, Level], dict[int, int]]:
        if num_id in _FIXED:
            return ("fixed", num_id), {lv.ilvl: lv for lv in _FIXED[num_id]}, {}
        index = num_id - 3
        if not 0 <= index < len(self.doc.numbering.instances):
            return None, {}, {}
        inst = self.doc.numbering.instances[index]
        definition = self.doc.numbering.definitions[inst.definition]
        levels = {lv.ilvl: lv for lv in definition.levels}
        starts: dict[int, int] = {}
        for ilvl, start, lvl in inst.overrides:
            if lvl is not None:
                levels[ilvl] = lvl
            if start is not None:
                starts[ilvl] = start
        key = ("instance", num_id) if starts else ("definition", inst.definition)
        return key, levels, starts

    def label(self, num_id: int, ilvl: int) -> tuple[str, Level | None]:
        key, levels, starts = self.levels(num_id)
        lvl = levels.get(ilvl)
        if key is None or lvl is None:
            return "", None
        values = self.values.setdefault(key, [None] * 9)
        if (num_id, ilvl) not in self.started and ilvl in starts:
            values[ilvl] = starts[ilvl]
            self.started.add((num_id, ilvl))
        elif values[ilvl] is None:
            values[ilvl] = lvl.start
        else:
            values[ilvl] += 1
        for deeper in range(ilvl + 1, 9):
            restart = levels[deeper].restart if deeper in levels else None
            if restart is None or restart > ilvl:
                values[deeper] = None
        if lvl.format == "bullet":
            return BULLETS.get(lvl.text, lvl.text), lvl

        def level_text(m: re.Match[str]) -> str:
            k = int(m.group(1)) - 1
            if not 0 <= k < 9:
                return ""
            value = values[k] if values[k] is not None else (levels[k].start if k in levels else 1)
            fmt = "decimal" if lvl.legal else (levels[k].format if k in levels else "decimal")
            return number_text(value, fmt)

        return re.sub(r"%(\d)", level_text, lvl.text), lvl


# --- Blocks ----------------------------------------------------------------------------


def _anchor(anchor: Anchor) -> dict:
    (hf, hk, hv), (vf, vk, vv) = anchor.horizontal, anchor.vertical
    return {"hf": hf, "hk": hk, "hv": hv, "vf": vf, "vk": vk, "vv": vv, "behind": dict(anchor.attrs).get("behindDoc") == "1"}


class _Pages:
    def __init__(self, doc: Document, images: dict[int, str]):
        self.doc = doc
        self.images = images  # id(figure) -> file name next to the description
        self.counters = _Counters(doc)

    def runs(self, runs: list[Run]) -> list[dict]:
        out: list[dict] = []
        for r in runs:
            item = {"text": r.text, **run_style(r.fmt)}
            if r.field == "PAGE" or r.field.startswith("PAGE "):
                item["field"] = "PAGE"
            elif r.field.startswith(("NUMPAGES", "SECTIONPAGES")):
                item["field"] = "NUMPAGES"
            out.append(item)
        return out

    def picture(self, f: Figure) -> dict:
        width, height = max(f.width_emu, 0) / EMU_PER_PT or 216, max(f.height_emu, 0) / EMU_PER_PT or 144
        item: dict = {"kind": "image", "file": self.images.get(id(f)), "w": round(width, 2), "h": round(height, 2)}
        if f.anchor is not None:
            item["anchor"] = _anchor(f.anchor)
        return item

    def text_box(self, box: TextBox) -> dict:
        body = dict(box.body)
        pad = [_num(body.get(k), d) / EMU_PER_PT for k, d in (("tIns", 45720), ("rIns", 91440), ("bIns", 45720), ("lIns", 91440))]
        item: dict = {
            "kind": "box", "w": round(box.width_emu / EMU_PER_PT, 2), "h": round(box.height_emu / EMU_PER_PT, 2),
            "fill": box.fill, "line": box.line, "line_w": round(box.line_width / EMU_PER_PT, 2), "shape": box.shape,
            "pad": [round(x, 2) for x in pad], "valign": {"ctr": "center", "b": "bottom"}.get(body.get("anchor", "t"), "top"),
            "wrap": body.get("wrap", "square") != "none",
            "blocks": self.blocks(box.blocks),
        }
        if box.anchor is not None:
            item["anchor"] = _anchor(box.anchor)
        return item

    def paragraph(self, p: Paragraph, top: bool = True, bottom: bool = True) -> dict:
        item: dict = {"t": "p", **para_style(p.fmt, top=top, bottom=bottom), "mark": run_style(p.mark), "runs": self.runs(p.runs)}
        num = p.num if p.num is not None else ((2 if p.numbered else 1, p.level) if p.kind == "list" else None)
        if num is not None:
            text, lvl = self.counters.label(*num)
            if lvl is not None:
                if p.num is None:  # DocSan's own lists: their indentation comes with the list
                    ind = dict(dict(lvl.paragraph).get("ind", ()))
                    item["left"] = _num(ind.get("left")) / 20
                    item["first"] = -_num(ind.get("hanging")) / 20
                label = {**item["mark"], **run_style(lvl.run)}
                if lvl.format == "bullet" and label.get("font", "").casefold() in SYMBOL_FONTS:
                    label.pop("font")  # its look-alike is an ordinary character
                item["label"] = {"text": text, "suffix": lvl.suffix, **label}
        drawings = [self.picture(d) if isinstance(d, Figure) else self.text_box(d) for d in p.drawings]
        if drawings:
            item["drawings"] = drawings
        return item

    def table(self, t: Table) -> dict:
        tp = _props(t.fmt)
        width = tp.get("tblW", {})
        cell_margin = {s: _num(tp.get(f"tblCellMar/{s}", {}).get("w"), 108 if s in ("left", "right") else 0) / 20
                       for s in ("top", "left", "bottom", "right")}
        layout: list[list[tuple[int, int, object]]] = []
        for i, row in enumerate(t.rows):
            rp = _props(t.row_fmts[i]) if i < len(t.row_fmts) else {}
            col = int(_num(rp.get("gridBefore", {}).get("val")))
            cells = []
            for cell in row:
                span = max(int(_num(_props(cell.fmt).get("gridSpan", {}).get("val"), 1)), 1)
                cells.append((col, span, cell))
                col += span
            layout.append(cells)
        columns = max([len(t.grid), *[c[-1][0] + c[-1][1] for c in layout if c], 1])
        rows: list[dict] = []
        n = len(layout)
        for i, cells in enumerate(layout):
            rp = _props(t.row_fmts[i]) if i < len(t.row_fmts) else {}
            out_cells = []
            for col, span, cell in cells:
                cp = _props(cell.fmt)
                merge = cp.get("vMerge", {}).get("val")
                if merge == "continue":
                    continue
                rowspan = 1
                if merge == "restart":
                    for below in layout[i + 1:]:
                        match = next((c for c in below if c[0] == col), None)
                        if match is None or _props(match[2].fmt).get("vMerge", {}).get("val") != "continue":
                            break
                        rowspan += 1
                sides = {
                    "top": cp.get("tcBorders/top") or tp.get("tblBorders/top" if i == 0 else "tblBorders/insideH"),
                    "bottom": cp.get("tcBorders/bottom") or tp.get("tblBorders/bottom" if i + rowspan >= n else "tblBorders/insideH"),
                    "left": cp.get("tcBorders/left") or tp.get("tblBorders/left" if col == 0 else "tblBorders/insideV"),
                    "right": cp.get("tcBorders/right") or tp.get("tblBorders/right" if col + span >= columns else "tblBorders/insideV"),
                }
                out_cells.append({
                    "row": i, "col": col, "span": span, "rowspan": rowspan,
                    "fill": _color(cp.get("shd", {}).get("fill")) or _color(tp.get("shd", {}).get("fill")),
                    "borders": {s: _border(a) for s, a in sides.items()},
                    "pad": [round(_num(cp.get(f"tcMar/{s}", {}).get("w"), cell_margin[s] * 20) / 20, 2) for s in ("top", "right", "bottom", "left")],
                    "valign": {"center": "center", "bottom": "bottom"}.get(cp.get("vAlign", {}).get("val", ""), "top"),
                    "width": _num(cp["tcW"].get("w")) / 20 if cp.get("tcW", {}).get("type") == "dxa" else None,
                    "blocks": self.blocks(cell.blocks),
                })
            rows.append({"cells": out_cells, "header": "tblHeader" in rp,
                         "height": _num(rp["trHeight"].get("val")) / 20 if "trHeight" in rp else None})
        return {
            "t": "table", "rows": rows, "count": columns,
            "columns": [w / 20 for w in t.grid] if t.grid and all(t.grid) and len(t.grid) == columns else [],
            "width": ({"pt": _num(width.get("w")) / 20} if width.get("type") == "dxa" and _num(width.get("w")) > 0
                      else {"pct": _num(width.get("w")) / 50} if width.get("type") == "pct" else None),
            "align": tp.get("jc", {}).get("val", "left"), "indent": _num(tp.get("tblInd", {}).get("w")) / 20,
        }

    def blocks(self, blocks: list[Block]) -> list[dict]:
        out: list[dict] = []
        for i, b in enumerate(blocks):
            if isinstance(b, Paragraph):
                prev = blocks[i - 1] if i else None
                nxt = blocks[i + 1] if i + 1 < len(blocks) else None
                contextual = "contextualSpacing" in _props(b.fmt)
                top = not (contextual and isinstance(prev, Paragraph) and prev.style == b.style and prev.kind == b.kind)
                bottom = not (contextual and isinstance(nxt, Paragraph) and nxt.style == b.style and nxt.kind == b.kind)
                out.append(self.paragraph(b, top, bottom))
            elif isinstance(b, Figure):
                out.append({"t": "p", **para_style(b.fmt), "mark": {}, "runs": [], "drawings": [self.picture(b)]})
            else:
                out.append(self.table(b))
        return out


def _page(sec: Section) -> dict:
    p = _props(sec.fmt)
    size, margin = p.get("pgSz", {}), p.get("pgMar", {})
    if "pgSz" not in p:
        margin = {"top": "1440", "right": "1440", "bottom": "1440", "left": "1440", "header": "708", "footer": "708"}

    def get(key: str, default: float) -> float:
        return abs(_num(margin.get(key), default)) / 20

    return {"w": _num(size.get("w"), 11906) / 20, "h": _num(size.get("h"), 16838) / 20, "mt": get("top", 1440),
            "mb": get("bottom", 1440), "ml": get("left", 1440), "mr": get("right", 1440), "hd": get("header", 708),
            "ft": get("footer", 708)}


def page_model(doc: Document, images: dict[int, str]) -> dict:
    """The document as sections, each with its page size, headers, footers and blocks."""
    pages = _Pages(doc, images)
    sections: list[tuple[Section, list[Block]]] = []
    current: list[Block] = []
    for b in doc.blocks:
        current.append(b)
        if isinstance(b, Paragraph) and b.section is not None:
            sections.append((b.section, current))
            current = []
    sections.append((doc.section, current))
    # A section without its own header or footer of a kind uses the previous section's.
    inherited: dict[tuple[str, str], int] = {}
    out = []
    for sec, blocks in sections:
        parts: dict[str, dict[str, list[dict]]] = {"headers": {}, "footers": {}}
        for kind, which in (("headers", sec.headers), ("footers", sec.footers)):
            for t in ("default", "first", "even"):
                if t in which:
                    inherited[(kind, t)] = which[t]
                index = inherited.get((kind, t))
                if index is not None and 0 <= index < len(doc.parts):
                    parts[kind][t] = pages.blocks(doc.parts[index].blocks)
        out.append({"page": _page(sec), "title_page": any(k == "titlePg" for k, _ in sec.fmt), **parts, "blocks": pages.blocks(blocks)})
    return {"sections": out, "even_odd": doc.settings.even_and_odd_headers}


def write_pages(doc: Document, folder: Path, images: dict[int, str]) -> Path:
    """Write the description to ``folder/pages.json``; the pictures it names are already in the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pages.json"
    path.write_text(json.dumps(page_model(doc, images), ensure_ascii=False), encoding="utf-8")
    return path
