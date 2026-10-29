/* Naamu — pure calculations over /api/data. Follows docs/CONTRACT.md:
   money_in = Σ income; spent (money_out) = −Σ (expense + refund);
   saved = −Σ saving (net: put_in − taken_out, can be negative);
   left_in_checking = in − spent − saved (cash, not savings); savings_rate = saved / in.
   Transfers count toward none of them. */
(function () {
  'use strict';
  const N = window.Naamu;
  const M = (N.model = {});

  M.SPEND_KINDS = { expense: 1, refund: 1 };

  /** Build lookups once per fetch. */
  M.index = function (data) {
    const acctById = new Map(data.accounts.map((a) => [a.id, a]));
    const stmtById = new Map(data.statements.map((s) => [s.id, s]));
    const txnById = new Map(data.transactions.map((t) => [t.id, t]));
    const catKind = new Map(data.categories.map((c) => [c.name, c.kind]));
    const monthSet = new Set(data.transactions.map((t) => N.monthKey(t.date)));
    const months = Array.from(monthSet).sort();
    // merged statement coverage per account: [[startDate, endDate], ...]
    const coverage = new Map();
    for (const a of data.accounts) coverage.set(a.id, []);
    const sorted = data.statements.slice().sort((x, y) => (x.period_start < y.period_start ? -1 : 1));
    for (const s of sorted) {
      const list = coverage.get(s.account_id) || [];
      const a = N.parseDate(s.period_start), b = N.parseDate(s.period_end);
      const last = list[list.length - 1];
      if (last && N.dayDiff(last[1], a) <= 1) { if (b > last[1]) last[1] = b; }
      else list.push([a, b]);
      coverage.set(s.account_id, list);
    }
    // merchant counts for "apply to all N"
    const merchantCount = new Map();
    for (const t of data.transactions) merchantCount.set(t.merchant, (merchantCount.get(t.merchant) || 0) + 1);
    return { data, acctById, stmtById, txnById, catKind, months, coverage, merchantCount };
  };

  M.periodMonths = function (period, months) {
    if (period === 'all' || !months.length) return months.slice();
    if (period === 'last3') return months.slice(-3);
    return months.includes(period) ? [period] : [];
  };

  M.select = function (ix, state) {
    const on = (t) => !state.acctOff.has(t.account_id);
    const txA = ix.data.transactions.filter(on);
    const selMonths = M.periodMonths(state.period, ix.months);
    const mset = new Set(selMonths);
    const txF = txA.filter((t) => mset.has(N.monthKey(t.date)));
    const accts = ix.data.accounts.filter((a) => !state.acctOff.has(a.id));
    return { txA, txF, selMonths, accts };
  };

  M.totals = function (txns) {
    let inn = 0, out = 0, refunds = 0, refundCount = 0, putIn = 0, takenOut = 0;
    const cats = new Map();
    const sources = new Map();
    const dests = new Map();   // savings destination -> { put, taken, count }
    for (const t of txns) {
      if (t.kind === 'income') {
        inn += t.amount;
        sources.set(t.merchant, (sources.get(t.merchant) || 0) + t.amount);
      } else if (t.kind === 'saving') {
        const d = dests.get(t.merchant) || { name: t.merchant, put: 0, taken: 0, count: 0 };
        if (t.amount < 0) { putIn -= t.amount; d.put -= t.amount; } else { takenOut += t.amount; d.taken += t.amount; }
        d.count++;
        dests.set(t.merchant, d);
      } else if (M.SPEND_KINDS[t.kind]) {
        out -= t.amount;
        if (t.kind === 'refund') { refunds += t.amount; refundCount++; }
        const c = cats.get(t.category) || { name: t.category, spend: 0, count: 0, refunds: 0 };
        c.spend -= t.amount;
        if (t.kind === 'expense') c.count++;
        else c.refunds += t.amount;
        cats.set(t.category, c);
      }
    }
    const saved = putIn - takenOut;
    const left = inn - out - saved;
    return { in: inn, out, saved, putIn, takenOut, left, rate: inn > 0 ? saved / inn : NaN,
      cats, sources, dests, refunds, refundCount };
  };

  M.byMonth = function (txns, months) {
    const groups = new Map(months.map((m) => [m, []]));
    for (const t of txns) {
      const k = N.monthKey(t.date);
      if (groups.has(k)) groups.get(k).push(t);
    }
    return months.map((m) => Object.assign({ key: m }, M.totals(groups.get(m))));
  };

  // ------------------------------------------------------------ coverage
  function covers(list, d) {
    for (const [a, b] of list) if (d >= a && d <= b) return true;
    return false;
  }
  M.dayCovered = function (ix, accts, d) {
    for (const a of accts) if (covers(ix.coverage.get(a.id) || [], d)) return true;
    return false;
  };
  /** Accounts (that have any statements) whose statements don't cover day d. */
  M.dayGaps = function (ix, accts, d) {
    return accts.filter((a) => { const l = ix.coverage.get(a.id) || []; return l.length && !covers(l, d); });
  };

  /** Short, unambiguous account name for sentences ("Prime Visa", or "Prime Visa ••1234"
      when two accounts would read the same). */
  M.acctName = function (ix, a) {
    const bare = N.shortAccount(a).replace(/\s*••\w+$/, '');
    const twins = ix.data.accounts.filter((b) => N.shortAccount(b).replace(/\s*••\w+$/, '') === bare).length;
    return twins > 1 ? N.shortAccount(a) : bare;
  };
  const andList = (xs) => (xs.length <= 1 ? xs.join('') : xs.slice(0, -1).join(', ') + ' and ' + xs[xs.length - 1]);

  /** A month is partial if ANY account that has statements lacks coverage for any day of
      it (up to today) — including days before that account's first statement — or if the
      month is still in progress. Returns plain-English reasons. */
  M.monthGaps = function (ix, accts, key) {
    const start = N.monthStart(key);
    const today = N.today();
    const mEnd = N.monthEnd(key);
    const end = mEnd > today ? today : mEnd;
    const missing = [], reasons = [];
    const monthName = N.monthLabel(key, { long: true });
    for (const a of accts) {
      const list = ix.coverage.get(a.id) || [];
      if (!list.length || end < start) continue;
      const holes = [];
      let open = null;
      for (let d = start; d <= end; d = N.addDays(d, 1)) {
        if (covers(list, d)) { if (open) { holes.push(open); open = null; } }
        else if (open) open[1] = d;
        else open = [d, d];
      }
      if (open) holes.push(open);
      if (!holes.length) continue;
      missing.push(a);
      for (const [h0, h1] of holes) {
        const atStart = +h0 === +start, atEnd = +h1 === +end;
        if (atStart && atEnd) reasons.push({ a, type: 'none', date: start });
        else if (atStart) reasons.push({ a, type: 'starts', date: N.addDays(h1, 1) });
        else if (atEnd) reasons.push({ a, type: 'ends', date: N.addDays(h0, -1) });
        else reasons.push({ a, type: 'gap', date: h0, to: h1 });
      }
    }
    const inProgress = mEnd > today;
    // Group accounts that share a reason: "Prime Visa and Costco Visa start Sep 15".
    const groups = new Map();
    for (const r of reasons) {
      const k = r.type + '|' + N.iso(r.date) + '|' + (r.to ? N.iso(r.to) : '');
      const g = groups.get(k) || Object.assign({ accts: [] }, r);
      g.accts.push(r.a);
      groups.set(k, g);
    }
    const order = { starts: 0, gap: 1, ends: 2, none: 3 };
    // Bank accounts first (they carry income), then cards; latest date first within each.
    const bankFirst = (g) => (g.accts.some((a) => a.type !== 'credit') ? 0 : 1);
    const phrases = Array.from(groups.values()).sort((x, y) => (bankFirst(x) - bankFirst(y)) || (order[x.type] - order[y.type]) || (y.date - x.date)).map((g) => {
      const names = andList(g.accts.map((a) => M.acctName(ix, a)));
      const many = g.accts.length > 1;
      if (g.type === 'starts') return names + (many ? ' start ' : ' starts ') + N.fmtDate(g.date);
      if (g.type === 'gap') return names + (many ? ' have' : ' has') + ' no statement for ' + N.fmtDate(g.date) + '–' + N.fmtDate(g.to);
      if (g.type === 'none') return names + (many ? ' have' : ' has') + ' no statement for ' + monthName;
      const paid = g.accts.some((a) => a.type !== 'credit' && ix.data.transactions.some((t) => t.account_id === a.id && t.kind === 'income' && t.category === 'Salary'));
      return names + (many ? ' end ' : ' ends ') + N.fmtDate(g.date) + (paid ? ', so ' + monthName + ' paychecks after that aren’t in yet' : '');
    });
    let text = phrases.length > 4 ? phrases.slice(0, 4).join('; ') + '; and ' + (phrases.length - 4) + ' more' : phrases.join('; ');
    if (!text && inProgress) text = monthName + ' is still in progress';
    return { missing, reasons, inProgress, partial: missing.length > 0 || inProgress, text };
  };

  // ------------------------------------------------------------ month vs month
  /** Pick the comparison pair: the chosen month (or the single selected month, or the
      latest month with data) against the calendar month before it. */
  M.comparePair = function (ix, sel, chosen) {
    const months = Array.from(new Set(sel.txA.map((t) => N.monthKey(t.date)))).sort();
    let cur = chosen && months.includes(chosen) ? chosen : sel.selMonths.length === 1 ? sel.selMonths[0] : months[months.length - 1];
    if (!cur) return { months, cur: null };
    const prev = N.shiftMonth(cur, -1);
    const options = months.filter((m) => months.includes(N.shiftMonth(m, -1)));
    return { months, cur, prev, hasPrev: months.includes(prev), options };
  };
  M.compare = function (ix, sel, cur, prev) {
    const inMonth = (k) => sel.txA.filter((t) => N.monthKey(t.date) === k);
    const tc = M.totals(inMonth(cur)), tp = M.totals(inMonth(prev));
    // % only when last month is non-zero and both months sit on the same side of zero.
    const fig = (label, key, a, b) => ({ label, key, cur: a, prev: b, diff: a - b,
      pct: Math.abs(b) > 0.005 && (a >= 0) === (b >= 0) ? (a - b) / Math.abs(b) : null, signFlip: Math.abs(b) > 0.005 && (a >= 0) !== (b >= 0) });
    const figures = [
      fig('Money in', 'in', tc.in, tp.in), fig('Spent', 'out', tc.out, tp.out),
      fig('Into savings', 'put', tc.putIn, tp.putIn), fig('From savings', 'taken', tc.takenOut, tp.takenOut),
      fig('Left in checking', 'left', tc.left, tp.left),
    ];
    const names = new Set([...tc.cats.keys(), ...tp.cats.keys()]);
    let cats = Array.from(names).map((n) => {
      const a = (tc.cats.get(n) || {}).spend || 0, b = (tp.cats.get(n) || {}).spend || 0;
      return { name: n, cur: a, prev: b, diff: a - b, categories: [n] };
    }).filter((c) => Math.abs(c.diff) > 0.005 || c.cur > 0.005);
    cats.sort((x, y) => Math.abs(y.diff) - Math.abs(x.diff));
    if (cats.length > 9) {
      const rest = cats.slice(8);
      cats = cats.slice(0, 8).concat([{ name: N.plural(rest.length, 'other category', 'other categories'), other: true,
        cur: N.sum(rest, (c) => c.cur), prev: N.sum(rest, (c) => c.prev), diff: N.sum(rest, (c) => c.diff), categories: rest.map((c) => c.name) }]);
    }
    const merch = (k) => { const m = new Map(); for (const t of inMonth(k)) if (M.SPEND_KINDS[t.kind]) m.set(t.merchant, (m.get(t.merchant) || 0) - t.amount); return m; };
    const mc = merch(cur), mp = merch(prev);
    const merchants = Array.from(new Set([...mc.keys(), ...mp.keys()])).map((n) => ({ name: n, cur: mc.get(n) || 0, prev: mp.get(n) || 0 }))
      .map((r) => Object.assign(r, { diff: r.cur - r.prev })).filter((r) => Math.abs(r.diff) > 0.5)
      .sort((x, y) => Math.abs(y.diff) - Math.abs(x.diff)).slice(0, 3);
    return { cur, prev, tc, tp, figures, cats, merchants, gCur: M.monthGaps(ix, sel.accts, cur), gPrev: M.monthGaps(ix, sel.accts, prev) };
  };

  // ------------------------------------------------------------ categories & merchants
  M.categoryRows = function (tot) {
    const rows = Array.from(tot.cats.values()).filter((c) => c.spend > 0.005);
    rows.sort((a, b) => b.spend - a.spend);
    const total = N.sum(rows, (r) => r.spend);
    rows.forEach((r) => { r.share = total > 0 ? r.spend / total : 0; });
    return rows;
  };

  M.merchantRows = function (txns) {
    const m = new Map();
    for (const t of txns) {
      if (!M.SPEND_KINDS[t.kind]) continue;
      const r = m.get(t.merchant) || { name: t.merchant, spend: 0, visits: 0, cats: new Map(), recurring: false };
      r.spend -= t.amount;
      if (t.kind === 'expense') r.visits++;
      r.cats.set(t.category, (r.cats.get(t.category) || 0) + 1);
      if (t.recurring) r.recurring = true;
      m.set(t.merchant, r);
    }
    const rows = Array.from(m.values()).filter((r) => r.spend > 0.005);
    rows.forEach((r) => { r.category = Array.from(r.cats.entries()).sort((a, b) => b[1] - a[1])[0][0]; });
    rows.sort((a, b) => b.spend - a.spend);
    return rows;
  };

  // ------------------------------------------------------------ sankey model
  /** Gross flows that balance: income + taken out of savings (+ checking drawn down)
      = spending + put into savings (+ left in checking). */
  M.sankey = function (tot, maxCats) {
    const srcRows = Array.from(tot.sources.entries()).filter((e) => e[1] > 0.005).sort((a, b) => b[1] - a[1]);
    const sources = [];
    const keepSrc = 4;
    srcRows.slice(0, keepSrc).forEach(([name, v]) => sources.push({ id: 's:' + name, label: name, value: v, kind: 'in' }));
    const restSrc = srcRows.slice(keepSrc);
    if (restSrc.length === 1) sources.push({ id: 's:' + restSrc[0][0], label: restSrc[0][0], value: restSrc[0][1], kind: 'in' });
    else if (restSrc.length) sources.push({ id: 's:other', label: 'Other income', value: N.sum(restSrc, (e) => e[1]), kind: 'in', members: restSrc.map((e) => e[0]) });
    const dests = Array.from(tot.dests.values());
    dests.filter((d) => d.taken > 0.005).sort((a, b) => b.taken - a.taken)
      .forEach((d) => sources.push({ id: 'from:' + d.name, label: 'From ' + d.name, value: d.taken, kind: 'saving', dest: d.name }));

    const targets = [];
    dests.filter((d) => d.put > 0.005).sort((a, b) => b.put - a.put)
      .forEach((d) => targets.push({ id: 'into:' + d.name, label: 'Into ' + d.name, value: d.put, kind: 'saving', dest: d.name }));
    const cats = M.categoryRows(tot);
    const keep = cats.length > maxCats ? maxCats - 1 : cats.length;
    cats.slice(0, keep).forEach((c) => targets.push({ id: 'c:' + c.name, label: c.name, value: c.spend, kind: 'out', categories: [c.name] }));
    const rest = cats.slice(keep);
    if (rest.length) targets.push({ id: 'c:other', label: N.plural(rest.length, 'other category', 'other categories'), value: N.sum(rest, (c) => c.spend), kind: 'out', categories: rest.map((c) => c.name) });

    // Balance: spending netted of refunds can exceed recorded spend rows only by rounding.
    const spend = N.sum(cats, (c) => c.spend);
    const left = Math.max(0, tot.in) + tot.takenOut - spend - tot.putIn;
    if (left > 0.005) targets.push({ id: 'left', label: 'Left in checking', value: left, kind: 'cash' });
    else if (left < -0.005) sources.push({ id: 'from-checking', label: 'From checking balance', value: -left, kind: 'cash' });
    const total = N.sum(sources, (x) => x.value);
    return { sources, targets, total, inn: Math.max(0, tot.in), spend, left };
  };

  // ------------------------------------------------------------ people
  M.personOf = function (ix, t) {
    if (t.cardholder) return N.personName(t.cardholder);
    const a = ix.acctById.get(t.account_id);
    return a && a.holder ? N.personName(a.holder) : null;
  };
  M.firstNames = function (names) {
    const firsts = names.map((n) => n.split(' ')[0]);
    const unique = new Set(firsts).size === firsts.length;
    return new Map(names.map((n, i) => [n, unique ? firsts[i] : n]));
  };

  // ------------------------------------------------------------ recurring
  function cadence(dates) {
    if (dates.length < 2) return { label: 'Repeats', perMonth: 1, interval: 30 };
    const ds = dates.map(N.parseDate).sort((a, b) => a - b);
    const gaps = [];
    for (let i = 1; i < ds.length; i++) gaps.push(N.dayDiff(ds[i - 1], ds[i]));
    const real = gaps.filter((x) => x >= 3);
    const g = real.length ? Math.min(...real) : 30;
    if (g <= 10) return { label: 'Weekly', perMonth: 4.33, interval: 7 };
    if (g <= 19) return { label: 'Every 2 weeks', perMonth: 2.17, interval: 14 };
    if (g <= 45) return { label: 'Monthly', perMonth: 1, interval: 30 };
    if (g <= 75) return { label: 'Every 2 months', perMonth: 0.5, interval: 61 };
    if (g <= 120) return { label: 'Quarterly', perMonth: 1 / 3, interval: 91 };
    return { label: 'Yearly', perMonth: 1 / 12, interval: 365 };
  }
  /** Merchants with recurring charges (or in Subscriptions) seen in the period;
      amounts and cadence come from their whole history for a fair monthly figure. */
  M.recurring = function (ix, txA, txF) {
    const inPeriod = new Set();
    for (const t of txF) if (t.kind === 'expense' && (t.recurring || t.category === 'Subscriptions')) inPeriod.add(t.merchant);
    const rows = [];
    for (const merchant of inPeriod) {
      const all = txA.filter((t) => t.merchant === merchant && t.kind === 'expense' && (t.recurring || t.category === 'Subscriptions'));
      if (!all.length) continue;
      const amounts = all.map((t) => -t.amount);
      const typical = N.median(amounts);
      const cad = cadence(all.map((t) => t.date));
      const last = all.reduce((a, b) => (a.date > b.date ? a : b));
      const next = N.addDays(N.parseDate(last.date), cad.interval);
      const subs = all.some((t) => t.category === 'Subscriptions');
      rows.push({
        merchant, category: last.category, typical, cadence: cad.label, monthly: typical * cad.perMonth,
        last: last.date, next: N.iso(next), account_id: last.account_id, count: all.length, subscription: subs,
        once: all.length < 2,
      });
    }
    rows.sort((a, b) => b.monthly - a.monthly);
    return rows;
  };

  // ------------------------------------------------------------ insights
  // Each insight: { tone: 'info'|'note', parts: [string | {b: string}] }.
  M.insights = function (ix, sel, tot, state) {
    const out = [];
    const b = (x) => ({ b: x });
    const spendTx = sel.txF.filter((t) => M.SPEND_KINDS[t.kind]);
    const periodName = sel.selMonths.length === 1 ? N.monthLabel(sel.selMonths[0], { long: true }) : null;

    // 1. Coverage caveat — the single most important thing to know before reading numbers.
    const flagged = sel.selMonths.map((k) => ({ k, g: M.monthGaps(ix, sel.accts, k) })).filter((x) => x.g.partial);
    const lastPartial = flagged[flagged.length - 1];
    if (lastPartial) {
      const month = N.monthLabel(lastPartial.k, { long: true });
      const bank = lastPartial.g.missing.some((a) => a.type !== 'credit');
      out.push({ tone: 'note', parts: [b(month + ' is partial'), ': ', lastPartial.g.text, '. ',
        bank ? 'Income and spending' : 'Spending', ' will change once those statements are added.'] });
    }

    // 1b. Savings & investments: where money went in and came out, and the net.
    if (tot.dests.size) {
      const list = (arr, f) => {
        if (arr.length === 1) return arr[0].name;
        const names = arr.slice(0, 3).map((d) => d.name + ' (' + N.money(f(d), { cents: false }) + ')');
        if (arr.length > 3) names.push(N.plural(arr.length - 3, 'other'));
        return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
      };
      const ins = Array.from(tot.dests.values()).filter((d) => d.put > 0.005).sort((a, c) => c.put - a.put);
      const outs = Array.from(tot.dests.values()).filter((d) => d.taken > 0.005).sort((a, c) => c.taken - a.taken);
      const parts = [];
      if (ins.length) parts.push('You put ', b(N.money(tot.putIn, { cents: false })), ' into ', list(ins, (d) => d.put));
      if (outs.length) parts.push(ins.length ? ' and took ' : 'You took ', b(N.money(tot.takenOut, { cents: false })), ' out of ', list(outs, (d) => d.taken));
      if (tot.saved >= 0) parts.push(', so ', b(N.money(tot.saved, { cents: false }) + ' net was saved'), isFinite(tot.rate) ? ' (' + N.pct(tot.rate) + ' of money in).' : '.');
      else parts.push(', a net ', b(N.money(-tot.saved, { cents: false }) + ' drawn from savings'), '. It went to spending or stayed in checking.');
      out.push({ tone: 'info', parts });
    }

    // 2. Month over month (single month vs previous, or last two months in range).
    let cur = null, prev = null;
    if (sel.selMonths.length === 1) { cur = sel.selMonths[0]; prev = N.shiftMonth(cur, -1); }
    else if (sel.selMonths.length >= 2) {
      const full = sel.selMonths.filter((k) => !M.monthGaps(ix, sel.accts, k).partial);
      if (full.length >= 2) { cur = full[full.length - 1]; prev = full[full.length - 2]; }
    }
    if (cur && ix.months.includes(prev)) {
      const tc = M.totals(sel.txA.filter((t) => N.monthKey(t.date) === cur));
      const tp = M.totals(sel.txA.filter((t) => N.monthKey(t.date) === prev));
      if (tp.out > 0 && tc.out > 0) {
        const d = tc.out - tp.out;
        const r = d / tp.out;
        // biggest category driver
        let driver = null;
        const names = new Set([...tc.cats.keys(), ...tp.cats.keys()]);
        for (const n of names) {
          const dv = ((tc.cats.get(n) || {}).spend || 0) - ((tp.cats.get(n) || {}).spend || 0);
          if (!driver || Math.abs(dv) > Math.abs(driver.dv)) driver = { n, dv };
        }
        const dir = d >= 0 ? 'higher' : 'lower';
        const parts = ['Spending in ', N.monthLabel(cur, { long: true }), ' was ', b(N.pct(Math.abs(r)) + ' ' + dir), ' than ', N.monthLabel(prev, { long: true }),
          ' (', N.money(tc.out, { cents: false }), ' vs ', N.money(tp.out, { cents: false }), ')'];
        if (driver && Math.abs(driver.dv) > 25 && Math.sign(driver.dv) === Math.sign(d)) {
          parts.push(', mostly ', driver.n, ' (', b(N.money(driver.dv, { cents: false, sign: true })), ')');
        }
        parts.push('.');
        if (Math.abs(r) >= 0.02) out.push({ tone: 'info', parts });
      }
    }

    // 3. Biggest one-off expense (not a recurring bill).
    const oneOffs = spendTx.filter((t) => t.kind === 'expense' && !t.recurring && t.category !== 'Housing');
    if (oneOffs.length) {
      const big = oneOffs.reduce((a, c) => (c.amount < a.amount ? c : a));
      out.push({ tone: 'info', parts: ['Biggest one-off: ', b(N.money(-big.amount)), ' at ', big.merchant, ' on ', N.fmtDate(big.date), ' (', big.category, ').'] });
    }

    // 4. Top everyday merchant (excluding recurring bills).
    const merchants = M.merchantRows(sel.txF).filter((r) => !r.recurring && r.visits >= 2 && r.category !== 'Housing');
    if (merchants.length) {
      const top = merchants[0];
      const share = tot.out > 0 ? top.spend / tot.out : 0;
      out.push({ tone: 'info', parts: [b(top.name), ' is where everyday money goes most: ', b(N.money(top.spend, { cents: false })), ' over ', N.plural(top.visits, 'visit'),
        share >= 0.02 ? ' (' + N.pct(share) + ' of spending).' : '.'] });
      const freq = merchants.slice().sort((a, c) => c.visits - a.visits)[0];
      if (freq.name !== top.name && freq.visits >= 6) {
        out.push({ tone: 'info', parts: ['Most frequent stop: ', b(freq.name), ', ', N.plural(freq.visits, 'visit'), ' averaging ', N.money(freq.spend / freq.visits), '.'] });
      }
    }

    // 5. Card spending by person (cardholder, else account holder).
    const cardSpend = new Map();
    for (const t of spendTx) {
      const a = ix.acctById.get(t.account_id);
      if (!a || a.type !== 'credit') continue;
      const p = M.personOf(ix, t);
      if (!p) continue;
      cardSpend.set(p, (cardSpend.get(p) || 0) - t.amount);
    }
    if (cardSpend.size >= 2) {
      const rows = Array.from(cardSpend.entries()).sort((x, y) => y[1] - x[1]);
      const total = N.sum(rows, (r) => r[1]);
      const short = M.firstNames(rows.map((r) => r[0]));
      const parts = ['On cards, '];
      rows.slice(0, 3).forEach((r, i) => {
        if (i) parts.push(i === rows.length - 1 || i === 2 ? ' and ' : ', ');
        parts.push(short.get(r[0]), ' spent ', b(N.money(r[1], { cents: false })), ' (' + N.pct(r[1] / total) + ')');
      });
      parts.push('.');
      out.push({ tone: 'info', parts });
    }

    // 6. Subscriptions per month.
    const rec = M.recurring(ix, sel.txA, sel.txF).filter((r) => r.subscription);
    if (rec.length) {
      const monthly = N.sum(rec, (r) => r.monthly);
      out.push({ tone: 'info', parts: ['Subscriptions run about ', b(N.money(monthly) + ' a month'), ' across ', N.plural(rec.length, 'service'), ' — ', N.money(monthly * 12, { cents: false }), ' a year.'] });
    }

    // 7. Fees & interest, refunds.
    const fees = tot.cats.get('Fees & Interest');
    if (fees && fees.spend > 0.5) out.push({ tone: 'info', parts: ['You paid ', b(N.money(fees.spend)), ' in fees and interest', periodName ? ' in ' + periodName : '', '.'] });
    if (tot.refunds > 0.5) out.push({ tone: 'info', parts: [b(N.money(tot.refunds)), ' came back as ', N.plural(tot.refundCount, 'refund'), ', already netted out of spending.'] });

    // 8. Savings rate over complete months, when meaningful.
    if (tot.in > 0 && sel.selMonths.length > 1) {
      const full = sel.selMonths.filter((k) => !M.monthGaps(ix, sel.accts, k).partial);
      if (full.length > 1) {
        const ft = M.totals(sel.txA.filter((t) => full.includes(N.monthKey(t.date))));
        if (ft.in > 0 && ft.saved > 0) out.push({ tone: 'info', parts: ['In complete months you saved ', b(N.pct(ft.rate)), ' of what came in, about ', b(N.money(ft.saved / full.length, { cents: false }) + ' a month'), ' into savings and investments.'] });
      }
    }
    return out.slice(0, 6);
  };

  // ------------------------------------------------------------ accounts & loose ends
  M.accountStatus = function (ix, acct) {
    const stmts = ix.data.statements.filter((s) => s.account_id === acct.id)
      .sort((a, b) => (a.period_end < b.period_end ? -1 : 1));
    const latest = stmts[stmts.length - 1] || null;
    const today = N.today();
    const res = { stmts, latest, nudge: null, gaps: [] };
    if (!latest) return res;
    const lastEnd = N.parseDate(latest.period_end);
    if (N.dayDiff(lastEnd, today) > 31) {
      res.nudge = { expectedEnd: N.iso(N.addDays(lastEnd, 30)), since: latest.period_end };
    }
    // gaps between consecutive statements (> 3 days uncovered)
    const cov = ix.coverage.get(acct.id) || [];
    for (let i = 1; i < cov.length; i++) {
      const a = N.addDays(cov[i - 1][1], 1), b = N.addDays(cov[i][0], -1);
      if (N.dayDiff(a, b) >= 3) res.gaps.push([N.iso(a), N.iso(b)]);
    }
    const sum = latest.summary || {};
    if (acct.type === 'credit') {
      res.balance = sum.new_balance;
      res.limit = sum.credit_limit;
      res.minimum = sum.minimum_due;
      res.due = sum.due_date;
      res.util = sum.credit_limit > 0 && sum.new_balance != null ? Math.max(0, sum.new_balance) / sum.credit_limit : null;
      if (sum.due_date) {
        // payment seen on this card after the statement closed?
        const pay = ix.data.transactions.filter((t) => t.account_id === acct.id && t.kind === 'transfer' && t.amount > 0 &&
          t.date > latest.period_end).sort((x, y) => (x.date < y.date ? -1 : 1))[0];
        res.paid = pay || null;
        res.dueIn = N.dayDiff(today, N.parseDate(sum.due_date));
      }
    } else {
      res.balance = sum.closing_balance != null ? sum.closing_balance : sum.new_balance;
    }
    return res;
  };

  /** Transfers with no counterpart, grouped into human sentences. */
  M.looseEnds = function (ix, accts) {
    const on = new Set(accts.map((a) => a.id));
    const loose = ix.data.transactions.filter((t) => t.kind === 'transfer' && !t.transfer_match_id && on.has(t.account_id));
    const groups = new Map();
    for (const t of loose) {
      const text = (t.merchant + ' ' + t.description).toLowerCase();
      let target = null;
      for (const a of ix.data.accounts) {
        if (a.id === t.account_id) continue;
        if (a.last4 && text.includes(a.last4)) { target = a; break; }
      }
      let institution = null;
      if (!target) {
        const cands = ix.data.accounts.filter((a) => a.id !== t.account_id && text.includes(String(a.institution).toLowerCase().split(' ')[0]));
        if (cands.length === 1) target = cands[0];
        else if (cands.length > 1) institution = cands[0].institution;  // e.g. one of several Chase cards
      }
      const dir = t.amount < 0 ? 'out' : 'in';
      const key = dir + '|' + (target ? 'a' + target.id : t.merchant) + '|' + t.account_id;
      const payee = String(t.merchant).replace(/\b(card\s+)?(online\s+)?(payments?|autopay|pmt|epay)\b/gi, '').replace(/\s+/g, ' ').trim() || t.merchant;
      const g = groups.get(key) || { dir, target, institution, payee, merchant: t.merchant, from: ix.acctById.get(t.account_id), txns: [], category: t.category };
      g.txns.push(t);
      groups.set(key, g);
    }
    const res = Array.from(groups.values());
    res.forEach((g) => { g.total = Math.abs(N.sum(g.txns, (t) => t.amount)); });
    res.sort((a, b) => b.total - a.total);
    return res;
  };
})();
