#!/usr/bin/env bash
# Claude Usage Monitor — uninstaller
set -euo pipefail

PLIST_LABEL="com.claude.usage-monitor"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
DEST_SCRIPT="$HOME/.claude/claude_usage_monitor.py"

echo "Uninstalling Claude Usage Monitor…"

# Stop and remove the LaunchAgent
if [[ -f "$PLIST_PATH" ]]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "Removed LaunchAgent plist."
else
    echo "LaunchAgent plist not found (already removed?)."
fi

# Remove the installed script
if [[ -f "$DEST_SCRIPT" ]]; then
    rm -f "$DEST_SCRIPT"
    echo "Removed script: $DEST_SCRIPT"
else
    echo "Script not found (already removed?)."
fi

echo ""
echo "Uninstalled. Log files (if any) were left at ~/.claude/usage_monitor*.log"
