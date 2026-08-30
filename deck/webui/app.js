"use strict";
/* quilt-deck day console — vanilla ES2020, no frameworks, no CDNs, offline.
 * Renders the day-export JSON described in docs/DAY-EXPORT-SCHEMA.md v1.
 * Pure logic lives above the DOM guard so `node -e` shims can test it. */

var SPECIES = ["pink", "chum", "king", "coho"];
var SP_COLOR = { pink: "#e75480", chum: "#7ec8a3", king: "#c9a227", coho: "#c0c0d0" };
var TOTE_ORDER = ["TOTE-PORT", "TOTE-HOLD", "TOTE-STBD-F", "TOTE-STBD-A"];
var TOTE_SP = { "TOTE-PORT": "pink", "TOTE-HOLD": "chum", "TOTE-STBD-F": "king", "TOTE-STBD-A": "coho" };
var FATHOMS_PER_HOOK = 1.5;
var VESSEL = "F/V EILEEN";

function fmtInt(n) {
  return Number.isFinite(n) ? Math.round(n).toLocaleString("en-US") : "—";
}
function fmtFm(n) {
  return Number.isFinite(n) ? (Math.round(n * 10) / 10).toFixed(1) : "—";
}
function spColor(sp) {
  return SP_COLOR[sp] || "#8b93a7";
}
function hexShort(h, n) {
  if (typeof h !== "string" || !h.length) return "—";
  n = n || 12;
  return h.slice(0, n) + (h.length > n ? "…" : "");
}

/* Per-species ledger rows: landed / in totes / in hold / moves (ops + fish). */
function speciesRows(books) {
  books = books || {};
  var landed = books.landed || {};
  var totes = books.totes || {};
  var hold = books.hold || {};
  var moves = books.moves || [];
  var toteN = {};
  var stat = {};
  Object.keys(totes).forEach(function (name) {
    var t = totes[name] || {};
    if (t.sp && t.n) toteN[t.sp] = (toteN[t.sp] || 0) + t.n;
  });
  moves.forEach(function (m) {
    if (!m || !m.sp) return;
    var s = stat[m.sp] || (stat[m.sp] = { ops: 0, fish: 0 });
    s.ops += 1;
    s.fish += m.n || 0;
  });
  var seen = {};
  var order = [];
  function add(sp) {
    if (sp && !seen[sp]) { seen[sp] = true; order.push(sp); }
  }
  SPECIES.forEach(function (sp) {
    if (landed[sp] || toteN[sp] || hold[sp] || stat[sp]) add(sp);
  });
  Object.keys(landed).forEach(add);
  Object.keys(hold).forEach(add);
  Object.keys(toteN).forEach(add);
  return order.map(function (sp) {
    var s = stat[sp] || { ops: 0, fish: 0 };
    return {
      sp: sp,
      landed: landed[sp] || 0,
      totes: toteN[sp] || 0,
      hold: hold[sp] || 0,
      ops: s.ops,
      moved: s.fish
    };
  });
}

/* The conservation invariant, recomputed live from the books — never trust
 * the export's own "balanced" flag without checking the sums. */
function liveBalance(books) {
  books = books || {};
  var rows = speciesRows(books);
  var landed = 0, totes = 0, hold = 0;
  rows.forEach(function (r) { landed += r.landed; totes += r.totes; hold += r.hold; });
  var declared = (books.conservation || {}).balanced;
  var recomputed = landed === totes + hold;
  return {
    landed: landed, totes: totes, hold: hold,
    gap: landed - totes - hold,
    recomputed: recomputed,
    declared: declared === undefined ? null : declared,
    balanced: declared === true && recomputed
  };
}

/* One hook set -> skate length in fathoms (hooks x spacing). */
function hookLine(hs) {
  var hooks = hs && Number.isFinite(hs.hooks_visible) ? hs.hooks_visible : 0;
  var fathoms = hooks * FATHOMS_PER_HOOK;
  var depth = hs && Number.isFinite(hs.depth_fm) ? hs.depth_fm : null;
  var agree = depth === null ? null : Math.abs(depth - fathoms) < 1e-9;
  return { set: hs ? hs.set : "?", hooks: hooks, fathoms: fathoms, depth: depth, agree: agree };
}

/* ------------------------------------------------------------------ dom -- */

