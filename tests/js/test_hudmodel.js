// Unit tests for extension/smartpanel-hud@omdev.dev/hudmodel.js.
// Run: gjs -m tests/js/test_hudmodel.js   (exit code != 0 on failure)
//
// Fixtures in tests/fixtures/hud/*.json are Store.to_dict() output of real
// smartpanel.state.model dataclasses (tests/test_hud_extension.py checks they still
// match the model), so these tests break if the HUD reads a field the model lacks.

import {dirOf, eq, ok, readJson, run, test} from './harness.js';
import {
    CORNERS,
    MAX_SESSION_ROWS,
    OFFLINE_TEXT,
    STATE_WORDS,
    alignFor,
    buildHudModel,
    buildOfflineModel,
    displayTitle,
    humanTokens,
    hudSettings,
    modelKey,
    percent,
    placeHud,
    rate,
    sessionState,
    sortSessions,
} from '../../extension/smartpanel-hud@omdev.dev/hudmodel.js';

const FIX = `${dirOf(import.meta.url)}/../fixtures/hud`;
const fixture = name => readJson(`${FIX}/${name}.json`);

const BUSY_METRICS = [
    {label: 'cpu', value: '23%', style: ''},
    {label: 'memory', value: '68%', style: ''},
    {label: 'swap', value: '12%', style: ''},
    {label: 'net ↓', value: '2.4 MB/s', style: ''},
];

// ── the prototype's B6 HUD, from the busy scenario ──

test('busy: spend headline and sub line (SP-0809)', () => {
    const m = buildHudModel(fixture('busy'));
    eq(m.visible, true);
    eq(m.offline, false);
    eq(m.label, 'CLAUDE · TODAY');
    eq(m.headline, '23.84');
    eq(m.sub, 'usd · 41.2M tokens · 3.10/h');
    eq(m.message, null);
});

test('busy: session rows with state words, waiting first (SP-1213)', () => {
    eq(buildHudModel(fixture('busy')).rows, [
        {left: 'Fix swap alert hysteresis', right: 'needs you', style: 'waiting'},
        {left: 'Truck search filters', right: 'running', style: 'running'},
        {left: 'Stripe webhook retries', right: 'running', style: 'running'},
        {left: 'Migrate goose 0023', right: 'idle', style: 'idle'},
    ]);
});

test('busy: cpu / memory / swap / net rows (SP-0206, SP-0305, SP-0504, SP-0110)', () => {
    const m = buildHudModel(fixture('busy'));
    eq(m.metrics, BUSY_METRICS);
    eq(m.alert, null);
});

test('busy: default corner and margin', () => {
    const m = buildHudModel(fixture('busy'));
    eq(m.corner, 'top-right');
    eq(m.margin, 30);
});

// ── many sessions ──

test('many: 6 rows then "+3 more", ordered by state, stable within a state (SP-1214)', () => {
    const m = buildHudModel(fixture('many'));
    eq(m.rows.length, MAX_SESSION_ROWS + 1);
    eq(m.rows.map(r => [r.left, r.style]), [
        ['Fix swap alert hysteresis', 'waiting'],
        ['Gift list sharing links', 'waiting'],
        ['Truck search filters', 'running'],
        ['Stripe webhook retries', 'running'],
        ['Advisor dashboard filters', 'journaling'],
        ['claude-netConfigs', 'launching'],
        ['+3 more', 'more'],
    ]);
    eq(m.rows[4].right, 'journaling');
    eq(m.rows[5].right, 'starting');
    eq(m.rows[6].right, '');
});

test('many: critical swap colours the row and adds the alert line (SP-0110)', () => {
    const m = buildHudModel(fixture('many'));
    eq(m.metrics[2], {label: 'swap', value: '93%', style: 'crit'});
    eq(m.alert, '⚠ swap critical');
});

test('many: corner and margin from settings (SP-2304)', () => {
    const m = buildHudModel(fixture('many'));
    eq(m.corner, 'bottom-left');
    eq(m.margin, 48);
});

test('many: low download rate falls back to KB/s', () => {
    eq(buildHudModel(fixture('many')).metrics[3].value, '45 KB/s');
});

test('swap warning: amber row and "⚠ swap high"', () => {
    const m = buildHudModel(fixture('swap_warning'));
    eq(m.metrics[2], {label: 'swap', value: '72%', style: 'warn'});
    eq(m.alert, '⚠ swap high');
});

