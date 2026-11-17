"""PanelState builders for the tray tests (busy / empty / notmux / many / loading / first).

Built directly from the state model, mirroring the prototype's scenarios
(`busySessions()` and `DEFAULTS()` in docs/design/prototype.html), so the tray tests
don't depend on the demo package being written in parallel.
"""
from __future__ import annotations

from smartpanel.state.model import (
    IDLE, LEVEL_CRITICAL, LEVEL_WARNING, PHASE_JOURNALING, PHASE_LAUNCHING, RUNNING,
    WAITING, JournalProgress, Loading, PanelState, Prompt, Session,
)

NOW = 1_763_390_000.0          # a fixed "now" so ages are deterministic


def _cpu_hist():
    return [18.0, 25.0, 31.0, 22.0, 40.0, 55.0, 23.0, 19.0, 64.0, 80.0, 12.0, 23.0]


def base() -> PanelState:
    st = PanelState()
    s = st.system
    s.cpu.percent, s.cpu.temp_c, s.cpu.cores = 23.0, 61.0, 16
    s.cpu.history = _cpu_hist()
    s.memory.percent, s.memory.used_gb, s.memory.total_gb = 68.0, 10.9, 16.0
    s.swap.percent, s.swap.used_gb, s.swap.total_gb = 12.0, 0.96, 8.0
    sp = st.spend
    sp.cost_today, sp.cost_week, sp.cost_lifetime = 23.84, 142.10, 2318.55
    sp.burn_per_hour = 3.10
    sp.tokens_today, sp.tokens_today_human = 41_200_000, "41.2M"
    return st


def busy_sessions() -> list[Session]:
    return [
        Session("claude-smartPanel", project="smartPanel", title="Fix swap alert hysteresis",
                activity=WAITING, attached=True, created=NOW - 62 * 60,
                prompt=Prompt("Do you want to make this edit to alerts.py?",
                              ["Yes", "Yes, allow all edits this session",
                               "No, tell Claude what to do differently"])),
        Session("claude-tacomap", project="tacomap", title="Truck search filters",
                activity=RUNNING, created=NOW - 38 * 60, doing="Bash · npm test -- filters"),
        Session("claude-JobScout", project="JobScout", title="Stripe webhook retries",
                activity=RUNNING, created=NOW - 12 * 60,
                doing="Waiting for 2 background agents"),
        Session("claude-ledgerly", project="ledgerly", title="Migrate goose 0023",
                activity=IDLE, created=NOW - 190 * 60),
    ]


def busy() -> PanelState:
    st = base()
    st.sessions.items = busy_sessions()
    return st


def empty() -> PanelState:
    st = base()
    st.sessions.items = []
    return st


def notmux() -> PanelState:
    st = base()
    st.sessions.items = []
    st.sessions.tmux_ok = False
    return st


def many() -> PanelState:
    st = base()
    projects = ["PromptKit", "JobScout", "tacomap", "wishlist", "LeadPilot",
                "noteKeeper", "ledgerly", "giftswap", "smartPanel", "netConfigs"]
    acts = [WAITING, WAITING, RUNNING, RUNNING, RUNNING, IDLE, IDLE, IDLE, IDLE, IDLE]
    st.sessions.items = [
        Session(f"claude-{p}", project=p, title=f"Work on {p}", activity=a,
                created=NOW - (i + 1) * 600)
        for i, (p, a) in enumerate(zip(projects, acts))
    ]
    return st


def transitional() -> PanelState:
    """One launching and one journaling session next to the busy four."""
    st = busy()
    st.sessions.items = [
        st.sessions.items[0],
        Session("claude-wishlist", project="wishlist", phase=PHASE_JOURNALING,
                activity=IDLE, created=NOW - 3600, journal=JournalProgress(step=2)),
        Session("claude-netConfigs", project="netConfigs", phase=PHASE_LAUNCHING,
                created=NOW - 3),
        st.sessions.items[1],
    ]
    return st


def loading() -> PanelState:
    st = busy()
    st.spend.cost_today = None
    st.app.loading = Loading(done=812, total=1284)
    return st


def first() -> PanelState:
    st = base()
    st.spend.cost_today = None
    st.spend.burn_per_hour = None
    st.app.first_run = True
    return st


def swap_warn() -> PanelState:
    st = busy()
    st.system.swap.percent, st.system.swap.level = 66.0, LEVEL_WARNING
    return st


def swap_crit() -> PanelState:
    st = busy()
    st.system.swap.percent, st.system.swap.level = 88.0, LEVEL_CRITICAL
    return st


def noswap() -> PanelState:
    st = busy()
    st.system.swap.percent = st.system.swap.used_gb = st.system.swap.total_gb = None
    return st


def gpu() -> PanelState:
    st = busy()
    g = st.system.gpu
    g.available, g.vendor, g.percent, g.temp_c = True, "nvidia", 41.0, 58.0
    g.vram_used_gb, g.vram_total_gb = 3.1, 8.0
    return st


SCENARIOS = {
    "busy": busy, "empty": empty, "notmux": notmux, "many": many,
    "transitional": transitional, "loading": loading, "first": first,
    "swap_warn": swap_warn, "swap_crit": swap_crit, "noswap": noswap, "gpu": gpu,
}
