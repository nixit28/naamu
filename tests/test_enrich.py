"""Unit tests for namu.enrich. Descriptions mimic the shape of real statement lines
(whitespace collapsed, as parsers emit them) but every payee, place, id and amount is
fictional."""
from datetime import date

import pytest

from namu.enrich import (
    CATEGORIES, categorize, detect_recurring, kind_for_category, match_transfers,
    merchant_name, title_case,
)

# (description, amount_cents, section, merchant, kind, category)
STATEMENT_LINES = [
    # --- Chase-style credit card ------------------------------------------------------
    ("& AUTOMATIC PAYMENT - THANK YOU", 120000, "payment", "Autopay Payment", "transfer", "Card Payment"),
    ("& RAZ*Sarvam AI Bengaluru", 1, "credit", "Sarvam AI", "refund", "Subscriptions"),
    ("& RAZ*Sarvam AI Bengaluru", -1, "purchase", "Sarvam AI", "expense", "Subscriptions"),
    ("& GOOGLE *Workspace_abcd cc@google.com CA", -1200, "purchase", "Google Workspace", "expense", "Subscriptions"),
    ("& USPS PO 1234567890 ANYTOWN NJ", -2500, "purchase", "USPS", "expense", "Services"),
    ("& WWP*TLC THE LAWN COMPANY 800-555-0101 MA", -4000, "purchase", "TLC The Lawn Company", "expense", "Home & Garden"),
    ("& BJS WHOLESALE #0123 ANYTOWN NJ", -10000, "purchase", "BJ's Wholesale", "expense", "Groceries"),
    ("& WM.COM 800-555-0102 TX", -6000, "purchase", "Waste Management", "expense", "Utilities"),
    ("& CL *Chase Travel TRIPCHRG.COM VA", -50000, "purchase", "Chase Travel", "expense", "Travel"),
    ("& VISIBLE 800-555-0103 CO", -2500, "purchase", "Visible", "expense", "Phone & Internet"),
    ("& E-Z*PASSNY REBILL 800-555-0104 NY", -5000, "purchase", "E-ZPass", "expense", "Tolls & Transit"),
    ("& APPLE.COM/BILL 800-555-0105 CA", -299, "purchase", "Apple", "expense", "Subscriptions"),
    ("& BAPS SHAYONA-CHICAGO ANYTOWN IL", -800, "purchase", "BAPS Shayona", "expense", "Dining"),
    ("& ANTHROPIC* CLAUDE SUB ANTHROPIC.COM CA", -2000, "purchase", "Anthropic", "expense", "Subscriptions"),
    ("& abevault.com +18005550106 UT", -3000, "purchase", "Abevault", "expense", "Shopping"),
    ("& WAWA #1234 ANYTOWN NJ", -500, "purchase", "Wawa", "expense", "Dining"),
    ("& PAYPAL *NEWEGGCOM 800-555-0107 CA", -40000, "purchase", "Newegg", "expense", "Shopping"),
    # --- Amazon card --------------------------------------------------------------------
    ("AMAZON MKTPLACE PMTS Amzn.com/bill WA", 1500, "credit", "Amazon", "refund", "Shopping"),
    ("Amazon.com Amzn.com/bill WA", 2000, "credit", "Amazon", "refund", "Shopping"),
    ("AUTOMATIC PAYMENT - THANK YOU", 40000, "payment", "Autopay Payment", "transfer", "Card Payment"),
    ("AMAZON MKTPL*AB12CD34E Amzn.com/bill WA", -1500, "purchase", "Amazon", "expense", "Shopping"),
    ("Amazon.com*ZY98XW76V Amzn.com/bill WA", -2000, "purchase", "Amazon", "expense", "Shopping"),
    # --- Amex-style card ----------------------------------------------------------------
    ("AUTOPAY PAYMENT RECEIVED - THANK YOU", 30000, "payment", "Autopay Payment", "transfer", "Card Payment"),
    ("AplPay SP PUMPIN PAL", 2500, "credit", "Pumpin Pal", "refund", "Shopping"),
    ("SPRINGFIELD GENERAL HOSPITAL Anytown NJ", -15000, "purchase", "Springfield General Hospital", "expense", "Health"),
    ("TARGET.COM 800-555-0108 MN", -3000, "purchase", "Target", "expense", "Shopping"),
    ("TARGET PLUS 0 MINNEAPOLIS MN", -1500, "purchase", "Target", "expense", "Shopping"),
    ("AplPay carters, Inc. 000000123 Atlanta GA", -5000, "purchase", "Carter's", "expense", "Shopping"),
    # --- Citi Costco-style card ---------------------------------------------------------
    ("AUTOPAY 123456789012345RAUTOPAY AUTO-PMT", 50000, "payment", "Autopay Payment", "transfer", "Card Payment"),
    ("COSTCO WHSE #0456 ANYTOWN NJ", 1000, "credit", "Costco", "refund", "Groceries"),
    ("COSTCO WHSE #0456 ANYTOWN NJ", -4000, "purchase", "Costco", "expense", "Groceries"),
    ("BJ'S FUEL #0789 ANYTOWN NH", -4000, "purchase", "BJ's Fuel", "expense", "Gas & Auto"),
    ("BJ'S FUEL #0790 OTHERTOWN MA", -2000, "purchase", "BJ's Fuel", "expense", "Gas & Auto"),
    ("WWW COSTCO COM 800-555-0109 WA", -6000, "purchase", "Costco.com", "expense", "Shopping"),
    ("COSTCO GAS #0456 ANYTOWN NJ", -3500, "purchase", "Costco Gas", "expense", "Gas & Auto"),
    ("PATIDAR SUPERMARKET SPRINGFIELD NJ", -1000, "purchase", "Patidar Supermarket", "expense", "Groceries"),
    ("ROSS STORES #0321 ANYTOWN NJ", -2500, "purchase", "Ross", "expense", "Shopping"),
    ("OLD NAVY US 1234 ANYTOWN TOWNNJ", -1500, "purchase", "Old Navy", "expense", "Shopping"),
    ("SHOPRITE ANYTOWN S1 ANYTOWN NJ", -1000, "purchase", "ShopRite", "expense", "Groceries"),
    ("JCPENNEY 0123 ANYTOWN NJ", -1000, "purchase", "JCPenney", "expense", "Shopping"),
    # --- Chase-style checking -----------------------------------------------------------
    ("Zelle Payment To Sam 12345678901", -5000, "withdrawal", "Zelle · Sam", "expense", "Family & Friends"),
    ("Acme Corp Payroll PPD ID: 1234567890", 300000, "deposit", "Acme Payroll", "income", "Salary"),
    ("Chase Credit Crd Autopay PPD ID: 1234567890", -120000, "withdrawal", "Chase Card Autopay", "transfer", "Card Payment"),
    # a matured Treasury is savings coming back, not income
    ("Xyz Treas 310 Misc Pay PPD ID: 1234567890", 500000, "deposit", "US Treasury", "saving", "Savings & Investments"),
    ("Barclays Bank De Collection 000123456789012 Web ID: 1234567890", -100000, "withdrawal", "Barclays", "saving", "Savings & Investments"),
    ("Barclays Bank De Collection 000123456789013 Web ID: 1234567890", -50000, "withdrawal", "Barclays", "saving", "Savings & Investments"),
    ("American Express ACH Pmt A1234 Web ID: 1234567890", -30000, "withdrawal", "American Express Payment", "transfer", "Card Payment"),
    ("Aqua Aqua Servi PPD ID: 1234567890", -6000, "withdrawal", "Aqua Water", "expense", "Utilities"),
    ("Newrez-Shellpoin ACH Pmt PPD ID: 1234567890", -200000, "withdrawal", "Newrez (Mortgage)", "expense", "Housing"),
    ("Zelle Payment To Shree Ram Mandir 12345678901", -10000, "withdrawal", "Zelle · Shree Ram Mandir", "expense", "Donations"),
    ("Paypal Purchase Visibleserv Web ID: Paypalsi00", -2500, "withdrawal", "Visible", "expense", "Phone & Internet"),
    ("Baps Endowment-R Payment 123456789012 Web ID: 1234567890", -10000, "withdrawal", "BAPS Endowment", "expense", "Donations"),
]


