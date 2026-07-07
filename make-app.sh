#!/bin/bash
# Builds "/Applications/Orac Voice.app" from THIS repo with py2app (alias mode).
# Alias mode references the repo source + .venv in place (it does NOT copy the
# heavy deps: onnxruntime, sounddevice, pyobjc, WebKit), so the app stays in
# sync with the source and is small. Because it's a real bundle whose main
# executable is a native stub, the Dock shows "Orac Voice" (icon, running dot,
# bounce) natively instead of "Python", and macOS permissions (Microphone,
# Accessibility, Input Monitoring) are attributed to "Orac Voice".
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Orac Voice.app"

[ -x "$DIR/.venv/bin/python" ] || {
  echo "Missing .venv/ in $DIR: run steps 1-3 of INSTALL-MAC.md first"
  exit 1
}

cd "$DIR"

# clean prior artifacts so a stale bundle never ships
rm -rf build "dist/Orac Voice.app"
.venv/bin/python setup.py py2app -A

# install into /Applications (replace any previous copy)
rm -rf "$APP"
cp -R "dist/Orac Voice.app" "$APP"

# ad-hoc signature with the same bundle id (com.davidt.oracvoice): gives TCC a
# stable identity so the Microphone/Accessibility grants stick to "Orac Voice".
codesign --force --deep --sign - "$APP"

echo "Done: $APP"
echo "Open it from Applications; macOS will request permissions as 'Orac Voice'."
