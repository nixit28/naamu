# Namu

A private, local personal-finance dashboard. Each month you drop in your bank and
credit-card statement PDFs, and Namu shows where the money came from and where it went:
money in, spent, saved, what was left in checking, spending by category, recurring
charges, and card payments matched to the checking account that paid them.

## Privacy

- Everything runs on your machine. The server listens on `127.0.0.1` only. It makes no
  network calls and uses no AI or LLM models. Every rule is plain, deterministic code.
- `data/` holds everything Namu stores: `data/namu.db` (SQLite) and the original PDFs in
  `data/uploads/<sha256>.pdf`. Delete that folder to wipe all data. Back it up to keep
  your history. It is ignored by git.
- API requests from other websites, and requests with a non-local Host header, are
  refused.

## Run

```sh
./run.sh
```

The first run creates `.venv` and installs `requirements.txt` (Flask, pdfplumber). Later
runs start straight away. Open <http://127.0.0.1:8765>.

Options: `NAMU_PORT=9000 ./run.sh` uses another port. `NAMU_DATA_DIR=/path ./run.sh` keeps
the data somewhere else.

Tests: `.venv/bin/python -m pytest tests -q`

## Supported statements

| Institution | Accounts | Status |
|---|---|---|
| Chase | Credit cards (Freedom, Prime Visa, …) | reconciled against statement totals |
| Chase | Checking and Savings | reconciled |
| American Express | Credit cards (Blue Cash, …) | reconciled |
| Citi | Credit cards (Costco Anywhere, …) | reconciled |
| Others | Any text-based PDF | best-effort generic parser, shown as **unverified** |

Each statement is checked against the totals printed on it (purchases, payments,
deposits, and so on). A failed check is flagged in the Statements list so you know to look.

## Monthly workflow

1. Download the month's statement PDFs from each bank.
2. Drag them all onto the dashboard. Order doesn't matter, and re-uploading is safe:
   - The same file twice is detected (by content hash) and skipped.
   - Overlapping statements of the same account don't double count. Each transaction
     has a fingerprint (account, date, amount, description, and its position among
     identical ones).
3. Review anything in **Other** and fix it. Your choice is remembered (see below).
4. Check the unmatched transfers list. It usually means a statement you haven't
   uploaded yet, such as a card paid from checking.

## How categories work

Each transaction gets a **kind**: expense, refund, income, saving, or transfer. It also
gets a **category**.

- On cards, the statement section decides first. Payments are `transfer / Card Payment`,
  credits are refunds, and fees or interest go to `Fees & Interest`. Purchases are
  categorized by merchant.
- In bank accounts, ordered rules run: IRS tax refunds and benefits are
  `Tax & Government`; payroll is `Salary`; money to or from a savings destination is
  `Savings & Investments` (see below); payments to card issuers are `Card Payment`;
  moves between your own accounts are `Transfer`; interest is `Interest`; temples and
  charities are `Donations` (this beats the Zelle rule); Zelle to someone is
  `Family & Friends`; Zelle from someone is `Other Income`. Everything else is
  categorized by merchant.
- Merchant names are cleaned up. Processor prefixes, store numbers, phone numbers, and
  reference IDs are removed, and variants collapse into one name. For example,
  `BJS WHOLESALE #0123 ANYTOWN NJ` becomes "BJ's Wholesale" and every Amazon variant
  becomes "Amazon".
- **Your edits win.** Changing a transaction's category is stored against its
  fingerprint, so it survives re-parsing and deleting and re-uploading. Choosing *apply to
  merchant* saves a rule that also applies to future uploads.
  Precedence: your edit, then your merchant rule, then the built-in rules.
- An expense category on a positive amount counts as a refund, which reduces money out.

## How it counts

- **Money in** = income (payroll, tax refunds, interest, other income).
- **Spent** = expenses − refunds.
- **Saved** = money put into your savings and investment destinations, minus money taken
  back out. These destinations are **Fidelity, Schwab, E\*Trade, Coinbase, Barclays and
  US Treasury** (TreasuryDirect purchases and Treasury payouts). They show as kind
  `saving`, category `Savings & Investments`, with the destination as the merchant. A
  matured Treasury ("TREAS 310 MISC PAY") is savings coming back, not income, so Saved
  can be negative in a month you cash out. IRS tax refunds stay income.
- **Left in checking** = money in − spent − saved. This is cash that stayed in the bank
  account. It is not counted as savings.
- **Savings rate** = saved / money in.
- Transfers (card payments, moves between your own accounts) count toward none of these.

To count another destination as savings, set one of its transactions to
`Savings & Investments` and choose *apply to merchant*.

## How transfers work

A transfer on one account is paired with a transfer for the same amount, with the
opposite sign, on a different account within ±6 days. The closest date wins, and each
transaction pairs at most once. Savings transactions are never paired and never show
as unmatched transfers. An example is the checking "Chase Credit Crd Autopay
−1,234.56" and the card "AUTOMATIC PAYMENT +1,234.56". Card payments stay transfers
even when unmatched.

Derived fields (merchant, kind, category, matches, recurring) are recomputed after every
change and each time the app starts, so improved rules apply to data you already
uploaded. Your edits and merchant rules still win.

**Recurring** marks a merchant charged in at least 2 different months at a similar
amount (within ±20%). Merchants charged more than twice in one month, like regular
grocery runs, are left out.

## Layout

```
namu/parsers/   PDF -> ParsedStatement (per-bank parsers + generic fallback)
namu/enrich.py  merchant names, categories, transfer matching, recurring
namu/ingest.py  upload pipeline, dedupe, reparse/delete, derived-field recompute
namu/db.py      SQLite schema
namu/app.py     Flask server + JSON API (docs/CONTRACT.md)
static/         the dashboard
```