@pytest.mark.parametrize("desc,amount,section,merchant,kind,category", STATEMENT_LINES,
                         ids=[f"{r[0][:40]}|{r[2]}" for r in STATEMENT_LINES])
def test_statement_lines(desc, amount, section, merchant, kind, category):
    assert merchant_name(desc) == merchant
    assert categorize({"description": desc, "amount_cents": amount, "section": section}) == (kind, category)


# More bank / card lines for rules not covered above.
@pytest.mark.parametrize("desc,amount,section,expected", [
    ("Zelle Payment From John Doe Bacnqg3x1k2", 5000, "deposit", ("income", "Other Income")),
    ("Online Transfer To Sav ...4321 Transaction#: 1234567", -50000, "withdrawal", ("transfer", "Transfer")),
    ("Online Transfer From Chk ...1111 Transaction#: 7654321", 50000, "deposit", ("transfer", "Transfer")),
    ("Payment To Chase Card Ending IN 2222 10/01", -10000, "withdrawal", ("transfer", "Card Payment")),
    ("CITI AUTOPAY PAYMENT 123456789012345 WEB ID: CITICARDAP", -50000, "withdrawal", ("transfer", "Card Payment")),
    ("DISCOVER E-PAYMENT 4567 WEB ID: 1234567890", -12000, "withdrawal", ("transfer", "Card Payment")),
    ("CAPITAL ONE CRCARDPMT 1234 PPD ID: 1234567890", -5000, "withdrawal", ("transfer", "Card Payment")),
    ("IRS TREAS 310 TAX REF PPD ID: 1234567890", 120000, "deposit", ("income", "Tax & Government")),
    ("SSA TREAS 310 XXSOC SEC", 150000, "deposit", ("income", "Tax & Government")),
    # savings & investment destinations, both directions
    ("TREASURYDIRECT TREAS DRCT PPD ID: 1234567890", -500000, "withdrawal", ("saving", "Savings & Investments")),
    ("TREAS 310 MISC PAY PPD ID: 1234567890", 500000, "deposit", ("saving", "Savings & Investments")),
    ("FID BKG SVC LLC MONEYLINE PPD ID: 1234567890", -200000, "withdrawal", ("saving", "Savings & Investments")),
    ("FIDELITY INVESTMENTS REDEMPTION", 300000, "deposit", ("saving", "Savings & Investments")),
    ("FMR LLC MONEYLINE PPD ID: 1234567890", -1000, "withdrawal", ("saving", "Savings & Investments")),
    ("SCHWAB BROKERAGE MONEYLINK", -100000, "withdrawal", ("saving", "Savings & Investments")),
    ("CHARLES SCHWAB TRANSFER", 100000, "deposit", ("saving", "Savings & Investments")),
    ("E*TRADE SECURITIES ACH", -100000, "withdrawal", ("saving", "Savings & Investments")),
    ("ETRADE TRANSFER", -100000, "withdrawal", ("saving", "Savings & Investments")),
    ("E TRADE FINANCIAL", 100000, "deposit", ("saving", "Savings & Investments")),
    ("Coinbase Inc. 8005550110 Web ID: 1234", -50000, "withdrawal", ("saving", "Savings & Investments")),
    ("BARCLAYS BANK DE TRANSFER", 50000, "deposit", ("saving", "Savings & Investments")),
    ("COINBASE.COM 800-555-0110 CA", -5000, "purchase", ("saving", "Savings & Investments")),
    ("Interest Payment", 12, "deposit", ("income", "Interest")),
    ("Monthly Service Fee", -1200, "withdrawal", ("expense", "Fees & Interest")),
    ("Zelle Payment To Shree Swaminarayan Temple 123456789", -10100, "withdrawal", ("expense", "Donations")),
    ("Remote Online Deposit 1", 25000, "deposit", ("income", "Other Income")),
    ("Some Unknown Debit 12345", -2500, "withdrawal", ("expense", "Other")),
    ("Online Payment 1234567 To Verizon Wireless 09/14", -8000, "withdrawal", ("expense", "Phone & Internet")),
    ("Card Purchase 09/14 Wawa 1234 Anytown NJ Card 0000", -500, "withdrawal", ("expense", "Dining")),
    ("Amazon.com Refund", 2500, "deposit", ("refund", "Shopping")),
    ("PURCHASE INTEREST CHARGE", -1234, "interest", ("expense", "Fees & Interest")),
    ("LATE FEE", -4000, "fee", ("expense", "Fees & Interest")),
    ("CASH ADVANCE ATM", -20000, "cash_advance", ("expense", "Other")),
    ("SHELL OIL 12345678 ANYTOWN NJ", -4000, "purchase", ("expense", "Gas & Auto")),
    ("EXXONMOBIL 4567 ANYTOWN NJ", -4000, "purchase", ("expense", "Gas & Auto")),
    ("WHOLEFDS ANY 10234 ANYTOWN NJ", -6000, "purchase", ("expense", "Groceries")),
    ("PATEL BROTHERS ANYTOWN NJ", -6000, "purchase", ("expense", "Groceries")),
    ("PSE&G 800-555-0111 NJ", -15000, "purchase", ("expense", "Utilities")),
    ("NETFLIX.COM 800-555-0112 CA", -1500, "purchase", ("expense", "Subscriptions")),
    ("Spotify USA 800-555-0113 NY", -1200, "purchase", ("expense", "Subscriptions")),
    ("THE HOME DEPOT #0912 ANYTOWN NJ", -4500, "purchase", ("expense", "Home & Garden")),
    ("LOWE'S #1234 ANYTOWN NJ", -4500, "purchase", ("expense", "Home & Garden")),
    ("CVS/PHARMACY #01234 ANYTOWN NJ", -1200, "purchase", ("expense", "Health")),
    ("DD *DOORDASH WINGSTOP 800-555-0114 CA", -2500, "purchase", ("expense", "Dining")),
    ("STARBUCKS STORE 12345 ANYTOWN NJ", -650, "purchase", ("expense", "Dining")),
    ("UNITED AIRLINES 0161234567890 HOUSTON TX", -45000, "purchase", ("expense", "Travel")),
    ("MARRIOTT HOTEL CHICAGO IL", -30000, "purchase", ("expense", "Travel")),
    ("TST* MASALA KITCHEN ANYTOWN NJ", -4200, "purchase", ("expense", "Dining")),
    ("BAPS CHARITIES ANYTOWN NJ", -10000, "purchase", ("expense", "Donations")),
    ("CASHBACK BONUS REDEMPTION", 2500, "credit", ("income", "Other Income")),
    # a card-side payment emitted as "credit" by a parser is still a card payment
    ("PAYMENT THANK YOU - WEB", 10000, "credit", ("transfer", "Card Payment")),
])
def test_rules(desc, amount, section, expected):
    assert categorize({"description": desc, "amount_cents": amount, "section": section}) == expected


