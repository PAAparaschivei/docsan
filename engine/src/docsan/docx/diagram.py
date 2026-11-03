"""Charts and SmartArt as text that states what the original shows (decision D-022).

A chart becomes a table of the values its cache holds, formatted the way the
chart formats them, under a heading that names the chart type. Nothing is
estimated: a value the file does not store stays empty and a note says so,
and what the table cannot show (trend lines, error bars, a second axis, text
placed on the chart) is listed in notes rather than left out silently.

A SmartArt diagram becomes a list of its text in the order and nesting of
its data model, with the kind of layout named (process, cycle, hierarchy) and
numbered when the order is the point of the diagram.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lxml import etree

from .numfmt import format_value, general

# Text as pieces: (text, written by DocSan). DocSan's own words never go to
# the unfamiliar-word review; the document's words always do.
Pieces = list[tuple[str, bool]]


def ours(text: str) -> tuple[str, bool]:
    return (text, True)


def theirs(text: str) -> tuple[str, bool]:
    return (text, False)


def _join(items: list[str], sep: str = ", ") -> Pieces:
    out: Pieces = []
    for i, item in enumerate(items):
        if i:
            out.append(ours(sep))
        out.append(theirs(item))
    return out

C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"

CHART_TYPES = {
    "barChart": "bar", "bar3DChart": "bar", "lineChart": "line", "line3DChart": "line", "pieChart": "pie",
    "pie3DChart": "pie", "ofPieChart": "pie", "doughnutChart": "doughnut", "areaChart": "area", "area3DChart": "area",
    "scatterChart": "scatter", "bubbleChart": "bubble", "radarChart": "radar", "stockChart": "stock",
    "surfaceChart": "surface", "surface3DChart": "surface",
}
UNITS = {
    "hundreds": "hundreds", "thousands": "thousands", "tenThousands": "tens of thousands",
    "hundredThousands": "hundreds of thousands", "millions": "millions", "tenMillions": "tens of millions",
    "hundredMillions": "hundreds of millions", "billions": "billions", "trillions": "trillions",
}
EXTRAS = (("trendline", "a trend line"), ("errBars", "error bars"), ("hiLowLines", "high-low lines"),
          ("upDownBars", "up-down bars"), ("dropLines", "drop lines"))


def _text(el: etree._Element | None) -> str:
    if el is None:
        return ""
    return " ".join(" ".join(t.text or "" for t in el.iter(A + "t", C + "v")).split())


@dataclass
class _Values:
    points: dict[int, str] = field(default_factory=dict)
    count: int = 0
    stored: bool = False
    raw: bool = False  # some values shown as stored: their format is not understood
    numeric: bool = False  # numbers DocSan formatted, such as "Jan 24": its own words, not the document's

    def cell(self, idx: int) -> list[tuple[str, bool]]:
        return [(self.points.get(idx, ""), self.numeric)]


def _values(el: etree._Element | None) -> _Values:
    """The cached values of a series' categories, values or sizes, as shown."""
    out = _Values()
    if el is None:
        return out
    multi = next(el.iter(C + "multiLvlStrCache"), None)
    if multi is not None:
        # The first level is the innermost; outer levels label groups from their first point on.
        levels = []
        for lvl in multi.findall(C + "lvl"):
            levels.append({int(pt.get("idx", "0")): _text(pt.find(C + "v")) for pt in lvl.findall(C + "pt")})
        count_el = multi.find(C + "ptCount")
        out.count = int(count_el.get("val", "0")) if count_el is not None else max((max(lv, default=-1) for lv in levels), default=-1) + 1
        out.stored = True
        for i in range(out.count):
            labels = []
            for lv in reversed(levels):
                at = max((k for k in lv if k <= i), default=None)
                if at is not None and lv[at]:
                    labels.append(lv[at])
            out.points[i] = " / ".join(labels)
        return out
    cache = next((c for c in el.iter(C + "numCache", C + "numLit", C + "strCache", C + "strLit")), None)
    if cache is None:
        return out
    out.stored = True
    out.numeric = cache.tag in (C + "numCache", C + "numLit")
    code_el = cache.find(C + "formatCode")
    code = (code_el.text or "") if code_el is not None else ""
    count_el = cache.find(C + "ptCount")
    for pt in cache.findall(C + "pt"):
        v = pt.find(C + "v")
        raw = (v.text or "") if v is not None else ""
        if cache.tag in (C + "numCache", C + "numLit"):
            shown = format_value(raw, pt.get("formatCode") or code)
            if shown is None:
                out.raw = True
                try:
                    shown = general(float(raw))
                except ValueError:
                    shown = raw
        else:
            shown = raw
        out.points[int(pt.get("idx", "0"))] = shown
    out.count = int(count_el.get("val", "0")) if count_el is not None and (count_el.get("val") or "").isdigit() else 0
    out.count = max(out.count, max(out.points, default=-1) + 1)
    return out


