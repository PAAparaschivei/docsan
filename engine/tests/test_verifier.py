"""The verifier on its own: crafted outputs that it must pass, flag or refuse."""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import leakmatrix as lm
import pytest
from docsan import verify
from docsan.docx.model import (
    Document,
    HeaderFooter,
    Level,
    ListDefinition,
    ListInstance,
    Numbering,
    Paragraph,
    Run,
    Section,
)
from docsan.docx.write import PACKAGE_RELS, STYLES_HEAD, package_bytes, styles_xml

PLACEHOLDER = '<w:t xml:space="preserve">PLACEHOLDER</w:t>'


def package(run_xml: str = "", *, edit=None) -> bytes:
    parts = package_bytes(Document([Paragraph([Run("PLACEHOLDER")])]))
    doc = parts["word/document.xml"].decode("utf-8")
    parts["word/document.xml"] = doc.replace(PLACEHOLDER, run_xml or '<w:t xml:space="preserve">Clean text.</w:t>').encode()
    if edit:
        parts = edit(parts)
    return zipped(parts)


def zipped(parts: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in parts.items():
            zf.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def written(doc: Document) -> bytes:
    """A package exactly as the writer makes it."""
    return zipped(package_bytes(doc))


def with_header(text: str) -> Document:
    return Document([Paragraph([Run("Clean text.")])], Section(headers={"default": 0}),
                    [HeaderFooter("header", [Paragraph([Run(text)])])])


def with_list_text(text: str) -> Document:
    level = Level(0, 1, "decimal", text, "left", None, False, "tab", (), ())
    return Document([Paragraph([Run("First point")], "list", 0, True, num=(3, 0))],
                    numbering=Numbering([ListDefinition([level], "multilevel")], [ListInstance(0, [])]))


def with_settings(parts):
    settings = parts["word/settings.xml"].decode().replace("<w:compat>", '<w:attachedTemplate w:val="Aurelan.dotx"/><w:compat>')
    return {**parts, "word/settings.xml": settings.encode()}


def t(text: str) -> str:
    from xml.sax.saxutils import escape

    return f'<w:t xml:space="preserve">{escape(text)}</w:t>'


def with_part(name: str, data: bytes):
    def edit(parts):
        return {**parts, name: data}

    return edit


def with_attribute(parts):
    doc = parts["word/document.xml"].decode().replace("<w:p><w:r>", '<w:p><w:pPr><w:pStyle w:val="Aurelan"/></w:pPr><w:r>', 1)
    return {**parts, "word/document.xml": doc.encode()}


@dataclass
class Case:
    name: str
    data: bytes
    expect: str
    extractor: str = "real"  # real, leaky or broken


CASES = [
    Case("clean", package(), "pass"),
    Case("coded-classification", package(t("C3 - Confidentiel")), "findings"),
    Case("account-identifier", package(t("Account 9876543210")), "findings"),
    Case("plain-name", package(t("Aurelan approved it.")), "findings"),
    Case("split-short-code", package(t("Ticker AG") + "</w:r><w:r>" + t("L closed.")), "findings"),
    Case("zero-width-short-code", package(t("Ticker AG\u200bL closed.")), "findings"),
    Case("cyrillic", package(t("Аurelan approved.")), "findings"),
    Case("accent-short-code", package(t("Ticker ÁGL closed.")), "findings"),
    Case("fullwidth", package(t("ＡＵＲＥＬＡＮ approved.")), "findings"),
    Case("phone-en-dash", package(t("Call 26–12–34–56 now.")), "findings"),
    Case("phone-nbsp", package(t("Call 26 12 34 56 now.")), "findings"),
    # U+1680 is a space that compatibility folding leaves alone.
    Case("phone-ogham-space", package(t("Call 26\u168012\u168034\u168056 now.")), "findings"),
    Case("upper", package(t("AURELAN approved.")), "findings"),
    Case("double-separator", package(t("Aure~~lan approved.")), "findings"),
    Case("hyphenated-label", package(t("Internal-Use-Only material.")), "findings"),
    Case("attribute", package(edit=with_attribute), "findings"),
    Case("email", package(t("Write to jane.doe@example.org now.")), "findings"),
    Case("url", package(t("Open http://10.0.0.5/admin now.")), "findings"),
    Case("url-short-host", package(t("Open http://service/status now.")), "findings"),
    Case("domain", package(t("Mail moved to mail.example.nl today.")), "findings"),
    Case("phone", package(t("Call +352 26 12 34 56 now.")), "findings"),
    Case("ambiguous-name", package(t("Run setup.py first.")), "findings"),
    Case("company-in-compound", package(t("Staff use the MyAGL portal.")), "findings"),
    Case("kept-system-name", package(t("The platform Vireo runs nightly.")), "pass"),
    Case("exception-honoured", package(t("Send a PATCH /v2/orders request.")), "pass"),
    Case("exception-not-matching", package(t("PATCH Networks runs it.")), "findings"),
    Case("trailing-bytes", package() + b"hidden payload", "findings"),
    Case("extra-part", package(edit=with_part("word/extra.xml", b"<x/>")), "findings"),
    Case("extra-relationship", package(edit=lambda parts: {**parts, "word/_rels/document.xml.rels": parts["word/_rels/document.xml.rels"].replace(
        b"</Relationships>", b'<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles2.xml"/></Relationships>')}), "findings"),
    Case("changed-styles", package(edit=with_part("word/styles.xml", styles_xml(0).encode() + b" ")), "findings"),
    Case("named-style", package(edit=with_part("word/styles.xml", styles_xml(0).replace("</w:styles>",
         '<w:style w:type="paragraph" w:styleId="Style1"><w:name w:val="Aurelan Body"/></w:style></w:styles>').encode())), "findings"),
    Case("neutral-styles", package(edit=with_part("word/styles.xml", styles_xml(3).encode())), "pass"),
    Case("listed-font", package('<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:b/><w:sz w:val="22"/></w:rPr>' + t("Clean text.")), "pass"),
    Case("company-font", package('<w:rPr><w:rFonts w:ascii="Aurelan Sans"/></w:rPr>' + t("Clean text.")), "findings"),
    Case("unlisted-font", package('<w:rPr><w:rFonts w:ascii="Corporate Grotesk"/></w:rPr>' + t("Clean text.")), "findings"),
    Case("text-in-colour", package('<w:rPr><w:color w:val="Aurelan"/></w:rPr>' + t("Clean text.")), "findings"),
    Case("theme-colour", package('<w:rPr><w:color w:val="1F3864" w:themeColor="accent1"/></w:rPr>' + t("Clean text.")), "findings"),
    Case("link-field", package("</w:r><w:fldSimple w:instr=\"HYPERLINK http://intranet\"><w:r>" + t("here") + "</w:r></w:fldSimple><w:r>" + t(".")), "findings"),
    Case("page-field", written(Document([Paragraph([Run("Page "), Run("1", field="PAGE")])])), "pass"),
    Case("header-with-name", written(with_header("Aurelan Confidential")), "findings"),
    Case("clean-header", written(with_header("Confidential")), "pass"),
    Case("list-text-with-name", written(with_list_text("Aurelan %1.")), "findings"),
    Case("list-text", written(with_list_text("%1.")), "pass"),
    Case("settings-template", package(edit=with_settings), "findings"),
    Case("hidden-element", package('<w:rPr><w:vanish/></w:rPr>' + t("Visible.")), "findings"),
    Case("extractor-only-leak", package(), "findings", extractor="leaky"),
    Case("broken-extractor", package(), "could_not_inspect", extractor="broken"),
]


def run_case(case: Case, tmp: Path, monkeypatch) -> str:
    path = tmp / f"{case.name}.docx"
    path.write_bytes(case.data)
    real = verify.extract_with_textutil
    if case.extractor == "leaky":
        monkeypatch.setattr(verify, "extractor", lambda p: real(p) + ("" if p.name == "self-test.docx" else "\nAurelan"))
    elif case.extractor == "broken":
        monkeypatch.setattr(verify, "extractor", lambda p: "")
    else:
        monkeypatch.setattr(verify, "extractor", real)
    return verify.verify_docx(path, lm.PROFILE).status


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_case(case, tmp_path, monkeypatch):
    assert run_case(case, tmp_path, monkeypatch) == case.expect


def test_pins_match_writer():
    assert verify.PINNED_PARTS["_rels/.rels"] == hashlib.sha256(PACKAGE_RELS.encode("utf-8")).hexdigest()
    assert verify.STYLES_HEAD_SHA256 == hashlib.sha256(STYLES_HEAD.encode("utf-8")).hexdigest()


def test_unreadable_profile_is_incomplete(tmp_path):
    path = tmp_path / "out.docx"
    path.write_bytes(package())
    bad = tmp_path / "profile.json"
    bad.write_text('{"format": "docsan-profile", "version": 1, "term": []}')
    assert verify.verify_docx(path, bad).status == "could_not_inspect"


def test_not_a_zip_is_incomplete(tmp_path):
    path = tmp_path / "out.docx"
    path.write_bytes(b"not a package")
    assert verify.verify_docx(path, lm.PROFILE).status == "could_not_inspect"


def test_system_names_are_checked_when_redaction_is_on(tmp_path):
    path = tmp_path / "out.docx"
    path.write_bytes(package(t("The platform Vireo runs nightly.")))
    profile = lm.write_profile(tmp_path / "profile.json", {"redact_system_names": True})
    assert verify.verify_docx(path, profile).status == "findings"


def test_first_names_may_stay_before_an_initial(tmp_path):
    """With first-name-initial style, "Paul A." passes and "Paul" alone does not.
    The verifier cannot tell a first name from a family name, so a family name
    before an initial passes too; the sanitizer keeps only a name's first word."""
    terms = lm.base_terms() + [{"text": "Paul", "label": "PERSON"}, {"text": "Aparaschivei", "label": "PERSON"}]
    profile = lm.write_profile(tmp_path / "profile.json", {"person_style": "first_name_initial", "terms": terms})
    outcomes = {}
    for name, text in [("initial", "Paul A. signed it."), ("alone", "Paul signed it."),
                       ("family", "Aparaschivei A. signed it."), ("full", "Elena Marchetti signed it.")]:
        path = tmp_path / f"{name}.docx"
        path.write_bytes(package(t(text)))
        outcomes[name] = verify.verify_docx(path, profile).status
    assert outcomes == {"initial": "pass", "alone": "findings", "family": "pass", "full": "findings"}
