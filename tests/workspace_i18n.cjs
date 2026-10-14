// Nonbrowser checks of the Workspace's language runtime (workspace-i18n.js) with a synthetic DOM:
// formatting, the language a page shows, switching a page and its title, and the language menu.
// The controller's use of it (URL state, return links, notices) is in workspace_navigation_repairs.cjs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const I18n = require(process.argv[2]);
const catalogs = path.join(path.dirname(process.argv[2]), 'i18n');
const en = JSON.parse(fs.readFileSync(path.join(catalogs, 'en.json'), 'utf8'));
const ko = JSON.parse(fs.readFileSync(path.join(catalogs, 'ko.json'), 'utf8'));

// --- formatting: placeholders, plurals, nested messages and the English fallback ---
I18n.register({
  en: {...en, 'x.count': {one: '{n} item', other: '{n} items'}, 'x.only_en': 'English only',
       'x.nest': 'A {@x.count} here', 'x.two': '{a} and {b}'},
  ko: {...ko, 'x.count': {other: '{n}개'}, 'x.nest': '여기 {@x.count}', 'x.two': '{b}, {a}'},
});
assert.deepEqual(I18n.format('x.count', {n: 1}, 'en'), {text: '1 item', fallback: false});
assert.equal(I18n.format('x.count', {n: 1200}, 'en').text, '1,200 items');
assert.equal(I18n.format('x.count', {n: 1}, 'ko').text, '1개');
assert.equal(I18n.format('x.nest', {n: 2}, 'ko').text, '여기 2개');
assert.deepEqual(I18n.format('x.only_en', undefined, 'ko'), {text: 'English only', fallback: true});
assert.deepEqual(I18n.format('x.unknown', undefined, 'ko'), {text: 'x.unknown', fallback: true});
assert.equal(I18n.format('x.two', {a: 'A', b: 'B'}, 'ko').text, 'B, A');
// A value is put in as given: never read again as a placeholder or a nested message.
assert.equal(I18n.format('x.two', {a: '{b}', b: '{@x.count}'}, 'en').text, '{b} and {@x.count}');
assert.equal(I18n.format('x.nest', {n: '{n}'}, 'en').text, 'A {n} items here');
// A missing argument stays visible rather than becoming another word.
assert.equal(I18n.format('x.two', {a: 'A'}, 'en').text, 'A and {b}');

// --- the language a page shows: a working storage's choice, else the URL state's, else English ---
function memory(initial = {}) {
  const held = {...initial};
  return {held, getItem: k => (k in held ? held[k] : null), setItem(k, v) { held[k] = String(v); }};
}
const blocked = {getItem() { throw new Error('SecurityError'); }, setItem() { throw new Error('SecurityError'); }};
const forgetting = {getItem: () => null, setItem() {}};
assert.equal(I18n.KEY, 'analytics-language');
assert.equal(I18n.choose(memory({'analytics-language': 'ko'}), 'en'), 'ko');
assert.equal(I18n.choose(memory({'analytics-language': 'en'}), 'ko'), 'en');
assert.equal(I18n.choose(memory(), 'ko'), 'ko');
assert.equal(I18n.choose(blocked, 'ko'), 'ko');
assert.equal(I18n.choose(null, 'ko'), 'ko');
assert.equal(I18n.choose(blocked, 'fr'), 'en');
assert.equal(I18n.choose(memory({'analytics-language': 'xx'}), null), 'en');
assert.equal(I18n.save(blocked, 'ko'), false);
assert.equal(I18n.save(forgetting, 'ko'), false);
const kept = memory();
assert.equal(I18n.save(kept, 'ko'), true);
assert.equal(kept.held['analytics-language'], 'ko');
assert.equal(I18n.save(kept, 'fr'), false);
assert.equal(kept.held['analytics-language'], 'ko');
const ws = value => '?ws=' + encodeURIComponent(JSON.stringify(value));
assert.equal(I18n.urlLanguage(ws({v: 1, page: 'index.html', lang: 'ko'})), 'ko');
assert.equal(I18n.urlLanguage(ws({v: 1, page: 'index.html', lang: 'fr'})), null);
assert.equal(I18n.urlLanguage('?ws=%7Bbroken'), null);
assert.equal(I18n.urlLanguage(''), null);

// start(), in the head: <html lang> is the page's language before the body is read.
function head(storage, search) {
  const html = node({lang: 'en'});
  const win = {document: {documentElement: html}, location: {search}};
  Object.defineProperty(win, 'localStorage', {get() { if (storage === blocked) throw new Error('SecurityError'); return storage; }});
  return {win, html};
}
let early = head(memory({'analytics-language': 'ko'}), '');
assert.equal(I18n.start(early.win), 'ko');
assert.equal(early.html.attrs.lang, 'ko');
early = head(blocked, ws({v: 1, page: 'index.html', lang: 'ko'}));
assert.equal(I18n.start(early.win), 'ko');
assert.equal(early.html.attrs.lang, 'ko');
early = head(memory(), '');
assert.equal(I18n.start(early.win), 'en');
assert.equal(early.html.attrs.lang, 'en');
assert.equal(I18n.start(null), 'en');

