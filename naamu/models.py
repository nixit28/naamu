"""Shared data model between the PDF parsers and the ingest pipeline.

Money is always integer cents. Signs are from the household's point of view:
negative = money leaving (purchase, withdrawal, fee, interest),
positive = money arriving (deposit, payroll, payment onto a card, refund/credit).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Normalized statement sections a parser may emit.
SECTIONS = (
    "purchase",      # card purchase
    "payment",       # payment received on a card (always a transfer candidate)
    "credit",        # merchant credit / refund on a card
    "fee",
    "interest",
    "cash_advance",
    "deposit",       # bank account money in
    "withdrawal",    # bank account money out
    "other",
)


class UnsupportedStatement(Exception):
    """Raised when no parser recognizes the PDF."""


@dataclass
class ParsedTxn:
    date: date                       # sale/transaction date (post date if that's all there is)
    description: str                 # raw first-line description, whitespace collapsed
    amount_cents: int                # signed, see module docstring
    section: str                     # one of SECTIONS
    post_date: date | None = None
    cardholder: str | None = None    # set when the statement groups txns by cardholder
    extra: str | None = None         # continuation lines (order no., FX rate, city/phone)


@dataclass
class Check:
    """One reconciliation line: a total printed on the statement vs the sum we parsed."""
    label: str                       # e.g. "Purchases", "Payments/Credits", "Deposits"
    expected_cents: int
    parsed_cents: int

    @property
    def ok(self) -> bool:
        return self.expected_cents == self.parsed_cents


@dataclass
class ParsedStatement:
    institution: str                 # "Chase", "American Express", "Citi", ...
    product: str                     # "Freedom Unlimited", "Total Checking", ...
    account_type: str                # "credit" | "checking" | "savings"
    last4: str
    holder: str | None
    period_start: date
    period_end: date
    parser: str                      # parser id, e.g. "chase_card"
    transactions: list[ParsedTxn] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)   # empty => unverified (generic parser)
    # Optional statement-level facts. Keys (all optional): previous_balance_cents,
    # new_balance_cents, opening_balance_cents, closing_balance_cents,
    # minimum_due_cents, due_date (ISO str), credit_limit_cents
    summary: dict = field(default_factory=dict)

    @property
    def reconciled(self) -> bool | None:
        if not self.checks:
            return None
        return all(c.ok for c in self.checks)
