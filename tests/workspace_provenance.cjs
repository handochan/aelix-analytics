// Nonbrowser controller test (mocked DOM), never a rendering check: a relation page's section bar goes
// before its first section, under the record (#100, design WORKSPACE-READABILITY 6.4); a provenance
// trail shows one direction at a time, the one the address names; a list that is not a graph gets no lines.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assets = path.resolve(process.argv[2]);
const codec = require(path.join(assets, 'workspace-state.js'));
const I18n = require(path.join(assets, 'workspace-i18n.js'));
I18n.register({en: JSON.parse(fs.readFileSync(path.join(assets, 'i18n', 'en.json'), 'utf8')),
  ko: JSON.parse(fs.readFileSync(path.join(assets, 'i18n', 'ko.json'), 'utf8'))});
const source = fs.readFileSync(path.join(assets, 'workspace.js'), 'utf8');

function element(props) {
  const e = {hidden: false, attrs: {}, children: [], dataset: {}, classes: new Set(), textContent: '', ...props};
  e.classList = {
    add(c) { e.classes.add(c); }, remove(c) { e.classes.delete(c); }, contains(c) { return e.classes.has(c); },
    toggle(c, on) { if (on === undefined) on = !e.classes.has(c); if (on) e.classes.add(c); else e.classes.delete(c); return on; },
  };
  e.setAttribute = (k, v) => { e.attrs[k] = String(v); };
  e.getAttribute = k => (k in e.attrs ? e.attrs[k] : null);
  e.removeAttribute = k => { delete e.attrs[k]; };
  e.hasAttribute = k => k in e.attrs;
  e.appendChild = c => { e.children.push(c); return c; };
  e.append = (...cs) => { e.children.push(...cs); };
  e.querySelector = () => null;
  e.querySelectorAll = () => [];
  e.addEventListener = (k, f) => { (e.listeners ||= {})[k] = f; };
  e.getBoundingClientRect = () => ({top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0});
  e.closest = e.closest || (() => null);
  e.focus = () => {};
  return e;
}

let url = new URL('file:///synthetic/workspace/relations/abc-0.html#trail-uses');
const placed = [];
const provenance = element({id: 'provenance'}), related = element({id: 'related', dataset: {count: '3'}}),
  history_ = element({id: 'history'});
for (const tab of [provenance, related, history_]) {
  tab.classes.add('tab');
  tab.closest = sel => (sel === 'section.tab' ? tab : null);
}
provenance.before = node => placed.push(['before', 'provenance', node.className]);
const trails = ['sources', 'uses'].map(d => {
  const trail = element({id: 'trail-' + d});
  trail.closest = sel => (sel === '.pv-trail' ? trail : sel === 'section.tab' ? provenance : null);
  return trail;
});
const links = ['sources', 'uses'].map(d => element({attrs: {href: '#trail-' + d}, dataset: {dir: d}}));
const lists = trails.map(trail => {
  const scroll = element({parentElement: trail});
  return element({offsetWidth: 0, parentElement: scroll, dataset: {e: '1>0c'}});
});
const record = element({id: 'record'});
record.closest = () => null;
const byId = {'trail-sources': trails[0], 'trail-uses': trails[1], record};
const docClasses = new Set();
const documentElement = {dataset: {root: '../', page: 'relations/abc-0.html', source: 'aabbccddeeff0011', selection: 'e1'},
  classList: {add(c) { docClasses.add(c); }, toggle() {}, contains(c) { return docClasses.has(c); }},
  setAttribute() {}, getAttribute() { return null; }, removeAttribute() {}, lang: 'en'};
const main = element({prepend(node) { placed.push(['prepend', 'main', node.className]); }});
const events = {}, documentEvents = {}, frames = [];
const document = {documentElement, hidden: false, head: {appendChild() {}},
  getElementById(id) { return byId[id] || null; },
  querySelector(sel) { return sel === 'main' ? main : null; },
  querySelectorAll(sel) {
    if (sel === 'main > section.tab') return [provenance, related, history_];
    if (sel === '.pv-trail') return trails;
    if (sel === '.pv-dir a') return links;
    if (sel === '.pv-list') return lists;
    return [];
  },
  addEventListener(k, f) { (documentEvents[k] ||= []).push(f); },
  createElement(tag) { return element({tagName: tag, remove() {}}); },
  createTextNode(text) { return {text}; }};
const history = {state: null, replaceState(value, _, p) { this.state = value; url = new URL(p, url); },
  pushState(value, _, p) { this.replaceState(value, _, p); }};
const location = {get href() { return url.href; }, get pathname() { return url.pathname; }, get protocol() { return url.protocol; },
  get host() { return url.host; }, get search() { return url.search; }, get hash() { return url.hash; }, reload() {}};
const window = {AnalyticsWorkspaceState: codec, AnalyticsI18n: I18n, scrollY: 0, scrollTo() {},
  getComputedStyle() { return {position: 'static', top: '0', getPropertyValue() { return ''; }}; },
  addEventListener(k, f) { (events[k] ||= []).push(f); }};
vm.runInNewContext(source, {window, document, location, history, URL, URLSearchParams, Set,
  requestAnimationFrame: f => frames.push(f), setInterval() {}, setTimeout() { return 1; }, clearTimeout() {}});
while (frames.length) frames.shift()();

// The section bar goes under the record: before the first section, not at the top of the page.
assert.deepEqual(placed, [['before', 'provenance', 'tabs']]);
assert.ok(docClasses.has('js-tabs') && docClasses.has('js-trail'));
// The address names the trail of what uses it: that one shows, the other is hidden, its link is on.
assert.deepEqual(trails.map(t => t.hidden), [true, false]);
assert.deepEqual(links.map(a => a.classes.has('on')), [false, true]);
assert.equal(links[1].getAttribute('aria-current'), 'true');
// The other direction's link changes the address; the page shows that trail.
url = new URL('#trail-sources', url);
events.hashchange.forEach(f => f());
assert.deepEqual(trails.map(t => t.hidden), [false, true]);
assert.deepEqual(links.map(a => a.classes.has('on')), [true, false]);
// An address outside the trails (the record) keeps the direction shown.
url = new URL('#record', url);
events.hashchange.forEach(f => f());
assert.deepEqual(trails.map(t => t.hidden), [false, true]);
// A list that is not laid out as a graph (narrow, or no room) gets no lines and no legend.
assert.ok(lists.every(list => list.parentElement.children.length === 0));
