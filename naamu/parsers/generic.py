"""Heuristic fallback for statements no dedicated parser recognizes.

Takes every line that starts with a date and ends with an amount. Output has no
checks (``reconciled is None`` -> shown as unverified). Institution, last4 and
period are best effort. Raises UnsupportedStatement when no rows are found.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from naamu.models import ParsedStatement, ParsedTxn, UnsupportedStatement

from .common import (
    MONTHS, all_lines, infer_date, is_card_payment, parse_amount, parse_long_date, parse_mdy,
    split_trailing_amounts, squash,
)

PARSER_ID = "generic"

_INSTITUTIONS = [
    ("American Express", r"American Express|americanexpress\.com"),
    ("Bank of America", r"Bank of America|bankofamerica\.com"),
    ("Wells Fargo", r"Wells Fargo|wellsfargo\.com"),
    ("Capital One", r"Capital One|capitalone\.com"),
    ("Discover", r"\bDiscover\b|discover\.com"),
    ("Barclays", r"Barclays|barclaysus\.com"),
    ("Citi", r"\bCiti(?:bank|cards)?\b|citi\.com"),
    ("Chase", r"\bChase\b|chase\.com"),
    ("U.S. Bank", r"U\.S\. Bank|usbank\.com"),
    ("PNC", r"\bPNC\b"),
    ("TD Bank", r"\bTD Bank\b"),
    ("Ally", r"\bAlly Bank\b|ally\.com"),
    ("Synchrony", r"Synchrony"),
    ("Navy Federal", r"Navy Federal"),
    ("USAA", r"\bUSAA\b"),
    ("Charles Schwab", r"Schwab"),
    ("Fidelity", r"Fidelity"),
    ("Goldman Sachs", r"Goldman Sachs"),
    ("Apple Card", r"Apple Card"),
    ("HSBC", r"\bHSBC\b"),
    ("Citizens", r"Citizens Bank"),
    ("Truist", r"Truist"),
    ("Santander", r"Santander"),
    ("PayPal", r"PayPal Credit"),
]

_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_LEAD_DATE = re.compile(
    rf"^\s*(?P<d>\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|\d{{4}}-\d{{2}}-\d{{2}}|{_MON}\s+\d{{1,2}}(?:,?\s+\d{{4}})?)\*?"
    rf"(?:\s+(?:\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|{_MON}\s+\d{{1,2}}))?\s+(?P<rest>\S.*)$", re.I)
_RANGES = [
    re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:-|–|to|through)\s*(\d{1,2}/\d{1,2}/\d{2,4})", re.I),
    re.compile(rf"({_MON}\s+\d{{1,2}},?\s+\d{{4}})\s*(?:-|–|to|through)\s*({_MON}\s+\d{{1,2}},?\s+\d{{4}})", re.I),
]


def _institution(text: str) -> str:
    head = text[:4000]
    hits = []
    for name, pat in _INSTITUTIONS:
        m = re.search(pat, head, re.I)
        if m:
            hits.append((len(re.findall(pat, text, re.I)), -m.start(), name))
    return max(hits)[2] if hits else "Unknown"


def _last4(text: str) -> str:
    m = re.search(
        r"(?:Account|Card)\s*(?:Number|No\.?|#|Ending(?:\s+in)?)\s*:?\s*[X*•.\-\s\d]*?(\d{4})\b",
        text, re.I)
    return m.group(1) if m else ""


def _period(text: str) -> tuple[date, date] | None:
    for rx in _RANGES:
        for m in rx.finditer(text):
            try:
                a, b = (parse_mdy(m.group(1)), parse_mdy(m.group(2))) if "/" in m.group(1) else (
                    parse_long_date(m.group(1)), parse_long_date(m.group(2)))
            except (ValueError, KeyError):
                continue
            if a <= b and (b - a).days <= 62:
                return a, b
    return None


def _to_date(token: str, start: date, end: date) -> date | None:
    token = token.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
            return date.fromisoformat(token)
        if "/" in token:
            parts = token.split("/")
            if len(parts) == 3:
                return parse_mdy(token)
            return infer_date(int(parts[0]), int(parts[1]), start, end)
        m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?", token)
        if m:
            month, day = MONTHS[m.group(1).lower()], int(m.group(2))
            if m.group(3):
                return date(int(m.group(3)), month, day)
            return infer_date(month, day, start, end)
    except (ValueError, KeyError):
        return None
    return None


def parse(pages: list[str]) -> list[ParsedStatement]:
    text = "\n".join(pages)
    is_credit = bool(re.search(
        r"Credit Limit|Credit Line|Minimum Payment|Payment Due Date|Available Credit", text, re.I))
    account_type = "credit" if is_credit else (
        "savings" if re.search(r"\bSavings\b", text, re.I) and not re.search(
            r"\bChecking\b", text, re.I) else "checking")

    period = _period(text)
    if period:
        start, end = period
    else:
        full = [parse_mdy(m) for m in re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
                if _safe(parse_mdy, m)]
        end = max(full) if full else date.today()
        start = end - timedelta(days=35)

    txns: list[ParsedTxn] = []
    for _, line in all_lines(pages):
        m = _LEAD_DATE.match(line)
        if not m:
            continue
        body = m.group("rest")
        cr = re.search(r"\s*\bCR\s*$", body)      # "12.34 CR" = credit on a card
        if cr:
            body = body[: cr.start()]
        rest, amounts = split_trailing_amounts(body)
        if not amounts or not re.search(r"[A-Za-z]", rest):
            continue
        d = _to_date(m.group("d"), start, end)
        if d is None:
            continue
        desc = squash(rest)
        if is_credit:
            cents = -parse_amount(amounts[-1])      # cards print charges positive
            if cr:
                cents = abs(cents)
            section = "purchase" if cents < 0 else (
                "payment" if is_card_payment(desc) else "credit")
        else:
            cents = parse_amount(amounts[-2] if len(amounts) >= 2 else amounts[-1])
            section = "deposit" if cents > 0 else "withdrawal"
        if cents == 0:
            continue
        txns.append(ParsedTxn(date=d, description=desc, amount_cents=cents, section=section))

    if not txns:
        raise UnsupportedStatement("Unrecognized statement format")
    if not period:
        start, end = min(t.date for t in txns), max(t.date for t in txns)

    product = {"credit": "Credit Card", "checking": "Checking", "savings": "Savings"}[account_type]
    return [ParsedStatement(
        institution=_institution(text), product=product, account_type=account_type,
        last4=_last4(text), holder=None, period_start=start, period_end=end,
        parser=PARSER_ID, transactions=txns, checks=[], summary={},
    )]


def _safe(fn, *args) -> bool:
    try:
        fn(*args)
        return True
    except (ValueError, KeyError):
        return False