@dataclass
class ChartData:
    heading: Pieces
    axes: Pieces
    tables: list[list[list[Pieces]]]
    notes: list[Pieces]


def read_chart(root: etree._Element, user_shapes: list[etree._Element]) -> ChartData:
    chart = root.find(C + "chart")
    plot = chart.find(C + "plotArea") if chart is not None else None
    plots = [p for p in plot if isinstance(p.tag, str) and p.tag[len(C):] in CHART_TYPES] if plot is not None else []
    kinds = []
    for p in plots:
        kind = CHART_TYPES[p.tag[len(C):]]
        bar_dir = p.find(C + "barDir")
        if kind == "bar" and bar_dir is not None and bar_dir.get("val") == "col":
            kind = "column"
        if kind not in kinds:
            kinds.append(kind)
    series = [(p, s) for p in plots for s in p.findall(C + "ser")]
    title = _text(chart.find(C + "title")) if chart is not None else ""
    deleted = chart.find(C + "autoTitleDeleted") if chart is not None else None
    automatic = chart is not None and (chart.find(C + "title") is not None or deleted is None or deleted.get("val") in ("0", "false"))
    if not title and len(series) == 1 and automatic:
        title = _text(series[0][1].find(C + "tx"))  # Word titles a one-series chart with its name
    what = " and ".join(kinds) + " chart" if kinds else "chart"
    if title:
        heading = [ours("Chart: "), theirs(title), ours(f" ({what}, shown as a table of its values)")]
    else:
        heading = [ours(f"Chart ({what}, shown as a table of its values)")]

    # Axes: titles, a second value axis, display units.
    axes: dict[str, tuple[str, str]] = {}  # axis id -> (kind, title)
    value_axes: list[str] = []
    notes: list[Pieces] = []
    for ax in plot if plot is not None else ():
        tag = ax.tag[len(C):] if isinstance(ax.tag, str) else ""
        if tag in ("catAx", "valAx", "dateAx", "serAx"):
            ax_id = ax.find(C + "axId")
            key = ax_id.get("val", "") if ax_id is not None else ""
            axes[key] = (tag, _text(ax.find(C + "title")))
            if tag == "valAx":
                value_axes.append(key)
                units = ax.find(f"{C}dispUnits/{C}builtInUnit")
                if units is not None and units.get("val") in UNITS:
                    notes.append([ours(f"The table shows full values; the chart's axis shows them in {UNITS[units.get('val')]}.")])
    category_title = next((t for k, t in axes.values() if k in ("catAx", "dateAx") and t), "")
    value_title = next((t for k, t in axes.values() if k == "valAx" and t), "")
    axis_line: Pieces = []
    if category_title or value_title:
        axis_line = [ours("Axes: ")]
        if category_title:
            axis_line += [ours("categories: "), theirs(category_title)]
        if value_title:
            axis_line += [ours("; values: " if category_title else "values: "), theirs(value_title)]

    def name(i: int, s: etree._Element) -> str:
        return _text(s.find(C + "tx")) or f"Series {i + 1}"

    def label(i: int, s: etree._Element) -> tuple[str, bool]:
        own = _text(s.find(C + "tx"))
        return theirs(own) if own else ours(f"Series {i + 1}")

    def on_second_axis(p: etree._Element) -> bool:
        ids = [a.get("val", "") for a in p.findall(C + "axId")]
        return len(value_axes) > 1 and value_axes[1] in ids

    tables: list[list[list[Pieces]]] = []
    missing: list[str] = []
    raw = False
    second: list[str] = []
    category_series = [(i, p, s) for i, (p, s) in enumerate(series) if p.tag not in (C + "scatterChart", C + "bubbleChart")]
    if category_series:
        cats = [_values(s.find(C + "cat")) for _, _, s in category_series]
        vals = [_values(s.find(C + "val")) for _, _, s in category_series]
        count = max([c.count for c in cats] + [v.count for v in vals] + [0])
        header: list[Pieces] = [[theirs(category_title) if category_title else ours("Category")]]
        for (i, p, s), v in zip(category_series, vals):
            header.append([label(i, s), *([ours(f" ({CHART_TYPES[p.tag[len(C):]]})")] if len(kinds) > 1 else [])])
            if not v.stored:
                missing.append(name(i, s))
            raw = raw or v.raw
            if on_second_axis(p):
                second.append(name(i, s))
        rows = [header]
        for idx in range(count):
            source = next((c for c in cats if c.points.get(idx)), None)
            rows.append([source.cell(idx) if source else [ours(str(idx + 1))], *[v.cell(idx) for v in vals]])
        tables.append(rows)
    scatter = [(i, p, s) for i, (p, s) in enumerate(series) if p.tag in (C + "scatterChart", C + "bubbleChart")]
    if scatter:
        bubble = any(p.tag == C + "bubbleChart" for _, p, _ in scatter)
        x_title = next((t for k, t in axes.values() if k == "valAx" and t), "")
        header = [[ours("Series")], [theirs(x_title) if x_title else ours("X")], [ours("Y")], *([[ours("Size")]] if bubble else [])]
        rows = [header]
        for i, p, s in scatter:
            xs, ys, sizes = (_values(s.find(C + t)) for t in ("xVal", "yVal", "bubbleSize"))
            if not ys.stored:
                missing.append(name(i, s))
            raw = raw or xs.raw or ys.raw or sizes.raw
            for idx in range(max(xs.count, ys.count, sizes.count)):
                x = xs.cell(idx) if xs.stored else [ours(str(idx + 1))]
                rows.append([[label(i, s)], x, ys.cell(idx), *([sizes.cell(idx)] if bubble else [])])
        tables.append(rows)

    if missing:
        notes.append([ours("The file does not store the values of "), *_join(missing), ours(", so those cells are empty.")])
    if raw:
        notes.append([ours("Some values are shown as stored, because their number format is not one DocSan applies.")])
    if second:
        notes.append([ours("On the chart, "), *_join(second), ours(f" {'uses' if len(second) == 1 else 'use'} a second value axis.")])
    shown = [text for tag, text in EXTRAS if any(True for _ in root.iter(C + tag))]
    if shown:
        notes.append([ours(f"The chart also shows {', '.join(shown)}, which the table does not.")])
    # Text placed on the chart: custom data labels and shapes drawn over it.
    labels = [_text(lbl.find(f"{C}tx/{C}rich")) for lbl in root.iter(C + "dLbl")]
    labels += [" ".join("".join(t.text or "" for t in p.iter(A + "t")).split()) for sh in user_shapes for p in sh.iter(A + "p")]
    labels = [t for t in labels if t]
    if labels:
        notes.append([ours("Text on the chart: "), *_join(labels, "; ")])
    return ChartData(heading, axis_line, tables, notes)


