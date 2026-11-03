"""Logos leave Word files whole, not just their lettering.

A picture is removed whole when it sits in a header or footer, when it
matches another picture removed from the file or a logo the profile
remembers, or when the owner removes it. The owner can keep any of them,
masked. Exporting remembers what was removed.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import imagekit
import leakmatrix as lm
import pytest
from harness import leaks
from test_docx_images import cached_ocr, image_rel, media, picture

from docsan import steps, verify
from docsan.image.logo import fingerprint
from docsan.pipeline import run_batch
from docsan.profile import ProfileError, add_logos, load_profile, parse_profile
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)


@pytest.fixture(autouse=True)
def fast_ocr(monkeypatch):
    monkeypatch.setattr(verify, "image_ocr", cached_ocr)
    imagekit.helper()


def logo(tmp: Path, scale: float = 1.0, pad: float = 10, name: str = "logo") -> bytes:
    """Two rings and the company's name beside them, like a brand mark."""
    s = scale
    items = [
        {"shape": "ellipse", "box": [pad, pad, 160 * s, 160 * s], "color": [0.95, 0.8, 0.1], "stroke": 14 * s},
        {"shape": "ellipse", "box": [pad + 30 * s, pad + 10 * s, 120 * s, 120 * s], "color": [0.2, 0.5, 0.9], "stroke": 10 * s},
        {"text": "Aurelan", "x": pad + 40 * s, "y": pad + 60 * s, "size": 34 * s, "color": [0.1, 0.5, 0.2]},
        {"text": "Group", "x": pad + 190 * s, "y": pad + 60 * s, "size": 30 * s, "color": [0.1, 0.5, 0.2]},
    ]
    path = tmp / f"{name}.png"
    imagekit.render({"output": str(path), "format": "png", "width": int(300 * s + 2 * pad),
                     "height": int(170 * s + 2 * pad), "items": items})
    return path.read_bytes()


def diagram(tmp: Path) -> bytes:
    items = []
    for i, label in enumerate(("Aurelan gateway", "Billing platform", "Data layer")):
        items.append({"shape": "rect", "box": [20 + 260 * i, 40, 230, 90], "color": [0.2, 0.3, 0.6], "stroke": 3})
        items.append({"text": label, "x": 36 + 260 * i, "y": 74, "size": 22})
    items.append({"text": "Integration report runs nightly", "x": 30, "y": 170, "size": 22})
    path = tmp / "diagram.png"
    imagekit.render({"output": str(path), "format": "png", "width": 820, "height": 220, "items": items})
    return path.read_bytes()


def word_file(tmp: Path, body: str, media_parts: dict[str, bytes], body_rels: list[str], header: str | None = None,
              header_rels: list[str] = ()) -> Path:
    parts: dict[str, str | bytes] = dict(media_parts)
    rels = list(body_rels)
    sect = ""
    if header is not None:
        parts["word/header1.xml"] = lm.DECL + f"<w:hdr {lm.NS}>{header}</w:hdr>"
        parts["word/_rels/header1.xml.rels"] = (
            lm.DECL + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(header_rels) + "</Relationships>"
        )
        rels.append(f'<Relationship Id="rIdH" Type="{lm.REL_NS}/header" Target="header1.xml"/>')
        sect = '<w:headerReference w:type="default" r:id="rIdH"/>'
    path = tmp / "in" / "logos.docx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(lm.zip_bytes(lm.package(body, parts=parts, doc_rels=rels, sect=sect)))
    return path


def branded(tmp: Path) -> Path:
    """The logo in the header, the same logo larger on the cover, and a diagram naming the company."""
    return word_file(
        tmp,
        lm.para(picture("rIdCover", cx=2 * 914400, cy=1143000)) + lm.text_para("Architecture overview")
        + lm.para(picture("rIdDiagram", cx=5 * 914400, cy=1340000)),
        {"word/media/header-logo.png": logo(tmp, 0.6, 4, "small"), "word/media/cover.png": logo(tmp, 1.6, 40, "large"),
         "word/media/diagram.png": diagram(tmp)},
        [image_rel("rIdCover", "media/cover.png"), image_rel("rIdDiagram", "media/diagram.png")],
        header=lm.para(picture("rIdLogo", cx=1143000, cy=685800)) + lm.text_para("Architecture overview"),
        header_rels=[image_rel("rIdLogo", "media/header-logo.png")],
    )


def run(src: Path, tmp: Path, images=None, out=True, profile=None):
    profile = profile or tmp / "profile.json"
    if not profile.exists():
        lm.write_profile(profile, {})
    return run_batch([src], profile, tmp / "out" if out else None, tmp / "private", lexicon=LEXICON,
                     release=out, images=images or {}).files[0]


