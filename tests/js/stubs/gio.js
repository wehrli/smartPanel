// Fake Gio: name watching, signal subscription and method calls are recorded so the
// test can play the smartPanel service. Error enums are the real ones.
import RealGio from 'gi://Gio';
import RealGLib from 'gi://GLib';
import {fake} from './fake.js';

class Cancellable {
    constructor() {
        this.cancelled = false;
    }

    cancel() {
        this.cancelled = true;
    }

    is_cancelled() {
        return this.cancelled;
    }
}

const session = {
    signal_subscribe(sender, iface, member, path, arg0, flags, cb) {
        const id = fake.nextSub++;
        fake.subscriptions.set(id, {sender, iface, member, path, cb});
        return id;
    },
    signal_unsubscribe(id) {
        if (!fake.subscriptions.delete(id))
            throw new Error(`unsubscribe of unknown id ${id}`);
    },
    call(name, path, iface, method, params, replyType, flags, timeout, cancellable, cb) {
        const conn = session;
        const entry = {name, path, iface, method, params, replyType, timeout, cancellable};
        const finish = result => cb(conn, result);
        entry.reply = json => {
            fake.calls.splice(fake.calls.indexOf(entry), 1);
            finish({variant: new RealGLib.Variant('(s)', [json])});
        };
        entry.fail = error => {
            fake.calls.splice(fake.calls.indexOf(entry), 1);
            finish({error});
        };
        fake.calls.push(entry);
    },
    call_finish(res) {
        if (res.error)
            throw res.error;
        return res.variant;
    },
};

export default {
    BusType: {SESSION: 2},
    BusNameWatcherFlags: {NONE: 0},
    DBusSignalFlags: {NONE: 0},
    DBusCallFlags: {NONE: 0},
    IOErrorEnum: RealGio.IOErrorEnum,
    Cancellable,
    DBus: {session},
    bus_watch_name(busType, name, flags, appeared, vanished) {
        const id = fake.nextWatch++;
        fake.watches.set(id, {name, appeared, vanished});
        return id;
    },
    bus_unwatch_name(id) {
        if (!fake.watches.delete(id))
            throw new Error(`unwatch of unknown id ${id}`);
    },
};
