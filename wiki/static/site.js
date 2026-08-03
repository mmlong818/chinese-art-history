/* 检索。无依赖，索引是构建期产出的 search.json。
   file:// 下浏览器禁止 fetch 本地 JSON，此时不假装能搜——直接说明要起本地服务器。 */
(function () {
  "use strict";
  var up = (document.currentScript && document.currentScript.dataset.up) || "";
  var box = document.getElementById("qBox");
  var btn = document.getElementById("qOpen");
  var input = document.getElementById("qIn");
  var out = document.getElementById("qRes");
  if (!box || !btn || !input || !out) return;

  var PATH = { artist: "artists", work: "works", site: "sites", "class": "classes",
             treatise: "treatises", event: "events", period: "periods" };
  var LABEL = { artist: "艺术家", work: "作品", site: "遗址", "class": "类目",
              treatise: "画论", event: "事件", period: "分期" };
  var index = null, loading = false;

  function load() {
    if (index || loading) return;
    loading = true;
    if (location.protocol === "file:") {
      out.innerHTML = '<p class="q-none">file:// 下无法读取检索索引。'
        + "请在 dist/ 目录起本地服务器：python -m http.server 8733</p>";
      return;
    }
    fetch(up + "search.json")
      .then(function (r) { return r.json(); })
      .then(function (d) { index = d; loading = false; run(); })
      .catch(function () { out.innerHTML = '<p class="q-none">检索索引加载失败。</p>'; });
  }

  function score(it, q) {
    var best = -1;
    [it.n, it.l, it.p, it.d].forEach(function (f, i) {
      if (!f) return;
      var at = f.toLowerCase().indexOf(q);
      if (at < 0) return;
      var s = 1000 - at * 8 - i * 60 - (i === 0 && at === 0 ? -300 : 0);
      if (s > best) best = s;
    });
    return best;
  }

  function run() {
    var q = input.value.trim().toLowerCase();
    if (!q) { out.innerHTML = ""; return; }
    if (!index) { load(); return; }
    var hits = [];
    for (var i = 0; i < index.length; i++) {
      var s = score(index[i], q);
      if (s > 0) hits.push([s, index[i]]);
    }
    hits.sort(function (a, b) { return b[0] - a[0]; });
    if (!hits.length) { out.innerHTML = '<p class="q-none">无匹配条目。</p>'; return; }
    out.innerHTML = hits.slice(0, 40).map(function (h) {
      var it = h[1];
      return '<a href="' + up + PATH[it.k] + "/" + it.i + '.html">'
        + '<span class="q-k">' + (LABEL[it.k] || it.k) + "</span>"
        + '<span class="q-n">' + esc(it.n) + (it.l ? ' <i>' + esc(it.l) + "</i>" : "") + "</span>"
        + '<span class="q-d">' + esc(it.p || it.d || "") + "</span></a>";
    }).join("");
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }

  function open() { box.hidden = false; load(); input.focus(); input.select(); }
  function close() { box.hidden = true; btn.focus(); }

  /* ── 放大镜 ──────────────────────────────────────────────────────────
     点图版看细部。有 IIIF 的按需切一张 2000px（那接口本来就是干这个的），
     没有的退到原图。先把已加载的缩略图放上去撑住画面，高清到了再换——
     不留白，加载失败就停在缩略图上，不报错。 */
  var ov = null;

  function zoom(img) {
    var url = img.getAttribute("data-zoom");
    if (!url) return;
    ov = document.createElement("div");
    ov.className = "zoomov";
    ov.innerHTML = '<div class="zoomst">载入高清…</div>'
      + '<img class="zoomimg" src="' + img.src + '" alt="' + (img.alt || "") + '">'
      + '<button class="zoomx" aria-label="关闭">关闭 ✕</button>';
    document.body.appendChild(ov);
    document.body.style.overflow = "hidden";

    var big = ov.querySelector(".zoomimg"), st = ov.querySelector(".zoomst");
    var hi = new Image();
    hi.onload = function () { big.src = hi.src; ov.classList.add("zoomed"); st.remove(); };
    hi.onerror = function () { st.textContent = "高清图不可用，显示缩略图"; };
    hi.src = url;

    var down = false, sx = 0, sy = 0, ox = 0, oy = 0;
    big.addEventListener("pointerdown", function (ev) {
      down = true; sx = ev.clientX; sy = ev.clientY;
      big.setPointerCapture(ev.pointerId); ev.preventDefault();
    });
    big.addEventListener("pointermove", function (ev) {
      if (!down) return;
      ox += ev.clientX - sx; oy += ev.clientY - sy; sx = ev.clientX; sy = ev.clientY;
      big.style.transform = "translate(" + ox + "px," + oy + "px)";
    });
    big.addEventListener("pointerup", function () { down = false; });
    ov.addEventListener("click", function (ev) {
      if (ev.target === ov || ev.target.className === "zoomx") unzoom();
    });
  }

  function unzoom() {
    if (!ov) return;
    ov.remove(); ov = null; document.body.style.overflow = "";
  }

  document.addEventListener("click", function (ev) {
    var img = ev.target.closest && ev.target.closest(".plate .ph img, .fig .ph img");
    if (img && img.getAttribute("data-zoom")) { ev.preventDefault(); zoom(img); }
  });

  btn.addEventListener("click", function () { box.hidden ? open() : close(); });
  input.addEventListener("input", run);
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape" && ov) { unzoom(); return; }
    if (ev.key === "Escape" && !box.hidden) close();
    if (ev.key === "/" && box.hidden && !/^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) {
      ev.preventDefault(); open();
    }
  });
})();
