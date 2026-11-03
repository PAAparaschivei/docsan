"""PDF fixtures through the whole pipeline, plus the owner's confirmation of each page."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import imagekit
import leakmatrix as lm
import pdfmatrix as pm
import pytest
from docsan import steps, verify
from docsan.pdf.plan import OutLine
from docsan.pdf.write import OutPage, pdf_bytes, png_rows
from docsan.pipeline import Hooks, run_batch
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
FIXTURES = pm.fixtures()
BY_NAME = {f.name: f for f in FIXTURES}
_ocr_cache: dict[tuple[str, int], tuple] = {}


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
    assert pm.check_pdf(fixture, tmp_path, LEXICON) == []


def test_matrix_covers_every_outcome():
    assert {f.expect for f in FIXTURES} == {"ready", "needs_review", "blocked"}


# The fixture each step exists for is tried first.
PROVES = {
    "pdf-text-layer": "light-small-print",
    "pdf-sensitive-fields": "screenshot-fields",
    "pdf-visual-regions": "brand-adjacent-mark",
    "pdf-sharp-ocr": "tiny-labels-in-picture",
    "pdf-visible-text": "hidden-text",
    "pdf-check-text-layer": "mismatched-text-layer",
    "pdf-join-lines": "split-across-lines",
}


@pytest.mark.parametrize("step", steps.PDF_STEPS)
def test_pdf_step_is_needed(step, tmp_path):
    ordered = sorted(FIXTURES, key=lambda f: f.name != PROVES.get(step))
    with steps.disabled(step):
        for i, fixture in enumerate(ordered):
            if pm.check_pdf(fixture, tmp_path / str(i), LEXICON):
                return
    pytest.fail(f"no PDF fixture fails with step {step!r} disabled")


def _first(tmp_path, name="mixed-pages"):
    fixture = BY_NAME[name]
    src, _ = pm.build(fixture, tmp_path / "in")
    profile, first = pm.run(fixture, tmp_path, LEXICON)
    return src, profile, first


def _again(src, profile, tmp_path, images, release=False, hooks=None):
    return run_batch([src], profile, tmp_path / "out" if release else None, tmp_path / "private", lexicon=LEXICON,
                     release=release, images=images, hooks=hooks).files[0]


def test_each_page_is_confirmed_on_its_own(tmp_path):
    src, profile, first = _first(tmp_path)
    assert first.kind == "pdf" and [i["index"] for i in first.images] == [1, 2]
    assert first.reasons == ["confirm the masks on 2 pages"]
    one, two = first.images
    half = _again(src, profile, tmp_path, {one["key"]: {"confirmed": one["digest"]}})
    assert half.status == "needs_review" and half.reasons == ["confirm the masks on one page"]
    # A page's digest does not confirm another page.
    swapped = _again(src, profile, tmp_path, {one["key"]: {"confirmed": two["digest"]}, two["key"]: {"confirmed": one["digest"]}})
    assert swapped.reasons == ["confirm the masks on 2 pages"]
    # Adding a mask after confirming asks again, for that page only.
    changed = {one["key"]: {"confirmed": one["digest"], "masks": [[10, 10, 40, 40]]}, two["key"]: {"confirmed": two["digest"]}}
    again = _again(src, profile, tmp_path, changed)
    assert again.reasons == ["confirm the masks on one page"]
    assert [m["source"] for m in again.images[0]["masks"]].count("owner") == 1


def test_confirmation_is_bound_to_the_file(tmp_path):
    src, profile, first = _first(tmp_path)
    choices = {i["key"]: {"confirmed": i["digest"]} for i in first.images}
    src.write_bytes(src.read_bytes() + b"\n% another revision\n")
    assert _again(src, profile, tmp_path, choices).status != "ready"


def _unmasked(info: dict, lines: list) -> OutPage:
    """A page as drawn, before any mask: what a broken writer might release."""
    width, height, colors, rows = png_rows(Path(info["source"]).read_bytes())
    return OutPage(rows, width, height, (595, 842), lines, "flate", colors)


def test_the_verifier_reads_the_released_file(tmp_path):
    """A staged file whose text layer is replaced is refused, whatever the plan said."""
    src, profile, first = _first(tmp_path)
    choices = {i["key"]: {"confirmed": i["digest"]} for i in first.images}

    lines = [OutLine("Aurelan approved the plan", (100, 100, 800, 40))]

    def tamper(staged):
        """Keep the masked pictures; write a name into the text layer."""
        pages = []
        for (suffix, picture), info in zip(pm.page_pictures(staged.read_bytes()), first.images):
            if suffix == ".png":
                width, height, colors, rows = png_rows(picture)
                pages.append(OutPage(rows, width, height, (595, 842), lines, "flate", colors))
            else:
                pages.append(OutPage(picture, info["width"], info["height"], (595, 842), lines))
        staged.write_bytes(pdf_bytes(pages))

    result = _again(src, profile, tmp_path, choices, release=True, hooks=Hooks(after_stage=tamper))
    assert result.status == "blocked" and "verifier found references" in result.reasons
    assert any(r.startswith("page 1 text:") for r in result.reasons), result.reasons
    assert not list((tmp_path / "out").glob("document-*"))


def test_unmasked_pixels_are_found_on_their_page(tmp_path):
    """A staged file whose pages are the unmasked originals is refused, and the
    app is told where on which page the verifier read a name."""
    src, profile, first = _first(tmp_path)
    choices = {i["key"]: {"confirmed": i["digest"]} for i in first.images}
    originals = [_unmasked(info, []) for info in first.images]
    result = _again(src, profile, tmp_path, choices, release=True,
                    hooks=Hooks(after_stage=lambda staged: staged.write_bytes(pdf_bytes(originals))))
    assert result.status == "blocked" and "verifier found references" in result.reasons
    assert any(i["found"] for i in result.images), "the app needs to know where the verifier read a name"


def test_report_holds_no_text(tmp_path):
    _first(tmp_path)
    report = next((tmp_path / "private" / "reports").glob("*.json")).read_text().lower()
    assert "marchetti" not in report and "aurelan" not in report and "platform" not in report


def test_drawn_pages_are_reused_then_deleted(tmp_path):
    src, profile, _ = _first(tmp_path)
    folder = tmp_path / "private" / "pdf"
    (drawn,) = list(folder.iterdir())
    saved = (drawn / "read.json").stat().st_mtime_ns
    cache = sorted(p.name for p in (tmp_path / "private" / "cache").iterdir())
    assert cache, "the helper's readings are kept for the next check"
    _again(src, profile, tmp_path, {})
    assert (drawn / "read.json").stat().st_mtime_ns == saved
    assert sorted(p.name for p in (tmp_path / "private" / "cache").iterdir()) == cache
    # A batch without the file deletes its pages and readings.
    (tmp_path / "other").mkdir()
    other = lm.build(next(f for f in lm.fixtures() if f.name == "body"), tmp_path / "other")
    run_batch([other], profile, None, tmp_path / "private", lexicon=LEXICON, release=False)
    assert not list(folder.iterdir()) and not list((tmp_path / "private" / "cache").iterdir())


def test_pages_are_described_for_the_app(tmp_path):
    _, _, first = _first(tmp_path)
    page = first.images[0]
    assert page["key"].endswith("#page-1") and page["source"].endswith(".png") and page["text_layer"] is True
    assert first.images[1]["text_layer"] is False  # a scanned page
    assert [line["kind"] for line in first.preview_original if line["kind"] == "page"] == ["page", "page"]
    assert any("[CLIENT]" in v for v in page["visible"])
    json.dumps(first.images)  # the bridge sends them as JSON


def test_second_look_masks_what_the_masked_page_still_shows(tmp_path, monkeypatch):
    """OCR misreads tiny lettering differently in each drawing, so the page with
    its masks burned in is read again, enlarged, and what it still shows is masked."""
    import hashlib as _hashlib

    from docsan.image.text import Line, Reading, _overlap
    from docsan.match import Labeler, Matcher
    from docsan.pdf import plan as planner
    from docsan.pdf.read import read_pdf
    from docsan.profile import load_profile
    from docsan.review import glossary_words

    src, _ = pm.build(BY_NAME["kept-system-name"], tmp_path / "in")
    data = src.read_bytes()
    page = read_pdf(src, data, tmp_path / "pages", _hashlib.sha256(data).hexdigest()).pages[0]
    matcher = Matcher(load_profile(lm.write_profile(tmp_path / "profile.json", {})), glossary_words())
    missed = (300.0, 900.0, 140.0, 24.0)
    real, zooms = planner.read_text, []

    def reading(path, crop=None, scale=1.0, zoom=1.0):
        zooms.append(zoom)
        text = real(path, crop, scale, zoom)
        if zoom > 1:  # only the second look reads this name
            chars = [(missed[0] + 20 * i, missed[1], 20.0, missed[3]) for i in range(7)]
            text.lines.append(Line(missed, [Reading("Aurelan", 1.0, chars)], 0, True))
        return text

    monkeypatch.setattr(planner, "read_text", reading)
    plan = planner.plan_page(page, "0" * 64, matcher, Labeler(), {})
    assert planner.LOOK_ZOOM in zooms
    assert any(m.label == "CLIENT" and _overlap(m.box, missed) > 0.5 for m in plan.masks)