function esc(s) {
  return String(s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}
function $(id) {
  return document.getElementById(id);
}
function chip(sp) {
  return '<span class="chip" style="--spc:' + spColor(sp) + '"><i class="dot"></i>' + esc(sp) + "</span>";
}
function cellChip(name) {
  var cls = name === "HOLD" ? "cellchip hold" : "cellchip";
  return '<span class="' + cls + '">' + esc(name || "—") + "</span>";
}

function renderHeader(exp) {
  var day = exp.day || {};
  var vessel = $("vessel"), meta = $("meta");
  if (!vessel || !meta) return;
  var sub = [];
  if (Number.isFinite(day.sets)) sub.push(fmtInt(day.sets) + " sets");
  if (day.seed !== undefined && day.seed !== null) sub.push("seed " + esc(day.seed));
  vessel.innerHTML =
    '<span class="anchor">⚓</span><span class="vname">' + esc(VESSEL) + "</span>" +
    '<span class="vdate num">' + esc(day.date || "—") + "</span>" +
    (sub.length ? '<span class="vsub">' + sub.join(" · ") + "</span>" : "");
  var backend = day.backend ? String(day.backend).toLowerCase() : "—";
  var conf = day.conformance || {};
  var confHtml;
  if (conf.match === true) {
    confHtml = '<span class="conf match" title="cold-boot QUF matches the export">✓ MATCH</span>';
  } else if (conf.match === false) {
    confHtml = '<span class="conf diverge" title="cold-boot QUF hash: ' +
      esc(hexShort(conf.cold_quf_sha256)) + '">⚠ DIVERGE</span>';
  } else {
    confHtml = '<span class="conf na">n-a</span>';
  }
  meta.innerHTML =
    '<span class="badge backend" title="fabric backend">' + esc(backend) + "</span>" +
    '<button type="button" class="quf num" id="quf" title="' + esc(exp.quf_sha256 || "") +
    ' — click to copy">▤ quf ' + hexShort(exp.quf_sha256) + "</button>" +
    confHtml;
  var q = $("quf");
  if (q) {
    q.addEventListener("click", function () {
      copyText(exp.quf_sha256 || "", q);
    });
  }
}

function copyText(txt, el) {
  function done() {
    var orig = el.getAttribute("data-orig") || el.textContent;
    el.setAttribute("data-orig", orig);
    el.textContent = "copied ✓";
    el.classList.add("flash");
    setTimeout(function () {
      el.textContent = el.getAttribute("data-orig");
      el.classList.remove("flash");
    }, 1200);
  }
  function fallback() {
    try {
      var ta = document.createElement("textarea");
      ta.value = txt;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      done();
    } catch (e) { /* gloves on glass; nothing to do */ }
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done, fallback);
  } else {
    fallback();
  }
}

function renderCons(books) {
  var host = $("cons");
  if (!host) return;
  var b = liveBalance(books);
  var eq = '<span class="num">' + fmtInt(b.landed) + "</span> landed = <span class=\"num\">" +
    fmtInt(b.totes) + "</span> in totes + <span class=\"num\">" + fmtInt(b.hold) + "</span> in hold";
  var cls, verdict, note = "";
  if (b.balanced) {
    cls = "ok";
    verdict = "✓ NO UNBOOKED FISH";
  } else {
    cls = "bad";
    verdict = "⚠ UNBOOKED FISH · gap " + fmtInt(Math.abs(b.gap));
    if (b.declared === true && !b.recomputed) {
      note = '<div class="cons-note">export claims balanced — the sums disagree</div>';
    } else if (b.declared === null) {
      note = '<div class="cons-note">export carries no verdict — live check only</div>';
    }
  }
  host.className = "cons " + cls;
  host.innerHTML =
    '<div class="cons-big">' + verdict + '</div>' +
    '<div class="cons-eq">' + eq + " · brail loss <span class=\"num\">0</span></div>" + note;
}

function renderBooks(books) {
  var tbl = $("books");
  if (!tbl) return;
  var rows = speciesRows(books);
  var html = "<thead><tr><th>species</th><th>landed</th><th>in totes</th><th>in hold</th>" +
    "<th>moves</th></tr></thead><tbody>";
  if (!rows.length) {
    html += '<tr><td colspan="5" class="empty">no fish booked</td></tr>';
  }
  var tl = 0, tt = 0, th = 0, tOps = 0, tMoved = 0;
  rows.forEach(function (r) {
    var bad = r.landed !== r.totes + r.hold;
    tl += r.landed; tt += r.totes; th += r.hold; tOps += r.ops; tMoved += r.moved;
    html += "<tr>" +
      "<td>" + chip(r.sp) + "</td>" +
      '<td class="num' + (bad ? " bad" : "") + '">' + fmtInt(r.landed) + "</td>" +
      '<td class="num' + (bad ? " bad" : "") + '">' + fmtInt(r.totes) + "</td>" +
      '<td class="num' + (bad ? " bad" : "") + '">' + fmtInt(r.hold) + "</td>" +
      '<td class="num">' + fmtInt(r.ops) + " <span class=\"dim\">(" + fmtInt(r.moved) + ")</span></td>" +
      "</tr>";
  });
  if (rows.length) {
    html += '<tr class="total"><td>total</td>' +
      '<td class="num">' + fmtInt(tl) + "</td>" +
      '<td class="num">' + fmtInt(tt) + "</td>" +
      '<td class="num">' + fmtInt(th) + "</td>" +
      '<td class="num">' + fmtInt(tOps) + " <span class=\"dim\">(" + fmtInt(tMoved) + ")</span></td></tr>";
  }
  tbl.innerHTML = html + "</tbody>";
}

