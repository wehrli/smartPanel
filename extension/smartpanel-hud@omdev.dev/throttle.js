// Rate limiter for GetState refreshes (SP-2301: the HUD updates at most every 5 s).
//
// Plain JS with injected clock and timers so it is unit-tested under bare gjs
// (tests/js/test_throttle.js); extension.js passes GLib-backed ones.
//
// Semantics:
// - request() fetches at once when the last fetch started >= interval ago, otherwise it
//   schedules ONE trailing fetch for when the interval has elapsed. Bursts of Changed
//   signals (the service coalesces to 250 ms) collapse into that one fetch.
// - request({urgent: true}) skips the wait: used for `settings` changes, so toggling the
//   HUD or moving its corner shows at once instead of up to 5 s later.
// - Never two fetches in flight: a request during a fetch is remembered and replayed
//   (through the same rules) when the fetch reports done().
// - reset() drops everything (name vanished / extension disabled).

export const DEFAULT_INTERVAL_MS = 5000;

export class RefreshThrottle {
    /**
     * @param {object} opts
     * @param {(done: () => void) => void} opts.fetch  start a fetch; call done() when finished
     * @param {() => number} opts.now                  monotonic milliseconds
     * @param {(fn: () => void, ms: number) => any} opts.schedule  one-shot timer, returns a handle
     * @param {(handle: any) => void} opts.cancel      cancel a pending timer
     * @param {number} [opts.intervalMs]
     */
    constructor({fetch, now, schedule, cancel, intervalMs = DEFAULT_INTERVAL_MS}) {
        this._fetch = fetch;
        this._now = now;
        this._schedule = schedule;
        this._cancel = cancel;
        this.intervalMs = intervalMs;
        this._last = null;         // when the last fetch started
        this._timer = null;
        this._inFlight = false;
        this._pending = false;     // a request arrived during the fetch
        this._pendingUrgent = false;
        this._generation = 0;      // bumps on reset(), so a stale done() is ignored
    }

    get inFlight() {
        return this._inFlight;
    }

    get scheduled() {
        return this._timer !== null;
    }

    request({urgent = false} = {}) {
        if (this._inFlight) {
            this._pending = true;
            this._pendingUrgent ||= urgent;
            return;
        }
        const now = this._now();
        const due = this._last === null ? now : this._last + this.intervalMs;
        if (urgent || now >= due) {
            this._clearTimer();
            this._start();
            return;
        }
        if (this._timer === null) {
            this._timer = this._schedule(() => {
                this._timer = null;
                this._start();
            }, due - now);
        }
    }

    reset() {
        this._clearTimer();
        this._inFlight = false;
        this._pending = false;
        this._pendingUrgent = false;
        this._last = null;
        this._generation++;
    }

    _clearTimer() {
        if (this._timer !== null) {
            this._cancel(this._timer);
            this._timer = null;
        }
    }

    _start() {
        if (this._inFlight)
            return;
        this._inFlight = true;
        this._last = this._now();
        const gen = this._generation;
        let finished = false;
        const done = () => {
            if (finished || gen !== this._generation)
                return;
            finished = true;
            this._inFlight = false;
            if (this._pending) {
                const urgent = this._pendingUrgent;
                this._pending = false;
                this._pendingUrgent = false;
                this.request({urgent});
            }
        };
        try {
            this._fetch(done);
        } catch (e) {
            done();
            throw e;
        }
    }
}
