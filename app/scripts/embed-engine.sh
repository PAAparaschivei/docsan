#!/bin/bash
# Xcode build phase: copy build/engine-bundle (already signed by
# bundle-engine.sh) into the app. Xcode skips its own signing step on
# incremental builds, so when the copy changes anything the app is sealed
# again here.
set -euo pipefail

SRC="$SRCROOT/build/engine-bundle"
DEST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/engine"
if [ ! -x "$SRC/python/bin/python3" ]; then
  echo "warning: no engine bundle; run app/scripts/bundle-engine.sh. The app will say the engine is missing."
  rm -rf "$DEST"
  exit 0
fi
CHANGES="$(rsync -a --delete --checksum --itemize-changes "$SRC/" "$DEST/")"
APP="$CODESIGNING_FOLDER_PATH"
XCENT="$TARGET_TEMP_DIR/$PRODUCT_NAME.app.xcent"
if [ -n "$CHANGES" ] && [ -d "$APP/Contents/_CodeSignature" ] && [ -f "$XCENT" ]; then
  codesign --force --sign "${EXPANDED_CODE_SIGN_IDENTITY:--}" --timestamp=none --options runtime \
    --entitlements "$XCENT" "$APP"
fi
