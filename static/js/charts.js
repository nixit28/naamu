/* Naamu — hand-built inline SVG charts. Colors come from CSS classes so light and
   dark are selected in styles.css (validated palettes; see the header there). */
(function () {
  'use strict';
  const N = window.Naamu;
  const { h, s } = N;
  const C = (N.charts = {});

  // ------------------------------------------------------------ shared pieces
  // Interactive charts are groups (their marks are buttons); static ones are images.
  function svgRoot(W, H, label, interactive) {
    return s('svg', { class: 'viz', width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, role: interactive ? 'group' : 'img', 'aria-label': label });
  }
  /** Column with a 4px rounded data-end, square at the baseline. dir: 'up' | 'down'. */
  function colPath(x, yTop, yBot, w, dir) {
    const hgt = yBot - yTop;
    if (hgt <= 0.3) return '';
    const r = Math.min(4, hgt, w / 2);
    if (dir === 'up') {
      return 'M' + x + ',' + yBot + 'V' + (yTop + r) + 'A' + r + ',' + r + ' 0 0 1 ' + (x + r) + ',' + yTop +
        'H' + (x + w - r) + 'A' + r + ',' + r + ' 0 0 1 ' + (x + w) + ',' + (yTop + r) + 'V' + yBot + 'Z';
    }
    return 'M' + x + ',' + yTop + 'V' + (yBot - r) + 'A' + r + ',' + r + ' 0 0 0 ' + (x + r) + ',' + yBot +
      'H' + (x + w - r) + 'A' + r + ',' + r + ' 0 0 0 ' + (x + w) + ',' + (yBot - r) + 'V' + yTop + 'Z';
  }
  /** Horizontal bar from x0 growing right, rounded at the data end. */
  function barPath(x0, x1, y, hgt) {
    const w = x1 - x0;
    if (w <= 0.3) return '';
    const r = Math.min(4, w, hgt / 2);
    return 'M' + x0 + ',' + y + 'H' + (x1 - r) + 'A' + r + ',' + r + ' 0 0 1 ' + x1 + ',' + (y + r) +
      'V' + (y + hgt - r) + 'A' + r + ',' + r + ' 0 0 1 ' + (x1 - r) + ',' + (y + hgt) + 'H' + x0 + 'Z';
  }
  C.barPath = barPath;

  C.legend = function (items) {
    return h('ul', { class: 'legend' }, items.map((it) =>
      h('li', null, h('span', { class: 'legend__key legend__key--' + (it.shape || 'rect') + ' ' + it.cls, 'aria-hidden': 'true' }), it.label)));
  };

  /** Accessible table twin of a chart. cols: [{label, num}], rows: [[...cells]] */
  C.table = function (caption, cols, rows) {
    return h('div', { class: 'table-view' }, h('table', { class: 'data-table' },
      h('caption', { class: 'sr-only', text: caption }),
      h('thead', null, h('tr', null, cols.map((c) => h('th', { scope: 'col', class: c.num ? 'num' : null, text: c.label })))),
      h('tbody', null, rows.map((r) => h('tr', null, r.map((cell, i) =>
        i === 0 ? h('th', { scope: 'row', text: cell }) : h('td', { class: cols[i].num ? 'num' : null, text: cell })))))));
  };

  function activate(el, fn) {
    el.addEventListener('click', fn);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(e); }
    });
  }

  // ------------------------------------------------------------ cash flow
  let patternSeq = 0;
  /** 45° hatch (from savings) and a faint 135° hatch (partial-month background). */
  function hatchDefs(svg) {
    const id = 'p' + (++patternSeq);
    const defs = s('defs');
    const save = s('pattern', { id: id + 's', width: 6, height: 6, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(45)' });
    save.appendChild(s('rect', { class: 'hatch-bg', width: 6, height: 6 }));
    save.appendChild(s('rect', { class: 'hatch-line', width: 2.2, height: 6 }));
    const part = s('pattern', { id: id + 'p', width: 7, height: 7, patternUnits: 'userSpaceOnUse', patternTransform: 'rotate(135)' });
    part.appendChild(s('rect', { class: 'partial-line', width: 1, height: 7 }));
    defs.appendChild(save);
    defs.appendChild(part);
    svg.appendChild(defs);
    return { save: 'url(#' + id + 's)', partial: 'url(#' + id + 'p)' };
  }
  C.hatchSwatch = function () {
    const svg = s('svg', { class: 'legend__swatch', width: 10, height: 10, viewBox: '0 0 10 10', 'aria-hidden': 'true' });
    const p = hatchDefs(svg);
    svg.appendChild(s('rect', { width: 10, height: 10, rx: 2.5, fill: p.save }));
    return svg;
  };

  /** rows: [{key, in, out, putIn, takenOut, left, sources, dests, partial, gapText, selected}]
      One signed axis, never netted. Above the line (sources): money in, then money taken
      out of savings (hatched). Below (uses): spent, then money put into savings (solid).
      The dot is what was left in checking; its label sits beside the dot. */
  C.cashflow = function (el, rows, opts) {
    const o = opts || {};
    const W = Math.max(280, el.clientWidth);
    const narrow = W < 520;
    const H = narrow ? 260 : 340;
    const years = new Set(rows.map((r) => r.key.slice(0, 4)));
    const m = { l: 52, r: 8, t: 22, b: years.size > 1 ? 46 : 32 };
    const up = (r) => r.in + r.takenOut;
    const down = (r) => r.out + r.putIn;
    const nt = N.niceTicks(-Math.max(1, ...rows.map(down)), Math.max(1, ...rows.map(up)), narrow ? 4 : 5);
    const plotH = H - m.t - m.b;
    const y = (v) => m.t + ((nt.hi - v) / (nt.hi - nt.lo)) * plotH;
    const n = rows.length;
    const plotW = W - m.l - m.r;
    const band = Math.min(plotW / n, 140);
    const x0 = m.l + (plotW - band * n) / 2;
    const barW = Math.min(24, Math.max(8, band * 0.3));
    const svg = svgRoot(W, H, 'Monthly money in, money taken out of savings, spending, money put into savings, and what was left in checking, for ' + n + ' months. Table view available.', true);
    const pat = hatchDefs(svg);

    // partial-month backgrounds sit under the grid
    rows.forEach((r, i) => {
      if (r.partial) svg.appendChild(s('rect', { class: 'cf-partial', x: x0 + band * i + 3, y: m.t, width: band - 6, height: plotH, fill: pat.partial }));
    });
    const grid = s('g', { class: 'axis' });
    for (const t of nt.ticks) {
      const yy = Math.round(y(t)) + 0.5;
      grid.appendChild(s('line', { class: t === 0 ? 'baseline' : 'gridline', x1: m.l, x2: W - m.r, y1: yy, y2: yy }));
      grid.appendChild(s('text', { class: 'tick', x: m.l - 8, y: yy + 4, 'text-anchor': 'end', text: N.compact(t) }));
    }
    svg.appendChild(grid);

    const listRows = (map, color, max) => Array.from(map.entries()).sort((a, b) => b[1] - a[1]).slice(0, max || 4)
      .map(([k, v]) => ({ value: N.money(v), label: k, color }));
    const fs = narrow ? 11 : 12;
    const room = band - barW - 14;   // up to the next month's bar
    const showAll = n <= (narrow ? 6 : 9);
    const maxLeft = rows.reduce((a, r) => (r.left > a.left ? r : a), rows[0]);
    const minLeft = rows.reduce((a, r) => (r.left < a.left ? r : a), rows[0]);
    rows.forEach((r, i) => {
      const cx = x0 + band * (i + 0.5);
      const bx = cx - barW / 2;
      const z = y(0);
      const title = N.monthLabel(r.key, { long: true, year: true });
      const g = s('g', { class: 'cf-month' + (r.selected ? '' : ' is-dim') + (r.partial ? ' is-partial' : ''), role: 'button', tabindex: '0',
        'aria-label': title + ': money in ' + N.money(r.in) + ', from savings ' + N.money(r.takenOut) + ', spent ' + N.money(r.out) +
          ', into savings ' + N.money(r.putIn) + ', left in checking ' + N.money(r.left, { sign: true }) +
          (r.partial ? '. Partial: ' + r.gapText : '') + '. Select to focus on this month.' });
      const hit = s('rect', { class: 'hit', x: x0 + band * i + 2, y: m.t - 16, width: band - 4, height: plotH + 16 + m.b - 4, rx: 6 });
      g.appendChild(hit);
      g._tip = {
        title: title + (r.partial ? ' · partial' : ''),
        rows: [
          { value: N.money(r.in), label: 'Money in', color: 'var(--in)' },
          { value: N.money(r.takenOut), label: 'Taken out of savings', color: 'var(--save-tint)' },
          { value: N.money(r.out), label: 'Spent', color: 'var(--out)' },
          { value: N.money(r.putIn), label: 'Put into savings', color: 'var(--save)' },
          { value: N.money(r.left, { sign: true }), label: 'Left in checking', color: 'var(--ink)', dot: true },
        ],
        note: r.partial ? r.gapText : null,
      };
      // one segment = one hover target with its own exact figures
      const segment = (cls, yTop, yBot, rounded, dir, tip, fill) => {
        if (yBot - yTop <= 0.3) return;
        const el2 = rounded ? s('path', { class: 'cf-seg ' + cls, d: colPath(bx, yTop, yBot, barW, dir) })
          : s('rect', { class: 'cf-seg ' + cls, x: bx, y: yTop, width: barW, height: yBot - yTop });
        if (fill) el2.setAttribute('fill', fill);
        el2._tip = tip;
        g.appendChild(el2);
      };
      const tipFor = (label, v, color, extra, note) => ({ title: title + ' · ' + label, rows: [{ value: N.money(v), label, color }].concat(extra || []), note: note || (r.partial ? r.gapText : null) });
      // sources, above the line
      const hasTaken = r.takenOut > 0.5;
      segment('m-in', y(r.in), z - 1, !hasTaken, 'up',
        tipFor('Money in', r.in, 'var(--in)', listRows(r.sources, null, 3)));
      if (hasTaken) segment('m-save-from', y(r.in + r.takenOut), y(r.in) - (r.in > 0.5 ? 2 : 1), true, 'up',
        tipFor('Taken out of savings', r.takenOut, 'var(--save-tint)', listRows(r.takenBy, null, 4), 'Savings coming back. Not income, and not netted against what you put in.'), pat.save);
      // uses, below the line
      const hasPut = r.putIn > 0.5;
      segment('m-out', z + 1, y(-r.out), !hasPut, 'down', tipFor('Spent', r.out, 'var(--out)', [{ value: N.plural(r.count, 'purchase'), label: 'net of refunds' }]));
      if (hasPut) segment('m-save', y(-r.out) + (r.out > 0.5 ? 2 : 1), y(-r.out - r.putIn), true, 'down',
        tipFor('Put into savings', r.putIn, 'var(--save)', listRows(r.putBy, null, 4), 'Counts as saved.'));
      // left in checking: marker + label beside it
      const yl = y(r.left);
      g.appendChild(s('line', { class: 'm-net-line', x1: bx - 5, x2: bx + barW + 5, y1: yl, y2: yl }));
      const dot = s('circle', { class: 'm-net-dot', cx, cy: yl, r: 4.5 });
      dot._tip = tipFor('Left in checking', r.left, 'var(--ink)', [], 'Money in + from savings − spent − into savings. Cash, not savings.');
      dot._tip.rows[0].value = N.money(r.left, { sign: true });
      dot._tip.rows[0].dot = true;
      g.appendChild(dot);
      if (showAll || r === maxLeft || r === minLeft || r.selected && rows.filter((q) => q.selected).length === 1) {
        const full = N.money(r.left, { cents: false, sign: true });
        const short = N.compact(r.left, { sign: true });
        const label = N.textWidth(full, fs, 600) <= room ? full : short;
        g.appendChild(s('text', { class: 'value-label value-label--halo', x: bx + barW + 8, y: yl + 4, 'font-size': fs, text: label }));
      }
      const first = i === 0 || r.key.slice(5) === '01';
      const xl = s('text', { class: 'tick tick--x' + (r.partial ? ' tick--partial' : ''), x: cx, y: H - m.b + 18, 'text-anchor': 'middle' },
        N.monthLabel(r.key, { year: years.size === 1 && i === 0 && band >= 64 }));
      if (r.partial) xl.appendChild(s('tspan', { class: 'tick--mark', text: '*' }));
      g.appendChild(xl);
      if (years.size > 1 && first) g.appendChild(s('text', { class: 'tick tick--note', x: cx, y: H - m.b + 32, 'text-anchor': 'middle', text: r.key.slice(0, 4) }));
      if (o.onSelect) activate(g, () => o.onSelect(r.key));
      svg.appendChild(g);
    });
    N.clear(el).appendChild(svg);
  };

  // ------------------------------------------------------------ sankey
  /** model from N.model.sankey; opts.onCategory(categories[], label) */
  C.sankey = function (el, model, opts) {
    const o = opts || {};
    const W = Math.max(300, el.clientWidth);
    const narrow = W < 640;
    const nodeW = 10;
    const labL = narrow ? 92 : 190;
    const labR = narrow ? 112 : 210;
    const gap = narrow ? 6 : 10;
    const slot = narrow ? 30 : 36;  // min vertical room per right-hand label
    const fs = narrow ? 11 : 12.5;
    const total = model.total;
    const baseH = narrow ? 260 : 340;
    const k = baseH / total;
    const colH = (nodes) => N.sum(nodes, (nd) => Math.max(nd.value * k, slot)) + gap * (nodes.length - 1);
    const H = Math.ceil(Math.max(baseH, colH(model.targets), colH(model.sources))) + 34;
    const top = 30;
    const xL = labL, xHub = Math.round(W / 2 - nodeW / 2), xR = W - labR - nodeW;
    const svg = svgRoot(W, H, 'Flow of money from ' + model.sources.length + ' sources to ' + model.targets.length + ' destinations. Table view available.', true);
    const links = s('g', { class: 'sk-links' });
    const nodes = s('g', { class: 'sk-nodes' });
    svg.appendChild(links);
    svg.appendChild(nodes);

    // hub: total, centered
    const hubH = total * k;
    const hubY = top + (H - top - 4 - hubH) / 2;
    nodes.appendChild(s('rect', { class: 'sk-hub', x: xHub, y: hubY, width: nodeW, height: hubH, rx: 2 }));
    nodes.appendChild(s('text', { class: 'sk-hub-label', x: xHub + nodeW / 2, y: hubY - 18, 'text-anchor': 'middle', text: 'Moved through' }));
    nodes.appendChild(s('text', { class: 'sk-hub-value', x: xHub + nodeW / 2, y: hubY - 4, 'text-anchor': 'middle', text: N.money(total, { cents: false }) }));

    function layout(list) {
      const hgt = colH(list);
      let yy = top + (H - top - 4 - hgt) / 2;
      return list.map((nd) => {
        const hh = nd.value * k;
        const slotH = Math.max(hh, slot);
        const r = { nd, y: yy + (slotH - hh) / 2, h: hh, cy: yy + slotH / 2 };
        yy += slotH + gap;
        return r;
      });
    }
    function band(xa, ya, xb, yb, hh) {
      const xm = (xa + xb) / 2;
      return 'M' + xa + ',' + ya + 'C' + xm + ',' + ya + ' ' + xm + ',' + yb + ' ' + xb + ',' + yb +
        'V' + (yb + hh) + 'C' + xm + ',' + (yb + hh) + ' ' + xm + ',' + (ya + hh) + ' ' + xa + ',' + (ya + hh) + 'Z';
    }
    const cls = { in: 'in', saving: 'save', out: 'out', cash: 'cash' };
    const color = { in: 'var(--in)', saving: 'var(--save)', out: 'var(--out)', cash: 'var(--cash)' };
    const share = (v) => (total > 0 ? N.pct(v / total) : '');

    // left
    let hubCursor = hubY;
    layout(model.sources).forEach((p) => {
      const nd = p.nd;
      const lk = s('path', { class: 'sk-link sk-link--' + cls[nd.kind], d: band(xL + nodeW, p.y, xHub, hubCursor, p.h) });
      hubCursor += p.h;
      const tip = {
        title: nd.label,
        rows: [{ value: N.money(nd.value), label: share(nd.value) + ' of the total', color: color[nd.kind] }],
        note: nd.kind === 'cash' ? 'More went out than came in, so the checking balance went down by this much.'
          : nd.kind === 'saving' ? 'Money taken back out of ' + nd.dest + '. It is not income; it lowers what you saved.'
            : (nd.members ? nd.members.join(', ') : 'Income'),
      };
      lk._tip = tip;
      links.appendChild(lk);
      const lClick = nd.kind === 'saving' && o.onDest;
      const g = s('g', { class: 'sk-node' + (lClick ? ' is-clickable' : ''), tabindex: '0', role: lClick ? 'button' : 'img',
        'aria-label': nd.label + ': ' + N.money(nd.value) + (lClick ? '. Select to list these transactions.' : '') });
      if (lClick) activate(g, () => o.onDest(nd.dest));
      g.appendChild(s('rect', { class: 'hit', x: 0, y: p.cy - slot / 2, width: xL + nodeW + 4, height: slot }));
      g.appendChild(s('rect', { class: 'sk-bar sk-bar--' + cls[nd.kind], x: xL, y: p.y, width: nodeW, height: Math.max(p.h, 1.5), rx: 2 }));
      g.appendChild(s('text', { class: 'sk-label', x: xL - 8, y: p.cy - 2, 'text-anchor': 'end', text: N.fitText(nd.label, labL - 12, fs, 500) }));
      g.appendChild(s('text', { class: 'sk-value', x: xL - 8, y: p.cy + fs + 1, 'text-anchor': 'end', text: N.money(nd.value, { cents: false }) }));
      g._tip = tip;
      nodes.appendChild(g);
    });

    // right
    hubCursor = hubY;
    layout(model.targets).forEach((p) => {
      const nd = p.nd;
      const lk = s('path', { class: 'sk-link sk-link--' + cls[nd.kind], d: band(xHub + nodeW, hubCursor, xR, p.y, p.h) });
      hubCursor += p.h;
      const tip = {
        title: nd.label,
        rows: [{ value: N.money(nd.value), label: share(nd.value) + ' of the total', color: color[nd.kind] }],
        note: nd.kind === 'saving' ? 'Put into ' + nd.dest + '. Counts as saved.'
          : nd.kind === 'cash' ? 'Stayed in checking as cash. Not counted as savings.'
            : (nd.categories && nd.categories.length > 1 ? nd.categories.join(', ') : 'Select to list these transactions.'),
      };
      lk._tip = tip;
      links.appendChild(lk);
      const clickable = (nd.kind === 'out' && o.onCategory) || (nd.kind === 'saving' && o.onDest);
      const g = s('g', { class: 'sk-node' + (clickable ? ' is-clickable' : ''), tabindex: '0', role: clickable ? 'button' : 'img',
        'aria-label': nd.label + ': ' + N.money(nd.value) + (clickable ? '. Select to filter transactions.' : '') });
      g.appendChild(s('rect', { class: 'hit', x: xR - 4, y: p.cy - slot / 2, width: W - xR + 4, height: slot }));
      g.appendChild(s('rect', { class: 'sk-bar sk-bar--' + cls[nd.kind], x: xR, y: p.y, width: nodeW, height: Math.max(p.h, 1.5), rx: 2 }));
      const tx = xR + nodeW + 8;
      g.appendChild(s('text', { class: 'sk-label' + (nd.kind === 'saving' ? ' sk-label--strong' : ''), x: tx, y: p.cy - 2, text: N.fitText(nd.label, labR - 14, fs, 500) }));
      g.appendChild(s('text', { class: 'sk-value', x: tx, y: p.cy + fs + 1, text: N.money(nd.value, { cents: false }) }));
      g._tip = tip;
      if (clickable) activate(g, () => (nd.kind === 'saving' ? o.onDest(nd.dest) : o.onCategory(nd.categories, nd.label)));
      nodes.appendChild(g);
    });
    // hover a node → emphasize its link
    svg.addEventListener('pointerover', (e) => {
      const g = e.target.closest && e.target.closest('.sk-node');
      svg.classList.toggle('has-focus', !!g);
      svg.querySelectorAll('.sk-link.is-lit').forEach((x) => x.classList.remove('is-lit'));
      if (g) {
        const idx = Array.prototype.indexOf.call(nodes.querySelectorAll('.sk-node'), g);
        const lk = links.children[idx];
        if (lk) lk.classList.add('is-lit');
      }
    });
    svg.addEventListener('pointerleave', () => {
      svg.classList.remove('has-focus');
      svg.querySelectorAll('.sk-link.is-lit').forEach((x) => x.classList.remove('is-lit'));
    });
    N.clear(el).appendChild(svg);
  };

  // ------------------------------------------------------------ category bars
  /** rows: [{name, spend, share, count}], opts: { selected:Set, onSelect(name), prev: Map } */
  C.categoryBars = function (el, rows, opts) {
    const o = opts || {};
    const W = Math.max(260, el.clientWidth);
    const narrow = W < 460;
    const rowH = 32, barH = 12;
    const labW = narrow ? 108 : 132;
    const valW = narrow ? 92 : 112;
    const H = rows.length * rowH + 4;
    const max = Math.max(...rows.map((r) => r.spend), 1);
    const x0 = labW + 8, xMax = W - valW;
    const svg = svgRoot(W, H, 'Spending by category, ' + rows.length + ' categories, largest first. Table view available.', true);
    const anySel = o.selected && o.selected.size > 0;
    rows.forEach((r, i) => {
      const yy = i * rowH;
      const sel = anySel && o.selected.has(r.name);
      const g = s('g', { class: 'cat-row' + (anySel && !sel ? ' is-dim' : '') + (sel ? ' is-selected' : ''), role: 'button', tabindex: '0',
        'aria-pressed': sel ? 'true' : 'false',
        'aria-label': r.name + ': ' + N.money(r.spend) + ', ' + N.pct(r.share) + ' of spending. Select to filter transactions.' });
      g.appendChild(s('rect', { class: 'hit', x: 0, y: yy + 1, width: W, height: rowH - 2, rx: 6 }));
      g.appendChild(s('text', { class: 'cat-label', x: labW, y: yy + rowH / 2 + 4.5, 'text-anchor': 'end', text: N.fitText(r.name, labW - 4, 13, 400) }));
      const x1 = x0 + Math.max(2, ((xMax - x0) * r.spend) / max);
      g.appendChild(s('path', { class: 'm-out', d: barPath(x0, x1, yy + (rowH - barH) / 2, barH) }));
      g.appendChild(s('text', { class: 'value-label', x: x1 + 8, y: yy + rowH / 2 + 4.5 },
        N.money(r.spend, { cents: false }), s('tspan', { class: 'value-label__muted', text: '  ' + N.pct(r.share) })));
      const rowsTip = [
        { value: N.money(r.spend), label: N.pct(r.share) + ' of spending', color: 'var(--out)' },
        { value: N.plural(r.count, 'purchase'), label: r.refunds > 0 ? 'less ' + N.money(r.refunds) + ' refunded' : '' },
      ];
      let note = null;
      if (o.prev && o.prevLabel) {
        const pv = o.prev.get(r.name) || 0;
        note = pv > 0 ? N.money(r.spend - pv, { sign: true }) + ' vs ' + o.prevLabel : 'Nothing in ' + o.prevLabel;
      }
      g._tip = { title: r.name, rows: rowsTip, note };
      if (o.onSelect) activate(g, () => o.onSelect(r.name));
      svg.appendChild(g);
    });
    N.clear(el).appendChild(svg);
  };

  // ------------------------------------------------------------ diverging change bars
  /** rows: [{name, cur, prev, diff, other}] — change per category, this month vs last.
      Diverging: less spending to the left (cool pole), more to the right (warm pole),
      gray zero line as the neutral midpoint. opts: { curLabel, prevLabel, onSelect(row) } */
  C.diverging = function (el, rows, opts) {
    const o = opts || {};
    const W = Math.max(280, el.clientWidth);
    const narrow = W < 560;
    const rowH = 34, barH = 12;
    const labW = narrow ? 100 : 150;
    const valW = narrow ? 92 : 210;
    const H = rows.length * rowH + 6;
    const x0 = labW + 10, x1 = W - valW - 10;
    const mid = (x0 + x1) / 2;
    const half = (x1 - x0) / 2 - 2;
    const max = Math.max(1, ...rows.map((r) => Math.abs(r.diff)));
    const svg = svgRoot(W, H, 'Change in spending by category, ' + o.curLabel + ' versus ' + o.prevLabel + '. Table view available.', true);
    svg.appendChild(s('line', { class: 'div-zero', x1: mid, x2: mid, y1: 0, y2: H }));
    rows.forEach((r, i) => {
      const yy = i * rowH;
      const up = r.diff > 0;
      const g = s('g', { class: 'cat-row', role: 'button', tabindex: '0',
        'aria-label': r.name + ': ' + N.money(r.cur) + ' in ' + o.curLabel + ' vs ' + N.money(r.prev) + ' in ' + o.prevLabel + ', change ' + N.money(r.diff, { sign: true }) + '. Select to list these transactions for both months.' });
      g.appendChild(s('rect', { class: 'hit', x: 0, y: yy + 1, width: W, height: rowH - 2, rx: 6 }));
      g.appendChild(s('text', { class: 'cat-label', x: labW, y: yy + rowH / 2 + 4.5, 'text-anchor': 'end', text: N.fitText(r.name, labW - 4, 13, 400) }));
      const len = (Math.abs(r.diff) / max) * half;
      const by = yy + (rowH - barH) / 2;
      if (len >= 1) {
        // barPath grows right from its baseline; mirror it for decreases.
        const d = barPath(mid + 1, mid + 1 + len, by, barH);
        const p = s('path', { class: up ? 'div-up' : 'div-down', d });
        if (!up) p.setAttribute('transform', 'translate(' + 2 * mid + ',0) scale(-1,1)');
        g.appendChild(p);
      } else {
        g.appendChild(s('rect', { class: 'div-flat', x: mid - 1, y: by, width: 2, height: barH }));
      }
      const change = N.money(r.diff, { cents: false, sign: true }) + (Math.abs(r.prev) > 0.005 ? ' (' + (r.diff >= 0 ? '+' : '') + N.pct(r.diff / r.prev) + ')' : r.cur > 0 ? ' (new)' : '');
      const txt = s('text', { class: 'value-label', x: W, y: yy + rowH / 2 + 4.5, 'text-anchor': 'end' }, change);
      if (!narrow) txt.insertBefore(s('tspan', { class: 'value-label__muted', text: N.money(r.cur, { cents: false }) + ' vs ' + N.money(r.prev, { cents: false }) + '   ' }), txt.firstChild);
      g.appendChild(txt);
      g._tip = {
        title: r.name,
        rows: [
          { value: N.money(r.cur), label: o.curLabel },
          { value: N.money(r.prev), label: o.prevLabel },
          { value: N.money(r.diff, { sign: true }), label: r.diff >= 0 ? 'more than last month' : 'less than last month', color: up ? 'var(--div-up)' : 'var(--div-down)' },
        ],
        note: r.other ? r.categories.join(', ') : 'Select to list both months.',
      };
      if (o.onSelect) activate(g, () => o.onSelect(r));
      svg.appendChild(g);
    });
    N.clear(el).appendChild(svg);
  };

  /** A tiny inline bar for list rows (merchants). */
  C.miniBar = function (frac, w) {
    const W = w || 96, Hh = 8;
    const svg = s('svg', { class: 'mini-bar', width: W, height: Hh, viewBox: '0 0 ' + W + ' ' + Hh, 'aria-hidden': 'true' });
    svg.appendChild(s('path', { class: 'm-out', d: barPath(0, Math.max(2, W * Math.min(1, frac)), 0, Hh) }));
    return svg;
  };

  // ------------------------------------------------------------ sparkline
  /** values: [{key, v, selected, label}] */
  C.sparkline = function (values, opts) {
    const o = opts || {};
    const W = o.width || 92, H = 30, pad = 5;
    const svg = s('svg', { class: 'spark', width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': 'true' });
    const vs = values.map((d) => d.v).filter(isFinite);
    if (values.length < 2 || !vs.length) return svg;
    let lo = Math.min(...vs), hi = Math.max(...vs);
    if (lo === hi) { lo -= 1; hi += 1; }
    const x = (i) => pad + (i * (W - pad * 2)) / (values.length - 1);
    const y = (v) => pad + ((hi - v) / (hi - lo)) * (H - pad * 2);
    if (lo < 0 && hi > 0) svg.appendChild(s('line', { class: 'spark-zero', x1: 0, x2: W, y1: y(0), y2: y(0) }));
    const pts = values.map((d, i) => (isFinite(d.v) ? x(i).toFixed(1) + ',' + y(d.v).toFixed(1) : null)).filter(Boolean);
    svg.appendChild(s('polyline', { class: 'spark-line', points: pts.join(' ') }));
    const step = (W - pad * 2) / (values.length - 1);
    values.forEach((d, i) => {
      if (!isFinite(d.v)) return;
      const g = s('g', { class: 'spark-pt' });
      g.appendChild(s('rect', { class: 'hit', x: x(i) - step / 2, y: 0, width: step, height: H }));
      if (d.selected) g.appendChild(s('circle', { class: 'spark-dot', cx: x(i), cy: y(d.v), r: 3.5 }));
      g._tip = { title: N.monthLabel(d.key, { long: true, year: true }), rows: [{ value: d.label, label: o.name }] };
      svg.appendChild(g);
    });
    svg.removeAttribute('aria-hidden');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', o.name + ' by month: ' + values.map((d) => N.monthLabel(d.key) + ' ' + d.label).join(', '));
    return svg;
  };

  // ------------------------------------------------------------ calendar heatmap
  /** model: { months:[key], day(iso)→{spend,count,top}|null, covered(iso)→bool, bins:[t1..t4], selected } */
  C.calendar = function (el, model, opts) {
    const o = opts || {};
    const W = Math.max(260, el.clientWidth);
    const single = model.months.length === 1;
    const today = N.today();
    const gap = single ? 4 : 3;
    const colGap = 22, rowGap = 18;
    const fit = Math.max(1, Math.min(model.months.length, W < 420 ? 2 : 3));
    const cell = single ? Math.floor(Math.min(46, (W - gap * 6) / 7))
      : Math.max(12, Math.min(20, Math.floor(((W - (fit - 1) * colGap) / fit - gap * 6) / 7)));
    const blockW = cell * 7 + gap * 6;
    const headH = single ? 22 : 34;
    const perRow = single ? 1 : Math.max(1, Math.floor((W + colGap) / (blockW + colGap)));
    const blocks = model.months.map((key) => {
      const start = N.monthStart(key);
      const days = N.monthEnd(key).getDate();
      const rowsN = Math.ceil((start.getDay() + days) / 7);
      return { key, start, days, rowsN };
    });
    const rowsOfBlocks = [];
    for (let i = 0; i < blocks.length; i += perRow) rowsOfBlocks.push(blocks.slice(i, i + perRow));
    let H = 0;
    const heights = rowsOfBlocks.map((rb) => headH + Math.max(...rb.map((b) => b.rowsN)) * (cell + gap));
    H = N.sum(heights) + rowGap * (rowsOfBlocks.length - 1);
    const svg = svgRoot(W, H, 'Daily spending calendar for ' + model.months.length + ' months. Table view available.');
    let yOff = 0;
    rowsOfBlocks.forEach((rb, ri) => {
      rb.forEach((b, bi) => {
        const bx = bi * (blockW + colGap);
        const g = s('g', { transform: 'translate(' + bx + ',' + yOff + ')' });
        if (!single) g.appendChild(s('text', { class: 'cal-title', x: 0, y: 12, text: N.monthLabel(b.key, { year: b.key.slice(5) === '01' || bi === 0 && ri === 0 }) }));
        const dowY = single ? 12 : 28;
        ['S', 'M', 'T', 'W', 'T', 'F', 'S'].forEach((d, i) => g.appendChild(s('text', { class: 'cal-dow', x: i * (cell + gap) + cell / 2, y: dowY, 'text-anchor': 'middle', text: single ? N.DOW[i] : d })));
        for (let d = 1; d <= b.days; d++) {
          const date = new Date(b.start.getFullYear(), b.start.getMonth(), d);
          const iso = N.iso(date);
          const idx = b.start.getDay() + d - 1;
          const cx = (idx % 7) * (cell + gap), cy = headH + Math.floor(idx / 7) * (cell + gap);
          if (date > today) {
            g.appendChild(s('rect', { class: 'cal-future', x: cx + 0.5, y: cy + 0.5, width: cell - 1, height: cell - 1, rx: 3 }));
            continue;
          }
          const info = model.day(iso);
          const covered = model.covered(iso);
          const gaps = model.gaps ? model.gaps(iso) : [];
          const c = s('g', { class: 'cal-cell' + (model.selected === iso ? ' is-selected' : ''), 'data-date': iso });
          let cls, lvl = 0;
          if (info && info.spend > 0.005) {
            lvl = 1;
            for (const t of model.bins) if (info.spend > t) lvl++;
            cls = 'hm hm-' + Math.min(5, lvl);
          } else cls = covered ? 'hm hm-0' : 'hm hm-none';
          c.appendChild(s('rect', { class: cls, x: cx + 0.5, y: cy + 0.5, width: cell - 1, height: cell - 1, rx: 3 }));
          // Same rule as partial months: a dot wherever some account has no statement that day.
          if (gaps.length) {
            if (info && info.spend > 0.005) c.appendChild(s('circle', { class: 'cal-nodata cal-nodata--corner', cx: cx + cell - 4, cy: cy + 4, r: 1.6 }));
            else c.appendChild(s('circle', { class: 'cal-nodata', cx: cx + cell / 2, cy: cy + cell / 2, r: 1.5 }));
          }
          if (single) c.appendChild(s('text', { class: 'cal-num' + (lvl >= 4 ? ' cal-num--inv' : ''), x: cx + 6, y: cy + 15, text: String(d) }));
          const rowsTip = info && info.spend > 0.005
            ? [{ value: N.money(info.spend), label: N.plural(info.count, 'purchase'), color: 'var(--out)' }].concat(info.top ? [{ value: N.money(info.top.v), label: info.top.name }] : [])
            : [{ value: covered ? 'No spending' : 'No statement covers this day', label: '' }];
          const gapNote = gaps.length ? 'No statement covers this day for ' + (gaps.length > 3 ? gaps.slice(0, 3).join(', ') + ' and ' + (gaps.length - 3) + ' more' : gaps.join(', ')) + '.' : null;
          c._tip = { title: N.fmtDate(date, { dow: true, year: true }), rows: rowsTip, note: [gapNote, info && info.spend > 0 && o.onSelect ? 'Select to list this day.' : null].filter(Boolean).join(' ') || null };
          if (o.onSelect && info && info.spend > 0) {
            c.classList.add('is-clickable');
            c.addEventListener('click', () => o.onSelect(iso));
          }
          g.appendChild(c);
        }
        svg.appendChild(g);
      });
      yOff += heights[ri] + rowGap;
    });
    N.clear(el).appendChild(svg);
  };

  C.calendarLegend = function (bins) {
    const labels = [];
    const edges = [0].concat(bins);
    for (let i = 0; i < 5; i++) {
      const lo = edges[i], hi = edges[i + 1];
      labels.push(hi == null ? 'over ' + N.compact(lo) : N.compact(lo) + '–' + N.compact(hi));
    }
    const sw = (cls, title) => h('span', { class: 'cal-swatch ' + cls, title });
    return h('div', { class: 'cal-legend' },
      h('span', { class: 'cal-legend__group' }, h('span', { class: 'cal-legend__txt', text: 'Less' }),
        [1, 2, 3, 4, 5].map((i) => sw('hm-' + i, labels[i - 1])), h('span', { class: 'cal-legend__txt', text: 'More' })),
      h('span', { class: 'cal-legend__group' }, sw('hm-0', 'No spending'), h('span', { class: 'cal-legend__txt', text: 'No spending' })),
      h('span', { class: 'cal-legend__group' }, h('span', { class: 'cal-swatch cal-swatch--nodata' }), h('span', { class: 'cal-legend__txt', text: 'Some accounts not covered' })));
  };

  // ------------------------------------------------------------ meter (utilization)
  C.meter = function (frac, opts) {
    const o = opts || {};
    const W = o.width || 160, Hh = 8;
    const f = Math.max(0, Math.min(1, frac));
    const level = frac >= 0.5 ? 'critical' : frac >= 0.3 ? 'warning' : 'ok';
    const svg = s('svg', { class: 'meter meter--' + level, width: W, height: Hh, viewBox: '0 0 ' + W + ' ' + Hh, role: 'img',
      'aria-label': 'Credit used: ' + N.pct(frac) });
    svg.appendChild(s('rect', { class: 'meter-track', x: 0, y: 0, width: W, height: Hh, rx: 4 }));
    if (f > 0) svg.appendChild(s('rect', { class: 'meter-fill', x: 0, y: 0, width: Math.max(Hh, W * f), height: Hh, rx: 4 }));
    // 30% guide
    svg.appendChild(s('line', { class: 'meter-guide', x1: W * 0.3, x2: W * 0.3, y1: 0, y2: Hh }));
    return { svg, level };
  };

  // ------------------------------------------------------------ coverage timeline
  /** One row: statements as segments on a shared [t0, t1] scale. */
  C.timeline = function (W, t0, t1, segs, gaps, due, highlight) {
    const Hh = 24;
    const span = N.dayDiff(t0, t1) || 1;
    const x = (d) => (N.dayDiff(t0, d) / span) * W;
    const svg = s('svg', { class: 'timeline', width: W, height: Hh, viewBox: '0 0 ' + W + ' ' + Hh, role: 'img',
      'aria-label': segs.length + ' statements' + (gaps.length ? ', ' + gaps.length + ' gap' + (gaps.length > 1 ? 's' : '') : '') });
    if (highlight) svg.appendChild(s('rect', { class: 'tl-period', x: x(highlight[0]), y: 0, width: Math.max(2, x(N.addDays(highlight[1], 1)) - x(highlight[0])), height: Hh }));
    svg.appendChild(s('line', { class: 'tl-track', x1: 0, x2: W, y1: Hh / 2, y2: Hh / 2 }));
    for (const g of gaps) {
      const a = x(N.parseDate(g.start)), b = x(N.addDays(N.parseDate(g.end), 1));
      const gEl = s('g', { class: 'tl-gapg' });
      gEl.appendChild(s('rect', { class: 'tl-gap', x: a + 1, y: 3.5, width: Math.max(2, b - a - 2), height: Hh - 7, rx: 3 }));
      if (b - a > 52) gEl.appendChild(s('text', { class: 'tl-label tl-label--gap', x: (a + b) / 2, y: Hh / 2 + 3.5, 'text-anchor': 'middle', text: 'missing' }));
      gEl._tip = g.tip;
      svg.appendChild(gEl);
    }
    if (due) {
      const a = x(N.parseDate(due.start)), b = Math.min(W, x(N.addDays(N.parseDate(due.end), 1)));
      const gEl = s('g', { class: 'tl-dueg' });
      gEl.appendChild(s('rect', { class: 'tl-due', x: a + 1, y: 3.5, width: Math.max(2, b - a - 2), height: Hh - 7, rx: 3 }));
      if (b - a > 64) gEl.appendChild(s('text', { class: 'tl-label tl-label--due', x: (a + b) / 2, y: Hh / 2 + 3.5, 'text-anchor': 'middle', text: 'not added' }));
      gEl._tip = due.tip;
      svg.appendChild(gEl);
    }
    for (const sg of segs) {
      const a = x(N.parseDate(sg.start)), b = x(N.addDays(N.parseDate(sg.end), 1));
      const r = s('rect', { class: 'tl-seg' + (sg.status ? ' tl-seg--' + sg.status : ''), x: a + 1, y: 4, width: Math.max(2, b - a - 2), height: Hh - 8, rx: 3 });
      r._tip = sg.tip;
      svg.appendChild(r);
    }
    const tx = x(N.today());
    svg.appendChild(s('line', { class: 'tl-today', x1: tx, x2: tx, y1: 1, y2: Hh - 1 }));
    return svg;
  };

  C.timelineAxis = function (W, t0, t1) {
    const Hh = 18;
    const span = N.dayDiff(t0, t1) || 1;
    const svg = s('svg', { class: 'timeline-axis', width: W, height: Hh, viewBox: '0 0 ' + W + ' ' + Hh, 'aria-hidden': 'true' });
    let d = new Date(t0.getFullYear(), t0.getMonth(), 1);
    if (d < t0) d = new Date(t0.getFullYear(), t0.getMonth() + 1, 1);
    const months = [];
    while (d <= t1) { months.push(d); d = new Date(d.getFullYear(), d.getMonth() + 1, 1); }
    const every = Math.max(1, Math.ceil(months.length / Math.max(1, Math.floor(W / 46))));
    months.forEach((m, i) => {
      const xx = (N.dayDiff(t0, m) / span) * W;
      svg.appendChild(s('line', { class: 'gridline', x1: xx, x2: xx, y1: Hh - 5, y2: Hh }));
      if (i % every === 0 && xx < W - 20) svg.appendChild(s('text', { class: 'tick', x: xx + 3, y: 11, text: N.MON[m.getMonth()] + (m.getMonth() === 0 ? ' ' + m.getFullYear() : '') }));
    });
    const tx = (N.dayDiff(t0, N.today()) / span) * W;
    svg.appendChild(s('text', { class: 'tick tick--today', x: Math.min(tx, W - 2), y: 11, 'text-anchor': 'end', text: 'today' }));
    return svg;
  };
})();
