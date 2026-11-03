"""Image fixtures through the whole pipeline, plus the owner's confirmation."""

from __future__ import annotations

import hashlib

import imagekit
import imagematrix as im
import leakmatrix as lm
import pytest

from docsan import steps, verify
from docsan.image import text as image_text
from docsan.match import Matcher
from docsan.pipeline import Hooks, run_batch
from docsan.profile import Profile, Term
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
FIXTURES = im.fixtures()
_ocr_cache: dict[tuple[str, int], tuple] = {}
# OS OCR models can read a font without needing the same fallback pass. These
# three engine paths are proven with controlled native response observations;
# every real-image fixture still runs through the complete pipeline above.
OCR_OBSERVATION_STEPS = ("ocr-rotations", "ocr-uncorrected", "ocr-alternates")


def cached_ocr(path, layout):
    key = (hashlib.sha256(path.read_bytes()).hexdigest(), layout)
    if key not in _ocr_cache:
        _ocr_cache[key] = verify.read_with_tesseract(path, layout)
    return _ocr_cache[key]


@pytest.fixture(autouse=True)
def fast_ocr(monkeypatch):
    monkeypatch.setattr(verify, "image_ocr", cached_ocr)
    imagekit.helper()


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f.name for f in FIXTURES])
def test_fixture(fixture, tmp_path):
    assert im.check_image(fixture, tmp_path, LEXICON) == []


@pytest.mark.parametrize("step", [s for s in steps.IMAGE_STEPS if s not in OCR_OBSERVATION_STEPS])
def test_image_step_is_needed(step, tmp_path):
    with steps.disabled(step):
        for i, fixture in enumerate(FIXTURES):
            if im.check_image(fixture, tmp_path / str(i), LEXICON):
                return
    pytest.fail(f"no image fixture fails with step {step!r} disabled")


@pytest.mark.parametrize("step", OCR_OBSERVATION_STEPS)
def test_ocr_step_is_needed_for_controlled_observations(step, tmp_path, monkeypatch):
    """Only the selected native observation carries the planted reference.

    Keep this negative check independent of how an OS model reads a particular
    font, while exercising the real request builder, response parser and matcher.
    """
    source = tmp_path / "native-observations.png"
    requests = []

    def reading(value):
        return {"text": value, "confidence": .9,
                "chars": [[20 + 10*i, 30, 10, 20] for i in range(len(value))]}

    def observation(rotation, correction, candidates):
        return {"rotation": rotation, "correction": correction,
                "lines": [{"box": [20, 30, 140, 20], "candidates": candidates}]}

    def native_response(request):
        assert request["command"] == "ocr" and request["input"] == str(source)
        requests.append(request)
        wrong, reference = reading("Integration"), reading("Aurelan")
        corrected = [wrong, reference] if step == "ocr-alternates" else [wrong]
        uncorrected = [reference] if step == "ocr-uncorrected" else [wrong]
        passes = [observation(0, True, corrected), observation(0, False, uncorrected)]
        for rotation in request["rotations"]:
            candidates = [reference] if step == "ocr-rotations" and rotation == 90 else [wrong]
            passes.append(observation(rotation, True, candidates))
        return {"ok": True, "width": 800, "height": 160, "frames": 1,
                "orientation": 1, "alpha": False, "passes": passes}

    monkeypatch.setattr(image_text, "call", native_response)
    matcher = Matcher(Profile("Example", "v1", [Term("Aurelan", "CLIENT")]), set())
    baseline = image_text.read_text(source)
    masks, _ = image_text.plan_masks(baseline, matcher)
    assert [mask.label for mask in masks] == ["CLIENT"]
    assert masks[0].box == (20, 30, 70, 20)
    with steps.disabled(step):
        reduced = image_text.read_text(source)
        assert image_text.plan_masks(reduced, matcher)[0] == []
    if step == "ocr-rotations":
        assert requests[0]["rotations"] == [90, 270] and requests[1]["rotations"] == []


def _first(tmp_path, fixture, images=None, **kw):
    src, _, _ = im.build(fixture, tmp_path / "in")
    profile = lm.write_profile(tmp_path / "profile.json", {})
    batch = run_batch([src], profile, kw.pop("out", None), tmp_path / "private", lexicon=LEXICON,
                      release=kw.pop("release", False), images=images or {}, **kw)
    return src, profile, batch.files[0]


def test_confirmation_is_bound_to_the_masks(tmp_path):
    fixture = FIXTURES[0]
    src, profile, first = _first(tmp_path, fixture)
    digest = first.image["digest"]
    # Adding a mask after confirming asks again.
    changed = {str(src): {"masks": [[0, 0, 10, 10]], "confirmed": digest}}
    again = run_batch([src], profile, None, tmp_path / "private", lexicon=LEXICON, release=False, images=changed).files[0]
    assert again.status == "needs_review" and "confirm the masks on this image" in again.reasons
    wrong = {str(src): {"confirmed": "0" * 32}}
    assert run_batch([src], profile, None, tmp_path / "private", lexicon=LEXICON, release=False, images=wrong).files[0].status == "needs_review"


def test_confirmation_is_bound_to_the_image(tmp_path):
    fixture = FIXTURES[0]
    src, profile, first = _first(tmp_path, fixture)
    src.write_bytes(src.read_bytes() + b"")  # same bytes: same digest
    data = bytearray(src.read_bytes())
    other = im.ImageFixture("other", [im.line("Integration overview", 30)], size=fixture.size)
    im.build(other, tmp_path / "other")
    moved = tmp_path / "other" / other.file
    choice = {str(moved): {"confirmed": first.image["digest"]}}
    result = run_batch([moved], profile, None, tmp_path / "private", lexicon=LEXICON, release=False, images=choice).files[0]
    assert result.status == "needs_review" and bytes(data) != moved.read_bytes()


def test_invalid_owner_mask_blocks(tmp_path):
    _first(tmp_path, FIXTURES[0], images=None)
    src = tmp_path / "in" / FIXTURES[0].file
    bad = {str(src): {"masks": [["x", 0, 1, 1]]}}
    out = run_batch([src], lm.write_profile(tmp_path / "p.json", {}), None, tmp_path / "private",
                    lexicon=LEXICON, release=False, images=bad).files[0]
    assert out.status == "blocked"


def test_verifier_blocks_a_tampered_image(tmp_path):
    """The verifier reads the staged file itself: an unmasked copy is refused."""
    fixture = FIXTURES[0]
    src, profile, first = _first(tmp_path, fixture)
    original = src.read_bytes()
    from pngio import read_png, without_alpha

    from docsan.image.encode import write_png

    px = without_alpha(read_png(original))
    unmasked = write_png(px.data, px.width, px.height, False)
    hooks = Hooks(after_stage=lambda staged: staged.write_bytes(unmasked))
    choice = {str(src): {"confirmed": first.image["digest"]}}
    result = run_batch([src], profile, tmp_path / "out", tmp_path / "private", lexicon=LEXICON,
                       images=choice, hooks=hooks).files[0]
    assert result.status == "blocked"
    assert "verifier found references" in result.reasons
    assert result.image["found"], "the app needs to know where the verifier found text"
    assert not list((tmp_path / "out").glob("image-*"))


def test_report_holds_no_recognized_text(tmp_path):
    _first(tmp_path, FIXTURES[0])
    report = next((tmp_path / "private" / "reports").glob("*.json")).read_text().lower()
    assert "marchetti" not in report and "aurelan" not in report
