// A tiny fake of St: enough actor tree to inspect what extension.js builds.
import {fake, Signals} from './fake.js';

export class Widget extends Signals {
    constructor(props = {}) {
        super();
        this.reactive = false;
        this.visible = true;
        this.x = 0;
        this.y = 0;
        this.children = [];
        this.parent = null;
        this.destroyed = false;
        const self = this;
        this.clutter_text = {
            ellipsize: 0,
            set_markup(markup) {
                self.markup = markup;
                self.text = markup.replace(/<[^>]*>/g, '').replace(/&lt;/g, '<')
                    .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&apos;/g, "'")
                    .replace(/&amp;/g, '&');
            },
        };
        Object.assign(this, props);
        fake.actors.push(this);
    }

    add_child(child) {
        if (child.parent)
            throw new Error('actor already has a parent');
        child.parent = this;
        this.children.push(child);
    }

    remove_child(child) {
        this.children = this.children.filter(c => c !== child);
        child.parent = null;
    }

    get_children() {
        return [...this.children];
    }

    set_child_above_sibling(child, sibling) {
        if (!this.children.includes(child))
            throw new Error('not a child');
        if (sibling !== null)
            throw new Error('test fake only supports null sibling');
        this.children = this.children.filter(c => c !== child).concat([child]);
    }

    destroy_all_children() {
        for (const c of [...this.children])
            c.destroy();
    }

    destroy() {
        this.destroy_all_children();
        this.parent?.remove_child(this);
        this.destroyed = true;
    }

    show() {
        this.visible = true;
    }

    hide() {
        this.visible = false;
    }

    set_position(x, y) {
        this.x = x;
        this.y = y;
    }

    /** 8 px per character of text: enough for the width maths. */
    get_preferred_width(_forHeight) {
        const w = (this.text ?? '').length * 8;
        return [w, w];
    }

    set_accessible_name(name) {
        this.accessible_name = name;
    }

    /** 20 px per visible label in the subtree: enough for placement maths. */
    get_preferred_height(_forWidth) {
        const count = a => (a.visible ? (a instanceof Label ? 1 : 0) +
            a.children.reduce((n, c) => n + count(c), 0) : 0);
        const h = count(this) * 20;
        return [h, h];
    }
}

export class BoxLayout extends Widget {}
export class Label extends Widget {
    constructor(props = {}) {
        super({text: '', ...props});
    }
}

export default {Widget, BoxLayout, Label};
