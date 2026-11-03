"""Visible formatting, resolved and kept through an allowlist (decision D-023).

Word shows a paragraph or run with formatting from several layers: document
defaults, the table style, numbering, the paragraph style, the character style
and direct formatting. The reader resolves those layers here into one set of
properties for each paragraph, run, table, row and cell, and keeps only the
properties listed below, each value checked against its kind. The writer
emits them as direct formatting under neutral styles, so no style name, theme
or other part of the original is carried over.

A corporate font's name can name the company, so a font outside the list in
``data/fonts.txt`` becomes Calibri, Cambria or Consolas, by the kind of font
the document's font table records.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from lxml import etree

from .. import steps
from .model import Fmt, Level, ListDefinition, ListInstance, Numbering

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

Props = dict[str, dict[str, str]]

# Kinds of attribute values. A frozenset lists the allowed words; a dict maps
# each allowed word to the word written.
ON, INT, UINT, COLOR, FONT, WIDTH = "on", "int", "uint", "color", "font", "width"


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


BORDER_STYLES = _words(
    "nil none single thick double dotted dashed dotDash dotDotDash triple thinThickSmallGap thickThinSmallGap "
    "thinThickThinSmallGap thinThickMediumGap thickThinMediumGap thinThickThinMediumGap thinThickLargeGap "
    "thickThinLargeGap thinThickThinLargeGap wave doubleWave dashSmallGap dashDotStroked threeDEmboss "
    "threeDEngrave outset inset"
)
SHADING_PATTERNS = _words(
    "nil clear solid horzStripe vertStripe reverseDiagStripe diagStripe horzCross diagCross thinHorzStripe "
    "thinVertStripe thinReverseDiagStripe thinDiagStripe thinHorzCross thinDiagCross pct5 pct10 pct12 pct15 "
    "pct20 pct25 pct30 pct35 pct37 pct40 pct45 pct50 pct55 pct60 pct62 pct65 pct70 pct75 pct80 pct85 pct87 "
    "pct90 pct95"
)
UNDERLINES = _words(
    "single words double thick dotted dottedHeavy dash dashedHeavy dashLong dashLongHeavy dotDash dashDotHeavy "
    "dotDotDash dashDotDotHeavy wave wavyHeavy wavyDouble none"
)
HIGHLIGHTS = _words(
    "black blue cyan green magenta red yellow white darkBlue darkCyan darkGreen darkMagenta darkRed darkYellow "
    "darkGray lightGray none"
)
NUMBER_FORMATS = _words(
    "decimal upperRoman lowerRoman upperLetter lowerLetter ordinal cardinalText ordinalText hex chicago "
    "ideographDigital japaneseCounting aiueo iroha decimalFullWidth decimalHalfWidth japaneseLegal "
    "japaneseDigitalTenThousand decimalEnclosedCircle decimalFullWidth2 aiueoFullWidth irohaFullWidth "
    "decimalZero bullet ganada chosung decimalEnclosedFullstop decimalEnclosedParen decimalEnclosedCircleChinese "
    "ideographEnclosedCircle ideographTraditional ideographZodiac ideographZodiacTraditional taiwaneseCounting "
    "ideographLegalTraditional taiwaneseCountingThousand taiwaneseDigital chineseCounting chineseLegalSimplified "
    "chineseCountingThousand koreanDigital koreanCounting koreanLegal koreanDigital2 vietnameseCounting "
    "russianLower russianUpper none numberInDash hebrew1 hebrew2 arabicAlpha arabicAbjad hindiVowels "
    "hindiConsonants hindiNumbers hindiCounting thaiLetters thaiNumbers thaiCounting"
)
JUSTIFY = {
    "left": "left", "start": "left", "center": "center", "right": "right", "end": "right", "both": "both",
    "distribute": "distribute", "thaiDistribute": "thaiDistribute", "highKashida": "highKashida",
    "lowKashida": "lowKashida", "mediumKashida": "mediumKashida",
}
TABLE_JUSTIFY = {"left": "left", "start": "left", "center": "center", "right": "right", "end": "right"}
WIDTH_TYPES = _words("nil pct dxa auto")
TEXT_DIRECTIONS = _words("lrTb tbRl btLr lrTbV tbRlV tbLrV tb rl lr tbV rlV lrV")

BORDER = {"val": BORDER_STYLES, "sz": UINT, "space": UINT, "color": COLOR}
SHADING = {"val": SHADING_PATTERNS, "color": COLOR, "fill": COLOR}
MEASURE = {"w": WIDTH, "type": WIDTH_TYPES}
TAB = {"val": _words("clear left center right decimal bar num start end"), "leader": _words("none dot hyphen underscore heavy middleDot"), "pos": INT}
COLUMN = {"w": UINT, "space": UINT}
TOGGLE = {"val": ON}


@dataclass
class Group:
    """The properties kept for one kind of element, in the order Word's schema writes them."""

    order: tuple[str, ...]
    specs: dict[str, dict]
    sides: dict[str, tuple[str, ...]] = field(default_factory=dict)  # container -> its child elements
    toggles: frozenset[str] = frozenset()

    def child_spec(self, container: str) -> dict:
        return {"tabs": TAB, "cols": COLUMN}.get(container) or (MEASURE if container.endswith("Mar") else BORDER)


