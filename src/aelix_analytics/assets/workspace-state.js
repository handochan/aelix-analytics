// Small URL codec; no DOM or storage required. Also exercised by nonbrowser Node checks.
(function (root, factory) {
  const codec = factory();
  if (typeof module === "object" && module.exports) module.exports = codec;
  else root.AnalyticsWorkspaceState = codec;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const tabs = new Set(["overview", "insights", "charts", "map", "timeline", "data", "runs", "context", "assets", "evaluation"]);
  // The UI languages a URL may carry (workspace-i18n.js): the page's language when storage is blocked.
  const languages = new Set(["en", "ko"]);
  function text(value, max) { return typeof value === "string" && value.length <= max ? value : ""; }
  function safePage(value) {
    if (typeof value !== "string" || value.length > 2048) return false;
    let decoded;
    try { decoded = decodeURIComponent(value); } catch (_) { return false; }
    if (/[\\:#?\x00-\x1f]/.test(decoded) || decoded.split("/").some(p => p === "." || p.startsWith(".."))) return false;
    return /^(index|context)\.html$/.test(decoded) || /^(runs|data|relations|lists)\/[^/]+\.html$/.test(decoded);
  }
  function normalize(value, originAllowed = true) {
    if (!value || typeof value !== "object" || Array.isArray(value) || value.v !== 1 || !safePage(value.page)) return null;
    const result = { v: 1, page: value.page, anchor: text(value.anchor, 1024),
      tab: tabs.has(value.tab) ? value.tab : "overview", filter: text(value.filter, 128),
      retired: value.retired === true, selection: text(value.selection, 4096),
      source: /^[a-f0-9]{16}$/.test(value.source) ? value.source : "",
      card: text(value.card, 1024), offset: Number.isFinite(value.offset) ? Math.max(-1000000, Math.min(1000000, value.offset)) : 0 };
    if (languages.has(value.lang)) result.lang = value.lang;
    if (originAllowed && value.origin) {
      result.origin = normalize(value.origin, false);
      if (!result.origin) return null;
    }
    return result;
  }
  function decode(search) {
    const raw = new URLSearchParams(search).get("ws");
    if (raw === null) return { state: null, invalid: false };
    if (raw.length > 32000) return { state: null, invalid: true };
    try { const state = normalize(JSON.parse(raw)); return { state, invalid: !state }; }
    catch (_) { return { state: null, invalid: true }; }
  }
  function search(state, existing = "") {
    const value = normalize(state);
    if (!value) throw new Error("Invalid Workspace state");
    const params = new URLSearchParams(existing); params.set("ws", JSON.stringify(value));
    return "?" + params.toString();
  }
  function next(value, page, anchor = "") {
    const held = normalize(value);
    if (!held || !safePage(page)) return null;
    // A card selected while browsing defines a new origin. Detail descendants retain it.
    const browsing = held.page === "index.html" || held.page.startsWith("lists/");
    const origin = normalize(browsing ? held : held.origin || held, false);
    const chartList = page.startsWith("lists/charts-");
    const retired = chartList && (held.retired || !!(held.origin && held.origin.retired));
    return normalize({ ...held, page, anchor, card: "", offset: 0, filter: "", retired, selection: "", origin });
  }
  function scrollTarget(value, currentScroll, cardTop = null) {
    const held = normalize(value);
    if (!held) return null;
    if (cardTop !== null && Number.isFinite(cardTop)) return Math.max(0, currentScroll + cardTop - held.offset);
    return held.card ? null : Math.max(0, -held.offset);
  }
  return { normalize, decode, search, safePage, next, scrollTarget };
});
