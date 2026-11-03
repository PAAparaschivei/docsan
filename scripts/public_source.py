#!/usr/bin/env python3
"""Validate or export DocSan's public source without reading private folders.

Reports contain file paths and rule names, never matching values. The export is
an explicit subset of Git-tracked and unignored source files, not a recursive
copy of the working directory. No Git metadata or history is exported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import zlib
from pathlib import Path, PurePosixPath


ROOT_FILES = {
    ".gitignore", "README.md", "LICENSE", "NOTICE", "THIRD-PARTY-NOTICES.md",
    "CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md",
}
PREFIXES = (
    ".github/", "app/DocSan/", "app/Helpers/", "app/scripts/",
    "engine/src/", "engine/tests/", "examples/", "scripts/",
)
EXTRA_FILES = {
    "app/README.md", "app/project.yml", "engine/README.md",
    "engine/pyproject.toml", "engine/uv.lock", "engine/.python-version", "engine/LICENSE", "engine/NOTICE",
    "docs/architecture.md", "docs/limitations.md", "docs/releasing.md",
    "docs/docx-component-contract.md", "docs/pdf-component-contract.md",
    "docs/image-component-contract.md", "docs/assets/app-icon.png",
    "docs/assets/app-icon-1024.png",
}
FORBIDDEN_PARTS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".ruff_cache", "private", "sensitive", "runtime", "staging", "output",
    "outputs", "logs", "build", "dist", "target", "xcuserdata",
}
FORBIDDEN_SUFFIXES = {
    ".doc", ".docx", ".docm", ".dotx", ".dotm", ".pdf", ".pptx",
    ".xlsx", ".zip", ".gz", ".tar", ".dmg", ".pkg", ".p12", ".pfx",
    ".pem", ".key", ".traineddata", ".dylib", ".so", ".pyc", ".log",
}
RULES = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "provider-token": re.compile(
        rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
        rb"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{30,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
        rb"xox[baprs]-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{30,}|hf_[A-Za-z0-9]{30,})"
    ),
    "local-home-path": re.compile(rb"(?:/Users/|/home/)[A-Za-z0-9_.-]+/"),
    "credential-assignment": re.compile(
        rb"(?im)^\s*[\x22\x27]?(?:[A-Z0-9_]+_)?(?:api[_-]?key|access[_-]?token|password|secret|client_secret|secret_access_key)"
        rb"[\x22\x27]?\s*[=:]\s*[\x22\x27]?[A-Za-z0-9_+/=-]{20,}"
    ),
    "bearer-credential": re.compile(rb"(?i)bearer[ ]+[A-Za-z0-9_.~+/-]{30,}"),
    "credential-in-url": re.compile(rb"https?://[^\s/:]+:[^\s/@]+@"),
}
EMAIL = re.compile(rb"[A-Za-z0-9_.+%-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# Aurelan is the invented company used by the synthetic regression fixtures.
SYNTHETIC_DOMAINS = {b"aurelan.lu", b"example.com", b"example.org", b"example.net"}


def email_findings(data: bytes, name: str) -> list[dict]:
    findings = []
    for match in EMAIL.finditer(data):
        domain = match.group(1).lower()
        if domain in SYNTHETIC_DOMAINS or domain.endswith((b".example", b".test", b".invalid")):
            continue
        # Asset catalog filenames such as icon_16x16@2x.png are not contacts.
        if domain in {b"2x.png", b"3x.png"} and match.group(0).startswith(b"icon_"):
            continue
        findings.append({"path": name, "rule": "non-fixture-email", "line": data[:match.start()].count(b"\n") + 1})
    return findings


def selected(name: str) -> bool:
    return name in ROOT_FILES or name in EXTRA_FILES or name.startswith(PREFIXES)


def path_problem(name: str) -> str | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        return "unsafe-path"
    if set(path.parts) & FORBIDDEN_PARTS:
        return "private-or-generated-path"
    if path.name.startswith(".env") and path.name != ".env.example":
        return "credential-file"
    if path.name in {".DS_Store", "Thumbs.db", "CLAUDE.local.md", "settings.local.json", "credentials.json", "auth.json"}:
        return "local-settings-or-credentials"
    if path.name in {"profile.json", "company-profile.json", "mappings.json", "report.json"}:
        if name not in {"engine/tests/fixtures/profile.json", "examples/profile.json"}:
            return "unreviewed-profile-or-report"
    if path.suffix.lower() in FORBIDDEN_SUFFIXES:
        return "document-archive-or-binary"
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg"}:
        if not (name.startswith("app/DocSan/Assets.xcassets/AppIcon.appiconset/")
                or name in {"docs/assets/app-icon.png", "docs/assets/app-icon-1024.png"}):
            return "unreviewed-image"
    return None


def png_problem(data: bytes) -> str | None:
    """Allow reviewed pixel/color chunks, rejecting text, EXIF and trailing data."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "invalid-png"
    allowed = {b"IHDR", b"IDAT", b"IEND", b"PLTE", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"pHYs"}
    offset = 8
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + size + 12
        if end > len(data):
            return "invalid-png"
        kind = data[offset + 4:offset + 8]
        if kind not in allowed:
            return "unreviewed-image-metadata"
        expected = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(data[offset + 4:end - 4]) != expected:
            return "invalid-png"
        offset = end
        if kind == b"IEND":
            return None if offset == len(data) else "trailing-image-data"
    return "invalid-png"


