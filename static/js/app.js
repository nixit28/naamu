/* Naamu — state, filters, data loading, upload flow and rendering orchestration. */
(function () {
  'use strict';
  const N = window.Naamu;
  const { h, model: M, charts: C, sections: S, table: T } = N;

  if (N.isMock) N.setToday(new Date(2026, 9, 29));

  const state = {
    period: 'all',
    acctOff: new Set(),
    table: { cats: null, merchant: null, date: null, search: '', acct: '', kind: '', sort: 'date', dir: 'desc', page: 0 },
    views: {},              // chart id -> 'chart' | 'table'
    uploads: null,          // { pending:[names] } | { results:[...] }
    pendingRule: null,      // { id, merchant, category, count } | { done, message }
    confirmDel: null,
    expanded: new Set(),
    cmpMonth: null,         // month shown on the left of "X vs the month before"; null = automatic
  };
  let ix = null;
  let ctx = null;
  const el = (id) => document.getElementById(id);
  const main = el('main');

  // ------------------------------------------------------------ boot
  function boot() {
    N.initTooltips();
    if (N.isMock) el('mock-badge').hidden = false;
    el('add-btn').addEventListener('click', pickFiles);
    el('file-input').addEventListener('change', (e) => {
      const files = Array.from(e.target.files || []);
      e.target.value = '';
      upload(files);
    });
    el('theme-btn').addEventListener('click', toggleTheme);
    syncThemeButton();
    el('export-btn').addEventListener('click', exportCSV);
    el('reparse-btn').addEventListener('click', reparse);
    initDrop();
    const ro = new ResizeObserver(N.debounce(() => { if (ctx) renderCharts(); }, 120));
    ro.observe(main);
    if (window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', syncThemeButton);
    load(true);
  }

  async function load(first) {
    if (!first) main.classList.add('is-refreshing');
    try {
      const data = await N.api.getData();
      ix = M.index(data);
      sanitize();
      render();
    } catch (err) {
      if (first || !ix) showError(err);
      else flash('Could not refresh: ' + err.message);
    } finally {
      main.classList.remove('is-refreshing');
    }
  }

  function sanitize() {
    if (state.period !== 'all' && state.period !== 'last3' && !ix.months.includes(state.period)) state.period = 'all';
    for (const id of Array.from(state.acctOff)) if (!ix.acctById.has(id)) state.acctOff.delete(id);
    if (state.table.acct && !ix.acctById.has(Number(state.table.acct))) state.table.acct = '';
    if (state.confirmDel && !ix.stmtById.has(state.confirmDel)) state.confirmDel = null;
  }

  function showError(err) {
    const box = N.clear(el('state-msg'));
    box.hidden = false;
    el('dashboard').hidden = true;
    el('filterbar').hidden = true;
    box.appendChild(h('div', { class: 'error-card', role: 'alert' },
      h('p', { class: 'state-msg__title', text: 'Naamu couldn’t load your data' }),
      h('p', { class: 'state-msg__body', text: N.isMock
        ? 'The demo data file (mock-data.json) didn’t load: ' + err.message
        : 'The local Naamu server didn’t answer. Make sure it’s running on this computer, then try again. (' + err.message + ')' }),
      h('button', { type: 'button', class: 'btn btn--primary', onclick: () => { N.clear(box).appendChild(h('p', { class: 'state-msg__title', text: 'Opening your ledger…' })); load(true); } }, 'Try again')));
  }

  let flashTimer = 0;
  function flash(msg) {
    let f = el('flash');
    if (!f) { f = h('div', { id: 'flash', class: 'flash', role: 'status' }); document.body.appendChild(f); }
    f.textContent = msg;
    f.hidden = false;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => { f.hidden = true; }, 4200);
  }

  // ------------------------------------------------------------ render
  function captureFocus() {
    const a = document.activeElement;
    return a && a.dataset && a.dataset.focusKey ? a.dataset.focusKey : null;
  }
  function restoreFocus(key) {
    if (!key) return;
    const t = document.querySelector('[data-focus-key="' + (window.CSS && CSS.escape ? CSS.escape(key) : key) + '"]');
    if (t) t.focus({ preventScroll: true });
  }

  function render() {
    const focusKey = captureFocus();
    el('state-msg').hidden = true;
    S.uploads(el('upload-results'), state.uploads, api);
    const hasData = ix.data.statements.length > 0 || ix.data.transactions.length > 0;
    el('empty').hidden = hasData;
    el('dashboard').hidden = !hasData;
    el('filterbar').hidden = !hasData;
    document.body.classList.toggle('is-empty', !hasData);
    if (!hasData) { S.empty(el('empty'), api); restoreFocus(focusKey); return; }

    renderFilters();
    const sel = M.select(ix, state);
    const tot = M.totals(sel.txF);
    ctx = { ix, sel, tot, state, app: api, afterLayout: [] };

    safe('kpis', () => S.kpis(el('kpis'), ctx));
    safe('insights', () => S.insights(el('insights'), ctx));
    safe('compare', () => { ctx.cmp = S.compare(el('compare'), ctx); });
    safe('merchants', () => S.merchants(el('merchants'), ctx));
    safe('recurring', () => S.recurring(el('recurring'), ctx));
    safe('accounts', () => S.accounts(el('accounts'), ctx));
    safe('statements', () => S.statements(el('statements'), ctx));
    safe('txns', () => T.render(el('txns'), ctx));
    renderTools();
    renderCharts();
    restoreFocus(focusKey);
  }

  /** One section failing (odd data from a new statement format) must not blank the page. */
  function safe(id, fn) {
    try { fn(); } catch (err) {
      console.error('Naamu: could not render ' + id, err);
      const host = el(id);
      if (host) N.clear(host).appendChild(S.emptyBlock('This section couldn’t be drawn', 'Something in the data was unexpected (' + err.message + '). The rest of the page is unaffected.'));
    }
  }

  function renderTools() {
    document.querySelectorAll('[data-tools]').forEach((host) => {
      const id = host.dataset.tools;
      const v = state.views[id] || 'chart';
      N.clear(host).appendChild(h('div', { class: 'seg', role: 'group', 'aria-label': 'Show as' },
        ['chart', 'table'].map((k) => h('button', { type: 'button', class: 'seg__btn', 'aria-pressed': v === k ? 'true' : 'false', 'data-focus-key': 'view:' + id + ':' + k,
          onclick: () => { state.views[id] = k; renderTools(); renderCharts(); } }, k === 'chart' ? 'Chart' : 'Table'))));
    });
  }

  function renderCharts() {
    if (!ctx) return;
    drawCharts();
  }
  function drawCharts() {
    N.hideTip();
    const { sel, tot } = ctx;
    const view = (id) => state.views[id] || 'chart';

    safe('chart-cashflow', () => {
      // cash flow — all months for context; the selected period is emphasized. Never netted.
      const cf = el('chart-cashflow');
      const months = ix.months.slice(-18);
      const selSet = new Set(sel.selMonths);
      const monthly = M.byMonth(sel.txA, months).map((m) => {
        const g = M.monthGaps(ix, sel.accts, m.key);
        const dests = Array.from(m.dests.values());
        return Object.assign(m, { selected: selSet.has(m.key), partial: g.partial, gapText: g.text,
          putBy: new Map(dests.filter((d) => d.put > 0.005).map((d) => [d.name, d.put])),
          takenBy: new Map(dests.filter((d) => d.taken > 0.005).map((d) => [d.name, d.taken])),
          count: N.sum(Array.from(m.cats.values()), (c) => c.count) });
      });
      if (!months.length || !sel.accts.length) cf.replaceChildren(S.emptyBlock('No months to show', 'Turn on an account above.'));
      else if (view('cashflow') === 'table') {
        cf.replaceChildren(C.table('Monthly cash flow', [{ label: 'Month' }, { label: 'Money in', num: 1 }, { label: 'From savings', num: 1 }, { label: 'Spent', num: 1 },
          { label: 'Into savings', num: 1 }, { label: 'Left in checking', num: 1 }, { label: 'Coverage' }],
          monthly.map((m) => [N.monthLabel(m.key, { year: true }), N.money(m.in), N.money(m.takenOut), N.money(m.out), N.money(m.putIn),
            N.money(m.left, { sign: true }), m.partial ? 'Partial: ' + m.gapText : 'Complete'])));
      } else {
        cf.replaceChildren();
        const lg = C.legend([{ cls: 'm-in', label: 'Money in' }, { cls: 'm-save-from', label: 'Taken out of savings' }, { cls: 'm-out', label: 'Spent' },
          { cls: 'm-save', label: 'Put into savings' }, { cls: 'm-net', shape: 'dot', label: 'Left in checking' }]);
        const hatch = lg.children[1].querySelector('.legend__key');
        hatch.replaceWith(C.hatchSwatch());
        cf.appendChild(lg);
        const host = h('div', { class: 'chart__plot' });
        cf.appendChild(host);
        C.cashflow(host, monthly, { onSelect: (k) => setPeriod(state.period === k ? 'all' : k) });
        const partial = monthly.filter((m) => m.partial);
        if (partial.length) {
          const shown = partial.slice(-3).reverse();
          cf.appendChild(h('div', { class: 'chart__note cf-notes' },
            h('p', null, h('span', { class: 'chart__mark', text: '*' }), ' Partial months (hatched background): not every account has statements covering every day.'),
            h('ul', null, shown.map((m) => h('li', null, h('strong', { text: N.monthLabel(m.key, { year: partial.length > 1 && new Set(partial.map((x) => x.key.slice(0, 4))).size > 1 }) + ': ' }), m.gapText + '.'))),
            partial.length > shown.length ? h('p', { text: 'And ' + N.plural(partial.length - shown.length, 'earlier partial month') + '; hover a month for details.' }) : null));
        } else cf.appendChild(h('p', { class: 'chart__note', text: 'Every month is fully covered. Select a month to focus the whole page on it.' }));
      }
    });

    safe('chart-compare', () => {
      const host = el('chart-compare');
      const detail = el('compare-detail');
      const cmp = ctx.cmp;
      detail.hidden = !cmp;
      if (!cmp) return;
      const { pair, c } = cmp;
      const curL = N.monthLabel(pair.cur, { year: true }), prevL = N.monthLabel(pair.prev, { year: true });
      if (!c.cats.length) host.replaceChildren(S.emptyBlock('No spending in either month', ''));
      else if (view('compare') === 'table') {
        host.replaceChildren(C.table('Spending by category, ' + curL + ' vs ' + prevL, [{ label: 'Category' }, { label: curL, num: 1 }, { label: prevL, num: 1 }, { label: 'Change', num: 1 }, { label: '%', num: 1 }],
          c.cats.map((r) => [r.name, N.money(r.cur), N.money(r.prev), N.money(r.diff, { sign: true }), Math.abs(r.prev) > 0.005 ? N.pct(r.diff / r.prev) : r.cur > 0 ? 'new' : '—'])));
      } else {
        host.replaceChildren();
        host.appendChild(C.legend([{ cls: 'div-down', label: 'Less than ' + N.monthLabel(pair.prev) }, { cls: 'div-up', label: 'More than ' + N.monthLabel(pair.prev) }]));
        const plot = h('div', { class: 'chart__plot' });
        host.appendChild(plot);
        C.diverging(plot, c.cats, { curLabel: curL, prevLabel: prevL, onSelect: (r) => filterCategory(r.categories, r.name, [pair.prev, pair.cur]) });
      }
      const ul = N.clear(el('compare-merch'));
      if (!c.merchants.length) ul.appendChild(h('li', { class: 'cmp-merch__empty', text: 'No merchant changed by more than a dollar.' }));
      // In a partial month "nothing" may just mean "not covered yet" — say so instead of "new".
      const prevPartial = M.monthGaps(ix, sel.accts, pair.prev).partial;
      const curPartial = M.monthGaps(ix, sel.accts, pair.cur).partial;
      const prevShort = N.monthLabel(pair.prev), curShort = N.monthLabel(pair.cur);
      for (const r of c.merchants) {
        let what;
        if (r.prev < 0.005) what = prevPartial
          ? [h('strong', { text: N.money(r.cur) }), ' this month; none in ' + prevShort + '’s statements (' + prevShort + ' is partial)']
          : [h('strong', { text: N.money(r.cur) }), ' new this month'];
        else if (r.cur < 0.005) what = curPartial
          ? [h('strong', { text: N.money(r.prev) }), ' last month; none yet in ' + curShort + ' (' + curShort + ' is partial)']
          : [h('strong', { text: N.money(r.prev) }), ' last month, nothing this month'];
        else what = [h('strong', { text: N.money(Math.abs(r.diff)) }), (r.diff > 0 ? ' more' : ' less') + ' than last month (' + N.money(r.cur, { cents: false }) + ' vs ' + N.money(r.prev, { cents: false }) + ')'];
        ul.appendChild(h('li', null, h('button', { type: 'button', class: 'cmp-merch__row', 'data-focus-key': 'cmp-m:' + r.name, onclick: () => filterMerchant(r.name, true, [pair.prev, pair.cur]) },
          h('span', { class: 'cmp-merch__name', text: r.name + ': ' }), h('span', null, what))));
      }
    });

    safe('chart-sankey', () => {
      // sankey
      const sk = el('chart-sankey');
      const wide = sk.clientWidth >= 640;
      const skModel = M.sankey(tot, wide ? 10 : 7);
      if (!skModel.total) sk.replaceChildren(S.emptyBlock('Nothing to show for this period', 'There is no income or spending in the selected months and accounts.'));
      else if (view('sankey') === 'table') {
        const side = { in: 'Money in', saving: 'Savings & investments', out: 'Spent', cash: 'Checking balance' };
        sk.replaceChildren(C.table('Where the money went', [{ label: 'From / to' }, { label: 'Kind' }, { label: 'Amount', num: 1 }, { label: 'Share', num: 1 }],
          skModel.sources.map((n) => [n.label, side[n.kind], N.money(n.value), N.pct(n.value / skModel.total)])
            .concat(skModel.targets.map((n) => [n.label, side[n.kind], N.money(n.value), N.pct(n.value / skModel.total)]))));
      } else {
        sk.replaceChildren();
        const all = skModel.sources.concat(skModel.targets);
        sk.appendChild(C.legend([{ cls: 'm-in', label: 'Money in' }, { cls: 'm-out', label: 'Spending' }]
          .concat(all.some((x) => x.kind === 'saving') ? [{ cls: 'm-save', label: 'Savings & investments' }] : [])
          .concat(all.some((x) => x.kind === 'cash') ? [{ cls: 'm-cash', label: 'Checking balance' }] : [])));
        const host = h('div', { class: 'chart__plot' });
        sk.appendChild(host);
        C.sankey(host, skModel, { onCategory: (names, label) => filterCategory(names, label), onDest: (d) => filterMerchant(d, true) });
      }
      const sub = el('sankey-sub');
      if (sub) sub.textContent = skModel.left >= 0
        ? 'Income and money taken out of savings on the left; spending, savings and what stayed in checking on the right.'
        : 'More went out than came in ' + periodWord(true) + ', so the checking balance covered the difference.';
    });

    safe('chart-categories', () => {
      // categories
      const cat = el('chart-categories');
      const rows = M.categoryRows(tot);
      const single = sel.selMonths.length === 1 ? sel.selMonths[0] : null;
      let prev = null, prevLabel = null;
      if (single && ix.months.includes(N.shiftMonth(single, -1))) {
        const pk = N.shiftMonth(single, -1);
        const pt = M.totals(sel.txA.filter((t) => N.monthKey(t.date) === pk));
        prev = new Map(Array.from(pt.cats.values()).map((c) => [c.name, c.spend]));
        prevLabel = N.monthLabel(pk);
      }
      if (!rows.length) cat.replaceChildren(S.emptyBlock('No spending in this period', 'Try a different month or include more accounts.'));
      else if (view('categories') === 'table') {
        cat.replaceChildren(C.table('Spending by category', [{ label: 'Category' }, { label: 'Spent', num: 1 }, { label: 'Share', num: 1 }, { label: 'Purchases', num: 1 }].concat(prev ? [{ label: 'vs ' + prevLabel, num: 1 }] : []),
          rows.map((r) => [r.name, N.money(r.spend), N.pct(r.share), String(r.count)].concat(prev ? [N.money(r.spend - (prev.get(r.name) || 0), { sign: true })] : []))));
      } else {
        cat.replaceChildren();
        const host = h('div', { class: 'chart__plot' });
        cat.appendChild(host);
        C.categoryBars(host, rows, { selected: state.table.cats ? state.table.cats.names : null, onSelect: (n) => filterCategory([n], n), prev, prevLabel });
      }
    });

    safe('chart-cards', () => {
      // spending by card (and checking, so the bars add up to Spent)
      const host = el('chart-cards');
      const rows = M.accountRows(ix, sel.txF);
      if (!rows.length) host.replaceChildren(S.emptyBlock('No spending in this period', 'Try a different month or include more accounts.'));
      else if (view('cards') === 'table') {
        host.replaceChildren(C.table('Spending by card', [{ label: 'Card or account' }, { label: 'Holder' }, { label: 'Spent', num: 1 }, { label: 'Share', num: 1 }, { label: 'Purchases', num: 1 }, { label: 'Top category' }],
          rows.map((r) => [N.shortAccount(r.acct), (r.acct && N.personName(r.acct.holder)) || '—', N.money(r.spend), N.pct(r.share), String(r.count), r.top.length ? r.top[0][0] : '—'])));
      } else {
        host.replaceChildren();
        const plot = h('div', { class: 'chart__plot' });
        host.appendChild(plot);
        C.accountBars(plot, rows, { selected: state.table.acct ? Number(state.table.acct) : null, onSelect: (r) => filterAccount(r.id) });
      }
    });

    safe('chart-calendar', () => {
      // calendar
      const cal = el('chart-calendar');
      const calMonths = sel.selMonths.slice(-6);
      const days = new Map();
      for (const t of sel.txF) {
        if (!M.SPEND_KINDS[t.kind]) continue;
        const d = days.get(t.date) || { spend: 0, count: 0, by: new Map() };
        d.spend -= t.amount;
        if (t.kind === 'expense') d.count++;
        d.by.set(t.merchant, (d.by.get(t.merchant) || 0) - t.amount);
        days.set(t.date, d);
      }
      for (const d of days.values()) {
        const top = Array.from(d.by.entries()).sort((a, b) => b[1] - a[1])[0];
        d.top = top && d.count > 1 ? { name: 'largest: ' + top[0], v: top[1] } : null;
      }
      const vals = Array.from(days.entries()).filter(([k, d]) => calMonths.includes(N.monthKey(k)) && d.spend > 0.005).map(([, d]) => d.spend).sort((a, b) => a - b);
      const q = (p) => vals[Math.min(vals.length - 1, Math.floor(p * vals.length))];
      // Skewed breaks keep ordinary days light and let the expensive ones stand out.
      const bins = vals.length ? [q(0.35), q(0.6), q(0.8), q(0.93)].map((v) => Math.round(v)) : [10, 25, 50, 100];
      if (!calMonths.length) cal.replaceChildren(S.emptyBlock('No days to show', 'Pick a period with transactions.'));
      else if (view('calendar') === 'table') {
        const list = Array.from(days.entries()).filter(([k, d]) => calMonths.includes(N.monthKey(k)) && d.spend > 0.005).sort((a, b) => (a[0] < b[0] ? -1 : 1));
        cal.replaceChildren(C.table('Daily spending', [{ label: 'Day' }, { label: 'Spent', num: 1 }, { label: 'Purchases', num: 1 }],
          list.map(([k, d]) => [N.fmtDate(k, { dow: true, year: true }), N.money(d.spend), String(d.count)])));
      } else {
        cal.replaceChildren();
        const host = h('div', { class: 'chart__plot' });
        cal.appendChild(host);
        C.calendar(host, {
          months: calMonths, bins, selected: state.table.date,
          day: (iso) => days.get(iso) || null,
          covered: (iso) => M.dayCovered(ix, sel.accts, N.parseDate(iso)),
          gaps: (iso) => M.dayGaps(ix, sel.accts, N.parseDate(iso)).map((a) => M.acctName(ix, a)),
        }, { onSelect: (iso) => filterDate(iso) });
        cal.appendChild(C.calendarLegend(bins));
        if (sel.selMonths.length > calMonths.length) cal.appendChild(h('p', { class: 'chart__note', text: 'Showing the latest 6 months of the selected period.' }));
      }
    });

    for (const fn of ctx.afterLayout) safe('accounts', fn);
  }

  function periodWord(withIn) {
    const m = ctx.sel.selMonths;
    if (m.length === 1) return (withIn ? 'in ' : '') + N.monthLabel(m[0], { long: true });
    return withIn ? 'over these months' : 'these months';
  }

  // ------------------------------------------------------------ filters
  function renderFilters() {
    const pc = N.clear(el('period-chips'));
    const chip = (key, label, pressed, onclick, extra) => h('button', Object.assign({ type: 'button', class: 'chip', 'aria-pressed': pressed ? 'true' : 'false', 'data-focus-key': key, onclick }, extra || {}), label);
    pc.appendChild(chip('p:all', 'All', state.period === 'all', () => setPeriod('all')));
    if (ix.months.length > 3) pc.appendChild(chip('p:last3', 'Last 3 months', state.period === 'last3', () => setPeriod('last3')));
    const recent = ix.months.slice(-6);
    const older = ix.months.slice(0, -6);
    const multiYear = new Set(ix.months.map((m) => m.slice(0, 4))).size > 1;
    if (older.length) {
      const s = h('select', { class: 'select select--chip' + (older.includes(state.period) ? ' is-active' : ''), 'aria-label': 'Earlier months', 'data-focus-key': 'p:older',
        onchange: (e) => setPeriod(e.target.value || 'all') },
        h('option', { value: '', text: 'Earlier…' }),
        older.slice().reverse().map((m) => h('option', { value: m, selected: state.period === m, text: N.monthLabel(m, { year: true }) })));
      pc.appendChild(s);
    }
    for (const m of recent) {
      pc.appendChild(chip('p:' + m, N.monthLabel(m, { year: multiYear && (m.endsWith('-01') || m === recent[0]) }), state.period === m, () => setPeriod(m),
        { title: N.monthLabel(m, { long: true, year: true }) }));
    }
    const ac = N.clear(el('account-chips'));
    for (const a of ix.data.accounts) {
      const on = !state.acctOff.has(a.id);
      ac.appendChild(chip('a:' + a.id, N.shortAccount(a), on, () => toggleAccount(a.id),
        { title: (on ? 'Hide ' : 'Show ') + a.label + (a.holder ? ' · ' + N.personName(a.holder) : ''), class: 'chip chip--acct' + (on ? '' : ' is-off'), 'aria-label': a.label }));
    }
    if (state.acctOff.size) ac.appendChild(h('button', { type: 'button', class: 'chip chip--reset', 'data-focus-key': 'a:all', onclick: () => { state.acctOff.clear(); afterFilter(); } }, 'Show all'));
  }

  function afterFilter() {
    state.table.page = 0;
    state.pendingRule = null;
    render();
  }
  function setPeriod(p) {
    state.period = p;
    state.cmpMonth = null;
    state.table.date = null;
    afterFilter();
  }
  function toggleAccount(id) {
    if (state.acctOff.has(id)) state.acctOff.delete(id);
    else state.acctOff.add(id);
    if (state.acctOff.size >= ix.data.accounts.length) state.acctOff.clear();
    afterFilter();
  }

  function scrollToTable(focusKey) {
    const card = el('card-txns');
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    card.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    if (focusKey) restoreFocus(focusKey);
  }
  function filterCategory(names, label, months) {
    const f = state.table;
    const same = f.cats && f.cats.names.size === names.length && names.every((n) => f.cats.names.has(n)) && String(f.months) === String(months || null);
    f.cats = same ? null : { names: new Set(names), label };
    f.months = same ? null : months || null;
    f.page = 0;
    render();
    if (!same) scrollToTable('chip:cats');
  }
  function filterMerchant(name, keep, months) {
    state.table.merchant = state.table.merchant === name && !keep ? null : name;
    state.table.months = state.table.merchant ? months || null : null;
    state.table.page = 0;
    render();
    if (state.table.merchant) scrollToTable('chip:merchant');
  }
  function filterAccount(id) {
    state.table.acct = state.table.acct === String(id) ? '' : String(id);
    state.table.page = 0;
    render();
    if (state.table.acct) scrollToTable('chip:acct');
  }
  function filterDate(iso) {
    state.table.date = state.table.date === iso ? null : iso;
    state.table.page = 0;
    render();
    if (state.table.date) scrollToTable('chip:date');
  }

  // ------------------------------------------------------------ table actions
  function renderTableOnly() {
    const focusKey = captureFocus();
    T.render(el('txns'), ctx);
    restoreFocus(focusKey);
  }
  const api = {
    pickFiles,
    filterCategory, filterMerchant, filterDate, filterAccount,
    setTableFilter(key, value) {
      const f = state.table;
      if (key === 'cat') f.cats = value ? { names: new Set([value]), label: value } : null;
      else f[key] = value;
      f.page = 0;
      if (key === 'cat' || key === 'acct') render(); else renderTableOnly();
    },
    setSearch(v) {
      state.table.search = v;
      state.table.page = 0;
      // Keep the input (and its caret) — only redraw the rows.
      const body = el('txns').querySelector('.txn-body');
      const chips = [];
      const f = state.table;
      if (f.cats) chips.push({ key: 'cats', label: (f.cats.names.size === 1 ? 'Category: ' : '') + f.cats.label });
      if (f.merchant) chips.push({ key: 'merchant', label: 'Merchant: ' + f.merchant });
      if (f.date) chips.push({ key: 'date', label: 'Day: ' + N.fmtDate(f.date, { dow: true }) });
      if (f.months) chips.push({ key: 'months', label: 'Months: ' + f.months.map((m) => N.monthLabel(m)).join(' & ') + ' ' + f.months[1].slice(0, 4) });
      if (f.acct) chips.push({ key: 'acct', label: 'Account: ' + N.shortAccount(ix.acctById.get(Number(f.acct))) });
      if (f.kind) chips.push({ key: 'kind', label: 'Kind: ' + ({ expense: 'Spending', refund: 'Refunds', income: 'Income', saving: 'Saving', transfer: 'Transfers' })[f.kind] });
      if (f.search.trim()) chips.push({ key: 'search', label: 'Search: “' + f.search.trim() + '”' });
      if (body) T.renderBody(body, ctx, chips);
    },
    clearTableFilter(key) {
      const f = state.table;
      const charts = key === 'cats' || key === 'merchant' || key === 'date' || key === 'months' || key === 'acct' || key === 'all';
      if (key === 'all') Object.assign(f, { cats: null, merchant: null, date: null, months: null, search: '', acct: '', kind: '' });
      else if (key === 'cats') f.cats = null;
      else if (key === 'search') f.search = '';
      else f[key] = key === 'acct' || key === 'kind' ? '' : null;
      f.page = 0;
      if (charts) render(); else renderTableOnly();
      restoreFocus('f-search');
    },
    sortTable(key) {
      const f = state.table;
      if (f.sort === key) f.dir = f.dir === 'asc' ? 'desc' : 'asc';
      else { f.sort = key; f.dir = key === 'date' || key === 'amount' ? 'desc' : 'asc'; }
      f.page = 0;
      renderTableOnly();
    },
    setPage(p) {
      state.table.page = Math.max(0, p);
      renderTableOnly();
      el('card-txns').scrollIntoView({ block: 'start' });
    },
    async changeCategory(t, category) {
      if (category === t.category) return;
      try {
        await N.api.patchTxn(t.id, { category });
        const count = ix.merchantCount.get(t.merchant) || 1;
        state.pendingRule = { id: t.id, merchant: t.merchant, category, count };
        await load();
        restoreFocus('rule-yes');
      } catch (err) {
        flash('Couldn’t change the category: ' + err.message);
        render();
      }
    },
    async applyRule(p) {
      try {
        const res = await N.api.patchTxn(p.id, { category: p.category, apply_to_merchant: true });
        const n = res && res.updated ? res.updated.length : p.count;
        state.pendingRule = { done: true, id: p.id, message: 'Updated ' + N.plural(n, 'transaction') + '. Future “' + p.merchant + '” transactions will be ' + p.category + '.' };
        await load();
        restoreFocus('rule-ok');
      } catch (err) {
        flash('Couldn’t save the rule: ' + err.message);
      }
    },
    dismissRule() {
      const id = state.pendingRule && state.pendingRule.id;
      state.pendingRule = null;
      renderTableOnly();
      if (id) restoreFocus('cat:' + id);
    },
    confirmDelete(id, prevId) {
      state.confirmDel = id;
      const s = el('statements');
      const focusKey = id ? 'del-no:' + id : 'del:' + prevId;
      if (id) state.expanded.delete(id);
      S.statements(s, ctx);
      restoreFocus(focusKey);
    },
    async deleteStatement(id) {
      try {
        await N.api.deleteStatement(id);
        state.confirmDel = null;
        state.expanded.delete(id);
        flash('Statement deleted.');
        await load();
        restoreFocus('reparse');
      } catch (err) {
        flash('Couldn’t delete it: ' + err.message);
      }
    },
    toggleChecks(id) {
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      S.statements(el('statements'), ctx);
      restoreFocus('badge:' + id);
    },
    showTransfers(merchant, accountId) {
      Object.assign(state.table, { cats: null, date: null, search: '', merchant, acct: String(accountId), kind: 'transfer', page: 0 });
      // Loose ends are listed across all dates; widen the period if none fall inside it.
      const sel = M.select(ix, state);
      if (!sel.txF.some((t) => t.merchant === merchant && t.account_id === accountId && t.kind === 'transfer')) state.period = 'all';
      render();
      scrollToTable('chip:merchant');
    },
    setCompare(k) {
      state.cmpMonth = k;
      safe('compare', () => { ctx.cmp = S.compare(el('compare'), ctx); });
      renderTools();
      drawCharts();
      restoreFocus(document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.focusKey : 'cmp-select');
    },
    toggleStatements() {
      state.stmtAll = !state.stmtAll;
      S.statements(el('statements'), ctx);
      restoreFocus('stmt-more');
    },
    toggleRecurring(key) {
      state.recOpen = state.recOpen || {};
      state.recOpen[key] = !state.recOpen[key];
      S.recurring(el('recurring'), ctx);
      restoreFocus('rec:' + key);
    },
    dismissUploads() {
      state.uploads = null;
      S.uploads(el('upload-results'), null, api);
      el('add-btn').focus();
    },
  };
  N.app = api;

  // ------------------------------------------------------------ upload
  function pickFiles() { el('file-input').click(); }

  async function upload(files) {
    if (!files.length) return;
    state.uploads = { pending: files.map((f) => f.name) };
    const box = el('upload-results');
    S.uploads(box, state.uploads, api);
    el('state-msg').hidden = true;
    box.scrollIntoView({ block: 'nearest' });
    try {
      const res = await N.api.upload(files);
      state.uploads = { results: (res && res.results) || [] };
    } catch (err) {
      state.uploads = { results: files.map((f) => ({ filename: f.name, status: 'error', message: err.message })) };
    }
    S.uploads(box, state.uploads, api);
    await load(!ix);
  }

  function initDrop() {
    const overlay = el('drop-overlay');
    let depth = 0;
    const hasFiles = (e) => e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], 'Files') !== -1;
    window.addEventListener('dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth++; overlay.hidden = false; });
    window.addEventListener('dragover', (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
    window.addEventListener('dragleave', (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
    window.addEventListener('drop', (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      overlay.hidden = true;
      upload(Array.from(e.dataTransfer.files || []));
    });
  }

  async function reparse() {
    const b = el('reparse-btn');
    b.disabled = true;
    b.textContent = 'Re-reading…';
    try {
      const res = await N.api.reparse();
      flash('Re-read ' + N.plural((res && res.statements) || 0, 'statement') + '. Your edits and rules were kept.');
      await load();
    } catch (err) {
      flash('Couldn’t re-read statements: ' + err.message);
    } finally {
      b.disabled = false;
      b.textContent = 'Re-read all statements';
    }
  }

  // ------------------------------------------------------------ export
  function exportCSV() {
    if (!ctx) return;
    const name = 'naamu-transactions-' + (state.period === 'all' ? 'all' : state.period) + '.csv';
    const n = T.exportCSV(ctx, name);
    flash('Exported ' + N.plural(n, 'transaction') + ' to ' + name + '.');
  }

  // ------------------------------------------------------------ theme
  function effectiveTheme() {
    const t = document.documentElement.getAttribute('data-theme');
    if (t) return t;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  function syncThemeButton() {
    const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    el('theme-btn').setAttribute('aria-label', 'Switch to ' + next + ' theme');
    el('theme-btn').title = 'Switch to ' + next + ' theme';
  }
  function toggleTheme() {
    const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    N.storage.set('naamu-theme', next);
    syncThemeButton();
  }

  document.addEventListener('DOMContentLoaded', boot);
})();
