"""Replace references in the content model with typed labels.

People's names follow the profile's ``person_style``. With
"first_name_initial" (decision D-021) a run of person matches separated only
by spaces becomes the first name and the family-name initial: "Paul Andrei
Aparaschivei" becomes "Paul A.", "APARASCHIVEI Paul" becomes "Paul A." (a
family name in capitals is recognised) and a single name becomes its initial.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .docx.model import Document, Run
from .match import Hit, Labeler, Matcher

_BETWEEN_NAMES = re.compile(r"[ \t\u00a0\u2009\u202f]+")


def _initial(token: str) -> str:
    letter = next((c for c in token if c.isalpha()), token[:1])
    return letter.upper() + "."


def first_name_initial(span: str) -> str:
    tokens = span.split()
    if len(tokens) == 1:
        return _initial(tokens[0])
    capitals = [t for t in tokens if len(t) > 1 and t.isupper()]
    if len(capitals) == 1 and len(capitals) < len(tokens):
        given = next(t for t in tokens if t is not capitals[0])
        return f"{given} {_initial(capitals[0])}"
    return f"{tokens[0]} {_initial(tokens[-1])}"


@dataclass
class SanitizeReport:
    replacements: int = 0
    by_label: dict[str, int] = field(default_factory=dict)
    exempt: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def _replacements(text: str, hits: list[Hit], labeler: Labeler, style: str) -> list[tuple[int, int, str, str]]:
    """(start, end, replacement, label) for every hit; person runs join up."""
    out: list[tuple[int, int, str, str]] = []
    i = 0
    while i < len(hits):
        hit = hits[i]
        if hit.label == "PERSON" and style == "first_name_initial":
            end = hit.end
            while i + 1 < len(hits) and hits[i + 1].label == "PERSON" and _BETWEEN_NAMES.fullmatch(text[end : hits[i + 1].start]):
                i += 1
                end = hits[i].end
            out.append((hit.start, end, first_name_initial(text[hit.start : end]), "PERSON"))
        else:
            out.append((hit.start, hit.end, labeler.label(hit), hit.label))
        i += 1
    return out


def _rewrite(runs: list[Run], matcher: Matcher, labeler: Labeler, report: SanitizeReport) -> list[Run]:
    text = "".join(r.text for r in runs)
    result = matcher.find(text)
    report.exempt += [text[s:e] for s, e, _ in result.exempt]
    report.review += result.review
    if not result.hits:
        return runs
    # Owner run for each character, so replacements keep the formatting of their first character.
    owner: list[int] = []
    for i, r in enumerate(runs):
        owner.extend([i] * len(r.text))
    out: list[Run] = []

    def emit(s: str, template: Run) -> None:
        if not s:
            return
        if out and out[-1].same_format(template):
            out[-1] = Run(out[-1].text + s, template.fmt, template.field, template.ours)
        else:
            out.append(Run(s, template.fmt, template.field, template.ours))

    def emit_range(a: int, b: int) -> None:
        i = a
        while i < b:
            j = i
            while j < b and owner[j] == owner[i]:
                j += 1
            emit(text[i:j], runs[owner[i]])
            i = j

    pos = 0
    for start, end, replacement, label in _replacements(text, result.hits, labeler, matcher.profile.person_style):
        emit_range(pos, start)
        emit(replacement, runs[owner[start]])
        report.replacements += 1
        report.by_label[label] = report.by_label.get(label, 0) + 1
        pos = end
    emit_range(pos, len(text))
    return out


def sanitize(doc: Document, matcher: Matcher, labeler: Labeler) -> SanitizeReport:
    """Every paragraph in the body, headers and footers, and the text of list numbers."""
    report = SanitizeReport()
    for p in doc.paragraphs():
        p.runs = _rewrite(p.runs, matcher, labeler, report)
    levels = [lvl for d in doc.numbering.definitions for lvl in d.levels]
    levels += [lvl for inst in doc.numbering.instances for _, _, lvl in inst.overrides if lvl is not None]
    for lvl in levels:
        lvl.text = "".join(r.text for r in _rewrite([Run(lvl.text)], matcher, labeler, report))
    return report


def plain_text(doc: Document) -> list[str]:
    """Paragraph texts in the order the writer emits them: body, then each header and footer."""
    return [p.text for p in doc.paragraphs()]


def review_text(doc: Document) -> list[str]:
    """The document's own words, without the notes DocSan writes about charts and diagrams."""
    return ["".join(r.text for r in p.runs if not r.ours) for p in doc.paragraphs()]

