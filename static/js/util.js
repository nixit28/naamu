/* Namu — small DOM, formatting, date and tooltip helpers. No dependencies. */
(function () {
  'use strict';
  const N = (window.Namu = window.Namu || {});
  const SVGNS = 'http://www.w3.org/2000/svg';

  // ---------------------------------------------------------------- DOM
  // All text goes through textContent / createTextNode: merchant names and file
  // names come from PDFs and must never be parsed as HTML.
  function setAttrs(el, attrs) {
    if (!attrs) return;
    for (const k in attrs) {
      const v = attrs[k];
      if (v == null || v === false) continue;
      if (k === 'text') el.textContent = v;
      else if (k === 'tip') el._tip = v;
      else if (k === 'style' && typeof v === 'object') {
        for (const p in v) {
          if (p.startsWith('--')) el.style.setProperty(p, v[p]);
          else el.style[p] = v[p];
        }
      } else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, String(v));
    }
  }
  function append(el, kids) {
    for (const k of kids) {
      if (k == null || k === false || k === '') continue;
      if (Array.isArray(k)) append(el, k);
      else el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    }
  }
  N.h = function (tag, attrs, ...kids) {
    const el = document.createElement(tag);
    setAttrs(el, attrs);
    append(el, kids);
    return el;
  };
  N.s = function (tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    setAttrs(el, attrs);
    append(el, kids);
    return el;
  };
  N.clear = function (el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    return el;
  };
  N.$ = (sel, root) => (root || document).querySelector(sel);

  // ---------------------------------------------------------------- money
  const MINUS = '−';
  N.MINUS = MINUS;
  const usd2 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
  const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0, minimumFractionDigits: 0 });
  /** $1,234.56 — `sign` adds a + for positives; negatives always get a true minus. */
  N.money = function (v, opts) {
    const o = opts || {};
    const f = o.cents === false ? usd0 : usd2;
    const tiny = o.cents === false ? 0.5 : 0.005;
    if (!isFinite(v) || Math.abs(v) < tiny) return f.format(0);
    const a = f.format(Math.abs(v));
    if (v < 0) return MINUS + a;
    return (o.sign ? '+' : '') + a;
  };
  function trimNum(x) {
    const r = x < 10 ? Math.round(x * 10) / 10 : Math.round(x);
    return String(r);
  }
  /** Compact for axes and tight labels: $950, $1.2K, $12K, $1.4M. */
  N.compact = function (v, opts) {
    const o = opts || {};
    const a = Math.abs(v);
    let s;
    if (a >= 1e6) s = '$' + trimNum(a / 1e6) + 'M';
    else if (a >= 1e3) s = '$' + trimNum(a / 1e3) + 'K';
    else s = '$' + Math.round(a);
    if (v < 0 && Math.round(a) !== 0) return MINUS + s;
    return (o.sign && v > 0 ? '+' : '') + s;
  };
  N.pct = function (v, digits) {
    if (!isFinite(v)) return '—';
    const s = (Math.abs(v) * 100).toFixed(digits == null ? 0 : digits) + '%';
    return v < 0 && Math.abs(v) >= 0.005 ? MINUS + s : s;
  };
  N.plural = (n, one, many) => n + ' ' + (n === 1 ? one : many || one + 's');
  N.num = (n) => new Intl.NumberFormat('en-US').format(n);

  // ---------------------------------------------------------------- dates
  // Dates are ISO strings (YYYY-MM-DD). Parse as *local* dates to avoid TZ shifts.
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  N.MON = MON;
  N.DOW = DOW;
  const pad = (n) => (n < 10 ? '0' : '') + n;
  N.parseDate = function (s) {
    const p = String(s).slice(0, 10).split('-').map(Number);
    return new Date(p[0], p[1] - 1, p[2]);
  };
  N.iso = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  N.addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  N.dayDiff = (a, b) => Math.round((b - a) / 86400000);
  N.monthKey = (s) => String(s).slice(0, 7);
  N.monthStart = (key) => N.parseDate(key + '-01');
  N.monthEnd = (key) => {
    const d = N.monthStart(key);
    return new Date(d.getFullYear(), d.getMonth() + 1, 0);
  };
  N.shiftMonth = function (key, n) {
    const d = N.monthStart(key);
    const x = new Date(d.getFullYear(), d.getMonth() + n, 1);
    return x.getFullYear() + '-' + pad(x.getMonth() + 1);
  };
  N.monthLabel = function (key, opts) {
    const o = opts || {};
    const y = key.slice(0, 4);
    const m = Number(key.slice(5, 7)) - 1;
    const name = o.long ? MONTH[m] : MON[m];
    return o.year ? name + ' ' + y : name;
  };
  N.fmtDate = function (s, opts) {
    const o = opts || {};
    const d = typeof s === 'string' ? N.parseDate(s) : s;
    let out = MON[d.getMonth()] + ' ' + d.getDate();
    if (o.dow) out = DOW[d.getDay()] + ', ' + out;
    if (o.year || (o.year !== false && d.getFullYear() !== N.today().getFullYear())) out += ', ' + d.getFullYear();
    return out;
  };
  N.fmtRange = function (a, b) {
    const da = N.parseDate(a), db = N.parseDate(b);
    const sameYear = da.getFullYear() === db.getFullYear();
    return N.fmtDate(da, { year: !sameYear }) + ' – ' + N.fmtDate(db, { year: true });
  };
  // "Today" — pinned in mock mode so the demo's nudges stay stable.
  let todayOverride = null;
  N.setToday = (d) => { todayOverride = d; };
  N.today = function () {
    const d = todayOverride || new Date();
    return new Date(d.getFullYear(), d.getMonth(), d.getDate());
  };

  // ---------------------------------------------------------------- names
  N.personName = function (raw) {
    if (!raw) return null;
    return String(raw).toLowerCase().split(/\s+/).filter((w) => w.length > 1)
      .map((w, i) => (i > 0 && (w === 'or' || w === 'and')) ? w : w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
  };
  N.shortAccount = function (a) {
    if (!a) return 'Unknown account';
    let p = String(a.product || a.institution || 'Account')
      .replace(/\b(Unlimited|Everyday|Anywhere|Total|Card|Preferred)\b/gi, '')
      .replace(/^\s*Amazon\s+/i, '').replace(/\s+/g, ' ').trim();
    if (!p) p = a.institution || 'Account';
    return p + (a.last4 ? ' ••' + a.last4 : '');
  };

  // ---------------------------------------------------------------- math
  N.sum = (arr, f) => arr.reduce((acc, x) => acc + (f ? f(x) : x), 0);
  N.median = function (arr) {
    if (!arr.length) return 0;
    const a = arr.slice().sort((x, y) => x - y);
    const m = a.length >> 1;
    return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
  };
  N.niceTicks = function (min, max, count) {
    const n = count || 4;
    if (min === max) { max = min + 1; }
    const raw = (max - min) / n;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    const lo = Math.floor(min / step - 1e-9) * step;
    const hi = Math.ceil(max / step - 1e-9) * step;
    const ticks = [];
    for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Math.round(v * 100) / 100);
    return { ticks, lo, hi, step };
  };

  // Text measurement for SVG labels (system font, matches CSS).
  const ctx = document.createElement('canvas').getContext('2d');
  const FONT = getComputedStyle(document.documentElement).getPropertyValue('--font') || 'system-ui, sans-serif';
  N.textWidth = function (text, size, weight) {
    ctx.font = (weight || 400) + ' ' + (size || 12) + 'px ' + FONT;
    return ctx.measureText(text).width;
  };
  N.fitText = function (text, maxW, size, weight) {
    if (N.textWidth(text, size, weight) <= maxW) return text;
    let t = text;
    while (t.length > 1 && N.textWidth(t + '…', size, weight) > maxW) t = t.slice(0, -1);
    return t.trimEnd() + '…';
  };

  // ---------------------------------------------------------------- icons
  const ICONS = {
    check: 'M4.5 10.5l3.5 3.5 7.5-8',
    info: 'M10 9v5M10 6.2v.1M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16z',
    alert: 'M10 7.5v4M10 14.3v.1M8.6 3.3 2.2 14.6A1.6 1.6 0 0 0 3.6 17h12.8a1.6 1.6 0 0 0 1.4-2.4L11.4 3.3a1.6 1.6 0 0 0-2.8 0z',
    x: 'M5.5 5.5l9 9m0-9-9 9',
    dash: 'M5 10h10',
    up: 'M10 15V5m0 0-4 4m4-4 4 4',
    down: 'M10 5v10m0 0-4-4m4 4 4-4',
    link: 'M3 7.5h11.5m0 0-3-3m3 3-3 3M17 12.5H5.5m0 0 3-3m-3 3 3 3',
    file: 'M11.5 2.5H6A1.5 1.5 0 0 0 4.5 4v12A1.5 1.5 0 0 0 6 17.5h8a1.5 1.5 0 0 0 1.5-1.5V6.5l-4-4zM11.5 2.5v4h4',
    copy: 'M7 7V4.5A1.5 1.5 0 0 1 8.5 3h7A1.5 1.5 0 0 1 17 4.5v7a1.5 1.5 0 0 1-1.5 1.5H13M4.5 7h7A1.5 1.5 0 0 1 13 8.5v7a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 3 15.5v-7A1.5 1.5 0 0 1 4.5 7z',
    chevron: 'M7.5 5l5 5-5 5',
    clock: 'M10 5.5V10l3 2M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16z',
    upload: 'M10 13V3m0 0L6 7m4-4 4 4M3 13v2.5A1.5 1.5 0 0 0 4.5 17h11a1.5 1.5 0 0 0 1.5-1.5V13',
    trash: 'M3.5 5.5h13M8 5.5V4h4v1.5M5.5 5.5l.8 10.6A1.5 1.5 0 0 0 7.8 17.5h4.4a1.5 1.5 0 0 0 1.5-1.4l.8-10.6',
    sparkle: 'M10 3v3M10 14v3M3 10h3M14 10h3',
  };
  N.icon = function (name, cls) {
    return N.s('svg', { class: 'icon' + (cls ? ' ' + cls : ''), viewBox: '0 0 20 20', 'aria-hidden': 'true', focusable: 'false' },
      N.s('path', { d: ICONS[name] || ICONS.info }));
  };

  // ---------------------------------------------------------------- tooltip
  // Any element (HTML or SVG) with an `_tip` payload gets a tooltip on hover and
  // on keyboard focus. Payload: { title, rows: [{ value, label, color }], note }.
  // Tooltips enhance; every value is also in a table view or a direct label.
  let tipEl = null;
  let hotEl = null;
  function findTip(t) {
    while (t && t !== document && t.nodeType === 1) {
      if (t._tip) return t;
      t = t.parentNode;
    }
    return null;
  }
  function renderTip(p) {
    const tip = typeof p === 'function' ? p() : p;
    N.clear(tipEl);
    if (tip.title) tipEl.appendChild(N.h('p', { class: 'tooltip__title', text: tip.title }));
    for (const r of tip.rows || []) {
      tipEl.appendChild(N.h('p', { class: 'tooltip__row' },
        r.color ? N.h('span', { class: 'tooltip__key' + (r.dot ? ' tooltip__key--dot' : ''), style: { background: r.color } }) : null,
        N.h('strong', { class: 'tooltip__value', text: r.value }),
        r.label ? N.h('span', { class: 'tooltip__label', text: r.label }) : null));
    }
    if (tip.note) tipEl.appendChild(N.h('p', { class: 'tooltip__note', text: tip.note }));
  }
  function place(x, y, anchorRect) {
    tipEl.hidden = false;
    const w = tipEl.offsetWidth, hgt = tipEl.offsetHeight;
    const vw = document.documentElement.clientWidth, vh = window.innerHeight;
    let left, top;
    if (anchorRect) {
      left = anchorRect.left + anchorRect.width / 2 - w / 2;
      top = anchorRect.top - hgt - 10;
      if (top < 8) top = anchorRect.bottom + 10;
    } else {
      left = x + 14;
      top = y + 16;
      if (left + w > vw - 8) left = x - w - 14;
      if (top + hgt > vh - 8) top = y - hgt - 14;
    }
    left = Math.max(8, Math.min(left, vw - w - 8));
    top = Math.max(8, Math.min(top, vh - hgt - 8));
    tipEl.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(top) + 'px)';
  }
  function setHot(el) {
    if (hotEl === el) return;
    if (hotEl) hotEl.classList.remove('is-hot');
    hotEl = el;
    if (el) el.classList.add('is-hot');
  }
  function hide() {
    if (tipEl) tipEl.hidden = true;
    setHot(null);
  }
  N.hideTip = hide;
  N.initTooltips = function () {
    tipEl = document.getElementById('tooltip');
    document.addEventListener('pointermove', (e) => {
      const t = findTip(e.target);
      if (!t) { if (hotEl && !hotEl.contains(document.activeElement)) hide(); return; }
      if (t !== hotEl) renderTip(t._tip);
      setHot(t);
      place(e.clientX, e.clientY);
    }, { passive: true });
    document.addEventListener('pointerleave', hide);
    document.addEventListener('focusin', (e) => {
      const t = findTip(e.target);
      if (!t || !e.target.matches(':focus-visible')) { hide(); return; }
      renderTip(t._tip);
      setHot(t);
      place(0, 0, t.getBoundingClientRect());
    });
    document.addEventListener('focusout', hide);
    window.addEventListener('scroll', hide, { passive: true });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hide(); });
  };

  // ---------------------------------------------------------------- misc
  N.debounce = function (fn, ms) {
    let t = 0;
    return function () {
      clearTimeout(t);
      const args = arguments;
      t = setTimeout(() => fn.apply(null, args), ms);
    };
  };
  N.storage = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
  };
  N.cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
})();
