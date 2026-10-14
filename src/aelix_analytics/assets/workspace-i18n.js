// The Workspace's own words by message key (i18n.py). A page is written in English; each word it can
// switch carries data-i18n="key" (and data-i18n-args), or data-i18n-<attribute> for an attribute.
// Elements people and scripts wrote carry lang="" and are never touched. No DOM is needed to format,
// so nonbrowser Node checks exercise this file too.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AnalyticsI18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const LANGUAGES = ["en", "ko"], DEFAULT = "en", KEY = "analytics-language";
  const ATTRIBUTES = ["title", "aria-label", "placeholder"];
  let catalogs = { en: {}, ko: {} };
  let current = DEFAULT;
  // Each document's title as it was written (English), so switching back and forth keeps its rest.
  const titles = new WeakMap();
  function register(data) { if (data && typeof data === "object") catalogs = data; }
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
    // and {name}: a value. One pass, so a value put in is never read again as either.
    const text = String(entry).replace(/\{@([a-z][a-z0-9_.]*)\}|\{([a-z][a-z0-9_]*)\}/g, (whole, nested, name) => {
      if (nested) {
        const inner = format(nested, args, fallback ? DEFAULT : lang);
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
  function apply(doc, language) {
    current = known(language) ? language : DEFAULT;
    const html = doc.documentElement;
    if (html && html.setAttribute) html.setAttribute("lang", current);
    if (!doc.querySelectorAll) return current;
    doc.querySelectorAll("[data-i18n]").forEach(el => {
      const result = format(el.getAttribute("data-i18n"), parse(el.getAttribute("data-i18n-args")));
      el.textContent = result.text;
      // An English word left in a Korean page says so; otherwise the page's language holds.
      if (result.fallback) el.setAttribute("lang", "en"); else el.removeAttribute("lang");
    });
    ATTRIBUTES.forEach(name => {
      doc.querySelectorAll("[data-i18n-" + name + "]").forEach(el => {
        el.setAttribute(name, t(el.getAttribute("data-i18n-" + name), parse(el.getAttribute("data-i18n-" + name + "-args"))));
      });
    });
    // The document title: the kit's words of the page, then what follows them as written.
    const titleKey = html && html.getAttribute && html.getAttribute("data-i18n-doctitle");
    if (titleKey && typeof doc.title === "string") {
      const titleArgs = parse(html.getAttribute("data-i18n-doctitle-args"));
      if (!titles.has(doc)) titles.set(doc, doc.title);
      const written = titles.get(doc), english = format(titleKey, titleArgs, DEFAULT).text;
      if (written.indexOf(english) === 0) doc.title = t(titleKey, titleArgs) + written.slice(english.length);
    }
    return current;
  }
  // In the head, before the body is read: the language the page will show sets <html lang> now.
  function start(win) {
    if (!win || !win.document || !win.document.documentElement) return DEFAULT;
    let storage = null;
    try { storage = win.localStorage; } catch (_) { storage = null; }
    const language = choose(storage, urlLanguage(win.location && win.location.search));
    if (language !== DEFAULT) win.document.documentElement.setAttribute("lang", language);
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
  return { LANGUAGES, DEFAULT, KEY, register, has, format, t, stored, save, choose, urlLanguage, start, apply, menu,
    get language() { return current; } };
});
