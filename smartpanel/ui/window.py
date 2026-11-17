"""The dashboard window, B1 (SP-2101..2109): sidebar, header, banner, toasts, dialogs.

Layout::

    Adw.ApplicationWindow
     └ Adw.NavigationSplitView          (collapses below 560sp)
        ├ sidebar: brand · 6 nav rows with badges · footer (updated / version)
        └ content: Adw.ToolbarView
             ├ top: HeaderBar (page title, palette button, window controls)
             ├ top: swap Banner (SP-0106)
             └ Adw.ToastOverlay → Gtk.Stack of pages

Pages come from `smartpanel.ui.pages.<name>.build(store, actions, config)`, are
built lazily on first visit and then cached: they subscribe to the store
themselves and update in place, so scroll position, focus and selection survive
every tick (SP-2108). The shell wraps a page in a vertical `Gtk.ScrolledWindow`
unless it already scrolls (a ScrolledWindow, an Adw.StatusPage, an
Adw.PreferencesPage, or a module that sets `SCROLLS = True`). A page module may
define `show_detail(widget, detail)` to handle routes like `sessions:board` or
`journal:<id>`; `widget` is what its `build` returned.

The window itself only reads the store and only touches what changed: badges
and the banner are deduped, the footer text is re-set only when it differs.
Closing hides the window (pages and their subscriptions stay alive, so
reopening is instant); size, maximized state and last page are saved to
`~/.local/state/smartpanel/window.json`.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

from smartpanel.state.model import PAGES  # noqa: E402
from smartpanel.ui import shellmodel, tokens  # noqa: E402
from smartpanel.ui.dialogs import DialogHost  # noqa: E402
from smartpanel.ui.toasts import ToastHost  # noqa: E402
from smartpanel.ui.widgets import Banner, EmptyState, NavBadge  # noqa: E402

log = logging.getLogger(__name__)

_DEFAULT = object()
FOOTER_TICK_S = 1
SECTIONS_FOR_FOOTER = {"system", "spend", "sessions", "app"}

# Keyboard (SP-2107). Esc on dialogs is libadwaita's own behaviour.
ACCELS: dict[str, list[str]] = {
    "win.palette": ["<Control>k"],
    "win.close": ["<Control>w"],
    **{f"win.page::{name}": [f"<Control>{i}"] for i, name in enumerate(PAGES, start=1)},
}


def _self_scrolling(module, widget: Gtk.Widget) -> bool:
    return bool(getattr(module, "SCROLLS", False)) or isinstance(
        widget, (Gtk.ScrolledWindow, Adw.StatusPage, Adw.PreferencesPage))


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app, store, actions, config, *, state_path: Any = _DEFAULT,
                 os_name: str | None = None):
        tokens.install()
        super().__init__(application=app, title="smartPanel")
        self.store, self.actions, self.config = store, actions, config
        self._state_path: Path | None = (shellmodel.window_state_path()
                                         if state_path is _DEFAULT else state_path)
        self._os_name = shellmodel.os_label() if os_name is None else os_name
        self._pages: dict[str, Gtk.Widget] = {}       # name -> widget returned by build
        self._modules: dict[str, Any] = {}
        self._page: str | None = None
        self._selecting = False
        self._footer_timer = 0

        ws = shellmodel.load_window_state(self._state_path)
        self.set_default_size(ws.width, ws.height)
        self.set_size_request(shellmodel.MIN_W, shellmodel.MIN_H)
        if ws.maximized:
            self.maximize()
        self.set_hide_on_close(True)

        self._build_sidebar()
        self._build_content()
        self.split = Adw.NavigationSplitView(sidebar=self._sidebar_page,
                                             content=self._content_page)
        self.split.set_min_sidebar_width(180)
        self.split.set_max_sidebar_width(230)
        self.set_content(self.split)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)
        # Collapsing (narrow window) lands on the page that was asked for, not
        # on the sidebar; the header's back button reaches the sidebar.
        self.split.connect("notify::collapsed", lambda sv, _p: sv.get_collapsed()
                           and self._page and sv.set_show_content(True))

        self.toasts = ToastHost(self.toast_overlay, self.show_page)
        self.dialogs = DialogHost(self)
        self._install_actions(app)

        self._unsubscribe = store.subscribe(self._on_store_changed)
        self.connect("close-request", self._on_close_request)
        self.connect("map", lambda *_: self._start_footer_timer())
        self.connect("unmap", lambda *_: self._stop_footer_timer())

        self._refresh_all()
        self.show_page(ws.page)

    # ───────────────────────── construction ─────────────────────────

    def _build_sidebar(self) -> None:
        brand = Gtk.Box(spacing=8)
        brand.add_css_class("sp-brand")
        mark = Gtk.Box(valign=Gtk.Align.CENTER, accessible_role=Gtk.AccessibleRole.PRESENTATION)
        mark.add_css_class("sp-brand-mark")
        brand.append(mark)
        brand.append(Gtk.Label(label="smartPanel"))
        header = Adw.HeaderBar(title_widget=brand)

        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.nav.add_css_class("navigation-sidebar")
        self.nav.add_css_class("sp-nav")
        self.nav.update_property([Gtk.AccessibleProperty.LABEL], ["Pages"])
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self.badges: dict[str, NavBadge] = {}
        for i, name in enumerate(PAGES, start=1):
            row = Gtk.ListBoxRow()
            row.page_name = name
            box = Gtk.Box(spacing=10, margin_top=2, margin_bottom=2)
            box.append(Gtk.Image(icon_name=shellmodel.PAGE_ICONS[name],
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
            label = Gtk.Label(label=shellmodel.PAGE_TITLES[name], xalign=0, hexpand=True)
            label.add_css_class("sp-nav-label")
            box.append(label)
            badge = NavBadge()
            box.append(badge)
            row.set_child(box)
            row.set_tooltip_text(f"{shellmodel.PAGE_TITLES[name]} (Ctrl+{i})")
            self.nav.append(row)
            self._rows[name] = row
            self.badges[name] = badge
        self.nav.connect("row-selected", self._on_row_selected)
        self.nav.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.nav)

        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        footer.add_css_class("sp-footer")
        self.footer_status = Gtk.Label(xalign=0, ellipsize=3)  # END
        self.footer_version = Gtk.Label(xalign=0, ellipsize=3)
        footer.append(self.footer_status)
        footer.append(self.footer_version)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.set_content(scroller)
        view.add_bottom_bar(footer)
        self._sidebar_page = Adw.NavigationPage(title="smartPanel", child=view, tag="sidebar")

    def _build_content(self) -> None:
        self.window_title = Adw.WindowTitle(title="Overview")
        header = Adw.HeaderBar(title_widget=self.window_title)
        search = Gtk.Button(icon_name="system-search-symbolic", action_name="win.palette")
        search.set_tooltip_text("Command palette (Ctrl+K)")
        search.update_property([Gtk.AccessibleProperty.LABEL], ["Command palette"])
        header.pack_end(search)
        self.search_button = search

        self.banner = Banner(on_action=lambda: self.show_page("system"))
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=120, vexpand=True, hexpand=True)
        self.toast_overlay = Adw.ToastOverlay(child=self.stack)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        view.set_content(self.toast_overlay)
        self._content_page = Adw.NavigationPage(title="Overview", child=view, tag="content")

    def _install_actions(self, app) -> None:
        palette = Gio.SimpleAction.new("palette", None)
        palette.connect("activate", lambda *_: self.actions.open_palette())
        self.add_action(palette)

        page = Gio.SimpleAction.new("page", GLib.VariantType.new("s"))
        page.connect("activate", lambda _a, v: self.show_page(v.get_string()))
        self.add_action(page)

        close = Gio.SimpleAction.new("close", None)
        close.connect("activate", lambda *_: self.close())
        self.add_action(close)

        if app is not None:
            for action, accels in ACCELS.items():
                app.set_accels_for_action(action, accels)

    # ───────────────────────── routing (SP-2102) ─────────────────────────

    @property
    def page(self) -> str | None:
        return self._page

    def show_page(self, target: str | None = None) -> Gtk.Widget:
        """Switch to `target` ('system', 'sessions:board', 'journal:<id>').

        An empty target keeps the current page; an unknown one falls back to
        Overview. Returns the page widget.
        """
        if not (target or "").strip() and self._page:
            target = self._page
        name, detail = shellmodel.parse_page(target)
        widget = self._ensure_page(name)
        self.stack.set_visible_child_name(name)
        if name != self._page:
            self._page = name
            title = shellmodel.PAGE_TITLES[name]
            self.window_title.set_title(title)
            self._content_page.set_title(title)
            self._select_row(name)
            self._update_banner()
        if self.split.get_collapsed():
            self.split.set_show_content(True)
        if detail:
            show_detail = getattr(self._modules.get(name), "show_detail", None)
            if callable(show_detail):
                try:
                    show_detail(widget, detail)
                except Exception:  # noqa: BLE001 — a page bug must not break routing
                    log.exception("page %s failed to show detail %r", name, detail)
        return widget

    def present_page(self, target: str | None = None) -> None:
        self.show_page(target)
        self.present()

    def page_widget(self, name: str) -> Gtk.Widget | None:
        return self._pages.get(name)

    def _ensure_page(self, name: str) -> Gtk.Widget:
        if name in self._pages:
            return self._pages[name]
        module = None
        try:
            module = importlib.import_module(f"smartpanel.ui.pages.{name}")
            widget = module.build(self.store, self.actions, self.config)
        except Exception as e:  # noqa: BLE001
            log.exception("page %s failed to build", name)
            widget = EmptyState(f"{shellmodel.PAGE_TITLES[name]} failed to load", str(e),
                                glyph="!")
        self._modules[name] = module
        self._pages[name] = widget
        child = widget
        if not _self_scrolling(module, widget):
            child = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                       vexpand=True, hexpand=True)
            child.set_child(widget)
        self.stack.add_named(child, name)
        return widget

    def _select_row(self, name: str) -> None:
        row = self._rows.get(name)
        if row is not None and self.nav.get_selected_row() is not row:
            self._selecting = True
            try:
                self.nav.select_row(row)
            finally:
                self._selecting = False

    def _on_row_selected(self, _box, row) -> None:
        if row is not None and not self._selecting:
            self.show_page(row.page_name)

    def _on_row_activated(self, _box, row) -> None:
        # Activating the already-selected row in collapsed mode must still
        # navigate to the content.
        if row is not None:
            self.show_page(row.page_name)

    # ───────────────────────── live updates (SP-2108) ─────────────────────────

    def _on_store_changed(self, sections: set[str]) -> None:
        if sections & {"sessions", "system"}:
            self._update_badges()
        if "system" in sections:
            self._update_banner()
        if sections & SECTIONS_FOR_FOOTER:
            self._update_footer()

    def _refresh_all(self) -> None:
        self._update_badges()
        self._update_banner()
        self._update_footer()

    def _update_badges(self) -> None:
        state = self.store.state
        for name in ("sessions", "system"):
            self.badges[name].set_badge(shellmodel.nav_badge(name, state))

    def _update_banner(self) -> None:
        model = shellmodel.swap_banner(self.store.state, self._page or "")
        if model is None:
            self.banner.hide()
        else:
            self.banner.show(model.text, model.level,
                             "Open System" if model.show_action else None)

    def _update_footer(self) -> bool:
        state = self.store.state
        status = shellmodel.footer_status(state)
        if self.footer_status.get_text() != status:
            self.footer_status.set_text(status)
        version = shellmodel.footer_version(state, self._os_name)
        if self.footer_version.get_text() != version:
            self.footer_version.set_text(version)
        return True

    def _start_footer_timer(self) -> None:
        if not self._footer_timer:
            self._update_footer()
            self._footer_timer = GLib.timeout_add_seconds(FOOTER_TICK_S, self._update_footer)

    def _stop_footer_timer(self) -> None:
        if self._footer_timer:
            GLib.source_remove(self._footer_timer)
            self._footer_timer = 0

    # ───────────────────────── toasts / dialogs ─────────────────────────

    def toast(self, data: dict) -> None:
        self.toasts.show(data)

    def present_dialog(self, dialog: Adw.Dialog) -> Adw.Dialog:
        return self.dialogs.present(dialog)

    # ───────────────────────── persistence ─────────────────────────

    def window_state(self) -> shellmodel.WindowState:
        maximized = self.is_maximized()
        w, h = self.get_default_size()
        if not maximized and self.get_width() > 0:
            w, h = self.get_width(), self.get_height()
        return shellmodel.WindowState(width=w, height=h, maximized=maximized,
                                      page=self._page or shellmodel.DEFAULT_PAGE)

    def _on_close_request(self, *_):
        shellmodel.save_window_state(self._state_path, self.window_state())
        self.dialogs.close_all()
        return False  # hide-on-close does the rest

    def shutdown(self) -> None:
        """Called by the app on quit: persist, drop subscriptions, destroy."""
        shellmodel.save_window_state(self._state_path, self.window_state())
        self._stop_footer_timer()
        if self._unsubscribe:
            self._unsubscribe()
            self._unsubscribe = None
        self.set_hide_on_close(False)
        self.destroy()
