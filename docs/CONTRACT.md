# Namu — internal contract

Local, private, deterministic (no LLMs, no network calls). Flask on `127.0.0.1:8765`,
SQLite at `data/namu.db`, original PDFs kept at `data/uploads/<sha256>.pdf`.

```
namu/models.py        shared dataclasses (ParsedStatement, ParsedTxn, Check)  — owner: lead
namu/parsers/         PDF -> list[ParsedStatement]                            — owner: parsers
namu/enrich.py        merchant names, categories, kinds, transfers, recurring — owner: backend
namu/db.py, ingest.py, app.py                                                  — owner: backend
static/               single-page dashboard (vanilla JS + inline SVG)          — owner: frontend
```

## Parser entry point

```python
from namu.parsers import parse_pdf          # parse_pdf(path: str | Path) -> list[ParsedStatement]
                                            # raises namu.models.UnsupportedStatement
```

## Transaction semantics

`amount` is signed from the household's view: negative = money out, positive = money in.

`kind` (assigned by enrich, user can change via category):

| kind      | meaning                                                   | counts toward |
|-----------|-----------------------------------------------------------|---------------|
| expense   | money spent (amount < 0)                                  | Money out     |
| refund    | money back on a spending category (amount > 0)            | Money out (reduces it) |
| income    | payroll, tax refund, interest, other income (amount > 0)  | Money in      |
| saving    | money to (amount < 0) or back from (amount > 0) a savings/investment destination | Saved |
| transfer  | card payments, moves between own accounts                 | neither       |

Saving destinations (user-defined): Fidelity, Schwab, E*Trade, Coinbase, Barclays, US Treasury
(TreasuryDirect purchases and Treasury payouts such as "TREAS 310 MISC PAY" — a matured
Treasury is savings coming back, NOT income; IRS tax refunds stay income). Money coming back
from a destination is a `saving` txn with a positive amount and reduces Saved.

Dashboard math:
- `money_in = Σ amount (income)`
- `money_out = −Σ amount (expense + refund)`  (shown as "Spent")
- `saved = −Σ amount (saving)` — net; also show `put_in = −Σ amount (saving, amount < 0)` and
  `taken_out = Σ amount (saving, amount > 0)`. Can be negative.
- `left_in_checking = money_in − money_out − saved` (what stayed as cash; NOT savings)
- `savings_rate = saved / money_in`

Categories (fixed order; `kind` is the kind a txn takes when put in that category):

- expense: Housing, Utilities, Phone & Internet, Groceries, Dining, Shopping, Gas & Auto,
  Tolls & Transit, Travel, Health, Subscriptions, Home & Garden, Donations,
  Family & Friends, Fees & Interest, Services, Other
- income: Salary, Tax & Government, Interest, Other Income
- saving: Savings & Investments (the destination is the txn's `merchant`: "Barclays", "Fidelity",
  "Schwab", "E*Trade", "Coinbase", "US Treasury")
- transfer: Card Payment, Transfer

Putting a txn in an expense category makes it `expense` if amount < 0, else `refund`.

## HTTP API

### `GET /api/data` — everything the dashboard needs (filtering/aggregation happens client-side)

```json
{
  "accounts": [
    {"id": 1, "institution": "Chase", "product": "Freedom Unlimited", "type": "credit",
     "last4": "1234", "holder": "ALEX J SMITH", "label": "Chase Freedom Unlimited ••1234"}
  ],
  "statements": [
    {"id": 3, "account_id": 1, "filename": "20261001-statements-1234.pdf",
     "period_start": "2026-09-02", "period_end": "2026-10-01",
     "uploaded_at": "2026-10-29T14:50:00", "parser": "chase_card", "txn_count": 21,
     "reconciled": true,
     "checks": [{"label": "Purchases", "expected": 2591.50, "parsed": 2591.50, "ok": true}],
     "summary": {"new_balance": 1234.56, "minimum_due": 35.0, "due_date": "2026-10-25",
                 "credit_limit": 20000.0}}
  ],
  "transactions": [
    {"id": 10, "account_id": 1, "statement_id": 3, "date": "2026-09-01",
     "description": "GOOGLE *Workspace_utar cc@google.com CA", "merchant": "Google Workspace",
     "amount": -14.00, "kind": "expense", "category": "Subscriptions",
     "category_source": "auto", "cardholder": null,
     "transfer_match_id": null, "recurring": false}
  ],
  "categories": [{"name": "Housing", "kind": "expense"}],
  "rules": [{"merchant": "Wawa", "category": "Dining"}]
}
```

- `reconciled`: `true` all checks pass, `false` some fail, `null` unverified (generic parser).
- `summary` keys are optional; dollars (floats). Bank statements use `opening_balance`/`closing_balance`.
- `category_source`: `auto` (built-in rules) | `rule` (user merchant rule) | `user` (set on this txn).
- `transfer_match_id`: id of the counterpart txn on another account (e.g. checking autopay ↔ card
  "payment received"). A `transfer` with `transfer_match_id: null` is *unmatched* — usually means
  the other side's statement hasn't been uploaded (e.g. paid a Barclays card).
- `recurring`: same merchant charged in ≥ 2 distinct calendar months at a similar amount (±20%).
- Transactions sorted by date desc, then id desc.

### `POST /api/upload` — multipart, field name `files` (repeatable)

```json
{"results": [
  {"filename": "a.pdf", "status": "imported", "statement_ids": [3],
   "label": "Chase Freedom Unlimited ••1234", "period_start": "2026-09-02",
   "period_end": "2026-10-01", "new_txns": 21, "duplicate_txns": 0, "reconciled": true},
  {"filename": "b.pdf", "status": "duplicate", "message": "Already imported on 2026-10-29"},
  {"filename": "c.pdf", "status": "error", "message": "Unrecognized statement format"}
]}
```

Duplicate protection: same file bytes (sha256) → `duplicate`. Overlapping statements of the
same account → transactions de-duplicated by fingerprint (`duplicate_txns` counts them).

### `PATCH /api/transactions/<id>` — `{"category": "Dining", "apply_to_merchant": true}`

Returns `{"updated": [ids]}`. With `apply_to_merchant`, stores a merchant rule that also applies
to future uploads. Client refetches `/api/data` afterwards.

### `DELETE /api/statements/<id>` → `{"deleted": true}` (removes its transactions too)

### `POST /api/reparse` → re-runs parsers + enrichment over stored PDFs, keeping user
overrides and rules. Returns `{"statements": n}`.