# --- SmartArt ---------------------------------------------------------------------

CATEGORIES = ("process", "cycle", "hierarchy", "relationship", "matrix", "pyramid", "list", "picture")


@dataclass
class Diagram:
    heading: Pieces
    category: str
    items: list[tuple[int, str]]
    notes: list[Pieces]


def _layout_category(layout: etree._Element | None) -> str:
    if layout is None:
        return ""
    cats = sorted(
        ((int(c.get("pri", "0") or 0) if (c.get("pri") or "").isdigit() else 0, c.get("type", "")) for c in layout.iter(DGM + "cat")),
    )
    for _, t in cats:
        if t in CATEGORIES:
            return t
    unique = (layout.get("uniqueId") or "").rsplit("/", 1)[-1].lower()
    return next((c for c in CATEGORIES if c in unique), "")


def read_smartart(root: etree._Element, layout: etree._Element | None) -> Diagram:
    kinds: dict[str, str] = {}
    text: dict[str, str] = {}
    pictures = 0
    for pt in root.iter(DGM + "pt"):
        pid = pt.get("modelId", "")
        kinds[pid] = pt.get("type", "node")
        body = pt.find(DGM + "t")
        text[pid] = " ".join("".join(t.text or "" for t in body.iter(A + "t")).split()) if body is not None else ""
        if kinds[pid] in ("node", "asst") and any(True for _ in pt.iter(A + "blip")):
            pictures += 1
    children: dict[str, list[tuple[int, str]]] = {}
    for cxn in root.iter(DGM + "cxn"):
        if cxn.get("type", "parOf") == "parOf":
            order = cxn.get("srcOrd", "0")
            children.setdefault(cxn.get("srcId", ""), []).append((int(order) if order.isdigit() else 0, cxn.get("destId", "")))
    items: list[tuple[int, str]] = []
    seen: set[str] = set()

    def walk(pid: str, depth: int) -> None:
        for _, child in sorted(children.get(pid, [])):
            if child in seen or kinds.get(child, "node") not in ("node", "asst"):
                continue
            seen.add(child)
            if text.get(child):
                items.append((depth, text[child]))
            walk(child, depth + 1)

    for root_id in (p for p, k in kinds.items() if k == "doc"):
        walk(root_id, 0)
    notes: list[Pieces] = []
    stray = [text[p] for p, k in kinds.items() if k in ("node", "asst") and p not in seen and text.get(p)]
    if stray:
        notes.append([ours("Other text in the diagram: "), *_join(stray, "; ")])
    connectors = [text[p] for p, k in kinds.items() if k in ("parTrans", "sibTrans") and text.get(p)]
    if connectors:
        notes.append([ours("Labels on the diagram's connectors: "), *_join(connectors, "; ")])
    category = _layout_category(layout)
    if category == "cycle" and items:
        notes.append([ours("In the diagram the steps form a cycle: the last leads back to the first.")])
    if pictures:
        notes.append([ours(f"The diagram's {'picture is' if pictures == 1 else f'{pictures} pictures are'} not kept.")])
    heading = [ours(f"Diagram ({category}, shown as a list)" if category else "Diagram (shown as a list)")]
    return Diagram(heading, category, items, notes)