// ── app states (E26) ──

test('loading: "…" headline with the scan progress (SP-2602)', () => {
    const m = buildHudModel(fixture('loading'));
    eq(m.headline, '…');
    eq(m.sub, 'usd · reading 812/1284');
    // CPU, memory and sessions don't depend on the scan; they still show.
    eq(m.metrics, BUSY_METRICS);
    eq(m.rows.length, 4);
});

test('loading with an unknown total', () => {
    const s = fixture('loading');
    s.app.loading = {done: 0, total: 0};
    eq(buildHudModel(s).sub, 'usd · reading transcripts');
});

test('first run: "—" headline (SP-2604) and "No sessions" (SP-2607)', () => {
    const m = buildHudModel(fixture('first_run'));
    eq(m.headline, '—');
    eq(m.sub, 'usd · no transcripts yet');
    eq(m.rows, [{left: 'No sessions', right: '', style: 'none'}]);
});

test('loading wins over first run', () => {
    const s = fixture('first_run');
    s.app.loading = {done: 1, total: 2};
    eq(buildHudModel(s).headline, '…');
});

test('tmux missing: one "tmux not installed" row with a red "!" (SP-2606)', () => {
    eq(buildHudModel(fixture('no_tmux')).rows,
        [{left: 'tmux not installed', right: '!', style: 'crit'}]);
});

test('tmux missing via app.tmux_ok (SP-2605 wording) is honoured too', () => {
    const s = fixture('busy');
    s.app.tmux_ok = false;
    eq(buildHudModel(s).rows[0].left, 'tmux not installed');
});

test('no sessions (SP-2607)', () => {
    eq(buildHudModel(fixture('no_sessions')).rows,
        [{left: 'No sessions', right: '', style: 'none'}]);
});

test('no swap: "—", no colour, never an alert even with a stale level (SP-2608)', () => {
    const m = buildHudModel(fixture('no_swap'));
    eq(m.metrics[2], {label: 'swap', value: '—', style: ''});
    eq(m.alert, null);
});

test('disabled: not visible, corner still parsed (SP-2303)', () => {
    const m = buildHudModel(fixture('disabled'));
    eq(m.visible, false);
    eq(m.corner, 'top-left');
});

test('fresh PanelState(): hidden, every value "—", nothing throws', () => {
    const m = buildHudModel(fixture('fresh'));
    eq(m.visible, false);
    eq(m.headline, '—');
    eq(m.sub, 'usd · 0 tokens · —/h');
    eq(m.metrics.map(x => x.value), ['—', '—', '—', '—']);
    eq(m.rows, [{left: 'No sessions', right: '', style: 'none'}]);
    eq(m.alert, null);
});

// ── robustness: GetState with missing sections or junk ──

test('garbage input never throws and stays hidden', () => {
    for (const junk of [null, undefined, 42, 'x', [], {}, {settings: null},
        {settings: {values: []}}, {sessions: {items: 'nope'}}, {sessions: {items: [null, 3]}},
        {system: {swap: {percent: 'NaN', level: 'high'}}}, {spend: {cost_today: NaN}}]) {
        const m = buildHudModel(junk);
        eq(m.visible, false, JSON.stringify(junk));
        ok(Array.isArray(m.rows) && Array.isArray(m.metrics));
    }
});

test('hud.enabled must be literally true', () => {
    for (const v of ['true', 1, 'yes', null])
        eq(hudSettings({settings: {values: {'hud.enabled': v}}}).enabled, false, String(v));
});

test('bad corner / margin fall back or clamp', () => {
    const s = v => hudSettings({settings: {values: v}});
    eq(s({'hud.corner': 'middle'}).corner, 'top-right');
    eq(s({'hud.margin': -5}).margin, 0);
    eq(s({'hud.margin': 9999}).margin, 400);
    eq(s({'hud.margin': '12'}).margin, 30);
    eq(s({'hud.margin': 12.6}).margin, 13);
});

// ── helpers ──

test('displayTitle mirrors Session.display_title and caps length', () => {
    eq(displayTitle({name: 'claude-x', project: 'x', title: 'T'}), 'T');
    eq(displayTitle({name: 'claude-x', project: 'x', title: ''}), 'x');
    eq(displayTitle({name: 'claude-x', project: '', title: ''}), 'claude-x');
    eq(displayTitle({title: 'a\n  b'}), 'a b');
    const long = displayTitle({title: 'x'.repeat(200)});
    eq(long.length, 60);
    ok(long.endsWith('…'));
});