function renderDeck(books) {
  var host = $("deck");
  if (!host) return;
  books = books || {};
  var totes = books.totes || {};
  var names = TOTE_ORDER.slice();
  Object.keys(totes).forEach(function (n) { if (names.indexOf(n) < 0) names.push(n); });
  var html = "";
  names.forEach(function (name) {
    var t = totes[name] || {};
    var sp = t.sp || TOTE_SP[name] || "";
    var n = Number.isFinite(t.n) ? t.n : 0;
    var cap = Number.isFinite(t.cap) ? t.cap : 0;
    var pct = cap > 0 ? Math.min(100, Math.round((n / cap) * 100)) : 0;
    html += '<div class="card tote" style="--spc:' + spColor(sp) + '">' +
      '<div class="c-name">' + esc(name) + "</div>" +
      (sp ? '<div class="c-sp"><i class="dot"></i>' + esc(sp) + "</div>" : "") +
      '<div class="c-n num">' + fmtInt(n) + '<span class="cap num">' +
      (cap ? " / " + fmtInt(cap) : "") + "</span></div>" +
      (cap ? '<div class="bar"><div class="fill" style="width:' + pct + '%"></div></div>' +
        '<div class="c-pct num">' + pct + "%</div>" : "") +
      "</div>";
  });
  var hold = books.hold || {};
  var hTotal = 0;
  var hParts = [];
  SPECIES.concat(Object.keys(hold).filter(function (s) { return SPECIES.indexOf(s) < 0; }))
    .forEach(function (sp) {
      var n = hold[sp] || 0;
      if (n) {
        hTotal += n;
        hParts.push('<span class="h-sp"><i class="dot" style="--spc:' + spColor(sp) +
          '"></i>' + esc(sp) + ' <span class="num">' + fmtInt(n) + "</span></span>");
      }
    });
  html += '<div class="card holdcard">' +
    '<div class="c-name">HOLD</div>' +
    '<div class="c-n num">' + fmtInt(hTotal) + "</div>" +
    '<div class="bar"><div class="fill" style="width:100%"></div></div>' +
    (hParts.length ? '<div class="h-list">' + hParts.join("") + "</div>" : "") +
    "</div>";
  host.innerHTML = html;
}

function renderHooks(books) {
  var host = $("hooks");
  if (!host) return;
  var list = ((books || {}).hook_sets || []).slice()
    .sort(function (a, b) { return (a.set || 0) - (b.set || 0); });
  if (!list.length) {
    host.innerHTML = '<li class="empty">no hook sets logged</li>';
    return;
  }
  host.innerHTML = list.map(function (raw) {
    var h = hookLine(raw);
    var depthHtml = h.depth === null ? "" :
      '<span class="hk-depth num' + (h.agree === false ? " warn" : "") + '">depth ' +
      fmtFm(h.depth) + " " + (h.agree === false ? "⚠" : "✓") + "</span>";
    return '<li><span class="hk-set num">S' + esc(h.set) + "</span>" +
      '<span class="num">' + fmtInt(h.hooks) + "</span> hooks × " +
      fmtFm(FATHOMS_PER_HOOK) + " fm = <b class=\"num\">" + fmtFm(h.fathoms) + "</b> fm" +
      depthHtml + "</li>";
  }).join("");
}

function renderMoves(books) {
  var tbl = $("moves");
  if (!tbl) return;
  var moves = ((books || {}).moves || []).slice()
    .sort(function (a, b) { return (a.t || 0) - (b.t || 0); });
  var html = "<thead><tr><th>t</th><th>from</th><th></th><th>to</th><th>n</th><th>species</th></tr></thead><tbody>";
  if (!moves.length) {
    html += '<tr><td colspan="6" class="empty">no moves — nothing brailed</td></tr>';
  }
  moves.forEach(function (m) {
    html += "<tr>" +
      '<td class="num t">' + fmtInt(m.t) + "</td>" +
      "<td>" + cellChip(m.from) + "</td>" +
      '<td class="arr">→</td>' +
      "<td>" + cellChip(m.to) + "</td>" +
      '<td class="num n">' + fmtInt(m.n) + "</td>" +
      "<td>" + chip(m.sp) + "</td></tr>";
  });
  tbl.innerHTML = html + "</tbody>";
}

