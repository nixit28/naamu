/* Namu — dashboard sections other than the charts and the transactions table. */
(function () {
  'use strict';
  const N = window.Namu;
  const { h, s, model: M, charts: C } = N;
  const S = (N.sections = {});

  const empty = (title, body) => h('div', { class: 'section-empty' },
    h('p', { class: 'section-empty__title', text: title }), body ? h('p', { class: 'section-empty__body', text: body }) : null);
  S.emptyBlock = empty;

  function richText(parts) {
    return parts.map((p) => (typeof p === 'string' ? p : h('strong', { text: p.b })));
  }

  // ------------------------------------------------------------ KPI tiles
  S.kpis = function (el, ctx) {
    const { ix, sel, tot, state } = ctx;
    N.clear(el);
    const single = sel.selMonths.length === 1 ? sel.selMonths[0] : null;
    const prevKey = single ? N.shiftMonth(single, -1) : null;
    const prev = prevKey && ix.months.includes(prevKey) ? M.totals(sel.txA.filter((t) => N.monthKey(t.date) === prevKey)) : null;
    const monthly = M.byMonth(sel.txA, ix.months.slice(-12));
    const selSet = new Set(sel.selMonths);
    const spark = (name, f, fmt) => C.sparkline(monthly.map((m) => ({ key: m.key, v: f(m), selected: selSet.has(m.key), label: fmt(f(m)) })), { name });
    const prevName = prevKey ? N.monthLabel(prevKey) : '';
    const purchases = N.sum(Array.from(tot.cats.values()), (c) => c.count);

    // goodWhenUp: true / false, or null for a neutral measure (left in checking).
    function delta(cur, before, goodWhenUp, fmt) {
      if (!prev || !isFinite(cur) || !isFinite(before)) return null;
      const d = cur - before;
      if (Math.abs(d) < 0.005) return h('p', { class: 'kpi__delta kpi__delta--flat' }, N.icon('dash'), 'Same as ' + prevName);
      const good = goodWhenUp == null ? null : goodWhenUp ? d > 0 : d < 0;
      return h('p', { class: 'kpi__delta ' + (good == null ? 'kpi__delta--flat' : good ? 'kpi__delta--good' : 'kpi__delta--bad') },
        N.icon(d > 0 ? 'up' : 'down'), h('span', { text: fmt(d) + ' vs ' + prevName }),
        good == null ? null : h('span', { class: 'sr-only', text: good ? ' (better)' : ' (worse)' }));
    }
    const usd = (v) => N.money(v, { cents: false });
    const signed = (v) => N.money(v, { cents: false, sign: true });
    const hasSaving = tot.putIn > 0.005 || tot.takenOut > 0.005;
    const rateText = !isFinite(tot.rate) ? 'Savings rate needs income in the period'
      : tot.saved >= 0 ? 'Savings rate ' + N.pct(tot.rate) + ' of money in'
        : 'More came out of savings than went in';
    const tiles = [
      { label: 'Money in', key: 'in', value: usd(tot.in), exact: N.money(tot.in),
        sub: tot.sources.size ? 'income from ' + N.plural(tot.sources.size, 'source') : 'no income recorded',
        delta: prev && delta(tot.in, prev.in, true, signed),
        spark: spark('Money in', (m) => m.in, usd) },
      { label: 'Spent', key: 'out', value: usd(tot.out), exact: N.money(tot.out),
        sub: N.plural(purchases, 'purchase') + ', net of refunds',
        delta: prev && delta(tot.out, prev.out, false, signed),
        spark: spark('Spent', (m) => m.out, usd) },
      { label: 'Saved & invested', key: 'save', value: hasSaving ? signed(tot.saved) : usd(0), exact: N.money(tot.saved, { sign: true }) + ' net',
        sub: hasSaving ? 'put in ' + usd(tot.putIn) + ' · took out ' + usd(tot.takenOut) : 'nothing moved to savings or investments',
        note: hasSaving ? rateText : null,
        delta: prev && delta(tot.saved, prev.saved, true, signed),
        spark: spark('Saved & invested', (m) => m.saved, signed) },
      { label: 'Left in checking', key: 'left', value: signed(tot.left), exact: N.money(tot.left, { sign: true }),
        sub: tot.left >= 0 ? 'stays as cash, not counted as savings' : 'checking balance went down by this much',
        delta: prev && delta(tot.left, prev.left, null, signed),
        spark: spark('Left in checking', (m) => m.left, signed) },
    ];
    for (const t of tiles) {
      el.appendChild(h('article', { class: 'kpi kpi--' + t.key },
        h('p', { class: 'kpi__label' }, h('span', { class: 'kpi__key kpi__key--' + t.key, 'aria-hidden': 'true' }), t.label),
        h('p', { class: 'kpi__value', title: t.exact, text: t.value }),
        h('p', { class: 'kpi__sub', text: t.sub }),
        t.note ? h('p', { class: 'kpi__note', text: t.note }) : null,
        h('div', { class: 'kpi__foot' }, t.delta || h('p', { class: 'kpi__delta kpi__delta--none', text: single ? 'No earlier month to compare' : periodText(state, sel) }), t.spark)));
    }
  };
  function periodText(state, sel) {
    if (!sel.selMonths.length) return '';
    if (sel.selMonths.length === 1) return N.monthLabel(sel.selMonths[0], { long: true, year: true });
    const a = sel.selMonths[0], z = sel.selMonths[sel.selMonths.length - 1];
    return N.monthLabel(a, { year: a.slice(0, 4) !== z.slice(0, 4) }) + ' – ' + N.monthLabel(z, { year: true });
  }
  S.periodText = periodText;

  // ------------------------------------------------------------ compared with last month
  S.compare = function (el, ctx) {
    const { ix, sel, state, app } = ctx;
    N.clear(el);
    const nav = N.$('[data-compare-nav]');
    if (nav) N.clear(nav);
    const sub = N.$('#compare-sub');
    const pair = M.comparePair(ix, sel, state.cmpMonth);
    if (pair.months.length < 2) {
      if (sub) sub.textContent = 'Month against month, once there are two.';
      el.appendChild(empty('Add next month’s statements to compare', 'With two months of statements, this shows what changed in money in, spending and savings.'));
      return null;
    }
    const lab = (k) => N.monthLabel(k, { year: true });
    if (sub) sub.textContent = lab(pair.cur) + ' vs ' + lab(pair.prev);
    if (nav && pair.options.length) {
      const idx = pair.options.indexOf(pair.cur);
      const go = (k) => app.setCompare(k);
      nav.appendChild(h('div', { class: 'pair-nav', role: 'group', 'aria-label': 'Choose months to compare' },
        h('button', { type: 'button', class: 'btn btn--quiet btn--sm btn--icon-sm', 'aria-label': 'Earlier pair', 'data-focus-key': 'cmp-prev',
          disabled: idx <= 0 && pair.options.includes(pair.cur), onclick: () => go(pair.options[Math.max(0, idx < 0 ? pair.options.length - 1 : idx - 1)]) }, h('span', { 'aria-hidden': 'true', text: '‹' })),
        h('select', { class: 'select select--sm', 'aria-label': 'Months to compare', 'data-focus-key': 'cmp-select', onchange: (e) => go(e.target.value) },
          pair.options.slice().reverse().map((k) => h('option', { value: k, selected: k === pair.cur, text: N.monthLabel(k) + ' vs ' + N.monthLabel(N.shiftMonth(k, -1), { year: true }) }))
            .concat(pair.options.includes(pair.cur) ? [] : [h('option', { value: pair.cur, selected: true, text: N.monthLabel(pair.cur) + ' vs ' + lab(pair.prev) })])),
        h('button', { type: 'button', class: 'btn btn--quiet btn--sm btn--icon-sm', 'aria-label': 'Later pair', 'data-focus-key': 'cmp-next',
          disabled: idx < 0 || idx >= pair.options.length - 1, onclick: () => go(pair.options[idx + 1]) }, h('span', { 'aria-hidden': 'true', text: '›' }))));
    }
    if (!pair.hasPrev) {
      el.appendChild(empty('No statements for ' + N.monthLabel(pair.prev, { long: true, year: true }), 'There’s nothing to compare ' + N.monthLabel(pair.cur, { long: true }) + ' with. Pick another pair above, or add that month’s statements.'));
      return null;
    }
    const c = M.compare(ix, sel, pair.cur, pair.prev);
    // honesty first: name why either month is partial
    const notes = [[pair.cur, c.gCur], [pair.prev, c.gPrev]].filter(([, g]) => g.partial);
    if (notes.length) {
      el.appendChild(h('div', { class: 'cmp-note', role: 'note' }, N.icon('info'),
        h('p', null, notes.map(([k, g], i) => [i ? ' ' : '', h('strong', { text: N.monthLabel(k, { long: true }) + ' is partial' }), ': ' + g.text + '.']),
          ' The comparison will change once you add ' + (notes.length > 1 ? 'those statements.' : 'it.'))));
    }
    const pct = (f) => (f.pct != null ? (f.pct >= 0 ? '+' : '') + N.pct(f.pct) : f.signFlip ? (f.cur < 0 ? 'now negative' : 'back above zero') : Math.abs(f.cur) > 0.005 ? 'new' : '—');
    el.appendChild(h('dl', { class: 'cmp-figs' }, c.figures.map((f) => {
      const flat = Math.abs(f.diff) < 0.5;
      const item = h('div', { class: 'cmp-fig', tabindex: '0' },
        h('dt', { class: 'cmp-fig__label' }, h('span', { class: 'kpi__key cmp-key--' + f.key, 'aria-hidden': 'true' }), f.label),
        h('dd', { class: 'cmp-fig__value', text: N.money(f.cur, { cents: false, sign: f.key === 'left' }) }),
        h('dd', { class: 'cmp-fig__prev', text: 'vs ' + N.money(f.prev, { cents: false, sign: f.key === 'left' }) + ' in ' + N.monthLabel(pair.prev) }),
        h('dd', { class: 'cmp-fig__diff' }, flat ? N.icon('dash') : N.icon(f.diff > 0 ? 'up' : 'down'),
          flat ? 'no change' : N.money(f.diff, { cents: false, sign: true }) + ' · ' + pct(f)));
      item._tip = { title: f.label, rows: [{ value: N.money(f.cur, { sign: f.key === 'left' }), label: lab(pair.cur) }, { value: N.money(f.prev, { sign: f.key === 'left' }), label: lab(pair.prev) },
        { value: N.money(f.diff, { sign: true }), label: 'change · ' + pct(f) }] };
      return item;
    })));
    return { pair, c };
  };

  // ------------------------------------------------------------ insights
  S.insights = function (el, ctx) {
    N.clear(el);
    const list = M.insights(ctx.ix, ctx.sel, ctx.tot, ctx.state);
    if (!list.length) { el.appendChild(h('li', { class: 'insight' }, empty('Nothing stands out yet', 'Add a few more statements and patterns will show up here.'))); return; }
    for (const it of list) {
      el.appendChild(h('li', { class: 'insight insight--' + it.tone }, N.icon(it.tone === 'note' ? 'info' : 'chevron', 'insight__icon'), h('p', null, richText(it.parts))));
    }
  };

  // ------------------------------------------------------------ merchants
  S.merchants = function (el, ctx) {
    N.clear(el);
    const rows = M.merchantRows(ctx.sel.txF).slice(0, 8);
    if (!rows.length) { el.appendChild(empty('No spending in this period', 'Try a different month or include more accounts.')); return; }
    const max = rows[0].spend;
    const total = ctx.tot.out;
    const active = ctx.state.table.merchant;
    const ol = h('ol', { class: 'merchants' });
    rows.forEach((r, i) => {
      const btn = h('button', { type: 'button', class: 'merchant-row' + (active === r.name ? ' is-selected' : ''), 'aria-pressed': active === r.name ? 'true' : 'false',
        'data-focus-key': 'merchant:' + r.name, onclick: () => ctx.app.filterMerchant(r.name) },
        h('span', { class: 'merchant-row__rank', text: String(i + 1) }),
        h('span', { class: 'merchant-row__main' },
          h('span', { class: 'merchant-row__name', text: r.name }),
          h('span', { class: 'merchant-row__meta', text: N.plural(r.visits, r.recurring ? 'charge' : 'visit') + ' · ' + r.category })),
        h('span', { class: 'merchant-row__bar' }, C.miniBar(r.spend / max, 88)),
        h('span', { class: 'merchant-row__total', text: N.money(r.spend, { cents: false }) }));
      btn._tip = {
        title: r.name,
        rows: [
          { value: N.money(r.spend), label: total > 0 ? N.pct(r.spend / total) + ' of spending' : '', color: 'var(--out)' },
          { value: r.visits ? N.money(r.spend / r.visits) : '—', label: r.recurring ? 'average charge' : 'average per visit' },
        ],
        note: 'Select to list these transactions.',
      };
      ol.appendChild(h('li', null, btn));
    });
    el.appendChild(ol);
  };

  // ------------------------------------------------------------ recurring
  S.recurring = function (el, ctx) {
    N.clear(el);
    const rows = M.recurring(ctx.ix, ctx.sel.txA, ctx.sel.txF);
    if (!rows.length) { el.appendChild(empty('No repeating charges found', 'Once the same charge shows up in two different months, it will be listed here.')); return; }
    const subs = rows.filter((r) => r.subscription);
    const bills = rows.filter((r) => !r.subscription);
    const monthly = N.sum(rows, (r) => r.monthly);
    el.appendChild(h('div', { class: 'rec-summary' },
      h('p', { class: 'rec-summary__value' }, h('strong', { text: N.money(monthly, { cents: false }) }), ' a month'),
      h('p', { class: 'rec-summary__sub', text: 'about ' + N.money(monthly * 12, { cents: false }) + ' a year across ' + N.plural(rows.length, 'recurring charge') })));
    const LIMIT = 5;
    const group = (key, title, list) => {
      if (!list.length) return null;
      const sub = N.sum(list, (r) => r.monthly);
      const open = ctx.state.recOpen && ctx.state.recOpen[key];
      const shown = open ? list : list.slice(0, LIMIT);
      const more = list.length - shown.length;
      return h('div', { class: 'rec-group' },
        h('p', { class: 'rec-group__head' }, h('span', { text: title + ' (' + list.length + ')' }), h('span', { class: 'num', text: N.money(sub) + ' / mo' })),
        h('ul', { class: 'rec-list' }, shown.map((r) => {
          const a = ctx.ix.acctById.get(r.account_id);
          const li = h('li', { class: 'rec-row' },
            h('span', { class: 'rec-row__main' },
              h('span', { class: 'rec-row__name', text: r.merchant }),
              h('span', { class: 'rec-row__meta', text: r.cadence + ' · ' + N.shortAccount(a) })),
            h('span', { class: 'rec-row__next', text: r.next >= N.iso(N.today()) ? 'next ~' + N.fmtDate(r.next) : 'last ' + N.fmtDate(r.last) }),
            h('span', { class: 'rec-row__amt num', text: N.money(r.monthly) }));
          li._tip = { title: r.merchant, rows: [
            { value: N.money(r.typical), label: 'typical charge' },
            { value: N.money(r.monthly), label: 'per month' },
            { value: N.fmtDate(r.last), label: 'last charged' },
          ], note: N.plural(r.count, 'charge') + ' seen · ' + r.category };
          return li;
        })),
        list.length > LIMIT ? h('button', { type: 'button', class: 'btn btn--link btn--sm rec-more', 'data-focus-key': 'rec:' + key, 'aria-expanded': open ? 'true' : 'false',
          onclick: () => ctx.app.toggleRecurring(key) }, open ? 'Show fewer' : 'Show ' + more + ' more') : null);
    };
    const g1 = group('subs', 'Subscriptions', subs);
    const g2 = group('bills', 'Bills & other regular charges', bills);
    if (g1) el.appendChild(g1);
    if (g2) el.appendChild(g2);
  };

  // ------------------------------------------------------------ accounts & coverage
  S.accounts = function (el, ctx) {
    const { ix, sel } = ctx;
    N.clear(el);
    if (!sel.accts.length) { el.appendChild(empty('No accounts selected', 'Turn an account back on in the filters above.')); return; }
    const withStmts = sel.accts.filter((a) => ix.data.statements.some((st) => st.account_id === a.id));
    const allStarts = ix.data.statements.map((st) => st.period_start).sort();
    const t0 = N.addDays(N.parseDate(allStarts[0] || N.iso(N.today())), -3);
    const t1 = N.addDays(N.today(), 6);
    const highlight = sel.selMonths.length && sel.selMonths.length < ix.months.length
      ? [N.monthStart(sel.selMonths[0]), N.monthEnd(sel.selMonths[sel.selMonths.length - 1])] : null;
    const list = h('div', { class: 'acct-list' });
    const axisHost = h('div', { class: 'acct-axis' }, h('span', { class: 'acct-axis__spacer' }), h('div', { class: 'acct-axis__svg' }));
    list.appendChild(axisHost);
    const timelines = [];
    for (const a of withStmts) {
      const st = M.accountStatus(ix, a);
      const facts = h('dl', { class: 'acct-facts' });
      const fact = (k, v, extra) => facts.appendChild(h('div', { class: 'acct-fact' }, h('dt', { text: k }), h('dd', null, v, extra || null)));
      if (a.type === 'credit') {
        if (st.balance != null) fact('Balance', N.money(st.balance), h('span', { class: 'muted', text: ' on ' + N.fmtDate(st.latest.period_end) }));
        if (st.due) {
          let status;
          if (st.paid) status = h('span', { class: 'status status--good' }, N.icon('check'), 'Paid ' + N.money(st.paid.amount) + ' on ' + N.fmtDate(st.paid.date));
          else if (st.dueIn < 0) status = h('span', { class: 'status status--muted' }, N.icon('clock'), 'No payment seen yet');
          else status = h('span', { class: 'status status--muted' }, N.icon('clock'), st.dueIn === 0 ? 'Due today' : 'in ' + N.plural(st.dueIn, 'day'));
          fact('Due', N.fmtDate(st.due) + ' ', status);
        }
        if (st.minimum != null) fact('Minimum', N.money(st.minimum));
        if (st.util != null) {
          const m = C.meter(st.util, { width: 96 });
          const label = m.level === 'ok' ? '' : m.level === 'warning' ? ' · above 30%' : ' · above 50%';
          fact('Credit used', h('span', { class: 'meter-wrap' }, m.svg, h('span', { class: 'meter-txt' },
            m.level !== 'ok' ? N.icon('alert', 'meter-icon meter-icon--' + m.level) : null,
            N.pct(st.util) + ' of ' + N.money(st.limit, { cents: false }) + label)));
        }
      } else if (st.balance != null) {
        fact('Balance', N.money(st.balance), h('span', { class: 'muted', text: ' on ' + N.fmtDate(st.latest.period_end) }));
      }
      const holder = N.personName(a.holder);
      const tlHost = h('div', { class: 'acct__timeline' });
      const row = h('article', { class: 'acct' },
        h('div', { class: 'acct__info' },
          h('p', { class: 'acct__name', text: N.shortAccount(a) }),
          h('p', { class: 'acct__meta', text: [a.institution, a.type === 'credit' ? 'Credit card' : a.type === 'checking' ? 'Checking' : a.type === 'savings' ? 'Savings' : a.type, holder].filter(Boolean).join(' · ') }),
          facts),
        tlHost);
      if (st.nudge) {
        row.appendChild(h('p', { class: 'nudge' }, N.icon('clock'),
          h('span', null, 'A new statement has probably closed (the period after ', h('strong', { text: N.fmtDate(st.nudge.since) }), '). Download it from ' + a.institution + ' and add it here.'),
          h('button', { type: 'button', class: 'btn btn--quiet btn--sm', 'data-focus-key': 'nudge:' + a.id, onclick: () => ctx.app.pickFiles() }, 'Add statement')));
      }
      list.appendChild(row);
      timelines.push({ host: tlHost, a, st });
    }
    el.appendChild(list);

    const draw = () => {
      const W = Math.max(160, Math.floor((timelines[0] && timelines[0].host.clientWidth) || 300));
      N.clear(axisHost.lastChild).appendChild(C.timelineAxis(W, t0, t1));
      for (const tl of timelines) {
        const segs = tl.st.stmts.map((x) => ({ start: x.period_start, end: x.period_end,
          tip: { title: x.filename, rows: [{ value: N.fmtRange(x.period_start, x.period_end), label: '' }, { value: N.plural(x.txn_count, 'transaction'), label: '' }],
            note: x.reconciled === true ? 'Totals match the statement' : x.reconciled === false ? 'Some totals do not match' : 'Unverified (generic reader)' } }));
        const gaps = tl.st.gaps.map((g) => ({ start: g[0], end: g[1], tip: { title: 'Missing statement', rows: [{ value: N.fmtRange(g[0], g[1]), label: '' }], note: 'No statement covers these dates.' } }));
        let due = null;
        if (tl.st.nudge) {
          const a = N.addDays(N.parseDate(tl.st.latest.period_end), 1);
          due = { start: N.iso(a), end: N.iso(N.today() < N.addDays(a, 29) ? N.today() : N.addDays(a, 29)),
            tip: { title: 'Probably available', rows: [{ value: 'Period after ' + N.fmtDate(tl.st.latest.period_end), label: '' }], note: 'Download it from ' + tl.a.institution + ' and add it.' } };
        }
        N.clear(tl.host).appendChild(C.timeline(W, t0, t1, segs, gaps, due, highlight));
      }
    };
    ctx.afterLayout.push(draw);

    // loose ends: unmatched transfers
    const loose = M.looseEnds(ix, sel.accts);
    if (loose.length) {
      const ul = h('ul', { class: 'loose' });
      for (const g of loose.slice(0, 6)) {
        const n = g.txns.length;
        const when = n === 1 ? ' on ' + N.fmtDate(g.txns[0].date) : ' in ' + N.plural(n, g.category === 'Transfer' ? 'transfer' : 'payment');
        let parts;
        if (g.dir === 'out' && g.target) {
          parts = [h('strong', { text: N.money(g.total) }), ' paid to ' + N.shortAccount(g.target) + when + ', but no ' + g.target.institution + ' statement covers ' + (n === 1 ? 'that date' : 'those dates') + '. Add it to match the payment.'];
        } else if (g.dir === 'out' && g.category === 'Transfer') {
          parts = [h('strong', { text: N.money(g.total) }), ' moved to ' + g.merchant.replace(/^(online\s+)?transfer\s+to\s+/i, '') + when + '. Add that account’s statements to follow it.'];
        } else if (g.dir === 'out' && g.institution) {
          parts = [h('strong', { text: N.money(g.total) }), ' card payment to ' + g.institution + when + ' didn’t match any uploaded ' + g.institution + ' card statement. Add the statement that covers ' + (n === 1 ? 'that date' : 'those dates') + ' to match it.'];
        } else if (g.dir === 'out') {
          parts = [h('strong', { text: N.money(g.total) }), ' paid to ' + g.payee + when + ', but no ' + g.payee + ' statement is uploaded. Add it to see what it bought.'];
        } else {
          parts = [N.shortAccount(g.from) + ' received ', h('strong', { text: N.money(g.total) }), when + ' from an account whose statement for that date isn’t uploaded yet.'];
        }
        ul.appendChild(h('li', { class: 'loose__item' }, N.icon('link', 'loose__icon'), h('p', null, parts),
          h('button', { type: 'button', class: 'btn btn--link btn--sm', 'data-focus-key': 'loose:' + g.merchant + g.from.id, onclick: () => ctx.app.showTransfers(g.merchant, g.from.id) }, 'Show')));
      }
      el.appendChild(h('div', { class: 'loose-wrap' }, h('h3', { class: 'subhead', text: 'Loose ends' }),
        h('p', { class: 'card__sub', text: 'Transfers with no matching side yet. They are left out of money in and out either way.' }), ul));
    }
  };

  // ------------------------------------------------------------ statements
  function reconBadge(st) {
    if (st.reconciled === true) return { cls: 'good', icon: 'check', text: 'Totals match' };
    if (st.reconciled === false) return { cls: 'bad', icon: 'alert', text: 'Totals differ' };
    return { cls: 'neutral', icon: 'info', text: 'Unverified' };
  }
  S.reconBadge = reconBadge;

  S.statements = function (el, ctx) {
    const { ix, sel, state, app } = ctx;
    N.clear(el);
    const all = ix.data.statements.slice().sort((a, b) => (a.period_end < b.period_end ? 1 : a.period_end > b.period_end ? -1 : a.account_id - b.account_id));
    const on = new Set(sel.accts.map((a) => a.id));
    let pStart = null, pEnd = null;
    if (sel.selMonths.length && sel.selMonths.length < ix.months.length) {
      pStart = sel.selMonths[0] + '-01';
      pEnd = N.iso(N.monthEnd(sel.selMonths[sel.selMonths.length - 1]));
    }
    const rows = all.filter((st) => on.has(st.account_id) && (!pStart || (st.period_end >= pStart && st.period_start <= pEnd)));
    const sub = N.$('#stmt-sub');
    if (sub) sub.textContent = rows.length === all.length ? 'Every file you have added, and whether its totals check out.'
      : 'Showing ' + rows.length + ' of ' + all.length + ' statements that overlap the selected period and accounts.';
    if (!rows.length) { el.appendChild(empty('No statements here', 'None of the statements overlap this period for the selected accounts.')); return; }
    const table = h('table', { class: 'data-table stmt-table' },
      h('caption', { class: 'sr-only', text: 'Uploaded statements' }),
      h('thead', null, h('tr', null,
        h('th', { scope: 'col', text: 'File' }), h('th', { scope: 'col', text: 'Account' }), h('th', { scope: 'col', text: 'Period' }),
        h('th', { scope: 'col', class: 'num', text: 'Transactions' }), h('th', { scope: 'col', text: 'Check' }),
        h('th', { scope: 'col', class: 'stmt-actions-h' }, h('span', { class: 'sr-only', text: 'Actions' })))));
    const tbody = h('tbody');
    const LIMIT = 10;
    const showAll = state.stmtAll || rows.length <= LIMIT + 2;
    // Keep an open confirm/expander visible even when it falls past the fold.
    const visible = showAll ? rows : rows.filter((st, i) => i < LIMIT || st.id === state.confirmDel || state.expanded.has(st.id));
    for (const st of visible) {
      const a = ix.acctById.get(st.account_id);
      const badge = reconBadge(st);
      const open = state.expanded.has(st.id);
      const confirming = state.confirmDel === st.id;
      const detailsId = 'stmt-checks-' + st.id;
      const actions = h('button', { type: 'button', class: 'btn btn--ghost btn--sm' + (confirming ? ' is-active' : ''), 'data-focus-key': 'del:' + st.id,
        'aria-label': 'Delete statement ' + st.filename, 'aria-expanded': confirming ? 'true' : 'false',
        onclick: () => app.confirmDelete(confirming ? null : st.id, st.id) }, N.icon('trash'), h('span', { class: 'btn__label', text: 'Delete' }));
      tbody.appendChild(h('tr', { class: confirming ? 'is-confirming' : null },
        h('td', { class: 'stmt-file' }, h('span', { class: 'stmt-file__inner' }, N.icon('file', 'stmt-file__icon'), h('span', { class: 'stmt-file__name', title: st.filename, text: st.filename }))),
        h('td', { class: 'nowrap', 'data-label': 'Account', text: N.shortAccount(a) }),
        h('td', { class: 'nowrap', 'data-label': 'Period', text: N.fmtRange(st.period_start, st.period_end) }),
        h('td', { class: 'num', 'data-label': 'Transactions', text: String(st.txn_count) }),
        h('td', { 'data-label': 'Check' }, h('button', { type: 'button', class: 'badge badge--' + badge.cls, 'aria-expanded': open ? 'true' : 'false', 'aria-controls': detailsId,
          'data-focus-key': 'badge:' + st.id, onclick: () => app.toggleChecks(st.id) }, N.icon(badge.icon), badge.text, N.icon('chevron', 'badge__chev'))),
        h('td', { class: 'stmt-actions' }, actions)));
      if (confirming) {
        tbody.appendChild(h('tr', { class: 'confirm-row' }, h('td', { colspan: '6' },
          h('div', { class: 'confirm', role: 'group', 'aria-label': 'Confirm delete' },
            N.icon('alert', 'confirm__icon'),
            h('span', { class: 'confirm__q' }, 'Delete ', h('strong', { text: st.filename }), ' and its ' + N.plural(st.txn_count, 'transaction') + '? Category edits on them are lost; your merchant rules stay.'),
            h('span', { class: 'confirm__btns' },
              h('button', { type: 'button', class: 'btn btn--danger btn--sm', 'data-focus-key': 'del-yes:' + st.id, onclick: () => app.deleteStatement(st.id) }, 'Delete statement'),
              h('button', { type: 'button', class: 'btn btn--quiet btn--sm', 'data-focus-key': 'del-no:' + st.id, onclick: () => app.confirmDelete(null, st.id) }, 'Keep it'))))));
      }
      if (open) {
        let body;
        if (!st.checks || !st.checks.length) {
          body = h('p', { class: 'checks__none', text: 'Read with the generic reader (' + st.parser + '). The statement has no totals Namu knows how to compare, so double-check its transactions.' });
        } else {
          body = h('table', { class: 'checks' },
            h('thead', null, h('tr', null, h('th', { scope: 'col', text: 'Check' }), h('th', { scope: 'col', class: 'num', text: 'On statement' }),
              h('th', { scope: 'col', class: 'num', text: 'Parsed' }), h('th', { scope: 'col', class: 'num', text: 'Difference' }))),
            h('tbody', null, st.checks.map((c) => h('tr', { class: c.ok ? null : 'is-bad' },
              h('th', { scope: 'row' }, h('span', { class: 'status status--' + (c.ok ? 'good' : 'bad') }, N.icon(c.ok ? 'check' : 'alert'), c.label)),
              h('td', { class: 'num', text: N.money(c.expected) }), h('td', { class: 'num', text: N.money(c.parsed) }),
              h('td', { class: 'num', text: c.ok ? '—' : N.money(c.parsed - c.expected, { sign: true }) })))));
        }
        const meta = h('p', { class: 'checks__meta', text: 'Parser ' + st.parser + ' · added ' + N.fmtDate(st.uploaded_at.slice(0, 10), { year: true }) });
        tbody.appendChild(h('tr', { class: 'checks-row', id: detailsId }, h('td', { colspan: '6' }, body, meta)));
      }
    }
    table.appendChild(tbody);
    el.appendChild(h('div', { class: 'table-scroll' }, table));
    if (rows.length > LIMIT + 2) {
      el.appendChild(h('button', { type: 'button', class: 'btn btn--link btn--sm stmt-more', 'data-focus-key': 'stmt-more', 'aria-expanded': showAll ? 'true' : 'false',
        onclick: () => app.toggleStatements() }, showAll ? 'Show fewer' : 'Show all ' + rows.length + ' statements'));
    }
  };

  // ------------------------------------------------------------ empty state
  S.empty = function (el, app) {
    N.clear(el);
    const zone = h('div', { class: 'dropzone', role: 'button', tabindex: '0', 'aria-describedby': 'empty-body',
      onclick: (e) => { if (!e.target.closest('button')) app.pickFiles(); },
      onkeydown: (e) => { if (e.target === zone && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); app.pickFiles(); } } },
      s('svg', { class: 'dropzone__icon', viewBox: '0 0 48 48', 'aria-hidden': 'true' }, s('path', { d: 'M24 31V9m0 0-8 8m8-8 8 8M8 30v6a4 4 0 0 0 4 4h24a4 4 0 0 0 4-4v-6' })),
      h('h1', { class: 'dropzone__title', id: 'empty-title', text: 'Drop your statements here' }),
      h('p', { class: 'dropzone__body', id: 'empty-body', text: 'Add a month or two of PDF statements from your bank and cards. Namu reads them on this computer and builds your dashboard. You can add more any time.' }),
      h('button', { type: 'button', class: 'btn btn--primary btn--lg', onclick: () => app.pickFiles() }, N.icon('upload'), 'Choose PDF files'),
      h('ul', { class: 'banks', 'aria-label': 'Supported statements' },
        h('li', null, h('strong', { text: 'Chase' }), h('span', { text: 'Checking, Freedom, Amazon Prime Visa' })),
        h('li', null, h('strong', { text: 'American Express' }), h('span', { text: 'Blue Cash' })),
        h('li', null, h('strong', { text: 'Citi' }), h('span', { text: 'Costco Anywhere Visa' })),
        h('li', null, h('strong', { text: 'Other banks' }), h('span', { text: 'Read with a generic reader, marked unverified' }))),
      h('p', { class: 'privacy' }, s('svg', { class: 'icon', viewBox: '0 0 20 20', 'aria-hidden': 'true' }, s('path', { d: 'M5.5 9V6.5a4.5 4.5 0 0 1 9 0V9M4.5 9h11v8h-11z' })),
        h('span', null, h('strong', { text: 'Everything stays on this computer.' }), ' No cloud, no sign-in, no network calls.')));
    el.appendChild(zone);
  };

  // ------------------------------------------------------------ upload results
  S.uploads = function (el, up, app) {
    N.clear(el);
    if (!up) { el.hidden = true; return; }
    el.hidden = false;
    const rowsEl = h('ul', { class: 'up-list' });
    let title;
    if (up.pending) {
      title = 'Reading ' + N.plural(up.pending.length, 'file') + '…';
      for (const name of up.pending) rowsEl.appendChild(h('li', { class: 'up-row up-row--pending' }, h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', { class: 'up-row__file', text: name }), h('span', { class: 'up-row__msg', text: 'Reading' })));
    } else {
      const res = up.results || [];
      const imported = res.filter((r) => r.status === 'imported');
      const newTx = N.sum(imported, (r) => r.new_txns || 0);
      title = imported.length ? 'Added ' + N.plural(imported.length, 'statement') + ' · ' + N.plural(newTx, 'new transaction')
        : res.length ? 'Nothing new was added' : 'No files';
      for (const r of res) {
        let icon, body;
        if (r.status === 'imported') {
          icon = h('span', { class: 'up-row__icon up-row__icon--good' }, N.icon('check'));
          const recon = r.reconciled === true ? h('span', { class: 'status status--good' }, N.icon('check'), 'matches statement totals')
            : r.reconciled === false ? h('span', { class: 'status status--bad' }, N.icon('alert'), 'totals don’t match, see Statements')
              : h('span', { class: 'status status--muted' }, N.icon('info'), 'unverified');
          body = [h('span', { class: 'up-row__what' }, h('strong', { text: r.label || 'Statement' }), r.period_start ? ' · ' + N.fmtRange(r.period_start, r.period_end) : ''),
            h('span', { class: 'up-row__msg' }, N.plural(r.new_txns || 0, 'new transaction'), ' · ', recon,
              r.duplicate_txns ? ' · ' + N.plural(r.duplicate_txns, 'duplicate') + ' skipped' : '')];
        } else if (r.status === 'duplicate') {
          icon = h('span', { class: 'up-row__icon up-row__icon--muted' }, N.icon('copy'));
          body = [h('span', { class: 'up-row__what' }, h('strong', { text: 'Already added' })), h('span', { class: 'up-row__msg', text: r.message || 'This file was imported before.' })];
        } else {
          icon = h('span', { class: 'up-row__icon up-row__icon--bad' }, N.icon('alert'));
          body = [h('span', { class: 'up-row__what' }, h('strong', { text: 'Couldn’t read this file' })), h('span', { class: 'up-row__msg', text: r.message || 'Unknown error' })];
        }
        rowsEl.appendChild(h('li', { class: 'up-row up-row--' + r.status }, icon, h('span', { class: 'up-row__file', text: r.filename }), h('span', { class: 'up-row__body' }, body)));
      }
    }
    el.appendChild(h('div', { class: 'up-head' },
      h('p', { class: 'up-head__title', text: title }),
      up.pending ? null : h('button', { type: 'button', class: 'btn btn--quiet btn--sm', onclick: () => app.dismissUploads() }, 'Dismiss')));
    el.appendChild(rowsEl);
  };
})();
