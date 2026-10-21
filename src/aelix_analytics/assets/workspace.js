// Aelix Analytics Workspace: tabs, plotly read and drawn when a figure shows, relation filters, live reload.
(function () {
  "use strict";
  const doc = document.documentElement;
  const root = doc.dataset.root || "";
  const build = doc.dataset.build;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* storage unavailable */ } },
  };
  // --- the kit's words: English as written, or the reader's language (workspace-i18n.js) ---
  const I18n = window.AnalyticsI18n || null;
  function T(key, args) { return I18n ? I18n.t(key, args) : key; }
  // An element whose words become the kit's: switched with the page from now on.
  function kitWords(el, key, args) {
    el.setAttribute("data-i18n", key);
    if (args) el.setAttribute("data-i18n-args", JSON.stringify(args)); else el.removeAttribute("data-i18n-args");
    // As the page's own words are: English where the language has none says so.
    const shown = I18n ? I18n.format(key, args) : { text: key, fallback: false };
    if (shown.fallback) el.setAttribute("lang", "en"); else el.removeAttribute("lang");
    el.textContent = shown.text;
  }

  // --- plotly: read the library when a figure is about to show, not before the page paints, and
  // draw each figure once it is visible (hidden tabs have no size) ---
  const pending = new Set(document.querySelectorAll(".plotly-chart[data-fig]"));
  let library = null;
  function plotlyLibrary() {
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (!doc.dataset.plotly || !document.createElement) return Promise.resolve(null);
    if (!library) {
      library = new Promise(resolve => {
        const script = document.createElement("script");
        script.src = doc.dataset.plotly;
        script.onload = () => resolve(window.Plotly || null);
        script.onerror = () => resolve(null);
        document.head.appendChild(script);
      });
    }
    return library;
  }
  // A narrow screen draws a figure without plotly's tool bar, which would cover its title.
  function narrow() { return !!(window.matchMedia && window.matchMedia("(max-width: 40em)").matches); }
  function renderPlot(el) {
    if (!pending.has(el)) return;
    pending.delete(el);
    return plotlyLibrary().then(Plotly => {
      if (!Plotly) {
        // The figure's element holds the chart's own text (lang=""); this message is the kit's.
        kitWords(el, "chart.plotly_missing");
        return;
      }
      try {
        const fig = JSON.parse(document.getElementById(el.dataset.fig).textContent);
        Plotly.newPlot(el, fig.data || [], fig.layout || {}, { responsive: true, displaylogo: false,
          displayModeBar: narrow() ? false : "hover" });
      } catch (err) {
        // The error is plotly's or the browser's, in their language: beside the kit's words, not in them.
        const words = document.createElement("span"), detail = document.createElement("span");
        kitWords(words, "chart.plotly_failed");
        detail.setAttribute("lang", "");
        detail.textContent = String(err);
        el.replaceChildren(words, " ", detail);
      }
    });
  }
  const observer = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        entries.forEach((en) => {
          if (en.isIntersecting) { observer.unobserve(en.target); renderPlot(en.target); }
        });
      }, { rootMargin: "300px" })
    : null;
  function renderVisiblePlots() {
    pending.forEach((el) => {
      if (el.offsetParent === null) return;
      if (observer) observer.observe(el); else renderPlot(el);
    });
  }

  // --- navigation: URL is authoritative; storage and history are optional supplements ---
  const codec = window.AnalyticsWorkspaceState;
  const page = doc.dataset.page;
  function identity(value) { try { return decodeURIComponent(value || ""); } catch (_) { return ""; } }
  function selectedIdentity() {
    const target = document.getElementById(anchor());
    return identity(target && target.dataset.selection || doc.dataset.selection);
  }
  const rootURL = new URL(root + "index.html", location.href);
  const directory = rootURL.pathname.slice(0, rootURL.pathname.lastIndexOf("/") + 1);
  function anchor() { try { return decodeURIComponent(location.hash.slice(1)); } catch (_) { return ""; } }
  let held = codec.decode(location.search);
  let state = held.state;
  // The page's language: a working storage's choice first, else the URL state's (storage blocked).
  let storage = null;
  try { storage = window.localStorage; } catch (_) { storage = null; }
  let language = I18n ? I18n.choose(storage, state && state.lang) : "en";
  if (I18n) I18n.apply(document, language);
  const notice = document.querySelector(".state-notice");
  function warn(key) {
    if (!notice) return;
    notice.hidden = false;
    if (!notice.append) { notice.textContent = T(key); return; }
    notice.textContent = "";
    const text = document.createElement("span");
    kitWords(text, key);
    const close = document.createElement("button");
    close.type = "button"; close.className = "notice-close";
    kitWords(close, "action.dismiss");
    close.addEventListener("click", () => { notice.hidden = true; });
    notice.append(text, " ", close);
  }
  // The height the sticky top bar and section/jump navigation cover once stuck.
  function clearance() {
    let covered = 0;
    document.querySelectorAll(".topbar, nav.tabs, nav.jump").forEach(el => {
      const style = window.getComputedStyle(el);
      if (style.position === "sticky" || style.position === "fixed") covered = Math.max(covered, (parseFloat(style.top) || 0) + el.offsetHeight);
    });
    return covered ? covered + 12 : 72;
  }
  if (held.invalid) warn("notice.state_invalid");
  if (state && state.source && state.source !== doc.dataset.source) warn("notice.state_source_changed");
  if (state && selectedIdentity() && state.selection && state.selection !== selectedIdentity()) warn("notice.state_selection_absent");
  const tabs = Array.from(document.querySelectorAll("main > section.tab"));
  let nav;
  // A relation page's provenance trails, one direction shown at a time (design WORKSPACE-READABILITY 6.3).
  const trails = Array.from(document.querySelectorAll(".pv-trail"));
  let shownTrail = trails.length ? trails[0].id : "";
  if (trails.length) doc.classList.add("js-trail");
  function show() {
    const id = anchor();
    const target = id ? document.getElementById(id) : null;
    const retiredCard = target && target.closest(".chart-card.is-retired");
    if (retired && retiredCard) { retired.checked = true; doc.classList.add("show-retired"); }
    const tab = (target && target.closest("section.tab")) || document.getElementById(state && state.tab) || tabs[0];
    tabs.forEach(t => t.classList.toggle("on", t === tab));
    if (nav) nav.querySelectorAll("a").forEach(a => {
      a.classList.toggle("on", a.dataset.tab === (tab && tab.id));
      if (a.classList.contains("on")) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    // A trail's box or direction in the address shows that direction (design 6.3: one at a time).
    const trail = target && target.closest && target.closest(".pv-trail");
    if (trails.length) direction(trail ? trail.id : shownTrail);
    revealTab();
    renderVisiblePlots();
    drawRelations();
    drawTrails();
  }
  if (tabs.length) {
    doc.classList.add("js-tabs");
    nav = document.createElement("nav"); nav.className = "tabs";
    nav.setAttribute("aria-label", T("nav.sections")); nav.setAttribute("data-i18n-aria-label", "nav.sections");
    tabs.forEach(t => {
      const a = document.createElement("a"); a.href = "#" + t.id; a.dataset.tab = t.id;
      // The tab's words in an element of their own: a language switch rewrites them, not the count.
      const label = document.createElement("span");
      if (I18n && I18n.has("tab." + t.id)) kitWords(label, "tab." + t.id);
      else { const heading = t.querySelector && t.querySelector("h2"); label.textContent = heading ? heading.textContent : t.id; }
      a.appendChild(label);
      // How many items a section lists, as it says (design WORKSPACE-READABILITY 7.1).
      const count = t.dataset && t.dataset.count;
      if (count) {
        // A space between the words and the count, so they are not read as one word.
        a.appendChild(document.createTextNode(" "));
        const n = document.createElement("span"); n.className = "n"; n.textContent = Number(count).toLocaleString("en-US"); a.appendChild(n);
      }
      nav.appendChild(a);
    });
    // A relation page names its item first and then its sections (design 6.4, mockup 7-B); the index starts with them.
    if (/^relations\//.test(page || "") && tabs[0].before) tabs[0].before(nav);
    else document.querySelector("main").prepend(nav);
  }
  const retired = document.getElementById("show-retired");
  // A relation page's direction filter (this page's relations): what the record cites, or what cites it.
  // A list's kind filter is a set of links to each kind's own list pages, which need no script.
  function filter(bar, kind) {
    bar.querySelectorAll("button").forEach(b => { b.classList.toggle("on", b.dataset.kind === kind); b.setAttribute("aria-pressed", String(b.dataset.kind === kind)); });
    document.querySelectorAll(".relation-group,.relation-node").forEach(el => { el.hidden = !!kind && el.dataset.direction !== kind; });
    drawRelations();
  }
  let restoring = false;
  function snapshot(card) {
    const savedCard = state && state.card && document.getElementById(state.card);
    const target = card || (savedCard && savedCard.offsetParent !== null ? savedCard : document.getElementById(anchor()));
    const tab = tabs.find(t => t.classList.contains("on"));
    const bar = document.querySelector(".filters button.on");
    const value = { v: 1, page, anchor: anchor(), tab: tab ? tab.id : "overview", filter: bar ? bar.dataset.kind : "",
      retired: !!(retired && retired.checked), selection: (card && identity(card.dataset.selection)) || selectedIdentity() || "",
      source: doc.dataset.source, card: target ? target.id : "", offset: target ? target.getBoundingClientRect().top : -window.scrollY };
    // The URL carries a language other than English, so a page whose storage is blocked keeps it.
    if (language !== "en") value.lang = language;
    if (state && state.origin) value.origin = state.origin;
    return codec.normalize(value);
  }
  function save(push = false, card) {
    if (restoring) return;
    const value = snapshot(card); if (!value) return;
    state = value;
    try { history[push ? "pushState" : "replaceState"](value, "", codec.search(value, location.search) + location.hash); } catch (_) { /* URL on each navigation still carries the snapshot */ }
  }
  function restore(value) {
    restoring = true;
    if (value && value.page !== page) {
      warn("notice.state_other_page");
      value = null;
    }
    state = value || null;
    if (retired) { retired.checked = !!(state && state.retired); doc.classList.toggle("show-retired", retired.checked); }
    document.querySelectorAll(".filters").forEach(bar => {
      const wanted = state && state.filter || "";
      const valid = Array.from(bar.querySelectorAll("button")).some(b => b.dataset.kind === wanted);
      if (!valid && wanted) warn("notice.state_filter_gone");
      filter(bar, valid ? wanted : "");
    });
    show();
    requestAnimationFrame(() => {
      const target = state && state.card ? document.getElementById(state.card) : document.getElementById(anchor());
      if (state && state.card && !target) warn("notice.state_card_gone");
      // A row inside a closed fold (Recent activity, the map's smaller flows) is shown before the page
      // measures and focuses it (#98).
      for (let fold = target && target.closest && target.closest("details:not([open])"); fold;
        fold = fold.parentElement && fold.parentElement.closest ? fold.parentElement.closest("details:not([open])") : null) fold.open = true;
      if (state && state.card) {
        const position = codec.scrollTarget(state, window.scrollY, target ? target.getBoundingClientRect().top : null);
        if (position !== null) window.scrollTo(0, position);
      } else if (target) {
        // A newly opened anchor has no saved card offset: place it below the sticky navigation.
        window.scrollTo(0, Math.max(0, window.scrollY + target.getBoundingClientRect().top - clearance()));
      } else if (state) {
        const position = codec.scrollTarget(state, window.scrollY, null);
        if (position !== null) window.scrollTo(0, position);
      }
      if (target && state && state.card) {
        if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
      }
      returnLinks();
      restoring = false;
      if (!value) save();
    });
  }
  // The way back names where it goes (design WORKSPACE-READABILITY 8.1): the index section or the
  // list the reader came from, or the page a reader opened directly and left (codec.next keeps a
  // page with no origin of its own as the origin).
  const LIST_BACK = { timeline: "back.tab.timeline", insights: "back.tab.insights", charts: "back.tab.charts",
    runs: "back.tab.runs", data: "back.tab.data", feedback: "back.list.feedback", open: "back.list.open",
    reports: "back.tab.reports" };
  const PAGE_BACK = { runs: "back.page.run", data: "back.page.data", relations: "back.page.relations",
    scripts: "back.page.script", reports: "back.page.report" };
  function backKey(origin) {
    if (origin.page === "index.html") return "back.tab." + (origin.tab || "overview");
    if (origin.page === "context.html") return "back.page.context";
    const list = /^lists\/(kind-.+|[a-z]+)-\d+\.html$/.exec(origin.page);
    if (list) return list[1].indexOf("kind-") === 0 ? "back.list.kind" : LIST_BACK[list[1]] || null;
    const detail = /^(runs|data|relations|scripts|reports)\//.exec(origin.page);
    return detail ? PAGE_BACK[detail[1]] : null;
  }
  // The words of a way back: its origin's, else the page's own (`data-back`). Both are the kit's
  // words of a key, so they switch with the page's language.
  function backWords(a, key) {
    if (!I18n) return;
    let words = a.querySelector("[data-back-words]");
    if (!key || !I18n.has(key)) {
      // No origin: the page's own words, which the page switches as its other words (a <k-w>);
      // an origin's words written here before go back to the page's own key.
      const home = a.getAttribute && a.getAttribute("data-back");
      if (words && home && I18n.has(home)) kitWords(words, home);
      return;
    }
    if (!words) {
      words = document.createElement("span");
      words.setAttribute("data-back-words", "");
      const written = a.querySelector(I18n.WORD);
      if (written) written.replaceWith(words); else a.append(words);
    }
    kitWords(words, key);
  }
  function returnLinks() {
    document.querySelectorAll("a[data-return]").forEach(a => {
      const origin = state && state.origin;
      if (origin) {
        // Back at the origin in the language chosen since, not the one it was left in.
        const back = { ...origin };
        if (language !== "en") back.lang = language; else delete back.lang;
        a.href = root + origin.page + codec.search(back) + (origin.anchor ? "#" + encodeURIComponent(origin.anchor) : "");
        if (a.querySelector) backWords(a, backKey(origin));
        return;
      }
      if (a.querySelector) backWords(a, null);
      // No origin (a page opened directly): the page's own return link, in the reader's language.
      if (a.dataset && !a.dataset.home) a.dataset.home = a.getAttribute("href");
      const home = new URL((a.dataset && a.dataset.home) || a.href, location.href);
      const dest = home.pathname.slice(directory.length);
      home.search = language !== "en" && codec.safePage(dest) ? codec.search({ v: 1, page: dest, lang: language }) : "";
      a.href = home.href;
    });
  }
  document.querySelectorAll(".filters").forEach(bar => bar.addEventListener("click", e => {
    const button = e.target.closest("button"); if (!button) return;
    filter(bar, button.dataset.kind); save(true); returnLinks();
  }));
  if (retired) retired.addEventListener("change", () => { doc.classList.toggle("show-retired", retired.checked); renderVisiblePlots(); save(true); });
  document.addEventListener("click", e => {
    const a = e.target.closest("a[href]"); if (!a || a.hasAttribute("download")) return;
    const url = new URL(a.href, location.href);
    if (url.protocol !== location.protocol || url.host !== location.host || !url.pathname.startsWith(directory)) return;
    const dest = url.pathname.slice(directory.length);
    if (!codec.safePage(dest)) return;
    if (a.hasAttribute("data-reset")) {
      // A reset view keeps the reader's language: a page whose storage is blocked has it only here.
      url.search = language !== "en" ? codec.search({ v: 1, page: dest, lang: language }) : "";
      a.href = url.href; return;
    }
    if (a.hasAttribute("data-return")) return;
    const card = a.closest(".chart-card[id],li.ev[id],article.panel[id],li[id],tr[id]");
    save(false, card);
    if (dest === page && url.hash !== location.hash) return; // native fragment/back behavior
    const next = codec.next(state, dest, identity(url.hash.slice(1)));
    if (!next) return;
    url.search = codec.search(next, url.search); a.href = url.href;
  });
  window.addEventListener("hashchange", () => { show(); save(); returnLinks(); });
  window.addEventListener("popstate", e => {
    const value = codec.normalize(e.state) || codec.decode(location.search).state;
    // Back or Forward: where storage is blocked, the entry's language (a stored choice holds).
    relanguage(value && value.lang);
    restore(value);
  });
  // The language as the page chooses it on load — a working storage's choice, else the URL state's,
  // else English — again: after Back or Forward, when shown from the back-forward cache, or when
  // another tab chose. True when it changed.
  function relanguage(stateLanguage) {
    if (!I18n) return false;
    const chosen = I18n.choose(storage, stateLanguage);
    if (chosen === language) return false;
    language = chosen;
    I18n.apply(document, language);
    document.querySelectorAll(".build .language select").forEach(select => { select.value = language; });
    return true;
  }
  window.addEventListener("pageshow", () => {
    const value = codec.decode(location.search).state || state;
    relanguage(value && value.lang);
    restore(value);
  });
  window.addEventListener("storage", e => {
    if (I18n && e.key === I18n.KEY && relanguage(state && state.lang)) { save(); returnLinks(); }
  });
  window.addEventListener("pagehide", () => save());
  let scrollTimer;
  window.addEventListener("scroll", () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(() => save(), 180); }, { passive: true });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && notice && !notice.hidden && notice.contains && notice.contains(e.target)) { notice.hidden = true; return; }
    // An open page menu closes first, and its button takes the focus back.
    if (e.key === "Escape" && topbar && topbar.classList.contains("menu-open")) { menuOpen(false); if (menuButton) menuButton.focus(); return; }
    if (e.key === "Escape" && !e.target.matches("input,textarea,select")) {
      const a = document.querySelector("a[data-return]"); if (a) a.click();
    }
  });
  function drawRelations() {
    document.querySelectorAll(".relation-map").forEach(map => {
      const svg = map.querySelector("svg"), selected = map.querySelector(".relation-selected");
      if (!svg || !selected || !map.offsetWidth) return;
      const area = map.getBoundingClientRect(), focus = selected.getBoundingClientRect();
      svg.setAttribute("viewBox", "0 0 " + area.width + " " + area.height);
      svg.replaceChildren();
      document.querySelectorAll(".relation-node").forEach(node => {
        if (node.hidden) return;
        const box = node.getBoundingClientRect(), incoming = node.dataset.direction === "incoming";
        const x1 = (incoming ? box.right : focus.right) - area.left;
        const y1 = (incoming ? box.top + box.height / 2 : focus.top + focus.height / 2) - area.top;
        const x2 = (incoming ? focus.left : box.left) - area.left;
        const y2 = (incoming ? focus.top + focus.height / 2 : box.top + box.height / 2) - area.top;
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", `M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2} M${x2-6},${y2-4} L${x2},${y2} L${x2-6},${y2+4}`);
        svg.appendChild(path);
      });
    });
  }
  window.addEventListener("resize", drawRelations);
  // A provenance trail (design WORKSPACE-READABILITY 6): the boxes are the list the page holds; on a wide screen the
  // style sheet places them on their grid (--graph) and the lines between them are drawn here from data-e, each
  // `from>to` with its kind (c cites, r read, w wrote, m made) and flags (a one of several sources, i inferred, y cycle).
  function direction(id) {
    const wanted = trails.find(tr => tr.id === id) || trails[0];
    shownTrail = wanted.id;
    trails.forEach(tr => { tr.hidden = trails.length > 1 && tr !== wanted; });
    document.querySelectorAll(".pv-dir a").forEach(a => {
      const on = a.getAttribute("href") === "#" + wanted.id;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
    });
  }
  const SVG = "http://www.w3.org/2000/svg";
  const LINE_WORDS = { c: "provenance.line.cites", r: "provenance.line.read", w: "provenance.line.wrote", m: "provenance.line.made" };
  function drawTrails() {
    if (!document.createElementNS) return;
    document.querySelectorAll(".pv-list").forEach(list => {
      const scroll = list.parentElement;
      let svg = scroll.querySelector("svg.pv-lines");
      let legend = scroll.parentElement.querySelector(".pv-legend");
      const graph = !!list.offsetWidth && window.getComputedStyle(list).getPropertyValue("--graph").trim() === "1";
      if (!graph) {
        if (svg) svg.replaceChildren();
        if (legend) legend.hidden = true;
        return;
      }
      if (!svg) {
        svg = document.createElementNS(SVG, "svg");
        svg.setAttribute("class", "pv-lines"); svg.setAttribute("aria-hidden", "true"); svg.setAttribute("focusable", "false");
        scroll.insertBefore(svg, list);
      }
      if (!legend) {
        legend = document.createElement("p"); legend.className = "pv-legend";
        kitWords(legend, "provenance.legend");
        scroll.after(legend);
      }
      legend.hidden = false;
      svg.replaceChildren();
      const area = list.getBoundingClientRect();
      svg.setAttribute("width", area.width); svg.setAttribute("height", area.height);
      svg.style.left = list.offsetLeft + "px"; svg.style.top = list.offsetTop + "px";
      const first = list.querySelector("li[id]");
      const prefix = first ? first.id.replace(/-\d+$/, "") : "";
      (list.dataset.e || "").split(" ").forEach(spec => {
        const m = /^(\d+)>(\d+)([crwm])([aiy]*)$/.exec(spec);
        const a = m && document.getElementById(prefix + "-" + m[1]), b = m && document.getElementById(prefix + "-" + m[2]);
        if (!a || !b || a.hidden || b.hidden) return;
        const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        const ax = (ra.left + ra.right) / 2 - area.left, ay = (ra.top + ra.bottom) / 2 - area.top;
        const bx = (rb.left + rb.right) / 2 - area.left, by = (rb.top + rb.bottom) / 2 - area.top;
        let x1, y1, x2, y2, d, dx = 0, dy = 0, lx, ly;
        if (Math.abs(ax - bx) < Math.min(ra.width, rb.width) / 2) {
          // In one column: from one box's top or bottom edge to the other's.
          x1 = ax; x2 = bx; y1 = ay < by ? ra.bottom - area.top : ra.top - area.top; y2 = ay < by ? rb.top - area.top : rb.bottom - area.top;
          d = `M${x1},${y1} L${x2},${y2}`; dy = y2 > y1 ? 1 : -1; lx = x1 + 6; ly = (y1 + y2) / 2 + 4;
        } else {
          // In other columns: from the facing sides, through the gap beside the first box, so a line never runs
          // behind a box and seems to leave it.
          x1 = ax < bx ? ra.right - area.left : ra.left - area.left; x2 = ax < bx ? rb.left - area.left : rb.right - area.left;
          y1 = ay; y2 = by; dx = x2 > x1 ? 1 : -1;
          const gap = Math.min(Math.abs(x2 - x1) / 2, 28), gx = x1 + dx * gap, r = Math.min(8, Math.abs(y2 - y1) / 2);
          const dv = y2 > y1 ? 1 : -1;
          d = Math.abs(y2 - y1) < 1 ? `M${x1},${y1} L${x2},${y2}`
            : `M${x1},${y1} L${gx - dx * r},${y1} Q${gx},${y1} ${gx},${y1 + dv * r} L${gx},${y2 - dv * r} Q${gx},${y2} ${gx + dx * r},${y2} L${x2},${y2}`;
          // A bent line has no label: the gap is too narrow for one, and the boxes' words say it.
          if (Math.abs(y2 - y1) < 1) { lx = (x1 + x2) / 2; ly = y1 - 5; }
        }
        const tone = m[3] === "c" ? "c" : /[ai]/.test(m[4]) ? "maybe" : "";
        const path = document.createElementNS(SVG, "path"); path.setAttribute("d", d); if (tone) path.setAttribute("class", tone);
        const head = document.createElementNS(SVG, "path");
        head.setAttribute("class", "head " + tone);
        head.setAttribute("d", dy ? `M${x2 - 4},${y2 - 6 * dy} L${x2},${y2} L${x2 + 4},${y2 - 6 * dy}Z` : `M${x2 - 6 * dx},${y2 - 4} L${x2},${y2} L${x2 - 6 * dx},${y2 + 4}Z`);
        svg.append(path, head);
        if (lx === undefined) return;
        // The kind of a line beside it: right of a vertical line, centred above a horizontal one.
        const label = document.createElementNS(SVG, "text");
        label.setAttribute("x", lx); label.setAttribute("y", ly);
        if (dx) label.setAttribute("text-anchor", "middle");
        kitWords(label, LINE_WORDS[m[3]]);
        svg.append(label);
      });
    });
  }
  window.addEventListener("resize", drawTrails);
  document.querySelectorAll(".relation-diagram").forEach(el => el.addEventListener("toggle", drawRelations));
  // The diagram is a secondary disclosure: it opens by default only where its three columns fit.
  if (window.matchMedia && !window.matchMedia("(max-width: 40em)").matches) {
    document.querySelectorAll(".relation-diagram").forEach(el => { el.open = true; });
  }
  restore(state);

  // --- a narrow screen's top bar is one line: the build line folds behind a menu button ---
  const topbar = document.querySelector(".topbar"), pageMenu = document.getElementById("page-menu");
  let menuButton = null;
  function menuOpen(open) {
    if (!topbar || !menuButton) return;
    topbar.classList.toggle("menu-open", open);
    menuButton.setAttribute("aria-expanded", String(open));
  }
  if (topbar && pageMenu && topbar.insertBefore && document.createElement) {
    menuButton = document.createElement("button");
    menuButton.type = "button"; menuButton.className = "menu-toggle";
    menuButton.setAttribute("aria-controls", "page-menu");
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.setAttribute("aria-label", T("chrome.menu")); menuButton.setAttribute("data-i18n-aria-label", "chrome.menu");
    menuButton.textContent = "⋯";
    menuButton.addEventListener("click", () => menuOpen(!topbar.classList.contains("menu-open")));
    topbar.insertBefore(menuButton, pageMenu);
    doc.classList.add("js-menu");
    // A click elsewhere closes an open menu.
    document.addEventListener("click", e => {
      if (topbar.classList.contains("menu-open") && topbar.contains && !topbar.contains(e.target)) menuOpen(false);
    });
  }
  // The sticky section bar sits below the top bar, whatever height the reader's text size gives it.
  function topbarHeight() {
    if (topbar && doc.style && doc.style.setProperty && topbar.offsetHeight) doc.style.setProperty("--topbar", topbar.offsetHeight + "px");
  }
  topbarHeight();
  window.addEventListener("resize", topbarHeight);
  // The section bar and a page's jump bar scroll sideways on a narrow screen: an edge fades where
  // more links are, and the section shown is scrolled into the bar.
  function edgesOf(bar) {
    if (!bar || !bar.classList || typeof bar.scrollWidth !== "number") return;
    const end = bar.scrollWidth - bar.clientWidth;
    bar.classList.toggle("more-before", bar.scrollLeft > 2);
    bar.classList.toggle("more-after", bar.scrollLeft < end - 2);
  }
  function bars() { return document.querySelectorAll ? Array.from(document.querySelectorAll("nav.tabs, nav.jump")) : []; }
  function edges() { bars().forEach(edgesOf); }
  function revealTab() {
    if (!nav || typeof nav.querySelector !== "function") return;
    const on = nav.querySelector("a.on");
    if (on && (on.offsetLeft < nav.scrollLeft || on.offsetLeft + on.offsetWidth > nav.scrollLeft + nav.clientWidth)) {
      nav.scrollLeft = Math.max(0, on.offsetLeft - 16);
    }
    edges();
  }
  bars().forEach(bar => { if (bar.addEventListener) bar.addEventListener("scroll", () => edgesOf(bar), { passive: true }); });
  window.addEventListener("resize", edges);
  revealTab();
  edges();

  // --- the language menu: a reader's choice is kept in storage, else carried in the URL state ---
  const buildBar = document.querySelector(".topbar .build");
  if (I18n && buildBar && buildBar.prepend) {
    buildBar.prepend(I18n.menu(document, chosen => {
      language = chosen;
      I18n.save(storage, chosen);
      I18n.apply(document, language);
      save(); returnLinks();
    }), " · ");
  }

  // --- live reload: poll assets/build.js (a script tag, so it also works from file://) ---
  // The storage keys were lab-live and lab-scroll: before kit 0.6.0. A missing new key takes the
  // old key's value once; the old key is left as it is, for a Workspace an earlier kit rendered.
  const box = document.getElementById("live");
  let liveSetting = store.get("analytics-live");
  if (liveSetting === null) {
    liveSetting = store.get("lab-live");
    if (liveSetting !== null) store.set("analytics-live", liveSetting);
  }
  let live = liveSetting !== "0";
  if (box) {
    box.checked = live;
    box.addEventListener("change", () => { live = box.checked; store.set("analytics-live", live ? "1" : "0"); });
  }
  const scrollKey = "analytics-scroll:" + location.pathname;
  // The position the page saved before it reloaded into this one, used once: the key is left empty,
  // not removed, so a position an earlier kit's page saved under the old key is read once.
  let saved = null;
  try {
    saved = sessionStorage.getItem(scrollKey);
    if (saved === null) saved = sessionStorage.getItem("lab-scroll:" + location.pathname);
  } catch (_) { /* storage unavailable */ }
  if (saved && !state) window.addEventListener("load", () => window.scrollTo(0, Number(saved)));
  if (saved !== null) {
    try { sessionStorage.setItem(scrollKey, ""); } catch (_) { /* storage full: restored anyway */ }
  }
  function poll() {
    if (!live || document.hidden) return;
    const s = document.createElement("script");
    s.src = root + "assets/build.js?t=" + Date.now();
    s.onload = () => {
      s.remove();
      if (window.__ANALYTICS_BUILD__ && build && window.__ANALYTICS_BUILD__ !== build) {
        try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (_) { /* ignore */ }
        save();
        location.reload();
      }
    };
    s.onerror = () => s.remove();
    document.head.appendChild(s);
  }
  setInterval(poll, 3000);
})();
