"""Citi cards (Costco Anywhere Visa, Double Cash, Custom Cash, ...).

Layout: page 1 "Billing Period: 09/15/26-10/14/26" and an Account Summary
(Previous balance, Payments, Credits, Purchases, Cash advances, Fees, Interest,
New balance). A CARDHOLDER SUMMARY lists "New Charges" per card member, then the
ACCOUNT SUMMARY table: "Payments, Credits and Adjustments", then per-cardholder
"Standard Purchases" groups, then "Fees Charged" / "Interest Charged". Rows have
Sale Date + Post Date (MM/DD), or only a post date (e.g. AUTOPAY).
"""
from __future__ import annotations

import re

from naamu.models import Check, ParsedStatement, ParsedTxn

from .common import (
    MONEY, MONEY_LOOSE, add_check, all_lines, find, find_amount, indent, is_card_payment, iso,
    md, parse_amount, parse_mdy, squash,
)

PARSER_ID = "citi"


def detect(text: str) -> bool:
    return bool(re.search(r"citicards\.com|Card by Citi|Citibank", text, re.I)) and bool(
        re.search(r"Billing Period:", text))


_BLOCKS = [
    (r"Payments,?\s+Credits\s+and\s+Adjustments", "payments"),
    (r"(?:Standard\s+)?Purchases", "purchases"),
    (r"Balance\s+Transfers", "purchases"),       # Citi folds BTs into "Purchases"
    (r"(?:Standard\s+)?(?:Cash\s+)?Advances", "cash_advances"),
    (r"Fees\s+Charged", "fees"),
    (r"Interest\s+Charged", "interest"),
]
_ACCOUNT_LEVEL = {"fees", "interest"}
_SECTION = {"purchases": "purchase", "cash_advances": "cash_advance", "fees": "fee",
            "interest": "interest"}
_TXN = re.compile(
    rf"^(?P<ind>\s*)(?:(?P<sale>\d{{2}}/\d{{2}})\s+)?(?P<post>\d{{2}}/\d{{2}})\s+"
    rf"(?P<desc>.*?\S)\s+(?P<amt>{MONEY})\s*$")
_UNDATED = re.compile(rf"^\s*(?P<desc>[A-Za-z].*?\S)\s+(?P<amt>{MONEY})\s*$")
_START = re.compile(r"^\s*ACCOUNT SUMMARY\b")
_END = re.compile(r"totals\s+year-to-date|Interest charge calculation", re.I)


def _block_of(line: str) -> str | None:
    flat = squash(line)
    return next((key for pat, key in _BLOCKS if re.fullmatch(pat, flat, re.I)), None)