def test_shayona_is_dining_not_donation():
    assert categorize({"description": "BAPS SHAYONA ANYTOWN NJ", "amount_cents": -900,
                       "section": "purchase"}) == ("expense", "Dining")


def test_donation_beats_zelle():
    kind, cat = categorize({"description": "Zelle Payment To Shree Ram Mandir 1",
                            "amount_cents": -100, "section": "withdrawal"})
    assert cat == "Donations"


@pytest.mark.parametrize("desc, merchant", [
    ("CHECK # 1043", "Check #1043"),
    ("Check #1043", "Check #1043"),
    ("CHECK 1044", "Check #1044"),
    ("CHK NO. 1045", "Check #1045"),
])
def test_each_check_keeps_its_number(desc, merchant):
    # Distinct merchants per check so "apply to all" never recategorizes every check.
    assert merchant_name(desc) == merchant


def test_payment_section_known_merchant_is_refund():
    # Chase lists Amazon refunds under "PAYMENTS AND OTHER CREDITS"; if a parser tags them
    # as payment, they must still count as Shopping refunds, not card payments.
    assert categorize({"description": "AMAZON MKTPLACE PMTS Amzn.com/bill WA",
                       "amount_cents": 1500, "section": "payment"}) == ("refund", "Shopping")


def test_categorize_accepts_objects():
    from namu.models import ParsedTxn
    t = ParsedTxn(date=date(2026, 9, 2), description="& BJS WHOLESALE #0123 ANYTOWN NJ",
                  amount_cents=-10000, section="purchase")
    assert categorize(t) == ("expense", "Groceries")


