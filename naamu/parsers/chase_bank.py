"""Chase deposit accounts (Total Checking, Savings, ...), incl. consolidated statements.

Layout: period "September 10, 2026 through October 08, 2026"; for each account a
product header ("CHASE TOTAL CHECKING") with the holder name(s) and
"Account Number:", a "<TYPE> SUMMARY" (Beginning Balance, category totals such as
Deposits and Additions / Electronic Withdrawals / Checks Paid / Fees, Ending
Balance) and, when there was activity, a TRANSACTION DETAIL table with AMOUNT and
BALANCE columns that may continue across pages under "(continued)" headers.
"""
from __future__ import annotations

import re

from naamu.models import ParsedStatement, ParsedTxn

from .common import (
    MONEY, add_check, all_lines, indent, md, parse_amount, parse_long_date, split_trailing_amounts,
    squash,
)

PARSER_ID = "chase_bank"

_LONG = r"[A-Z][a-z]+\.?\s+\d{1,2},\s*\d{4}"
_PERIOD = re.compile(rf"({_LONG})\s*through\s*({_LONG})")
_PRODUCT_HEADER = re.compile(r"^\s*(CHASE(?:\s+[A-Z][A-Z&']*)+)\s*$")
_SUMMARY_HEADER = re.compile(r"^\s*(CHECKING|SAVINGS|[A-Z ]+?)\s+SUMMARY\b")
_SUMMARY_LINE = re.compile(rf"^\s*(?P<label>[A-Za-z][A-Za-z&,'/\- ]*?[A-Za-z])\s+(?:\d+\s+)?(?P<amt>{MONEY})\s*$")
_DETAIL_HEADER = re.compile(r"^\s*TRANSACTION\s+DETAIL\b")
_ROW = re.compile(r"^(?P<ind>\s*)(?P<md>\d{2}/\d{2})\s+(?P<rest>\S.*)$")
_STRUCTURAL = re.compile(
    r"^\s*(?:DATE\s+DESCRIPTION|\(continued\)|Page\s+of\b|\d+\s+\d+\s*$|Primary Account"
    rf"|\d{{9,}}\s*$|{_LONG}\s*through)", re.I)


def detect(text: str) -> bool:
    return bool(_PERIOD.search(text)) and bool(
        re.search(r"JPMorgan Chase Bank|Chase\.com", text, re.I)) and bool(
        re.search(r"^\s*(?:CHECKING|SAVINGS)\s+SUMMARY\b|TRANSACTION\s+DETAIL", text, re.M))


def _product_name(header: str) -> str:
    words = squash(header).split()[1:]          # drop "CHASE"
    return " ".join(w.capitalize() for w in words) or "Account"


def _consolidated_numbers(text: str) -> dict[str, str]:
    """'chase total checking' -> account number, from the CONSOLIDATED BALANCE SUMMARY."""
    out = {}
    for m in re.finditer(r"^\s*(Chase(?:\s+[A-Za-z&']+)+?)\s+(\d{8,})\b", text, re.M):
        out.setdefault(squash(m.group(1)).lower(), m.group(2))
    return out


def parse(pages: list[str]) -> list[ParsedStatement]:
    text = "\n".join(pages)
    pm = _PERIOD.search(text)
    if not pm:
        raise ValueError("Chase bank: no statement period")
    start, end = parse_long_date(pm.group(1)), parse_long_date(pm.group(2))
    lines = [line for _, line in all_lines(pages)]
    page_starts = set()
    n = 0
    for p in pages:
        page_starts.add(n)
        n += len(p.splitlines())

    # Locate account sections: a CHASE <PRODUCT> header with "Account Number" right below.
    heads = []
    for i, line in enumerate(lines):
        if _PRODUCT_HEADER.match(line) and any(
                "Account Number" in l for l in lines[i:i + 4]):
            heads.append(i)
    if not heads:
        raise ValueError("Chase bank: no account sections")
    numbers = _consolidated_numbers(text)

    statements = []
    for k, h in enumerate(heads):
        body = lines[h:heads[k + 1] if k + 1 < len(heads) else len(lines)]
        body_starts = {i - h for i in page_starts if h <= i}
        statements.append(_parse_account(body, body_starts, start, end, numbers))
    return statements


