"""Run the Apple Vision helper (``app/Helpers/docsan-vision``).

The helper finds text and paints boxes; every decision stays in the engine.
In the app it sits next to the engine bundle, and tests point
``DOCSAN_VISION`` at a build of it.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ENV = "DOCSAN_VISION"
# Text recognition is a pure function of the image and the request, so tests
# that process the same image many times reuse the answer.
_cache: dict[str, dict[str, Any]] = {}
# During a batch the answers are also kept in the private folder: the app
# checks the batch again after every decision, and reading every page of a
# long PDF again each time would take minutes. See ``disk_cache``.
_disk: Path | None = None
_used: set[str] = set()


@contextmanager
def disk_cache(folder: Path) -> Iterator[None]:
    """Keep answers in ``folder`` while the block runs, then delete every
    answer the block did not use, so the folder holds only the current
    batch's (private) reading of its files."""
    global _disk
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    _disk = folder
    _used.clear()
    try:
        yield
    finally:
        _disk = None
        for entry in folder.glob("*.json"):
            if entry.name not in _used:
                entry.unlink(missing_ok=True)
        _used.clear()


class HelperError(Exception):
    pass


def helper_path() -> Path:
    if os.environ.get(ENV):
        return Path(os.environ[ENV])
    # Bundle layout: <engine>/site/docsan/image/helper.py and <engine>/bin/.
    return Path(__file__).resolve().parents[3] / "bin" / "docsan-vision"


_builds: dict[tuple[str, int, int], str] = {}


def helper_build() -> str:
    """A fingerprint of the helper binary. Saved answers are keyed by it, so an
    updated helper never reuses what an older one read."""
    path = helper_path()
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _builds:
        _builds[key] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return _builds[key]


def _environment() -> dict[str, str]:
    # Vision keeps its model cache in the user's caches folder. In the sandbox
    # that must be the app container's, which CFFIXED_USER_HOME names.
    env = {"PATH": "/usr/bin:/bin"}
    for key in ("HOME", "CFFIXED_USER_HOME", "APP_SANDBOX_CONTAINER_ID", "TMPDIR", "LANG"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def call(request: dict[str, Any], timeout: int = 600) -> dict[str, Any]:
    path = helper_path()
    if not os.access(path, os.X_OK):
        raise HelperError("the image helper is not installed")
    key = None
    saved: Path | None = None
    if request.get("command") in ("probe", "ocr", "fit", "print", "regions"):
        digest = hashlib.sha256(Path(request["input"]).read_bytes()).hexdigest()
        key = json.dumps({**request, "input": digest, "helper": helper_build()}, sort_keys=True)
        if _disk is not None:
            saved = _disk / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
            _used.add(saved.name)
        if key in _cache:
            if saved is not None and not saved.exists():
                _save(saved, _cache[key])
            return _cache[key]
        if saved is not None:
            try:
                _cache[key] = json.loads(saved.read_text(encoding="utf-8"))
                return _cache[key]
            except (OSError, ValueError):
                pass
    try:
        proc = subprocess.run(
            [str(path)],
            input=json.dumps(request).encode(),
            capture_output=True,
            timeout=timeout,
            env=_environment(),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError(f"image helper failed: {exc}") from exc
    try:
        response = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise HelperError(f"image helper returned no answer (exit {proc.returncode})") from exc
    if not response.get("ok"):
        raise HelperError(str(response.get("error", "image helper failed")))
    if key is not None:
        _cache[key] = response
        if saved is not None:
            _save(saved, response)
    return response


def _save(path: Path, response: dict[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(response, fh)
