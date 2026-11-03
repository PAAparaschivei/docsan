"""The JSON bridge the Mac app uses, exercised as a separate process."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import leakmatrix as lm


def call(request: dict | str) -> tuple[int, dict]:
    raw = request if isinstance(request, str) else json.dumps(request)
    proc = subprocess.run([sys.executable, "-I", "-m", "docsan.bridge"], input=raw, capture_output=True, text=True, check=False)
    return proc.returncode, json.loads(proc.stdout)


def fixture(name: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return lm.build(next(f for f in lm.fixtures() if f.name == name), directory)


def batch_request(tmp: Path, command: str, inputs: list[Path], profile: Path, **extra) -> dict:
    return {"command": command, "inputs": [str(p) for p in inputs], "profile": str(profile),
            "lexicon": str(lm.LEXICON), "private": str(tmp / "private"), **extra}


def test_hello():
    code, out = call({"command": "hello"})
    assert code == 0 and out["ok"] and out["protocol"] == 1


def test_errors_are_reported_not_raised():
    assert call({"command": "nope"})[1] == {"ok": False, "error": "unknown command 'nope'"}
    code, out = call("not json")
    assert code == 1 and not out["ok"]
    code, out = call({"command": "analyze", "inputs": []})
    assert code == 1 and "no input files" in out["error"]


def test_create_profile_and_policy(tmp_path):
    path = tmp_path / "profile.json"
    code, out = call({"command": "create-profile", "profile": str(path), "name": "Aurelan Group",
                      "aliases": ["Aurelan", "AGL"], "people": ["Elena Marchetti"]})
    assert code == 0, out
    assert out["profile"]["terms_by_label"] == {"CLIENT": 3, "PERSON": 1}
    data = json.loads(path.read_text())
    assert {"text": "AGL", "label": "CLIENT", "case_sensitive": True} in data["terms"]
    code, out = call({"command": "set-policy", "profile": str(path), "redact_system_names": True})
    assert out["profile"]["redact_system_names"] is True


def test_analyze_is_a_dry_run_with_previews(tmp_path):
    src = fixture("body", tmp_path / "in")
    profile = lm.write_profile(tmp_path / "profile.json")
    code, out = call(batch_request(tmp_path, "analyze", [src], profile))
    assert code == 0, out
    f = out["files"][0]
    assert f["status"] == "ready" and f["output"] is None
    original = " ".join(p["text"] for p in f["preview_original"])
    sanitized = " ".join(p["text"] for p in f["preview_sanitized"])
    assert "Aurelan" in original and "Aurelan" not in sanitized and "[CLIENT]" in sanitized
    report = next((tmp_path / "private" / "reports").glob("*.json")).read_text()
    assert "Aurelan" not in report  # previews stay out of the report


def test_review_classify_then_export(tmp_path):
    src = fixture("unknown-word", tmp_path / "in")
    profile = lm.write_profile(tmp_path / "profile.json")
    _, out = call(batch_request(tmp_path, "analyze", [src], profile))
    assert out["files"][0]["status"] == "needs_review"
    assert [r["word"] for r in out["files"][0]["review"]] == ["Halden"]
    code, out = call({"command": "classify", "profile": str(profile), "company": {"Halden": "SITE"}})
    assert code == 0, out
    code, out = call(batch_request(tmp_path, "export", [src], profile, out=str(tmp_path / "out")))
    assert code == 0, out
    f = out["files"][0]
    assert f["status"] == "ready" and f["output"] == "document-01.docx"
    assert (tmp_path / "out" / "document-01.docx").exists() and Path(out["summary"]).exists()


def test_system_decision_keeps_the_name(tmp_path):
    src = fixture("unknown-word", tmp_path / "in")
    profile = lm.write_profile(tmp_path / "profile.json")
    call({"command": "classify", "profile": str(profile), "systems": ["Halden"]})
    _, out = call(batch_request(tmp_path, "analyze", [src], profile))
    f = out["files"][0]
    assert f["status"] == "ready"
    assert any("Halden" in p["text"] for p in f["preview_sanitized"])


def test_image_round_trip(tmp_path):
    """Analyze proposes masks and a digest; export with that digest releases the image."""
    import imagekit
    import imagematrix as im

    imagekit.helper()
    f = next(x for x in im.fixtures() if x.name == "png-references")
    src, _, _ = im.build(f, tmp_path / "in")
    profile = lm.write_profile(tmp_path / "profile.json", {})
    code, out = call(batch_request(tmp_path, "analyze", [src], profile))
    assert code == 0 and out["ok"], out
    image = out["files"][0]["image"]
    assert out["files"][0]["kind"] == "image" and out["files"][0]["status"] == "needs_review", out["files"][0]["reasons"]
    assert image["masks"] and image["width"] == f.size[0] and not image["confirmed"]
    choice = {str(src): {"masks": [], "confirmed": image["digest"]}}
    code, out = call(batch_request(tmp_path, "export", [src], profile, out=str(tmp_path / "out"), images=choice))
    assert code == 0 and out["files"][0]["status"] == "ready", out["files"][0]["reasons"]
    assert (tmp_path / "out" / out["files"][0]["output"]).name == "image-01.png"
    code, out = call(batch_request(tmp_path, "analyze", [src], profile, images={str(src): {"masks": "x"}}))
    assert code == 1 and "masks" in out["error"]


def test_pdf_round_trip(tmp_path):
    """Analyze plans each page with its own digest; export with every page's digest releases the PDF."""
    import imagekit
    import pdfmatrix as pm

    imagekit.helper()
    f = next(x for x in pm.fixtures() if x.name == "mixed-pages")
    src, _ = pm.build(f, tmp_path / "in")
    profile = lm.write_profile(tmp_path / "profile.json", {})
    code, out = call(batch_request(tmp_path, "analyze", [src], profile))
    assert code == 0 and out["ok"], out
    result = out["files"][0]
    assert result["kind"] == "pdf" and result["status"] == "needs_review" and "pdf_folder" not in result
    pages = result["images"]
    assert [p["key"] for p in pages] == [f"{src}#page-1", f"{src}#page-2"] and all(p["masks"] for p in pages)
    choice = {p["key"]: {"masks": [], "confirmed": p["digest"]} for p in pages}
    code, out = call(batch_request(tmp_path, "export", [src], profile, out=str(tmp_path / "out"), images=choice))
    assert code == 0 and out["files"][0]["status"] == "ready", out["files"][0]["reasons"]
    assert out["files"][0]["output"] == "document-01.pdf" and (tmp_path / "out" / "document-01.pdf").exists()
    summary = json.loads(Path(out["summary"]).read_text())
    assert summary["files"][0]["checks"]["masks"] == "confirmed by the owner"


def test_profile_words_can_be_changed(tmp_path):
    profile = lm.write_profile(tmp_path / "profile.json", {"safe_words": ["Paul", "Kubernetes"]})
    code, out = call({"command": "profile-words", "profile": str(profile)})
    assert code == 0 and {"word": "Paul", "kind": ""} in out["words"]["safe"]
    code, out = call({"command": "reclassify", "profile": str(profile), "changes": {"Paul": "PERSON", "Kubernetes": "remove"}})
    assert code == 0, out
    assert out["words"]["safe"] == [] and {"text": "Paul", "label": "PERSON"} in out["words"]["terms"]
    code, out = call({"command": "reclassify", "profile": str(profile), "changes": {"Aurelan Group": "remove"}})
    assert code == 1 and "company name" in out["error"]
    code, out = call({"command": "set-policy", "profile": str(profile), "person_style": "label"})
    assert code == 0 and out["profile"]["person_style"] == "label"
