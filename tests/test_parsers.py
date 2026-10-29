"""Parser tests.

* Real statements: every ``samples/*.pdf`` (gitignored, local only) must parse,
  reconcile and have consistent signs. Exact regression values live in
  ``samples/expected.json`` (also gitignored); regenerate it from the current
  parser output with::

      .venv/bin/python -m tests.test_parsers --write-expected

* Synthetic layouts (fictional names, accounts and amounts) cover year
  inference, extra sections, page breaks and the generic fallback.
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pytest

from namu.models import SECTIONS, UnsupportedStatement
from namu.parsers import parse_pages, parse_pdf
from namu.parsers.common import infer_date, is_card_payment, parse_amount, split_trailing_amounts

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
EXPECTED_FILE = SAMPLES / "expected.json"

NEGATIVE = {"purchase", "fee", "interest", "cash_advance", "withdrawal"}
POSITIVE = {"payment", "credit", "deposit"}


# ------------------------------------------------------------ real samples

def _sample_names() -> list[str]:
    return sorted(p.name for p in SAMPLES.glob("*.pdf")) if SAMPLES.is_dir() else []


_NAMES = _sample_names() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no sample PDFs in samples/"))]


@lru_cache(maxsize=None)
def _parsed(name: str):
    return parse_pdf(SAMPLES / name)


def snapshot(statements) -> list[dict]:
    """JSON-able exact view of parser output (for samples/expected.json)."""
    def d(x):
        return x.isoformat() if x else None

    return [{
        "parser": st.parser, "institution": st.institution, "product": st.product,
        "account_type": st.account_type, "last4": st.last4, "holder": st.holder,
        "period": [d(st.period_start), d(st.period_end)], "summary": st.summary,
        "checks": [[c.label, c.expected_cents, c.parsed_cents] for c in st.checks],
        "transactions": [[d(t.date), d(t.post_date), t.description, t.amount_cents, t.section,
                          t.cardholder, t.extra] for t in st.transactions],
    } for st in statements]


def write_expected(path: Path = EXPECTED_FILE) -> dict:
    data = {name: snapshot(_parsed(name)) for name in _sample_names()}
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    return data


@lru_cache(maxsize=None)
def _expected() -> dict:
    return json.loads(EXPECTED_FILE.read_text()) if EXPECTED_FILE.exists() else {}


@pytest.mark.parametrize("name", _NAMES)
def test_sample_parses_and_reconciles(name):
    statements = _parsed(name)
    assert statements
    for st in statements:
        failing = [(c.label, c.expected_cents, c.parsed_cents) for c in st.checks if not c.ok]
        assert st.checks and not failing, failing
        assert st.reconciled is True
        assert st.institution and st.product
        assert st.account_type in ("credit", "checking", "savings")
        assert len(st.last4) == 4 and st.last4.isdigit()
        assert st.period_start <= st.period_end
        assert 20 <= (st.period_end - st.period_start).days <= 40
        for t in st.transactions:
            assert st.period_start - timedelta(days=45) <= t.date <= st.period_end + timedelta(days=10), t
            assert t.description and t.description == " ".join(t.description.split())


@pytest.mark.parametrize("name", _NAMES)
def test_sample_sections_and_signs(name):
    for st in _parsed(name):
        for t in st.transactions:
            assert t.section in SECTIONS, t
            assert isinstance(t.amount_cents, int) and t.amount_cents != 0, t
            if t.section in NEGATIVE:
                assert t.amount_cents < 0, t
            elif t.section in POSITIVE:
                assert t.amount_cents > 0, t
            if st.account_type == "credit":
                assert t.section not in ("deposit", "withdrawal"), t
            else:
                assert t.section in ("deposit", "withdrawal"), t
            # continuation lines (FX rates, order numbers) never become rows
            assert not t.description.startswith(("Order Number", "Transaction#"))
            assert "(EXCHG RATE)" not in t.description


@pytest.mark.parametrize("name", _NAMES)
def test_sample_matches_expected(name):
    expected = _expected().get(name)
    if expected is None:
        pytest.skip("no entry in samples/expected.json (run: python -m tests.test_parsers --write-expected)")
    assert snapshot(_parsed(name)) == expected


# ---------------------------------------------------------------- helper units

@pytest.mark.parametrize("text,cents", [
    ("-$1,357.24", -135724), (".99", 99), ("$1,234.00", 123400), ("-.01", -1),
    ("+$2,468.10", 246810), ("$15,000", 1500000), ("12,000.00", 1200000),
    ("(12.34)", -1234), ("12.34 CR", -1234), ("$0.00", 0),
])
def test_parse_amount(text, cents):
    assert parse_amount(text) == cents


@pytest.mark.parametrize("bad", ["", "$", "abc", "12/34"])
def test_parse_amount_rejects(bad):
    with pytest.raises(ValueError):
        parse_amount(bad)


def test_infer_year_across_december_january():
    start, end = date(2026, 12, 15), date(2027, 1, 14)
    assert infer_date(12, 20, start, end) == date(2026, 12, 20)
    assert infer_date(1, 5, start, end) == date(2027, 1, 5)
    assert infer_date(12, 13, start, end) == date(2026, 12, 13)   # before opening date
    assert infer_date(1, 16, start, end) == date(2027, 1, 16)     # posted after closing


def test_infer_year_regular_and_leap():
    assert infer_date(3, 1, date(2026, 3, 2), date(2026, 4, 1)) == date(2026, 3, 1)
    assert infer_date(2, 29, date(2028, 2, 10), date(2028, 3, 9)) == date(2028, 2, 29)


def test_split_trailing_amounts():
    rest, amts = split_trailing_amounts(
        "03/15 Example Bank Collection 000000000000001 Web ID: 0000000001 -1,250.00 4,321.09")
    assert amts == ["-1,250.00", "4,321.09"] and rest.endswith("0000000001")


def test_is_card_payment():
    assert is_card_payment("AUTOMATIC PAYMENT - THANK YOU")
    assert is_card_payment("Payment Thank You-Mobile")
    assert is_card_payment("AUTOPAY 000000000000000RAUTOPAY AUTO-PMT")
    assert not is_card_payment("AMAZON MKTPLACE PMTS Amzn.com/bill WA")   # merchant refund
    assert not is_card_payment("SAMPLE WAREHOUSE #100 ANYTOWN ST")


# ------------------------------------------------------- synthetic layouts

def _check_failures(st):
    return [(c.label, c.expected_cents, c.parsed_cents) for c in st.checks if not c.ok]


CHASE_CARD_DEC_JAN = [
    """
                          www.chase.com/cardhelp 1-800-000-0000 Chase Mobile app today
                                      CHASE  FREEDOM   FLEX
  ACCOUNT    SUMMARY
  Account Number: XXXX XXXX XXXX 1234
  Previous Balance            $100.00
  Payment, Credits           -$112.50
  Purchases                   +$75.00
  Cash Advances                 $0.00
  Balance Transfers             $0.00
  Fees Charged                 +$39.00
  Interest Charged              +$1.23
  New Balance                 $102.73
  Opening/Closing Date   12/15/26 - 01/14/27
  Credit Access Line           $5,000
                       Payment Due Date: 02/08/27
                                     Minimum Payment Due:         $40.00
