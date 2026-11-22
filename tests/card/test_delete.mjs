// Soft delete in the card, without a browser: node tests/card/test_delete.mjs
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "../../custom_components/notification_history/www/notification-history-card.js"), "utf8");
globalThis.window = { location: { origin: "http://ha.local" } };
globalThis.HTMLElement = class {};
globalThis.document = { querySelector: () => null };
let Card;
globalThis.customElements = { define: (_n, c) => (Card = c) };
const info = console.info;
console.info = () => {};
new Function(src)();
console.info = info;

const card = new Card();
card._config = { limit: 15, delete: true };
card._render = () => {};
const rec = (id, ts, extra = {}) => ({ id, ts, ...extra });
const ids = () => card._items.map((x) => x.id);

// Hidden mode: a deleted notification leaves the list, a restored one comes back in date order.
card._showDeleted = false;
card._more = false;
card._items = [rec("c", 3), rec("b", 2), rec("a", 1)];
card._onEvent({ record: rec("b", 2, { deleted_ts: 10 }) });
assert.deepEqual(ids(), ["c", "a"]);
card._onEvent({ record: rec("x", 5, { deleted_ts: 10 }) }); // deleted elsewhere, not shown: ignored
assert.deepEqual(ids(), ["c", "a"]);
card._onEvent({ record: rec("b", 2) });
assert.deepEqual(ids(), ["c", "b", "a"]);
card._onEvent({ record: rec("d", 4) });
assert.deepEqual(ids(), ["d", "c", "b", "a"]);

// Older than the last loaded one while "Show more" is there: left for the next page.
card._more = true;
card._onEvent({ record: rec("z", 0) });
assert.deepEqual(ids(), ["d", "c", "b", "a"]);

// Shown mode: a deleted notification stays, marked.
card._showDeleted = true;
card._onEvent({ record: rec("c", 3, { deleted_ts: 11 }) });
assert.deepEqual(ids(), ["d", "c", "b", "a"]);
assert.equal(card._items[1].deleted_ts, 11);

// The item gets a delete button, or a restore button and a "Deleted" badge.
card._hass = { locale: { language: "en" } };
card._signed = new Map();
assert.match(card._itemHtml(rec("a", 1, { message: "m" }), []), /data-del="delete"/);
const html = card._itemHtml(rec("a", 1, { message: "m", deleted_ts: 5 }), []);
assert.match(html, /data-del="restore"/);
assert.match(html, />Deleted</);
assert.match(html, /class="item dim"/);
card._config.delete = false;
assert.doesNotMatch(card._itemHtml(rec("a", 1, { message: "m" }), []), /data-del/);

console.log("card delete: ok");
