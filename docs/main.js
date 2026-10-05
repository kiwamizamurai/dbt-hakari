/* dbt-hakari landing page. Content is complete without JavaScript; this adds language and theme
   switching, copy buttons, and GSAP animations (skipped for prefers-reduced-motion). */
(function () {
  "use strict";
  var root = document.documentElement;
  var $ = function (s, c) { return (c || document).querySelector(s); };
  var $$ = function (s, c) { return Array.prototype.slice.call((c || document).querySelectorAll(s)); };
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  };

  /* language: both languages are in the HTML; the lang attribute picks which one is shown */
  var lang = store.get("hakari-lang") || ((navigator.language || "en").toLowerCase().indexOf("ja") === 0 ? "ja" : "en");
  function setLang(next) {
    lang = next; root.lang = next; store.set("hakari-lang", next);
    $("#lang").textContent = next === "ja" ? "English" : "日本語";
    document.title = next === "ja"
      ? "dbt-hakari: view を秤にかけて、BigQuery の請求を削る"
      : "dbt-hakari: weigh your views, shave your BigQuery bill";
  }
  setLang(lang);
  $("#lang").addEventListener("click", function () { setLang(lang === "ja" ? "en" : "ja"); });

  /* theme */
  var savedTheme = store.get("hakari-theme");
  if (savedTheme) root.setAttribute("data-theme", savedTheme);
  $("#theme").addEventListener("click", function () {
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next); store.set("hakari-theme", next);
  });

  /* nav shadow */
  var nav = $("#nav");
  function onScroll() { nav.classList.toggle("scrolled", window.scrollY > 12); }
  onScroll(); window.addEventListener("scroll", onScroll, { passive: true });

  /* install tabs and copy */
  var tabs = $$(".tab");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        $("#" + t.getAttribute("aria-controls")).hidden = !on;
      });
    });
  });
  $("#copy").addEventListener("click", function () {
    var panel = $$(".panel").filter(function (p) { return !p.hidden; })[0];
    var text = panel ? $("[data-copy]", panel).textContent.trim() : "";
    var btn = this, original = btn.innerHTML;
    var done = function () {
      btn.textContent = lang === "ja" ? "コピーしました" : "Copied";
      setTimeout(function () { btn.innerHTML = original; }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(done, function () {}); }
  });

  /* bars: heights come from data-v, relative to the first (largest) value */
  var max = Math.max.apply(null, $$(".bar").map(function (b) { return parseFloat(b.dataset.v); }));
  $$(".bar").forEach(function (b) { b.style.height = (parseFloat(b.dataset.v) / max * 100).toFixed(1) + "%"; });

  /* diagram: the state "materialized" changes what is billed */
  var materialized = false;
  var total = { v: 90 };
  function renderDiagram(next, animate) {
    materialized = next;
    var label = $("#d-view-label"), view = $("#d-view");
    label.textContent = next ? "table V" : "view V";
    $$("#d-chips text").forEach(function (t) { t.textContent = next ? "10 MiB ×1" : "10 MiB ×3"; });
    var target = next ? 60 : 90;
    var paint = function () { $("#d-total").textContent = Math.round(total.v) + " MiB / day"; };
    if (window.gsap && animate) {
      gsap.to(total, { v: target, duration: 0.8, ease: "power2.out", onUpdate: paint });
      gsap.to(view, { attr: { fill: next ? "#c8372d" : "" }, duration: 0.5 });
      gsap.to("#d-view-label", { fill: next ? "#fff" : "", duration: 0.5 });
      gsap.to(".l-in", { opacity: next ? 0.18 : 0.55, duration: 0.6 });
      gsap.fromTo("#d-chips .chip", { scale: 0.6, transformOrigin: "50% 50%" }, { scale: 1, duration: 0.5, stagger: 0.08, ease: "back.out(2)" });
    } else {
      total.v = target; paint();
      view.style.fill = next ? "#c8372d" : ""; label.style.fill = next ? "#fff" : "";
      $$(".l-in").forEach(function (l) { l.style.opacity = next ? 0.18 : 0.55; });
    }
  }
  $("#d-toggle").addEventListener("click", function () { renderDiagram(!materialized, true); });

  /* animations */
  var reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!window.gsap || reduce) {
    $$("[data-count]").forEach(function (el) { el.textContent = el.dataset.count + (el.dataset.suffix || ""); });
    return;
  }
  try {
    gsap.registerPlugin(ScrollTrigger);

    gsap.to("#wave", { attr: { x: 80 }, duration: 16, ease: "none", repeat: -1 });

    var intro = gsap.timeline({ defaults: { ease: "power3.out" } });
    intro.from(".kicker", { y: 14, opacity: 0, duration: 0.5 }, 0)
      .from("#h1 .word", { yPercent: 70, opacity: 0, rotate: 2, duration: 0.8, stagger: 0.09 }, 0.1)
      .from(".hero-copy", { y: 22, opacity: 0, duration: 0.7, stagger: 0.12 }, 0.55)
      .from(".terminal", { y: 40, opacity: 0, duration: 0.9 }, 0.35)
      .from("#hero-logo", { scale: 0.4, rotate: -18, opacity: 0, duration: 0.9, ease: "back.out(1.8)" }, 0.7)
      .from("#term .t-line", { opacity: 0, x: -8, duration: 0.25, stagger: 0.07 }, 0.9);
    gsap.to("#hero-logo", { y: -10, duration: 2.6, ease: "sine.inOut", yoyo: true, repeat: -1, delay: 1.6 });

    gsap.set(".reveal", { y: 36, opacity: 0 });
    ScrollTrigger.batch(".reveal", {
      start: "top 88%", once: true,
      onEnter: function (els) { gsap.to(els, { y: 0, opacity: 1, duration: 0.8, stagger: 0.12, ease: "power3.out" }); }
    });

    gsap.set(".bar", { scaleY: 0 });
    ScrollTrigger.create({
      trigger: "#bars", start: "top 82%", once: true,
      onEnter: function () { gsap.to(".bar", { scaleY: 1, duration: 1.1, ease: "elastic.out(1, 0.7)", stagger: 0.06 }); }
    });

    $$("[data-count]").forEach(function (el) {
      var end = parseFloat(el.dataset.count), decimals = parseInt(el.dataset.decimals || "0", 10), obj = { v: 0 };
      el.textContent = (0).toFixed(decimals) + (el.dataset.suffix || "");
      ScrollTrigger.create({
        trigger: el, start: "top 90%", once: true,
        onEnter: function () {
          gsap.to(obj, { v: end, duration: 1.4, ease: "power2.out",
            onUpdate: function () { el.textContent = obj.v.toFixed(decimals) + (el.dataset.suffix || ""); } });
        }
      });
    });

    ScrollTrigger.create({
      trigger: ".diagram", start: "top 70%", once: true,
      onEnter: function () { setTimeout(function () { if (!materialized) renderDiagram(true, true); }, 900); }
    });
  } catch (error) {
    /* never leave content hidden if an animation fails */
    gsap.set([".reveal", ".bar", "#h1 .word", ".hero-copy", ".terminal", "#hero-logo", "#term .t-line"], { clearProps: "all" });
    $$("[data-count]").forEach(function (el) { el.textContent = el.dataset.count + (el.dataset.suffix || ""); });
  }
})();