""",
    """
    ACCOUNT   ACTIVITY
     Date of
    Transaction          Merchant Name or Transaction Description $ Amount
    PAYMENTS AND OTHER CREDITS
    01/02      & Payment Thank You-Mobile                      -100.00
    01/04      AMAZON MKTPLACE PMTS Amzn.com/bill WA            -12.50
                Order Number 111-0000000-0000001
    PURCHASE
    12/14      CORNER MARKET #12 SPRINGFIELD IL                  20.00
    01/03      CAFE EXEMPLAR PARIS                               30.00
                01/03 EURO
                 27.40 X 1.094890510 (EXCHG RATE)
    01/04      AMAZON MKTPL*AB12CD34 Amzn.com/bill WA            25.00
  ALEX J SMITH                    Page2 of 3            Statement Date: 01/14/27
   0000001 FIS00000 D 000 Y 0 00 27/01/14 Page 2 of 3 00000 XX XX 00000 00000000000000000000
""",
    """
    ACCOUNT   ACTIVITY  (CONTINUED)
     Date of
    Transaction           Merchant Name or Transaction Description $ Amount
                Order Number 111-0000000-0000002
    FEES CHARGED
    01/05      LATE FEE                                          39.00
    TOTAL FEES FOR THIS PERIOD                                  $39.00
    INTEREST CHARGED
    01/14      PURCHASE INTEREST CHARGE                           1.23
    TOTAL INTEREST FOR THIS PERIOD                               $1.23
                               2027 Totals Year-to-Date
    INTEREST CHARGES
    PURCHASES
     Purchases            26.74%(v)(d)        150.00    1.23
