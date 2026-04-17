#!/usr/bin/env python3
"""
Claude Usage Monitor — macOS Menu Bar App
Reads Claude Code JSONL session logs, calculates token costs, and displays
a live summary in the menu bar. Refreshes every 15 minutes automatically.
Inline graph rendered as a custom NSView inside the dropdown menu.
"""

import io
import json
import logging
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

import rumps
from AppKit import (
    NSColor, NSImage, NSImageView, NSMakeRect, NSMakeSize,
    NSMenuItem, NSView, NSBezierPath, NSFont, NSString,
    NSForegroundColorAttributeName, NSFontAttributeName,
    NSParagraphStyleAttributeName, NSMutableParagraphStyle,
    NSTextAlignmentCenter, NSLineBreakByTruncatingTail,
)
from Foundation import NSData, NSAttributedString, NSDictionary

# ── Constants ─────────────────────────────────────────────────────────────────

CLAUDE_DIR       = Path.home() / ".claude"
LOG_PATH         = CLAUDE_DIR / "usage_monitor.log"
SETTINGS_PATH    = CLAUDE_DIR / "settings.json"
REFRESH_INTERVAL = 900    # seconds
MAX_MODEL_SLOTS  = 10

GRAPH_W          = 370    # logical pixels (NSView frame)
GRAPH_H          = 130    # logical pixels
GRAPH_DAYS       = 14     # days shown in inline graph
GRAPH_DPI        = 144    # render at 2× for Retina

STATUS_URL       = "https://status.anthropic.com/api/v2/status.json"
STATUS_TTL       = 300    # seconds between status page polls
STATUS_PAGE_URL  = "https://status.anthropic.com"

_STATUS_ICONS = {
    "none":     "✅",
    "minor":    "⚠️",
    "major":    "🔴",
    "critical": "🔴",
    "unknown":  "❓",
}
_TITLE_ALERTS = {"minor", "major", "critical"}  # indicators that bleed into menu-bar title

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

# ── Pricing ───────────────────────────────────────────────────────────────────

