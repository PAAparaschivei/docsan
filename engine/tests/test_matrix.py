"""End-to-end leak matrix: every fixture is released clean, needs review or is blocked."""

import leakmatrix as lm
import pytest
from harness import check, output_paragraphs, run_fixture

from docsan.profile import add_classifications
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
FIXTURES = lm.fixtures()


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f.name for f in FIXTURES])
def test_fixture(fixture, tmp_path):
    assert check(fixture, tmp_path, LEXICON) == []


def test_matrix_covers_every_outcome():
    outcomes = {f.expect for f in FIXTURES}
    assert outcomes == {"ready", "needs_review", "blocked"}


@pytest.mark.parametrize(
    ("name", "decisions", "expected"),
    [
        ("unknown-word", ({"Halden": "SITE"}, []), "The [SITE-1] data centre is ready."),
        ("file-like-name", ({}, ["setup.py"]), "Run setup.py before the release."),
    ],
)
def test_review_then_release(tmp_path, name, decisions, expected):
    fixture = next(f for f in FIXTURES if f.name == name)
    first, _, profile = run_fixture(fixture, tmp_path, LEXICON)
    assert first.status == "needs_review"
    add_classifications(profile, *decisions)
    second, _, _ = run_fixture(fixture, tmp_path, LEXICON)
    assert second.status == "ready", second.reasons
    assert expected in output_paragraphs(tmp_path / "out" / second.output)