def removed(result) -> dict[int, str | None]:
    return {i["index"]: i["removed"] for i in result.images}


def test_header_logo_and_its_copy_are_removed_whole(tmp_path):
    src = branded(tmp_path)
    first = run(src, tmp_path, out=False)
    # Pictures are numbered body first, then the header.
    assert removed(first) == {1: "same", 2: None, 3: "margin"}, first.images
    assert first.status == "needs_review" and first.reasons[-1] == "confirm the masks on one image"
    assert first.dropped["picture removed whole"] == 2
    diagram_info = next(i for i in first.images if i["removed"] is None)
    final = run(src, tmp_path, images={diagram_info["key"]: {"confirmed": diagram_info["digest"]}})
    assert final.status == "ready", final.reasons
    out = tmp_path / "out" / final.output
    assert leaks(out) == []
    assert len(media(out)) == 1, "only the diagram is left, masked"
    with zipfile.ZipFile(out) as zf:
        header = next(zf.read(n).decode() for n in zf.namelist() if n.startswith("word/header"))
    assert "<w:drawing" not in header and "Architecture overview" in header
    # Exporting remembers both removed pictures; they are one logo, so the prints are close.
    assert len(load_profile(tmp_path / "profile.json").logos) == 2


def test_a_remembered_logo_is_removed_from_the_next_file(tmp_path):
    data = logo(tmp_path, 1.2, 20)
    (tmp_path / "seen.png").write_bytes(logo(tmp_path, 0.7, 6, "seen"))
    seen = fingerprint(tmp_path / "seen.png")
    profile = lm.write_profile(tmp_path / "profile.json", {"logos": [{"print": seen.hex, "aspect": seen.aspect}]})
    src = word_file(tmp_path, lm.text_para("Architecture overview") + lm.para(picture("rIdImg")), {"word/media/image1.png": data},
                    [image_rel("rIdImg", "media/image1.png")])
    result = run(src, tmp_path, profile=profile)
    assert removed(result) == {1: "profile"}
    assert result.status == "ready", "a removed picture needs no confirmation"
    assert media(tmp_path / "out" / result.output) == {}


def test_the_owner_keeps_or_removes_any_picture(tmp_path):
    src = branded(tmp_path)
    first = run(src, tmp_path, out=False)
    keys = {i["index"]: i["key"] for i in first.images}
    # Keep the header logo and remove the diagram: the cover is no longer a copy of a removed picture.
    choice = {keys[3]: {"picture": "keep"}, keys[2]: {"picture": "remove"}}
    second = run(src, tmp_path, images=choice, out=False)
    assert removed(second) == {1: None, 2: "chosen", 3: None}
    assert second.reasons[-1] == "confirm the masks on 2 images"
    header = next(i for i in second.images if i["index"] == 3)
    assert header["masks"], "a kept logo still has its name masked"


def test_a_name_with_little_else_is_flagged_as_a_logo(tmp_path):
    src = word_file(tmp_path, lm.para(picture("rIdA")) + lm.para(picture("rIdB")),
                    {"word/media/a.png": logo(tmp_path), "word/media/b.png": diagram(tmp_path)},
                    [image_rel("rIdA", "media/a.png"), image_rel("rIdB", "media/b.png")])
    result = run(src, tmp_path, out=False)
    assert [(i["logo"], i["removed"]) for i in result.images] == [(True, None), (False, None)]


@pytest.mark.parametrize("step", steps.PICTURE_STEPS)
def test_picture_step_is_needed(step, tmp_path):
    src = branded(tmp_path)
    with steps.disabled(step):
        result = run(src, tmp_path, out=False)
    assert removed(result) != {1: "same", 2: None, 3: "margin"}


def test_profile_logos_are_checked_and_deduplicated(tmp_path):
    base = json.loads(lm.PROFILE.read_text(encoding="utf-8"))
    for bad in ([{"print": "xyz", "aspect": 1.0}], [{"print": "0" * 16}], [{"print": "0" * 16, "aspect": -1}], {}):
        with pytest.raises(ProfileError):
            parse_profile({**base, "logos": bad})
    profile = lm.write_profile(tmp_path / "profile.json", {})
    add_logos(profile, [("0123456789abcdef", 1.4271), ("0123456789abcdef", 1.427)])
    assert load_profile(profile).logos == [("0123456789abcdef", 1.427)]
    assert verify.load_rules(profile)[0], "the verifier reads a profile with logos"
