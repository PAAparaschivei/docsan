import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Read images with the language data the app ships (app/scripts/fetch-tessdata.sh),
# when it has been prepared; otherwise Tesseract's own English data.
_TESSDATA = Path(__file__).resolve().parents[2] / "app" / "build" / "tessdata"
if (_TESSDATA / "fra.traineddata").exists():
    os.environ.setdefault("DOCSAN_TESSDATA", str(_TESSDATA))

# Every test sees the same engine: the Vision helper is built once (cached by
# source hash) and found through DOCSAN_VISION, so a picture in a Word file is
# always read the way the app reads it.
import imagekit

imagekit.helper()
