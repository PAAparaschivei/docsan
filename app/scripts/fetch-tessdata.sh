#!/bin/bash
# Prepare build/tessdata: the Tesseract language data DocSan ships. English
# comes with Homebrew's Tesseract; French is downloaded once from the
# tessdata_fast release 4.1.0 (Apache-2.0, the same model family as the
# English file) and checked against a pinned SHA-256.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$APP_DIR/build/tessdata"
PREFIX="${HOMEBREW_PREFIX:-/opt/homebrew}"
FRA_URL="https://github.com/tesseract-ocr/tessdata_fast/raw/4.1.0/fra.traineddata"
FRA_SHA256="ced037562e8c80c13122dece28dd477d399af80911a28791a66a63ac1e3445ca"

mkdir -p "$OUT"
cp "$PREFIX/share/tessdata/eng.traineddata" "$OUT/eng.traineddata"
if [ ! -f "$OUT/fra.traineddata" ] || [ "$(shasum -a 256 "$OUT/fra.traineddata" | cut -d' ' -f1)" != "$FRA_SHA256" ]; then
  curl -fsSL --max-time 300 -o "$OUT/fra.traineddata.part" "$FRA_URL"
  if [ "$(shasum -a 256 "$OUT/fra.traineddata.part" | cut -d' ' -f1)" != "$FRA_SHA256" ]; then
    rm -f "$OUT/fra.traineddata.part"
    echo "error: fra.traineddata does not match the pinned SHA-256" >&2
    exit 1
  fi
  mv "$OUT/fra.traineddata.part" "$OUT/fra.traineddata"
fi
echo "tessdata: $(cd "$OUT" && /bin/ls *.traineddata | tr '\n' ' ')"