@pytest.mark.parametrize("desc,expected", [
    ("Card Purchase 09/14 Joes Pizza Springfield NJ Card 1234", "Joes Pizza"),
    ("SQ *JOES COFFEE Springfield NJ", "Joes Coffee"),
    ("TST* TACO SPOT 1234 ANYTOWN NJ", "Taco Spot"),
    ("RANDOM THING LLC 555-555-5555 NJ", "Random Thing"),
    ("Online Transfer To Sav ...4321 Transaction#: 1234567", "Transfer to Savings"),
    ("Zelle Payment From John Doe Bacnqg3x1k2", "Zelle · John Doe"),
    ("SOMETHING #123 CITY ST", "Something"),
    ("", "Unknown"),
])
def test_merchant_fallbacks(desc, expected):
    assert merchant_name(desc) == expected


def test_title_case_brands():
    assert title_case("BJS WHOLESALE") == "BJ's Wholesale"
    assert title_case("THE LAWN OF THE KING") == "The Lawn of the King"


@pytest.mark.parametrize("desc,merchant", [
    ("Xyz Treas 310 Misc Pay PPD ID: 1234567890", "US Treasury"),
    ("TREASURYDIRECT TREAS DRCT PPD ID: 1234567890", "US Treasury"),
    ("IRS TREAS 310 TAX REF PPD ID: 1234567890", "IRS"),
    ("FID BKG SVC LLC MONEYLINE", "Fidelity"),
    ("CHARLES SCHWAB TRANSFER", "Schwab"),
    ("E*TRADE SECURITIES ACH", "E*Trade"),
    ("Coinbase Inc. 8005550110", "Coinbase"),
    ("Barclays Bank De Collection 000123456789012 Web ID: 1234567890", "Barclays"),
])
def test_saving_destination_names(desc, merchant):
    assert merchant_name(desc) == merchant


