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

  // --- tabs (index page): sections become tabs; any #anchor opens the tab that holds it ---
  const tabs = Array.from(document.querySelectorAll("main > section.tab"));
  // Tab names are labels; the section ids (URL anchors) stay as they are.
  const labels = { overview: "Overview", charts: "Visualizations", timeline: "Timeline", data: "Data", runs: "Runs", context: "Context", evaluation: "Evaluation" };
  if (tabs.length) {
    doc.classList.add("js-tabs");
    const nav = document.createElement("nav");
    nav.className = "tabs";
    nav.setAttribute("aria-label", "Workspace sections");
    tabs.forEach((t) => {
      const a = document.createElement("a");
      a.href = "#" + t.id;
      a.textContent = labels[t.id] || t.id;
      a.dataset.tab = t.id;
      nav.appendChild(a);
    });
    document.querySelector("main").prepend(nav);
    const show = () => {
      const id = decodeURIComponent(location.hash.slice(1));
      const target = id ? document.getElementById(id) : null;
      const tab = (target && target.closest("section.tab")) || tabs[0];
      tabs.forEach((t) => t.classList.toggle("on", t === tab));
      nav.querySelectorAll("a").forEach((a) => a.classList.toggle("on", a.dataset.tab === tab.id));
      if (target && target !== tab) requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
      else if (target === tab) window.scrollTo(0, 0);
      renderVisiblePlots();
    };
    window.addEventListener("hashchange", show);
    show();
  } else {
    renderVisiblePlots();
  }

  // --- charts that the latest run of their script no longer produces ---
  const retired = document.getElementById("show-retired");
  if (retired) retired.addEventListener("change", () => {
    doc.classList.toggle("show-retired", retired.checked);
    renderVisiblePlots();
  });

  // --- timeline filters ---
  document.querySelectorAll(".filters").forEach((bar) => {
    const list = bar.nextElementSibling;
    bar.addEventListener("click", (e) => {
      const btn = e.target.closest("button");
      if (!btn || !list) return;
      bar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === btn));
      const kind = btn.dataset.kind;
      list.querySelectorAll("li.ev").forEach((li) => { li.hidden = !!kind && li.dataset.kind !== kind; });
      list.querySelectorAll("li.day").forEach((day) => {
        let n = day.nextElementSibling;
        let any = false;
        while (n && !n.classList.contains("day")) { if (!n.hidden) any = true; n = n.nextElementSibling; }
        day.hidden = !any;
      });
    });
  });

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
  try {
    // The position the page saved before it reloaded into this one, used once: the key is left
    // empty, not removed, so a position an earlier kit's page saved under the old key is read once.
    let saved = sessionStorage.getItem(scrollKey);
    if (saved === null) saved = sessionStorage.getItem("lab-scroll:" + location.pathname);
    if (saved !== null) sessionStorage.setItem(scrollKey, "");
    if (saved) {
      window.addEventListener("load", () => window.scrollTo(0, Number(saved)));
    }
  } catch (_) { /* storage unavailable */ }
  function poll() {
    if (!live || document.hidden) return;
    const s = document.createElement("script");
    s.src = root + "assets/build.js?t=" + Date.now();
    s.onload = () => {
      s.remove();
      if (window.__ANALYTICS_BUILD__ && build && window.__ANALYTICS_BUILD__ !== build) {
        try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (_) { /* ignore */ }
        location.reload();
      }
    };
    s.onerror = () => s.remove();
    document.head.appendChild(s);
  }
  setInterval(poll, 3000);
})();
