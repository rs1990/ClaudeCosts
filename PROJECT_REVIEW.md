# ClaudeCosts — Project Review

Reviewed 2026-06-10. Scope: `claude_usage_monitor.py` (718 lines), `install.sh`, `uninstall.sh`, `requirements.txt`, `README.md`, `LICENSE`.

Verification performed:
- `python3 -m py_compile claude_usage_monitor.py` — PASS
- `bash -n install.sh` / `bash -n uninstall.sh` — PASS
- Dependencies not installed (per review constraints); runtime behavior not exercised.

---

## Overview

A single-file macOS menu bar app (rumps + PyObjC + matplotlib) that scans Claude Code JSONL session logs under `~/.claude/projects/`, computes token costs from a hardcoded pricing table, and shows Today / Month / All-time totals plus an inline usage graph and Anthropic status. Installed via a LaunchAgent and an auto-restart `PreToolUse` hook injected into `~/.claude/settings.json`.

The core idea is sound and the parsing pipeline is defensive (malformed JSONL lines skipped, missing dirs handled, UUID dedup across subagent files). The main problems are stale pricing, timezone-incorrect day bucketing, an uninstaller that does not actually uninstall, and UI mutation from a background thread.

## Architecture Assessment

**Good:**
- Clean separation: pricing → parsing → aggregation → rendering → menu plumbing.
- `parse_usage()` (lines 185–242) is robust to missing `~/.claude/projects/`, blank lines, malformed JSON, and missing timestamps ("unknown" bucket).
- LaunchAgent points at the repo checkout (live edits) rather than a stale copy — pragmatic for a dev tool.
- Graph rendered headless (Agg) to PNG, embedded via NSImageView; degrades gracefully if matplotlib is absent.

**Questionable:**
- The "Session Config" submenu writes `model`, `env.CLAUDE_VERBOSE_TOOLS`, and `autoCompact` into `~/.claude/settings.json` (lines 592–629). This is scope creep for a *usage monitor*, races with Claude Code's own writes to the same file (non-atomic `write_text`, no merge), and the values written are dubious: `"model": "claude-opus-4-7"` is not a valid Claude Code model alias/ID, and `CLAUDE_VERBOSE_TOOLS` / `autoCompact` are not documented Claude Code settings. Recommend removing this submenu entirely.
- Full re-scan of every JSONL file on every refresh (no mtime cache, no incremental tailing). Fine for small histories; will block the UI thread for multi-GB `~/.claude/projects/` trees.
- Two startup mechanisms (LaunchAgent + PreToolUse hook) is belt-and-suspenders; the hook fires on *every tool call in every Claude session* just to `pgrep`. One mechanism would do.

## Bugs Found

### High

1. **Stale / wrong pricing table** — `claude_usage_monitor.py:71–82`, `104–110`
   - Current-generation models **Fable 5** and **Opus 4.8** are absent from `PRICING` and `AVAILABLE_MODELS`; newest listed is `claude-opus-4-7`.
   - Opus 4.6/4.7 priced at `$15/$75` per Mtok — Opus pricing dropped to **$5 in / $25 out** (cache write $6.25, cache read $0.50) starting with Opus 4.5. Opus costs are overstated ~3×.
   - `claude-haiku-4-5` priced at `$0.80/$4.00` — that is Haiku **3.5** pricing; Haiku 4.5 is **$1.00/$5.00**.
   - `claude-opus-3-5`, `claude-sonnet-3-5`, `claude-haiku-3-5` (lines 78–80) are dead entries: real 3.x IDs use the `claude-3-5-sonnet-*` naming order, so these keys never match anything.
   - No long-context (>200K) premium tier for Sonnet.

2. **Silent fallback misprices unknown models** — `claude_usage_monitor.py:120–126`
   `_pricing()` falls back to Sonnet rates (`_DEFAULT_PRICING`) for any unrecognized model with no warning. All Fable 5 and Opus 4.8 usage is currently being costed at Sonnet prices. The fuzzy match `key in model` is also dict-order-dependent. At minimum, log unknown models once and surface "estimated" in the UI.