def _parse_account(body: list[str], page_starts: set[int], start, end,
                   numbers: dict[str, str]) -> ParsedStatement:
    header = squash(body[0])
    product = _product_name(header)

    # Holder name(s) + account number live between the header and the SUMMARY.
    holder_parts, number = [], None
    i = 1
    while i < len(body) and not _SUMMARY_HEADER.match(body[i]) and i < 8:
        line = body[i]
        m = re.search(r"Account Number:\s*(\d+)?", line)
        name = line[: m.start()] if m else line
        if m and m.group(1):
            number = m.group(1)
        if name.strip():
            holder_parts.append(squash(name))
        i += 1
    number = number or numbers.get(header.lower())
    if not number:
        raise ValueError(f"Chase bank: no account number for {header}")

    sm = _SUMMARY_HEADER.match(body[i]) if i < len(body) else None
    kind = (sm.group(1).strip().lower() if sm else "")
    account_type = "savings" if "SAVINGS" in header or kind == "savings" else "checking"

    # --- summary block
    opening = closing = None
    categories: list[tuple[str, int]] = []
    j = i + 1
    while j < len(body):
        line = body[j]
        if _DETAIL_HEADER.match(line):
            break
        lm = _SUMMARY_LINE.match(line)
        if lm:
            label, cents = squash(lm.group("label")), parse_amount(lm.group("amt"))
            if re.fullmatch(r"Beginning Balance", label, re.I):
                opening = cents
            elif re.fullmatch(r"Ending Balance", label, re.I):
                closing = cents
                j += 1
                break
            else:
                categories.append((label, cents))
        j += 1
    if opening is None or closing is None:
        raise ValueError(f"Chase bank: no Beginning/Ending Balance for {header}")

    # --- transaction detail (may span pages; continuation lines -> extra)
    txns: list[ParsedTxn] = []
    in_detail = False
    last: ParsedTxn | None = None
    last_indent = 0
    pending: tuple[str, str, int] | None = None   # date row whose amount wrapped
    for idx in range(j, len(body)):
        line = body[idx]
        if idx in page_starts:
            last = None
        if _DETAIL_HEADER.match(line):
            in_detail, last = True, None
            continue
        if not in_detail or not line.strip():
            continue
        flat = squash(line)
        if re.match(r"Ending Balance\b", flat, re.I):
            in_detail, last, pending = False, None, None
            continue
        if re.match(r"Beginning Balance\b", flat, re.I) or _STRUCTURAL.match(line):
            continue
        rm = _ROW.match(line)
        if rm:
            rest, amounts = split_trailing_amounts(rm.group("rest"))
            if not amounts:
                pending = (rm.group("md"), squash(rest), len(rm.group("ind")))
                last = None
                continue
            pending = None
            last = _emit(txns, rm.group("md"), squash(rest), None, amounts, start, end)
            last_indent = len(rm.group("ind"))
            continue
        if pending is not None:
            rest, amounts = split_trailing_amounts(line)
            if amounts:
                md_, desc, ind = pending
                last = _emit(txns, md_, desc, squash(rest) or None, amounts, start, end)
                last_indent, pending = ind, None
            else:
                # keep accumulating wrapped text until the amount line shows up
                md_, desc, ind = pending
                pending = (md_, f"{desc} {flat}".strip(), ind)
            continue
        if last is not None and indent(line) > last_indent:
            last.extra = f"{last.extra} | {flat}" if last.extra else flat

    # --- checks
    checks: list = []
    pos = [(l, c) for l, c in categories if c > 0]
    neg = [(l, c) for l, c in categories if c < 0]
    parsed_pos = sum(t.amount_cents for t in txns if t.amount_cents > 0)
    parsed_neg = sum(t.amount_cents for t in txns if t.amount_cents < 0)
    if pos:
        add_check(checks, " + ".join(l for l, _ in pos), sum(c for _, c in pos), parsed_pos,
                  always=True)
    else:
        add_check(checks, "Deposits and Additions", None, parsed_pos)
    if neg:
        add_check(checks, " + ".join(l for l, _ in neg), sum(c for _, c in neg), parsed_neg,
                  always=True)
    else:
        add_check(checks, "Withdrawals", None, parsed_neg)
    add_check(checks, "Ending Balance", closing, opening + parsed_pos + parsed_neg, always=True)

    return ParsedStatement(
        institution="Chase", product=product, account_type=account_type, last4=number[-4:],
        holder=" ".join(holder_parts) or None, period_start=start, period_end=end,
        parser=PARSER_ID, transactions=txns, checks=checks,
        summary={"opening_balance_cents": opening, "closing_balance_cents": closing},
    )


def _emit(txns, md_str, desc, extra, amounts, start, end) -> ParsedTxn:
    # With AMOUNT and BALANCE columns the txn amount is the second-to-last number.
    cents = parse_amount(amounts[-2] if len(amounts) >= 2 else amounts[-1])
    txn = ParsedTxn(date=md(md_str, start, end), description=desc, amount_cents=cents,
                    section="deposit" if cents > 0 else "withdrawal", extra=extra)
    txns.append(txn)
    return txn
