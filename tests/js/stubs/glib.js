// Real GLib for Variant / VariantType / Error; fake clock and timers.
import RealGLib from 'gi://GLib';
import {fake} from './fake.js';

export default {
    Variant: RealGLib.Variant,
    VariantType: RealGLib.VariantType,
    Error: RealGLib.Error,
    PRIORITY_DEFAULT: RealGLib.PRIORITY_DEFAULT,
    PRIORITY_DEFAULT_IDLE: RealGLib.PRIORITY_DEFAULT_IDLE,
    markup_escape_text: (text, len) => RealGLib.markup_escape_text(text, len),
    SOURCE_REMOVE: false,
    SOURCE_CONTINUE: true,
    get_monotonic_time: () => fake.now * 1000,
    timeout_add(_priority, ms, fn) {
        const id = fake.nextTimer++;
        fake.timers.set(id, {fn, ms, at: fake.now + ms});
        return id;
    },
    idle_add(_priority, fn) {
        const id = fake.nextTimer++;
        fake.timers.set(id, {fn, ms: 0, at: fake.now, idle: true});
        return id;
    },
    source_remove(id) {
        if (!fake.timers.delete(id))
            throw new Error(`source_remove of unknown source ${id}`);
        return true;
    },
};
