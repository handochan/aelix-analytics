// The Workspace's own words by message key (i18n.py). A page is written in English; each word it can
// switch is a <k-w> element — bare when its English names its key, else with k="key" (and a='{values}')
// — or an attribute marked data-i18n-<attribute> (bare, or ="key"); words a script adds carry
// data-i18n="key".
// A date is a <time datetime> written from its stamp. Elements people and scripts wrote carry lang=""
// and are never touched. No DOM is needed to format, so nonbrowser Node checks exercise this file too.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AnalyticsI18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const LANGUAGES = ["en", "ko"], DEFAULT = "en", KEY = "analytics-language";
  const ATTRIBUTES = ["title", "aria-label", "placeholder"], WORD = "k-w";
  let catalogs = { en: {}, ko: {} };
  // English text -> a key that says it, for the bare words (i18n.by_english keeps them unambiguous).
  let byEnglish = new Map();
  let current = DEFAULT;
  // Each document's title as it was written (English), so switching back and forth keeps its rest.
  const titles = new WeakMap();
  function register(data) {
    if (!data || typeof data !== "object") return;
    catalogs = data;
    byEnglish = new Map();
    Object.keys(catalogs[DEFAULT] || {}).forEach(key => {
      const text = catalogs[DEFAULT][key];
      if (typeof text === "string" && text.indexOf("{") < 0 && !byEnglish.has(text)) byEnglish.set(text, key);
    });
  }
  function known(language) { return LANGUAGES.indexOf(language) >= 0; }
  function has(key) { return Object.prototype.hasOwnProperty.call(catalogs[DEFAULT] || {}, key); }
  function value(arg) {
    return typeof arg === "number" && Number.isInteger(arg) ? arg.toLocaleString("en-US") : String(arg);
  }
  // {text, fallback}: fallback when the language has no such key and English is shown instead.
  function format(key, args, language) {
    const lang = known(language) ? language : current;
    let entry = (catalogs[lang] || {})[key], fallback = false;
    if (entry === undefined) { entry = (catalogs[DEFAULT] || {})[key]; fallback = lang !== DEFAULT; }
    if (entry === undefined) return { text: key, fallback: true };
    if (entry && typeof entry === "object") entry = args && args.n === 1 && "one" in entry ? entry.one : entry.other;
    // {@key}: another message of the same language and arguments (a long explanation written once),
    // and {name}: a value. One pass, so a value put in is never read again as either. A message
    // shown in English is English throughout (i18n.py does the same); a nested message left in
    // English marks the whole text as a fallback, and the next one keeps the language.
    const nestedLanguage = fallback ? DEFAULT : lang;
    const text = String(entry).replace(/\{@([a-z][a-z0-9_.]*)\}|\{([a-z][a-z0-9_]*)\}/g, (whole, nested, name) => {
      if (nested) {
        const inner = format(nested, args, nestedLanguage);
        if (inner.fallback) fallback = true;
        return inner.text;
      }
      return args && Object.prototype.hasOwnProperty.call(args, name) ? value(args[name]) : whole;
    });
    return { text, fallback };
  }
  function t(key, args) { return format(key, args).text; }
  function parse(json) { if (!json) return undefined; try { return JSON.parse(json); } catch (_) { return undefined; } }
  function stored(storage) {
    try { const saved = storage && storage.getItem(KEY); return known(saved) ? saved : null; } catch (_) { return null; }
  }
  // Whether the choice is kept: a page whose storage is blocked carries it in its URL state instead.
  function save(storage, language) {
    if (!known(language)) return false;
    try { storage.setItem(KEY, language); return storage.getItem(KEY) === language; } catch (_) { return false; }
  }
  // The language a page shows: a working storage's choice, else the URL state's, else English.
  function choose(storage, urlLanguage) { return stored(storage) || (known(urlLanguage) ? urlLanguage : DEFAULT); }
  // The `lang` of the URL state (?ws=…), read without the state codec so the head can use it.
  function urlLanguage(search) {
    try {
      const raw = new URLSearchParams(search || "").get("ws");
      const state = raw ? JSON.parse(raw) : null;
      return state && known(state.lang) ? state.lang : null;
    } catch (_) { return null; }
  }
  // A word's text in the page's language: an English word left in a Korean page says so. Text that is
  // already right is not written again (a write is a mutation, which the head's observer would see).
  function show(el, result) {
    if (el.textContent !== result.text) el.textContent = result.text;
    if (result.fallback) el.setAttribute("lang", "en"); else if (el.hasAttribute("lang")) el.removeAttribute("lang");
  }
  // A key found by the English the page was written in, once: it is kept on the element.
  function keyOf(el, name, english) {
    let key = el.getAttribute(name);
    if (!key) {
      key = byEnglish.get(english);
      if (!key) return null;
      el.setAttribute(name, key);
    }
    return key;
  }
  const SWITCHED = WORD + ",[data-i18n],time[datetime]," + ATTRIBUTES.map(name => "[data-i18n-" + name + "]").join(",");
  function switchOne(el) {
    const tag = String(el.localName || el.tagName || "").toLowerCase();
    if (tag === WORD) {
      const key = keyOf(el, "k", el.textContent);
      if (key) show(el, format(key, parse(el.getAttribute("a"))));
    } else if (tag === "time" && el.hasAttribute("datetime")) {
      // A date: the wall clock the record wrote, in its own offset (never the reader's zone).
      const stamp = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(el.getAttribute("datetime") || "");
      const month = stamp ? Number(stamp[2]) : 0;
      if (month >= 1 && month <= 12) {
        const day = format("time.date." + month, { y: stamp[1], d: Number(stamp[3]) });
        show(el, { text: day.text + (stamp[4] ? " " + stamp[4] + ":" + stamp[5] : ""), fallback: day.fallback });
      }
    }
    if (el.hasAttribute("data-i18n")) show(el, format(el.getAttribute("data-i18n"), parse(el.getAttribute("data-i18n-args"))));
    ATTRIBUTES.forEach(name => {
      if (!el.hasAttribute("data-i18n-" + name)) return;
      // A bare mark: the key is found by the attribute's English, as for a bare word.
      const key = keyOf(el, "data-i18n-" + name, el.getAttribute(name));
      const text = key && t(key, parse(el.getAttribute("data-i18n-" + name + "-args")));
      if (text && el.getAttribute(name) !== text) el.setAttribute(name, text);
    });
  }
  function apply(doc, language) {
    current = known(language) ? language : DEFAULT;
    const html = doc.documentElement;
    if (html && html.setAttribute) html.setAttribute("lang", current);
    if (!doc.querySelectorAll) return current;
    doc.querySelectorAll(SWITCHED).forEach(switchOne);
    switchTitle(doc);
    return current;
  }
  // The document title: the kit's words of the page, then what follows them as written.
  function switchTitle(doc) {
    const html = doc.documentElement;
    const titleKey = html && html.getAttribute && html.getAttribute("data-i18n-doctitle");
    if (!titleKey || typeof doc.title !== "string") return;
    const titleArgs = parse(html.getAttribute("data-i18n-doctitle-args"));
    if (!titles.has(doc)) titles.set(doc, doc.title);
    const written = titles.get(doc), english = format(titleKey, titleArgs, DEFAULT).text;
    const shown = t(titleKey, titleArgs) + written.slice(english.length);
    if (written.indexOf(english) === 0 && doc.title !== shown) doc.title = shown;
  }
  // In the head, before the body is read: the language the page will show sets <html lang> now and,
  // when it is not English, the words are switched while the parser adds them, so the page does not
  // paint in English first. An element is switched once the parser has added the node after it: by
  // then its text is whole (a word's text arrives after its element, and may arrive in parts). The
  // last few words of a parser pass wait for the next one, and the script at the end of the body
  // applies the whole page again; the observer stops once the page is read.
  function start(win) {
    if (!win || !win.document || !win.document.documentElement) return DEFAULT;
    let storage = null;
    try { storage = win.localStorage; } catch (_) { storage = null; }
    const language = choose(storage, urlLanguage(win.location && win.location.search));
    if (language === DEFAULT) return language;
    const doc = win.document;
    doc.documentElement.setAttribute("lang", language);
    current = language;
    switchTitle(doc);  // the <title> comes before this script in the head
    if (typeof win.MutationObserver === "function") {
      const observer = new win.MutationObserver(records => records.forEach(record => record.addedNodes.forEach(node => {
        const whole = node.previousSibling;
        if (!whole || whole.nodeType !== 1) return;
        if (whole.matches(SWITCHED)) switchOne(whole);
        whole.querySelectorAll(SWITCHED).forEach(switchOne);
      })));
      observer.observe(doc.documentElement, { childList: true, subtree: true });
      doc.addEventListener("DOMContentLoaded", () => observer.disconnect(), { once: true });
    }
    return language;
  }
  // The language menu: each language named in its own language, built here so a page holds no
  // words of a second language until a script runs (and no menu when none does).
  function menu(doc, onChange) {
    const label = doc.createElement("label");
    label.className = "language";
    const name = doc.createElement("span");
    name.className = "language-name";
    LANGUAGES.forEach((language, i) => {
      if (i) name.appendChild(doc.createTextNode(" · "));
      const part = doc.createElement("span");
      part.setAttribute("lang", language);
      part.textContent = format("language.label", undefined, language).text;
      name.appendChild(part);
    });
    const select = doc.createElement("select");
    LANGUAGES.forEach(language => {
      const option = doc.createElement("option");
      option.value = language;
      option.setAttribute("lang", language);
      option.textContent = format("language.name", undefined, language).text;
      if (language === current) option.selected = true;
      select.appendChild(option);
    });
    select.addEventListener("change", () => onChange(select.value));
    label.appendChild(name);
    label.appendChild(select);
    return label;
  }
  return { LANGUAGES, DEFAULT, KEY, WORD, register, has, format, t, stored, save, choose, urlLanguage, start, apply, menu,
    get language() { return current; } };
});