3. **`uninstall.sh` does not actually uninstall** — `uninstall.sh:7,21–26` + `install.sh:94–125`
   - It removes `~/.claude/claude_usage_monitor.py`, a path the installer never creates (the installer points the LaunchAgent at the repo — README step 3 "Copies the script to ~/.claude/" is also wrong).
   - It never removes the `PreToolUse` hook from `~/.claude/settings.json`, so the next Claude session **relaunches the monitor after "uninstall"**.
   - It never kills hook-spawned `nohup` instances (`launchctl unload` only stops the LaunchAgent-managed one). Needs `pkill -f claude_usage_monitor.py` and a settings.json hook-removal step.

4. **UI mutated from a background thread** — `claude_usage_monitor.py:654,656–671`
   `_refresh_status` runs in a daemon `threading.Thread` and sets `self._lbl_status.title` and `self.title`, i.e. AppKit objects, off the main thread. This is undefined behavior in Cocoa (intermittent crashes/no-repaint). Marshal back to the main thread (e.g. `pyobjc` `performSelectorOnMainThread_`, or fetch in background and apply via a rumps Timer tick).

### Medium

5. **Timezone bug: UTC day bucketing vs local "today"** — `claude_usage_monitor.py:218–219` vs `676–677`
   Timestamps are parsed as UTC (`Z → +00:00`) and bucketed with `dt.strftime("%Y-%m-%d")` (UTC date), but Today/Month filters use local `date.today()`. For a US user, all evening usage lands on "tomorrow" and Today's cost reads near-$0 after ~4–7 PM. Fix: `dt.astimezone().strftime(...)`.

6. **Crash at import if `~/.claude/` does not exist** — `claude_usage_monitor.py:59–66`
   `logging.FileHandler(LOG_PATH)` at module import raises `FileNotFoundError` when the directory is missing (manual run on a machine that never ran Claude Code). Add `CLAUDE_DIR.mkdir(exist_ok=True)` or `delay=True`.

