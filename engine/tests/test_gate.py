"""Failure injection: no failure, change or cancellation may release a file."""

from __future__ import annotations

import json

import leakmatrix as lm
import pytest

from docsan import gate, verify
from docsan.pipeline import Hooks, run_batch
from docsan.review import Lexicon

LEXICON = Lexicon.load(lm.LEXICON)
BODY = next(f for f in lm.fixtures() if f.name == "body")


def batch(tmp_path, hooks=None, fixture=BODY):
    src_dir = tmp_path / "in"
    src_dir.mkdir(exist_ok=True)
    src = lm.build(fixture, src_dir)
    profile = tmp_path / "profile.json"
    if not profile.exists():
        lm.write_profile(profile)
    return run_batch([src], profile, tmp_path / "out", tmp_path / "private", lexicon=LEXICON, hooks=hooks)


def released(tmp_path):
    return sorted(p.name for p in (tmp_path / "out").glob("*") if p.suffix == ".docx")


def test_baseline_releases_one_file(tmp_path):
    result = batch(tmp_path)
    assert result.files[0].status == "ready"
    assert released(tmp_path) == ["document-01.docx"]


def test_file_changed_after_verification_is_refused(tmp_path):
    hooks = Hooks(before_release=lambda p: p.write_bytes(p.read_bytes() + b"\0"))
    result = batch(tmp_path, hooks)
    assert result.files[0].status == "blocked"
    assert any("changed after verification" in r for r in result.files[0].reasons)
    assert released(tmp_path) == []


def test_file_changed_before_verification_is_caught(tmp_path):
    def tamper(path):
        import zipfile

        with zipfile.ZipFile(path, "a") as zf:
            zf.writestr("word/extra.xml", "<x>Aurelan</x>")

    result = batch(tmp_path, Hooks(after_stage=tamper))
    assert result.files[0].status == "blocked"
    assert released(tmp_path) == []


def test_cancellation_leaves_nothing_behind(tmp_path):
    def cancel(_):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        batch(tmp_path, Hooks(before_release=cancel))
    assert released(tmp_path) == []
    assert not any((tmp_path / "private" / "staging").iterdir())
    report = next((tmp_path / "private" / "reports").glob("*.json"))
    assert json.loads(report.read_text())["status"] == "cancelled"


def test_crash_during_processing_leaves_nothing_behind(tmp_path, monkeypatch):
    def crash(*_args, **_kw):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(verify, "verify_docx", crash)
    with pytest.raises(RuntimeError):
        batch(tmp_path)
    assert released(tmp_path) == []
    assert not any((tmp_path / "private" / "staging").iterdir())


def test_verifier_error_blocks(tmp_path, monkeypatch):
    def broken(*_args):
        raise RuntimeError("profile unreadable")

    monkeypatch.setattr(verify, "load_rules", broken)
    result = batch(tmp_path)
    assert result.files[0].status == "blocked"
    assert "inspection incomplete" in result.files[0].reasons
    assert released(tmp_path) == []


def test_broken_second_extractor_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "extractor", lambda p: "")
    result = batch(tmp_path)
    assert result.files[0].status == "blocked"
    assert any("self-test" in r for r in result.files[0].reasons)


def test_different_volume_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "same_volume", lambda a, b: False)
    result = batch(tmp_path)
    assert result.files[0].status == "blocked"
    assert any("different volumes" in r for r in result.files[0].reasons)
    assert released(tmp_path) == []


def test_release_never_overwrites(tmp_path):
    staged = tmp_path / "staged.docx"
    staged.write_bytes(b"verified bytes")
    out = tmp_path / "out"
    out.mkdir()
    (out / "document-01.docx").write_bytes(b"earlier file")
    with pytest.raises(gate.ReleaseRefused):
        gate.release(staged, gate.sha256_file(staged), out, "document-01.docx")
    assert (out / "document-01.docx").read_bytes() == b"earlier file"


def test_second_batch_gets_a_new_name(tmp_path):
    batch(tmp_path)
    batch(tmp_path)
    assert released(tmp_path) == ["document-01.docx", "document-02.docx"]


def test_mapping_is_not_written(tmp_path):
    person = next(f for f in lm.fixtures() if f.name == "person")
    result = batch(tmp_path, fixture=person)
    assert result.files[0].status == "ready"
    report = result.report_path.read_text(encoding="utf-8")
    assert "Marchetti" not in report and "Elena" not in report


def test_private_folders_are_owner_only(tmp_path):
    result = batch(tmp_path)
    assert (tmp_path / "private").stat().st_mode & 0o077 == 0
    assert result.report_path.stat().st_mode & 0o077 == 0


def test_released_bytes_match_the_verified_hash(tmp_path):
    result = batch(tmp_path)
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    entry = summary["files"][0]
    assert entry["name"] == result.files[0].output
    assert gate.sha256_file(tmp_path / "out" / entry["name"]) == entry["sha256"] == result.files[0].sha256


def test_volume_without_hard_links_blocks(tmp_path, monkeypatch):
    def no_links(*_args):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(gate.os, "link", no_links)
    result = batch(tmp_path)
    assert result.files[0].status == "blocked"
    assert any("atomically" in r for r in result.files[0].reasons)
    assert released(tmp_path) == []
