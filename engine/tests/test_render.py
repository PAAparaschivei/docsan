"""Released files open in a real office suite (LibreOffice) and keep their text."""

from __future__ import annotations

import html
import re
import subprocess
import zipfile
from pathlib import Path

import leakmatrix as lm
import pytest
from harness import run_fixture

from docsan.review import Lexicon

SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
NAMES = ["body", "structure", "footnote", "technical-terms", "table-cell", "text-box", "image-removed"]

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not SOFFICE.exists(), reason="LibreOffice is not installed"),
]


def convert(src: Path, fmt: str, tmp: Path) -> Path:
    subprocess.run(
        [str(SOFFICE), "--headless", "--norestore", f"-env:UserInstallation=file://{tmp / 'lo-profile'}",
         "--convert-to", fmt, "--outdir", str(tmp / "converted"), str(src)],
        check=True, capture_output=True, timeout=180,
    )
    return tmp / "converted" / (src.stem + "." + fmt.split(":")[0])


@pytest.mark.parametrize("name", NAMES)
def test_output_opens_in_libreoffice(name, tmp_path):
    fixture = next(f for f in lm.fixtures() if f.name == name)
    result, _, _ = run_fixture(fixture, tmp_path, Lexicon.load(lm.LEXICON))
    assert result.status == "ready"
    out = tmp_path / "out" / result.output
    pdf = convert(out, "pdf", tmp_path)
    assert pdf.exists() and pdf.stat().st_size > 1000
    # LibreOffice's own format, which includes text in text boxes (its text export skips shapes).
    with zipfile.ZipFile(convert(out, "odt", tmp_path)) as zf:
        content = zf.read("content.xml").decode("utf-8")
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<text:s(?: [^>]*)?/>", " ", content)))
    flat = " ".join(text.split())
    for expected in fixture.must_contain:
        assert " ".join(expected.split()) in flat, expected
