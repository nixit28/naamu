"""Shared helpers for the statement parsers: text extraction, money, dates, lines.

Everything here is deterministic string/regex work; no network, no models.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

from namu.models import UnsupportedStatement

# --------------------------------------------------------------------------- text

def _is_white(color) -> bool:
    """True for a fill color that renders as white (used for hidden marker text)."""
    if color is None or isinstance(color, str):
        return False
    if isinstance(color, (int, float)):
        color = (color,)
    try:
        vals = tuple(float(v) for v in color)
    except (TypeError, ValueError):
        return False
    if len(vals) == 1:
        return vals[0] >= 0.999                       # gray
    if len(vals) == 3:
        return all(v >= 0.999 for v in vals)          # RGB
    if len(vals) == 4:
        return all(v <= 0.001 for v in vals)          # CMYK
    return False


def _keep_object(obj) -> bool:
    if obj.get("object_type") != "char":
        return True
    # Rotated text (barcodes, margin codes) breaks pdfplumber's line ordering.
    if not obj.get("upright", True):
        return False
    # Chase bank statements carry invisible white "*start*section" markers.
    if _is_white(obj.get("non_stroking_color")):
        return False
    return True


def extract_pages(path: str | Path) -> list[str]:
    """Return the layout text of every page.

    Uses pdfplumber ``page.extract_text(layout=True)`` after dropping rotated and
    white (invisible) characters and de-duplicating the overprinted "fake bold"
    characters Chase uses (which otherwise read as ``AACCCCOOUUNNTT``).
    """
    import pdfplumber  # local import keeps module import cheap

    try:
        pdf = pdfplumber.open(str(path))
    except Exception as exc:  # pdfminer raises a zoo of exception types
        raise UnsupportedStatement(f"Could not open PDF: {exc}") from exc
    pages: list[str] = []
    try:
        for page in pdf.pages:
            clean = page.filter(_keep_object).dedupe_chars()
            text = clean.extract_text(layout=True) or ""
            pages.append("\n".join(line.rstrip() for line in text.splitlines()))
    except Exception as exc:
        raise UnsupportedStatement(f"Could not read PDF text: {exc}") from exc
    finally:
        pdf.close()
    if not any(p.strip() for p in pages):
        raise UnsupportedStatement("PDF has no text layer (scanned image?)")
    return pages


def all_lines(pages: list[str]) -> list[tuple[int, str]]:
    """(page_index, line) for every line of every page."""
    return [(i, line) for i, page in enumerate(pages) for line in page.splitlines()]


def indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def squash(s: str) -> str:
    """Collapse runs of whitespace to one space and strip."""
    return re.sub(r"\s+", " ", s).strip()


def find(pattern: str, text: str, flags: int = re.M, group: int = 1) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(group) if m else None


# -------------------------------------------------------------------------- money

# A money token with cents: -$1,234.56  $1,234.00  .99  -.01  +$2,345.67  1234.56
MONEY = r"[-+]?\$?\s?(?:\d{1,3}(?:,\d{3})+|\d+)?\.\d{2}"
# Same, but cents optional (credit limits print as "$20,000").
MONEY_LOOSE = r"[-+]?\$?\s?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{2})?|[-+]?\$?\.\d{2}"

_MONEY_PARSE = re.compile(
    r"^(?P<open>\()?\s*(?P<sign>[-+])?\s*\$?\s*(?P<sign2>[-+])?\s*"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)?(?:\.(?P<frac>\d{1,2}))?\s*"
    r"(?P<close>\))?\s*(?P<trail>-|CR)?$",
    re.I,
)


def parse_amount(s: str) -> int:
    """Parse a printed amount into integer cents.

    Handles ``-$1,234.56``, ``.99``, ``$1,234.00``, ``-.01``, ``+$2,345.67``,
    ``$20,000``, ``(12.34)`` and trailing ``-``/``CR`` as negative.
    """
    raw = s.strip()
    m = _MONEY_PARSE.match(raw)
    if not m or (m.group("int") is None and m.group("frac") is None):
        raise ValueError(f"not an amount: {s!r}")
    whole = int((m.group("int") or "0").replace(",", ""))
    frac = m.group("frac") or "0"
    cents = whole * 100 + int(frac.ljust(2, "0"))
    negative = (
        m.group("sign") == "-"
        or m.group("sign2") == "-"
        or (m.group("open") and m.group("close"))
        or (m.group("trail") is not None)
    )
    return -cents if negative else cents


def find_amount(pattern: str, text: str, flags: int = re.M) -> int | None:
    """Search ``pattern`` (whose group 1 is a money token) and parse it."""
    v = find(pattern, text, flags)
    return parse_amount(v) if v is not None else None


_TRAILING_MONEY = re.compile(rf"(?:(?<=\s)|^)({MONEY})\s*$")


def split_trailing_amounts(line: str, max_n: int = 2) -> tuple[str, list[str]]:
    """Peel up to ``max_n`` money tokens off the end of ``line``.

    Returns (rest_of_line, [amounts in printed order]).
    """
    rest = line.rstrip()
    found: list[str] = []
    while len(found) < max_n:
        m = _TRAILING_MONEY.search(rest)
        if not m:
            break
        found.insert(0, m.group(1).replace(" ", ""))
        rest = rest[: m.start()].rstrip()
    return rest, found


# -------------------------------------------------------------------------- dates

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_mdy(s: str) -> date:
    """``10/26/26`` or ``10/26/2026`` -> date."""
    mm, dd, yy = re.split(r"[/-]", s.strip())
    year = int(yy)
    if year < 100:
        year += 2000
    return date(year, int(mm), int(dd))


def parse_long_date(s: str) -> date:
    """``September 10, 2026`` / ``Sep 10, 2026`` -> date."""
    m = re.match(r"\s*([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", s)
    if not m:
        raise ValueError(f"not a date: {s!r}")
    return date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))


def infer_date(month: int, day: int, start: date, end: date) -> date:
    """Pick the year for a MM/DD that belongs to the statement period start..end.

    Chooses the candidate date nearest the period (0 distance when inside it), so
    Dec->Jan periods and transactions dated a few days before the opening date
    both resolve correctly.
    """
    best: tuple[int, date] | None = None
    for year in range(start.year - 1, end.year + 2):
        try:
            cand = date(year, month, day)
        except ValueError:          # Feb 29 in a non-leap year
            continue
        if cand < start:
            dist = (start - cand).days
        elif cand > end:
            dist = (cand - end).days
        else:
            dist = 0
        if best is None or dist < best[0]:
            best = (dist, cand)
    if best is None:
        raise ValueError(f"invalid month/day {month}/{day}")
    return best[1]


def md(s: str, start: date, end: date) -> date:
    """``09/25`` -> date inside (or nearest) the statement period."""
    mm, dd = s.strip().split("/")[:2]
    return infer_date(int(mm), int(dd), start, end)


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def month_before(d: date) -> date:
    """Same day one month earlier (clamped), plus one day: a default period start."""
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day) + timedelta(days=1)
        except ValueError:
            continue
    return d - timedelta(days=30)


# ---------------------------------------------------------------------- semantics

_CARD_PAYMENT = re.compile(
    r"\bAUTOPAY\b|\bAUTO[- ]?PMT\b|\bAUTOMATIC PAYMENT\b|\bPAYMENT\b.*\bTHANK\s*YOU\b"
    r"|\bONLINE PAYMENT\b|\bMOBILE PAYMENT\b|\bELECTRONIC PAYMENT\b|\bPAYMENT RECEIVED\b"
    r"|^PAYMENT\b|\bE-?PAYMENT\b",
    re.I,
)


def is_card_payment(description: str) -> bool:
    """True for a real payment onto a card (not a merchant refund).

    Deliberately does not match "AMAZON MKTPLACE PMTS", which is a refund.
    """
    return bool(_CARD_PAYMENT.search(description))


def add_check(checks: list, label: str, expected: int | None, parsed: int,
              always: bool = False) -> None:
    """Append a Check when the statement printed a total for it.

    Zero/zero checks are skipped unless ``always``; a nonzero parsed sum with no
    printed total becomes a failing check so it can't silently pass.
    """
    from namu.models import Check

    if expected is None:
        if parsed:
            checks.append(Check(label, 0, parsed))
        return
    if always or expected or parsed:
        checks.append(Check(label, expected, parsed))
