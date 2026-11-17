// Real GLib for Variant / VariantType / Error; fake clock and timers.
import RealGLib from 'gi://GLib';
import {fake} from './fake.js';

export default {
    Variant: RealGLib.Variant,
    VariantType: RealGLib.VariantType,
    Error: RealGLib.Error,
    PRIORITY_DEFAULT: RealGLib.PRIORITY_DEFAULT,
    SOURCE_REMOVE: false,
    SOURCE_CONTINUE: true,
    get_monotonic_time: () => fake.now * 1000,
    timeout_add(_priority, ms, fn) {
        const id = fake.nextTimer++;
        fake.timers.set(id, {fn, ms, at: fake.now + ms});
        return id;
    },
    source_remove(id) {
        if (!fake.timers.delete(id))
            throw new Error(`source_remove of unknown source ${id}`);
        return true;
    },
};
