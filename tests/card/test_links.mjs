// Link and image rules of the card, without a browser: node tests/card/test_links.mjs
// URLs come from notify calls that any user or automation can make (HACS review, hacs/default#13445):
// only http(s); in-app navigation only for a path starting with a single "/"; files of this
// Home Assistant that need a login are signed, other /api/ and /auth/ paths are never used.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(here, "../../custom_components/notification_history/www/notification-history-card.js"), "utf8");

const opened = [];
const pushed = [];
const signed = [];
globalThis.window = {
  location: { origin: "http://192.168.0.99:8123" },
  open: (u) => opened.push(u),
  history: { pushState: (_s, _t, u) => pushed.push(u) },
  dispatchEvent() {},
};
globalThis.HTMLElement = class {};
globalThis.CustomEvent = class {};
globalThis.document = { querySelector: () => null };
let Card;
globalThis.customElements = { define: (_n, c) => (Card = c) };
const info = console.info;
console.info = () => {};
new Function(src)();
console.info = info;

const card = new Card();
card._config = { frigate: true, limit: 15 };
card._signed = new Map();
let renders = 0;
card._render = () => renders++;
card._overlay = (html) => opened.push(`overlay:${html}`);
card._hass = {
  config: { external_url: "https://ha.example.org" },
  locale: { language: "en" },
  callWS: async (m) => {
    assert.equal(m.type, "auth/sign_path");
    signed.push(m.path);
    return { path: `${m.path}${m.path.includes("?") ? "&" : "?"}authSig=SIG` };
  },
};
const tick = () => new Promise((r) => setTimeout(r, 0));
const kind = (u) => card._kind(card._local(u));

// _local(): http(s) only; this Home Assistant becomes a same-origin path.
for (const bad of ["javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "vbscript:x", "entityId:light.x", "intent://x"]) {
  assert.equal(card._local(bad), null, bad);
}
assert.equal(card._local("https://ha.example.org/media/local/a.jpg?x=1#h"), "/media/local/a.jpg?x=1#h");
assert.equal(card._local("//evil.com/x"), "http://evil.com/x");
assert.equal(card._local("/\\evil.com/x"), "http://evil.com/x");

// _kind()
assert.equal(kind("/media/local/Notify/Snapshot.jpg"), "signed");
assert.equal(kind("/api/camera_proxy/camera.door"), "signed");
assert.equal(kind("/api/frigate/abc/clips/x.mp4"), "signed");
assert.equal(kind("/api/frigate/notifications/1/snapshot.jpg"), "public");
assert.equal(kind("/api/frigate/abc/notifications/1/thumbnail.jpg"), "public");
assert.equal(kind("/local/x.png"), "public");
for (const blocked of ["/api/states", "/api/config", "/auth/token", "/local/../api/states", "/media/../api/states"]) {
  assert.equal(kind(blocked), null, blocked);
}
assert.equal(kind("/lovelace/0"), "nav");
assert.equal(kind("https://example.com/a.jpg"), "external");
assert.equal(card._kind("//evil.com/x"), null);

// _src(): signed once, null until the signature arrives, nothing for blocked paths.
assert.equal(card._src("/media/local/a.jpg"), null);
assert.equal(card._src("/api/states"), null);
await tick();
assert.equal(card._src("/media/local/a.jpg"), "/media/local/a.jpg?authSig=SIG");
assert.equal(card._src("/media/local/a.jpg"), "/media/local/a.jpg?authSig=SIG");
assert.deepEqual(signed, ["/media/local/a.jpg"]);
assert.equal(renders, 1);

// Buttons and what they do.
const n = {
  id: "r1",
  link: "/media/local/a.jpg",
  image: "/api/frigate/notifications/9/snapshot.jpg",
  actions: [
    { action: "URI", title: "Browser", uri: "/media-browser/browser" },
    { action: "URI", title: "Bad", uri: "javascript:alert(1)" },
    { action: "URI", title: "API", uri: "/api/states" },
    { action: "URI", title: "Ext", uri: "https://example.com/" },
  ],
};
const buttons = card._buttons(n);
assert.deepEqual(
  buttons.map((b) => [b.label, b.kind, b.url]),
  [
    ["Open", "signed", "/media/local/a.jpg?authSig=SIG"],
    ["Watch clip", "public", "/api/frigate/notifications/9/clip.mp4"],
    ["Browser", "nav", "/media-browser/browser"],
    ["Ext", "external", "https://example.com/"],
  ],
);
for (const b of buttons) await card._run(b);
assert.deepEqual(opened, [
  'overlay:<img src="/media/local/a.jpg?authSig=SIG" alt="">',
  'overlay:<video src="/api/frigate/notifications/9/clip.mp4" controls autoplay playsinline></video>',
  "https://example.com/",
]);
assert.deepEqual(pushed, ["/media-browser/browser"]);

// _linkify(): same rules for links in the message text; everything else stays escaped text.
const html = card._linkify(
  "See https://example.com/a?b=1&c=2. <b>x</b> [doc](https://ha.example.org/lovelace/0), " +
    "javascript:alert(1) [api](https://ha.example.org/api/states) https://ha.example.org/media/local/a.jpg!",
);
assert.equal(
  html,
  'See <a href="https://example.com/a?b=1&amp;c=2" data-l="external" target="_blank" rel="noopener noreferrer">https://example.com/a?b=1&amp;c=2</a>. ' +
    '&lt;b&gt;x&lt;/b&gt; <a href="/lovelace/0" data-l="nav" target="_blank" rel="noopener noreferrer">doc</a>, ' +
    "javascript:alert(1) api " +
    '<a href="/media/local/a.jpg?authSig=SIG" data-l="signed" target="_blank" rel="noopener noreferrer">https://ha.example.org/media/local/a.jpg</a>!',
);
assert.equal(card._linkify('[x](javascript:alert(1)) "quoted"'), "[x](javascript:alert(1)) &quot;quoted&quot;");

// iOS camera notification: a "Camera now" button with a signed camera proxy URL, shown in the viewer.
// A malformed entity id gets no button (it would go into the URL).
opened.length = 0;
card._buttons({ id: "c1", camera: "camera.front_door" });
await tick();
const cam = card._buttons({ id: "c1", camera: "camera.front_door" });
assert.deepEqual(cam.map((b) => [b.label, b.kind, b.url]), [["Camera now", "signed", "/api/camera_proxy/camera.front_door?authSig=SIG"]]);
await card._run(cam[0]);
assert.deepEqual(opened, ['overlay:<img src="/api/camera_proxy/camera.front_door?authSig=SIG" alt="">']);
for (const bad of ["camera.x/../../api/states", "light.kitchen", "camera.X", "camera.a?b=1"]) {
  assert.deepEqual(card._buttons({ id: "c2", camera: bad }), [], bad);
}

// max_height: pixels or a CSS length, nothing else ends up in the style sheet.
for (const [v, css] of [[400, "400px"], ["400", "400px"], ["60vh", "60vh"], [" 30em ", "30em"], [0, ""], ["red; x", ""], ["1px}a{", ""], [null, ""]]) {
  card._config = { max_height: v };
  assert.equal(card._maxHeight(), css, String(v));
}

console.log("card link tests: ok");
