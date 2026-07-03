#!/bin/bash
# Crea "/Applications/Orac Voice.app" apuntando a ESTA copia del repo (macOS).
# Con la app, los permisos de macOS (Micrófono, Accessibility, Input
# Monitoring) se piden y se listan como "Orac Voice", no como Terminal/Python.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="/Applications/Orac Voice.app"

[ -x "$DIR/.venv/bin/python" ] || {
  echo "Falta .venv/ en $DIR: corre primero los pasos 1-3 de INSTALL-MAC.md"
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
  <string>Orac Voice necesita el micrófono para transcribir tu dictado localmente.</string>
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

# firma ad-hoc: sin esto TCC no tiene identidad estable y los permisos
# aparecen atribuidos a "Python" o a la Terminal
codesign --force --deep --sign - "$APP"

echo "Listo: $APP"
echo "Ábrela desde Aplicaciones; macOS pedirá los permisos como 'Orac Voice'."
