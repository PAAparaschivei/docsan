"""Company profile: the private list of references to remove.

Profiles live outside the repository. The loader is strict: an unknown key, a
missing field or an empty term list is an error, because a typo that silently
loads no terms would let every reference through.

Format::

    {
      "format": "docsan-profile",
      "version": 1,
      "name": "Aurelan Group",
      "policy": "v1",
      "terms": [
        {"text": "Aurelan", "label": "CLIENT"},
        {"text": "PATCH", "label": "CLIENT", "case_sensitive": true},
        {"text": "Elena Marchetti", "label": "PERSON", "group": "marchetti"}
      ],
      "exceptions": [{"text": "PATCH", "followed_by": ["\\\\s*/"]}],
      "safe_words": ["Kubernetes"],
      "redact_system_names": false,
      "person_style": "first_name_initial",
      "logos": [{"print": "3f0a9c51e2b7d804", "aspect": 1.43}]
    }

System names (label SYSTEM) are kept unless ``redact_system_names`` is true.
People's names become the first name and the family-name initial ("Elena
M.") by default, or numbered labels ("[PERSON-1]") with ``"person_style":
"label"`` (decision D-021).
A company term inside a kept name is still replaced: "MyAurelan" becomes
"My[CLIENT]".
``logos`` holds the fingerprints of pictures the owner exported removed
(``docsan.image.logo``); a picture that matches one is removed too.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

LABELS = frozenset(
    {"CLIENT", "ORG", "PERSON", "SYSTEM", "PROJECT", "SITE", "PRODUCT", "LABEL", "ADDRESS", "DOMAIN"}
)
UNNUMBERED = frozenset({"CLIENT", "LABEL"})
PERSON_STYLES = frozenset({"first_name_initial", "label"})

_TOP_KEYS = {
    "format", "version", "name", "policy", "terms", "exceptions", "safe_words", "redact_system_names", "person_style",
    "logos",
}
_TERM_KEYS = {"text", "label", "case_sensitive", "whole_word", "group"}
_EXCEPTION_KEYS = {"text", "followed_by"}
_LOGO_KEYS = {"print", "aspect"}


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    text: str
    label: str
    case_sensitive: bool = False
    whole_word: bool = False
    group: str | None = None


@dataclass(frozen=True)
class TermException:
    text: str
    followed_by: tuple[str, ...]


@dataclass
class Profile:
    name: str
    policy: str
    terms: list[Term]
    exceptions: list[TermException] = field(default_factory=list)
    safe_words: list[str] = field(default_factory=list)
    redact_system_names: bool = False
    person_style: str = "first_name_initial"
    logos: list[tuple[str, float]] = field(default_factory=list)  # fingerprint and ink shape


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ProfileError(message)


def parse_profile(data: object) -> Profile:
    _require(isinstance(data, dict), "profile must be a JSON object")
    assert isinstance(data, dict)
    unknown = set(data) - _TOP_KEYS
    _require(not unknown, f"unknown profile keys: {sorted(unknown)}")
    _require(data.get("format") == "docsan-profile", "format must be 'docsan-profile'")
    _require(data.get("version") == 1, "version must be 1")
    _require(isinstance(data.get("name"), str) and data["name"].strip() != "", "name is required")
    _require(isinstance(data.get("policy"), str) and data["policy"] != "", "policy is required")

    raw_terms = data.get("terms")
    _require(isinstance(raw_terms, list) and len(raw_terms) > 0, "terms must be a non-empty list")
    terms: list[Term] = []
    for i, t in enumerate(raw_terms):
        _require(isinstance(t, dict), f"terms[{i}] must be an object")
        unknown = set(t) - _TERM_KEYS
        _require(not unknown, f"terms[{i}] has unknown keys: {sorted(unknown)}")
        text = t.get("text")
        _require(isinstance(text, str) and text.strip() != "", f"terms[{i}].text is required")
        label = t.get("label")
        _require(label in LABELS, f"terms[{i}].label must be one of {sorted(LABELS)}")
        for flag in ("case_sensitive", "whole_word"):
            _require(isinstance(t.get(flag, False), bool), f"terms[{i}].{flag} must be true or false")
        group = t.get("group")
        _require(group is None or isinstance(group, str), f"terms[{i}].group must be a string")
        terms.append(
            Term(
                text=text.strip(),
                label=label,
                case_sensitive=t.get("case_sensitive", False),
                whole_word=t.get("whole_word", False),
                group=group,
            )
        )

    exceptions: list[TermException] = []
    for i, e in enumerate(data.get("exceptions", [])):
        _require(isinstance(e, dict), f"exceptions[{i}] must be an object")
        unknown = set(e) - _EXCEPTION_KEYS
        _require(not unknown, f"exceptions[{i}] has unknown keys: {sorted(unknown)}")
        text = e.get("text")
        _require(isinstance(text, str) and text != "", f"exceptions[{i}].text is required")
        _require(
            any(t.text == text for t in terms), f"exceptions[{i}].text must match a term exactly"
        )
        followed = e.get("followed_by")
        _require(
            isinstance(followed, list) and followed and all(isinstance(f, str) for f in followed),
            f"exceptions[{i}].followed_by must be a non-empty list of patterns",
        )
        for f in followed:
            try:
                re.compile(f)
            except re.error as exc:
                raise ProfileError(f"exceptions[{i}] pattern {f!r} is invalid: {exc}") from exc
        exceptions.append(TermException(text=text, followed_by=tuple(followed)))

    redact_systems = data.get("redact_system_names", False)
    _require(isinstance(redact_systems, bool), "redact_system_names must be true or false")

    person_style = data.get("person_style", "first_name_initial")
    _require(person_style in PERSON_STYLES, f"person_style must be one of {sorted(PERSON_STYLES)}")

    logos: list[tuple[str, float]] = []
    raw_logos = data.get("logos", [])
    _require(isinstance(raw_logos, list), "logos must be a list")
    for i, lg in enumerate(raw_logos):
        _require(isinstance(lg, dict) and set(lg) == _LOGO_KEYS, f"logos[{i}] must hold a print and an aspect")
        _require(isinstance(lg["print"], str) and re.fullmatch(r"[0-9a-f]{16}", lg["print"]) is not None,
                 f"logos[{i}].print must be 16 hexadecimal digits")
        _require(isinstance(lg["aspect"], (int, float)) and not isinstance(lg["aspect"], bool) and lg["aspect"] > 0,
                 f"logos[{i}].aspect must be a positive number")
        logos.append((lg["print"], float(lg["aspect"])))

    safe = data.get("safe_words", [])
    _require(
        isinstance(safe, list) and all(isinstance(s, str) and s for s in safe),
        "safe_words must be a list of strings",
    )
    return Profile(
        name=data["name"].strip(),
        policy=data["policy"],
        terms=terms,
        exceptions=exceptions,
        safe_words=list(safe),
        redact_system_names=redact_systems,
        person_style=person_style,
        logos=logos,
    )


def load_profile(path: Path) -> Profile:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"cannot read profile {path}: {exc}") from exc
    return parse_profile(data)


def _write_atomic(path: Path, data: dict) -> None:
    """Validate, then replace the file in one step so a crash cannot truncate it."""
    parse_profile(data)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".profile-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_raw(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    parse_profile(data)  # refuse to edit an invalid profile
    return data


def create_profile(
    path: Path,
    name: str,
    aliases: list[str] = (),
    people: list[str] = (),
    labels: list[str] = (),
) -> Profile:
    """Write a new profile. Short all-capital aliases ("AGL") match case-sensitively."""
    path = Path(path)
    if path.exists():
        raise ProfileError(f"{path} already exists")
    terms: list[dict] = []
    seen: set[str] = set()

    def add(text: str, label: str) -> None:
        text = text.strip()
        if not text or text.casefold() in seen:
            return
        seen.add(text.casefold())
        term: dict = {"text": text, "label": label}
        if label == "CLIENT" and len(text) <= 4 and text.isupper():
            term["case_sensitive"] = True
        terms.append(term)

    add(name, "CLIENT")
    for a in aliases:
        add(a, "CLIENT")
    for p in people:
        add(p, "PERSON")
    for lab in labels:
        add(lab, "LABEL")
    data = {"format": "docsan-profile", "version": 1, "name": name.strip(), "policy": "v1", "terms": terms,
            "exceptions": [], "safe_words": [], "redact_system_names": False}
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, data)
    return parse_profile(data)


def set_redact_system_names(path: Path, value: bool) -> None:
    data = _read_raw(path)
    data["redact_system_names"] = bool(value)
    _write_atomic(Path(path), data)


def set_person_style(path: Path, value: str) -> None:
    if value not in PERSON_STYLES:
        raise ProfileError(f"person_style must be one of {sorted(PERSON_STYLES)}")
    data = _read_raw(path)
    data["person_style"] = value
    _write_atomic(Path(path), data)


def profile_words(path: Path) -> dict:
    """The words a profile has learned: kept words, each with a hint ("place"
    or "glossary"), and classified terms."""
    from .review import glossary_words, place_words
    from .text import fold_word

    data = _read_raw(path)
    places, glossary = place_words(), glossary_words()

    def hint(word: str) -> str:
        folded = fold_word(word)
        return "place" if folded in places else "glossary" if folded in glossary else ""

    return {
        "safe": [{"word": w, "kind": hint(w)} for w in sorted(data.get("safe_words", []), key=str.casefold)],
        "terms": [{"text": t["text"], "label": t["label"]} for t in data["terms"]],
    }


def reclassify(path: Path, changes: dict[str, str]) -> None:
    """Change what the profile does with words it already knows.

    Each value is "safe", "remove" or a label (SYSTEM keeps a system name).
    Changing a word drops it from the kept words and from the terms, then adds
    it back as asked. The company's name (the profile's own name) cannot be
    removed this way.
    """
    path = Path(path)
    data = _read_raw(path)
    for word, choice in changes.items():
        if choice != "safe" and choice != "remove" and choice not in LABELS:
            raise ProfileError(f"{word!r}: choose 'safe', 'remove' or one of {sorted(LABELS)}")
        if word == data["name"] and choice in ("safe", "remove"):
            raise ProfileError("the company name stays in the profile")
        data["safe_words"] = [w for w in data.get("safe_words", []) if w != word]
        kept_terms = [t for t in data["terms"] if t["text"] != word]
        data["exceptions"] = [e for e in data.get("exceptions", []) if e["text"] != word or any(t["text"] == word for t in kept_terms)]
        data["terms"] = kept_terms
        if choice == "safe":
            data["safe_words"].append(word)
        elif choice != "remove":
            data["terms"].append({"text": word, "label": choice})
    _write_atomic(path, data)


def add_classifications(path: Path, company: dict[str, str], safe: list[str], systems: list[str] = ()) -> None:
    """Record review decisions in the private profile.

    ``company`` maps a word to its label; ``safe`` lists words to keep;
    ``systems`` lists system names, kept unless system names are redacted.
    """
    path = Path(path)
    data = _read_raw(path)
    existing = {t["text"] for t in data["terms"]}
    for word, label in company.items():
        if label not in LABELS:
            raise ProfileError(f"label for {word!r} must be one of {sorted(LABELS)}")
        if word not in existing:
            data["terms"].append({"text": word, "label": label})
            existing.add(word)
    for word in systems:
        if word not in existing:
            data["terms"].append({"text": word, "label": "SYSTEM"})
            existing.add(word)
    safe_list = data.setdefault("safe_words", [])
    for word in safe:
        if word not in safe_list:
            safe_list.append(word)
    _write_atomic(path, data)


def add_logos(path: Path, prints: list[tuple[str, float]]) -> None:
    """Remember the fingerprints of removed pictures, so the same logo is removed next time."""
    path = Path(path)
    data = _read_raw(path)
    logos = data.setdefault("logos", [])
    known = {(lg["print"], lg["aspect"]) for lg in logos}
    for bits, aspect in prints:
        if (bits, round(aspect, 3)) not in known:
            logos.append({"print": bits, "aspect": round(aspect, 3)})
            known.add((bits, round(aspect, 3)))
    _write_atomic(path, data)
