"""Pictures inside Word files: masked, confirmed by the owner, verified."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import imagekit
import imagematrix as im
import leakmatrix as lm
import pytest
from harness import leaks
from pngio import read_png

from docsan import verify
from docsan.pipeline import Hooks, run_batch
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
_ocr_cache: dict = {}


def cached_ocr(path, layout):
    key = (hashlib.sha256(path.read_bytes()).hexdigest(), layout)
    if key not in _ocr_cache:
        _ocr_cache[key] = verify.read_with_tesseract(path, layout)
    return _ocr_cache[key]


@pytest.fixture(autouse=True)
def fast_ocr(monkeypatch):
    monkeypatch.setattr(verify, "image_ocr", cached_ocr)


def picture(rid: str, cx: int = 3 * 914400, cy: int = 914400, crop_left: int = 0, link: bool = False) -> str:
    """A picture as Word writes it, with a name and description naming the company."""
    ref = f'r:link="{rid}"' if link else f'r:embed="{rid}"'
    crop = f'<a:srcRect l="{crop_left}"/>' if crop_left else ""
    inner = (
        '<pic:pic><pic:nvPicPr><pic:cNvPr id="1" name="aurelan-logo.png" descr="Aurelan logo"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip {ref}/>{crop}<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr/></pic:pic>'
    )
    return (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="1" name="Aurelan logo" descr="Aurelan logo"/>'
        f'<a:graphic><a:graphicData uri="{lm.PIC_URI}">{inner}</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'
    )


def rendered(tmp: Path, items: list[dict], size=(800, 200), fmt="png", orientation=1) -> tuple[bytes, list[dict]]:
    path = tmp / f"render.{fmt}"
    words = imagekit.render({"output": str(path), "format": fmt, "width": size[0], "height": size[1],
                             "orientation": orientation, "items": items})
    return path.read_bytes(), words


def word_file(tmp: Path, body: str, media: dict[str, bytes], rels: list[str]) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / "in" / "pictures.docx"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(lm.doc(body, parts=dict(media), doc_rels=rels))
    return path


def image_rel(rid: str, target: str, external: bool = False) -> str:
    mode = ' TargetMode="External"' if external else ""
    return f'<Relationship Id="{rid}" Type="{lm.REL_NS}/image" Target="{target}"{mode}/>'


def run(src: Path, tmp: Path, images=None, out=True, hooks=None):
    profile = tmp / "profile.json"
    if not profile.exists():
        lm.write_profile(profile, {})
    return run_batch([src], profile, tmp / "out" if out else None, tmp / "private", lexicon=LEXICON,
                     release=out, images=images or {}, hooks=hooks).files[0]


def confirm_all(src: Path, tmp: Path):
    first = run(src, tmp, out=False)
    assert first.status == "needs_review", first.reasons
    assert first.reasons[-1].startswith("confirm the masks on"), first.reasons
    choices = {info["key"]: {"confirmed": info["digest"]} for info in first.images}
    return first, run(src, tmp, images=choices)


def media(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist() if n.startswith("word/media/")}


def test_picture_is_masked_and_released(tmp_path):
    data, words = rendered(tmp_path, [im.line("Aurelan Group", 30, 40), im.line("Integration overview", 110)])
    src = word_file(tmp_path, lm.text_para("Architecture diagram") + lm.para(picture("rIdImg")),
                    {"word/media/image1.png": data}, [image_rel("rIdImg", "media/image1.png")])
    first, final = confirm_all(src, tmp_path)
    info = first.images[0]
    assert info["key"] == f"{src}#image-1" and info["masks"] and Path(info["source"]).exists()
    assert final.status == "ready", final.reasons
    out = tmp_path / "out" / final.output
    assert leaks(out) == []
    parts = media(out)
    assert list(parts) == ["word/media/image1.png"]
    px = read_png(parts["word/media/image1.png"])
    for word in ("Aurelan", "Group"):
        for w in (w for w in words if w["text"] == word):
            assert im.unmasked(px, w["box"], 0) == 0, word
    source = read_png(data)
    for w in (w for w in words if w["text"] in ("Integration", "overview")):
        assert im.changed(source, px, w["box"]) <= 0.02, w["text"]
    with zipfile.ZipFile(out) as zf:
        document = zf.read("word/document.xml").decode()
    assert 'name="Picture 1"' in document and "descr" not in document and "logo" not in document


def test_jpeg_picture_on_its_side(tmp_path):
    data, words = rendered(tmp_path, [im.line("Aurelan platform report", 40, 36)], size=(900, 140), fmt="jpeg", orientation=6)
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.jpeg": data},
                    [image_rel("rIdImg", "media/image1.jpeg")])
    _, final = confirm_all(src, tmp_path)
    assert final.status == "ready", final.reasons
    out = tmp_path / "out" / final.output
    parts = media(out)
    assert list(parts) == ["word/media/image1.jpg"]
    jpg = tmp_path / "out.jpg"
    jpg.write_bytes(parts["word/media/image1.jpg"])
    px = imagekit.pixels(jpg)
    assert (px.width, px.height) == (900, 140)
    for w in (w for w in words if w["text"] == "Aurelan"):
        assert im.unmasked(px, w["box"], 60) == 0


def test_cropped_part_never_reaches_the_output(tmp_path):
    """The document shows only the right half; the left half holds the name."""
    data, _ = rendered(tmp_path, [im.line("Aurelan", 80, 40, x=40), im.line("overview", 80, 40, x=460)])
    src = word_file(tmp_path, lm.para(picture("rIdImg", crop_left=50000)), {"word/media/image1.png": data},
                    [image_rel("rIdImg", "media/image1.png")])
    first, final = confirm_all(src, tmp_path)
    assert first.images[0]["width"] == 400 and not first.images[0]["masks"]
    assert final.status == "ready", final.reasons
    px = read_png(media(tmp_path / "out" / final.output)["word/media/image1.png"])
    assert (px.width, px.height) == (400, 200)


def test_words_in_pictures_join_the_review(tmp_path):
    data, _ = rendered(tmp_path, [im.line("Approved by Halvorsen", 60)])
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.png": data},
                    [image_rel("rIdImg", "media/image1.png")])
    result = run(src, tmp_path, out=False)
    assert result.status == "needs_review" and "Halvorsen" in [r.word for r in result.review]


def test_confirmation_is_per_picture(tmp_path):
    a, _ = rendered(tmp_path, [im.line("Aurelan Group", 30, 40)])
    b, _ = rendered(tmp_path, [im.line("Integration overview", 30, 40)])
    body = lm.para(picture("rIdA")) + lm.text_para("Notes") + lm.para(picture("rIdB"))
    src = word_file(tmp_path, body, {"word/media/a.png": a, "word/media/b.png": b},
                    [image_rel("rIdA", "media/a.png"), image_rel("rIdB", "media/b.png")])
    first = run(src, tmp_path, out=False)
    assert [i["index"] for i in first.images] == [1, 2]
    only_first = {first.images[0]["key"]: {"confirmed": first.images[0]["digest"]}}
    half = run(src, tmp_path, images=only_first, out=False)
    assert half.status == "needs_review" and "confirm the masks on one image" in half.reasons
    both = {i["key"]: {"confirmed": i["digest"]} for i in first.images}
    final = run(src, tmp_path, images=both)
    assert final.status == "ready", final.reasons
    assert sorted(media(tmp_path / "out" / final.output)) == ["word/media/image1.png", "word/media/image2.png"]


def test_picture_in_a_table(tmp_path):
    data, _ = rendered(tmp_path, [im.line("Integration overview", 60)])
    body = lm.table([[lm.text_para("Diagram"), lm.para(picture("rIdImg"))]])
    src = word_file(tmp_path, body, {"word/media/image1.png": data}, [image_rel("rIdImg", "media/image1.png")])
    _, final = confirm_all(src, tmp_path)
    assert final.status == "ready", final.reasons
    assert verify.verify_docx(tmp_path / "out" / final.output, lm.PROFILE).status == "pass"


def test_unreadable_vector_picture_blocks_or_becomes_a_placeholder(tmp_path):
    emf = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 32  # an EMF header and nothing to draw
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.emf": emf}, [image_rel("rIdImg", "media/image1.emf")])
    blocked = run(src, tmp_path, out=False)
    assert blocked.status == "blocked", blocked.reasons
    profile = lm.write_profile(tmp_path / "profile.json", {})
    removed = run_batch([src], profile, tmp_path / "out", tmp_path / "private", lexicon=LEXICON, remove_images=True).files[0]
    assert removed.status == "ready", removed.reasons


def test_linked_picture_becomes_a_placeholder(tmp_path):
    src = word_file(tmp_path, lm.para(picture("rIdImg", link=True)), {},
                    [image_rel("rIdImg", "https://aurelan.lu/logo.png", external=True)])
    result = run(src, tmp_path)
    assert result.status == "ready", result.reasons
    out = tmp_path / "out" / result.output
    assert leaks(out) == [] and media(out) == {}
    assert "[IMAGE-REMOVED]" in "".join(lm_text(out))


def lm_text(path: Path) -> list[str]:
    from harness import output_paragraphs

    return output_paragraphs(path)


def test_verifier_blocks_an_unmasked_picture(tmp_path):
    data, _ = rendered(tmp_path, [im.line("Aurelan Group", 30, 40), im.line("Integration overview", 110)])
    src = word_file(tmp_path, lm.para(picture("rIdImg")), {"word/media/image1.png": data},
                    [image_rel("rIdImg", "media/image1.png")])
    first = run(src, tmp_path, out=False)
    choices = {i["key"]: {"confirmed": i["digest"]} for i in first.images}
    from pngio import without_alpha

    from docsan.image.encode import write_png

    px = without_alpha(read_png(data))
    clean = write_png(px.data, px.width, px.height, False)

    def swap(staged: Path) -> None:
        with zipfile.ZipFile(staged) as zf:
            parts = {n: zf.read(n) for n in zf.namelist()}
        parts["word/media/image1.png"] = clean
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for n, d in parts.items():
                zf.writestr(zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0)), d, compress_type=zipfile.ZIP_DEFLATED)

    result = run(src, tmp_path, images=choices, hooks=Hooks(after_stage=swap))
    assert result.status == "blocked" and "verifier found references" in result.reasons
    assert result.images[0]["found"], "the app needs to know where the verifier read the name"


def test_page_preview_shows_the_masks_before_confirmation(tmp_path):
    """The app shows the sanitized pages while the owner reviews: a private
    description with the proposed masks burned into its pictures, never in
    the shareable folder."""
    import json

    data, words = rendered(tmp_path, [im.line("Aurelan Group", 30, 40), im.line("Integration overview", 110)])
    src = word_file(tmp_path, lm.text_para("Architecture diagram") + lm.para(picture("rIdImg")),
                    {"word/media/image1.png": data}, [image_rel("rIdImg", "media/image1.png")])
    first = run(src, tmp_path, out=False)
    assert first.status == "needs_review" and first.pages and first.pages_original
    pages = Path(first.pages)
    assert pages.exists() and (tmp_path / "private") in pages.parents
    model = json.loads(pages.read_text())
    blocks = model["sections"][0]["blocks"]
    assert any(r["text"] == "Architecture diagram" for b in blocks for r in b.get("runs", []))
    shown = next(d for b in blocks for d in b.get("drawings", []) if d["kind"] == "image")
    px = read_png((pages.parent / shown["file"]).read_bytes())
    for w in (w for w in words if w["text"] in ("Aurelan", "Group")):
        assert im.unmasked(px, w["box"], 0) == 0, w["text"]
