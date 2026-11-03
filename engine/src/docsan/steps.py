"""Named sanitizer steps that tests can switch off one at a time.

The mutation tests disable each step in turn and require at least one check
to fail. That proves every step is exercised by a fixture. The verifier keeps
its own, separate registry in ``docsan.verify``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

SANITIZER_STEPS: tuple[str, ...] = (
    "strip-zero-width",
    "map-confusables",
    "fold-accents",
    "fold-compatibility",
    "map-dashes",
    "map-spaces",
    "fold-case",
    "match-separators",
    "match-inside-words",
    "match-camel-case",
    "apply-exceptions",
    "detect-emails",
    "detect-urls",
    "detect-domains",
    "detect-phones",
    "detect-identifiers",
    "detect-classification",
    "drop-deleted-text",
    "drop-field-codes",
    "drop-hidden-text",
    "map-fonts",
)

# Image steps, proven by the image fixtures.
IMAGE_STEPS: tuple[str, ...] = (
    "ocr-orientation",
    "ocr-rotations",
    "ocr-uncorrected",
    "ocr-alternates",
    "ocr-tiles",
    "mask-padding",
    "mask-fit-ink",
    "match-loose-case",
    "rebuild-jpeg",
)

# Steps that remove whole pictures from Word files, proven by tests/test_logos.py.
PICTURE_STEPS: tuple[str, ...] = (
    "remove-margin-pictures",
    "match-logos",
)

# PDF steps, proven by the PDF fixtures (tests/pdfmatrix.py).
PDF_STEPS: tuple[str, ...] = (
    "pdf-text-layer",
    "pdf-sharp-ocr",
    "pdf-visible-text",
    "pdf-check-text-layer",
    "pdf-join-lines",
    "pdf-sensitive-fields",
    "pdf-visual-regions",
)

_disabled: set[str] = set()


def enabled(step: str) -> bool:
    if step not in SANITIZER_STEPS + IMAGE_STEPS + PICTURE_STEPS + PDF_STEPS:
        raise KeyError(f"unknown sanitizer step: {step}")
    return step not in _disabled


@contextmanager
def disabled(step: str) -> Iterator[None]:
    """Disable one step for the duration of a test."""
    if step not in SANITIZER_STEPS + IMAGE_STEPS + PICTURE_STEPS + PDF_STEPS:
        raise KeyError(f"unknown sanitizer step: {step}")
    _disabled.add(step)
    try:
        yield
    finally:
        _disabled.discard(step)
