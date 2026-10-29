/* Naamu — transactions table: search, filters, sort, pagination, inline category
   edits with "apply to merchant", and client-side CSV export. */
(function () {
  'use strict';
  const N = window.Naamu;
  const { h, model: M } = N;
  const T = (N.table = {});
  const PAGE = 50;

  const KIND_LABEL = { expense: 'Spending', refund: 'Refunds', income: 'Income', saving: 'Saving', transfer: 'Transfers' };
  const GROUPS = [['Spending', 'expense'], ['Income', 'income'], ['Savings', 'saving'], ['Transfers', 'transfer']];

  /** Apply global + table filters; returns rows sorted. */
  T.rows = function (ctx) {
    const { ix, sel, state } = ctx;
    const f = state.table;
    const q = f.search.trim().toLowerCase();
    const qNum = q.replace(/[$,\s]/g, '');
    const isNum = qNum !== '' && !isNaN(Number(qNum));
    // A month-pair filter (from "Compared with last month") reaches outside the period.
    const base = f.months ? sel.txA.filter((t) => f.months.includes(N.monthKey(t.date))) : sel.txF;
    let rows = base.filter((t) => {
      if (f.cats && !f.cats.names.has(t.category)) return false;
      if (f.merchant && t.merchant !== f.merchant) return false;
      if (f.date && t.date !== f.date) return false;
      if (f.acct && String(t.account_id) !== f.acct) return false;
      if (f.kind && t.kind !== f.kind) return false;
      if (q) {
        if (isNum && Math.abs(t.amount).toFixed(2).includes(qNum)) return true;
        const hay = (t.merchant + ' ' + t.description + ' ' + t.category + ' ' + (t.cardholder || '')).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
    const dir = f.dir === 'asc' ? 1 : -1;
    const acctName = (t) => N.shortAccount(ix.acctById.get(t.account_id));
    const key = {
      date: (t) => t.date, merchant: (t) => t.merchant.toLowerCase(), account: acctName,
      category: (t) => t.category, amount: (t) => t.amount,
    }[f.sort] || ((t) => t.date);
    rows = rows.slice().sort((a, b) => {
      const ka = key(a), kb = key(b);
      if (ka < kb) return -dir;
      if (ka > kb) return dir;
      return (b.date < a.date ? -1 : b.date > a.date ? 1 : b.id - a.id);
    });
    return rows;
  };

  function categorySelect(ctx, t) {
    const groups = { expense: [], income: [], saving: [], transfer: [] };
    for (const c of ctx.ix.data.categories) (groups[c.kind] || groups.expense).push(c.name);
    // Arrow keys on a closed <select> fire `change` on every step, so keyboard
    // edits commit on Enter or blur; pointer picks commit immediately.
    let viaKeyboard = false, committed = false;
    const commit = () => {
      if (committed || sel.value === t.category) return;
      committed = true;
      ctx.app.changeCategory(t, sel.value);
    };
    const sel = h('select', { class: 'cat-select', 'aria-label': 'Category for ' + t.merchant + ' on ' + N.fmtDate(t.date, { year: true }),
      'data-focus-key': 'cat:' + t.id,
      onpointerdown: () => { viaKeyboard = false; },
      onkeydown: (e) => { viaKeyboard = true; if (e.key === 'Enter') commit(); },
      onchange: () => { if (!viaKeyboard) commit(); },
      onblur: commit },
      GROUPS.filter(([, k]) => groups[k].length).map(([label, k]) =>
        h('optgroup', { label }, groups[k].map((n) => h('option', { value: n, selected: n === t.category, text: n })))));
    if (!ctx.ix.catKind.has(t.category)) sel.insertBefore(h('option', { value: t.category, selected: true, text: t.category }), sel.firstChild);
    return sel;
  }

  function tags(ctx, t) {
    const out = [];
    if (t.kind === 'transfer') {
      if (t.transfer_match_id) {
        const o = ctx.ix.txnById.get(t.transfer_match_id);
        const oa = o && ctx.ix.acctById.get(o.account_id);
        const tag = h('span', { class: 'tag tag--match', tabindex: '0', 'aria-label': 'Matched transfer' + (oa ? ' with ' + N.shortAccount(oa) : '') }, N.icon('link'), 'matched');
        tag._tip = { title: 'Matched transfer', rows: [{ value: oa ? N.shortAccount(oa) : 'Other account', label: o ? N.fmtDate(o.date) : '' }],
          note: 'Counted as neither money in nor money out.' };
        out.push(tag);
      } else {
        const tag = h('span', { class: 'tag tag--loose', tabindex: '0', 'aria-label': 'Unmatched transfer' }, 'unmatched');
        tag._tip = { title: 'No matching side yet', rows: [{ value: 'Transfer', label: 'excluded from totals' }], note: 'Usually the other account’s statement isn’t uploaded yet.' };
        out.push(tag);
      }
    } else if (t.kind === 'saving') {
      const tag = h('span', { class: 'tag tag--save', tabindex: '0', 'aria-label': t.amount < 0 ? 'Saving: into ' + t.merchant : 'Saving: taken out of ' + t.merchant },
        t.amount < 0 ? 'into savings' : 'from savings');
      tag._tip = { title: t.amount < 0 ? 'Saved & invested' : 'Taken out of savings', rows: [{ value: N.money(Math.abs(t.amount)), label: (t.amount < 0 ? 'into ' : 'from ') + t.merchant, color: 'var(--save)' }],
        note: t.amount < 0 ? 'Counts toward Saved, not Spent.' : 'Not income: it lowers what you saved.' };
      out.push(tag);
    } else if (t.kind === 'refund') out.push(h('span', { class: 'tag', text: 'refund' }));
    else if (t.kind === 'income') out.push(h('span', { class: 'tag tag--in', text: 'income' }));
    if (t.recurring) out.push(h('span', { class: 'tag', text: 'recurring' }));
    return out;
  }

  function promptRow(ctx, p, cols) {
    const inner = p.done
      ? h('div', { class: 'rule-prompt rule-prompt--done', role: 'status' }, N.icon('check'), h('span', { text: p.message }),
        h('button', { type: 'button', class: 'btn btn--quiet btn--sm', 'data-focus-key': 'rule-ok', onclick: () => ctx.app.dismissRule() }, 'OK'))
      : h('div', { class: 'rule-prompt', role: 'group', 'aria-label': 'Apply category to merchant' },
        h('span', null, 'Apply ', h('strong', { text: p.category }),
          p.count > 2 ? ' to all ' + p.count + ' “' + p.merchant + '” transactions and future uploads?'
            : p.count === 2 ? ' to both “' + p.merchant + '” transactions and future uploads?'
              : ' to future “' + p.merchant + '” transactions too?'),
        h('span', { class: 'rule-prompt__actions' },
          h('button', { type: 'button', class: 'btn btn--primary btn--sm', 'data-focus-key': 'rule-yes', onclick: () => ctx.app.applyRule(p) },
            p.count > 2 ? 'Apply to all ' + p.count : p.count === 2 ? 'Apply to both' : 'Remember it'),
          h('button', { type: 'button', class: 'btn btn--quiet btn--sm', 'data-focus-key': 'rule-no', onclick: () => ctx.app.dismissRule() }, 'Just this one')));
    if (!cols) return inner;
    return h('tr', { class: 'prompt-row' }, h('td', { colspan: String(cols) }, inner));
  }

  T.render = function (el, ctx) {
    const { ix, state, app } = ctx;
    const f = state.table;
    N.clear(el);

    // toolbar ---------------------------------------------------------
    const catSel = h('select', { class: 'select', 'aria-label': 'Filter by category', 'data-focus-key': 'f-cat',
      onchange: (e) => app.setTableFilter('cat', e.target.value) },
      h('option', { value: '', text: 'All categories' }),
      GROUPS.filter(([, kind]) => ix.data.categories.some((c) => c.kind === kind)).map(([label, kind]) =>
        h('optgroup', { label }, ix.data.categories.filter((c) => c.kind === kind).map((c) =>
          h('option', { value: c.name, selected: f.cats && f.cats.names.size === 1 && f.cats.names.has(c.name), text: c.name })))));
    const acctSel = h('select', { class: 'select', 'aria-label': 'Filter by account', 'data-focus-key': 'f-acct',
      onchange: (e) => app.setTableFilter('acct', e.target.value) },
      h('option', { value: '', text: 'All accounts' }),
      ctx.sel.accts.map((a) => h('option', { value: String(a.id), selected: f.acct === String(a.id), text: N.shortAccount(a) })));
    const kindSel = h('select', { class: 'select', 'aria-label': 'Filter by kind', 'data-focus-key': 'f-kind',
      onchange: (e) => app.setTableFilter('kind', e.target.value) },
      h('option', { value: '', text: 'All kinds' }),
      Object.keys(KIND_LABEL).map((k) => h('option', { value: k, selected: f.kind === k, text: KIND_LABEL[k] })));
    const search = h('input', { type: 'search', class: 'search', placeholder: 'Search merchant, description or amount', 'aria-label': 'Search transactions',
      value: f.search, 'data-focus-key': 'f-search', autocomplete: 'off', spellcheck: 'false' });
    search.addEventListener('input', N.debounce(() => app.setSearch(search.value), 140));
    el.appendChild(h('div', { class: 'txn-toolbar' },
      h('label', { class: 'search-wrap' }, h('span', { class: 'sr-only', text: 'Search' }), search), catSel, acctSel, kindSel));

    // chips -----------------------------------------------------------
    const chips = [];
    if (f.cats) chips.push({ key: 'cats', label: (f.cats.names.size === 1 ? 'Category: ' : '') + f.cats.label });
    if (f.merchant) chips.push({ key: 'merchant', label: 'Merchant: ' + f.merchant });
    if (f.date) chips.push({ key: 'date', label: 'Day: ' + N.fmtDate(f.date, { dow: true }) });
    if (f.months) chips.push({ key: 'months', label: 'Months: ' + f.months.map((m) => N.monthLabel(m)).join(' & ') + ' ' + f.months[1].slice(0, 4) });
    if (f.acct) chips.push({ key: 'acct', label: 'Account: ' + N.shortAccount(ix.acctById.get(Number(f.acct))) });
    if (f.kind) chips.push({ key: 'kind', label: 'Kind: ' + KIND_LABEL[f.kind] });
    if (f.search.trim()) chips.push({ key: 'search', label: 'Search: “' + f.search.trim() + '”' });
    const body = h('div', { class: 'txn-body' });
    el.appendChild(body);
    T.renderBody(body, ctx, chips);
  };

  T.renderBody = function (body, ctx, chipsIn) {
    const { ix, state, app } = ctx;
    const f = state.table;
    N.clear(body);
    const chips = chipsIn || [];
    if (chips.length) {
      body.appendChild(h('div', { class: 'filter-chips', role: 'group', 'aria-label': 'Active table filters' },
        chips.map((c) => h('button', { type: 'button', class: 'fchip', 'data-focus-key': 'chip:' + c.key, 'aria-label': 'Remove filter ' + c.label,
          onclick: () => app.clearTableFilter(c.key) }, h('span', { text: c.label }), N.icon('x'))),
        chips.length > 1 ? h('button', { type: 'button', class: 'btn btn--link btn--sm', 'data-focus-key': 'chip:all', onclick: () => app.clearTableFilter('all') }, 'Clear all') : null));
    }
    const rows = T.rows(ctx);
    state._tableRows = rows;
    const tot = M.totals(rows);
    const transfers = rows.filter((t) => t.kind === 'transfer').length;
    const savings = rows.some((t) => t.kind === 'saving');
    body.appendChild(h('p', { class: 'txn-summary', role: 'status' },
      h('strong', { text: N.plural(rows.length, 'transaction') }),
      rows.length ? [' · in ', h('span', { class: 'num', text: N.money(tot.in) }), ' · spent ', h('span', { class: 'num', text: N.money(tot.out) }),
        savings ? [' · saved ', h('span', { class: 'num', text: N.money(tot.saved, { sign: true }) })] : '',
        transfers ? ' · ' + N.plural(transfers, 'transfer') + ' not counted' : ''] : ''));

    if (!rows.length) {
      body.appendChild(h('div', { class: 'section-empty' }, h('p', { class: 'section-empty__title', text: 'No transactions match' }),
        h('p', { class: 'section-empty__body', text: 'Try clearing a filter or changing the period above.' }),
        chips.length ? h('button', { type: 'button', class: 'btn btn--quiet btn--sm', onclick: () => app.clearTableFilter('all') }, 'Clear table filters') : null));
      if (state.pendingRule) body.appendChild(promptRow(ctx, state.pendingRule, 0));
      return;
    }
    const pages = Math.max(1, Math.ceil(rows.length / PAGE));
    if (f.page >= pages) f.page = pages - 1;
    const slice = rows.slice(f.page * PAGE, f.page * PAGE + PAGE);

    const th = (key, label, cls) => {
      const active = f.sort === key;
      return h('th', { scope: 'col', class: cls || null, 'aria-sort': active ? (f.dir === 'asc' ? 'ascending' : 'descending') : 'none' },
        h('button', { type: 'button', class: 'sort' + (active ? ' is-active' : ''), 'data-focus-key': 'sort:' + key, onclick: () => app.sortTable(key) },
          label, h('span', { class: 'sort__arrow', 'aria-hidden': 'true', text: active ? (f.dir === 'asc' ? '↑' : '↓') : '↕' })));
    };
    const table = h('table', { class: 'data-table txn-table' },
      h('caption', { class: 'sr-only', text: 'Transactions, page ' + (f.page + 1) + ' of ' + pages }),
      h('thead', null, h('tr', null, th('date', 'Date'), th('merchant', 'Merchant'), th('account', 'Account'), th('category', 'Category'), th('amount', 'Amount', 'num'))));
    const tbody = h('tbody');
    const p = state.pendingRule;
    let promptShown = false;
    for (const t of slice) {
      const a = ix.acctById.get(t.account_id);
      const holder = t.cardholder ? M.firstNames([N.personName(t.cardholder)]).get(N.personName(t.cardholder)) : null;
      const src = t.category_source === 'rule' ? h('span', { class: 'src', title: 'Set by your rule for ' + t.merchant, text: 'rule' })
        : t.category_source === 'user' ? h('span', { class: 'src', title: 'You set this', text: 'edited' }) : null;
      tbody.appendChild(h('tr', { class: 'txn kind-' + t.kind },
        h('td', { class: 'txn-date', 'data-label': 'Date', text: N.fmtDate(t.date) }),
        h('td', { class: 'txn-merchant' },
          h('span', { class: 'txn-merchant__line' }, h('span', { class: 'txn-merchant__name', text: t.merchant }), tags(ctx, t)),
          h('span', { class: 'txn-merchant__desc', title: t.description, text: t.description })),
        h('td', { class: 'txn-acct', 'data-label': 'Account' }, N.shortAccount(a), holder ? h('span', { class: 'txn-holder', text: ' · ' + holder.split(' ')[0] }) : null),
        h('td', { class: 'txn-cat' }, categorySelect(ctx, t), src),
        h('td', { class: 'num txn-amt' + (t.amount > 0 ? ' is-pos' : ''), text: N.money(t.amount, { sign: true }) })));
      if (p && p.id === t.id) { tbody.appendChild(promptRow(ctx, p, 5)); promptShown = true; }
    }
    table.appendChild(tbody);
    if (p && !promptShown) body.appendChild(promptRow(ctx, p, 0));
    body.appendChild(h('div', { class: 'table-scroll' }, table));

    const from = f.page * PAGE + 1, to = Math.min(rows.length, from + PAGE - 1);
    body.appendChild(h('nav', { class: 'pager', 'aria-label': 'Transaction pages' },
      h('span', { class: 'pager__info', text: N.num(from) + '–' + N.num(to) + ' of ' + N.num(rows.length) }),
      h('span', { class: 'pager__btns' },
        h('button', { type: 'button', class: 'btn btn--quiet btn--sm', 'data-focus-key': 'page-prev', disabled: f.page === 0, onclick: () => app.setPage(f.page - 1) }, 'Previous'),
        h('span', { class: 'pager__page', text: 'Page ' + (f.page + 1) + ' of ' + pages }),
        h('button', { type: 'button', class: 'btn btn--quiet btn--sm', 'data-focus-key': 'page-next', disabled: f.page >= pages - 1, onclick: () => app.setPage(f.page + 1) }, 'Next'))));
  };

  // ------------------------------------------------------------ CSV
  function csvCell(v, numeric) {
    let s = v == null ? '' : String(v);
    // Spreadsheet formula injection guard for text cells.
    if (!numeric && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }
  T.exportCSV = function (ctx, filename) {
    const rows = T.rows(ctx);
    const head = ['Date', 'Merchant', 'Description', 'Account', 'Cardholder', 'Category', 'Kind', 'Amount', 'Matched transfer'];
    const lines = [head.join(',')];
    for (const t of rows) {
      const a = ctx.ix.acctById.get(t.account_id);
      lines.push([
        csvCell(t.date, true), csvCell(t.merchant), csvCell(t.description), csvCell(a ? a.label : ''), csvCell(t.cardholder || ''),
        csvCell(t.category), csvCell(t.kind), csvCell(t.amount.toFixed(2), true), csvCell(t.kind === 'transfer' ? (t.transfer_match_id ? 'yes' : 'no') : ''),
      ].join(','));
    }
    const blob = new Blob(['﻿' + lines.join('\r\n') + '\r\n'], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = h('a', { href: url, download: filename });
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
    return rows.length;
  };
})();
