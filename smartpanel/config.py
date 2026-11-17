"""Settings: ~/.config/smartpanel/config.yaml with env-var precedence (SP-0007, SP-2402, SP-2403).

Public API (fixed by the v2 contract):
    cfg = Config.load(path=None)        # never raises; bad file -> defaults + warning
    cfg.get("swap.warn") -> value
    cfg.set("swap.warn", 70)            # validates, persists atomically, notifies
    cfg.overridden_by("swap.warn") -> "SMARTPANEL_SWAP_WARN" | None
    cfg.subscribe(fn(key, value)) -> unsubscribe
    cfg.all() -> dict[key, effective value]
    cfg.bind_store(store) -> unsubscribe  # mirrors all()/overrides into store.settings

Precedence: env var, then file, then default. The env table is `ENV_VARS`; every key
has a `Spec` in `SPECS` (kind, bounds, label, group) so the Settings page and the D-Bus
`SetSetting` share one validator.

Decisions worth keeping:
- **Loading never raises.** A missing file is normal (first run); a garbled one, or a
  bad value inside a good one, logs a warning and keeps the default for that key.
  Settings must never be the reason the panel doesn't start.
- **`set()` does raise**, with `ValueError` (`SettingOverridden` for env-owned keys):
  its callers are a UI control or a D-Bus method, and both need to tell the user why
  nothing changed.
- **A garbled env var is ignored, not obeyed** (same as `alerts.thresholds_from_env`),
  and then it doesn't count as an override either — the control stays editable.
- **Critical can't go below warning.** Raising `swap.warn` past `swap.crit` pushes crit
  up with it (SP-2404); setting crit below warn clamps it to warn; and the *effective*
  crit is clamped on every read, so env + file combinations can't invert them either.
- **Atomic save**: write a temp file in the same directory, fsync, `os.replace`. A crash
  mid-write leaves the old file, never half a YAML document. Keys the file had that we
  don't know (a newer version's settings) are kept on save.
- **Observers fire only on a real change of the effective value**, one call per key, and
  a broken observer is logged, never propagated.
"""
from __future__ import annotations

import logging
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import yaml

log = logging.getLogger(__name__)

DEFAULTS: dict[str, Any] = {
    "bar.strip": True, "bar.graphs": True, "bar.text": True, "bar.ticker": False,
    "bar.ticker_seconds": 5,
    "swap.warn": 60, "swap.crit": 85, "swap.hysteresis": 5, "swap.repeat_s": 600,
    "budget.daily_usd": None, "notifications.budget": True,
    "sessions.journal": True, "sessions.min_age_s": 120, "sessions.confirm_seconds": 5,
    "sessions.reattach": True, "sessions.dir": "~/dev/omdev", "sessions.terminal": "terminator",
    "disk.mount": "/",
    "hud.enabled": False, "hud.corner": "top-right",
    "appearance.style": "system",
    "palette.shortcut": "<Ctrl><Alt>space",
}

# Config key -> env var that overrides it (docs/architecture/v2.md "Settings keys").
ENV_VARS: dict[str, str] = {
    "swap.warn": "SMARTPANEL_SWAP_WARN",
    "swap.crit": "SMARTPANEL_SWAP_CRIT",
    "swap.hysteresis": "SMARTPANEL_SWAP_HYSTERESIS",
    "swap.repeat_s": "SMARTPANEL_SWAP_REPEAT",
    "sessions.min_age_s": "SMARTPANEL_JOURNAL_MIN_AGE",
    "sessions.reattach": "SMARTPANEL_FOCUS_REATTACH",
    "sessions.dir": "SMARTPANEL_SESSIONS_DIR",
    "sessions.terminal": "SMARTPANEL_TERMINAL",
}

HUD_CORNERS = ("top-right", "top-left", "bottom-right", "bottom-left")
STYLES = ("system", "light", "dark")


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "smartpanel" / "config.yaml"


class SettingOverridden(ValueError):
    """`set()` on a key an env var currently owns."""


# ───────────────────────── per-key specs ─────────────────────────