test('sessionState: phase beats activity; unknown activity stays unknown', () => {
    eq(sessionState({activity: 'running', phase: 'journaling'}), 'journaling');
    eq(sessionState({activity: 'unknown', phase: 'launching'}), 'launching');
    eq(sessionState({activity: 'waiting', phase: 'none'}), 'waiting');
    eq(sessionState({activity: 'bogus'}), 'unknown');
    eq(STATE_WORDS[sessionState({})], 'unknown');
});

test('sortSessions is stable and drops non-objects', () => {
    const items = [{name: 'a', activity: 'idle'}, null, {name: 'b', activity: 'waiting'},
        {name: 'c', activity: 'idle'}, {name: 'd', activity: 'waiting'}];
    eq(sortSessions(items).map(s => s.name), ['b', 'd', 'a', 'c']);
    eq(sortSessions(undefined), []);
});

test('formatters', () => {
    eq(percent(23.5), '24%');
    eq(percent(null), '—');
    eq(rate(2_400_000), '2.4 MB/s');
    eq(rate(100_000), '0.1 MB/s');
    eq(rate(0), '0 KB/s');
    eq(rate(-1), '—');
    eq(humanTokens(41_200_000), '41.2M');
    eq(humanTokens(1_500), '1.5K');
    eq(humanTokens(2_100_000_000), '2.1B');
    eq(humanTokens(12), '12');
    eq(humanTokens(null), '—');
});

test('tokens fall back to tokens_today when tokens_today_human is empty', () => {
    const s = fixture('busy');
    s.spend.tokens_today_human = '';
    eq(buildHudModel(s).sub, 'usd · 41.2M tokens · 3.10/h');
});

// ── offline ──

test('offline: dim note only when the HUD was on last time we heard', () => {
    const on = buildOfflineModel({enabled: true, corner: 'bottom-right', margin: 10});
    eq(on.visible, true);
    eq(on.offline, true);
    eq(on.message, OFFLINE_TEXT);
    eq(on.corner, 'bottom-right');
    eq(on.margin, 10);
    eq(on.rows, []);
    eq(buildOfflineModel(null).visible, false);
    eq(buildOfflineModel({enabled: false}).visible, false);
});

// ── placement (SP-2304) ──

const WA = {x: 0, y: 32, width: 1920, height: 1048};   // top bar 32 px tall
const SIZE = {width: 240, height: 300};

test('placeHud: each corner inside the work area', () => {
    eq(placeHud(WA, SIZE, 'top-right', 30), {x: 1650, y: 62});
    eq(placeHud(WA, SIZE, 'top-left', 30), {x: 30, y: 62});
    eq(placeHud(WA, SIZE, 'bottom-right', 30), {x: 1650, y: 750});
    eq(placeHud(WA, SIZE, 'bottom-left', 30), {x: 30, y: 750});
});

test('placeHud: secondary-offset monitor and bad corner', () => {
    const wa = {x: 1920, y: 0, width: 1280, height: 1024};
    eq(placeHud(wa, SIZE, 'top-right', 0), {x: 2960, y: 0});
    eq(placeHud(wa, SIZE, 'nope', 0), {x: 2960, y: 0});
});

test('placeHud: clamps when the HUD is bigger than the margin allows', () => {
    const tiny = {x: 0, y: 0, width: 200, height: 100};
    eq(placeHud(tiny, SIZE, 'bottom-right', 30), {x: 0, y: 0});
});

test('alignFor follows the corner', () => {
    eq(CORNERS.map(alignFor), ['right', 'left', 'right', 'left']);
});

test('modelKey changes only when the picture does', () => {
    const a = buildHudModel(fixture('busy'));
    const b = buildHudModel(fixture('busy'));
    eq(modelKey(a), modelKey(b));
    const s = fixture('busy');
    s.system.cpu.percent = 99;
    ok(modelKey(buildHudModel(s)) !== modelKey(a));
    // history churn is invisible on the HUD, so it must not trigger a rebuild
    const h = fixture('busy');
    h.system.cpu.history.push(55);
    eq(modelKey(buildHudModel(h)), modelKey(a));
});

await run();
