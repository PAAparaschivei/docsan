"""The image verifier on its own: crafted outputs it must pass, flag or refuse."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import imagekit
import imagematrix as im
import leakmatrix as lm
import pytest
from docsan import verify
from docsan.image.encode import clean_jpeg, write_png
from pngio import read_png, without_alpha
from pngio import write_png as write_test_png


@pytest.fixture(autouse=True)
def current_vision_helper():
    imagekit.helper()


def _render(tmp: Path, items: list[dict], size=(900, 120), fmt="png") -> bytes:
    path = tmp / f"render.{fmt}"
    imagekit.render({"output": str(path), "format": fmt, "width": size[0], "height": size[1], "items": items})
    return path.read_bytes()


def released_png(tmp: Path, items: list[dict], size=(900, 120)) -> bytes:
    px = without_alpha(read_png(_render(tmp, items, size)))
    return write_png(px.data, px.width, px.height, False)


def released_jpeg(tmp: Path, items: list[dict], size=(900, 120)) -> bytes:
    return clean_jpeg(_render(tmp, items, size, "jpeg"))


def png_chunk(data: bytes, kind: bytes, body: bytes) -> bytes:
    at = data.rfind(b"IEND") - 4
    return data[:at] + struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body)) + data[at:]


def hidden_colour(tmp: Path) -> bytes:
    px = without_alpha(read_png(_render(tmp, [im.line("Integration overview", 30)])))
    rgba = bytearray()
    for i in range(0, len(px.data), 3):
        rgba += px.data[i : i + 3] + (b"\x00" if i < len(px.data) // 2 else b"\xff")
    return write_png(bytes(rgba), px.width, px.height, True)


def damaged_matrix(tmp):
    import random
    rng=random.Random(19)
    items=[{"shape":"rect","box":[90+x*2,80+y*2,2,2],"color":[.55,.55,.55]}
           for y in range(19) for x in range(19) if rng.random()<.48]
    return released_png(tmp,items,size=(400,240))


CLEAN = [im.line("Integration overview", 30)]


def with_comment(jpeg: bytes) -> bytes:
    return jpeg[:2] + b"\xff\xfe\x00\x07hello" + jpeg[2:]


@dataclass
class Case:
    name: str
    build: object  # (tmp) -> bytes
    expect: str
    ocr: str = "real"  # real, blind or wrong-size
    suffix: str = ".png"


CASES = [
    Case("unreadable-matrix", damaged_matrix, "findings"),
    Case("clean-png", lambda t: released_png(t, CLEAN), "pass"),
    Case("name", lambda t: released_png(t, [im.line("Aurelan approved it", 30)]), "findings"),
    Case("phone", lambda t: released_png(t, [im.line("Call +352 26 12 34 56 now", 30)]), "findings"),
    Case("french-clean", lambda t: released_png(t, [im.line("Contrôle d'accès et sécurité des données", 30)]), "pass"),
    Case("french-name", lambda t: released_png(t, [im.line("Réunion de projet avec Aurélan à Genève", 30)]), "findings"),
    Case("kept-system-name", lambda t: released_png(t, [im.line("Vireo platform runs nightly", 30)]), "pass"),
    Case("exception", lambda t: released_png(t, [im.line("PATCH /orders updates an order", 30, font="Menlo")]), "pass"),
    # OCR gets letter case wrong in drawn lettering: "AGL" read as "Agl" still counts; "agl" would be a word.
    Case("other-letter-case", lambda t: released_png(t, [im.line("Ticker Agl closed", 30)]), "findings"),
    Case("text-chunk", lambda t: png_chunk(released_png(t, CLEAN), b"tEXt", b"Comment\x00overview"), "findings"),
    Case("trailing-bytes", lambda t: released_png(t, CLEAN) + b"payload", "findings"),
    Case("filtered-rows", lambda t: write_test_png(without_alpha(read_png(_render(t, CLEAN))), row_filter=1), "findings"),
    Case("hidden-colour", hidden_colour, "findings"),
    Case("clean-jpeg", lambda t: released_jpeg(t, CLEAN), "pass", suffix=".jpg"),
    Case("jpeg-application-data", lambda t: _render(t, CLEAN, fmt="jpeg"), "findings", suffix=".jpg"),
    Case("jpeg-comment", lambda t: with_comment(released_jpeg(t, CLEAN)), "findings", suffix=".jpg"),
    Case("blind-ocr", lambda t: released_png(t, [im.line("Aurelan approved it", 30)]), "could_not_inspect", ocr="blind"),
    Case("wrong-size-ocr", lambda t: released_png(t, CLEAN), "could_not_inspect", ocr="wrong-size"),
    Case("not-an-image", lambda t: b"plain text", "could_not_inspect"),
]


def run_case(case: Case, tmp: Path, monkeypatch) -> str:
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"{case.name}{case.suffix}"
    path.write_bytes(case.build(tmp))
    real = verify.read_with_tesseract
    if case.ocr == "blind":
        monkeypatch.setattr(verify, "image_ocr", lambda p, layout: (real(p, layout)[0], []))
    elif case.ocr == "wrong-size":
        monkeypatch.setattr(verify, "image_ocr", lambda p, layout: ((1, 1), real(p, layout)[1]))
    else:
        monkeypatch.setattr(verify, "image_ocr", real)
    return verify.verify_image(path, lm.PROFILE).status


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case, tmp_path, monkeypatch):
    assert run_case(case, tmp_path, monkeypatch) == case.expect


@pytest.mark.parametrize("step", verify.IMAGE_VERIFIER_STEPS)
def test_image_verifier_step_is_needed(step, tmp_path, monkeypatch):
    with verify.step_disabled(step):
        for i, case in enumerate(CASES):
            if run_case(case, tmp_path / str(i), monkeypatch) != case.expect:
                return
    pytest.fail(f"no image case changes with verifier step {step!r} disabled")


def test_findings_carry_regions(tmp_path):
    path = tmp_path / "name.png"
    path.write_bytes(released_png(tmp_path, [im.line("Aurelan approved it", 30)]))
    result = verify.verify_image(path, lm.PROFILE)
    assert result.status == "findings" and result.regions
    x, y, w, _ = result.regions[0]
    assert 20 <= x <= 60 and 15 <= y <= 50 and w > 100


def test_system_names_are_checked_when_redaction_is_on(tmp_path):
    path = tmp_path / "out.png"
    path.write_bytes(released_png(tmp_path, [im.line("Vireo platform runs nightly", 30)]))
    profile = lm.write_profile(tmp_path / "profile.json", {"redact_system_names": True})
    assert verify.verify_image(path, profile).status == "findings"