PRICING: dict[str, dict] = {
    "claude-opus-4-7":            {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
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

_status_cache: dict = {"indicator": "unknown", "description": "Checking…", "fetched_at": 0.0}


def _fetch_claude_status() -> tuple[str, str]:
    now = time.time()
    if now - _status_cache["fetched_at"] < STATUS_TTL and _status_cache["indicator"] != "unknown":
        return _status_cache["indicator"], _status_cache["description"]
    try:
        req = urllib.request.Request(STATUS_URL, headers={"User-Agent": "ClaudeCosts/1.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            payload = json.loads(resp.read())
        indicator   = payload["status"]["indicator"]
        description = payload["status"]["description"]
    except Exception as exc:
        log.warning("Claude status fetch failed: %s", exc)
        indicator, description = "unknown", "Status unavailable"
    _status_cache.update({"indicator": indicator, "description": description, "fetched_at": now})
    return indicator, description


AVAILABLE_MODELS = [
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
]

REFRESH_OPTIONS = {
    "5 minutes":  300,
    "15 minutes": 900,
    "30 minutes": 1800,
    "1 hour":     3600,
}


def _pricing(model: str) -> dict:
    if model in PRICING:
        return PRICING[model]
    for key, val in PRICING.items():
        if model.startswith(key) or key in model:
            return val
    return _DEFAULT_PRICING


def _calc_cost(model, it, ot, cw, cr):
    p = _pricing(model)
    return it/1e6*p["input"] + ot/1e6*p["output"] + cw/1e6*p["cache_write"] + cr/1e6*p["cache_read"]


def _fmt_tok(n):
    if n >= 1_000_000: return f"{n/1e6:.2f}M"
    if n >= 1_000:     return f"{n/1e3:.1f}K"
    return str(n)


def _fmt_cost(c):
    if c >= 1.0:  return f"${c:.2f}"
    if c > 0.0:   return f"${c:.4f}"
    return "$0.00"


def _short_model(model):
    s = model.replace("claude-", "").replace("-20251001", "")
    parts = s.split("-")
    return f"{parts[0].capitalize()} {'-'.join(parts[1:])}".strip() if parts else model


# ── Settings helpers ──────────────────────────────────────────────────────────

def _load_settings():
    try:
        if SETTINGS_PATH.exists():
            return json.loads(SETTINGS_PATH.read_text())
    except Exception as e:
        log.error("Failed to load settings: %s", e)
    return {}


def _save_settings(settings):
    try:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2))
        return True
    except Exception as e:
        log.error("Failed to save settings: %s", e)
        return False


def _get_setting(settings, *keys, default=None):
    cur = settings
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


# ── Data parsing ──────────────────────────────────────────────────────────────

def parse_usage():
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return {}

    data: dict = {}
    seen: set  = set()

    for fp in projects_dir.rglob("*.jsonl"):
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
                    uid = entry.get("uuid", f"{fp}:{line_no}")
                    if uid in seen:
                        continue
                    seen.add(uid)

                    model    = msg.get("model", "unknown")
                    ts       = entry.get("timestamp", "")
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

    return data


def _aggregate(data, prefix=""):
    totals   = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "cost": 0.0, "calls": 0}
    by_model = {}
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


def _get_daily_series(data, days=14):
    """Return daily totals for the last `days` days. Pass days=None for all-time."""
    today = date.today()

    if days is None:
        # Derive span from earliest date key in data (skip "unknown")
        date_keys = [d for d in data if d != "unknown" and len(d) == 10]
        if date_keys:
            earliest  = date.fromisoformat(min(date_keys))
            days      = (today - earliest).days + 1
        else:
            days = 30   # fallback when no data

    series = []
    for i in range(days - 1, -1, -1):
        d  = today - timedelta(days=i)
        ds = d.strftime("%Y-%m-%d")
        a  = _aggregate(data, prefix=ds)
        series.append({
            "date":   ds,
            "label":  d.strftime("%m/%d"),
            "input":  a["input"],
            "output": a["output"],
            "cost":   a["cost"],
            "calls":  a["calls"],
        })
    return series


# ── Graph rendering (matplotlib → PNG bytes) ──────────────────────────────────

def _render_graph_png(data, days=14):
    """
    Render a compact token-usage chart to PNG bytes using the Agg (headless)
    backend.  Rendered at GRAPH_DPI so it looks sharp on Retina displays when
    the NSImage logical size is set to GRAPH_W × GRAPH_H.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError:
        return None
    from matplotlib.gridspec import GridSpec

    series  = _get_daily_series(data, days=days)
    labels  = [s["label"]          for s in series]
    inputs  = [s["input"]  / 1000  for s in series]
    outputs = [s["output"] / 1000  for s in series]
    costs   = [s["cost"]           for s in series]
    x       = list(range(len(labels)))

    # figsize in inches × dpi = physical pixels; we display at GRAPH_W×GRAPH_H logical px
    fig_w = GRAPH_W * 2 / GRAPH_DPI   # 2× for Retina → 5.14 in
    fig_h = GRAPH_H * 2 / GRAPH_DPI   # 2× for Retina → 1.81 in

    BG   = "#1c1c1e"
    GRID = "#3a3a3c"
    TXT  = "#8e8e93"

    fig = plt.figure(figsize=(fig_w, fig_h), facecolor=BG, dpi=GRAPH_DPI)
    gs  = GridSpec(2, 1, figure=fig, hspace=0.08, height_ratios=[2.6, 1])

    # ── Token bars ──
    ax1 = fig.add_subplot(gs[0])
    ax1.set_facecolor(BG)
    ax1.bar(x, inputs,  label="Input",  color="#4f9cf9", alpha=0.92, width=0.65)
    ax1.bar(x, outputs, bottom=inputs,  label="Output", color="#5ac97d", alpha=0.92, width=0.65)
    ax1.set_xlim(-0.5, len(x) - 0.5)
    ax1.set_xticks([])
    ax1.tick_params(axis="y", colors=TXT, labelsize=6)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}K"))
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.spines["bottom"].set_visible(False)
    ax1.spines["left"].set_edgecolor(GRID)
    ax1.grid(axis="y", color=GRID, linewidth=0.5, alpha=0.6)
    ax1.legend(facecolor="#2c2c2e", labelcolor="#ebebf0", fontsize=6,
               loc="upper left", framealpha=0.85, ncol=2,
               borderpad=0.4, handlelength=1.2, handletextpad=0.4)
    span_label = "all time" if days is None else f"last {days} days"
    ax1.set_title(f"Token usage — {span_label}", color="#ebebf0",
                  fontsize=7, pad=4, loc="right")

    # ── Cost line ──
    ax2 = fig.add_subplot(gs[1])
    ax2.set_facecolor(BG)
    ax2.fill_between(x, costs, alpha=0.20, color="#ff6b6b")
    ax2.plot(x, costs, color="#ff6b6b", linewidth=1.3, marker="o", markersize=2.2)
    ax2.set_xlim(-0.5, len(x) - 0.5)
    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=40, ha="right", fontsize=5.5, color=TXT)
    ax2.tick_params(axis="y", colors=TXT, labelsize=5.5)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:.2f}"))
    for sp in ax2.spines.values():
        sp.set_visible(False)
    ax2.spines["bottom"].set_visible(True)
    ax2.spines["bottom"].set_edgecolor(GRID)
    ax2.grid(axis="y", color=GRID, linewidth=0.5, alpha=0.6)

    fig.patch.set_facecolor(BG)
    plt.subplots_adjust(left=0.10, right=0.97, top=0.88, bottom=0.26, hspace=0.06)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=GRAPH_DPI,
                facecolor=BG, bbox_inches=None)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ── Inline graph menu item ────────────────────────────────────────────────────

_GRAPH_PLACEHOLDER = "__graph_placeholder__"


def _make_ns_image(png_bytes):
    """Create an NSImage from raw PNG bytes, scaled to GRAPH_W×GRAPH_H logical px."""
    ns_data  = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    ns_image = NSImage.alloc().initWithData_(ns_data)
    ns_image.setSize_(NSMakeSize(GRAPH_W, GRAPH_H))
    return ns_image


# ── Menu bar app ──────────────────────────────────────────────────────────────

class ClaudeUsageApp(rumps.App):

    def __init__(self):
        super().__init__("⚡ …", quit_button=None)
        self._data: dict = {}
        self._refresh_interval = REFRESH_INTERVAL
        self._graph_days: int | None = 14   # None = all time
        self._graph_view: NSImageView | None = None
        self._graph_ns_item: NSMenuItem | None = None
        self._setup_menu()
        self._install_inline_graph()
        self._do_refresh()
        self._timer = rumps.Timer(self._on_timer, self._refresh_interval)
        self._timer.start()

    # ── Menu setup ────────────────────────────────────────────────────────────

    def _setup_menu(self):
        self._lbl_updated     = rumps.MenuItem("lbl_updated")
        self._lbl_status_hdr  = rumps.MenuItem("CLAUDE STATUS")
        self._lbl_status      = rumps.MenuItem("  ❓ Checking…")
        self._lbl_status_hdr.set_callback(None)
        self._lbl_status.set_callback(None)
        self._btn_status_page = rumps.MenuItem("  Open Status Page", callback=self._open_status_page)
        self._graph_ph        = rumps.MenuItem(_GRAPH_PLACEHOLDER)  # replaced later

        self._lbl_today_hdr   = rumps.MenuItem("TODAY")
        self._lbl_today_cost  = rumps.MenuItem("lbl_today_cost")
        self._lbl_today_tok   = rumps.MenuItem("lbl_today_tok")
        self._lbl_today_calls = rumps.MenuItem("lbl_today_calls")

        self._lbl_month_hdr   = rumps.MenuItem("THIS MONTH")
        self._lbl_month_cost  = rumps.MenuItem("lbl_month_cost")
        self._lbl_month_tok   = rumps.MenuItem("lbl_month_tok")
        self._lbl_month_calls = rumps.MenuItem("lbl_month_calls")

        self._lbl_all_hdr     = rumps.MenuItem("ALL TIME")
        self._lbl_all_cost    = rumps.MenuItem("lbl_all_cost")
        self._lbl_all_tok     = rumps.MenuItem("lbl_all_tok")
        self._lbl_all_calls   = rumps.MenuItem("lbl_all_calls")

        self._lbl_model_hdr   = rumps.MenuItem("MODELS (all time)")
        self._model_slots     = [
            rumps.MenuItem(f"lbl_model_{i}") for i in range(MAX_MODEL_SLOTS)
        ]

        # Time-range selectors for the inline graph
        _RANGES = [("7 days", 7), ("14 days", 14), ("Month", 30), ("All time", None)]
        self._range_items: list[rumps.MenuItem] = []
        self._range_map: dict[str, int | None]  = {}
        for lbl, val in _RANGES:
            item = rumps.MenuItem(f"  {lbl}", callback=self._on_set_graph_range)
            self._range_items.append(item)
            self._range_map[f"  {lbl}"] = val

        # Session config submenu
        config_menu = rumps.MenuItem("⚙️  Session Config")
        config_menu.add(rumps.MenuItem("── Model ──"))
        self._model_items = {}
        for model in AVAILABLE_MODELS:
            lbl  = f"  {_short_model(model)}"
            item = rumps.MenuItem(lbl, callback=self._on_set_model)
            self._model_items[lbl] = model
            config_menu.add(item)

        config_menu.add(None)
        config_menu.add(rumps.MenuItem("── Refresh Interval ──"))
        self._refresh_items = {}
        for lbl, secs in REFRESH_OPTIONS.items():
            item = rumps.MenuItem(f"  {lbl}", callback=self._on_set_refresh)
            self._refresh_items[f"  {lbl}"] = secs
            config_menu.add(item)

        config_menu.add(None)
        config_menu.add(rumps.MenuItem("── Permissions ──"))
        self._perm_verbose  = rumps.MenuItem("  Verbose tool output",   callback=self._toggle_verbose)
        self._perm_autocomp = rumps.MenuItem("  Auto-compact sessions", callback=self._toggle_autocompact)
        config_menu.add(self._perm_verbose)
        config_menu.add(self._perm_autocomp)
        config_menu.add(None)
        config_menu.add(rumps.MenuItem("  Edit settings.json…", callback=self._open_settings_file))

        refresh_btn = rumps.MenuItem("⟳  Refresh Now", callback=self._manual_refresh)
        quit_btn    = rumps.MenuItem("✕  Quit",         callback=lambda _: rumps.quit_application())

        self.menu = (
            [
                self._lbl_updated,
                None,
                self._lbl_status_hdr,
                self._lbl_status,
                self._btn_status_page,
                None,
                self._graph_ph,      # ← will become the inline graph NSMenuItem
            ]
            + self._range_items
            + [
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
            + [
                None,
                config_menu,
                None,
                refresh_btn,
                quit_btn,
            ]
        )

        self._sync_config_state()

    # ── Inline graph installation ─────────────────────────────────────────────

    def _install_inline_graph(self):
        """
        Replace the placeholder rumps.MenuItem with a native NSMenuItem whose
        view is an NSImageView.  The image is populated on the first refresh.
        """
        try:
            ns_menu  = self.menu._menu
            ph_item  = self._graph_ph._menuitem
            idx      = ns_menu.indexOfItem_(ph_item)
            if idx == -1:
                log.error("Graph placeholder not found in NSMenu")
                return

            self._graph_view = NSImageView.alloc().initWithFrame_(
                NSMakeRect(0, 0, GRAPH_W, GRAPH_H)
            )
            self._graph_ns_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "", None, ""
            )
            self._graph_ns_item.setView_(self._graph_view)
            self._graph_ns_item.setEnabled_(False)

            ns_menu.removeItemAtIndex_(idx)
            ns_menu.insertItem_atIndex_(self._graph_ns_item, idx)
            log.info("Inline graph view installed at index %d", idx)
        except Exception as e:
            log.exception("install_inline_graph failed: %s", e)

    def _update_inline_graph(self):
        """Re-render PNG and push it into the NSImageView (runs on main thread)."""
        if self._graph_view is None:
            return
        try:
            png  = _render_graph_png(self._data, days=self._graph_days)
            if png is None:
                return
            img  = _make_ns_image(png)
            self._graph_view.setImage_(img)
        except Exception as e:
            log.exception("update_inline_graph failed: %s", e)

    # ── Config state sync ─────────────────────────────────────────────────────

    def _sync_config_state(self):
        # Graph range checkmarks
        for item in self._range_items:
            base_lbl = "  " + item.title.lstrip("✓ ").strip()
            active   = self._range_map.get(base_lbl) == self._graph_days
            item.title = ("✓ " + base_lbl.strip()) if active else base_lbl

        settings  = _load_settings()
        preferred = _get_setting(settings, "model", default="")

        for lbl, model in self._model_items.items():
            item = self.menu["⚙️  Session Config"][lbl]
            item.title = ("✓ " + lbl.strip()) if model == preferred else lbl

        for lbl, secs in self._refresh_items.items():
            item = self.menu["⚙️  Session Config"][lbl]
            item.title = ("✓ " + lbl.strip()) if secs == self._refresh_interval else lbl

        verbose  = _get_setting(settings, "env", "CLAUDE_VERBOSE_TOOLS", default="") == "1"
        autocomp = _get_setting(settings, "autoCompact", default=False)
        self._perm_verbose.title  = ("✓  Verbose tool output"   if verbose  else "  Verbose tool output")
        self._perm_autocomp.title = ("✓  Auto-compact sessions" if autocomp else "  Auto-compact sessions")

    # ── Config callbacks ──────────────────────────────────────────────────────

    def _on_set_graph_range(self, sender):
        lbl  = sender.title
        days = self._range_map.get(lbl, self._range_map.get("  " + lbl.lstrip("✓ ")))
        self._graph_days = days
        self._sync_config_state()
        self._update_inline_graph()

    def _on_set_model(self, sender):
        lbl   = sender.title
        model = self._model_items.get(lbl) or self._model_items.get("  " + lbl.lstrip("✓ "))
        if not model:
            return
        s = _load_settings()
        s["model"] = model
        _save_settings(s)
        self._sync_config_state()

    def _on_set_refresh(self, sender):
        lbl  = sender.title
        secs = self._refresh_items.get(lbl) or self._refresh_items.get("  " + lbl.lstrip("✓ "))
        if not secs:
            return
        self._refresh_interval = secs
        self._timer.stop()
        self._timer = rumps.Timer(self._on_timer, secs)
        self._timer.start()
        self._sync_config_state()

    def _toggle_verbose(self, _):
        s   = _load_settings()
        env = s.setdefault("env", {})
        if env.get("CLAUDE_VERBOSE_TOOLS") == "1":
            env.pop("CLAUDE_VERBOSE_TOOLS")
        else:
            env["CLAUDE_VERBOSE_TOOLS"] = "1"
        if not env:
            s.pop("env", None)
        _save_settings(s)
        self._sync_config_state()

    def _toggle_autocompact(self, _):
        s = _load_settings()
        s["autoCompact"] = not s.get("autoCompact", False)
        _save_settings(s)
        self._sync_config_state()

    def _open_settings_file(self, _):
        subprocess.Popen(["open", "-a", "TextEdit", str(SETTINGS_PATH)])

    def _open_status_page(self, _):
        subprocess.Popen(["open", STATUS_PAGE_URL])

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _on_timer(self, _):
        self._do_refresh()

    def _manual_refresh(self, _):
        self._do_refresh()

    def _do_refresh(self):
        log.info("Refreshing…")
        try:
            self._data = parse_usage()
            self._update_display(self._data)
            self._update_inline_graph()
        except Exception as exc:
            log.exception("Refresh failed: %s", exc)
            self.title = "⚡ ERR"
        threading.Thread(target=self._refresh_status, daemon=True).start()

    def _refresh_status(self):
        indicator, description = _fetch_claude_status()
        icon  = _STATUS_ICONS.get(indicator, "❓")
        self._lbl_status.title = f"  {icon} {description}"
        if indicator in _TITLE_ALERTS:
            self._update_title_with_status(indicator)

    def _update_title_with_status(self, indicator: str):
        icon = _STATUS_ICONS.get(indicator, "")
        cur  = self.title
        # Strip any previously prepended status icon
        for v in _STATUS_ICONS.values():
            if cur.startswith(v + " "):
                cur = cur[len(v) + 1:]
        if indicator in _TITLE_ALERTS:
            self.title = f"{icon} {cur}"

    # ── Display update ────────────────────────────────────────────────────────

    def _update_display(self, data):
        today = date.today().strftime("%Y-%m-%d")
        month = date.today().strftime("%Y-%m")

        t = _aggregate(data, prefix=today)
        m = _aggregate(data, prefix=month)
        a = _aggregate(data)

        today_tok  = t["input"] + t["output"]
        self.title = f"⚡ {_fmt_cost(t['cost'])} | {_fmt_tok(today_tok)} tok"

        self._lbl_updated.title = f"Updated: {datetime.now().strftime('%b %d  %I:%M %p')}"

        self._lbl_today_cost.title  = f"  Cost      {_fmt_cost(t['cost'])}"
        self._lbl_today_tok.title   = f"  Tokens    {_fmt_tok(t['input']+t['output'])} ({_fmt_tok(t['input'])} in / {_fmt_tok(t['output'])} out)"
        self._lbl_today_calls.title = f"  API calls {t['calls']}"

        self._lbl_month_cost.title  = f"  Cost      {_fmt_cost(m['cost'])}"
        self._lbl_month_tok.title   = f"  Tokens    {_fmt_tok(m['input']+m['output'])} ({_fmt_tok(m['input'])} in / {_fmt_tok(m['output'])} out)"
        self._lbl_month_calls.title = f"  API calls {m['calls']}"

        self._lbl_all_cost.title    = f"  Cost      {_fmt_cost(a['cost'])}"
        self._lbl_all_tok.title     = f"  Tokens    {_fmt_tok(a['input']+a['output'])} ({_fmt_tok(a['input'])} in / {_fmt_tok(a['output'])} out)"
        self._lbl_all_calls.title   = f"  API calls {a['calls']}"

        models_sorted = sorted(a["by_model"].items(), key=lambda x: -x[1]["cost"])
        for i, slot in enumerate(self._model_slots):
            if i < len(models_sorted):
                model, stats = models_sorted[i]
                slot.title = f"  {_short_model(model):<18}  {_fmt_cost(stats['cost'])}   {_fmt_tok(stats['input']+stats['output'])} tok"
            else:
                slot.title = "\u2003"

        self._sync_config_state()

        log.info("Display updated — today %s  month %s  all-time %s",
                 _fmt_cost(t["cost"]), _fmt_cost(m["cost"]), _fmt_cost(a["cost"]))


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Claude Usage Monitor v3.0 starting")
    ClaudeUsageApp().run()