@dataclass(frozen=True)
class Spec:
    """How one key is validated, and how the Settings page labels it (SP-2402)."""

    kind: str                   # "bool" | "int" | "num" | "money" | "str" | "choice"
    group: str                  # Settings page group
    label: str
    hint: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    unit: str = ""

    def coerce(self, key: str, value: Any) -> Any:
        """Validated, normalised value, or ValueError naming the key."""
        match self.kind:
            case "bool":
                return _to_bool(key, value)
            case "int" | "num":
                n = _to_number(key, value)
                if self.kind == "int":
                    if n != int(n):
                        raise ValueError(f"{key} must be a whole number, got {value!r}")
                    n = int(n)
                elif n == int(n):
                    n = int(n)  # 70.0 -> 70: keeps YAML and labels tidy
                self._bounds(key, n)
                return n
            case "money":
                if value is None or (isinstance(value, str) and value.strip().lower()
                                     in ("", "none", "null", "off")):
                    return None
                n = _to_number(key, value)
                if n < 0:
                    raise ValueError(f"{key} can't be negative")
                return None if n == 0 else round(float(n), 2)
            case "str":
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{key} must be a non-empty string")
                v = value.strip()
                if key == "disk.mount" and not v.startswith("/"):
                    raise ValueError(f"{key} must be an absolute path")
                return v
            case "choice":
                if not isinstance(value, str) or value.strip().lower() not in self.choices:
                    raise ValueError(f"{key} must be one of {', '.join(self.choices)}")
                return value.strip().lower()
        raise ValueError(f"{key}: unknown kind {self.kind}")  # pragma: no cover

    def _bounds(self, key: str, n: float) -> None:
        if self.min is not None and n < self.min:
            raise ValueError(f"{key} must be at least {self.min:g}")
        if self.max is not None and n > self.max:
            raise ValueError(f"{key} must be at most {self.max:g}")


