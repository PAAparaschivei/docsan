"""Unfamiliar-word review (decision D-014).

Any word that is absent from the dictionaries, the technical glossary and the
profile's approved words must be classified before a file is released. This
catches company, product and people names that the profile does not list yet.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .text import fold_word

_TOKEN = re.compile(r"[^\W_](?:[\w'’.\-]*[^\W_])?")
_LABEL = re.compile(r"\[[A-Z]+(?:-[A-Z]+)?(?:-\d+)?\]")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)


@dataclass
class ReviewItem:
    word: str
    context: str
    count: int = 1
    kind: str = ""  # "place" for countries, nationalities and cities (context clues, D-011)


def _read_words(text: str) -> set[str]:
    words: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.add(fold_word(line.split("/", 1)[0]))
    return words


def place_words() -> set[str]:
    text = resources.files("docsan").joinpath("data/places.txt").read_text(encoding="utf-8")
    return _read_words(text)


_PLACES: set[str] | None = None


def _is_place(word: str) -> bool:
    global _PLACES
    if _PLACES is None:
        _PLACES = place_words()
    return fold_word(word) in _PLACES


def glossary_words() -> set[str]:
    text = resources.files("docsan").joinpath("data/technical-glossary.txt").read_text(encoding="utf-8")
    return _read_words(text)


class Lexicon:
    """Known words: dictionaries plus the technical glossary plus approved words."""

    def __init__(self, words: set[str]):
        self.words = words

    @classmethod
    def load(cls, directory: Path, extra: Iterable[str] = ()) -> Lexicon:
        directory = Path(directory)
        files = sorted(directory.glob("*.txt")) if directory.is_dir() else []
        if not files:
            raise FileNotFoundError(f"no word lists (*.txt) found in {directory}")
        words = glossary_words()
        for f in files:
            words |= _read_words(f.read_text(encoding="utf-8"))
        words |= {fold_word(w) for w in extra}
        return cls(words)

    def knows(self, word: str) -> bool:
        return fold_word(word) in self.words


def _needs_review(token: str, lexicon: Lexicon) -> bool:
    if lexicon.knows(token):
        return False
    letters = "".join(c for c in token if c.isalpha())
    if any(c.isdigit() for c in token):
        # Version numbers and short codes ("v2", "S3") pass; longer codes do not.
        return len(letters) >= 3 and not lexicon.knows(letters)
    return len(letters) >= 2


def _context(text: str, start: int, end: int, reach: int = 48) -> str:
    """About `reach` characters either side of a match, cut at whole words."""
    lo, hi = max(0, start - reach), min(len(text), end + reach)
    if lo > 0:
        space = text.find(" ", lo, start)
        lo = space + 1 if space != -1 else lo
    if hi < len(text):
        space = text.rfind(" ", end, hi)
        hi = space if space != -1 else hi
    snippet = " ".join(text[lo:hi].split())
    return ("…" if lo > 0 else "") + snippet + ("…" if hi < len(text) else "")


def unfamiliar_words(texts: Iterable[str], lexicon: Lexicon, skip: Iterable[str] = ()) -> list[ReviewItem]:
    """Words that need a decision. ``skip`` holds names already queued for review."""
    skipped = {fold_word(s) for s in skip}
    found: dict[str, ReviewItem] = {}
    for text in texts:
        cleaned = _LABEL.sub(" ", text)
        for m in _TOKEN.finditer(cleaned):
            token = _POSSESSIVE.sub("", m.group())
            if not token or token.isdigit() or fold_word(token) in skipped:
                continue
            parts = [token] if lexicon.knows(token) else [p for p in re.split(r"[\-'’.]", token) if p]
            for part in parts:
                if part.isdigit() or not _needs_review(part, lexicon):
                    continue
                key = fold_word(part)
                if key in found:
                    found[key].count += 1
                else:
                    found[key] = ReviewItem(part, _context(cleaned, m.start(), m.end()), kind="place" if _is_place(part) else "")
    return sorted(found.values(), key=lambda r: fold_word(r.word))
