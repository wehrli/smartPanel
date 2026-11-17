// Fake of resource:///org/gnome/shell/ui/main.js: just the layout manager bits the HUD uses.
import {Signals} from './fake.js';
import {Widget} from './st.js';

class LayoutManager extends Signals {
    constructor() {
        super();
        this.reset();
    }

    reset() {
        this._handlers.clear();
        this._backgroundGroup = new Widget({name: 'backgroundGroup'});
        // the wallpaper actor the shell already put there
        this._backgroundGroup.add_child(new Widget({name: 'wallpaper'}));
        this.primaryIndex = 0;
        this.workAreas = [{x: 0, y: 32, width: 1920, height: 1048}];
    }

    getWorkAreaForMonitor(i) {
        return this.workAreas[i];
    }
}

export const layoutManager = new LayoutManager();
