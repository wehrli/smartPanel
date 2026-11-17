// Minimal test runner for bare `gjs -m` (no shell, no npm). Each test file calls
// test(...) then `await run()`; the process exits non-zero when anything failed.
import GLib from 'gi://GLib';
import System from 'system';

const tests = [];

export function test(name, fn) {
    tests.push({name, fn});
}

function show(v) {
    try {
        return JSON.stringify(v, null, 1);
    } catch {
        return String(v);
    }
}

function deepEqual(a, b) {
    if (Object.is(a, b))
        return true;
    if (typeof a !== 'object' || typeof b !== 'object' || a === null || b === null)
        return false;
    if (Array.isArray(a) !== Array.isArray(b))
        return false;
    const ka = Object.keys(a), kb = Object.keys(b);
    if (ka.length !== kb.length)
        return false;
    return ka.every(k => Object.hasOwn(b, k) && deepEqual(a[k], b[k]));
}

export function eq(actual, expected, msg = '') {
    if (!deepEqual(actual, expected))
        throw new Error(`${msg ? `${msg}: ` : ''}expected ${show(expected)}\n  got ${show(actual)}`);
}

export function ok(cond, msg = 'expected truthy') {
    if (!cond)
        throw new Error(msg);
}

export function throws(fn, msg = 'expected an exception') {
    try {
        fn();
    } catch {
        return;
    }
    throw new Error(msg);
}

/** Directory of the calling module's URL, as a filesystem path. */
export function dirOf(url) {
    return GLib.path_get_dirname(GLib.filename_from_uri(url)[0]);
}

export function readText(path) {
    const [okRead, bytes] = GLib.file_get_contents(path);
    if (!okRead)
        throw new Error(`cannot read ${path}`);
    return new TextDecoder().decode(bytes);
}

export function readJson(path) {
    return JSON.parse(readText(path));
}

export async function run() {
    let failed = 0;
    for (const {name, fn} of tests) {
        try {
            // eslint-disable-next-line no-await-in-loop
            await fn();
            print(`ok   ${name}`);
        } catch (e) {
            failed++;
            print(`FAIL ${name}\n     ${e.message}${e.stack ? `\n${e.stack}` : ''}`);
        }
    }
    print(`${tests.length - failed}/${tests.length} passed`);
    System.exit(failed ? 1 : 0);
}
