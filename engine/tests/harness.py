"""Shared test helpers, including a third, test-only leak check.

The leak oracle below is deliberately crude and separate from both the
sanitizer and the verifier: it decompresses every part, strips case, accents,
look-alike letters and every non-alphanumeric character, then searches for the
planted secrets. A released file must pass it as well as the verifier.
"""

from __future__ import annotations

import html
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import leakmatrix as lm

from docsan.pipeline import BatchResult, FileResult, run_batch
from docsan.review import Lexicon

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_CONFUSE = str.maketrans("аеорсухіјѕАЕОРСУХІЈЅαοιρΑΟΙΡ", "aeopcyxijsAEOPCYXIJSaoipAOIP")


def skeleton(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.translate(_CONFUSE))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return "".join(c for c in text if c.isalnum())


def textutil(path: Path) -> str:
    proc = subprocess.run(
        ["/usr/bin/textutil", "-convert", "txt", "-encoding", "UTF-8", "-stdout", str(path)],
        capture_output=True,
        check=True,
    )
    return proc.stdout.decode("utf-8")


def leaks(path: Path) -> list[str]:
    found: list[str] = []
    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            data = zf.read(name)
            # Pictures are binary: look for the secrets in their raw bytes.
            text = data.decode("latin-1") if name.startswith("word/media/") else html.unescape(data.decode("utf-8"))
            sk = skeleton(text)
            found += [f"{name}: {s}" for s in lm.SECRETS if s in sk]
    text = skeleton(textutil(path))
    found += [f"textutil: {s}" for s in (*lm.SECRETS, "26123456", "1234567") if s in text]
    found += [f"filename: {s}" for s in lm.SECRETS if s in skeleton(path.name)]
    return found


def output_paragraphs(path: Path) -> list[str]:
    """Paragraph texts of the document, then of each header and footer."""
    out: list[list[str]] = []

    def walk(el: ET.Element, current: list[str] | None) -> None:
        for child in el:
            if child.tag in (_W + "pPr", _W + "rPr"):
                continue  # tab stops are not tabs
            if child.tag == _W + "p":
                entry: list[str] = []
                out.append(entry)
                walk(child, entry)
                continue
            if current is not None:
                if child.tag == _W + "t":
                    current.append(child.text or "")
                elif child.tag in (_W + "tab", _W + "ptab"):
                    current.append("\t")
                elif child.tag == _W + "br":
                    current.append("\n")
            walk(child, current)

    with zipfile.ZipFile(path) as zf:
        parts = sorted((n for n in zf.namelist() if re.fullmatch(r"word/(header|footer)\d+\.xml", n)), key=lambda n: int(re.search(r"\d+", n).group()))
        for name in ["word/document.xml", *parts]:
            walk(ET.fromstring(zf.read(name)), None)
    return ["".join(e) for e in out]


def fast_extractor(path: Path) -> str:
    """Stand-in for textutil in slow loops; reads the document text directly."""
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    return html.unescape(re.sub(r"<[^>]+>", "", xml.replace("</w:p>", "\n")))


def run_fixture(f: lm.Fixture, workdir: Path, lexicon: Lexicon) -> tuple[FileResult, BatchResult, Path]:
    (workdir / "in").mkdir(parents=True, exist_ok=True)
    src = lm.build(f, workdir / "in")
    profile = workdir / "profile.json"
    if not profile.exists():
        lm.write_profile(profile, f.profile)
    batch = run_batch([src], profile, workdir / "out", workdir / "private", lexicon=lexicon, remove_images=f.remove_images)
    return batch.files[0], batch, profile


def check(f: lm.Fixture, workdir: Path, lexicon: Lexicon, *, oracle: bool = True) -> list[str]:
    """Every way this fixture's outcome differs from what the matrix expects."""
    result, batch, _ = run_fixture(f, workdir, lexicon)
    problems: list[str] = []
    if result.status != f.expect:
        problems.append(f"status {result.status}, expected {f.expect}: {result.reasons[:4]}")
    released = sorted((workdir / "out").glob("*.docx"))
    if f.expect != "ready":
        if released:
            problems.append(f"files released: {[p.name for p in released]}")
        if f.expect == "blocked" and f.reason and not any(f.reason in r for r in result.reasons):
            problems.append(f"no reason mentioning {f.reason!r}: {result.reasons}")
        return problems
    if len(released) != 1:
        return problems + [f"expected one released file, found {len(released)}"]
    out = released[0]
    if not re.fullmatch(r"document-\d\d\.docx", out.name):
        problems.append(f"output name not neutral: {out.name}")
    text = "\n".join(output_paragraphs(out))
    problems += [f"missing {s!r}" for s in f.must_contain if s not in text]
    problems += [f"unexpected {s!r}" for s in f.must_not_contain if s in text]
    if oracle:
        problems += [f"leak {x}" for x in leaks(out)]
    if batch.summary_path is not None:
        summary = skeleton(batch.summary_path.read_text(encoding="utf-8"))
        problems += [f"summary leak {s}" for s in lm.SECRETS if s in summary]
        if skeleton(Path(f.file).stem) in summary:
            problems.append(f"summary names the source file {f.file}")
    return problems