def test_kind_for_category():
    assert kind_for_category("Dining", -500) == "expense"
    assert kind_for_category("Dining", 500) == "refund"
    assert kind_for_category("Salary", 500) == "income"
    assert kind_for_category("Card Payment", -500) == "transfer"
    assert kind_for_category("Transfer", 500) == "transfer"
    assert kind_for_category("Savings & Investments", -500) == "saving"
    assert kind_for_category("Savings & Investments", 500) == "saving"


def test_categories_fixed_order():
    names = [n for n, _ in CATEGORIES]
    assert names[0] == "Housing" and names[16] == "Other"
    assert names[17:21] == ["Salary", "Tax & Government", "Interest", "Other Income"]
    assert CATEGORIES[21] == ("Savings & Investments", "saving")
    assert names[22:] == ["Card Payment", "Transfer"]


# --------------------------------------------------------------------------------------
# Transfers
# --------------------------------------------------------------------------------------

def T(id, account, d, cents, kind="transfer", merchant="x"):
    return {"id": id, "account_id": account, "date": d, "amount_cents": cents, "kind": kind,
            "merchant": merchant}


def test_match_basic_both_ways():
    m = match_transfers([T(1, 10, "2026-09-17", -30000), T(2, 20, "2026-09-17", 30000)])
    assert m == {1: 2, 2: 1}


def test_match_window_six_days():
    inside = match_transfers([T(1, 10, "2026-09-28", -120000), T(2, 20, "2026-09-22", 120000)])
    outside = match_transfers([T(1, 10, "2026-09-29", -120000), T(2, 20, "2026-09-22", 120000)])
    assert inside == {1: 2, 2: 1}
    assert outside == {}


def test_match_same_account_excluded():
    assert match_transfers([T(1, 10, "2026-09-10", -1000), T(2, 10, "2026-09-10", 1000)]) == {}


def test_match_requires_opposite_sign_and_equal_amount():
    assert match_transfers([T(1, 10, "2026-09-10", -1000), T(2, 20, "2026-09-10", -1000)]) == {}
    assert match_transfers([T(1, 10, "2026-09-10", -1000), T(2, 20, "2026-09-10", 1001)]) == {}


