"""JSON bridge between the Mac app and the engine.

The app starts the engine as a helper process, writes one JSON request to
stdin and reads one JSON response from stdout. Nothing else is printed to
stdout. Requests carry only paths inside the app's private container, and the
helper inherits the app's sandbox, so it cannot open network connections.

Request: {"command": "<name>", ...arguments}
Response: {"ok": true, ...result} or {"ok": false, "error": "<message>"}
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .pipeline import FileResult, run_batch
from .profile import (
    PERSON_STYLES,
    ProfileError,
    add_classifications,
    create_profile,
    load_profile,
    profile_words,
    reclassify,
    set_person_style,
    set_redact_system_names,
)
from .review import Lexicon

PROTOCOL = 1
Handler = Callable[[dict[str, Any]], dict[str, Any]]
COMMANDS: dict[str, Handler] = {}


class BridgeError(Exception):
    pass


def command(name: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        COMMANDS[name] = fn
        return fn

    return register


def _path(req: dict[str, Any], key: str) -> Path:
    value = req.get(key)
    if not isinstance(value, str) or not value:
        raise BridgeError(f"missing path {key!r}")
    return Path(value)


def _strings(req: dict[str, Any], key: str) -> list[str]:
    value = req.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise BridgeError(f"{key!r} must be a list of strings")
    return value


def _images(req: dict[str, Any]) -> dict[str, dict]:
    """The owner's choices per image path: extra ``masks``, the ``confirmed``
    digest and, for a picture in a Word file, ``picture``: "remove" to remove
    it whole or "keep" to mask it even where DocSan would remove it."""
    value = req.get("images", {})
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, dict) for k, v in value.items()):
        raise BridgeError("'images' must map image paths to choices")
    for choice in value.values():
        if not isinstance(choice.get("masks", []), list) or not isinstance(choice.get("confirmed", ""), str):
            raise BridgeError("an image choice holds a list of masks and a confirmed digest")
        if choice.get("picture", "keep") not in ("keep", "remove"):
            raise BridgeError("an image choice's picture is 'keep' or 'remove'")
    return value


def _file_json(f: FileResult) -> dict[str, Any]:
    data = asdict(f)
    data.pop("pdf_folder", None)
    data["review"] = [{"word": r.word, "context": r.context, "count": r.count, "kind": r.kind} for r in f.review]
    return data


def _profile_json(path: Path) -> dict[str, Any]:
    p = load_profile(path)
    counts: dict[str, int] = {}
    for t in p.terms:
        counts[t.label] = counts.get(t.label, 0) + 1
    return {
        "name": p.name,
        "policy": p.policy,
        "terms": len(p.terms),
        "terms_by_label": counts,
        "redact_system_names": p.redact_system_names,
        "person_style": p.person_style,
    }


@command("hello")
def _hello(req: dict[str, Any]) -> dict[str, Any]:
    return {"protocol": PROTOCOL, "python": sys.version.split()[0]}


@command("profile-info")
def _profile_info(req: dict[str, Any]) -> dict[str, Any]:
    return {"profile": _profile_json(_path(req, "profile"))}


@command("create-profile")
def _create_profile(req: dict[str, Any]) -> dict[str, Any]:
    path = _path(req, "profile")
    name = req.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BridgeError("a company name is required")
    create_profile(path, name, _strings(req, "aliases"), _strings(req, "people"), _strings(req, "labels"))
    return {"profile": _profile_json(path)}


@command("set-policy")
def _set_policy(req: dict[str, Any]) -> dict[str, Any]:
    """Change ``redact_system_names`` (true or false) and/or ``person_style``."""
    path = _path(req, "profile")
    if "redact_system_names" not in req and "person_style" not in req:
        raise BridgeError("nothing to change")
    if "redact_system_names" in req:
        value = req["redact_system_names"]
        if not isinstance(value, bool):
            raise BridgeError("redact_system_names must be true or false")
        set_redact_system_names(path, value)
    if "person_style" in req:
        style = req["person_style"]
        if style not in PERSON_STYLES:
            raise BridgeError(f"person_style must be one of {sorted(PERSON_STYLES)}")
        set_person_style(path, style)
    return {"profile": _profile_json(path)}


@command("profile-words")
def _profile_words(req: dict[str, Any]) -> dict[str, Any]:
    """Every word the profile has learned, to show and change in the app."""
    return {"words": profile_words(_path(req, "profile"))}


@command("reclassify")
def _reclassify(req: dict[str, Any]) -> dict[str, Any]:
    path = _path(req, "profile")
    changes = req.get("changes")
    if not isinstance(changes, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in changes.items()):
        raise BridgeError("'changes' must map words to 'safe', 'remove' or a label")
    reclassify(path, changes)
    return {"profile": _profile_json(path), "words": profile_words(path)}


@command("classify")
def _classify(req: dict[str, Any]) -> dict[str, Any]:
    path = _path(req, "profile")
    company = req.get("company", {})
    if not isinstance(company, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in company.items()):
        raise BridgeError("'company' must map words to labels")
    add_classifications(path, company, _strings(req, "safe"), _strings(req, "systems"))
    return {"profile": _profile_json(path)}


def _batch(req: dict[str, Any], release: bool) -> dict[str, Any]:
    inputs = [Path(p) for p in _strings(req, "inputs")]
    if not inputs:
        raise BridgeError("no input files")
    lexicon = Lexicon.load(_path(req, "lexicon"))
    out = _path(req, "out") if release else None
    batch = run_batch(
        inputs,
        _path(req, "profile"),
        out,
        _path(req, "private"),
        lexicon=lexicon,
        remove_images=bool(req.get("remove_images", False)),
        label_unreadable=bool(req.get("label_unreadable", False)),
        release=release,
        images=_images(req),
    )
    return {
        "batch": batch.batch_id,
        "files": [_file_json(f) for f in batch.files],
        "summary": str(batch.summary_path) if batch.summary_path else None,
    }


@command("analyze")
def _analyze(req: dict[str, Any]) -> dict[str, Any]:
    """Read, sanitize, review and verify every file without releasing any."""
    return _batch(req, release=False)


@command("export")
def _export(req: dict[str, Any]) -> dict[str, Any]:
    """Run the batch again and release verified files into ``out``."""
    return _batch(req, release=True)


def handle(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        return {"ok": False, "error": "request must be a JSON object"}
    name = request.get("command")
    handler = COMMANDS.get(name) if isinstance(name, str) else None
    if handler is None:
        return {"ok": False, "error": f"unknown command {name!r}"}
    try:
        return {"ok": True, **handler(request)}
    except (BridgeError, ProfileError, FileNotFoundError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - report, never crash silently
        return {"ok": False, "error": f"engine error: {exc!r}"}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        response = {"ok": False, "error": f"invalid JSON request: {exc}"}
    else:
        response = handle(request)
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    sys.stdout.flush()
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