def parse(pages: list[str]) -> list[ParsedStatement]:
    text = "\n".join(pages)

    m = re.search(r"Billing Period:\s*(\d{2}/\d{2}/\d{2,4})\s*-\s*(\d{2}/\d{2}/\d{2,4})", text)
    if not m:
        raise ValueError("Citi: no Billing Period")
    start, end = parse_mdy(m.group(1)), parse_mdy(m.group(2))

    last4 = find(r"Account number ending in:?\s*(\d{4})", text, re.M | re.I)
    if not last4:
        raise ValueError("Citi: no account number")

    product = (find(r"(?:^|\s{2,})([A-Za-z][A-Za-z ]*?)\s*®?\s+Card by Citi", text)
               or find(r"(?:^|\s{2,})(Citi [A-Z][A-Za-z ]*?)\s*®?\s+Card\b", text)
               or "Credit Card")
    product = squash(product.replace("®", ""))

    def amt(label: str) -> int | None:
        return find_amount(rf"\b{label}[ \t]+({MONEY})", text)

    s = {
        "previous": amt(r"Previous balance"),
        "payments": amt(r"Payments"),
        "credits": amt(r"Credits"),
        "purchases": amt(r"Purchases"),
        "cash_advances": amt(r"Cash advances"),
        "fees": amt(r"(?<!TOTAL )Fees"),
        "interest": amt(r"(?<!TOTAL )Interest"),
        "new": find_amount(rf"New balance[ \t]+({MONEY})", text),
    }
    if s["previous"] is None or s["new"] is None:
        raise ValueError("Citi: no Previous/New balance")

    # CARDHOLDER SUMMARY: "NAME   Card ending in 5678" then "New Charges $123.45".
    holders: dict[str, dict] = {}
    lines = [line for _, line in all_lines(pages)]
    for i, line in enumerate(lines):
        hm = re.match(r"^\s*([A-Z][A-Z.'\- ]*?[A-Z.])\s{2,}Card ending in\s*(\d{4})\s*$", line)
        if not hm:
            continue
        name = squash(hm.group(1))
        info = holders.setdefault(name, {"card": hm.group(2), "new_charges": None})
        for nxt in lines[i + 1:i + 4]:
            nm = re.match(rf"^\s*New Charges[ \t]+({MONEY})\s*$", nxt)
            if nm:
                info["new_charges"] = parse_amount(nm.group(1))
                break

    txns: list[ParsedTxn] = []
    printed: list[tuple[str, int, str | None]] = []
    in_table = False
    block = None
    cardholder = None
    last: ParsedTxn | None = None
    last_indent = 0
    for i, line in enumerate(lines):
        if _START.match(line):
            in_table = True
            continue
        if not in_table:
            continue
        if _END.search(line):
            in_table, last = False, None
            continue
        if not line.strip():
            last = None
            continue
        b = _block_of(line)
        if b:
            block, last = b, None
            if b in _ACCOUNT_LEVEL:
                cardholder = None
            continue
        flat = squash(line)
        if flat in holders:
            # A group header is followed by a block header or a row. The primary
            # name is also the page header (right under "Account number ending in")
            # and must not switch the group mid-list on a continuation page.
            nxt = next((l for l in lines[i + 1:i + 4] if l.strip()), "")
            prev = [l for l in lines[max(0, i - 3):i] if l.strip()]
            page_header = any(re.search(r"Account number ending in", l, re.I) for l in prev)
            if not page_header and (_block_of(nxt) or _TXN.match(nxt)):
                cardholder = flat
            last = None
            continue
        if block is None:
            continue
        tm = _TXN.match(line)
        if tm:
            cents = parse_amount(tm.group("amt"))
            post = md(tm.group("post"), start, end)
            sale = md(tm.group("sale"), start, end) if tm.group("sale") else post
            desc = squash(tm.group("desc"))
            if block == "payments":
                section = "payment" if is_card_payment(desc) else "credit"
            else:
                section = _SECTION[block]
                if section == "purchase" and cents < 0:
                    section = "credit"
            txn = ParsedTxn(date=sale, description=desc, amount_cents=-cents, section=section,
                            post_date=post, cardholder=cardholder)
            txns.append(txn)
            printed.append((block, cents, cardholder))
            last, last_indent = txn, len(tm.group("ind"))
            continue
        if block in _ACCOUNT_LEVEL:
            um = _UNDATED.match(line)
            if um and not re.match(r"\s*TOTAL\b", line, re.I):
                cents = parse_amount(um.group("amt"))
                txns.append(ParsedTxn(date=end, description=squash(um.group("desc")),
                                      amount_cents=-cents, section=_SECTION[block]))
                printed.append((block, cents, None))
                last = None
                continue
        if last is not None and indent(line) >= last_indent + 4 and not re.search(
                r"Page \d+ of \d+|citicards\.com|Account number ending", line, re.I):
            last.extra = f"{last.extra} | {flat}" if last.extra else flat
        else:
            last = None

    def total(*blocks: str) -> int:
        return sum(c for b, c, _ in printed if b in blocks)

    pay = sum(c for (b, c, _), t in zip(printed, txns) if b == "payments" and t.section == "payment")
    cred = total("payments") - pay
    checks: list = []
    add_check(checks, "Payments", s["payments"], pay, always=True)
    add_check(checks, "Credits", s["credits"], cred, always=True)
    add_check(checks, "Purchases", s["purchases"], total("purchases"), always=True)
    add_check(checks, "Cash advances", s["cash_advances"], total("cash_advances"))
    add_check(checks, "Fees", s["fees"], total("fees"))
    add_check(checks, "Interest", s["interest"], total("interest"))
    for name, info in holders.items():
        if info["new_charges"] is not None:
            parsed = sum(c for b, c, h in printed if h == name and b != "payments")
            checks.append(Check(f"New Charges ({name})", info["new_charges"], parsed))
    add_check(checks, "New balance", s["new"], s["previous"] + total(*{b for _, b in _BLOCKS}),
              always=True)

    summary = {"previous_balance_cents": s["previous"], "new_balance_cents": s["new"]}
    mind = find_amount(rf"Minimum payment due:?[ \t]+({MONEY})", text, re.M | re.I)
    if mind is not None:
        summary["minimum_due_cents"] = mind
    due = find(r"Payment due date:?\s+(\d{2}/\d{2}/\d{2,4})", text, re.M | re.I)
    if due:
        summary["due_date"] = iso(parse_mdy(due))
    limit = find(rf"Credit Limit[ \t]+({MONEY_LOOSE})", text)
    if limit:
        summary["credit_limit_cents"] = parse_amount(limit)

    holder = next((n for n, info in holders.items() if info["card"] == last4), None)
    if holder is None and holders:
        holder = next(iter(holders))
    return [ParsedStatement(
        institution="Citi", product=product, account_type="credit", last4=last4,
        holder=holder, period_start=start, period_end=end, parser=PARSER_ID,
        transactions=txns, checks=checks, summary=summary,
    )]
