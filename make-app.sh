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

# start clean: a previous py2app build may leave symlinks/artifacts that break
# the plain file copies below (e.g. a symlinked AppIcon.icns)
rm -rf "$APP"
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
  <key>CFBundleShortVersionString</key><string>1.5</string>
  <key>CFBundleVersion</key><string>1.5</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>Orac Voice needs the microphone to transcribe your dictation locally.</string>
</dict>
</plist>
PLIST

# The Dock attributes a running process to a bundle. Homebrew's Python is a
# framework build that re-execs through its own Python.app, so without a hint
# the Dock shows "Python". __CFBundleIdentifier makes CoreFoundation treat this
# bundle as the main one, so the running dot, bounce and icon attach to Orac
# Voice. flow.py also sets the Dock icon/name at runtime as a belt-and-braces.
cat > "$APP/Contents/MacOS/orac" <<LAUNCHER
#!/bin/bash
cd "$DIR"
mkdir -p .tmp
export __CFBundleIdentifier="com.davidt.oracvoice"
exec .venv/bin/python -u flow.py >> .tmp/orac.log 2>&1
LAUNCHER
chmod +x "$APP/Contents/MacOS/orac"

cp "$DIR/assets/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# ad-hoc signature: without it TCC has no stable identity and the permission
# entries get attributed to "Python" or the Terminal
codesign --force --deep --sign - "$APP"

echo "Done: $APP"
echo "Open it from Applications; macOS will request permissions as 'Orac Voice'."