def candidate_names(root: Path, tree: bool = False, strict: bool = False) -> list[str]:
    if tree:
        # An exported tree has already been isolated. Reject private/generated
        # directory names before traversal rather than opening their contents.
        names = []
        def visit(directory: Path) -> None:
            for path in sorted(directory.iterdir()):
                name = path.relative_to(root).as_posix()
                if path.is_symlink() or path_problem(name) or not path.is_dir():
                    names.append(name)
                else:
                    visit(path)
        visit(root)
        return names
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, check=True,
    )
    return sorted({n for n in result.stdout.decode().split("\0") if n and (strict or selected(n))})


def inspect(root: Path, names: list[str]) -> tuple[list[dict], list[dict]]:
    manifest, findings = [], []
    for name in names:
        problem = path_problem(name)
        path = root / name
        if not selected(name):
            problem = "outside-public-file-set"
        if path.is_symlink():
            problem = "symlink"
        # A selected path's parents must also be real directories inside root.
        if any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents):
            problem = "symlink-parent"
        if problem:
            findings.append({"path": name, "rule": problem})
            continue
        if not path.is_file():
            findings.append({"path": name, "rule": "not-a-file"})
            continue
        data = path.read_bytes()
        if path.suffix.lower() == ".png":
            problem = png_problem(data)
            if problem:
                findings.append({"path": name, "rule": problem})
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                findings.append({"path": name, "rule": "unreviewed-binary"})
        for rule, regex in RULES.items():
            for match in regex.finditer(data):
                findings.append({"path": name, "rule": rule, "line": data[:match.start()].count(b"\n") + 1})
        findings.extend(email_findings(data, name))
        manifest.append({"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    missing = ROOT_FILES - {row["path"] for row in manifest}
    findings.extend({"path": name, "rule": "required-file-missing"} for name in sorted(missing))
    hashes = {row["path"]: row["sha256"] for row in manifest}
    for name in ["LICENSE", "NOTICE"]:
        if name in hashes and "engine/" + name in hashes:
            if hashes[name] != hashes["engine/" + name]:
                findings.append({"path": "engine/" + name, "rule": "package-license-differs"})
    return manifest, findings


def export(root: Path, destination: Path, manifest: list[dict]) -> None:
    root, destination = root.resolve(), destination.resolve()
    if destination.exists():
        raise ValueError("Destination already exists; choose a new empty path.")
    if destination == root or destination in root.parents:
        raise ValueError("Destination must not contain the source repository.")
    # Read and verify every selected byte before creating an export. Copying a
    # snapshot never falls back to a changed, unreviewed working-tree file.
    snapshot = []
    for row in manifest:
        path = root / row["path"]
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
            raise ValueError("A source path became a symlink after inspection.")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("A source file changed after inspection; run the check again.")
        snapshot.append((row["path"], data, path.stat().st_mode & 0o111))
    destination.mkdir(parents=True)
    for name, data, executable in snapshot:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if executable else 0o644)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tree", action="store_true", help="validate an isolated export without Git")
    parser.add_argument("--strict", action="store_true", help="reject every tracked/unignored file outside the public file set")
    parser.add_argument("--export", type=Path, help="write a new reviewed source tree, without Git history")
    parser.add_argument("--report", type=Path, help="write the path/hash manifest and redacted findings")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest, findings = inspect(root, candidate_names(root, args.tree, args.strict))
    report = {
        "files": manifest, "findings": findings,
        "scope": "explicit public source file set; no ignored private files or Git history",
        "limits": "Pattern checks supplement a human review; they do not establish universal absence of sensitive information.",
    }
    if args.report:
        if args.report.resolve().is_relative_to(root) and selected(args.report.resolve().relative_to(root).as_posix()):
            raise ValueError("The report must be outside the public source file set.")
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"files_checked": len(manifest), "findings": findings}, indent=2))
    if findings:
        return 1
    if args.export:
        export(root, args.export, manifest)
        print("Public source export written. Git history and internal project files were excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