_RUN_TOGGLES = _words("b bCs i iCs caps smallCaps strike dstrike rtl")
RUN = Group(
    order=(
        "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "color", "spacing", "w", "kern",
        "position", "sz", "szCs", "highlight", "u", "shd", "vertAlign", "rtl",
    ),
    specs={
        "rFonts": {"ascii": FONT, "hAnsi": FONT, "eastAsia": FONT, "cs": FONT},
        **{t: TOGGLE for t in _RUN_TOGGLES},
        "color": {"val": COLOR},
        "spacing": {"val": INT},
        "w": {"val": UINT},
        "kern": {"val": UINT},
        "position": {"val": INT},
        "sz": {"val": UINT},
        "szCs": {"val": UINT},
        "highlight": {"val": HIGHLIGHTS},
        "u": {"val": UNDERLINES, "color": COLOR},
        "shd": SHADING,
        "vertAlign": {"val": _words("baseline superscript subscript")},
    },
    toggles=_RUN_TOGGLES,
)
_PARAGRAPH_TOGGLES = _words("keepNext keepLines pageBreakBefore widowControl suppressAutoHyphens bidi contextualSpacing")
PARAGRAPH = Group(
    order=(
        "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "pBdr", "shd", "tabs",
        "suppressAutoHyphens", "bidi", "spacing", "ind", "contextualSpacing", "jc",
    ),
    specs={
        **{t: TOGGLE for t in _PARAGRAPH_TOGGLES},
        "framePr": {
            "w": UINT, "h": UINT, "hRule": _words("auto exact atLeast"), "hSpace": UINT, "vSpace": UINT,
            "wrap": _words("auto notBeside around tight through none"), "hAnchor": _words("text margin page"),
            "vAnchor": _words("text margin page"), "x": INT, "y": INT, "xAlign": _words("left center right inside outside"),
            "yAlign": _words("inline top center bottom inside outside"), "dropCap": _words("none drop margin"),
            "lines": UINT, "anchorLock": ON,
        },
        "shd": SHADING,
        "spacing": {
            "before": UINT, "after": UINT, "line": INT, "lineRule": _words("auto exact atLeast"),
            "beforeAutospacing": ON, "afterAutospacing": ON,
        },
        "ind": {"left": INT, "right": INT, "start": INT, "end": INT, "firstLine": UINT, "hanging": UINT},
        "jc": {"val": JUSTIFY},
    },
    sides={"pBdr": ("top", "left", "bottom", "right", "between", "bar"), "tabs": ()},
    toggles=_PARAGRAPH_TOGGLES,
)
# Numbering levels keep their own indentation and tab stop.
LEVEL_PARAGRAPH = Group(order=("tabs", "ind"), specs={"ind": PARAGRAPH.specs["ind"]}, sides={"tabs": ()})
TABLE = Group(
    order=("tblW", "jc", "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar"),
    specs={
        "tblW": MEASURE, "jc": {"val": TABLE_JUSTIFY}, "tblCellSpacing": MEASURE, "tblInd": MEASURE,
        "shd": SHADING, "tblLayout": {"type": _words("fixed autofit")},
    },
    sides={"tblBorders": ("top", "left", "bottom", "right", "insideH", "insideV"), "tblCellMar": ("top", "left", "bottom", "right")},
)
ROW = Group(
    order=("gridBefore", "gridAfter", "wBefore", "wAfter", "cantSplit", "trHeight", "tblHeader", "jc"),
    specs={
        "gridBefore": {"val": UINT}, "gridAfter": {"val": UINT}, "wBefore": MEASURE, "wAfter": MEASURE,
        "cantSplit": TOGGLE, "trHeight": {"val": UINT, "hRule": _words("auto exact atLeast")}, "tblHeader": TOGGLE,
        "jc": {"val": TABLE_JUSTIFY},
    },
    toggles=_words("cantSplit tblHeader"),
)
CELL = Group(
    order=("tcW", "gridSpan", "vMerge", "tcBorders", "shd", "noWrap", "tcMar", "textDirection", "vAlign"),
    specs={
        "tcW": MEASURE, "gridSpan": {"val": UINT}, "vMerge": {"val": _words("restart continue")}, "shd": SHADING,
        "noWrap": TOGGLE, "textDirection": {"val": TEXT_DIRECTIONS}, "vAlign": {"val": _words("top center bottom both")},
    },
    sides={
        "tcBorders": ("top", "left", "bottom", "right", "insideH", "insideV", "tl2br", "tr2bl"),
        "tcMar": ("top", "left", "bottom", "right"),
    },
    toggles=_words("noWrap"),
)
SECTION = Group(
    order=("type", "pgSz", "pgMar", "pgNumType", "cols", "vAlign", "titlePg"),
    specs={
        "type": {"val": _words("nextPage continuous evenPage oddPage nextColumn")},
        "pgSz": {"w": UINT, "h": UINT, "orient": _words("portrait landscape"), "code": UINT},
        "pgMar": {"top": INT, "right": UINT, "bottom": INT, "left": UINT, "header": UINT, "footer": UINT, "gutter": UINT},
        "pgNumType": {"fmt": NUMBER_FORMATS, "start": UINT},
        "cols": {"num": UINT, "space": UINT, "equalWidth": ON, "sep": ON},
        "vAlign": {"val": _words("top center both bottom")},
        "titlePg": TOGGLE,
    },
    sides={"cols": ()},
    toggles=_words("titlePg"),
)

