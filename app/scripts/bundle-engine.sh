#!/bin/bash
# Prepare build/engine-bundle: a relocatable CPython runtime plus the engine
# and its dependencies, the Apple Vision helper and Tesseract. The Xcode build
# embeds it (embed-engine.sh).
#
# Needs a uv-managed CPython 3.13 (python-build-standalone), which can be
# copied into an app bundle. Install it once with: uv python install 3.13
# Tesseract comes from Homebrew: brew install tesseract
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE_DIR="$APP_DIR/../engine"
OUT="$APP_DIR/build/engine-bundle"
VERSION=3.13

PYTHON="$(uv python find --managed-python --no-project "$VERSION" 2>/dev/null || true)"
if [ -z "$PYTHON" ]; then
  echo "error: no uv-managed CPython $VERSION. Run: uv python install $VERSION" >&2
  exit 1
fi
PY_ROOT="$(cd "$(dirname "$(readlink -f "$PYTHON")")/.." && pwd)"
STDLIB="lib/python$VERSION"

rm -rf "$OUT"
mkdir -p "$OUT/python"
rsync -a \
  --exclude 'include/' --exclude 'share/' --exclude '__pycache__/' \
  --exclude "$STDLIB/test/" --exclude "$STDLIB/idlelib/" --exclude "$STDLIB/tkinter/" \
  --exclude "$STDLIB/turtledemo/" --exclude "$STDLIB/ensurepip/" --exclude "$STDLIB/site-packages/" \
  --exclude "$STDLIB/config-$VERSION-darwin/" --exclude 'lib/pkgconfig/' --exclude 'lib/tcl*' --exclude 'lib/tk*' \
  --exclude 'lib/itcl*' --exclude 'lib/thread*' --exclude 'lib/libtcl*' --exclude 'lib/libtk*' --exclude "$STDLIB/lib-dynload/_tkinter*" \
  "$PY_ROOT/" "$OUT/python/"
# Keep only the interpreter in bin/.
find "$OUT/python/bin" -mindepth 1 ! -name "python$VERSION" ! -name python3 -delete
ln -sf "python$VERSION" "$OUT/python/bin/python3"

(cd "$ENGINE_DIR" && uv build --wheel --quiet --out-dir "$OUT/wheel")
# Bundle the same runtime dependency versions the synthetic suite tests.
# Hash checking also rejects an unexpected downloaded dependency artifact.
(cd "$ENGINE_DIR" && uv export --locked --no-dev --no-emit-project --no-header \
  --format requirements-txt --output-file "$OUT/runtime-requirements.txt" >/dev/null)
uv pip install --quiet --python "$OUT/python/bin/python3" --target "$OUT/site" \
  --require-hashes -r "$OUT/runtime-requirements.txt"
uv pip install --quiet --no-deps --python "$OUT/python/bin/python3" --target "$OUT/site" "$OUT"/wheel/*.whl
rm -rf "$OUT/wheel" "$OUT/site/bin"

cat > "$OUT/launch.py" <<'PY'
"""Entry point for the app's engine helper."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).with_name("site")))
from docsan.bridge import main  # noqa: E402

raise SystemExit(main())
PY

"$OUT/python/bin/python3" -m compileall -q -j 0 "$OUT/site" "$OUT/python/$STDLIB" >/dev/null || true

# Image tools: the Apple Vision helper and Tesseract for the verifier.
mkdir -p "$OUT/bin"
xcrun swiftc -O -target arm64-apple-macos26.0 -o "$OUT/bin/docsan-vision" "$APP_DIR"/Helpers/docsan-vision/*.swift \
  -Xlinker -sectcreate -Xlinker __TEXT -Xlinker __info_plist -Xlinker "$APP_DIR/Helpers/docsan-vision/Info.plist"
"$APP_DIR/scripts/fetch-tessdata.sh"
python3 "$APP_DIR/scripts/bundle-ocr.py" "$OUT" "$APP_DIR/build/tessdata"

# Sign every binary now (ad hoc, for local builds) so the embed step is a
# plain copy. The interpreter and the image tools get the sandbox-inherit
# entitlements.
IDENTITY="${DOCSAN_SIGN_IDENTITY:--}"
find "$OUT" -type f \( -name '*.so' -o -name '*.dylib' \) -print0 \
  | xargs -0 -n 50 codesign --force --sign "$IDENTITY" --timestamp=none
for exe in "$OUT/python/bin/python$VERSION" "$OUT/bin/docsan-vision" "$OUT/bin/tesseract"; do
  codesign --force --sign "$IDENTITY" --timestamp=none \
    --entitlements "$APP_DIR/scripts/engine-helper.entitlements" "$exe"
done
echo "engine bundle: $OUT ($(du -sh "$OUT" | cut -f1))"