def test_match_ignores_non_transfers():
    assert match_transfers([T(1, 10, "2026-09-10", -1000, kind="expense"),
                            T(2, 20, "2026-09-10", 1000)]) == {}


def test_match_one_to_one_closest_wins():
    txns = [T(1, 10, "2026-09-10", -50000), T(2, 10, "2026-09-20", -50000),
            T(3, 20, "2026-09-12", 50000)]
    assert match_transfers(txns) == {1: 3, 3: 1}            # 2 days beats 8 (and 8 > window)
    txns.append(T(4, 30, "2026-09-21", 50000))
    assert match_transfers(txns) == {1: 3, 3: 1, 2: 4, 4: 2}


def test_match_global_closest_first():
    # B is 1 day from C but 4 days from A: B pairs with C, A stays unmatched.
    txns = [T(1, 10, "2026-09-10", -100), T(2, 20, "2026-09-14", 100), T(3, 30, "2026-09-13", -100)]
    assert match_transfers(txns) == {2: 3, 3: 2}


def test_match_never_pairs_savings():
    txns = [T(1, 10, "2026-09-15", -100000, kind="saving"), T(2, 20, "2026-09-15", 100000),
            T(3, 30, "2026-09-15", 100000, kind="saving")]
    assert match_transfers(txns) == {}


def test_match_accepts_date_objects():
    m = match_transfers([T(1, 10, date(2026, 9, 17), -5), T(2, 20, date(2026, 9, 18), 5)])
    assert m == {1: 2, 2: 1}


# --------------------------------------------------------------------------------------
# Recurring
# --------------------------------------------------------------------------------------

def E(id, merchant, d, cents, kind="expense"):
    return {"id": id, "merchant": merchant, "date": d, "amount_cents": cents, "kind": kind}


def test_recurring_two_months_same_amount():
    assert detect_recurring([E(1, "Netflix", "2026-09-05", -1549),
                             E(2, "Netflix", "2026-10-05", -1549)]) == {1, 2}


def test_recurring_needs_two_distinct_months():
    assert detect_recurring([E(1, "E-ZPass", "2026-09-07", -5500),
                             E(2, "E-ZPass", "2026-09-25", -5500)]) == set()


def test_recurring_tolerance_twenty_percent():
    assert detect_recurring([E(1, "Aqua Water", "2026-09-01", -10000),
                             E(2, "Aqua Water", "2026-10-01", -8000)]) == {1, 2}
    assert detect_recurring([E(1, "Aqua Water", "2026-09-01", -10000),
                             E(2, "Aqua Water", "2026-10-01", -7900)]) == set()


def test_recurring_only_similar_members_flagged():
    got = detect_recurring([E(1, "Visible", "2026-09-04", -2000), E(2, "Visible", "2026-10-04", -2000),
                            E(3, "Visible", "2026-11-04", -9000)])
    assert got == {1, 2}


def test_recurring_expenses_only():
    assert detect_recurring([E(1, "Acme Payroll", "2026-09-11", 300000, kind="income"),
                             E(2, "Acme Payroll", "2026-10-11", 300000, kind="income")]) == set()
    assert detect_recurring([E(1, "Amazon", "2026-09-11", 1000, kind="refund"),
                             E(2, "Amazon", "2026-10-11", 1000, kind="refund")]) == set()


def test_recurring_excludes_savings():
    assert detect_recurring([E(1, "Barclays", "2026-09-15", -50000, kind="saving"),
                             E(2, "Barclays", "2026-10-15", -50000, kind="saving")]) == set()


def test_recurring_skips_frequent_shopping():
    txns = [E(i, "Costco", f"2026-09-{d:02d}", -5000) for i, d in enumerate((3, 10, 17), 1)]
    txns.append(E(9, "Costco", "2026-10-03", -5000))
    assert detect_recurring(txns) == set()


def test_recurring_merchants_not_mixed():
    assert detect_recurring([E(1, "Netflix", "2026-09-05", -1549),
                             E(2, "Spotify", "2026-10-05", -1549)]) == set()


def test_categorize_accepts_sqlite_rows():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT 'Acme Corp Payroll PPD ID: 1234567890' AS description, "
                       "300000 AS amount_cents, 'deposit' AS section").fetchone()
    assert categorize(row) == ("income", "Salary")
