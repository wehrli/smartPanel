// Shared state of the fake GNOME Shell used by test_extension.js. Every stub module
// imports this one file, so the test sees exactly what extension.js did.
export const fake = {
    now: 0,                 // ms, drives GLib.get_monotonic_time and timers
    timers: new Map(),      // id -> {fn, at}
    nextTimer: 1,
    watches: new Map(),     // id -> {name, appeared, vanished}
    nextWatch: 1,
    subscriptions: new Map(),
    nextSub: 1,
    calls: [],              // pending D-Bus calls: {method, params, cancellable, reply(json), fail(err)}
    actors: [],             // every St actor ever created
    warnings: [],
};

export function resetFake() {
    fake.now = 0;
    fake.timers.clear();
    fake.watches.clear();
    fake.subscriptions.clear();
    fake.calls.length = 0;
    fake.actors.length = 0;
    fake.warnings.length = 0;
}

/** Run due timers in order, moving the clock forward by `ms`. */
export function advance(ms) {
    const end = fake.now + ms;
    for (;;) {
        const due = [...fake.timers.entries()].filter(([, t]) => t.at <= end)
            .sort((a, b) => a[1].at - b[1].at)[0];
        if (!due)
            break;
        const [id, t] = due;
        fake.now = t.at;
        if (!t.fn())
            fake.timers.delete(id);
        else
            t.at = fake.now + t.ms;
    }
    fake.now = end;
}

export class Signals {
    constructor() {
        this._handlers = new Map();
        this._next = 1;
    }

    connect(name, fn) {
        const id = this._next++;
        this._handlers.set(id, {name, fn});
        return id;
    }

    disconnect(id) {
        if (!this._handlers.delete(id))
            throw new Error(`disconnect of unknown handler ${id}`);
    }

    emit(name, ...args) {
        for (const {name: n, fn} of [...this._handlers.values()]) {
            if (n === name)
                fn(this, ...args);
        }
    }

    get handlerCount() {
        return this._handlers.size;
    }
}