""",
]


def test_chase_card_dec_jan_page_break_fees_interest():
    (st,) = parse_pages(CHASE_CARD_DEC_JAN)
    assert (st.parser, st.product, st.last4, st.holder) == (
        "chase_card", "Freedom Flex", "1234", "ALEX J SMITH")
    assert (st.period_start, st.period_end) == (date(2026, 12, 15), date(2027, 1, 14))
    assert st.reconciled is True, _check_failures(st)
    assert len(st.transactions) == 7
    by_desc = {t.description: t for t in st.transactions}
    assert by_desc["CORNER MARKET #12 SPRINGFIELD IL"].date == date(2026, 12, 14)
    assert by_desc["CAFE EXEMPLAR PARIS"].date == date(2027, 1, 3)
    assert "EXCHG RATE" in by_desc["CAFE EXEMPLAR PARIS"].extra
    pay = by_desc["Payment Thank You-Mobile"]            # "& " marker stripped
    assert (pay.section, pay.amount_cents) == ("payment", 10000)
    refund = by_desc["AMAZON MKTPLACE PMTS Amzn.com/bill WA"]
    assert (refund.section, refund.amount_cents) == ("credit", 1250)
    assert "0000001" in refund.extra
    # Order Number continuation after the page break + repeated header
    assert "0000002" in by_desc["AMAZON MKTPL*AB12CD34 Amzn.com/bill WA"].extra
    assert (by_desc["LATE FEE"].section, by_desc["LATE FEE"].amount_cents) == ("fee", -3900)
    assert by_desc["PURCHASE INTEREST CHARGE"].section == "interest"
    assert {c.label for c in st.checks} >= {"Fees Charged", "Interest Charged", "New Balance"}
    assert st.summary == {"previous_balance_cents": 10000, "new_balance_cents": 10273,
                          "minimum_due_cents": 4000, "due_date": "2027-02-08",
                          "credit_limit_cents": 500000}


def test_chase_card_zero_activity_month():
    pages = ["""
  www.chase.com/cardhelp
  ACCOUNT SUMMARY
  Account Number: XXXX XXXX XXXX 5678
  Previous Balance              $0.00
  Payment, Credits              $0.00
  Purchases                     $0.00
  Fees Charged                  $0.00
  Interest Charged              $0.00
  New Balance                   $0.00
  Opening/Closing Date   10/02/26 - 11/01/26
"""]
    (st,) = parse_pages(pages)
    assert st.last4 == "5678" and st.transactions == [] and st.reconciled is True


CHASE_CONSOLIDATED = ["""
                                                 December 10, 2026throughJanuary 08, 2027
        JPMorgan Chase Bank, N.A.
      CONSOLIDATED    BALANCE   SUMMARY
      Chase Total Checking       000001111111234            $1,000.00 $2,838.00
      Chase Savings              000002222225678               50.00     50.00
      CHASE  TOTAL  CHECKING
      ALEX J SMITH                                   Account Number:
      OR JORDAN K SMITH
      CHECKING  SUMMARY
                                          INSTANCES          AMOUNT
     Beginning Balance                                    $1,000.00
     Deposits and Additions                    1           2,000.00
     Checks Paid                               1            -100.00
     ATM & Debit Card Withdrawals              1             -50.00
     Fees                                      1             -12.00
     Ending Balance                            4          $2,838.00
     TRANSACTION   DETAIL
     DATE  DESCRIPTION                                      AMOUNT   BALANCE
            Beginning Balance                                       $1,000.00
     12/11 Card Purchase 12/10 Corner Market Springfield IL Card 1234 -50.00 950.00
     12/31 Check # 101                                       -100.00    850.00
                                                                Page of
                                                                  1 2
""", """
                                                 December 10, 2026throughJanuary 08, 2027
                                                   Primary Account:
                                                            000001111111234
                           (continued)
     TRANSACTION   DETAIL
     DATE  DESCRIPTION                                      AMOUNT   BALANCE
     01/02 Example Corp Payroll PPD ID: 0000000000         2,000.00 2,850.00
     01/08 Monthly Service Fee                               -12.00 2,838.00
            Ending Balance                                         $2,838.00
     CHASE   SAVINGS
     ALEX J SMITH                                   Account Number: 000002222225678
     OR JORDAN K SMITH
      SAVINGS  SUMMARY
                                               AMOUNT
     Beginning Balance                          $50.00
     Ending Balance                             $50.00
