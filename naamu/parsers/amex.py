"""American Express cards (Blue Cash Everyday, Gold, Platinum, ...).

Layout: page 1 Account Summary (Previous Balance, Payments/Credits, New Charges,
Fees, Interest Charged, New Balance), "Closing Date MM/DD/YY", "Account Ending
9-12345". Detail pages: "Payments and Credits" (Summary, then Detail with
Payments / Credits sub-blocks), "New Charges" (Summary, then Detail grouped by
card member with "Card Ending ..."), "Fees", "Interest Charged". Rows are
MM/DD/YY (a trailing * marks the posting date) with amounts like -$123.45 /
$200.00; the following indented lines (city, phone, bank name) are continuation.
"""
from __future__ import annotations

import re
from datetime import timedelta

from naamu.models import ParsedStatement, ParsedTxn

from .common import (
    MONEY, MONEY_LOOSE, add_check, all_lines, find, find_amount, indent, iso, month_before,
    parse_amount, parse_mdy, squash,
)

PARSER_ID = "amex"


def detect(text: str) -> bool:
    return bool(re.search(r"americanexpress\.com", text, re.I)) and bool(
        re.search(r"Account Ending", text)) and bool(re.search(r"Closing Date", text))


_TXN = re.compile(
    rf"^(?P<ind>\s*)(?P<date>\d{{2}}/\d{{2}}/\d{{2,4}})(?P<star>\*?)\s+(?P<desc>.*?\S)\s+"
    rf"(?P<amt>{MONEY})\s*(?:⧫|⧫)?\s*$")
_UNDATED = re.compile(rf"^(?P<ind>\s*)(?P<desc>[A-Za-z].*?\S)\s+(?P<amt>{MONEY})\s*$")
_BLOCK_HEADERS = [
    (re.compile(r"^\s*Payments\s+and\s+Credits\s*$"), None),
    (re.compile(r"^\s*Payments\s+Amount\s*$"), "payments"),
    (re.compile(r"^\s*Credits\s+Amount\s*$"), "credits"),
    (re.compile(r"^\s*New\s+Charges\s*$"), "charges"),
    (re.compile(r"^\s*Fees\s*$"), "fees"),
    (re.compile(r"^\s*Interest\s+Charged\s*$"), "interest"),
]
_END = re.compile(
    r"About Trailing Interest|Fees and Interest Totals Year-to-Date|Interest Charge Calculation"
    r"|^\s*Important Notices\b", re.I)
_SECTION = {"payments": "payment", "credits": "credit", "charges": "purchase",
            "fees": "fee", "interest": "interest"}
_NOT_CONT = re.compile(r"Account Ending|Closing Date|^\s*p\.\s*\d+/\d+|Continued on|Amount\s*$", re.I)


