"""Chase credit cards (Freedom Unlimited, Prime Visa, Sapphire, ...).

Layout: ACCOUNT SUMMARY box on page 1 (Opening/Closing Date, totals), then
ACCOUNT ACTIVITY grouped under PAYMENTS AND OTHER CREDITS / PURCHASE /
CASH ADVANCES / BALANCE TRANSFERS / FEES CHARGED / INTEREST CHARGED, possibly
continued on later pages under "ACCOUNT ACTIVITY (CONTINUED)". Rows print MM/DD
only; purchases print positive and payments/credits negative (card's view).
"""
from __future__ import annotations

import re

from naamu.models import ParsedStatement, ParsedTxn

from .common import (
    MONEY, MONEY_LOOSE, add_check, all_lines, find, find_amount, indent, is_card_payment,
    iso, md, parse_amount, parse_mdy, squash,
)

PARSER_ID = "chase_card"


def detect(text: str) -> bool:
    return bool(re.search(r"Opening/Closing Date", text)) and bool(
        re.search(r"chase\.com|\bCHASE\b|Chase Mobile|Cardmember Service", text, re.I))


# Checked in order against page 1 only (so merchant names can't interfere).
_PRODUCTS = [
    (r"FREEDOM\s+UNLIMITED", "Freedom Unlimited"),
    (r"FREEDOM\s+FLEX", "Freedom Flex"),
    (r"FREEDOM\s+RISE", "Freedom Rise"),
    (r"SAPPHIRE\s+RESERVE", "Sapphire Reserve"),
    (r"SAPPHIRE\s+PREFERRED", "Sapphire Preferred"),
    (r"PRIME\s+VISA", "Prime Visa"),
    (r"chase\.com/amazon|AMAZON\s+(?:REWARDS|VISA)", "Amazon Visa"),
    (r"\bSLATE\b", "Slate"),
    (r"\bINK\s+BUSINESS\s+(?:CASH|UNLIMITED|PREFERRED|PREMIER)", "Ink Business"),
    (r"\bFREEDOM\b", "Freedom"),
]

# ACCOUNT ACTIVITY block headers -> (block key, default section)
_BLOCKS = [
    (r"PAYMENTS\s+AND\s+OTHER\s+CREDITS", "payments"),
    (r"PURCHASES?", "purchases"),
    (r"CASH\s+ADVANCES?", "cash_advances"),
    (r"BALANCE\s+TRANSFERS?", "balance_transfers"),
    (r"FEES\s+CHARGED", "fees"),
    (r"INTEREST\s+CHARGED", "interest"),
]
_BLOCK_SECTION = {
    "purchases": "purchase", "cash_advances": "cash_advance", "balance_transfers": "other",
    "fees": "fee", "interest": "interest", "other": "other",
}

_ACTIVITY_START = re.compile(r"^\s*(?:ACCOUNT|AACCCCOOUUNNTT)\s+(?:ACTIVITY|AACCTTIIVVIITTYY)")
_ACTIVITY_END = re.compile(
    r"Totals\s+Year-to-Date|^\s*(?:INTEREST\s+CHARGES|IINNTTEERREESSTT\s+CCHHAARRGGEESS)\s*$"
    r"|^\s*(?:SHOP WITH POINTS|YOUR\s+.*POINTS|.*REWARDS\s+SUMMARY)", re.I)
_TXN = re.compile(rf"^(?P<ind>\s*)(?P<md>\d{{2}}/\d{{2}})\s+(?P<desc>.*?\S)\s+(?P<amt>{MONEY})\s*$")
# Rows start at column ~4; continuation lines (FX, order numbers) sit at ~16.
_MAX_TXN_INDENT = 10
_SKIP = re.compile(
    r"^\s*(?:Date\s+of\b|Transaction\s+Merchant\s+Name|Merchant\s+Name\s+or|Transaction\s*$"
    r"|TOTAL\b|.*\bPage\s*\d+\s*of\s*\d+\b|\d{7}\s+FIS)", re.I)


def _summary(text: str) -> dict:
    def amt(label: str) -> int | None:
        return find_amount(rf"^\s*{label}[ \t]+({MONEY})", text)

    return {
        "previous": amt(r"Previous\s+Balance"),
        "payments": amt(r"Payments?,?\s*(?:and\s+(?:Other\s+)?)?Credits"),
        "purchases": amt(r"Purchases"),
        "cash_advances": amt(r"Cash\s+Advances?"),
        "balance_transfers": amt(r"B\W?alance\s+Transfers?"),
        "fees": amt(r"Fees\s+Charged"),
        "interest": amt(r"Interest\s+Charged"),
        "new": amt(r"New\s+Balance"),
    }


