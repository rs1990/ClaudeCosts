#!/usr/bin/env bash
# Claude Usage Monitor — installer
# Points the LaunchAgent directly at this repo so edits are always live.
# Also injects a PreToolUse hook so the monitor auto-starts with every Claude session.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$REPO_DIR/claude_usage_monitor.py"
PLIST_LABEL="com.claude.usage-monitor"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
CLAUDE_DIR="$HOME/.claude"
SETTINGS="$CLAUDE_DIR/settings.json"

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

[[ -z "$PYTHON" ]] && { echo "ERROR: python3 not found."; exit 1; }
echo "Using Python: $PYTHON  ($(${PYTHON} --version))"

# ── 2. Install Python dependencies ───────────────────────────────────────────

echo "Installing dependencies (rumps + pyobjc + matplotlib)…"
"$PYTHON" -m pip install --quiet -r "$REPO_DIR/requirements.txt"
"$PYTHON" -c "import rumps" 2>/dev/null || {
    echo "ERROR: rumps not importable after install."
    echo "Try: $PYTHON -m pip install rumps pyobjc-framework-Cocoa matplotlib"
    exit 1
}

# ── 3. Write LaunchAgent plist (points directly at repo source) ───────────────

mkdir -p "$HOME/Library/LaunchAgents"
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
        <string>${SCRIPT}</string>
    </array>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>${CLAUDE_DIR}/usage_monitor.log</string>
    <key>StandardErrorPath</key>
    <string>${CLAUDE_DIR}/usage_monitor_error.log</string>

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

# ── 4. Inject PreToolUse hook into ~/.claude/settings.json ───────────────────

mkdir -p "$CLAUDE_DIR"
HOOK_CMD="pgrep -f 'claude_usage_monitor.py' > /dev/null 2>&1 || nohup ${PYTHON} '${SCRIPT}' >> ${CLAUDE_DIR}/usage_monitor.log 2>&1 &"

if [[ ! -f "$SETTINGS" ]]; then
    cat > "$SETTINGS" <<JSON
{
  "hooks": {
    "PreToolUse": [
      { "hooks": [{ "type": "command", "timeout": 3, "command": "$HOOK_CMD" }] }
    ]
  }
}
JSON
    echo "Created $SETTINGS with PreToolUse hook."
else
    if ! grep -q "claude_usage_monitor" "$SETTINGS" 2>/dev/null; then
        "$PYTHON" - "$SETTINGS" "$HOOK_CMD" <<'PY'
import json, sys
path, cmd = sys.argv[1], sys.argv[2]
s = json.loads(open(path).read())
hooks = s.setdefault("hooks", {})
pre   = hooks.setdefault("PreToolUse", [])
pre.insert(0, {"hooks": [{"type": "command", "timeout": 3, "command": cmd}]})
open(path, "w").write(json.dumps(s, indent=2))
print("PreToolUse hook added to", path)
PY
    else
        echo "PreToolUse hook already present in $SETTINGS — skipped."
    fi
fi

# ── 5. Load / reload the LaunchAgent ─────────────────────────────────────────

launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load  "$PLIST_DEST"

echo ""
echo "Done! Claude Usage Monitor is running and will:"
echo "  • Start automatically at login        (LaunchAgent)"
echo "  • Auto-restart on any Claude session  (PreToolUse hook in settings.json)"
echo "  • Always run from: $SCRIPT"
echo ""
echo "To uninstall: bash $REPO_DIR/uninstall.sh"
