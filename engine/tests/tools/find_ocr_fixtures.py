"""Search for images that only one Vision pass reads correctly.

The ``script-code`` and ``handwritten-plural`` image fixtures prove that the
uncorrected pass and the alternate readings are needed. Which images show
that depends on the Vision model in macOS, so after an update that breaks
those fixtures, run ``uv run python tests/tools/find_ocr_fixtures.py`` and
copy a reported case into ``tests/imagematrix.py``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imagekit
import leakmatrix as lm

from docsan.image.text import read_text
from docsan.match import Matcher
from docsan.profile import load_profile
from docsan.review import glossary_words

FONTS = ["Snell Roundhand", "Bradley Hand", "Zapfino", "Chalkduster", "Savoye LET", "Apple Chancery", "Brush Script MT", "SignPainter"]
TEXTS = ["the AGL app", "AGLs stable", "MyAGL portal", "Ticker AGL closed", "AGLConnect hub", "Aurelan approved", "Marchetti signed"]


def main() -> None:
    imagekit.helper()
    matcher = Matcher(load_profile(lm.PROFILE), glossary_words())
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.png"
        for font in FONTS:
            for text in TEXTS:
                for size in (22, 26, 32):
                    width, height, y = 40 + int(size * 0.75 * len(text)) + 80, int(size * 3), size * 0.8
                    imagekit.render({"output": str(path), "format": "png", "width": width, "height": height,
                                     "items": [{"text": text, "x": 40, "y": y, "size": size, "font": font}]})
                    lines = read_text(path).lines
                    found = [(line.corrected, rank) for line in lines for rank, r in enumerate(line.readings) if matcher.find(r.text).hits]
                    case = f"line({text!r}, {y}, {size}, font={font!r}), size=({width}, {height})"
                    if found and all(not corrected for corrected, _ in found):
                        print("uncorrected only:", case)
                    if found and all(rank > 0 for _, rank in found):
                        print("alternates only: ", case)


if __name__ == "__main__":
    main()
