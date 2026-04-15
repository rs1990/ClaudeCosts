# Claude Usage Monitor

A macOS menu bar app that reads your [Claude Code](https://claude.ai/code) session logs and shows live token usage and cost estimates — broken down by today, this month, and all time.

![Menu bar showing ⚡ $0.42 | 38.5K tok](https://placeholder)

## Features

- **Menu bar title** shows today's cost and token count at a glance
- **Today / This Month / All Time** cost, token, and API call totals
- **Per-model breakdown** sorted by cost
- **Auto-refreshes** every 15 minutes
- **Manual refresh** button in the menu
- Runs silently in the background, auto-starts at login via LaunchAgent

## Requirements

- macOS 12 or later
- Python 3.9+
- [Claude Code](https://claude.ai/code) installed and used at least once (session data lives in `~/.claude/projects/`)

## Installation

```bash
git clone https://github.com/your-username/claude-usage-monitor.git
cd claude-usage-monitor
bash install.sh
```

The installer:
1. Detects your Python 3 installation
2. Installs `rumps` and `pyobjc-framework-Cocoa` via pip
3. Copies the script to `~/.claude/`
4. Registers a LaunchAgent so the app starts automatically at login
5. Launches the app immediately

Look for the **⚡** icon in your menu bar.

### Manual run (no auto-start)

If you'd rather not use the LaunchAgent:

```bash
pip install rumps pyobjc-framework-Cocoa
python3 claude_usage_monitor.py
```

## Uninstall

```bash
bash uninstall.sh
```

This stops the app, removes the LaunchAgent, and deletes the installed script. Log files at `~/.claude/usage_monitor*.log` are left in place.

## Pricing

Token prices are hardcoded in `claude_usage_monitor.py` under the `PRICING` dict. Update them if Anthropic changes rates:

```python
PRICING: dict[str, dict] = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, ...},
    ...
}
```

Prices are in USD per million tokens.

## How it works

Claude Code writes every assistant response (including token usage metadata) to JSONL files under `~/.claude/projects/`. This app:

1. Scans all `*.jsonl` files under that directory
2. Extracts `usage` fields from `assistant`-type entries
3. Deduplicates by UUID (subagent files can overlap with the main session file)
4. Multiplies token counts by the per-model price to compute cost

No data leaves your machine. No API calls are made.

## Troubleshooting

| Symptom | Fix |
|---|---|
| ⚡ icon not appearing | Check `~/.claude/usage_monitor_error.log` |
| `$0.00` cost always | Verify `~/.claude/projects/` exists and contains `.jsonl` files |
| `import rumps` fails | Run `python3 -m pip install rumps pyobjc-framework-Cocoa` manually |
| App not starting at login | Run `launchctl load ~/Library/LaunchAgents/com.claude.usage-monitor.plist` |
