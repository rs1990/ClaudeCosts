#!/usr/bin/env bash
# Claude Usage Monitor — installer
# Installs the menu bar app and sets it to launch at login.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="claude_usage_monitor.py"
DEST_DIR="$HOME/.claude"
DEST_SCRIPT="$DEST_DIR/$SCRIPT_NAME"
PLIST_LABEL="com.claude.usage-monitor"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

# ── 1. Locate python3 ─────────────────────────────────────────────────────────

PYTHON=""
for candidate in \
    "$(command -v python3 2>/dev/null)" \
    /usr/bin/python3 \
    /usr/local/bin/python3 \
    "$HOME/anaconda3/bin/python3" \
    "$HOME/miniconda3/bin/python3" \
    "$HOME/.pyenv/shims/python3"
do
    if [[ -x "$candidate" ]]; then
        PYTHON="$candidate"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "ERROR: python3 not found. Install Python 3 first (https://www.python.org/downloads/)."
    exit 1
fi

echo "Using Python: $PYTHON  ($(${PYTHON} --version))"

# ── 2. Install Python dependencies ───────────────────────────────────────────

echo "Installing dependencies (rumps + pyobjc + matplotlib)…"
"$PYTHON" -m pip install --quiet -r "$REPO_DIR/requirements.txt"

# Verify rumps is importable with the chosen python
if ! "$PYTHON" -c "import rumps" 2>/dev/null; then
    echo "ERROR: 'rumps' could not be imported after installation."
    echo "Try running:  $PYTHON -m pip install rumps pyobjc-framework-Cocoa matplotlib"
    exit 1
fi

# ── 3. Copy script ────────────────────────────────────────────────────────────

mkdir -p "$DEST_DIR"
cp "$REPO_DIR/$SCRIPT_NAME" "$DEST_SCRIPT"
echo "Script installed to: $DEST_SCRIPT"

# ── 4. Create LaunchAgent plist ───────────────────────────────────────────────

mkdir -p "$HOME/Library/LaunchAgents"

# Resolve the PATH that should be used in the plist (include Python's directory)
PYTHON_DIR="$(dirname "$PYTHON")"
LAUNCH_PATH="${PYTHON_DIR}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cat > "$PLIST_DEST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>

    <key>Label</key>
    <string>${PLIST_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${DEST_SCRIPT}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>${DEST_DIR}/usage_monitor.log</string>
    <key>StandardErrorPath</key>
    <string>${DEST_DIR}/usage_monitor_error.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${LAUNCH_PATH}</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>

</dict>
</plist>
PLIST

echo "LaunchAgent plist written to: $PLIST_DEST"

# ── 5. Load / reload the LaunchAgent ─────────────────────────────────────────

# Unload any existing job silently before reloading
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo ""
echo "Done! Claude Usage Monitor is running and will start automatically at login."
echo "Look for the ⚡ icon in your macOS menu bar."
echo ""
echo "To run manually without auto-start:  $PYTHON $DEST_SCRIPT"
echo "To uninstall:                         bash $REPO_DIR/uninstall.sh"
