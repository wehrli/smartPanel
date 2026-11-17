// smartPanel HUD: every bit of formatting and derivation, as plain JS.
//
// No shell, GI or GLib imports here: this module is unit-tested with bare `gjs -m`
// (tests/js/test_hudmodel.js). extension.js only turns the model into actors.
//
// The input is the JSON that dev.omdev.SmartPanel1.GetState returns, i.e.
// smartpanel.state.store.Store.to_dict() — field names match smartpanel/state/model.py
// exactly. Any section can be missing (GetState with a subset, an older service) and
// any field can be null ("unknown"); nothing here may throw on that.
//
// Design source: docs/design/prototype.html, function hudHTML() and the `.hud` CSS (B6).

// Sections the HUD asks GetState for.
export const HUD_SECTIONS = ['spend', 'sessions', 'system', 'settings', 'app'];

export const CORNERS = ['top-right', 'top-left', 'bottom-right', 'bottom-left'];
export const DEFAULT_CORNER = 'top-right';
export const DEFAULT_MARGIN = 30;           // px from the work-area edges (prototype: right:30px)
export const MAX_MARGIN = 400;
export const MAX_SESSION_ROWS = 6;
export const HUD_WIDTH = 240;               // prototype .hud width
export const MAX_TITLE_CHARS = 60;          // St ellipsizes too; this caps pathological titles

export const LABEL = 'Claude · today';      // shown upper-cased, as the CSS text-transform did
export const OFFLINE_TEXT = 'smartPanel not running';

// Lifecycle phase wins over activity (prototype `state`), then the activity itself.
// Order and words come from the prototype's ORDER and META tables.
export const STATE_ORDER = {waiting: 0, running: 1, journaling: 2, launching: 3, idle: 4, unknown: 5};
export const STATE_WORDS = {
    waiting: 'needs you',
    running: 'running',
    journaling: 'journaling',
    launching: 'starting',
    idle: 'idle',
    unknown: 'unknown',
};

const LEVEL_WARNING = 1;
const LEVEL_CRITICAL = 2;

// ───────────────────────── small helpers ─────────────────────────

function isObj(v) {
    return v !== null && typeof v === 'object' && !Array.isArray(v);
}

function obj(v) {
    return isObj(v) ? v : {};
}

