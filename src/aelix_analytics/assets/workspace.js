// Aelix Analytics Workspace: tabs, lazy plotly rendering, timeline filters, live reload.
(function () {
  "use strict";
  const doc = document.documentElement;
  const root = doc.dataset.root || "";
  const build = doc.dataset.build;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* storage unavailable */ } },
  };

  // --- plotly: render a figure once it is visible (hidden tabs have no size) ---
  const pending = new Set(document.querySelectorAll(".plotly-chart[data-fig]"));
  function renderPlot(el) {
    if (!pending.has(el)) return;
    if (!window.Plotly) {
      // The figure's element holds the chart's own text (lang=""); this message is the kit's.
      el.lang = "en";
      el.textContent = "Could not load plotly.js (assets/plotly.min.js).";
      pending.delete(el);
      return;
    }
    pending.delete(el);
    try {
      const fig = JSON.parse(document.getElementById(el.dataset.fig).textContent);
      window.Plotly.newPlot(el, fig.data || [], fig.layout || {}, { responsive: true, displaylogo: false });
    } catch (err) {
      el.lang = "en";
      el.textContent = "Could not draw the plotly figure: " + err;
    }
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
  const notice = document.querySelector(".state-notice");
  function warn(message) { if (notice) { notice.hidden = false; notice.textContent = message; } }
  if (held.invalid) warn("Invalid saved Workspace state was ignored. Select the original item again.");
  if (state && state.source && state.source !== doc.dataset.source) warn("Source records changed since this view was saved. The selected identity is retained; evidence availability is checked now.");
  if (state && selectedIdentity() && state.selection && state.selection !== selectedIdentity()) warn("The requested selected identity is not present on this page. No latest-version substitution was made.");
  const tabs = Array.from(document.querySelectorAll("main > section.tab"));
  const labels = { overview: "Overview", insights: "Insights", charts: "Visualizations", timeline: "Timeline", data: "Data", runs: "Runs", context: "Context", assets: "Analysis assets", evaluation: "Evaluation" };
  let nav;
  function show() {
    const id = anchor();
    const target = id ? document.getElementById(id) : null;
    const tab = (target && target.closest("section.tab")) || document.getElementById(state && state.tab) || tabs[0];
    tabs.forEach(t => t.classList.toggle("on", t === tab));
    if (nav) nav.querySelectorAll("a").forEach(a => {
      a.classList.toggle("on", a.dataset.tab === (tab && tab.id));
      if (a.classList.contains("on")) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    renderVisiblePlots();
    drawRelations();
  }
  if (tabs.length) {
    doc.classList.add("js-tabs");
    nav = document.createElement("nav"); nav.className = "tabs"; nav.setAttribute("aria-label", "Workspace sections");
    tabs.forEach(t => { const a = document.createElement("a"); a.href = "#" + t.id; a.textContent = labels[t.id] || t.id; a.dataset.tab = t.id; nav.appendChild(a); });
    document.querySelector("main").prepend(nav);
  }
  const retired = document.getElementById("show-retired");
  function filter(bar, kind) {
    const list = bar.nextElementSibling && (bar.nextElementSibling.matches("ol") ? bar.nextElementSibling : bar.parentElement.querySelector("ol"));
    if (!list) return;
    bar.querySelectorAll("button").forEach(b => { b.classList.toggle("on", b.dataset.kind === kind); b.setAttribute("aria-pressed", String(b.dataset.kind === kind)); });
    list.querySelectorAll("li.ev,li.relation-item").forEach(li => { li.hidden = !!kind && (li.dataset.kind || li.dataset.direction) !== kind; });
    list.querySelectorAll("li.day").forEach(day => {
      let n = day.nextElementSibling, any = false;
      while (n && !n.classList.contains("day")) { if (!n.hidden) any = true; n = n.nextElementSibling; }
      day.hidden = !any;
    });
    if (bar.classList.contains("relation-filters")) document.querySelectorAll(".relation-node").forEach(el => { el.hidden = !!kind && el.dataset.direction !== kind; });
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
    if (value && value.page !== page) { restoring = false; return; }
    state = value || null;
    if (retired) { retired.checked = !!(state && state.retired); doc.classList.toggle("show-retired", retired.checked); }
    document.querySelectorAll(".filters").forEach(bar => {
      const wanted = state && state.filter || "";
      const valid = Array.from(bar.querySelectorAll("button")).some(b => b.dataset.kind === wanted);
      if (!valid && wanted) warn("The saved filter is no longer available on this page. Showing this page's complete scope.");
      filter(bar, valid ? wanted : "");
    });
    show();
    requestAnimationFrame(() => {
      const target = state && state.card ? document.getElementById(state.card) : document.getElementById(anchor());
      if (state && state.card && !target) warn("The original card is no longer in this page's scope. Its saved identity was not replaced by a latest version.");
      if (state) {
        const position = codec.scrollTarget(state, window.scrollY, target ? target.getBoundingClientRect().top : null);
        if (position !== null) window.scrollTo(0, position);
      } else if (target) window.scrollTo(0, window.scrollY + target.getBoundingClientRect().top - 72);
      if (target && state && state.card) {
        if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
        target.focus({ preventScroll: true });
      }
      returnLinks();
      restoring = false;
      if (!value) save();
    });
  }
  function returnLinks() {
    document.querySelectorAll("a[data-return]").forEach(a => {
      const origin = state && state.origin;
      if (!origin) return;
      a.href = root + origin.page + codec.search(origin) + (origin.anchor ? "#" + encodeURIComponent(origin.anchor) : "");
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
    if (a.hasAttribute("data-reset")) { url.search = ""; a.href = url.href; return; }
    if (a.hasAttribute("data-return")) return;
    const card = a.closest(".chart-card[id],li.ev[id],article.panel[id],li[id],tr[id]");
    save(false, card);
    if (dest === page && url.hash !== location.hash) return; // native fragment/back behavior
    const next = codec.next(state, dest, identity(url.hash.slice(1)));
    if (!next) return;
    url.search = codec.search(next, url.search); a.href = url.href;
  });
  window.addEventListener("hashchange", () => { show(); save(); returnLinks(); });
  window.addEventListener("popstate", e => { const decoded = codec.decode(location.search); restore(codec.normalize(e.state) || decoded.state); });
  window.addEventListener("pageshow", () => restore(codec.decode(location.search).state || state));
  window.addEventListener("pagehide", () => save());
  let scrollTimer;
  window.addEventListener("scroll", () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(() => save(), 180); }, { passive: true });
  document.addEventListener("keydown", e => {
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
  document.querySelectorAll(".relation-diagram").forEach(el => el.addEventListener("toggle", drawRelations));
  restore(state);

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
