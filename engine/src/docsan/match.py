"""Find company references in text: profile terms and fixed patterns.

Matching runs on normalized views of the text (see ``docsan.text``) and maps
each hit back to offsets in the original string.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from itertools import pairwise

from . import steps
from .profile import UNNUMBERED, Profile, Term
from .text import fold_word, normalize

# Two-letter country-code top-level domains assigned by IANA.
CC_TLDS = frozenset(
    """ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo bq br bs
    bt bw by bz ca cc cd cf cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg er es
    et eu fi fj fk fm fo fr ga gb gd ge gf gg gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id
    ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn kp kr kw ky kz la lb lc li lk lr ls lt lu lv
    ly ma mc md me mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na nc ne nf ng ni nl no np nr nu
    nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sk sl sm sn
    so sr ss st su sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc ve
    vg vi vn vu wf ws ye yt za zm zw""".split()  # noqa: SIM905
)

# Generic and internal top-level domains seen in corporate documents.
GENERIC_TLDS = frozenset(
    """com net org info biz name pro mobi edu gov mil int arpa aero asia cat coop jobs museum tel travel
    app dev page cloud tech online site website store shop blog xyz top club live news email digital
    solutions services systems network group global agency company consulting media studio design software
    tools today world zone link click space host bank insurance legal law finance capital ventures partners
    holdings international europe brussels paris amsterdam berlin london swiss bayern wien zip mov
    local internal corp lan intra intranet home localdomain example test invalid localhost""".split()  # noqa: SIM905
)

TLDS = CC_TLDS | GENERIC_TLDS

# Top-level domains that are also common file extensions. A single-dot name
# with one of these endings ("setup.py", "main.tf") goes to review instead of
# being replaced.
AMBIGUOUS_TLDS = frozenset(
    "py md sh rs pl cc so ai ps ml sc tf mk am in pm mo ac gd cr do es zip mov app".split()  # noqa: SIM905
)

_SEP = "[ \\-._]*"
_LETTER_SEP = "[ \\-._]?"
_URL = re.compile(r"(?:\b(?:https?|ftp|file|smb)://|\bwww\.)[^\s<>\"'\]\)]+|\\\\[a-z0-9][a-z0-9.\-]*\\[^\s<>\"']*")
_EMAIL = re.compile(r"(?<![\w.+%\-])[\w.+%\-]+@[\w\-]+(?:\.[\w\-]+)+")
_DOMAIN = re.compile(
    r"(?<![\w@./\\\-])\.?((?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+)([a-z]{2,63})(?![\w\-])"
)
_PHONE_INTL = re.compile(
    r"(?<![\w+])(?:\+|00)\(?\d{1,4}\)?(?:[ .\-/]{0,2}(?:\(0\)|\(\d{1,4}\)|\d{1,5})){1,7}(?!\w)"
)
_PHONE_LOCAL = re.compile(r"(?<![\w.,+])(?:\(\d{2,4}\) ?)?\d{2,4}(?:[ .\-]\d{2,4}){3,5}(?!\w|[.,]\d)")

_CLASSIFICATION = re.compile(r"(?<!\w)c[0-4]\s*[-:]?\s*(?:internal|interne|confidential|confidentiel(?:le)?|restricted|restreint(?:e)?|secret)(?!\w)")

_IP_ADDRESS = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
_HOST_TOKEN = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*(?![\w-])", re.IGNORECASE)
_HOST_SOFTWARE = re.compile(r"(?:postgres(?:ql)?|odoo|python|windows|http|rfc|iso|tls|sha|utf|ipv)[-_]?\d+", re.IGNORECASE)


def _context_hosts(text: str, safe: set[str]) -> list[tuple[int, int]]:
    result = []
    for m in _HOST_TOKEN.finditer(text):
        value = m.group()
        letters = re.sub(r"[^a-z]", "", value.lower())
        if (len(value) > 63 or len(letters) < 4 or not any(c.isdigit() for c in value)
                or letters in safe or _HOST_SOFTWARE.fullmatch(value)):
            continue
        before = text[max(0, m.start()-90):m.start()]
        shell = re.search(r"[a-z_][a-z0-9_-]*@$", before, re.IGNORECASE)
        if _HOST_CONTEXT.search(before) or (shell and re.match(r"[\]\s:#$/]|$", text[m.end():])):
            result.append(m.span())
    return result


def _code_domain_spans(text: str, system_variables: bool) -> list[tuple[int, int]]:
    if "." not in text:
        return []
    spans = []
    # Public Odoo table symbols can be a fragment of a multi-paragraph query.
    quoted = [m.span() for m in re.finditer(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", text)]
    spans += [m.span() for m in re.finditer(r"\b(?:ir_cron|cron_log)\.[a-z_]\w*",text,re.IGNORECASE)
              if not any(a<=m.start()<b for a,b in quoted)]
    tables = {m.group(1).lower() for m in re.finditer(r"\b(?:from|join)\s+([a-z_]\w*)", text, re.IGNORECASE)}
    if re.search(r"\bselect\b", text, re.IGNORECASE):
        for m in re.finditer(r"\b([a-z_]\w*)\.[a-z_]\w*", text, re.IGNORECASE):
            if m.group(1).lower() in tables and not any(a <= m.start() < b for a,b in quoted):
                spans.append(m.span())
    if re.search(r"\b(?:info|debug|warning|error|logger|traceback|starting|cron)\b", text, re.IGNORECASE):
        spans += [m.span() for m in re.finditer(r"\b(?:odoo\.)?(?:addons\.)?base\.models\.[a-z_][\w.]*", text, re.IGNORECASE)]
    if system_variables:
        spans += [m.span() for m in re.finditer(r"\bsystem\.[a-z_][\w.]*", text, re.IGNORECASE)]
    return spans
_HOST_CONTEXT = re.compile(r"\b(?:servers?|serveurs?|host(?:name)?|hote|snapshots?|sftp|ssh|mft|agent|password|vms?|machines?|backup|db|database|production|prod)\b",re.IGNORECASE)

_TICKET_REFERENCE = re.compile(r"(?<!\w)#[ \u00a0]*\d(?:[ \u00a0]?\d){6,14}(?!\w)")
_PUBLIC_SOFTWARE = re.compile(r"(?<!\w)postgres(?:ql)?(?!\w)", re.IGNORECASE)

_IDENTIFIER = re.compile(r"(?<![\w.,])(?:[A-Z]{1,12}[-/]?\d{5,}|\d{4}/\d{2}-\d{3,}|\d{8,})(?!\w|[.,]\d)", re.IGNORECASE)

def numeric_context(text: str, start: int, end: int) -> bool:
    value = text[start:end]
    # OCR sometimes reads a leading icon as "+" beside a calendar date.
    if re.fullmatch(r"[+ ]*(?:0?[1-9]|[12][0-9]|3[01])[/.-](?:0?[1-9]|1[0-2])[/.-](?:19|20)[0-9]{2}", value.strip()):
        return True
    if (re.fullmatch(r"[0-9]+[.,][0-9]{2}(?:\s+[0-9]+[.,][0-9]{2}){2,}", value.strip())
            and re.search(r"\b(?:unit price|prix unitaire|montant|subtotal|total|amount)\b", text, re.IGNORECASE)):
        return True
    if re.search(r"(?:[€$£]|\b(?:eur|usd|gbp)\b)", text[max(0,start-5):min(len(text),end+5)], re.IGNORECASE):
        return True
    parts = re.findall(r"\d+", value)
    # Consecutive chart ticks are not a telephone number.
    if len(parts) >= 3 and len(set(map(len, parts))) == 1:
        nums = list(map(int, parts))
        gaps = [b-a for a,b in pairwise(nums)]
        if len(set(gaps)) == 1 and gaps[0] > 0:
            return True
    return False

# Kinds in priority order when two hits start at the same place.
_PRIORITY = {"url": 0, "email": 1, "term": 2, "domain": 3, "phone": 4, "identifier": 5, "classification": 2}


@dataclass(frozen=True)
class Hit:
    start: int
    end: int
    kind: str
    label: str
    key: str


@dataclass
class MatchResult:
    hits: list[Hit] = field(default_factory=list)
    exempt: list[tuple[int, int, str]] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def _alnum_len(s: str) -> int:
    return sum(1 for c in s if c.isalnum())


def term_regex(term: Term) -> re.Pattern[str]:
    nterm = normalize(term.text, fold_case=not term.case_sensitive)[0].strip()
    flexible = steps.enabled("match-separators")
    letter_sep = flexible and _alnum_len(nterm) >= 4
    pieces: list[str] = []
    prev_alnum = False
    for ch in nterm:
        if ch in " -._" and flexible:
            if pieces and pieces[-1] != _SEP:
                pieces.append(_SEP)
            prev_alnum = False
        else:
            if prev_alnum and letter_sep:
                pieces.append(_LETTER_SEP)
            pieces.append(re.escape(ch))
            prev_alnum = ch.isalnum()
    body = "".join(pieces)
    inside_words = (
        steps.enabled("match-inside-words")
        and not term.whole_word
        and not term.case_sensitive
        and _alnum_len(nterm) >= 5
    )
    if not inside_words:
        left, right = r"(?<![^\W_])", r"(?![^\W_])"
        if term.case_sensitive and steps.enabled("match-camel-case") and nterm[:1].isupper():
            # Inside compound names: "MyAGL", "AGLConnect", "AGLs".
            left = rf"(?:{left}|(?<=[a-z]))"
            right = rf"(?:{right}|(?=[A-Z][a-z])|(?=s(?![^\W_])))"
        body = f"{left}{body}{right}"
    return re.compile(body)


def _span(origin: list[int], start: int, end: int) -> tuple[int, int]:
    return origin[start], origin[end - 1] + 1


def _phone_ok(s: str, low: int, high: int) -> bool:
    digits = sum(c.isdigit() for c in s)
    return low <= digits <= high


def _looks_like_ipv4(s: str) -> bool:
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts)


class Matcher:
    def __init__(self, profile: Profile, safe_words: set[str]):
        self.profile = profile
        self._hosts: set[str] = set()
        self._host_re: re.Pattern[str] | None = None
        self.safe = {fold_word(w) for w in safe_words} | {fold_word(w) for w in profile.safe_words}
        if not profile.redact_system_names:
            self.safe |= {fold_word(t.text) for t in profile.terms if t.label == "SYSTEM"}
        active = [t for t in profile.terms if t.label != "SYSTEM" or profile.redact_system_names]
        self.terms = [(t, term_regex(t)) for t in active]
        # In pictures, letter case is what OCR gets wrong most: small capitals
        # and drawn lettering turn "AGL" into "Agl". A case-sensitive term
        # also matches there in any case but all lower case, as a whole word.
        self.loose = [
            (t, term_regex(Term(t.text, t.label, whole_word=True))) for t in active if t.case_sensitive
        ]
        self.exceptions: dict[str, list[re.Pattern[str]]] = {}
        for e in profile.exceptions:
            self.exceptions.setdefault(e.text, []).extend(re.compile(f) for f in e.followed_by)

    def with_context(self, texts: list[str]) -> Matcher:
        """Document-local host facts; never write them into the company profile."""
        local = copy.copy(self)
        local._hosts = self._hosts | {text[a:b].casefold() for text in texts for a,b in _context_hosts(text, self.safe)}
        local._host_re = (re.compile("(?:" + "|".join(
            term_regex(Term(v,"DOMAIN",whole_word=True)).pattern for v in sorted(local._hosts,key=len,reverse=True)
        ) + ")",re.IGNORECASE) if local._hosts else None)
        return local

    def reviewable(self, text: str, *, system_variables: bool = False) -> str:
        """Known public code symbols do not require saved per-word decisions.

        This affects the unfamiliar-word queue only. Every original symbol still
        goes through company matching and remains searchable in the preview.
        """
        chars = list(text)
        for a,b in _code_domain_spans(text, system_variables):
            token = text[a:b].lower()
            public = ((system_variables and token.startswith("system."))
                      or re.fullmatch(r"(?:odoo\.)?(?:addons\.)?base\.models\.[a-z_][\w.]*", token)
                      or token.split(".",1)[0] in ("ir_cron","cron_log"))
            if public:
                chars[a:b] = " " * (b-a)
        return "".join(chars)

    def find(self, text: str, *, loose_case: bool = False, system_variables: bool = False) -> MatchResult:
        """``loose_case`` is for text read in pictures (see ``loose``)."""
        result = MatchResult()
        if not text:
            return result
        folded, fmap = normalize(text, fold_case=True)
        cased, cmap = normalize(text, fold_case=False)
        hits: list[Hit] = []

        public_software = [m.span() for m in _PUBLIC_SOFTWARE.finditer(text)]
        code_domains = _code_domain_spans(text, system_variables)

        loose = [(t, rx, True) for t, rx in self.loose] if loose_case and steps.enabled("match-loose-case") else []
        for term, rx, any_case in [(t, rx, False) for t, rx in self.terms] + loose:
            view, vmap = (folded, fmap) if any_case or not term.case_sensitive else (cased, cmap)
            for m in rx.finditer(view):
                if m.end() == m.start():
                    continue
                start, end = _span(vmap, m.start(), m.end())
                if any(a <= start < end <= b and (a, b) != (start, end) for a, b in public_software):
                    continue  # a short profile token inside a complete public software name
                if any_case and text[start:end] == text[start:end].lower():
                    continue  # all lower case is an ordinary word; "Agl" may be the name
                after = view[m.end() : m.end() + 60]
                if steps.enabled("apply-exceptions") and any(
                    p.match(after) for p in self.exceptions.get(term.text, [])
                ):
                    result.exempt.append((start, end, term.text))
                    continue
                key = term.group or normalize(term.text)[0].strip()
                hits.append(Hit(start, end, "term", term.label, f"{term.label}:{key}"))

        if steps.enabled("detect-urls"):
            for m in _URL.finditer(folded):
                s = m.group().rstrip(".,;:!?")
                start, end = _span(fmap, m.start(), m.start() + len(s))
                hits.append(Hit(start, end, "url", "URL", "URL:" + s))
        if steps.enabled("detect-emails"):
            for m in _EMAIL.finditer(folded):
                s = m.group().rstrip(".-")
                tld = s.rsplit(".", 1)[-1]
                if tld.isalpha() and len(tld) >= (1 if loose_case else 2):
                    start, end = _span(fmap, m.start(), m.start() + len(s))
                    hits.append(Hit(start, end, "email", "EMAIL", "EMAIL:" + s))
        if steps.enabled("detect-domains"):
            for m in _DOMAIN.finditer(folded):
                s, tld = m.group(), m.group(2)
                if tld not in TLDS or fold_word(s.lstrip(".")) in self.safe:
                    continue
                start, end = _span(fmap, m.start(), m.end())
                if any(a <= start and end <= b for a,b in code_domains):
                    continue
                if tld in AMBIGUOUS_TLDS and s.lstrip(".").count(".") == 1:
                    result.review.append(text[start:end])
                    continue
                hits.append(Hit(start, end, "domain", "DOMAIN", "DOMAIN:" + s))
        if steps.enabled("detect-phones"):
            for m in _PHONE_INTL.finditer(folded):
                if _phone_ok(m.group(), 8, 15) and not numeric_context(folded, m.start(), m.end()):
                    start, end = _span(fmap, m.start(), m.end())
                    hits.append(Hit(start, end, "phone", "PHONE", "PHONE:" + re.sub(r"\D", "", m.group())))
            for m in _PHONE_LOCAL.finditer(folded):
                s = m.group()
                if _phone_ok(s, 8, 12) and not _looks_like_ipv4(s) and not numeric_context(folded, m.start(), m.end()):
                    start, end = _span(fmap, m.start(), m.end())
                    hits.append(Hit(start, end, "phone", "PHONE", "PHONE:" + re.sub(r"\D", "", s)))

        if steps.enabled("detect-classification"):
            for m in _CLASSIFICATION.finditer(folded):
                start, end = _span(fmap, m.start(), m.end())
                hits.append(Hit(start, end, "classification", "LABEL", "LABEL:" + m.group()))
        if steps.enabled("detect-identifiers"):
            for m in _IP_ADDRESS.finditer(folded):
                before=folded[max(0,m.start()-16):m.start()]
                if all(int(v)<=255 for v in m.group().split(".")) and not re.search(r"(?:version|release|v)\s*$",before):
                    start,end=_span(fmap,m.start(),m.end())
                    hits.append(Hit(start,end,"identifier","DOMAIN","DOMAIN:"+m.group()))
            hosts = _context_hosts(folded, self.safe)
            if self._host_re:
                hosts += [m.span() for m in self._host_re.finditer(folded)]
            for a,b in hosts:
                start,end=_span(fmap,a,b)
                hits.append(Hit(start,end,"identifier","DOMAIN","DOMAIN:"+folded[a:b]))
            for m in _TICKET_REFERENCE.finditer(cased):
                start, end = _span(cmap, m.start(), m.end())
                hits.append(Hit(start, end, "identifier", "ID", "ID:" + re.sub(r"\D", "", m.group())))
            for m in _IDENTIFIER.finditer(cased):
                if not numeric_context(cased, m.start(), m.end()):
                    start, end = _span(cmap, m.start(), m.end())
                    hits.append(Hit(start, end, "identifier", "ID", "ID:" + m.group().casefold()))
        result.hits = _merge(hits)
        return result


def _merge(hits: list[Hit]) -> list[Hit]:
    """Resolve overlaps: the earliest, longest hit wins and absorbs the rest."""
    hits = sorted(hits, key=lambda h: (h.start, -(h.end - h.start), _PRIORITY[h.kind]))
    merged: list[Hit] = []
    for h in hits:
        if merged and h.start < merged[-1].end:
            last = merged[-1]
            if h.end > last.end:
                merged[-1] = Hit(last.start, h.end, last.kind, last.label, last.key)
            continue
        merged.append(h)
    return merged


class Labeler:
    """Assign typed, numbered labels that stay the same within one batch."""

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}
        self._counts: dict[str, int] = {}

    def label(self, hit: Hit) -> str:
        if hit.label in UNNUMBERED:
            return f"[{hit.label}]"
        if hit.key not in self._numbers:
            self._counts[hit.label] = self._counts.get(hit.label, 0) + 1
            self._numbers[hit.key] = self._counts[hit.label]
        return f"[{hit.label}-{self._numbers[hit.key]}]"