# Elements that change nothing visible, or that the reader handles itself, so
# leaving them out is not counted as formatting lost.
_HANDLED = _words(
    "pStyle rStyle tblStyle numPr outlineLvl rPr sectPr tblLook cnfStyle rPrChange pPrChange tblPrChange trPrChange "
    "tcPrChange sectPrChange tblGridChange lang noProof webHidden snapToGrid autoSpaceDE autoSpaceDN adjustRightInd "
    "kinsoku wordWrap overflowPunct topLinePunct divId hideMark tblOverlap tblStyleRowBandSize tblStyleColBandSize "
    "tblCaption tblDescription vanish specVanish oMath cs eastAsianLayout headerReference footerReference "
    "footnotePr endnotePr paperSrc docGrid printerSettings formProt noEndnote textDirection bidi rtlGutter "
    "suppressLineNumbers mirrorIndents suppressOverlap textboxTightWrap ins del moveFrom moveTo"
)

_NUMBER = re.compile(r"-?\d{1,9}(?:\.\d+)?")
_HEX = re.compile(r"[0-9A-Fa-f]{6}")
_LIMIT = 100_000_000


def _local(tag: object) -> str:
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


@cache
def allowed_fonts() -> dict[str, str]:
    """Casefolded name -> the name as written."""
    path = Path(__file__).resolve().parents[1] / "data" / "fonts.txt"
    names = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {n.casefold(): n for n in names if n and not n.startswith("#")}


GENERIC_FONTS = {"sans": "Calibri", "serif": "Cambria", "mono": "Consolas"}


class Fonts:
    """Font names as written: listed fonts stay, others become a generic font of their kind."""

    def __init__(self, table: etree._Element | None):
        self.kinds: dict[str, str] = {}
        self.alternatives: dict[str, list[str]] = {}
        self.replaced: set[str] = set()
        for f in table.iter(W + "font") if table is not None else ():
            name = (f.get(W + "name") or "").strip().casefold()
            family, pitch, alt = (f.find(W + t) for t in ("family", "pitch", "altName"))
            fam = family.get(W + "val", "") if family is not None else ""
            if fam == "modern" or (pitch is not None and pitch.get(W + "val") == "fixed"):
                self.kinds[name] = "mono"
            elif fam == "roman":
                self.kinds[name] = "serif"
            elif fam:
                self.kinds[name] = "sans"
            if alt is not None:
                self.alternatives[name] = [a.strip().casefold() for a in (alt.get(W + "val") or "").split(",")]

    def name(self, raw: str) -> str:
        allowed = allowed_fonts()
        key = raw.strip().casefold()
        if key in allowed or not steps.enabled("map-fonts"):
            return allowed.get(key, raw.strip())
        for alt in self.alternatives.get(key, []):
            if alt in allowed:
                return allowed[alt]
        self.replaced.add(key)
        kind = self.kinds.get(key)
        if kind is None:
            kind = "mono" if re.search(r"mono|code|courier|console", key) else "serif" if "serif" in key and "sans" not in key else "sans"
        return GENERIC_FONTS[kind]


_THEME_COLORS = {
    "dark1": "dk1", "light1": "lt1", "dark2": "dk2", "light2": "lt2", "text1": "dk1", "background1": "lt1",
    "text2": "dk2", "background2": "lt2", "hyperlink": "hlink", "followedHyperlink": "folHlink",
    **{f"accent{i}": f"accent{i}" for i in range(1, 7)},
}


class Theme:
    """Colours and fonts a document names by their place in its theme."""

    def __init__(self, root: etree._Element | None):
        self.colors: dict[str, str] = {}
        self.fonts: dict[str, str] = {}
        if root is None:
            return
        scheme = next(root.iter(A + "clrScheme"), None)
        for c in scheme if scheme is not None else ():
            srgb, system = c.find(A + "srgbClr"), c.find(A + "sysClr")
            value = srgb.get("val") if srgb is not None else system.get("lastClr") if system is not None else None
            if value and _HEX.fullmatch(value):
                self.colors[_local(c.tag)] = value.upper()
        fonts = next(root.iter(A + "fontScheme"), None)
        for which in ("major", "minor"):
            node = fonts.find(A + which + "Font") if fonts is not None else None
            for script, tag in (("HAnsi", "latin"), ("Ascii", "latin"), ("EastAsia", "ea"), ("Bidi", "cs")):
                el = node.find(A + tag) if node is not None else None
                if el is not None and el.get("typeface"):
                    self.fonts[which + script] = el.get("typeface")

    def color(self, name: str, tint: str | None, shade: str | None) -> str | None:
        value = self.colors.get(_THEME_COLORS.get(name, ""))
        if value is None:
            return None
        rgb = [int(value[i : i + 2], 16) for i in (0, 2, 4)]
        if tint and _HEX.fullmatch("0000" + tint):
            t = int(tint, 16) / 255
            rgb = [round(c * t + 255 * (1 - t)) for c in rgb]
        if shade and _HEX.fullmatch("0000" + shade):
            s = int(shade, 16) / 255
            rgb = [round(c * s) for c in rgb]
        return "".join(f"{min(max(c, 0), 255):02X}" for c in rgb)


