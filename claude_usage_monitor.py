#!/usr/bin/env python3
"""
Claude Usage Monitor — macOS Menu Bar App
Reads Claude Code JSONL session logs, calculates token costs, and displays
a live summary in the menu bar. Refreshes every 15 minutes automatically.
"""

import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path

import rumps

# ── Constants ─────────────────────────────────────────────────────────────────

CLAUDE_DIR       = Path.home() / ".claude"
LOG_PATH         = CLAUDE_DIR / "usage_monitor.log"
REFRESH_INTERVAL = 900   # 15 minutes
MAX_MODEL_SLOTS  = 10    # pre-allocated menu rows for model breakdown

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ── Pricing (USD per million tokens, March 2026) ──────────────────────────────

PRICING: dict[str, dict] = {
    "claude-opus-4-6":            {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6":          {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "claude-sonnet-4-5":          {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "claude-haiku-4-5":           {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
    "claude-haiku-4-5-20251001":  {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
    "claude-opus-3-5":            {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-3-5":          {"input":  3.00, "output": 15.00, "cache_write":  3.75, "cache_read": 0.30},
    "claude-haiku-3-5":           {"input":  0.80, "output":  4.00, "cache_write":  1.00, "cache_read": 0.08},
}
_DEFAULT_PRICING = {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30}


def _pricing(model: str) -> dict:
    if model in PRICING:
        return PRICING[model]
    for key, val in PRICING.items():
        if model.startswith(key) or key in model:
            return val
    return _DEFAULT_PRICING


# ── Cost calculation ──────────────────────────────────────────────────────────

def _calc_cost(model: str, input_tok: int, output_tok: int,
               cache_write: int, cache_read: int) -> float:
    p = _pricing(model)
    return (
        input_tok   / 1e6 * p["input"]       +
        output_tok  / 1e6 * p["output"]      +
        cache_write / 1e6 * p["cache_write"] +
        cache_read  / 1e6 * p["cache_read"]
    )


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_tok(n: int) -> str:
    if n >= 1_000_000: return f"{n / 1e6:.2f}M"
    if n >= 1_000:     return f"{n / 1e3:.1f}K"
    return str(n)


def _fmt_cost(c: float) -> str:
    if c >= 1.0:  return f"${c:.2f}"
    if c > 0.0:   return f"${c:.4f}"
    return "$0.00"


def _short_model(model: str) -> str:
    """Turn 'claude-sonnet-4-6' → 'Sonnet 4-6'."""
    s = model.replace("claude-", "").replace("-20251001", "")
    parts = s.split("-")
    if not parts:
        return model
    name = parts[0].capitalize()
    ver  = "-".join(parts[1:]) if len(parts) > 1 else ""
    return f"{name} {ver}".strip()


# ── Data parsing ──────────────────────────────────────────────────────────────

def parse_usage() -> dict:
    """
    Scan all JSONL files under ~/.claude/projects/ and aggregate token usage.

    Returns:
        { date_str: { model: { input, output, cache_write, cache_read, cost, calls } } }
    """
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        log.warning("Projects dir not found: %s", projects_dir)
        return {}

    data: dict = {}
    seen: set  = set()   # deduplicate entries by UUID across files
    files_seen = 0
    entries_seen = 0

    for fp in projects_dir.rglob("*.jsonl"):
        files_seen += 1
        try:
            with open(fp, encoding="utf-8") as fh:
                for line_no, raw in enumerate(fh, 1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "assistant":
                        continue

                    msg   = entry.get("message", {})
                    usage = msg.get("usage", {})
                    if not usage:
                        continue

                    # Deduplicate (same conversation may appear in main + subagent file)
                    uid = entry.get("uuid", f"{fp}:{line_no}")
                    if uid in seen:
                        continue
                    seen.add(uid)
                    entries_seen += 1

                    model = msg.get("model", "unknown")
                    ts    = entry.get("timestamp", "")
                    try:
                        dt       = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        date_str = dt.strftime("%Y-%m-%d")
                    except Exception:
                        date_str = "unknown"

                    it = usage.get("input_tokens", 0)
                    ot = usage.get("output_tokens", 0)
                    cw = usage.get("cache_creation_input_tokens", 0)
                    cr = usage.get("cache_read_input_tokens", 0)

                    data.setdefault(date_str, {}).setdefault(model, {
                        "input": 0, "output": 0, "cache_write": 0,
                        "cache_read": 0, "cost": 0.0, "calls": 0,
                    })
                    d = data[date_str][model]
                    d["input"]       += it
                    d["output"]      += ot
                    d["cache_write"] += cw
                    d["cache_read"]  += cr
                    d["cost"]        += _calc_cost(model, it, ot, cw, cr)
                    d["calls"]       += 1

        except Exception as ex:
            log.error("Error reading %s: %s", fp, ex)

    log.info("Parsed %d files → %d unique assistant entries", files_seen, entries_seen)
    return data


def _aggregate(data: dict, prefix: str = "") -> dict:
    """Sum stats for all dates matching prefix ('2026-03' for month, '2026-03-21' for day)."""
    totals   = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "cost": 0.0, "calls": 0}
    by_model: dict = {}

    for date_str, models in data.items():
        if prefix and not date_str.startswith(prefix):
            continue
        for model, stats in models.items():
            for k in totals:
                totals[k] += stats.get(k, 0)
            by_model.setdefault(model, {k: 0 for k in totals})
            for k in totals:
                by_model[model][k] += stats.get(k, 0)

    totals["by_model"] = by_model
    return totals


# ── Menu bar app ──────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):

    def __init__(self):
        super().__init__("⚡ …", quit_button=None)
        self._setup_menu()
        # First load runs synchronously before the run-loop so UI is ready immediately
        self._do_refresh()
        # 15-minute auto-refresh timer
        self._timer = rumps.Timer(self._on_timer, REFRESH_INTERVAL)
        self._timer.start()

    # ── Menu setup ───────────────────────────────────────────────────────────

    def _setup_menu(self):
        """Build the static menu skeleton — titles will be filled in by _update_display."""

        # Items we update in place (unique initial titles required by rumps dict keying)
        self._lbl_updated      = rumps.MenuItem("lbl_updated")

        self._lbl_today_hdr    = rumps.MenuItem("TODAY")
        self._lbl_today_cost   = rumps.MenuItem("lbl_today_cost")
        self._lbl_today_tok    = rumps.MenuItem("lbl_today_tok")
        self._lbl_today_calls  = rumps.MenuItem("lbl_today_calls")

        self._lbl_month_hdr    = rumps.MenuItem("THIS MONTH")
        self._lbl_month_cost   = rumps.MenuItem("lbl_month_cost")
        self._lbl_month_tok    = rumps.MenuItem("lbl_month_tok")
        self._lbl_month_calls  = rumps.MenuItem("lbl_month_calls")

        self._lbl_all_hdr      = rumps.MenuItem("ALL TIME")
        self._lbl_all_cost     = rumps.MenuItem("lbl_all_cost")
        self._lbl_all_tok      = rumps.MenuItem("lbl_all_tok")
        self._lbl_all_calls    = rumps.MenuItem("lbl_all_calls")

        # Pre-allocated model slots — hidden until a model occupies the slot
        self._lbl_model_hdr    = rumps.MenuItem("MODELS (all time)")
        self._model_slots      = [
            rumps.MenuItem(f"lbl_model_{i}") for i in range(MAX_MODEL_SLOTS)
        ]

        refresh_btn = rumps.MenuItem("⟳  Refresh Now", callback=self._manual_refresh)
        quit_btn    = rumps.MenuItem("✕  Quit",         callback=lambda _: rumps.quit_application())

        menu_items = (
            [
                self._lbl_updated,
                None,
                self._lbl_today_hdr,
                self._lbl_today_cost,
                self._lbl_today_tok,
                self._lbl_today_calls,
                None,
                self._lbl_month_hdr,
                self._lbl_month_cost,
                self._lbl_month_tok,
                self._lbl_month_calls,
                None,
                self._lbl_all_hdr,
                self._lbl_all_cost,
                self._lbl_all_tok,
                self._lbl_all_calls,
                None,
                self._lbl_model_hdr,
            ]
            + self._model_slots
            + [None, refresh_btn, quit_btn]
        )

        self.menu = menu_items

    # ── Refresh callbacks ─────────────────────────────────────────────────────

    def _on_timer(self, _):
        self._do_refresh()

    def _manual_refresh(self, _):
        self._do_refresh()

    def _do_refresh(self):
        log.info("Refreshing usage data…")
        try:
            data = parse_usage()
            self._update_display(data)
        except Exception as exc:
            log.exception("Refresh failed: %s", exc)
            self.title = "⚡ ERR"

    # ── Display update ────────────────────────────────────────────────────────

    def _update_display(self, data: dict):
        today = date.today().strftime("%Y-%m-%d")
        month = date.today().strftime("%Y-%m")

        t = _aggregate(data, prefix=today)
        m = _aggregate(data, prefix=month)
        a = _aggregate(data)

        # ── Title bar ──
        today_tok = t["input"] + t["output"]
        self.title = f"⚡ {_fmt_cost(t['cost'])} | {_fmt_tok(today_tok)} tok"

        # ── Last updated ──
        now_str = datetime.now().strftime("%b %d  %I:%M %p")
        self._lbl_updated.title = f"Updated: {now_str}"

        # ── Today ──
        self._lbl_today_cost.title  = f"  Cost      {_fmt_cost(t['cost'])}"
        self._lbl_today_tok.title   = f"  Tokens    {_fmt_tok(t['input'] + t['output'])} ({_fmt_tok(t['input'])} in / {_fmt_tok(t['output'])} out)"
        self._lbl_today_calls.title = f"  API calls {t['calls']}"

        # ── This month ──
        self._lbl_month_cost.title  = f"  Cost      {_fmt_cost(m['cost'])}"
        self._lbl_month_tok.title   = f"  Tokens    {_fmt_tok(m['input'] + m['output'])} ({_fmt_tok(m['input'])} in / {_fmt_tok(m['output'])} out)"
        self._lbl_month_calls.title = f"  API calls {m['calls']}"

        # ── All time ──
        self._lbl_all_cost.title    = f"  Cost      {_fmt_cost(a['cost'])}"
        self._lbl_all_tok.title     = f"  Tokens    {_fmt_tok(a['input'] + a['output'])} ({_fmt_tok(a['input'])} in / {_fmt_tok(a['output'])} out)"
        self._lbl_all_calls.title   = f"  API calls {a['calls']}"

        # ── Model breakdown ──
        models_sorted = sorted(
            a["by_model"].items(), key=lambda x: -x[1]["cost"]
        )
        for i, slot in enumerate(self._model_slots):
            if i < len(models_sorted):
                model, stats = models_sorted[i]
                tok  = stats["input"] + stats["output"]
                name = _short_model(model)
                slot.title = f"  {name:<18}  {_fmt_cost(stats['cost'])}   {_fmt_tok(tok)} tok"
            else:
                # Hide unused slots with an em-space (invisible but not empty)
                slot.title = "\u2003"  # em space — renders as blank row

        log.info(
            "Display updated — today: %s  month: %s  all-time: %s",
            _fmt_cost(t["cost"]), _fmt_cost(m["cost"]), _fmt_cost(a["cost"]),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Claude Usage Monitor v1.0 starting")
    ClaudeUsageApp().run()
