"""Panel item: StatusNotifierItem + DBusMenu over Gio, wide PNG bar icon + XAyatanaLabel.

Glue between the `Store` and the two tray protocols. All logic lives in the pure
modules (`barspec`, `menu`, `barrender`, `dbusmenu` model); this file only decides
*when* to push what:

* Store change -> recompute `BarSpec` and the menu rows.
* Label: `XAyatanaNewLabel` only when the label or guide string changed.
* Icon: re-rendered only when the `IconSpec` changed. Structural changes (strip
  blocks, swap level, toggles) go out at once. Graph-only value changes redraw at
  the CPU interval, no faster (SP-0203): a new CPU sample renders (at most once
  per `icon_min_interval`), while a memory/swap value moving between two CPU
  samples is parked and folded into the next CPU-driven render. If the CPU
  history stops moving, the parked change still goes out after
  `icon_fallback_interval`.
* The icon's `IconAccessibleDesc` is `BarSpec.icon_desc` (no live readings), the
  live numbers go in the ToolTip, so a CPU tick never makes the host reload an
  unchanged PNG (SP-1905).
* Menu: fixed rows, property updates only (SP-2002).
* Ticker (A5): a `GLib.timeout_add_seconds(bar.ticker_seconds)` that exists only
  while `bar.ticker` is on; it advances `app.ticker_index` unless pinned (by the
  user via `actions.pin_ticker`, or by a swap alert). Scrolling on the item steps it.
* Middle click (and Activate) -> `actions.open_dashboard('')`, the last page.

Main-loop rule: everything here is sub-millisecond except the PNG render (~1 ms).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from gi.repository import GLib

from smartpanel.tray import barspec, menu
from smartpanel.tray.barrender import IconWriter
from smartpanel.tray.dbusmenu import DbusMenuServer
from smartpanel.tray.sni import StatusNotifierItem

log = logging.getLogger(__name__)

ITEM_PATH = "/org/ayatana/NotificationItem/smartpanel"
MENU_SUFFIX = "/Menu"
BAR_SECTIONS = {"system", "spend", "sessions", "app", "settings"}
MENU_AGE_REFRESH_S = 30


class _ConfigView:
    """`get(key)`: the Config first, then the settings mirrored into the store."""

    def __init__(self, config: Any, store: Any):
        self._config, self._store = config, store

    def get(self, key: str) -> Any:
        if self._config is not None and hasattr(self._config, "get"):
            try:
                v = self._config.get(key)
            except Exception:
                v = None
            if v is not None:
                return v
        vals = getattr(getattr(self._store.state, "settings", None), "values", None) or {}
        return vals.get(key)


class Tray:
    def __init__(self, store, actions, config, theme=None, *, icon_dir=None,
                 path: str = ITEM_PATH, item_id: str = "smartpanel",
                 icon_min_interval: float = 1.0, icon_fallback_interval: float = 10.0):
        self.store, self.actions, self.config = store, actions, config
        self.theme = theme
        self.path = path
        self.item_id = item_id
        self.icon_min_interval = icon_min_interval
        self.icon_fallback_interval = icon_fallback_interval
        self.cfg = _ConfigView(config, store)
        self.icons = IconWriter(icon_dir)
        self.sni: StatusNotifierItem | None = None
        self.menu_server: DbusMenuServer | None = None
        self.spec: barspec.BarSpec | None = None
        self.rows: dict[int, menu.MenuRow] = {}
        self._unsubs: list = []
        self._ticker_id = 0
        self._ticker_secs = 0
        self._age_id = 0
        self._icon_pending_id = 0
        self._icon_pending: barspec.IconSpec | None = None
        self._icon_applied: barspec.IconSpec | None = None
        self._icon_last_render = 0.0
        self.icon_renders = 0

    # ───────────────────────── lifecycle ─────────────────────────

    def start(self, connection) -> None:
        if self.sni is not None:
            return
        menu_path = self.path + MENU_SUFFIX
        self.menu_server = DbusMenuServer(menu_path, on_event=self._on_menu_event)
        self.sni = StatusNotifierItem(
            self.path, item_id=self.item_id, title="smartPanel", menu_path=menu_path,
            icon_name=barspec.ICON_PLAIN,
            on_activate=lambda _x, _y: self._open_dashboard(),
            on_secondary=self._open_dashboard,
            on_scroll=self._on_scroll)
        # Initial values before export, so the host's first GetAll sees real data.
        self.refresh(force=True)
        self.menu_server.export(connection)
        self.sni.export(connection)
        self._unsubs.append(self.store.subscribe(self._on_store))
        if self.config is not None and hasattr(self.config, "subscribe"):
            self._unsubs.append(self.config.subscribe(self._on_config))
        self._sync_ticker()
        self._age_id = GLib.timeout_add_seconds(MENU_AGE_REFRESH_S, self._on_age_tick)

    def stop(self) -> None:
        for unsub in self._unsubs:
            try:
                unsub()
            except Exception:
                pass
        self._unsubs.clear()
        for attr in ("_ticker_id", "_age_id", "_icon_pending_id"):
            sid = getattr(self, attr)
            if sid:
                GLib.source_remove(sid)
                setattr(self, attr, 0)
        if self.sni:
            self.sni.unexport()
        if self.menu_server:
            self.menu_server.unexport()
        self.sni = self.menu_server = None
        self.icons.cleanup(keep_none=True)
        self._icon_applied = None

    # ───────────────────────── updates ─────────────────────────

    def _on_store(self, sections: set[str]) -> None:
        if not sections & BAR_SECTIONS:
            return
        if "settings" in sections:
            self._sync_ticker()
        self.refresh()

    def _on_config(self, key: str, _value: Any) -> None:
        if key.startswith("bar.") or key in ("hud.enabled", "palette.shortcut"):
            self._sync_ticker()
            self.refresh()

    def refresh(self, force: bool = False) -> None:
        try:
            self._refresh_bar(force)
        except Exception:
            log.exception("tray bar refresh failed")
        try:
            self._refresh_menu()
        except Exception:
            log.exception("tray menu refresh failed")

    def compose(self) -> barspec.BarSpec:
        state = self.store.state
        tpl = barspec.render_template_with_guide(self.theme, state)
        if tpl is None:
            return barspec.compose_bar(state, self.cfg)
        return barspec.compose_bar(state, self.cfg, template_label=tpl[0],
                                   template_guide_str=tpl[1])

    def _refresh_bar(self, force: bool = False) -> None:
        spec = self.compose()
        self.spec = spec
        sni = self.sni
        if sni is None:
            return
        sni.set_label(spec.label, spec.guide)
        sni.set_tooltip("smartPanel", spec.accessible_desc)
        if spec.icon is None:
            # Loading / plain icon. `_icon_applied` is kept: when loading ends with
            # the same IconSpec, the existing PNG is shown again without a render.
            self._cancel_pending_icon()
            sni.set_icon(spec.icon_name, spec.icon_desc)
        else:
            self._apply_icon(spec.icon, spec.icon_desc, force)

    def _refresh_menu(self) -> None:
        if self.menu_server is None:
            return
        rows = menu.build_rows(self.store.state, self.cfg, time.time())
        self.rows = {r.id: r for r in rows}
        self.menu_server.set_root(menu.to_node(rows))

    # ── icon throttling ──
    @staticmethod
    def _structural(a: barspec.IconSpec | None, b: barspec.IconSpec) -> bool:
        if a is None:
            return True
        return (a.strip, a.strip_more, a.graphs, a.swap_level, a.swap_pct is None) != \
               (b.strip, b.strip_more, b.graphs, b.swap_level, b.swap_pct is None)

    def _apply_icon(self, icon: barspec.IconSpec, desc: str, force: bool = False) -> None:
        current = self.icons.current
        if (icon == self._icon_applied and not force and current is not None
                and current.exists()):
            self._cancel_pending_icon()
            if self.sni:
                self.sni.set_icon(str(current), desc)
            return
        applied = self._icon_applied
        if force or self._structural(applied, icon):
            self._cancel_pending_icon()
            self._render_icon(icon, desc)
            return
        elapsed = time.monotonic() - self._icon_last_render
        if icon.cpu != applied.cpu:
            # A CPU tick: this is the redraw cadence (the new spec already carries
            # the latest memory/swap values, so any parked change folds in here).
            if elapsed >= self.icon_min_interval:
                self._cancel_pending_icon()
                self._render_icon(icon, desc)
            else:
                self._park_icon(icon, self.icon_min_interval - elapsed)
            return
        # Memory/swap moved between CPU ticks: wait for the next CPU sample.
        self._park_icon(icon, max(self.icon_fallback_interval - elapsed, 0.001))

    def _park_icon(self, icon: barspec.IconSpec, delay_s: float) -> None:
        self._icon_pending = icon
        if not self._icon_pending_id:
            delay = max(1, int(delay_s * 1000))
            self._icon_pending_id = GLib.timeout_add(delay, self._on_icon_pending)

    def _on_icon_pending(self) -> bool:
        self._icon_pending_id = 0
        icon, self._icon_pending = self._icon_pending, None
        if icon is not None and icon != self._icon_applied and self.spec is not None:
            self._render_icon(icon, self.spec.icon_desc)
        return False

    def _cancel_pending_icon(self) -> None:
        if self._icon_pending_id:
            GLib.source_remove(self._icon_pending_id)
            self._icon_pending_id = 0
        self._icon_pending = None

    def _render_icon(self, icon: barspec.IconSpec, desc: str) -> None:
        try:
            path = self.icons.write(icon)
        except Exception:
            log.exception("bar icon render failed; falling back to the plain icon")
            if self.sni:
                self.sni.set_icon(barspec.ICON_PLAIN, desc)
            self._icon_applied = None
            return
        self._icon_last_render = time.monotonic()
        self._icon_applied = icon
        self.icon_renders += 1
        if self.sni:
            self.sni.set_icon(str(path), desc)

    # ── ticker ──
    def _ticker_on(self) -> bool:
        return bool(self.cfg.get("bar.ticker"))

    def _sync_ticker(self) -> None:
        on = self._ticker_on() and self.sni is not None
        try:
            secs = max(1, int(self.cfg.get("bar.ticker_seconds") or 5))
        except (TypeError, ValueError):
            secs = 5
        if self._ticker_id and (not on or secs != self._ticker_secs):
            GLib.source_remove(self._ticker_id)
            self._ticker_id = 0
        if on and not self._ticker_id:
            self._ticker_secs = secs
            self._ticker_id = GLib.timeout_add_seconds(secs, self._on_ticker)

    @property
    def ticker_running(self) -> bool:
        return bool(self._ticker_id)

    def _on_ticker(self) -> bool:
        try:
            self.advance_ticker(1, manual=False)
        except Exception:
            log.exception("ticker step failed")
        return True

    def advance_ticker(self, step: int = 1, *, manual: bool = True) -> bool:
        """Move the A5 reading. The timer respects pins; a scroll (manual) only the alert."""
        state = self.store.state
        if barspec.ticker_alert(state) is not None:
            return False
        if state.app.ticker_pinned and not manual:
            return False
        n = len(barspec.TICKER_KEYS)
        self.store.update("app", ticker_index=(state.app.ticker_index + step) % n)
        return True

    def _on_scroll(self, delta: int, _orientation: str) -> None:
        if self._ticker_on() and delta:
            self.advance_ticker(1 if delta > 0 else -1, manual=True)

    # ── menu / clicks ──
    def _on_menu_event(self, nid: int, event_id: str, _ts: int) -> None:
        if event_id != "clicked":
            return
        row = self.rows.get(nid)
        if row is None or not row.enabled or not row.visible:
            return
        menu.invoke(self.actions, row.action)

    def _open_dashboard(self) -> None:
        try:
            self.actions.open_dashboard("")
        except Exception:
            log.exception("open_dashboard failed")

    def _on_age_tick(self) -> bool:
        try:
            self._refresh_menu()
        except Exception:
            log.exception("tray menu age refresh failed")
        return True