def _value(kind: object, raw: str | None, name: str, fonts: Fonts) -> str | None:
    if kind == ON:
        if raw is None:
            return "1" if name == "val" else None
        low = raw.lower()
        return "1" if low in ("1", "true", "on") else "0" if low in ("0", "false", "off", "none") else None
    if raw is None:
        return None
    if kind in (INT, UINT, WIDTH):
        scale = 1
        if kind == WIDTH and raw.endswith("%"):
            raw, scale = raw[:-1], 50  # a percentage, in fiftieths of a percent
        if not _NUMBER.fullmatch(raw):
            return None
        n = round(float(raw) * scale)
        return None if abs(n) > _LIMIT or (kind == UINT and n < 0) else str(n)
    if kind == COLOR:
        return raw.upper() if _HEX.fullmatch(raw) else "auto" if raw.lower() == "auto" else None
    if kind == FONT:
        return fonts.name(raw) if raw.strip() else None
    if isinstance(kind, dict):
        return kind.get(raw)
    if isinstance(kind, frozenset):
        return raw if raw in kind else None
    raise ValueError(f"unknown value kind {kind!r}")


def merge(base: Props, over: Props) -> Props:
    """``over`` on top of ``base``, property by property and attribute by attribute."""
    out = {k: dict(v) for k, v in base.items()}
    for key, attrs in over.items():
        if key.startswith("tabs/") and attrs.get("val") == "clear":
            out.pop(key, None)
            continue
        current = out.setdefault(key, {})
        if key == "ind":
            if "hanging" in attrs:
                current.pop("firstLine", None)
            if "firstLine" in attrs:
                current.pop("hanging", None)
        current.update(attrs)
    return out


def toggle_merge(base: Props, over: Props, toggles: frozenset[str]) -> Props:
    """A character style on its paragraph: toggles such as bold switch the
    paragraph's value instead of setting it (ECMA-376 17.7.3)."""
    out = merge(base, {k: v for k, v in over.items() if k not in toggles})
    for key in toggles & over.keys():
        on = base.get(key, {}).get("val") == "1"
        out[key] = {"val": "1" if on != (over[key].get("val") == "1") else "0"}
    return out


def _rank(group: Group, key: str) -> tuple[int, int, int]:
    container, _, child = key.partition("/")
    i = group.order.index(container)
    if not child:
        return (i, 0, 0)
    if container in ("tabs", "cols"):
        return (i, 1, int(child))
    return (i, 1, group.sides[container].index(child))


def freeze(props: Props, group: Group, *, keep_off: bool = False) -> Fmt:
    """Properties in writing order, without the ones that equal Word's defaults
    (``keep_off`` keeps toggles that are explicitly off, which numbering needs)."""
    items = []
    for key, attrs in props.items():
        if key in group.toggles:
            if attrs.get("val") != "1" and not keep_off:
                continue
            attrs = {"val": attrs.get("val", "1")}
        elif not attrs:
            continue
        val = attrs.get("val")
        if (
            (key == "u" and val in (None, "none"))
            or (key == "vertAlign" and val == "baseline")
            or (key == "highlight" and val == "none")
            or (key == "color" and val in (None, "auto"))
            or (key == "shd" and val in (None, "clear", "nil") and attrs.get("fill", "auto") == "auto")
            or (key.startswith(("pBdr/", "tblBorders/")) and val in (None, "nil", "none"))
            or (key.startswith("tabs/") and ("pos" not in attrs or val in (None, "clear")))
        ):
            continue
        items.append((_rank(group, key), key, tuple(sorted(attrs.items()))))
    items.sort()
    return tuple((k, a) for _, k, a in items)


def thaw(fmt: Fmt) -> Props:
    return {k: dict(a) for k, a in fmt}


@dataclass
class _Style:
    kind: str
    based: str | None
    p: Props
    r: Props
    tbl: Props
    tr: Props
    tc: Props
    cond: dict[str, dict[str, Props]]
    num: tuple[str | None, int | None] | None
    bands: tuple[int | None, int | None]


@dataclass
class TableContext:
    props: Props
    look: set[str]
    row_band: int
    col_band: int
    cond: dict[str, dict[str, Props]]


@dataclass
class CellContext:
    table: TableContext
    types: list[str]


@dataclass
class ParaFormat:
    style: str | None  # the paragraph style's id in the original
    props: Props
    run: Props  # what a run starts from: defaults, table and paragraph style
    mark: Props  # the paragraph mark, which sizes an empty paragraph
    num: tuple[str, int] | None  # numbering instance and level


_COND_ORDER = (
    "wholeTable", "band1Vert", "band2Vert", "band1Horz", "band2Horz", "firstCol", "lastCol", "firstRow", "lastRow",
    "neCell", "nwCell", "seCell", "swCell",
)
_LOOK_BITS = {"firstRow": 0x20, "lastRow": 0x40, "firstColumn": 0x80, "lastColumn": 0x100, "noHBand": 0x200, "noVBand": 0x400}


def _on(el: etree._Element | None) -> bool:
    return el is not None and (el.get(W + "val") or "1").lower() not in ("0", "false", "off", "none")


def _number_properties(ppr: etree._Element | None) -> tuple[str | None, int | None] | None:
    numpr = ppr.find(W + "numPr") if ppr is not None else None
    if numpr is None:
        return None
    num_el, lvl_el = numpr.find(W + "numId"), numpr.find(W + "ilvl")
    num_id = num_el.get(W + "val") if num_el is not None else None
    ilvl = lvl_el.get(W + "val", "") if lvl_el is not None else ""
    return num_id, int(ilvl) if ilvl.isdigit() else None


