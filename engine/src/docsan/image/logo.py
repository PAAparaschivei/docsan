"""Find the same logo again: fingerprints of what a picture draws.

The helper's ``print`` command reduces a picture to 63 bits that survive a
new size, margin, compression or slight colour change (see ``fingerprint``
in ``app/Helpers/docsan-vision``), plus the width over the height of its
ink. Two pictures show the same logo when at most ``MAX_BITS`` bits differ
and their shapes are within ``MAX_ASPECT`` of each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .helper import call

MAX_BITS = 10
MAX_ASPECT = 0.15


@dataclass(frozen=True)
class Print:
    bits: int
    aspect: float

    @property
    def hex(self) -> str:
        return f"{self.bits:016x}"


def fingerprint(path: Path, crop=None) -> Print | None:
    """The picture's fingerprint, or None when it draws nothing."""
    request: dict = {"command": "print", "input": str(path)}
    if crop and any(crop):
        request["crop"] = list(crop)
    answer = call(request)
    if not answer.get("print"):
        return None
    return Print(int(answer["print"], 16), float(answer["aspect"]))


def distance(a: Print, b: Print) -> int:
    return (a.bits ^ b.bits).bit_count()


def same(a: Print, b: Print) -> bool:
    if distance(a, b) > MAX_BITS:
        return False
    return abs(a.aspect - b.aspect) <= MAX_ASPECT * max(a.aspect, b.aspect)