"""]


def test_chase_consolidated_checks_paid_fees_and_year_boundary():
    checking, savings = parse_pages(CHASE_CONSOLIDATED)
    assert (checking.parser, checking.account_type, checking.product, checking.last4) == (
        "chase_bank", "checking", "Total Checking", "1234")
    assert checking.holder == "ALEX J SMITH OR JORDAN K SMITH"     # 2-row joint name
    assert checking.reconciled is True, _check_failures(checking)
    assert [t.date for t in checking.transactions] == [
        date(2026, 12, 11), date(2026, 12, 31), date(2027, 1, 2), date(2027, 1, 8)]
    assert [t.section for t in checking.transactions] == [
        "withdrawal", "withdrawal", "deposit", "withdrawal"]
    labels = [c.label for c in checking.checks]
    assert "Deposits and Additions" in labels
    assert any("Checks Paid" in l and "Fees" in l for l in labels)
    assert (savings.account_type, savings.last4, savings.transactions) == ("savings", "5678", [])
    assert savings.reconciled is True
    assert savings.summary == {"opening_balance_cents": 5000, "closing_balance_cents": 5000}


AMEX = ["""
              Blue Cash Everyday® from American Express                p. 1/2
              ALEX J SMITH                                  Customer Care: 1-800-000-0000
              Closing Date12/02/26                          Website: americanexpress.com
              Account Ending5-61234
         New Balance                       $150.00
         Minimum Payment Due                $40.00
         Payment Due Date                 12/27/26
                                                       Previous Balance  $100.00
        Late Payment Warning: If we do not receive  Payments/Credits -$120.00
                                                       New Charges +$145.00
        and your APRs may be increased.                Fees +$25.00
                                                       Interest Charged   +$0.00
                                                       Credit Limit     $2,000.00
""", """
        ALEX J SMITH                 Account Ending5-61234              p. 2/2
        Payments and Credits
        Summary
       Payments                                                         -$100.00
       Credits                                                           -$20.00
       Total Payments and Credits                                       -$120.00
        Detail *Indicates posting date
       Payments                                                         Amount
       11/20/26* AUTOPAY PAYMENT RECEIVED - THANK YOU                   -$100.00
              EXAMPLE BANK, NA
       Credits                                                          Amount
       11/22/26 SAMPLE OUTFITTERS                                        -$20.00
              ANYTOWN  ST

        New Charges
        Summary
       Total New Charges                                                $145.00
        Detail
           ALEX J SMITH
           Card Ending5-61234
                                                                        Amount
       11/05/26 CORNER CAFE              ANYTOWN  ST                     $45.00
              555-0100

           JORDAN K SMITH
           Card Ending5-65678
       11/30/26 BOOK BARN                OTHERTOWN  ST                  $100.00
        Fees
                                                                        Amount
       12/02/26 LATE PAYMENT FEE                                          $25.00
       Total Fees for this Period                                         $25.00
        Interest Charged
                                                                        Amount
       Total Interest Charged for this Period                             $0.00
        Interest Charge Calculation                             Days in Billing Period:30
"""]


def test_amex_sections_cardmembers_and_fees():
    (st,) = parse_pages(AMEX)
    assert (st.parser, st.product, st.last4, st.holder) == (
        "amex", "Blue Cash Everyday", "1234", "ALEX J SMITH")
    assert (st.period_start, st.period_end) == (date(2026, 11, 3), date(2026, 12, 2))
    assert st.reconciled is True, _check_failures(st)
    rows = [(t.description.split()[0], t.amount_cents, t.section, t.cardholder)
            for t in st.transactions]
    assert rows == [("AUTOPAY", 10000, "payment", None), ("SAMPLE", 2000, "credit", None),
                    ("CORNER", -4500, "purchase", "ALEX J SMITH"),
                    ("BOOK", -10000, "purchase", "JORDAN K SMITH"), ("LATE", -2500, "fee", None)]
    pay = st.transactions[0]
    assert pay.post_date == date(2026, 11, 20) and pay.extra == "EXAMPLE BANK, NA"
    assert st.summary["credit_limit_cents"] == 200000 and st.summary["due_date"] == "2026-12-27"


CITI = ["""
                 www.citicards.com          Costco Anywhere Visa® Card by Citi
                 Member Since 2020. Account number ending in: 1234
     Billing Period: 12/15/26-01/14/27
     Minimum payment due:          $41.00
     Payment due date:            02/10/27
                                            Previous balance               $500.00
                                            Payments                       -$500.00
                                            Credits                         -$10.00
                                            Purchases                      +$130.00
                                            Cash advances                   +$0.00
                                            Fees                            +$0.00
                                            Interest                        +$0.00
                                            New balance                    $120.00
                                            Credit Limit                   $9,000