7. **Installer edits `~/.claude/settings.json` with no backup and fragile quoting** — `install.sh:97,100–125`
   - No backup before mutating a file Claude Code depends on; a malformed write bricks every Claude session (hooks fail closed in some configs).
   - `$HOOK_CMD` is interpolated into JSON unescaped (line 104 heredoc); safe only because the command happens to contain no `"` or `\` — any future path with a double quote breaks settings.json.
   - In `HOOK_CMD` (line 97), the log redirect path `${CLAUDE_DIR}/usage_monitor.log` is unquoted inside the generated shell command; breaks for a `$HOME` containing spaces.

8. **`pip install` into whichever python3 is found first** — `install.sh:16–42`
   On modern Homebrew/system Pythons this hits PEP 668 (`externally-managed-environment`) and fails; on conda it pollutes base. No venv. Recommend a dedicated venv (e.g. `~/.claude/claudecosts-venv`) and pointing the plist at it.

9. **Status icon clobbered by display refresh** — `claude_usage_monitor.py:684` vs `663–671`
   `_update_display` rewrites `self.title` unconditionally, erasing the ⚠️/🔴 prefix until the next status poll (up to `STATUS_TTL`). Title composition should be centralized.

### Low

10. **`_short_model` strips only `-20251001`** — `claude_usage_monitor.py:147`. Any other dated ID (e.g. `-20260115`) shows raw. Strip a trailing `-\d{8}` with a regex.
11. **`lstrip("✓ ")` is a character-set strip** — lines 563, 587, 594, 604. Works for current labels by luck; a label starting with a space-or-✓ run would corrupt. Use `removeprefix("✓ ")`.
12. **Version skew** — "v3.0" in the startup log (line 717) vs `User-Agent: ClaudeCosts/1.0` (line 92). Single-source a `__version__`.
13. **`launchctl load/unload` are deprecated** — `install.sh:129–130`; prefer `bootstrap`/`bootout gui/$UID`.
14. **Zero-usage entries counted as API calls** — line 208 only skips empty `usage` dicts; `usage` objects with all-zero counts still increment `calls`. Cosmetic.
15. **Graph all-time mode** can build a bar per day since the earliest log (line 269–271) — a year of history yields 365 bars in 370 px; unreadable. Cap or down-sample.

## Production Readiness Gaps

- **No packaging.** No `pyproject.toml`; install is clone + bash. For PyPI: package as `claudecosts` with a console entry point, move pricing to a data file (or fetch from a maintained source like LiteLLM's price map with a baked-in fallback), and provide a `claudecosts install-agent` subcommand replacing install.sh logic.
- **No tests.** `parse_usage`, `_aggregate`, `_pricing`, `_get_daily_series`, and `_calc_cost` are pure and trivially unit-testable with fixture JSONL (including malformed lines, missing timestamps, dup UUIDs, unknown models, timezone boundaries). The UI layer can stay untested.
- **No CI.** A minimal GitHub Actions workflow on `macos-latest`: `py_compile`, `shellcheck install.sh uninstall.sh`, `pytest`. (Note: shellcheck would likely flag the quoting issues above.)
- **Cross-platform: macOS-only by design** (rumps/AppKit). Fine, but README should state it under Requirements, and the script should fail with a clear message on other platforms instead of an AppKit ImportError.
- **README inaccuracies:** step 3 of "The installer" section is wrong (nothing is copied to `~/.claude/`); the PreToolUse hook injection — a significant, surprising modification of the user's Claude settings — is not mentioned at all and must be disclosed for a public release.
- **No screenshot** (placeholder comment at README line 5); essential for a menu-bar app's repo.
- **requirements.txt:** floors only; consider upper bounds for matplotlib (large dep — consider making the graph optional / extras `[graph]`).
- **Privacy posture is good** (local-only, one GET to status.anthropic.com) — worth a hardening pass: the status fetch is the only network call; keep it that way.

## Feature Recommendations

1. **Pricing auto-update**: fetch a community-maintained price map (with hardcoded fallback + "prices as of <date>" in the menu). Eliminates the staleness class entirely.
2. **Unknown-model badge**: show "~" before estimated costs when `_DEFAULT_PRICING` was used.
3. **Incremental parsing**: cache per-file byte offsets/mtimes; only tail changed JSONL files. Makes 5-minute refresh cheap.
4. **Budget alert**: optional daily/monthly cost threshold with a macOS notification (rumps supports this natively).
5. **Cache-token visibility**: cache read/write are parsed and costed but never shown; for heavy Claude Code users cache reads dominate cost — add a line per period.
6. **Drop the Session Config submenu** (or rebuild it against documented Claude Code settings with atomic, merged writes).

## Cleanup Actions

- [ ] Update `PRICING`: add Fable 5 and Opus 4.8 (verify current rates at docs.claude.com/pricing); fix Opus 4.6/4.7 to $5/$25 tier; fix Haiku 4.5 to $1/$5; delete dead `*-3-5` reverse-named entries and redundant `claude-haiku-4-5-20251001`.
- [ ] Fix timezone bucketing (`dt.astimezone()` before date-string formatting).
- [ ] Rewrite `uninstall.sh`: remove the PreToolUse hook from settings.json, `pkill` running instances, delete the correct paths; fix README installer description to match reality.
- [ ] Move status-fetch UI updates onto the main thread; centralize title composition.
- [ ] Guard `~/.claude/` existence before `logging.FileHandler` (or `delay=True`).
- [ ] install.sh: back up settings.json before editing; do the JSON edit entirely in Python (build the hook command there too, properly escaped); quote the log path inside `HOOK_CMD`; use a venv; switch to `launchctl bootstrap/bootout`.
- [ ] Add `pyproject.toml`, pytest suite for the pure functions, and a macOS CI workflow with shellcheck.
- [ ] Unify version string; add screenshot; document the settings.json hook in README.
- [ ] Remove stray `__pycache__/` from the working tree (already gitignored).