function renderRefusals(books) {
  var tbl = $("refusals");
  if (!tbl) return;
  var rs = ((books || {}).refusals || []).slice()
    .sort(function (a, b) { return (a.t || 0) - (b.t || 0); });
  var html = "<thead><tr><th>t</th><th>op</th><th>reason</th><th>detail</th></tr></thead><tbody>";
  if (!rs.length) {
    html += '<tr><td colspan="4" class="empty">clean day — no refusals</td></tr>';
  }
  rs.forEach(function (r) {
    html += "<tr>" +
      '<td class="num t">' + fmtInt(r.t) + "</td>" +
      '<td><code>' + esc(r.op || "—") + "</code></td>" +
      '<td><span class="reason">⚠ ' + esc(r.reason || "—") + "</span></td>" +
      '<td class="detail">' + esc(r.detail || "") + "</td></tr>";
  });
  tbl.innerHTML = html + "</tbody>";
}

function renderFires(books) {
  var host = $("fires");
  if (!host) return;
  var fires = ((books || {}).fires || []).slice()
    .sort(function (a, b) { return (a.tick || 0) - (b.tick || 0); });
  if (!fires.length) {
    host.innerHTML = '<li class="empty">no fires — quiet fabric</li>';
    return;
  }
  host.innerHTML = fires.map(function (f) {
    var dat = Number.isFinite(f.dat) ? f.dat : 0;
    var frac = Math.max(0.01, Math.min(1, dat / 0xffff));
    return "<li>" +
      '<span class="f-cell">' + esc(f.cell || "—") + "</span>" +
      '<span class="f-tick num">t' + fmtInt(f.tick) + "</span>" +
      '<svg class="f-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">' +
      '<rect x="0" y="2" width="' + (frac * 100).toFixed(1) + '" height="6"></rect></svg>' +
      '<span class="f-dat num">' + fmtInt(f.dat) + "</span></li>";
  }).join("");
}

function renderFooter(exp) {
  var f = $("ftr");
  if (!f) return;
  f.innerHTML = "quilt-deck day console · export v1 · " +
    (Number.isFinite(exp.quf_bytes) ? fmtInt(exp.quf_bytes) + " bytes · " : "") +
    "day.json " + (location.protocol === "file:" ? "(loaded from disk)" : "(fetched)");
}

function renderAll(exp) {
  if (!exp || typeof exp !== "object") return;
  var books = exp.books || {};
  renderHeader(exp);
  renderCons(books);
  renderBooks(books);
  renderDeck(books);
  renderHooks(books);
  renderMoves(books);
  renderRefusals(books);
  renderFires(books);
  renderFooter(exp);
}

/* ------------------------------------------------------------ offline io -- */

function showPicker(msg) {
  var p = $("picker");
  if (!p) return;
  if (msg) {
    var m = $("pickmsg");
    if (m) m.textContent = msg;
  }
  p.classList.remove("hidden");
}
function hidePicker() {
  var p = $("picker");
  if (p) p.classList.add("hidden");
}
function bindPicker() {
  var btn = $("loadbtn");
  if (btn) btn.addEventListener("click", function () { showPicker("pick a day-export JSON"); });
  var inp = $("filein");
  if (inp) {
    inp.addEventListener("change", function () {
      var f = inp.files && inp.files[0];
      if (!f) return;
      var err = $("pickerr");
      if (err) err.hidden = true;
      var rd = new FileReader();
      rd.onload = function () {
        try {
          renderAll(JSON.parse(String(rd.result)));
          hidePicker();
        } catch (e) {
          if (err) { err.textContent = "not a day export: " + e.message; err.hidden = false; }
        }
      };
      rd.onerror = function () {
        if (err) { err.textContent = "could not read that file"; err.hidden = false; }
      };
      rd.readAsText(f);
    });
  }
}

function boot() {
  bindPicker();
  if (location.protocol === "file:") {
    /* fetch() on file:// only produces CORS noise — go straight to the picker. */
    showPicker("opened from disk — fetch is blocked, so load the day JSON:");
    return;
  }
  fetch("day.json", { cache: "no-store" })
    .then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(renderAll)
    .catch(function () {
      showPicker("day.json not fetchable — pick the day export from disk:");
    });
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    SPECIES: SPECIES,
    SP_COLOR: SP_COLOR,
    TOTE_ORDER: TOTE_ORDER,
    TOTE_SP: TOTE_SP,
    FATHOMS_PER_HOOK: FATHOMS_PER_HOOK,
    VESSEL: VESSEL,
    fmtInt: fmtInt,
    fmtFm: fmtFm,
    spColor: spColor,
    hexShort: hexShort,
    speciesRows: speciesRows,
    liveBalance: liveBalance,
    hookLine: hookLine
  };
}
