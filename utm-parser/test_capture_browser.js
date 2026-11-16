// Tests utm_capture_browser.js against a minimal fake DOM (no dependencies).
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PARSER = fs.readFileSync(path.join(__dirname, 'utm_parser.js'), 'utf8');
const SNIPPET = fs.readFileSync(path.join(__dirname, 'utm_capture_browser.js'), 'utf8');

function makeForm() {
  const inputs = [];
  return {
    tagName: 'FORM', inputs, nativeSubmits: 0,
    querySelector(sel) { const n = /name="([^"]+)"/.exec(sel)[1]; return inputs.find((i) => i.name === n) || null; },
    appendChild(el) { inputs.push(el); },
    field(n) { const i = inputs.find((x) => x.name === n); return i ? i.value : undefined; },
  };
}

function visit(url, store) {
  const listeners = [];
  function HTMLFormElement() {}
  HTMLFormElement.prototype.submit = function () { this.nativeSubmits++; };
  const ctx = {
    console, JSON, Date, Object, URL, URLSearchParams,
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    document: {
      referrer: 'https://ref.example/',
      addEventListener: (t, fn) => listeners.push(fn),
      createElement: () => ({ type: '', name: '', value: '' }),
    },
    HTMLFormElement,
  };
  ctx.window = ctx; ctx.self = ctx;
  ctx.window.location = { href: url };
  vm.createContext(ctx);
  vm.runInContext(PARSER, ctx);
  vm.runInContext(SNIPPET, ctx);
  return {
    ctx,
    submitEvent(form) { listeners.forEach((fn) => fn({ target: form })); },
    programmaticSubmit(form) { Object.setPrototypeOf(form, HTMLFormElement.prototype); form.submit(); },
  };
}

// 1. click-ID-only landing (Google Ads auto-tagging) is captured
let store = {};
let page = visit('https://site.example/?gclid=Cj0KCQ', store);
let attr = page.ctx.getUtmAttribution();
assert.ok(attr.first_touch && attr.last_touch, 'gclid-only visit should be recorded');
assert.strictEqual(attr.last_touch.click_ids.google_click_id, 'Cj0KCQ');

// 2. a later UTM visit updates last touch but keeps first touch
page = visit('https://site.example/book?utm_source=FB&utm_medium=paid_social&utm_campaign=Spring&utm_marketing_tactic=Retarget&fbclid=IwAR', store);
attr = page.ctx.getUtmAttribution();
assert.strictEqual(attr.first_touch.click_ids.google_click_id, 'Cj0KCQ');
assert.strictEqual(attr.last_touch.utm_source, 'fb');

// 3. submit event fills all UTM fields, click IDs and landing URL
let form = makeForm();
page.submitEvent(form);
assert.strictEqual(form.field('last_utm_marketing_tactic'), 'retarget');
assert.deepStrictEqual(JSON.parse(form.field('last_click_ids')), { facebook_click_id: 'IwAR' });
assert.strictEqual(JSON.parse(form.field('first_click_ids')).google_click_id, 'Cj0KCQ');
assert.strictEqual(form.field('last_landing_url'), 'https://site.example/book?utm_source=FB&utm_medium=paid_social&utm_campaign=Spring&utm_marketing_tactic=Retarget&fbclid=IwAR');
assert.strictEqual(form.field('first_utm_source'), '');

// 4. programmatic form.submit() (no submit event) is also filled, and still submits
form = makeForm();
page.programmaticSubmit(form);
assert.strictEqual(form.field('last_utm_campaign'), 'spring');
assert.strictEqual(form.nativeSubmits, 1);

// 5. a visit with nothing to record leaves storage alone
const before = JSON.stringify(store);
visit('https://site.example/about', store);
assert.strictEqual(JSON.stringify(store), before);

console.log('All capture snippet tests passed');
