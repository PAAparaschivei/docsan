"""Chart values as the chart shows them.

A chart stores raw numbers with a number format, such as 0.25 with "0%" or
45292 with "mmm yy". A table of raw numbers would misstate what the chart
shows, so the common formats are applied here: percentages, decimals,
thousands separators and scaling, currency and other literal text,
scientific notation and dates. A format outside these returns None, and the
caller shows the stored value and says so.
"""

from __future__ import annotations

import datetime as dt
import re

_DATE_TOKENS = re.compile(r"yyyy|yy|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|AM/PM|am/pm|A/P|a/p", re.IGNORECASE)
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def general(x: float) -> str:
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.10g}"


def _literals(section: str) -> tuple[str, list[str]]:
    """The format with quoted text, escapes and currency tags replaced by
    private-use placeholders, and the literal texts in order."""
    texts: list[str] = []

    def keep(text: str) -> str:
        texts.append(text)
        return "\ue000" + chr(0xE100 + len(texts) - 1)

    out = re.sub(r'"([^"]*)"', lambda m: keep(m.group(1)), section)
    out = re.sub(r"\[\$([^\]-]*)(?:-[^\]]*)?\]", lambda m: keep(m.group(1)), out)
    out = re.sub(r"\[[^\]]*\]", "", out)  # colours and conditions
    out = re.sub(r"\\(.)", lambda m: keep(m.group(1)), out)
    out = re.sub(r"_.", lambda m: keep(" "), out)  # space the width of a character
    out = re.sub(r"\*.", "", out)  # fill characters
    return out, texts


def _restore(text: str, texts: list[str]) -> str:
    return re.sub("\ue000(.)", lambda m: texts[ord(m.group(1)) - 0xE100], text)


def _date(x: float, fmt: str, texts: list[str]) -> str:
    base = dt.datetime(1899, 12, 30, tzinfo=dt.UTC)  # Excel serial dates have no time zone
    when = base + dt.timedelta(days=x)
    twelve = bool(re.search(r"AM/PM|A/P", fmt, re.IGNORECASE))
    out: list[str] = []
    pos = 0
    tokens = list(_DATE_TOKENS.finditer(fmt))
    for i, m in enumerate(tokens):
        out.append(fmt[pos : m.start()])
        t = m.group().lower()
        prev = tokens[i - 1].group().lower() if i else ""
        nxt = tokens[i + 1].group().lower() if i + 1 < len(tokens) else ""
        minutes = t in ("m", "mm") and (prev.startswith("h") or nxt.startswith("s"))
        if t == "yyyy":
            out.append(f"{when.year:04d}")
        elif t == "yy":
            out.append(f"{when.year % 100:02d}")
        elif minutes:
            out.append(f"{when.minute:02d}" if t == "mm" else str(when.minute))
        elif t == "mmmmm":
            out.append(_MONTHS[when.month - 1][0])
        elif t == "mmmm":
            out.append(_MONTHS[when.month - 1])
        elif t == "mmm":
            out.append(_MONTHS[when.month - 1][:3])
        elif t == "mm":
            out.append(f"{when.month:02d}")
        elif t == "m":
            out.append(str(when.month))
        elif t == "dddd":
            out.append(_DAYS[when.weekday()])
        elif t == "ddd":
            out.append(_DAYS[when.weekday()][:3])
        elif t == "dd":
            out.append(f"{when.day:02d}")
        elif t == "d":
            out.append(str(when.day))
        elif t in ("hh", "h"):
            hour = when.hour % 12 or 12 if twelve else when.hour
            out.append(f"{hour:02d}" if t == "hh" else str(hour))
        elif t in ("ss", "s"):
            out.append(f"{when.second:02d}" if t == "ss" else str(when.second))
        elif t in ("am/pm", "a/p"):
            pm = when.hour >= 12
            word = ("PM" if pm else "AM") if t == "am/pm" else ("P" if pm else "A")
            out.append(word if m.group()[0].isupper() else word.lower())
        pos = m.end()
    out.append(fmt[pos:])
    return _restore("".join(out), texts)


def _number(x: float, fmt: str, texts: list[str]) -> str | None:
    """A non-negative number under a numeric format section."""
    if "/" in fmt and "?" in fmt:
        return None  # fractions
    exp = re.search(r"([0#?.]+)E([+-])(0+)", fmt, re.IGNORECASE)
    if exp:
        decimals = len(exp.group(1).split(".")[1]) if "." in exp.group(1) else 0
        mantissa, exponent = f"{x:.{decimals}E}".split("E")
        sign = "-" if int(exponent) < 0 else ("+" if exp.group(2) == "+" else "")
        return _restore(f"{fmt[: exp.start()]}{mantissa}E{sign}{abs(int(exponent)):0{len(exp.group(3))}d}{fmt[exp.end():]}", texts)
    m = re.search(r"[0#?][0#?,.]*|\.[0#?]+", fmt)
    if not m:
        return _restore(fmt, texts)  # only text, such as "n/a"
    x *= 100 ** fmt.count("%")
    body = m.group()
    scale = len(body) - len(body.rstrip(","))
    body = body.rstrip(",")
    x /= 1000**scale  # each trailing comma divides by a thousand
    integer, _, fraction = body.partition(".")
    decimals = sum(c in "0#?" for c in fraction)
    required = len(fraction) - len(fraction.lstrip("0"))
    grouped = "," in integer
    text = f"{x:,.{decimals}f}" if grouped else f"{x:.{decimals}f}"
    whole, _, frac = text.partition(".")
    frac = frac[: max(required, len(frac.rstrip("0")))]
    min_integer = integer.count("0")
    digits = whole.replace(",", "")
    if min_integer == 0 and digits == "0":
        whole = ""
    elif len(digits) < min_integer:
        whole = f"{int(digits):0{min_integer}d}"
    return _restore(fmt[: m.start()] + whole + ("." + frac if frac else "") + fmt[m.end():], texts)


def format_value(raw: str, code: str) -> str | None:
    """The value as the chart shows it, or None when the format is not understood."""
    try:
        x = float(raw)
    except ValueError:
        return raw
    if not code or code.strip().lower() == "general":
        return general(x)
    sections = code.split(";")
    if x < 0 and len(sections) > 1 and sections[1].strip():
        section, x = sections[1], -x
        negative = ""
    elif x == 0 and len(sections) > 2 and sections[2].strip():
        section, negative = sections[2], ""
    else:
        section, negative = sections[0], "-" if x < 0 else ""
    fmt, texts = _literals(section)
    if fmt.strip() == "@":
        return raw
    if fmt.strip().lower() == "general":
        return negative + general(abs(x))
    if _DATE_TOKENS.search(re.sub("\ue000.", "", fmt).replace("E+", "").replace("E-", "")) and not re.search(r"[0#?]", fmt):
        return _date(x, fmt, texts)
    out = _number(abs(x), fmt, texts)
    if out is None:
        return None
    return negative + out if negative and any(c in "123456789" for c in out) else out
