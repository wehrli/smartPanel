// Unit tests for extension/smartpanel-hud@omdev.dev/throttle.js (SP-2301 rate limit).
// Run: gjs -m tests/js/test_throttle.js

import {eq, ok, run, test, throws} from './harness.js';
import {RefreshThrottle} from '../../extension/smartpanel-hud@omdev.dev/throttle.js';

/** Fake clock + timers; fetches complete only when the test says so. */
function rig(intervalMs = 5000) {
    const r = {t: 0, timers: [], fetches: [], cancelled: 0};
    r.throttle = new RefreshThrottle({
        intervalMs,
        now: () => r.t,
        schedule: (fn, ms) => {
            const timer = {fn, at: r.t + ms, live: true};
            r.timers.push(timer);
            return timer;
        },
        cancel: timer => {
            timer.live = false;
            r.cancelled++;
        },
        fetch: done => r.fetches.push({at: r.t, done}),
    });
    r.advance = ms => {
        const end = r.t + ms;
        for (;;) {
            const next = r.timers.filter(x => x.live && x.at <= end).sort((a, b) => a.at - b.at)[0];
            if (!next)
                break;
            r.t = next.at;
            next.live = false;
            next.fn();
        }
        r.t = end;
    };
    r.finish = () => r.fetches[r.fetches.length - 1].done();
    r.live = () => r.timers.filter(x => x.live).length;
    return r;
}

test('first request fetches at once', () => {
    const r = rig();
    r.throttle.request();
    eq(r.fetches.length, 1);
    ok(r.throttle.inFlight);
});

test('a burst inside the interval collapses into one trailing fetch at +5 s', () => {
    const r = rig();
    r.throttle.request();
    r.finish();
    r.advance(1000);
    for (let i = 0; i < 10; i++)
        r.throttle.request();
    eq(r.fetches.length, 1, 'no fetch before the interval');
    eq(r.live(), 1, 'exactly one timer');
    r.advance(3999);
    eq(r.fetches.length, 1);
    r.advance(1);
    eq(r.fetches.length, 2);
    eq(r.fetches[1].at, 5000);
});

test('never more than one fetch every 5 s under a steady stream of Changed', () => {
    const r = rig();
    for (let t = 0; t < 60000; t += 250) {
        r.throttle.request();
        if (r.throttle.inFlight)
            r.finish();
        r.advance(250);
    }
    const gaps = r.fetches.slice(1).map((f, i) => f.at - r.fetches[i].at);
    ok(gaps.every(g => g >= 5000), `gaps ${gaps}`);
    ok(r.fetches.length >= 11 && r.fetches.length <= 13, `count ${r.fetches.length}`);
});

test('a request after a quiet period fetches immediately', () => {
    const r = rig();
    r.throttle.request();
    r.finish();
    r.advance(7000);
    r.throttle.request();
    eq(r.fetches.length, 2);
    eq(r.fetches[1].at, 7000);
});

test('urgent (settings changed) skips the wait and cancels the trailing timer', () => {
    const r = rig();
    r.throttle.request();
    r.finish();
    r.advance(500);
    r.throttle.request();
    eq(r.live(), 1);
    r.throttle.request({urgent: true});
    eq(r.fetches.length, 2);
    eq(r.live(), 0);
    eq(r.cancelled, 1);
});

test('never two fetches in flight; a request during a fetch replays after it', () => {
    const r = rig();
    r.throttle.request();
    r.throttle.request({urgent: true});
    r.throttle.request();
    eq(r.fetches.length, 1);
    r.finish();
    // the replayed request was urgent, so it goes out at once
    eq(r.fetches.length, 2);
    r.finish();
    eq(r.fetches.length, 2);
});

test('a non-urgent request during a fetch is replayed through the interval', () => {
    const r = rig();
    r.throttle.request();
    r.advance(100);
    r.throttle.request();
    r.finish();
    eq(r.fetches.length, 1);
    eq(r.live(), 1);
    r.advance(4900);
    eq(r.fetches.length, 2);
});

test('reset drops timers, pending requests and ignores a stale done()', () => {
    const r = rig();
    r.throttle.request();
    const stale = r.fetches[0].done;
    r.throttle.request();
    r.throttle.reset();
    ok(!r.throttle.inFlight);
    stale();
    eq(r.fetches.length, 1, 'stale done() must not replay the pending request');
    r.throttle.request();
    eq(r.fetches.length, 2, 'after reset the next request fetches at once');
    r.finish();
    r.advance(10);
    r.throttle.request();
    eq(r.live(), 1);
    r.throttle.reset();
    eq(r.live(), 0);
    ok(!r.throttle.scheduled);
});

test('done() is idempotent', () => {
    const r = rig();
    r.throttle.request();
    r.advance(10);
    r.throttle.request({urgent: true});
    const done = r.fetches[0].done;
    done();
    done();
    eq(r.fetches.length, 2);
});

test('a throwing fetch does not wedge the throttle', () => {
    let calls = 0;
    const t = new RefreshThrottle({
        now: () => 0, schedule: () => null, cancel: () => {},
        fetch: () => {
            calls++;
            throw new Error('boom');
        },
    });
    throws(() => t.request());
    ok(!t.inFlight);
    throws(() => t.request({urgent: true}));
    eq(calls, 2);
});

await run();