""", """
                                      Page 2 of 3
                   www.citicards.com                 Costco Anywhere Visa® Card by Citi
                                         Member Since 2020. Account number ending in: 1234
     ALEX J SMITH

     CARDHOLDER SUMMARY
     ALEX J SMITH                                                     Card ending in 1234
     New Charges                                                          $100.00

     JORDAN K SMITH                                                   Card ending in 5678
     New Charges                                                           $30.00

     ACCOUNT SUMMARY
     Sale     Post
     Date     Date    Description                                          Amount
     Payments, Credits and Adjustments
              12/18   AUTOPAY 000000000000000RAUTOPAY AUTO-PMT             -$500.00
     12/20    12/21   SAMPLE WAREHOUSE #100 ANYTOWN ST                      -$10.00

     ALEX J SMITH
     Standard Purchases
     12/16    12/17   CORNER FUEL #12 ANYTOWN ST                            $40.00
     01/02    01/03   SAMPLE WAREHOUSE #100 ANYTOWN ST                      $60.00
     JORDAN K SMITH
     Standard Purchases
     12/31    01/02   BOOK BARN OTHERTOWN ST                                $20.00
""", """
                                      Page 3 of 3
                   www.citicards.com                 Costco Anywhere Visa® Card by Citi
                                         Member Since 2020. Account number ending in: 1234
     ALEX J SMITH
     01/10    01/11   CORNER CAFE OTHERTOWN ST                              $10.00
     Fees Charged
     TOTAL FEES FOR THIS PERIOD                                             $0.00
     Interest Charged
     TOTAL INTEREST FOR THIS PERIOD                                         $0.00
     2027 totals year-to-date
"""]


def test_citi_cardholder_groups_across_page_break():
    (st,) = parse_pages(CITI)
    assert (st.parser, st.product, st.last4, st.holder) == (
        "citi", "Costco Anywhere Visa", "1234", "ALEX J SMITH")
    assert st.reconciled is True, _check_failures(st)
    assert any(c.label == "New Charges (JORDAN K SMITH)" for c in st.checks)
    autopay, refund, *purchases = st.transactions
    assert (autopay.section, autopay.amount_cents, autopay.date, autopay.cardholder) == (
        "payment", 50000, date(2026, 12, 18), None)
    assert (refund.section, refund.amount_cents) == ("credit", 1000)
    assert [(t.cardholder, t.date, t.post_date) for t in purchases] == [
        ("ALEX J SMITH", date(2026, 12, 16), date(2026, 12, 17)),
        ("ALEX J SMITH", date(2027, 1, 2), date(2027, 1, 3)),
        ("JORDAN K SMITH", date(2026, 12, 31), date(2027, 1, 2)),
        # page header name on page 3 must not switch the group
        ("JORDAN K SMITH", date(2027, 1, 10), date(2027, 1, 11)),
    ]


def test_generic_fallback_is_unverified():
    pages = ["""
    Example Credit Union                 Statement Period 09/01/2026 - 09/30/2026
    Account Number ****5678              Credit Limit $5,000.00
    09/03/2026   GROCERY STORE 123                     45.67
    09/10/2026   PAYMENT THANK YOU                    -100.00
"""]
    (st,) = parse_pages(pages)
    assert st.parser == "generic" and st.checks == [] and st.reconciled is None
    assert st.last4 == "5678" and st.account_type == "credit"
    assert (st.period_start, st.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert [(t.amount_cents, t.section) for t in st.transactions] == [
        (-4567, "purchase"), (10000, "payment")]


def test_unrecognized_raises():
    with pytest.raises(UnsupportedStatement):
        parse_pages(["Hello world\nnothing to see here\n"])


if __name__ == "__main__":
    if "--write-expected" in sys.argv[1:]:
        written = write_expected()
        print(f"wrote {EXPECTED_FILE} ({len(written)} sample PDFs)")
    else:
        print("usage: python -m tests.test_parsers --write-expected")