class Formatting:
    """Resolves a document's formatting and records what it needs to write it again."""

    def __init__(
        self,
        styles: etree._Element | None,
        theme: etree._Element | None,
        font_table: etree._Element | None,
        numbering: etree._Element | None,
        dropped: Counter[str],
    ):
        self.dropped = dropped
        self.theme = Theme(theme)
        self.fonts = Fonts(font_table)
        self.styles: dict[str, _Style] = {}
        self.defaults_run: Props = {}
        self.defaults_paragraph: Props = {}
        self.default: dict[str, str | None] = {"paragraph": None, "character": None, "table": None}
        self.output_styles: dict[str, int] = {}
        self._cache: dict[tuple[str | None, str, str], Props] = {}
        if styles is not None:
            self._load_styles(styles)
        self.lists = _Lists(self, numbering)

    # -- reading -----------------------------------------------------------
    def _values(self, el: etree._Element, spec: dict) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, kind in spec.items():
            raw = el.get(W + name)
            if kind == COLOR:
                prefix = "themeFill" if name == "fill" else "theme"
                theme = el.get(W + ("themeFill" if name == "fill" else "themeColor"))
                if theme and (raw is None or raw.lower() == "auto"):
                    raw = self.theme.color(theme, el.get(W + prefix + "Tint"), el.get(W + prefix + "Shade")) or raw
            elif kind == FONT:
                theme = el.get(W + {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme", "eastAsia": "eastAsiaTheme", "cs": "cstheme"}[name])
                if theme and theme in self.theme.fonts:
                    raw = self.theme.fonts[theme]
            value = _value(kind, raw, name, self.fonts)
            if value is not None:
                out[name] = value
            elif raw is not None:
                self.dropped["formatting value not kept"] += 1
        return out

    def read(self, el: etree._Element | None, group: Group) -> Props:
        out: Props = {}
        for child in el if el is not None else ():
            tag = _local(child.tag)
            if not tag:
                continue
            if tag in group.sides:
                if tag in group.specs:
                    out[tag] = self._values(child, group.specs[tag])
                subs = [c for c in child if isinstance(c.tag, str)]
                for i, sub in enumerate(subs):
                    side = {"start": "left", "end": "right"}.get(_local(sub.tag), _local(sub.tag))
                    attrs = self._values(sub, group.child_spec(tag))
                    if tag == "tabs":
                        if "pos" in attrs:
                            out[f"tabs/{attrs['pos']}"] = attrs
                    elif tag == "cols":
                        out[f"cols/{i}"] = attrs
                    elif side in group.sides[tag]:
                        out[f"{tag}/{side}"] = attrs
            elif tag in group.specs:
                attrs = self._values(child, group.specs[tag])
                if tag == "vMerge" and "val" not in attrs:
                    attrs = {"val": "continue"}
                if tag == "ind":
                    for logical, physical in (("start", "left"), ("end", "right")):
                        if logical in attrs:
                            attrs.setdefault(physical, attrs[logical])
                            del attrs[logical]
                out[tag] = attrs
            elif child.tag.startswith("{http://schemas.microsoft.com/office/word/2010/wordml}"):
                self.dropped["Word text effect not kept"] += 1  # glow, gradient text: w:color still holds the colour
            elif tag not in _HANDLED:
                self.dropped["formatting not kept"] += 1
        return out

    def _load_styles(self, root: etree._Element) -> None:
        defaults = root.find(W + "docDefaults")
        if defaults is not None:
            self.defaults_run = self.read(defaults.find(f"{W}rPrDefault/{W}rPr"), RUN)
            self.defaults_paragraph = self.read(defaults.find(f"{W}pPrDefault/{W}pPr"), PARAGRAPH)
        for st in root.iter(W + "style"):
            sid = st.get(W + "styleId")
            if not sid:
                continue
            kind = st.get(W + "type") or "paragraph"
            based = st.find(W + "basedOn")
            ppr, tblpr = st.find(W + "pPr"), st.find(W + "tblPr")
            cond: dict[str, dict[str, Props]] = {}
            for part in st.findall(W + "tblStylePr"):
                if part.get(W + "type") in _COND_ORDER:
                    cond[part.get(W + "type")] = self._table_parts(part)
            bands: list[int | None] = []
            for tag in ("tblStyleRowBandSize", "tblStyleColBandSize"):
                el = tblpr.find(W + tag) if tblpr is not None else None
                v = el.get(W + "val", "") if el is not None else ""
                bands.append(int(v) if v.isdigit() and int(v) > 0 else None)
            parts = self._table_parts(st)
            self.styles[sid] = _Style(
                kind, based.get(W + "val") if based is not None else None, parts["p"], parts["r"], parts["tbl"],
                parts["tr"], parts["tc"], cond, _number_properties(ppr), (bands[0], bands[1]),
            )
            if _on(st.find(W + "default")) or (st.get(W + "default") or "").lower() in ("1", "true", "on"):
                self.default[kind] = sid

    def _table_parts(self, el: etree._Element) -> dict[str, Props]:
        return {
            "p": self.read(el.find(W + "pPr"), PARAGRAPH),
            "r": self.read(el.find(W + "rPr"), RUN),
            "tbl": self.read(el.find(W + "tblPr"), TABLE),
            "tr": self.read(el.find(W + "trPr"), ROW),
            "tc": self.read(el.find(W + "tcPr"), CELL),
        }

    def chain(self, sid: str | None, kind: str) -> list[_Style]:
        out: list[_Style] = []
        seen: set[str] = set()
        while sid and sid in self.styles and sid not in seen and len(out) < 20:
            st = self.styles[sid]
            if st.kind != kind:
                break
            seen.add(sid)
            out.append(st)
            sid = st.based
        return out[::-1]

    def style_props(self, sid: str | None, kind: str, part: str) -> Props:
        key = (sid, kind, part)
        if key not in self._cache:
            props: Props = {}
            for st in self.chain(sid, kind):
                props = merge(props, getattr(st, part))
            self._cache[key] = props
        return self._cache[key]

    # -- paragraphs and runs -------------------------------------------------
    def paragraph_style(self, ppr: etree._Element | None) -> str | None:
        el = ppr.find(W + "pStyle") if ppr is not None else None
        sid = el.get(W + "val") if el is not None else None
        if sid in self.styles and self.styles[sid].kind == "paragraph":
            return sid
        return self.default["paragraph"]

    def paragraph(self, ppr: etree._Element | None, cell: CellContext | None = None) -> ParaFormat:
        sid = self.paragraph_style(ppr)
        style_p = self.style_props(sid, "paragraph", "p")
        style_r = self.style_props(sid, "paragraph", "r")
        table_p: Props = {}
        table_r: Props = {}
        for t in cell.types if cell else ():
            table_p = merge(table_p, cell.table.cond.get(t, {}).get("p", {}))
            table_r = merge(table_r, cell.table.cond.get(t, {}).get("r", {}))
        # Numbering: a level and an instance, from the paragraph or its style.
        direct = _number_properties(ppr)
        styled = next((st.num for st in reversed(self.chain(sid, "paragraph")) if st.num), None)
        num_id = (direct[0] if direct and direct[0] is not None else None) or (styled[0] if styled else None)
        ilvl = direct[1] if direct and direct[1] is not None else (styled[1] if styled and styled[1] is not None else 0)
        num = (num_id, min(ilvl, 8)) if num_id and num_id != "0" and self.lists.exists(num_id) else None
        level_p = {k: v for k, v in self.lists.level_paragraph(*num).items() if k == "ind"} if num else {}
        # ECMA-376 17.7.2: defaults, then the table style, then the paragraph style.
        # A table style's spacing only shows where no paragraph style sets it.
        layers = [self.defaults_paragraph, table_p, style_p]
        if num and direct and direct[0] is not None:
            layers.append(level_p)  # numbering set on the paragraph indents over its style
        elif num:
            layers.insert(2, level_p)
        props: Props = {}
        for layer in [*layers, self.read(ppr, PARAGRAPH)]:
            props = merge(props, layer)
        run_layers = [self.defaults_run, table_r, style_r]
        run: Props = {}
        for layer in run_layers:
            run = merge(run, layer)
        mark = self.run(ppr.find(W + "rPr") if ppr is not None else None, ParaFormat(sid, props, run, {}, num))
        return ParaFormat(sid, props, run, mark, num)

    def run(self, rpr: etree._Element | None, para: ParaFormat) -> Props:
        el = rpr.find(W + "rStyle") if rpr is not None else None
        char = self.style_props(el.get(W + "val"), "character", "r") if el is not None else {}
        return merge(toggle_merge(para.run, char, _RUN_TOGGLES), self.read(rpr, RUN))

    def output_style(self, sid: str | None) -> int:
        """A neutral style number for each original paragraph style, so that
        spacing Word suppresses between paragraphs of one style still is."""
        if sid is None or sid == self.default["paragraph"]:
            return 0
        return self.output_styles.setdefault(sid, len(self.output_styles) + 1)

    # -- tables --------------------------------------------------------------
    def table(self, tblpr: etree._Element | None) -> TableContext:
        el = tblpr.find(W + "tblStyle") if tblpr is not None else None
        sid = el.get(W + "val") if el is not None else self.default["table"]
        chain = self.chain(sid, "table")
        props: Props = {}
        cond: dict[str, dict[str, Props]] = {}
        row_band = col_band = 1
        for st in chain:
            props = merge(props, st.tbl)
            whole = {"p": st.p, "r": st.r, "tr": st.tr, "tc": st.tc}
            for t, parts in [("wholeTable", whole), *st.cond.items()]:
                current = cond.setdefault(t, {})
                for k, v in parts.items():
                    current[k] = merge(current.get(k, {}), v)
            row_band = st.bands[0] or row_band
            col_band = st.bands[1] or col_band
        props = merge(props, self.read(tblpr, TABLE))
        if tblpr is not None and tblpr.find(W + "tblpPr") is not None:
            self.dropped["floating table position"] += 1
        return TableContext(props, _look(tblpr.find(W + "tblLook") if tblpr is not None else None), row_band, col_band, cond)

    def row(self, trpr: etree._Element | None, table: TableContext, types: list[str]) -> Props:
        props: Props = {}
        for t in types:
            props = merge(props, table.cond.get(t, {}).get("tr", {}))
        return merge(props, self.read(trpr, ROW))

    def cell(self, tcpr: etree._Element | None, cell: CellContext, edges: dict[str, tuple[bool, bool, bool, bool]]) -> Props:
        props: Props = {}
        for t in cell.types:
            props = merge(props, _region_borders(cell.table.cond.get(t, {}).get("tc", {}), edges[t]))
        return merge(props, self.read(tcpr, CELL))

    # -- sections and settings ---------------------------------------------------
    def section(self, sectpr: etree._Element | None) -> Fmt:
        return freeze(self.read(sectpr, SECTION), SECTION)


def _look(el: etree._Element | None) -> set[str]:
    out: set[str] = set()
    if el is None:
        return out
    val = el.get(W + "val") or ""
    if re.fullmatch(r"[0-9A-Fa-f]{1,4}", val):
        out = {name for name, bit in _LOOK_BITS.items() if int(val, 16) & bit}
    for name in _LOOK_BITS:
        attr = el.get(W + name)
        if attr is not None:
            (out.add if attr.lower() in ("1", "true", "on") else out.discard)(name)
    return out


def cell_types(table: TableContext, r: int, rows: int, c0: int, c1: int, cols: int) -> tuple[list[str], dict[str, tuple[bool, bool, bool, bool]]]:
    """Which conditional formats of the table style apply to a cell, in the order
    Word applies them, and for each whether the cell is on that region's top,
    bottom, left and right edge."""
    look = table.look
    head, foot = int("firstRow" in look), int("lastRow" in look)
    lead, tail = int("firstColumn" in look), int("lastColumn" in look)
    first_row, last_row = bool(head) and r == 0, bool(foot) and r == rows - 1
    first_col, last_col = bool(lead) and c0 == 0, bool(tail) and c1 == cols - 1
    whole = (r == 0, r == rows - 1, c0 == 0, c1 == cols - 1)
    types = ["wholeTable"]
    edges = {"wholeTable": whole}
    if "noVBand" not in look and not first_col and not last_col:
        k = (c0 - lead) // table.col_band
        start = lead + k * table.col_band
        t = "band1Vert" if k % 2 == 0 else "band2Vert"
        types.append(t)
        edges[t] = (r == 0, r == rows - 1, c0 == start, c1 >= start + table.col_band - 1 or c1 == cols - 1 - tail)
    if "noHBand" not in look and not first_row and not last_row:
        k = (r - head) // table.row_band
        start = head + k * table.row_band
        t = "band1Horz" if k % 2 == 0 else "band2Horz"
        types.append(t)
        edges[t] = (r == start, r == start + table.row_band - 1 or r == rows - 1 - foot, c0 == 0, c1 == cols - 1)
    for t, on, edge in (
        ("firstCol", first_col, (r == 0, r == rows - 1, True, True)),
        ("lastCol", last_col, (r == 0, r == rows - 1, True, True)),
        ("firstRow", first_row, (True, True, c0 == 0, c1 == cols - 1)),
        ("lastRow", last_row, (True, True, c0 == 0, c1 == cols - 1)),
        ("neCell", first_row and last_col, (True, True, True, True)),
        ("nwCell", first_row and first_col, (True, True, True, True)),
        ("seCell", last_row and last_col, (True, True, True, True)),
        ("swCell", last_row and first_col, (True, True, True, True)),
    ):
        if on:
            types.append(t)
            edges[t] = edge
    return types, edges


def _region_borders(tc: Props, edge: tuple[bool, bool, bool, bool]) -> Props:
    """A conditional format's cell borders for one cell: the region's outer
    borders on its edges, its inside borders between its cells."""
    out = {k: v for k, v in tc.items() if not k.startswith("tcBorders/")}
    borders = {k.split("/", 1)[1]: v for k, v in tc.items() if k.startswith("tcBorders/")}
    top, bottom, left, right = edge
    for side, outer, inner in (("top", top, "insideH"), ("bottom", bottom, "insideH"), ("left", left, "insideV"), ("right", right, "insideV")):
        chosen = borders.get(side) if outer else borders.get(inner)
        if chosen is not None:
            out[f"tcBorders/{side}"] = chosen
    for side in ("tl2br", "tr2bl"):
        if side in borders:
            out[f"tcBorders/{side}"] = borders[side]
    return out


class _Lists:
    """The original numbering, and the definitions written for the instances used."""

    def __init__(self, fmt: Formatting, root: etree._Element | None):
        self.fmt = fmt
        self.abstract: dict[str, etree._Element] = {}
        self.nums: dict[str, tuple[str, dict[int, tuple[int | None, etree._Element | None]]]] = {}
        for an in root.iter(W + "abstractNum") if root is not None else ():
            self.abstract[an.get(W + "abstractNumId", "")] = an
        for num in root.iter(W + "num") if root is not None else ():
            ref = num.find(W + "abstractNumId")
            overrides: dict[int, tuple[int | None, etree._Element | None]] = {}
            for o in num.findall(W + "lvlOverride"):
                ilvl = o.get(W + "ilvl", "")
                start = o.find(W + "startOverride")
                sval = start.get(W + "val", "") if start is not None else ""
                if ilvl.isdigit():
                    overrides[int(ilvl)] = (int(sval) if sval.isdigit() else None, o.find(W + "lvl"))
            if ref is not None:
                self.nums[num.get(W + "numId", "")] = (ref.get(W + "val", ""), overrides)
        self.out_abstract: dict[str, int] = {}
        self.out_num: dict[str, int] = {}
        self.definitions: list[ListDefinition] = []
        self.instances: list[ListInstance] = []

    def exists(self, num_id: str) -> bool:
        return num_id in self.nums and bool(self._levels(self.nums[num_id][0]))

    def _levels(self, aid: str, depth: int = 0) -> dict[int, etree._Element]:
        an = self.abstract.get(aid)
        if an is None:
            return {}
        link = an.find(W + "numStyleLink")
        if link is not None and depth < 5:
            st = self.fmt.styles.get(link.get(W + "val", ""))
            target = self.nums.get(st.num[0] or "") if st and st.num else None
            return self._levels(target[0], depth + 1) if target else {}
        return {int(lv.get(W + "ilvl")): lv for lv in an.findall(W + "lvl") if (lv.get(W + "ilvl") or "").isdigit()}

    def level(self, num_id: str, ilvl: int) -> etree._Element | None:
        aid, overrides = self.nums.get(num_id, ("", {}))
        override = overrides.get(ilvl, (None, None))[1]
        return override if override is not None else self._levels(aid).get(ilvl)

    def numbered(self, num_id: str, ilvl: int) -> bool:
        lvl = self.level(num_id, ilvl)
        fmt_el = lvl.find(W + "numFmt") if lvl is not None else None
        return fmt_el is not None and fmt_el.get(W + "val", "") not in ("bullet", "none", "")

    def level_paragraph(self, num_id: str, ilvl: int) -> Props:
        lvl = self.level(num_id, ilvl)
        return self.fmt.read(lvl.find(W + "pPr"), LEVEL_PARAGRAPH) if lvl is not None else {}

    def output(self, num_id: str) -> int:
        """The numbering instance written for an original one (DocSan's own lists are 1 and 2)."""
        if num_id not in self.out_num:
            aid, overrides = self.nums[num_id]
            if aid not in self.out_abstract:
                an = self.abstract.get(aid)
                kind = an.find(W + "multiLevelType") if an is not None else None
                multi = kind.get(W + "val", "") if kind is not None else ""
                levels = self._levels(aid)
                self.out_abstract[aid] = len(self.definitions)
                self.definitions.append(ListDefinition(
                    [self._convert(i, levels[i]) for i in sorted(levels) if i <= 8],
                    multi if multi in ("singleLevel", "multilevel", "hybridMultilevel") else "multilevel",
                ))
            self.out_num[num_id] = len(self.instances) + 3
            self.instances.append(ListInstance(
                self.out_abstract[aid],
                [(i, start, self._convert(i, lvl) if lvl is not None else None) for i, (start, lvl) in sorted(overrides.items()) if i <= 8],
            ))
        return self.out_num[num_id]

    def _convert(self, ilvl: int, el: etree._Element) -> Level:
        def val(tag: str) -> str | None:
            c = el.find(W + tag)
            return c.get(W + "val") if c is not None else None

        number_format = val("numFmt") or "decimal"
        if number_format not in NUMBER_FORMATS:
            self.fmt.dropped["list number style not kept"] += 1
            number_format = "decimal"
        text = val("lvlText") or ""
        if el.find(W + "lvlPicBulletId") is not None:
            number_format, text = "bullet", "•"  # a picture bullet becomes a plain one
        start, restart = val("start") or "", val("lvlRestart") or ""
        suffix = val("suff") or "tab"
        return Level(
            ilvl=ilvl,
            start=int(start) if start.isdigit() else 1,
            format=number_format,
            text=text[:64],
            justify=TABLE_JUSTIFY.get(val("lvlJc") or "left", "left"),
            restart=int(restart) if restart.isdigit() else None,
            legal=_on(el.find(W + "isLgl")),
            suffix=suffix if suffix in ("tab", "space", "nothing") else "tab",
            paragraph=freeze(self.fmt.read(el.find(W + "pPr"), LEVEL_PARAGRAPH), LEVEL_PARAGRAPH),
            run=freeze(self.fmt.read(el.find(W + "rPr"), RUN), RUN, keep_off=True),
        )

    def model(self) -> Numbering:
        return Numbering(self.definitions, self.instances)


# --- Writing ----------------------------------------------------------------------


def _element(tag: str, attrs: tuple[tuple[str, str], ...], toggle: bool) -> str:
    if toggle and attrs == (("val", "1"),):
        return f"<w:{tag}/>"
    return f"<w:{tag}{''.join(f' w:{k}=\"{v}\"' for k, v in attrs)}/>"


def to_xml(fmt: Fmt, group: Group) -> str:
    """Properties as WordprocessingML elements, in schema order."""
    out: list[str] = []
    open_container = ""
    for key, attrs in fmt:
        container, _, child = key.partition("/")
        if child:
            if open_container != container:
                if open_container:
                    out.append(f"</w:{open_container}>")
                out.append(f"<w:{container}>")
                open_container = container
            out.append(_element({"tabs": "tab", "cols": "col"}.get(container, child), attrs, False))
            continue
        if open_container:
            out.append(f"</w:{open_container}>")
            open_container = ""
        if key in group.sides:
            out.append(_element(key, attrs, False)[:-2] + ">")
            open_container = key
        else:
            out.append(_element(key, attrs, key in group.toggles))
    if open_container:
        out.append(f"</w:{open_container}>")
    return "".join(out)
