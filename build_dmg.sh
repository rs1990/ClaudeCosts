#!/usr/bin/env bash
# Build ClaudeCosts.app with py2app, then package it into a DMG.
set -euo pipefail

APP_NAME="ClaudeCosts"
DMG_NAME="${APP_NAME}.dmg"
BUILD_DIR="dist"

echo "==> Installing build deps…"
pip install --quiet py2app

echo "==> Cleaning previous build…"
rm -rf build dist

echo "==> Building .app with py2app…"
python setup.py py2app 2>&1

APP_PATH="${BUILD_DIR}/${APP_NAME}.app"
if [[ ! -d "$APP_PATH" ]]; then
    echo "ERROR: .app not found at $APP_PATH"
    exit 1
fi

echo "==> Creating DMG…"
# Use create-dmg if available; fall back to plain hdiutil
if command -v create-dmg &>/dev/null; then
    create-dmg \
        --volname "$APP_NAME" \
        --window-size 540 380 \
        --icon-size 128 \
        --icon "${APP_NAME}.app" 160 160 \
        --app-drop-link 380 160 \
        --no-internet-enable \
        "${DMG_NAME}" \
        "${BUILD_DIR}/"
else
    # Minimal fallback: plain hdiutil
    hdiutil create \
        -volname "$APP_NAME" \
        -srcfolder "${BUILD_DIR}" \
        -ov -format UDZO \
        "${DMG_NAME}"
fi

echo ""
echo "Done → ${DMG_NAME}"
