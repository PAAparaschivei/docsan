"""Mutation tests: switching off any one step must make a check fail.

This proves every normalization, detection and removal step is exercised by
at least one fixture. A step that can be removed without any test noticing is
either dead or untested.
"""

from __future__ import annotations

import leakmatrix as lm
import pytest
from harness import check, fast_extractor
from test_verifier import CASES, run_case

from docsan import steps, verify
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
FIXTURES = lm.fixtures()


@pytest.mark.parametrize("step", steps.SANITIZER_STEPS)
def test_sanitizer_step_is_needed(step, tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "extractor", fast_extractor)
    with steps.disabled(step):
        for i, fixture in enumerate(FIXTURES):
            if check(fixture, tmp_path / str(i), LEXICON, oracle=False):
                return
    pytest.fail(f"no fixture fails with sanitizer step {step!r} disabled")


@pytest.mark.parametrize("step", verify.VERIFIER_STEPS)
def test_verifier_step_is_needed(step, tmp_path, monkeypatch):
    # The XML views are tested without the second extractor, which would
    # otherwise mask them; the extractor and self-test are tested as built.
    baseline = [] if step in ("second-extractor", "self-test") else ["second-extractor"]
    cases = [c for c in CASES if not (baseline and c.extractor != "real")]
    for off in baseline:
        verify._off.add(off)
    try:
        assert all(run_case(c, tmp_path, monkeypatch) == c.expect for c in cases), "baseline must pass"
        with verify.step_disabled(step):
            if any(run_case(c, tmp_path, monkeypatch) != c.expect for c in cases):
                return
    finally:
        for off in baseline:
            verify._off.discard(off)
    pytest.fail(f"no verifier case fails with step {step!r} disabled")
