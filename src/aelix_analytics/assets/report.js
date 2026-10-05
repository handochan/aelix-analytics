// Aelix Analytics report: tabs, lazy plotly rendering, timeline filters, live reload.
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
      el.textContent = "plotly.js를 불러오지 못했습니다 (assets/plotly.min.js).";
      pending.delete(el);
      return;
    }
    pending.delete(el);
    try {
      const fig = JSON.parse(document.getElementById(el.dataset.fig).textContent);
      window.Plotly.newPlot(el, fig.data || [], fig.layout || {}, { responsive: true, displaylogo: false });
    } catch (err) {
      el.textContent = "plotly 그림을 그리지 못했습니다: " + err;
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
  const labels = { overview: "개요", charts: "차트", timeline: "타임라인", data: "데이터", runs: "실행", context: "맥락", evaluation: "평가" };
  if (tabs.length) {
    doc.classList.add("js-tabs");
    const nav = document.createElement("nav");
    nav.className = "tabs";
    nav.setAttribute("aria-label", "리포트 섹션");
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
  const box = document.getElementById("live");
  let live = store.get("lab-live") !== "0";
  if (box) {
    box.checked = live;
    box.addEventListener("change", () => { live = box.checked; store.set("lab-live", live ? "1" : "0"); });
  }
  const scrollKey = "lab-scroll:" + location.pathname;
  try {
    const saved = sessionStorage.getItem(scrollKey);
    if (saved) {
      sessionStorage.removeItem(scrollKey);
      window.addEventListener("load", () => window.scrollTo(0, Number(saved)));
    }
  } catch (_) { /* storage unavailable */ }
  function poll() {
    if (!live || document.hidden) return;
    const s = document.createElement("script");
    s.src = root + "assets/build.js?t=" + Date.now();
    s.onload = () => {
      s.remove();
      if (window.__LAB_BUILD__ && build && window.__LAB_BUILD__ !== build) {
        try { sessionStorage.setItem(scrollKey, String(window.scrollY)); } catch (_) { /* ignore */ }
        location.reload();
      }
    };
    s.onerror = () => s.remove();
    document.head.appendChild(s);
  }
  setInterval(poll, 3000);
})();