def parse(pages: list[str]) -> list[ParsedStatement]:
    text = "\n".join(pages)

    closing = find(r"Closing Date\s*(\d{2}/\d{2}/\d{2,4})", text)
    if not closing:
        raise ValueError("Amex: no Closing Date")
    end = parse_mdy(closing)
    days = find(r"Days in Billing Period:\s*(\d+)", text)
    start = end - timedelta(days=int(days) - 1) if days else month_before(end)

    acct = find(r"Account Ending\s*([\d-]+)", text)
    if not acct or len(re.sub(r"\D", "", acct)) < 4:
        raise ValueError("Amex: no account number")
    last4 = re.sub(r"\D", "", acct)[-4:]

    product = find(r"^\s*(?:The\s+)?(.+?)\s*®?\s+from American Express", text) or "Card"
    product = squash(product.replace("®", "")) or "Card"
    holder = find(r"^\s*([A-Z][A-Z.'\- ]*?[A-Z.])\s{2,}(?:Closing Date|Account Ending)", text)

    def amt(label: str) -> int | None:
        return find_amount(rf"{label}[ \t]+({MONEY})", text)

    s = {
        "previous": amt(r"Previous Balance"),
        "payments_credits": amt(r"Payments/Credits"),
        "charges": amt(r"(?<!Total )New Charges"),
        "fees": amt(r"(?<!Total )\bFees"),
        "interest": amt(r"(?<!Total )Interest Charged"),
        "new": amt(r"New Balance"),
        # From the "Payments and Credits Summary" table on the detail pages.
        "payments": find_amount(rf"^\s*Payments[ \t]+({MONEY})\s*$", text),
        "credits": find_amount(rf"^\s*Credits[ \t]+({MONEY})\s*$", text),
    }
    if s["previous"] is None or s["new"] is None:
        raise ValueError("Amex: no Previous/New Balance")

    txns: list[ParsedTxn] = []
    printed: list[tuple[str, int]] = []
    block = None
    cardholder = None
    last: ParsedTxn | None = None
    last_indent = 0
    prev_line = ""
    for _, line in all_lines(pages):
        if _END.search(line):
            block, last = None, None
            continue
        hdr = next((b for rx, b in _BLOCK_HEADERS if rx.match(line)), "none")
        if hdr != "none":
            block, last = hdr, None
            if hdr != "charges":
                cardholder = None
            continue
        if not line.strip():
            last = None
            continue
        if re.match(r"^\s*Card Ending", line) and block == "charges":
            cardholder = squash(prev_line) or None
            last = None
            prev_line = line
            continue
        prev_line = line
        if block is None:
            continue
        tm = _TXN.match(line)
        if tm:
            cents = parse_amount(tm.group("amt"))
            d = parse_mdy(tm.group("date"))
            section = _SECTION[block]
            if block == "charges" and cents < 0:
                section = "credit"
            txn = ParsedTxn(date=d, description=squash(tm.group("desc")), amount_cents=-cents,
                            section=section, post_date=d if tm.group("star") else None,
                            cardholder=cardholder if block == "charges" else None)
            txns.append(txn)
            printed.append((block, cents))
            last, last_indent = txn, len(tm.group("ind"))
            continue
        if block in ("fees", "interest"):
            um = _UNDATED.match(line)
            if um and not re.match(r"\s*Total\b", line, re.I):
                cents = parse_amount(um.group("amt"))
                txn = ParsedTxn(date=end, description=squash(um.group("desc")),
                                amount_cents=-cents, section=_SECTION[block])
                txns.append(txn)
                printed.append((block, cents))
                last = None
                continue
        if last is not None and indent(line) >= last_indent + 3 and not _NOT_CONT.search(line):
            extra = squash(line)
            last.extra = f"{last.extra} | {extra}" if last.extra else extra
        else:
            last = None

    def total(*blocks: str) -> int:
        return sum(c for b, c in printed if b in blocks)

    checks: list = []
    if s["payments"] is not None or s["credits"] is not None:
        add_check(checks, "Payments", s["payments"] or 0, total("payments"), always=True)
        add_check(checks, "Credits", s["credits"] or 0, total("credits"), always=True)
    else:
        add_check(checks, "Payments/Credits", s["payments_credits"],
                  total("payments", "credits"), always=True)
    add_check(checks, "New Charges", s["charges"], total("charges"), always=True)
    add_check(checks, "Fees", s["fees"], total("fees"))
    add_check(checks, "Interest Charged", s["interest"], total("interest"))
    add_check(checks, "New Balance", s["new"], s["previous"] + total(*_SECTION), always=True)

    summary = {"previous_balance_cents": s["previous"], "new_balance_cents": s["new"]}
    mind = find_amount(rf"Minimum Payment Due[ \t]+({MONEY})", text)
    if mind is not None:
        summary["minimum_due_cents"] = mind
    due = find(r"Payment Due Date\s+(\d{2}/\d{2}/\d{2,4})", text)
    if due:
        summary["due_date"] = iso(parse_mdy(due))
    limit = find(rf"Credit Limit[ \t]+({MONEY_LOOSE})", text)
    if limit:
        summary["credit_limit_cents"] = parse_amount(limit)

    return [ParsedStatement(
        institution="American Express", product=product, account_type="credit", last4=last4,
        holder=squash(holder) if holder else None, period_start=start, period_end=end,
        parser=PARSER_ID, transactions=txns, checks=checks, summary=summary,
    )]