// --- switching a page: the kit's words, their attributes, <html lang> and the title ---
function node(attrs = {}, text = '') {
  return {attrs: {...attrs}, textContent: text,
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; }};
}
function page(elements, title, titleKey) {
  const html = node({lang: 'en', 'data-i18n-doctitle': titleKey});
  return {documentElement: html, title, elements,
    querySelectorAll(selector) {
      if (selector === 'k-w') return elements.filter(el => el.tag === 'k-w');
      if (selector === 'time[datetime]') return elements.filter(el => el.tag === 'time' && 'datetime' in el.attrs);
      const name = /^\[([a-z0-9-]+)\]$/.exec(selector);
      assert.ok(name, selector);
      return elements.filter(el => name[1] in el.attrs);
    }};
}
function word(attrs, text) { const el = node(attrs, text); el.tag = 'k-w'; return el; }
function time(attrs, text) { const el = node(attrs, text); el.tag = 'time'; return el; }
// As the generator writes them: a bare word, a word with its key and values, a word the Korean
// catalog lacks, and a word a script added (data-i18n).
const tab = word({}, 'Insights');
const counted = word({k: 'context.versions_count', a: '{"n":3}'}, '3 version(s)');
const english = word({k: 'x.only_en'}, 'English only');
const added = node({'data-i18n': 'action.dismiss'}, 'Dismiss');
const unknown = word({}, 'Not a message of the kit');
const written = node({lang: ''}, '사람이 쓴 글 — written as it was');
const labelled = node({'aria-label': 'Filter by kind', 'data-i18n-aria-label': ''});  // a bare mark
const titled = node({title: '2 inputs', 'data-i18n-title': 'badge.input_changed_title', 'data-i18n-title-args': '{"n":2}'});
// Dates: a day heading, a time in its record's own offset (never the reader's zone), and a time
// alone (no datetime: the same in every language).
const day = time({datetime: '2026-10-01'}, 'Oct 1, 2026');
const stamped = time({datetime: '2026-08-03T23:46:12.123+09:00'}, 'Aug 3, 2026 23:46');
const clock = time({}, '23:46');
const relations = page([tab, counted, english, added, unknown, written, labelled, titled, day, stamped, clock],
                       'Relations · scripts/a.py::trend', 'page.relations');
assert.equal(I18n.apply(relations, 'ko'), 'ko');
assert.equal(I18n.language, 'ko');
assert.equal(relations.documentElement.attrs.lang, 'ko');
assert.equal(tab.textContent, ko['tab.insights']);
assert.equal(tab.attrs.k, 'list.title.insights');  // found by its English once, then kept
assert.equal(counted.textContent, '버전 3개');
assert.equal('lang' in tab.attrs, false);
assert.equal(added.textContent, ko['action.dismiss']);
// A key the Korean catalog lacks is shown in English and says so; text that is no message is left.
assert.equal(english.textContent, 'English only');
assert.equal(english.attrs.lang, 'en');
assert.equal(unknown.textContent, 'Not a message of the kit');
assert.equal('k' in unknown.attrs, false);
// What people wrote is not the kit's: never touched, and it keeps lang="".
assert.equal(written.textContent, '사람이 쓴 글 — written as it was');
assert.equal(written.attrs.lang, '');
assert.equal(labelled.attrs['aria-label'], ko['filters.by_kind']);
assert.equal(titled.attrs.title, ko['badge.input_changed_title'].replace('{n}', '2'));
assert.equal(day.textContent, '2026년 10월 1일');
assert.equal(stamped.textContent, '2026년 8월 3일 23:46');
assert.equal(clock.textContent, '23:46');
// The title: the kit's words of the page, then the rest as written.
assert.equal(relations.title, '관계 · scripts/a.py::trend');
// And back: the English the page was written in, lang marks gone.
I18n.apply(relations, 'en');
assert.equal(relations.documentElement.attrs.lang, 'en');
assert.equal(tab.textContent, 'Insights');
assert.equal(counted.textContent, '3 version(s)');
assert.equal(added.textContent, 'Dismiss');
assert.equal('lang' in english.attrs, false);
assert.equal(labelled.attrs['aria-label'], 'Filter by kind');
assert.equal(day.textContent, 'Oct 1, 2026');
assert.equal(stamped.textContent, 'Aug 3, 2026 23:46');
assert.equal(relations.title, 'Relations · scripts/a.py::trend');
// Another page has its own title (one runtime serves one page in a browser, two here).
const other = page([], 'Workspace · 지역 매출', 'page.workspace');
I18n.apply(other, 'ko');
assert.equal(other.title, ko['page.workspace'] + ' · 지역 매출');
I18n.apply(relations, 'ko');
assert.equal(relations.title, '관계 · scripts/a.py::trend');
// A language the kit does not have shows English.
assert.equal(I18n.apply(relations, 'fr'), 'en');
assert.equal(tab.textContent, 'Insights');

// --- the language menu: each language named in its own language ---
function element(tag) {
  const el = node();
  el.tag = tag; el.children = [];
  el.appendChild = child => { el.children.push(child); return child; };
  el.addEventListener = (name, f) => { el.on = {...el.on, [name]: f}; };
  return el;
}
const made = {createElement: element, createTextNode: text => ({text})};
I18n.apply(page([], '', ''), 'ko');
const picked = [];
const menu = I18n.menu(made, chosen => picked.push(chosen));
assert.equal(menu.tag, 'label');
assert.equal(menu.className, 'language');
const [name, select] = menu.children;
assert.deepEqual(name.children.filter(c => c.tag).map(c => [c.attrs.lang, c.textContent]),
                 [['en', en['language.label']], ['ko', ko['language.label']]]);
assert.deepEqual(select.children.map(o => [o.value, o.attrs.lang, o.textContent, !!o.selected]),
                 [['en', 'en', en['language.name'], false], ['ko', 'ko', ko['language.name'], true]]);
select.value = 'en';
select.on.change();
assert.deepEqual(picked, ['en']);
console.log('ok');