function num(v) {
    return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function str(v) {
    return typeof v === 'string' ? v : '';
}

/** `41.2M` style token count, used when the service didn't send `tokens_today_human`. */
export function humanTokens(n) {
    n = num(n);
    if (n === null || n < 0)
        return '—';
    if (n >= 1e9)
        return `${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6)
        return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3)
        return `${(n / 1e3).toFixed(1)}K`;
    return String(Math.round(n));
}

/** `23%`, or `—` when unknown. */
export function percent(v) {
    v = num(v);
    return v === null ? '—' : `${Math.round(v)}%`;
}

/** Download rate: `2.4 MB/s` (prototype), `45 KB/s` below 0.1 MB/s, `—` when unknown. */
export function rate(bps) {
    bps = num(bps);
    if (bps === null || bps < 0)
        return '—';
    if (bps >= 1e5)
        return `${(bps / 1e6).toFixed(1)} MB/s`;
    return `${Math.round(bps / 1e3)} KB/s`;
}

/** Session.display_title (a Python property, so not in the JSON): title, project, name. */
export function displayTitle(s) {
    s = obj(s);
    let t = str(s.title) || str(s.project) || str(s.name) || 'session';
    t = t.replace(/\s+/g, ' ').trim();
    if (t.length > MAX_TITLE_CHARS)
        t = `${t.slice(0, MAX_TITLE_CHARS - 1)}…`;
    return t;
}

/** One state key per session: journaling/launching phase first, else the activity. */
export function sessionState(s) {
    s = obj(s);
    if (s.phase === 'journaling' || s.phase === 'launching')
        return s.phase;
    if (s.activity === 'waiting' || s.activity === 'running' || s.activity === 'idle')
        return s.activity;
    return 'unknown';
}

/** Stable sort by state, so the service's own order is kept within a state. */
export function sortSessions(items) {
    if (!Array.isArray(items))
        return [];
    return items
        .filter(isObj)
        .map((s, i) => ({s, i, k: STATE_ORDER[sessionState(s)]}))
        .sort((a, b) => a.k - b.k || a.i - b.i)
        .map(x => x.s);
}

/** Effective HUD settings from `settings.values`, validated. */
export function hudSettings(state) {
    const values = obj(obj(obj(state).settings).values);
    const corner = CORNERS.includes(values['hud.corner']) ? values['hud.corner'] : DEFAULT_CORNER;
    let margin = num(values['hud.margin']);
    margin = margin === null ? DEFAULT_MARGIN : Math.min(MAX_MARGIN, Math.max(0, Math.round(margin)));
    return {enabled: values['hud.enabled'] === true, corner, margin};
}

// ───────────────────────── the model ─────────────────────────

function spendLines(spend, app) {
    const loading = isObj(app.loading);
    if (loading) {
        const done = num(app.loading.done), total = num(app.loading.total);
        const progress = total ? `reading ${done ?? 0}/${total}` : 'reading transcripts';
        return {headline: '…', sub: `usd · ${progress}`};
    }
    if (app.first_run === true)
        return {headline: '—', sub: 'usd · no transcripts yet'};

    const cost = num(spend.cost_today);
    const tokens = str(spend.tokens_today_human) || humanTokens(spend.tokens_today);
    const burn = num(spend.burn_per_hour);
    return {
        headline: cost === null ? '—' : cost.toFixed(2),
        sub: `usd · ${tokens} tokens · ${burn === null ? '—' : burn.toFixed(2)}/h`,
    };
}

function sessionRows(sessions, app) {
    // `sessions.tmux_ok` is the contract field; SP-2605 also mentions `app.tmux_ok`.
    if (sessions.tmux_ok === false || app.tmux_ok === false)
        return [{left: 'tmux not installed', right: '!', style: 'crit'}];
    const sorted = sortSessions(sessions.items);
    if (!sorted.length)
        return [{left: 'No sessions', right: '', style: 'none'}];
    const rows = sorted.slice(0, MAX_SESSION_ROWS).map(s => {
        const st = sessionState(s);
        return {left: displayTitle(s), right: STATE_WORDS[st], style: st};
    });
    if (sorted.length > MAX_SESSION_ROWS)
        rows.push({left: `+${sorted.length - MAX_SESSION_ROWS} more`, right: '', style: 'more'});
    return rows;
}

function swapLevel(swap) {
    // No swap configured is not 0% full and never alerts (SP-2608).
    if (num(swap.percent) === null)
        return 0;
    const level = num(swap.level);
    return level === null ? 0 : level;
}

function metricRows(system) {
    const swap = obj(system.swap);
    const level = swapLevel(swap);
    return [
        {label: 'cpu', value: percent(obj(system.cpu).percent), style: ''},
        {label: 'memory', value: percent(obj(system.memory).percent), style: ''},
        {
            label: 'swap',
            value: percent(swap.percent),
            style: level >= LEVEL_CRITICAL ? 'crit' : level >= LEVEL_WARNING ? 'warn' : '',
        },
        {label: 'net ↓', value: rate(obj(system.net).down_bps), style: ''},
    ];
}

function alertLine(system) {
    const level = swapLevel(obj(system.swap));
    if (level >= LEVEL_CRITICAL)
        return '⚠ swap critical';
    if (level >= LEVEL_WARNING)
        return '⚠ swap high';
    return null;
}

/**
 * GetState JSON (parsed) -> everything the HUD draws.
 *
 * @returns {{visible: boolean, offline: boolean, corner: string, margin: number,
 *   label: string, headline: string, sub: string,
 *   rows: {left: string, right: string, style: string}[],
 *   metrics: {label: string, value: string, style: string}[],
 *   alert: string|null, message: string|null}}
 */
export function buildHudModel(state) {
    const st = obj(state);
    const {enabled, corner, margin} = hudSettings(st);
    const app = obj(st.app);
    const system = obj(st.system);
    const {headline, sub} = spendLines(obj(st.spend), app);
    return {
        visible: enabled,
        offline: false,
        corner,
        margin,
        label: LABEL.toUpperCase(),
        headline,
        sub,
        rows: sessionRows(obj(st.sessions), app),
        metrics: metricRows(system),
        alert: alertLine(system),
        message: null,
    };
}

/**
 * The service vanished. Show a dim note only if the HUD was on when we last heard from
 * it; otherwise show nothing (we don't know whether the user wants a HUD at all).
 */
export function buildOfflineModel(last = null) {
    const s = isObj(last) ? last : {enabled: false, corner: DEFAULT_CORNER, margin: DEFAULT_MARGIN};
    return {
        visible: s.enabled === true,
        offline: true,
        corner: CORNERS.includes(s.corner) ? s.corner : DEFAULT_CORNER,
        margin: num(s.margin) ?? DEFAULT_MARGIN,
        label: LABEL.toUpperCase(),
        headline: '',
        sub: '',
        rows: [],
        metrics: [],
        alert: null,
        message: OFFLINE_TEXT,
    };
}

/**
 * Where to put a box of `size` inside `workArea` (primary monitor, panels and docks
 * excluded) for a corner and margin. Pixel-rounded and clamped inside the work area.
 */
export function placeHud(workArea, size, corner = DEFAULT_CORNER, margin = DEFAULT_MARGIN) {
    const wa = obj(workArea);
    const ax = num(wa.x) ?? 0, ay = num(wa.y) ?? 0;
    const aw = num(wa.width) ?? 0, ah = num(wa.height) ?? 0;
    const w = num(obj(size).width) ?? 0, h = num(obj(size).height) ?? 0;
    const c = CORNERS.includes(corner) ? corner : DEFAULT_CORNER;
    const m = num(margin) ?? DEFAULT_MARGIN;

    let x = c.endsWith('right') ? ax + aw - m - w : ax + m;
    let y = c.startsWith('bottom') ? ay + ah - m - h : ay + m;
    x = Math.max(ax, Math.min(x, ax + aw - w));
    y = Math.max(ay, Math.min(y, ay + ah - h));
    return {x: Math.round(x), y: Math.round(y)};
}

/** Text alignment that reads naturally from the chosen corner. */
export function alignFor(corner) {
    return String(corner).endsWith('left') ? 'left' : 'right';
}

/** Cheap structural identity, so the view only rebuilds when the picture changes. */
export function modelKey(model) {
    return JSON.stringify(model);
}
