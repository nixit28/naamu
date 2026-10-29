/* Namu — data access. Real mode talks to the local Flask API (same origin).
   `?mock` loads static/mock-data.json and simulates uploads/edits in memory;
   `?mock&empty` starts with nothing so the first-run state can be tested. */
(function () {
  'use strict';
  const N = window.Namu;
  const params = new URLSearchParams(location.search);
  const MOCK = params.has('mock');
  N.isMock = MOCK;

  async function json(res) {
    let body = null;
    try { body = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) {
      const msg = (body && (body.error || body.message)) || ('The server answered ' + res.status + ' ' + res.statusText);
      throw new Error(msg);
    }
    return body;
  }

  const real = {
    getData: () => fetch('/api/data', { cache: 'no-store' }).then(json),
    upload(files) {
      const fd = new FormData();
      for (const f of files) fd.append('files', f, f.name);
      return fetch('/api/upload', { method: 'POST', body: fd }).then(json);
    },
    patchTxn: (id, body) => fetch('/api/transactions/' + encodeURIComponent(id), {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(json),
    deleteStatement: (id) => fetch('/api/statements/' + encodeURIComponent(id), { method: 'DELETE' }).then(json),
    reparse: () => fetch('/api/reparse', { method: 'POST' }).then(json),
  };

  // ------------------------------------------------------------------ mock
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  let master = null;
  let loaded = new Set();          // master statement ids currently "uploaded"
  let synthetic = [];              // [{ statement, txns }]
  const overrides = new Map();     // txn id -> { category, source }
  let rules = [];
  const seenFiles = new Map();     // filename -> ISO date imported
  let nextId = 100000;

  async function ensureMaster() {
    if (master) return;
    const file = 'mock-data.json';
    const res = await fetch(file, { cache: 'no-store' });
    master = await json(res);
    if (!params.has('empty')) {
      master.statements.forEach((s) => { loaded.add(s.id); seenFiles.set(s.filename, s.uploaded_at.slice(0, 10)); });
      rules = master.rules.slice();
    }
  }
  const catKind = () => new Map(master.categories.map((c) => [c.name, c.kind]));
  function kindFor(cat, amount, kinds) {
    const k = kinds.get(cat) || 'expense';
    return k === 'expense' ? (amount < 0 ? 'expense' : 'refund') : k;
  }

  function build() {
    const kinds = catKind();
    const stmts = master.statements.filter((s) => loaded.has(s.id)).concat(synthetic.map((x) => x.statement));
    const sids = new Set(stmts.map((s) => s.id));
    let txns = master.transactions.filter((t) => sids.has(t.statement_id)).concat(...synthetic.map((x) => x.txns));
    txns = txns.map((t) => Object.assign({}, t));
    const ruleMap = new Map(rules.map((r) => [r.merchant, r.category]));
    const ids = new Set(txns.map((t) => t.id));
    for (const t of txns) {
      if (ruleMap.has(t.merchant) && t.category_source !== 'user') {
        t.category = ruleMap.get(t.merchant);
        t.category_source = 'rule';
      }
      const o = overrides.get(t.id);
      if (o) { t.category = o.category; t.category_source = o.source; }
      t.kind = kindFor(t.category, t.amount, kinds);
      if (t.transfer_match_id && !ids.has(t.transfer_match_id)) t.transfer_match_id = null;
    }
    txns.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.id - a.id));
    const usedAccts = new Set(stmts.map((s) => s.account_id));
    const accounts = master.accounts.filter((a) => usedAccts.has(a.id));
    stmts.forEach((s) => { s.txn_count = txns.filter((t) => t.statement_id === s.id).length; });
    return JSON.parse(JSON.stringify({ accounts, statements: stmts, transactions: txns, categories: master.categories, rules }));
  }

  const KEYWORDS = [
    [/checking|4821/i, 1], [/freedom|3307/i, 2], [/prime|amazon|6618/i, 3],
    [/amex|american|blue|1005/i, 4], [/citi|costco|7712/i, 5],
  ];

  function synthesize(acctId) {
    const own = master.statements.filter((s) => s.account_id === acctId)
      .concat(synthetic.filter((x) => x.statement.account_id === acctId).map((x) => x.statement))
      .sort((a, b) => (a.period_end < b.period_end ? -1 : 1));
    const last = own[own.length - 1];
    const start = N.addDays(N.parseDate(last.period_end), 1);
    let end = N.addDays(start, 29);
    if (end > N.today()) end = N.today();  // a statement can't cover the future
    const sid = nextId++;
    // Replay the account's latest month, shifted forward — a believable next statement.
    const lastTx = build().transactions.filter((t) => t.statement_id === last.id && t.kind !== 'transfer');
    const shift = N.dayDiff(N.parseDate(last.period_start), start);
    const txns = [];
    lastTx.forEach((src, i) => {
      let d = N.addDays(N.parseDate(src.date), shift);
      if (d > end) return;
      if (d < start) d = start;
      const wobble = src.kind === 'income' || src.recurring ? 1 : 0.8 + ((i * 37) % 40) / 100;
      txns.push(Object.assign({}, src, {
        id: nextId++, statement_id: sid, date: N.iso(d),
        amount: Math.round(src.amount * wobble * 100) / 100, transfer_match_id: null,
      }));
    });
    const n = txns.length;
    const purchases = -N.sum(txns.filter((t) => t.amount < 0), (t) => t.amount);
    const acct = master.accounts.find((a) => a.id === acctId);
    const statement = {
      id: sid, account_id: acctId, filename: '', period_start: N.iso(start), period_end: N.iso(end),
      uploaded_at: new Date().toISOString().slice(0, 19), parser: acct.type === 'credit' ? 'card' : 'bank',
      txn_count: n, reconciled: true,
      checks: [{ label: 'Purchases', expected: Math.round(purchases * 100) / 100, parsed: Math.round(purchases * 100) / 100, ok: true }],
      summary: acct.type === 'credit'
        ? { new_balance: Math.round(purchases * 100) / 100, minimum_due: 40, due_date: N.iso(N.addDays(end, 24)), credit_limit: (last.summary || {}).credit_limit || 10000 }
        : { opening_balance: 5000, closing_balance: 5200 },
    };
    synthetic.push({ statement, txns });
    return { statement, dup: 2 };
  }

  const mock = {
    async getData() {
      await ensureMaster();
      await wait(160);
      return build();
    },
    async upload(files) {
      await ensureMaster();
      const results = [];
      for (const f of files) {
        await wait(380);
        const name = f.name;
        if (!/\.pdf$/i.test(name)) {
          results.push({ filename: name, status: 'error', message: 'Not a PDF. Namu reads PDF statements.' });
          continue;
        }
        if (seenFiles.has(name)) {
          results.push({ filename: name, status: 'duplicate', message: 'Already imported on ' + seenFiles.get(name) });
          continue;
        }
        let stmt = master.statements.find((s) => s.filename === name && !loaded.has(s.id));
        let acctId = stmt ? stmt.account_id : null;
        if (!acctId) for (const [re, id] of KEYWORDS) if (re.test(name)) { acctId = id; break; }
        if (!acctId) {
          results.push({ filename: name, status: 'error', message: 'Unrecognized statement format' });
          continue;
        }
        let dup = 0;
        if (!stmt) stmt = master.statements.filter((s) => s.account_id === acctId && !loaded.has(s.id))
          .sort((a, b) => (a.period_start < b.period_start ? -1 : 1))[0];
        if (stmt) loaded.add(stmt.id);
        else { const syn = synthesize(acctId); stmt = syn.statement; dup = syn.dup; stmt.filename = name; }
        seenFiles.set(name, N.iso(N.today()));
        if (stmt.filename !== name && !synthetic.some((x) => x.statement === stmt)) {
          seenFiles.set(stmt.filename, N.iso(N.today()));
        }
        const acct = master.accounts.find((a) => a.id === acctId);
        const count = synthetic.some((x) => x.statement === stmt)
          ? stmt.txn_count
          : master.transactions.filter((t) => t.statement_id === stmt.id).length;
        results.push({
          filename: name, status: 'imported', statement_ids: [stmt.id], label: acct.label,
          period_start: stmt.period_start, period_end: stmt.period_end,
          new_txns: count, duplicate_txns: dup, reconciled: stmt.reconciled,
        });
      }
      return { results };
    },
    async patchTxn(id, body) {
      await ensureMaster();
      await wait(140);
      const all = build().transactions;
      const t = all.find((x) => x.id === id);
      if (!t) throw new Error('Transaction not found');
      const updated = [id];
      overrides.set(id, { category: body.category, source: 'user' });
      if (body.apply_to_merchant) {
        rules = rules.filter((r) => r.merchant !== t.merchant).concat([{ merchant: t.merchant, category: body.category }]);
        for (const o of all) {
          if (o.merchant === t.merchant && o.id !== id) {
            overrides.set(o.id, { category: body.category, source: 'rule' });
            updated.push(o.id);
          }
        }
      }
      return { updated };
    },
    async deleteStatement(id) {
      await ensureMaster();
      await wait(200);
      const m = master.statements.find((s) => s.id === id);
      if (m) { loaded.delete(id); seenFiles.delete(m.filename); }
      const syn = synthetic.find((x) => x.statement.id === id);
      if (syn) { synthetic = synthetic.filter((x) => x !== syn); seenFiles.delete(syn.statement.filename); }
      return { deleted: true };
    },
    async reparse() {
      await ensureMaster();
      await wait(900);
      return { statements: loaded.size + synthetic.length };
    },
  };

  N.api = MOCK ? mock : real;
})();
