#!/bin/bash
# WARNING: the shipped app icon is the hand-made lime mic (AppIcon.icns /
# OracVoice.ico committed as artifacts, no >512px source). This script
# regenerates from assets/logo-icon.png (the brand isotipo, which David chose
# NOT to use as the app icon) and WILL overwrite the lime mic. Only run it if
# you actually want the isotipo back.
#
# Regenerates the app icons from the single source PNG (assets/logo-icon.png,
# the isotipo). Run this whenever the logo changes; then run make-app.sh (Mac)
# so the new .icns is copied into the bundle.
#   - assets/AppIcon.icns        (Mac: Dock, Finder, app tile)
#   - windows/assets/OracVoice.ico (Windows: taskbar, shortcut)
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$DIR/assets/logo-icon.png"
[ -f "$SRC" ] || { echo "Missing $SRC (the isotipo)"; exit 1; }

# --- macOS .icns (sips + iconutil, no deps) ---
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z "$s" "$s" "$SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2))
  sips -z "$d" "$d" "$SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$DIR/assets/AppIcon.icns"
echo "Wrote assets/AppIcon.icns"

# --- Windows .ico (Pillow) ---
"$DIR/.venv/bin/python" - "$SRC" "$DIR/windows/assets/OracVoice.ico" <<'PY'
import sys
from PIL import Image
src, out = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGBA")
img.save(out, format="ICO",
         sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("Wrote windows/assets/OracVoice.ico")
PY
