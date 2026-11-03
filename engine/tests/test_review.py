"""The unfamiliar-word review: what it asks and how it hints at each word."""

from __future__ import annotations

import leakmatrix as lm

from docsan.review import Lexicon, unfamiliar_words


def test_places_are_hinted_and_calendar_words_are_known():
    lexicon = Lexicon.load(lm.LEXICON)
    items = {i.word: i.kind for i in unfamiliar_words(["Halden approved the plan in Luxembourg on Monday in November."], lexicon)}
    assert items == {"Halden": "", "Luxembourg": "place"}


def test_first_names_that_are_places_keep_the_names_default():
    lexicon = Lexicon.load(lm.LEXICON)
    items = {i.word: i.kind for i in unfamiliar_words(["Jordan and Nancy approved it."], lexicon)}
    assert items == {"Jordan": "", "Nancy": ""}
