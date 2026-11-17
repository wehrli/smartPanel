// Fake of resource:///org/gnome/shell/extensions/extension.js
export class Extension {
    constructor(metadata = {}) {
        this.metadata = metadata;
        this.uuid = metadata.uuid;
        this.path = metadata.path;
    }

    enable() {}

    disable() {}
}
