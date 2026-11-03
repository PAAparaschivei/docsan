"""Text normalization for matching, with a map back to original offsets.

Matching runs on a normalized view so that look-alike spellings of a name are
found. Each normalized character records the index of the original character
it came from, so a match can be replaced in the original text.
"""

from __future__ import annotations

import unicodedata

from . import steps

# Invisible characters that can hide inside a name: zero-width spaces and
# joiners, soft hyphen, bidirectional controls, variation selectors.
ZERO_WIDTH = frozenset(
    chr(c)
    for c in [
        0x00AD, 0x034F, 0x061C, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E,
        *range(0x200B, 0x2010), *range(0x202A, 0x202F), *range(0x2060, 0x2070),
        0x3164, *range(0xFE00, 0xFE10), 0xFEFF, 0xFFA0,
    ]
)

# Letters from other scripts that look like Latin letters.
CONFUSABLES = {
    # Cyrillic
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h", "о": "o",
    "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "ѕ": "s", "і": "i", "ї": "i",
    "ј": "j", "ԁ": "d", "ԛ": "q", "ԝ": "w", "һ": "h", "ӏ": "l", "ɡ": "g",
    "А": "A", "В": "B", "Е": "E", "Ё": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "І": "I", "Ї": "I",
    "Ј": "J", "Ԁ": "D", "Ԛ": "Q", "Ԝ": "W", "Һ": "H", "Ӏ": "I",
    # Greek
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p",
    "τ": "t", "υ": "u", "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H",
    "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y",
    "Χ": "X",
    # Latin variants that do not decompose
    "ı": "i", "ȷ": "j", "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T", "ℓ": "l",
}

DASHES = frozenset("‐‑‒–—―⁃−⸺⸻﹘﹣－")


def _expand(ch: str, fold_case: bool) -> list[str]:
    if steps.enabled("map-confusables"):
        ch = CONFUSABLES.get(ch, ch)
    if steps.enabled("fold-compatibility"):
        chars = unicodedata.normalize("NFKD", ch)
    elif steps.enabled("fold-accents"):
        chars = unicodedata.normalize("NFD", ch)
    else:
        chars = ch
    out: list[str] = []
    for c in chars:
        if steps.enabled("fold-accents") and unicodedata.combining(c):
            continue
        if steps.enabled("map-confusables"):
            c = CONFUSABLES.get(c, c)
        if steps.enabled("map-dashes") and c in DASHES:
            c = "-"
        if steps.enabled("map-spaces") and (c.isspace() or unicodedata.category(c) == "Zs"):
            c = " "
        if fold_case and steps.enabled("fold-case"):
            out.extend(c.casefold())
        else:
            out.append(c)
    return out


def normalize(text: str, *, fold_case: bool = True) -> tuple[str, list[int]]:
    """Return the normalized text and, per character, its original index."""
    out: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        if steps.enabled("strip-zero-width") and ch in ZERO_WIDTH:
            continue
        for c in _expand(ch, fold_case):
            if c == " " and out and out[-1] == " ":
                continue
            out.append(c)
            origin.append(i)
    return "".join(out), origin


def norm(text: str, *, fold_case: bool = True) -> str:
    return normalize(text, fold_case=fold_case)[0].strip()


def fold_word(word: str) -> str:
    """Fold a word for dictionary lookup: no accents, no case."""
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