def _to_bool(key: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
    raise ValueError(f"{key} must be true or false, got {value!r}")


def _to_number(key: str, value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number, got {value!r}")
    if isinstance(value, (int, float)):
        n = float(value)
    elif isinstance(value, str):
        try:
            n = float(value.strip())
        except ValueError:
            raise ValueError(f"{key} must be a number, got {value!r}") from None
    else:
        raise ValueError(f"{key} must be a number, got {value!r}")
    if not math.isfinite(n):
        raise ValueError(f"{key} must be a finite number")
    return n


SPECS: dict[str, Spec] = {
    "bar.strip": Spec("bool", "Top bar", "Session strip",
                      "One block per session, coloured by what it’s doing"),
    "bar.graphs": Spec("bool", "Top bar", "Live graphs",
                       "CPU histogram, memory and swap meters"),
    "bar.text": Spec("bool", "Top bar", "Numbers",
                     "swap · sessions/waiting · today’s dollars"),
    "bar.ticker": Spec("bool", "Top bar", "Rotate readings",
                       "Replaces the numbers with one reading every 5 s. Alerts pin themselves."),
    "bar.ticker_seconds": Spec("int", "Top bar", "Rotate every", min=1, max=60, unit="s"),
    "swap.warn": Spec("num", "Swap alert", "Warning at",
                      "Badge in the bar and one notification", min=0, max=100, unit="%"),
    "swap.crit": Spec("num", "Swap alert", "Critical at",
                      "Repeats every 10 minutes while it lasts", min=0, max=100, unit="%"),
    "swap.hysteresis": Spec("num", "Swap alert", "Hysteresis", min=0, max=50, unit="%"),
    "swap.repeat_s": Spec("int", "Swap alert", "Repeat critical every", min=0, unit="s"),
    "budget.daily_usd": Spec("money", "Spending", "Daily budget",
                             "Shown on Overview and on every receipt", unit="$"),
    "notifications.budget": Spec("bool", "Spending", "Notify when over budget"),
    "sessions.journal": Spec("bool", "Sessions", "Journal before closing",
                             "Runs /journal in the session, then closes it"),
    "sessions.min_age_s": Spec("int", "Sessions", "Only if open longer than",
                               "Shorter sessions close straight away", min=0, unit="s"),
    "sessions.confirm_seconds": Spec("int", "Sessions", "Auto-confirm after",
                                     "The journal dialog says yes on its own",
                                     min=1, max=120, unit="s"),
    "sessions.reattach": Spec("bool", "Sessions", "Re-attach if focus fails",
                              "Opens a fresh terminal on Wayland, closing the old one"),
    "sessions.dir": Spec("str", "Sessions", "New sessions start in",
                         "Folder picker opens here"),
    "sessions.terminal": Spec("str", "Sessions", "Terminal"),
    "disk.mount": Spec("str", "System", "Disk to watch"),
    "hud.enabled": Spec("bool", "Desktop", "Desktop HUD",
                        "Spend and sessions drawn on the wallpaper"),
    "hud.corner": Spec("choice", "Desktop", "HUD corner", choices=HUD_CORNERS),
    "appearance.style": Spec("choice", "Appearance", "Window style",
                             "Follows GNOME’s dark style by default", choices=STYLES),
    "palette.shortcut": Spec("str", "Desktop", "Command palette shortcut",
                             "Set it in GNOME Settings → Keyboard; apps can’t grab keys on Wayland"),
}

assert set(SPECS) == set(DEFAULTS), "every key needs a spec"


def validate(key: str, value: Any) -> Any:
    """Coerce `value` for `key`; ValueError for unknown keys or bad values."""
    spec = SPECS.get(key)
    if spec is None:
        raise ValueError(f"unknown setting {key!r}")
    return spec.coerce(key, value)


def _flatten(tree: Any, prefix: str = "") -> dict[str, Any]:
    """{'swap': {'warn': 60}} -> {'swap.warn': 60}; flat dotted keys pass through."""
    out: dict[str, Any] = {}
    if not isinstance(tree, dict):
        return out
    for k, v in tree.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and key not in SPECS:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _nest(flat: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in sorted(flat):
        node = out
        *parents, leaf = key.split(".")
        for p in parents:
            nxt = node.setdefault(p, {})
            if not isinstance(nxt, dict):        # a scalar where a group should be
                nxt = node[p] = {}
            node = nxt
        node[leaf] = flat[key]
    return out


# ───────────────────────── Config ─────────────────────────

Observer = Callable[[str, Any], None]


class Config:
    def __init__(self, values: dict[str, Any] | None = None, path: Path | None = None,
                 *, env: Mapping[str, str] | None = None):
        """`values` are file-level values (validated; bad ones dropped with a warning).
        `env` is the environment to read overrides from; None means *no* overrides,
        so a bare `Config()` is deterministic. `Config.load()` passes `os.environ`."""
        self.path = Path(path) if path is not None else None
        self._file: dict[str, Any] = {}
        self._extra: dict[str, Any] = {}      # unknown keys from the file, kept on save
        self._env: dict[str, tuple[str, Any]] = {}
        self._subs: list[Observer] = []
        self._stores: list[Any] = []
        for key, raw in (values or {}).items():
            self._accept_file_value(key, raw)
        self._read_env(env or {})

    @classmethod
    def load(cls, path: Path | None = None, *, env: Mapping[str, str] | None = None
             ) -> "Config":
        """Read the YAML file (default `~/.config/smartpanel/config.yaml`). Never raises."""
        p = Path(path) if path is not None else default_path()
        cfg = cls(path=p, env=os.environ if env is None else env)
        cfg._file, cfg._extra = cls._read_file(p)
        return cfg

    # ── reading ──
    @staticmethod
    def _read_file(p: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            text = p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, {}
        except OSError as e:
            log.warning("can't read %s (%s); using defaults", p, e)
            return {}, {}
        try:
            tree = yaml.safe_load(text)
        except yaml.YAMLError as e:
            log.warning("%s is not valid YAML (%s); using defaults", p, e)
            return {}, {}
        if tree is None:
            return {}, {}
        if not isinstance(tree, dict):
            log.warning("%s should hold a mapping of settings; using defaults", p)
            return {}, {}
        tmp = Config()
        for key, raw in _flatten(tree).items():
            tmp._accept_file_value(key, raw, source=str(p))
        return tmp._file, tmp._extra

    def _accept_file_value(self, key: str, raw: Any, source: str = "values") -> None:
        if key not in SPECS:
            log.warning("%s: unknown setting %r ignored", source, key)
            self._extra[key] = raw
            return
        try:
            self._file[key] = validate(key, raw)
        except ValueError as e:
            log.warning("%s: %s; using the default", source, e)

    def _read_env(self, env: Mapping[str, str]) -> None:
        for key, name in ENV_VARS.items():
            raw = env.get(name)
            if raw is None or raw == "":
                continue
            try:
                self._env[key] = (name, validate(key, raw))
            except ValueError as e:
                log.warning("%s=%r ignored: %s", name, raw, e)

    # ── public API ──
    def get(self, key: str) -> Any:
        if key not in SPECS:   # same as the wave-0 stub: unknown keys read as None
            log.warning("unknown setting %r read", key)
            return None
        if key == "swap.crit":  # effective crit is never below effective warn
            return max(self._raw("swap.crit"), self._raw("swap.warn"))
        return self._raw(key)

    def _raw(self, key: str) -> Any:
        if key in self._env:
            return self._env[key][1]
        if key in self._file:
            return self._file[key]
        return DEFAULTS[key]

    def set(self, key: str, value: Any) -> None:
        """Validate, persist atomically, notify observers of what really changed.

        Raises ValueError for an unknown key or a bad value, and SettingOverridden when an
        env var owns the key (the UI greys those controls out; D-Bus callers get the error).
        """
        if key not in SPECS:
            raise ValueError(f"unknown setting {key!r}")
        if key in self._env:
            name = self._env[key][0]
            raise SettingOverridden(f"{key} is set by {name}; unset it to change this here")
        v = validate(key, value)
        before = self.all()
        self._file[key] = v
        if key == "swap.warn" and "swap.crit" not in self._env and self._raw("swap.crit") < v:
            self._file["swap.crit"] = v         # SP-2404: crit is pushed up, not refused
        elif key == "swap.crit" and v < self._raw("swap.warn"):
            self._file["swap.crit"] = self._raw("swap.warn")
        self.save()
        self._notify(before)

    def overridden_by(self, key: str) -> str | None:
        entry = self._env.get(key)
        return entry[0] if entry else None

    def overrides(self) -> dict[str, str]:
        return {k: name for k, (name, _v) in self._env.items()}

    def subscribe(self, fn: Observer) -> Callable[[], None]:
        self._subs.append(fn)
        return lambda: self._subs.remove(fn) if fn in self._subs else None

    def all(self) -> dict[str, Any]:
        return {k: self.get(k) for k in DEFAULTS}

    def reload(self) -> None:
        """Re-read the file (e.g. after a hand edit) and notify what changed."""
        if self.path is None:
            return
        before = self.all()
        self._file, self._extra = self._read_file(self.path)
        self._notify(before)

    # ── persistence ──
    def save(self) -> bool:
        """Atomically write the file-level values. Logs and returns False on failure;
        the in-memory value still applies for this run."""
        if self.path is None:
            return False
        data = _nest({**self._extra, **self._file})
        tmp_name = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".config.", suffix=".tmp",
                                            dir=str(self.path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("# smartPanel settings. Env vars (SMARTPANEL_*) take precedence.\n")
                yaml.safe_dump(data, fh, sort_keys=True, allow_unicode=True,
                               default_flow_style=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
            tmp_name = None
            return True
        except OSError as e:
            log.warning("can't save %s (%s); the change applies until restart", self.path, e)
            return False
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    # ── notification ──
    def _notify(self, before: dict[str, Any]) -> None:
        after = self.all()
        changed = [k for k in DEFAULTS if before[k] != after[k]]
        if not changed:
            return
        for store in list(self._stores):
            self._mirror(store)
        for key in changed:
            for fn in list(self._subs):
                try:
                    fn(key, after[key])
                except Exception:
                    log.exception("settings observer failed for %s", key)

    # ── store mirror (the Settings page reads store.settings) ──
    def bind_store(self, store) -> Callable[[], None]:
        """Mirror `all()` and the env overrides into `store.settings`, now and on every
        change. Idempotent per store. Call on the main thread (it mutates the Store)."""
        if store not in self._stores:
            self._stores.append(store)
        self._mirror(store)
        return lambda: self._stores.remove(store) if store in self._stores else None

    def _mirror(self, store) -> None:
        store.update("settings", values=self.all(), overridden_by=self.overrides())