def _holder(text: str) -> str | None:
    name = find(r"^\s*([A-Z][A-Z.'\- ]*?[A-Z.])\s+Page\s*\d+\s*of\s*\d+\s+Statement Date", text)
    if name:
        return squash(name)
    # Fallback: the name on the remit coupon, printed just above the street address.
    m = re.search(r"\b\d{5}\s+BEX\b.*\n\s*([A-Z][A-Z.'\- ]+?)\s*\n", text)
    return squash(m.group(1)) if m else None


def parse(pages: list[str]) -> list[ParsedStatement]:
    text = "\n".join(pages)

    m = re.search(r"Opening/Closing Date\s+(\d{2}/\d{2}/\d{2,4})\s*-\s*(\d{2}/\d{2}/\d{2,4})", text)
    if not m:
        raise ValueError("Chase card: no Opening/Closing Date")
    start, end = parse_mdy(m.group(1)), parse_mdy(m.group(2))

    last4 = find(r"Account Number:\s*[X*\s\d]*?(\d{4})\s*$", text)
    if not last4:
        raise ValueError("Chase card: no account number")

    product = "Credit Card"
    for pat, name in _PRODUCTS:
        if re.search(pat, pages[0], re.I):
            product = name
            break

    s = _summary(text)
    if s["previous"] is None or s["new"] is None:
        raise ValueError("Chase card: no Previous/New Balance in account summary")

    txns: list[ParsedTxn] = []
    printed: list[tuple[str, int]] = []      # (block, printed cents) per txn
    in_activity = False
    block = None
    last: ParsedTxn | None = None
    last_indent = 0
    for _, line in all_lines(pages):
        if _ACTIVITY_START.match(line):
            in_activity = True
            continue
        if not in_activity:
            continue
        if _ACTIVITY_END.search(line):
            in_activity = False
            last = None
            continue
        if not line.strip() or _SKIP.match(line):
            continue
        header = squash(line).upper()
        matched_block = next((key for pat, key in _BLOCKS if re.fullmatch(pat, header)), None)
        if matched_block:
            block, last = matched_block, None
            continue
        tm = _TXN.match(line)
        if tm and len(tm.group("ind")) <= _MAX_TXN_INDENT:
            desc = re.sub(r"^&\s*", "", squash(tm.group("desc")))
            cents = parse_amount(tm.group("amt"))
            amount = -cents                      # card's view -> household view
            blk = block or "other"
            if blk == "payments":
                section = "payment" if is_card_payment(desc) else "credit"
            else:
                section = _BLOCK_SECTION[blk]
                if section == "purchase" and amount > 0:
                    section = "credit"           # a return printed inside PURCHASE
            txn = ParsedTxn(date=md(tm.group("md"), start, end), description=desc,
                            amount_cents=amount, section=section)
            txns.append(txn)
            printed.append((blk, cents))
            last, last_indent = txn, len(tm.group("ind"))
            continue
        if last is not None and indent(line) >= last_indent + 4:
            # FX ("09/29 INDIAN RUPEE", "1.00 X 0.01 (EXCHG RATE)"), "Order Number ..."
            extra = squash(line)
            last.extra = f"{last.extra} | {extra}" if last.extra else extra

    def total(blk: str) -> int:
        return sum(c for b, c in printed if b == blk)

    checks: list = []
    add_check(checks, "Payments/Credits", s["payments"], total("payments"), always=True)
    add_check(checks, "Purchases", s["purchases"], total("purchases"), always=True)
    add_check(checks, "Cash Advances", s["cash_advances"], total("cash_advances"))
    add_check(checks, "Balance Transfers", s["balance_transfers"], total("balance_transfers"))
    add_check(checks, "Fees Charged", s["fees"], total("fees"))
    add_check(checks, "Interest Charged", s["interest"], total("interest"))
    add_check(checks, "Other activity", None, total("other"))   # rows outside known blocks
    add_check(checks, "New Balance", s["new"], s["previous"] + sum(c for _, c in printed),
              always=True)

    summary = {"previous_balance_cents": s["previous"], "new_balance_cents": s["new"]}
    mind = find_amount(rf"Minimum Payment Due:[ \t]*({MONEY})", text)
    if mind is not None:
        summary["minimum_due_cents"] = mind
    due = find(r"Payment Due Date:\s*(\d{2}/\d{2}/\d{2,4})", text)
    if due:
        summary["due_date"] = iso(parse_mdy(due))
    limit = find(rf"^\s*(?:Credit Access Line|Credit Limit)[ \t]+({MONEY_LOOSE})", text)
    if limit:
        summary["credit_limit_cents"] = parse_amount(limit)

    return [ParsedStatement(
        institution="Chase", product=product, account_type="credit", last4=last4,
        holder=_holder(text), period_start=start, period_end=end, parser=PARSER_ID,
        transactions=txns, checks=checks, summary=summary,
    )]
