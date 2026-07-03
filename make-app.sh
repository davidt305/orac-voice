#!/bin/bash
# Builds "/Applications/Orac Voice.app" pointing at THIS copy of the repo (macOS).
# With the app, macOS permissions (Microphone, Accessibility, Input Monitoring)
# are requested and listed as "Orac Voice" instead of Terminal/Python.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Orac Voice.app"

[ -x "$DIR/.venv/bin/python" ] || {
  echo "Missing .venv/ in $DIR: run steps 1-3 of INSTALL-MAC.md first"
  exit 1
}

mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Orac Voice</string>
  <key>CFBundleDisplayName</key><string>Orac Voice</string>
  <key>CFBundleIdentifier</key><string>com.davidt.oracvoice</string>
  <key>CFBundleExecutable</key><string>orac</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Orac Voice needs the microphone to transcribe your dictation locally.</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/orac" <<LAUNCHER
#!/bin/bash
cd "$DIR"
mkdir -p .tmp
exec .venv/bin/python -u flow.py >> .tmp/orac.log 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/orac"

cp "$DIR/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# ad-hoc signature: without it TCC has no stable identity and the permission
# entries get attributed to "Python" or the Terminal
codesign --force --deep --sign - "$APP"

echo "Done: $APP"
echo "Open it from Applications; macOS will request permissions as 'Orac Voice'."
