"""Deterministic enrichment: merchant names, kinds/categories, transfers, recurring.

Everything here is a pure function over plain data (dicts or objects with the
same attribute names) so it can be unit-tested without a database.

Rules are ordered regex tables; the first match wins. No network, no models.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Callable, Iterable

# --------------------------------------------------------------------------------------
# Categories (fixed order, from docs/CONTRACT.md)
# --------------------------------------------------------------------------------------

EXPENSE_CATEGORIES = (
    "Housing", "Utilities", "Phone & Internet", "Groceries", "Dining", "Shopping",
    "Gas & Auto", "Tolls & Transit", "Travel", "Health", "Subscriptions",
    "Home & Garden", "Donations", "Family & Friends", "Fees & Interest", "Services",
    "Other",
)
INCOME_CATEGORIES = ("Salary", "Tax & Government", "Interest", "Other Income")
SAVING_CATEGORIES = ("Savings & Investments",)
TRANSFER_CATEGORIES = ("Card Payment", "Transfer")

CATEGORIES: list[tuple[str, str]] = (
    [(c, "expense") for c in EXPENSE_CATEGORIES]
    + [(c, "income") for c in INCOME_CATEGORIES]
    + [(c, "saving") for c in SAVING_CATEGORIES]
    + [(c, "transfer") for c in TRANSFER_CATEGORIES]
)
CATEGORY_KIND: dict[str, str] = dict(CATEGORIES)

TRANSFER_WINDOW_DAYS = 6
RECURRING_TOLERANCE = 0.20
# A merchant charged more often than this in a single month is a shopping habit
# (groceries, Amazon), not a bill/subscription, so it is never flagged recurring.
RECURRING_MAX_PER_MONTH = 2


def is_category(name: str | None) -> bool:
    return name in CATEGORY_KIND


def kind_for_category(category: str, amount_cents: int) -> str:
    """Contract rule: expense categories -> expense (amount < 0) or refund (amount > 0);
    income / saving / transfer categories set the kind directly (regardless of sign)."""
    kind = CATEGORY_KIND.get(category, "expense")
    if kind == "expense":
        return "refund" if amount_cents > 0 else "expense"
    return kind


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dict, a mapping-like row (sqlite3.Row) or an object."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    if hasattr(obj, "keys") and not hasattr(obj, name):
        return obj[name] if name in obj.keys() else default
    return getattr(obj, name, default)


def _norm(description: str | None) -> str:
    """Upper-case, whitespace-collapsed, without Chase's leading '&' (lost/stolen marker)."""
    s = re.sub(r"\s+", " ", description or "").strip()
    s = re.sub(r"^&\s*", "", s)
    return s.upper()


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


_BRAND_CASE = {
    "BJS": "BJ's", "BJ'S": "BJ's", "USPS": "USPS", "UPS": "UPS",
    "CVS": "CVS", "IRS": "IRS", "ATM": "ATM", "ACH": "ACH", "BAPS": "BAPS", "TLC": "TLC",
    "PSE&G": "PSE&G", "JCPENNEY": "JCPenney", "SHOPRITE": "ShopRite", "AI": "AI",
    "LLC": "LLC", "USA": "USA", "US": "US", "NJ": "NJ", "NY": "NY", "PA": "PA",
    "IKEA": "IKEA", "TJ": "TJ", "AT&T": "AT&T", "HBO": "HBO", "AWS": "AWS", "BP": "BP",
    "KFC": "KFC", "IHOP": "IHOP", "DMV": "DMV", "MVC": "MVC", "EZ": "EZ", "II": "II",
    "III": "III", "MCDONALD'S": "McDonald's", "MCDONALDS": "McDonald's",
}
_SMALL_WORDS = {"of", "and", "the", "for", "at", "in", "on", "to", "a", "an", "&"}


def _title_word(w: str, first: bool) -> str:
    up = w.upper()
    if up in _BRAND_CASE:
        return _BRAND_CASE[up]
    low = w.lower()
    if not first and low in _SMALL_WORDS:
        return low
    if "." in w and re.search(r"\.(com|net|org|io|ai|co)\b", low):
        return low[:1].upper() + low[1:]
    # capitalise each hyphen part: "e-zpass" -> "E-Zpass", "chick-fil-a" -> "Chick-Fil-A"
    parts = low.split("-")
    return "-".join(p[:1].upper() + p[1:] for p in parts)


def title_case(text: str) -> str:
    words = text.split()
    return " ".join(_title_word(w, i == 0) for i, w in enumerate(words))


# --------------------------------------------------------------------------------------
# Merchant alias table: (regex on normalized description, display name, category | None)
# Order matters: first match wins. Name may be a callable(match, normalized) -> str.
# --------------------------------------------------------------------------------------

def _zelle_name(m: re.Match, s: str) -> str:
    who = s[m.end():].strip()
    # drop trailing reference tokens (anything containing a digit), e.g. "30765898059"
    toks = who.split()
    while toks and re.search(r"\d", toks[-1]):
        toks.pop()
    who = title_case(" ".join(toks)) if toks else "Unknown"
    return f"Zelle · {who}"


def _p2p_name(label: str) -> Callable[[re.Match, str], str]:
    return lambda m, s: label


def _payroll_name(m: re.Match, s: str) -> str:
    emp = s[: m.start()].strip()
    emp = re.sub(r"\b(INC|INC\.|LLC|CORP|CORPORATION|CO|LTD|COMPANY)\b\.?", "", emp).strip(" ,.-")
    return f"{title_case(emp)} Payroll" if emp else "Payroll"


def _transfer_name(m: re.Match, s: str) -> str:
    direction = m.group("dir").lower()
    acct = (m.group("acct") or "").upper()
    target = {"SAV": "Savings", "SAVINGS": "Savings", "CHK": "Checking",
              "CHECKING": "Checking"}.get(acct, "Account")
    return f"Transfer {direction} {target}"


_A = list[tuple[str, Any, str | None]]

ALIASES: _A = [
    # ---- card payments made from a bank account --------------------------------------
    (r"CHASE CREDIT CRD\s+AUTO ?PAY|CHASE CARD AUTOPAY", "Chase Card Autopay", "Card Payment"),
    (r"CHASE CREDIT CRD|PAYMENT TO CHASE CARD|CHASE CARD SERV|CHASE EPAY",
     "Chase Card Payment", "Card Payment"),
    (r"AMERICAN EXPRESS\s+(ACH PMT|EPAYMENT|AUTOPAY|AUTO PAY|PAYMENT|ACH)|AMEX\s+EPAYMENT|AMEX\s+ACH",
     "American Express Payment", "Card Payment"),
    (r"^CITI\s+(CARD|AUTOPAY|AUTO PAY|AUTO-?PMT|PAYMENT|ONLINE|BANK)|CITIBANK\b.*\b(PAYMENT|AUTOPAY|PMT)|\bCITI CARDS?\b",
     "Citi Payment", "Card Payment"),
    (r"DISCOVER\s+(E-?PAYMENT|DC ?PYMNT|DCPYMT|PAYMENT|CARD|BANK)", "Discover Payment", "Card Payment"),
    (r"CAPITAL ONE\s+(CRCARDPMT|CRCARD|ONLINE PMT|MOBILE PMT|PAYMENT|AUTOPAY|AUTOPMT)",
     "Capital One Payment", "Card Payment"),
    (r"APPLECARD GSBANK|APPLE CARD\b.*PAYMENT|GS BANK.*APPLE", "Apple Card Payment", "Card Payment"),
    (r"WELLS FARGO CARD|WF CREDIT CARD", "Wells Fargo Card Payment", "Card Payment"),
    (r"BK OF AMER\b.*(CRD|CARD)|BANK OF AMERICA\b.*(CRD|CARD)", "Bank of America Card Payment",
     "Card Payment"),
    (r"SYNCHRONY BANK|SYNCB\b.*(PAYMENT|PYMT|PMT)", "Synchrony Payment", "Card Payment"),
    (r"U\.?S\.? BANK\b.*(CARD|PAYMENT)", "US Bank Card Payment", "Card Payment"),
    # ---- savings & investment destinations (user-defined) --------------------------
    # Money to them is saved, money back from them is "taken out" (kind saving either way).
    # Tax refunds / benefits paid by the Treasury are income: named here, categorized by
    # the Tax & Government bank rule, which runs before these.
    (r"\bIRS\b|TAX REF|TAXRFD", "IRS", None),
    (r"\bSSA\b|SOC SEC", "Social Security", None),
    (r"FIDELITY\b|FID BKG SVC|\bFMR\b", "Fidelity", "Savings & Investments"),
    (r"SCHWAB", "Schwab", "Savings & Investments"),
    (r"E\s?\*\s?TRADE|\bETRADE\b|\bE TRADE\b", "E*Trade", "Savings & Investments"),
    (r"COINBASE", "Coinbase", "Savings & Investments"),
    (r"BARCLAYS?\b|BARCLAYCARD", "Barclays", "Savings & Investments"),
    (r"TREASURYDIRECT|TREASURY DIRECT|TREAS DRCT|\bTREAS\b|US TREASURY|U\.S\. TREASURY",
     "US Treasury", "Savings & Investments"),
    # ---- transfers between own accounts / investments -------------------------------
    (r"(?:ONLINE |MOBILE )?TRANSFER (?P<dir>TO|FROM) (?P<acct>SAVINGS|SAV|CHECKING|CHK)\b",
     _transfer_name, "Transfer"),
    (r"(?:ONLINE|MOBILE) TRANSFER (?P<dir>TO|FROM)(?P<acct>)", _transfer_name, "Transfer"),
    (r"ONLINE BANKING TRANSFER|INTERNAL TRANSFER|ACCT TO ACCT|BOOK TRANSFER",
     "Account Transfer", "Transfer"),
    (r"ROBINHOOD", "Robinhood", "Transfer"),
    (r"VANGUARD", "Vanguard", "Transfer"),
    # ---- people (P2P) --------------------------------------------------------------
    (r"^ZELLE (PAYMENT )?(TO|FROM)\s+", _zelle_name, None),
    (r"\bVENMO\b", "Venmo", None),
    (r"CASH APP|SQUARE CASH|\bSQC\*", "Cash App", None),
    # ---- income-ish names (category decided by bank rules, sign aware) ------------
    (r"\b(INC\.?\s+|LLC\s+|CORP\.?\s+)?PAYROLL\b", _payroll_name, None),
    (r"NJ (DIV(ISION)? OF )?TAXATION|STATE OF NJ\b.*TAX|NJ TAX", "NJ Taxation", None),
    # ---- housing & utilities --------------------------------------------------------
    (r"NEWREZ|SHELLPOIN", "Newrez (Mortgage)", "Housing"),
    (r"ROCKET MORTGAGE|QUICKEN LOANS", "Rocket Mortgage", "Housing"),
    (r"MR\.? COOPER|NATIONSTAR", "Mr. Cooper (Mortgage)", "Housing"),
    (r"^AQUA\b|AQUA (PA|NJ|SERVI|AMERICA)", "Aqua Water", "Utilities"),
    (r"\bWM\.COM\b|WASTE MANAGEMENT|WM EZPAY|\bWM CORPORATE", "Waste Management", "Utilities"),
    (r"PSE&G|\bPSEG\b|PUBLIC SERVICE ELEC", "PSE&G", "Utilities"),
    (r"JCP&L|JERSEY CENTRAL P", "JCP&L", "Utilities"),
    (r"NJ NATURAL GAS|\bNJNG\b", "NJ Natural Gas", "Utilities"),
    (r"NEW JERSEY AMERICAN WATER|NJ AMERICAN WATER|AMERICAN WATER", "American Water", "Utilities"),
    # ---- phone & internet -----------------------------------------------------------
    (r"VISIBLE", "Visible", "Phone & Internet"),
    (r"VERIZON", "Verizon", "Phone & Internet"),
    (r"T-MOBILE|TMOBILE", "T-Mobile", "Phone & Internet"),
    (r"\bAT&T\b|\bATT\*", "AT&T", "Phone & Internet"),
    (r"COMCAST|XFINITY", "Xfinity", "Phone & Internet"),
    (r"OPTIMUM", "Optimum", "Phone & Internet"),
    (r"SPECTRUM", "Spectrum", "Phone & Internet"),
    (r"MINT MOBILE", "Mint Mobile", "Phone & Internet"),
    (r"GOOGLE\s*\*?\s*FI\b", "Google Fi", "Phone & Internet"),
    # ---- gas & auto (before the warehouse clubs) ------------------------------------
    (r"BJ'?S\s+(FUEL|GAS)", "BJ's Fuel", "Gas & Auto"),
    (r"COSTCO\s+GAS", "Costco Gas", "Gas & Auto"),
    (r"\bSHELL\b", "Shell", "Gas & Auto"),
    (r"EXXON|\bMOBIL\b", "ExxonMobil", "Gas & Auto"),
    (r"SUNOCO", "Sunoco", "Gas & Auto"),
    (r"\bBP\b|\bBP#", "BP", "Gas & Auto"),
    (r"CHEVRON", "Chevron", "Gas & Auto"),
    (r"LUKOIL", "Lukoil", "Gas & Auto"),
    (r"SPEEDWAY", "Speedway", "Gas & Auto"),
    (r"QUICK ?CHEK", "QuickChek", "Gas & Auto"),
    (r"JIFFY LUBE", "Jiffy Lube", "Gas & Auto"),
    (r"AUTOZONE", "AutoZone", "Gas & Auto"),
    (r"PEP BOYS", "Pep Boys", "Gas & Auto"),
    (r"FIRESTONE", "Firestone", "Gas & Auto"),
    (r"NJ MVC|MOTOR VEHICLE|\bDMV\b", "Motor Vehicles", "Gas & Auto"),
    (r"GEICO", "GEICO", "Gas & Auto"),
    (r"PROGRESSIVE", "Progressive", "Gas & Auto"),
    (r"TESLA", "Tesla", "Gas & Auto"),
    # ---- groceries ------------------------------------------------------------------
    (r"BJ'?S\s+(WHOLESALE|WHSE|CLUB|MEMBERSHIP)|^BJ'?S\b", "BJ's Wholesale", "Groceries"),
    (r"WWW\s+COSTCO\s+COM|COSTCO\.COM", "Costco.com", "Shopping"),
    (r"COSTCO", "Costco", "Groceries"),
    (r"SHOP ?RITE", "ShopRite", "Groceries"),
    (r"PATIDAR", "Patidar Supermarket", "Groceries"),
    (r"WHOLE ?FOODS|WHOLE ?FDS|\bWFM\b", "Whole Foods", "Groceries"),
    (r"PATEL BRO", "Patel Brothers", "Groceries"),
    (r"TRADER JOE", "Trader Joe's", "Groceries"),
    (r"WEGMANS", "Wegmans", "Groceries"),
    (r"\bACME\b", "ACME Markets", "Groceries"),
    (r"STOP ?& ?SHOP", "Stop & Shop", "Groceries"),
    (r"\bALDI\b", "Aldi", "Groceries"),
    (r"\bLIDL\b", "Lidl", "Groceries"),
    (r"H ?MART\b", "H Mart", "Groceries"),
    (r"APNA BAZAAR|APNA BAZAR", "Apna Bazar", "Groceries"),
    (r"INSTACART", "Instacart", "Groceries"),
    (r"AMAZON FRESH|AMZN FRESH", "Amazon Fresh", "Groceries"),
    (r"SAM'?S CLUB", "Sam's Club", "Groceries"),
    # ---- tolls & transit ------------------------------------------------------------
    (r"E-?Z\s*\*?\s*PASS|EZPASS", "E-ZPass", "Tolls & Transit"),
    (r"NJ ?TRANSIT|\bNJT\b", "NJ Transit", "Tolls & Transit"),
    (r"\bSEPTA\b", "SEPTA", "Tolls & Transit"),
    (r"\bMTA\b|OMNY", "MTA", "Tolls & Transit"),
    (r"UBER\s*\*?\s*EATS", "Uber Eats", "Dining"),
    (r"\bUBER\b", "Uber", "Tolls & Transit"),
    (r"\bLYFT\b", "Lyft", "Tolls & Transit"),
    (r"PARKMOBILE|PARK MOBILE", "ParkMobile", "Tolls & Transit"),
    # ---- subscriptions --------------------------------------------------------------
    (r"GOOGLE\s*\*?\s*(WORKSPACE|GSUITE)", "Google Workspace", "Subscriptions"),
    (r"GOOGLE\s*\*?\s*(GOOGLE ONE|STORAGE)", "Google One", "Subscriptions"),
    (r"YOUTUBE ?PREMIUM|GOOGLE\s*\*?\s*YOUTUBE", "YouTube Premium", "Subscriptions"),
    (r"APPLE\.COM/BILL|APPLE\.COM BILL|ITUNES\.COM", "Apple", "Subscriptions"),
    (r"ANTHROPIC|CLAUDE\.AI", "Anthropic", "Subscriptions"),
    (r"OPENAI|CHATGPT", "OpenAI", "Subscriptions"),
    (r"SARVAM", "Sarvam AI", "Subscriptions"),
    (r"NETFLIX", "Netflix", "Subscriptions"),
    (r"SPOTIFY", "Spotify", "Subscriptions"),
    (r"\bHULU\b", "Hulu", "Subscriptions"),
    (r"DISNEY ?PLUS|DISNEYPLUS", "Disney+", "Subscriptions"),
    (r"\bMAX\.COM|HBO ?MAX", "Max", "Subscriptions"),
    (r"PEACOCK", "Peacock", "Subscriptions"),
    (r"PARAMOUNT", "Paramount+", "Subscriptions"),
    (r"AMAZON PRIME|PRIME VIDEO|AMZN PRIME|PRIME MEMBERSHIP", "Amazon Prime", "Subscriptions"),
    (r"AUDIBLE", "Audible", "Subscriptions"),
    (r"AMAZON WEB SERVICES|\bAWS\b", "AWS", "Subscriptions"),
    (r"MICROSOFT|\bMSFT\b", "Microsoft", "Subscriptions"),
    (r"ADOBE", "Adobe", "Subscriptions"),
    (r"DROPBOX", "Dropbox", "Subscriptions"),
    (r"GITHUB", "GitHub", "Subscriptions"),
    (r"NYTIMES|NEW YORK TIMES", "New York Times", "Subscriptions"),
    (r"PATREON", "Patreon", "Subscriptions"),
    # ---- shopping -------------------------------------------------------------------
    (r"AMAZON|AMZN", "Amazon", "Shopping"),
    (r"TARGET", "Target", "Shopping"),
    (r"NEWEGG", "Newegg", "Shopping"),
    (r"ROSS STORES|^ROSS\b|ROSS DRESS", "Ross", "Shopping"),
    (r"OLD NAVY", "Old Navy", "Shopping"),
    (r"JCPENNEY|JC PENNEY", "JCPenney", "Shopping"),
    (r"CARTER'?S", "Carter's", "Shopping"),
    (r"ABEVAULT", "Abevault", "Shopping"),
    (r"PUMPIN PAL", "Pumpin Pal", "Shopping"),
    (r"WALMART|WAL-MART|WM SUPERCENTER", "Walmart", "Shopping"),
    (r"KOHL'?S", "Kohl's", "Shopping"),
    (r"MACY'?S", "Macy's", "Shopping"),
    (r"MARSHALLS", "Marshalls", "Shopping"),
    (r"TJ ?MAXX", "TJ Maxx", "Shopping"),
    (r"BURLINGTON", "Burlington", "Shopping"),
    (r"BEST ?BUY", "Best Buy", "Shopping"),
    (r"\bEBAY\b", "eBay", "Shopping"),
    (r"\bETSY\b", "Etsy", "Shopping"),
    (r"\bSHEIN\b", "Shein", "Shopping"),
    (r"\bTEMU\b", "Temu", "Shopping"),
    (r"DOLLAR ?TREE", "Dollar Tree", "Shopping"),
    (r"MICHAELS", "Michaels", "Shopping"),
    (r"\bGAP\b", "Gap", "Shopping"),
    (r"\bNIKE\b", "Nike", "Shopping"),
    # ---- travel ---------------------------------------------------------------------
    (r"CHASE TRAVEL|TRIPCHRG", "Chase Travel", "Travel"),
    (r"UNITED AIRLINES|\bUNITED \d{6,}", "United Airlines", "Travel"),
    (r"DELTA AIR|\bDELTA \d{6,}", "Delta", "Travel"),
    (r"AMERICAN AIRLINES|\bAA\.COM", "American Airlines", "Travel"),
    (r"SOUTHWEST AIR|SOUTHWES\b", "Southwest", "Travel"),
    (r"JETBLUE", "JetBlue", "Travel"),
    (r"SPIRIT AIRL", "Spirit Airlines", "Travel"),
    (r"FRONTIER AIR", "Frontier Airlines", "Travel"),
    (r"ALASKA AIR", "Alaska Airlines", "Travel"),
    (r"AIR INDIA", "Air India", "Travel"),
    (r"EMIRATES", "Emirates", "Travel"),
    (r"LUFTHANSA", "Lufthansa", "Travel"),
    (r"BRITISH AIRWAYS|BRITISH A", "British Airways", "Travel"),
    (r"QATAR AIR", "Qatar Airways", "Travel"),
    (r"AMTRAK", "Amtrak", "Travel"),
    (r"MARRIOTT|COURTYARD", "Marriott", "Travel"),
    (r"HILTON|HAMPTON INN", "Hilton", "Travel"),
    (r"HYATT", "Hyatt", "Travel"),
    (r"HOLIDAY INN|\bIHG\b", "IHG", "Travel"),
    (r"AIRBNB", "Airbnb", "Travel"),
    (r"BOOKING\.COM", "Booking.com", "Travel"),
    (r"EXPEDIA", "Expedia", "Travel"),
    (r"HOTELS\.COM", "Hotels.com", "Travel"),
    (r"\bVRBO\b", "Vrbo", "Travel"),
    (r"HERTZ", "Hertz", "Travel"),
    (r"\bAVIS\b", "Avis", "Travel"),
    (r"ENTERPRISE RENT", "Enterprise", "Travel"),
    # ---- health ---------------------------------------------------------------------
    (r"PRINCETON HEALTHCARE|PENN MEDICINE PRINCETON", "Princeton Healthcare", "Health"),
    (r"\bCVS\b", "CVS", "Health"),
    (r"WALGREENS", "Walgreens", "Health"),
    (r"RITE AID", "Rite Aid", "Health"),
    (r"LABCORP", "Labcorp", "Health"),
    (r"QUEST DIAG", "Quest Diagnostics", "Health"),
    # ---- home & garden --------------------------------------------------------------
    (r"TLC THE LAWN|LAWN COMPANY", "TLC The Lawn Company", "Home & Garden"),
    (r"HOME ?DEPOT", "Home Depot", "Home & Garden"),
    (r"LOWE'?S\b", "Lowe's", "Home & Garden"),
    (r"\bIKEA\b", "IKEA", "Home & Garden"),
    (r"WAYFAIR", "Wayfair", "Home & Garden"),
    (r"HOMEGOODS", "HomeGoods", "Home & Garden"),
    (r"ACE HARDWARE", "Ace Hardware", "Home & Garden"),
    (r"TRACTOR SUPPLY", "Tractor Supply", "Home & Garden"),
    # ---- dining (Shayona before the generic BAPS donation rule) ---------------------
    (r"SHAYONA", "BAPS Shayona", "Dining"),
    (r"\bWAWA\b", "Wawa", "Dining"),
    (r"DOORDASH|^DD \*", "DoorDash", "Dining"),
    (r"GRUBHUB", "Grubhub", "Dining"),
    (r"STARBUCKS", "Starbucks", "Dining"),
    (r"DUNKIN", "Dunkin'", "Dining"),
    (r"MCDONALD", "McDonald's", "Dining"),
    (r"CHIPOTLE", "Chipotle", "Dining"),
    (r"PANERA", "Panera Bread", "Dining"),
    (r"CHICK-?FIL-?A|CHICK FIL A", "Chick-fil-A", "Dining"),
    (r"DOMINO'?S", "Domino's", "Dining"),
    (r"PIZZA HUT", "Pizza Hut", "Dining"),
    (r"\bSUBWAY\b", "Subway", "Dining"),
    (r"TACO BELL", "Taco Bell", "Dining"),
    (r"WENDY'?S", "Wendy's", "Dining"),
    (r"BURGER KING", "Burger King", "Dining"),
    (r"CHEESECAKE FACTORY", "Cheesecake Factory", "Dining"),
    (r"OLIVE GARDEN", "Olive Garden", "Dining"),
    # ---- donations ------------------------------------------------------------------
    (r"BAPS ENDOWMENT", "BAPS Endowment", "Donations"),
    (r"BAPS CHARITIES", "BAPS Charities", "Donations"),
    # ---- fees & interest ------------------------------------------------------------
    (r"^INTEREST CHARGE|PURCHASE INTEREST|INTEREST CHARGED|^INTEREST ON\b", "Interest Charge",
     "Fees & Interest"),
    (r"^INTEREST (PAYMENT|PAID|EARNED|CREDIT)", "Interest Paid", None),
    (r"LATE (PAYMENT )?FEE", "Late Fee", "Fees & Interest"),
    (r"ANNUAL (MEMBERSHIP )?FEE", "Annual Fee", "Fees & Interest"),
    (r"FOREIGN (TRANSACTION|TRANS|EXCHANGE) FEE|FOREIGN TRANSACTION", "Foreign Transaction Fee",
     "Fees & Interest"),
    (r"MONTHLY SERVICE FEE|SERVICE CHARGE", "Monthly Service Fee", "Fees & Interest"),
    (r"OVERDRAFT|\bNSF\b|INSUFFICIENT FUNDS", "Overdraft Fee", "Fees & Interest"),
    # ---- services -------------------------------------------------------------------
    (r"\bUSPS\b|POSTAL SERVICE", "USPS", "Services"),
    (r"THE UPS STORE|UPS STORE|\bUPS\b", "UPS", "Services"),
    (r"FEDEX", "FedEx", "Services"),
]
_ALIASES = [(re.compile(p), n, c) for p, n, c in ALIASES]

# Category-only keyword rules (after the alias table). (regex, category)
KEYWORD_RULES = [
    (r"MANDIR|TEMPLE|\bBAPS\b|CHURCH|MOSQUE|MASJID|GURUDWARA|DONATION|DONATE|CHARITY|"
     r"CHARITIES|GOFUNDME|RED CROSS|UNICEF|\bSEVA\b|\bSEWA\b|PARISH|SYNAGOGUE", "Donations"),
    (r"^TST\s*\*|RESTAURANT|PIZZ|GRILL|\bCAFE\b|DINER|BISTRO|KITCHEN|TAQUERIA|SUSHI|BURGER|"
     r"BAKERY|BAGEL|COFFEE|DHABA|BIRYANI|DOSA|TANDOOR|CURRY|NOODLE|RAMEN|BBQ|STEAKHOUSE|"
     r"TAVERN|BREWERY|\bPUB\b|DELI\b|ICE CREAM|DONUT|CHAAT|SWEETS", "Dining"),
    (r"PHARMACY|\bPHARM\b|MEDICAL|HOSPITAL|DENTAL|DENTIST|ORTHODONT|PEDIATRIC|CLINIC|"
     r"URGENT CARE|OPTOMETR|EYE CARE|HEALTHCARE|HEALTH SYSTEM|PHYSICIAN|\bMD\b|THERAPY", "Health"),
    (r"\bGAS\b|FUEL|AUTO PARTS|AUTO REPAIR|CAR WASH|TIRE|COLLISION|MOTORS\b", "Gas & Auto"),
    (r"PARKING|\bTOLL|TURNPIKE|TRANSIT", "Tolls & Transit"),
    (r"AIRLINE|AIRWAYS|\bHOTEL|\bINN\b|RESORT|MOTEL", "Travel"),
    (r"SUPERMARKET|GROCERY|GROCER|FARM MARKET|\bMARKET\b|BAZAAR|BAZAR", "Groceries"),
    (r"ELECTRIC|\bWATER\b|SEWER|NATURAL GAS|UTILIT", "Utilities"),
    (r"HARDWARE|NURSERY|GARDEN|LAWN|LANDSCAP|PEST CONTROL|PLUMB|HVAC|ROOFING", "Home & Garden"),
    (r"SALON|BARBER|LAUNDR|DRY CLEAN|CLEANERS|TAILOR|NOTARY|POST OFFICE|SHIPPING", "Services"),
    (r"LATE FEE|ANNUAL FEE|FOREIGN TRANSACTION|INTEREST CHARGE|PURCHASE INTEREST|"
     r"SERVICE FEE|OVERDRAFT|SERVICE CHARGE|\bFEE\b|\bNSF\b", "Fees & Interest"),
    (r"SUBSCRIPTION|MEMBERSHIP", "Subscriptions"),
    (r"\bSTORE\b|\bSTORES\b|OUTLET|BOUTIQUE|APPAREL|CLOTHING|\bSHOP\b|\.COM\b", "Shopping"),
]
_KEYWORDS = [(re.compile(p), c) for p, c in KEYWORD_RULES]

# Card-side "payment received" lines, used defensively when a parser emits them as
# credit/other rather than payment.
_CARD_PAYMENT_RX = re.compile(
    r"^(AUTOMATIC PAYMENT|AUTOPAY\b|AUTO-?PMT|PAYMENT RECEIVED|PAYMENT THANK YOU|"
    r"PAYMENT - THANK YOU|ONLINE PAYMENT|MOBILE PAYMENT|INTERNET PAYMENT|ELECTRONIC PAYMENT|"
    r"THANK YOU FOR YOUR PAYMENT|ONLINE ACH PAYMENT|PAYMENT\s*-?\s*(WEB|MOBILE|ONLINE|ACH)|"
    r"PAYMENT$|AUTOPAY PAYMENT)"
)
_REWARDS_RX = re.compile(r"CASH ?BACK REDEMPTION|REWARDS? REDEMPTION|REDEMPTION CREDIT|"
                         r"STATEMENT CREDIT.*REWARD|CASHBACK BONUS|REWARD CREDIT")

# Bank (deposit / withdrawal / other) rules, evaluated before the merchant tables.
# (regex, category, required sign: +1 / -1 / 0 = any)
# Run before the saving / transfer destinations: "IRS TREAS 310 TAX REF" is a tax refund
# (income), while "TREAS 310 MISC PAY" is a matured Treasury (savings coming back).
BANK_INCOME_FIRST = [
    (r"\bIRS\b|TAX REF|TAXRFD|TAX REFUND|NJ (DIV(ISION)? OF )?TAXATION|STATE OF NJ\b.*TAX|"
     r"\bSSA\b|SOC SEC|UNEMPLOYMENT|STIMULUS|\bVA BENEFIT", "Tax & Government", +1),
    (r"\bPAYROLL\b|DIRECT DEP|\bDIR DEP\b|\bSALARY\b|\bPAYRLL\b", "Salary", +1),
]
_BANK_INCOME_FIRST = [(re.compile(p), c, s) for p, c, s in BANK_INCOME_FIRST]

BANK_RULES = [
    (r"INTEREST (PAYMENT|PAID|EARNED|CREDIT)|^INTEREST$|^INTEREST\b.*\bPAID\b", "Interest", +1),
    # savings destinations, card payments and own-account transfers are matched through
    # the alias table (categories "Savings & Investments" / "Card Payment" / "Transfer");
    # donations must beat the Zelle rule:
    (r"MANDIR|TEMPLE|BAPS ENDOWMENT|\bBAPS\b|CHURCH|MOSQUE|MASJID|GURUDWARA|DONATION|"
     r"CHARITY|CHARITIES|GOFUNDME|\bSEVA\b|\bSEWA\b|SYNAGOGUE", "Donations", 0),
    (r"^ZELLE (PAYMENT )?TO\b", "Family & Friends", 0),
    (r"^ZELLE (PAYMENT )?FROM\b", "Other Income", 0),
    (r"\bVENMO\b|CASH APP|SQUARE CASH|\bSQC\*", "Family & Friends", -1),
    (r"\bVENMO\b|CASH APP|SQUARE CASH|\bSQC\*", "Other Income", +1),
    (r"ATM WITHDRAWAL|CASH WITHDRAWAL|WITHDRAWAL AT|^ATM\b", "Other", -1),
]
_BANK_RULES = [(re.compile(p), c, s) for p, c, s in BANK_RULES]

_BANK_SECTIONS = {"deposit", "withdrawal", "other"}


# --------------------------------------------------------------------------------------
# Merchant names
# --------------------------------------------------------------------------------------

_PROCESSORS = (
    "SQ", "SQU", "SP", "TST", "PAYPAL", "PP", "CL", "WWP", "RAZ", "E-Z", "PY", "DD", "IC",
    "BT", "FS", "SMP", "LS", "PAR", "EB", "IN", "GLF", "CKO", "DNH", "TCB", "PLS", "BPS",
    "ZTL", "SPO", "SQSP", "HLU", "BRG", "GOOGLE", "APPLE", "AMZ",
)
_PROC_RX = re.compile(r"^(?:%s)\s*\*\s*" % "|".join(re.escape(p) for p in _PROCESSORS), re.I)
_WALLET_RX = re.compile(r"^(?:APLPAY|APPLE PAY|APPLEPAY|GPAY|GOOGLE PAY|SAMSUNG PAY)\s+", re.I)
_SHOPIFY_RX = re.compile(r"^SP\s+(?=\S)", re.I)
_ACH_ID_RX = re.compile(r"\b(?:PPD|WEB|CCD|TEL|ARC|BOC|POP|IAT|CIE)\s+ID:\s*\S+|\bID:\s*\S+", re.I)
_CARD_FMT_RX = re.compile(
    r"^(?:RECURRING\s+)?(?:DEBIT\s+)?(?:CARD|POS)\s+PURCHASE(?:\s+WITH\s+PIN)?(?:\s+\d{1,2}/\d{1,2})?\s+"
    r"|^PURCHASE\s+AUTHORIZED\s+ON\s+\d{1,2}/\d{1,2}\s+|^POS\s+DEBIT\s+", re.I)
_CARD_SUFFIX_RX = re.compile(r"\s+CARD\s+\d{4}$", re.I)
_PAYPAL_RX = re.compile(r"^PAYPAL\s+(?:PURCHASE|INST\s+XFER)\s+", re.I)
_EMAIL_RX = re.compile(r"\S+@\S+")
_PHONE_RX = re.compile(r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\+\d{10,}")
_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR".split()
)
_TRAILING_FILLER = {"INC", "INC.", "LLC", "CORP", "CORP.", "CO", "CO.", "LTD", "ACH", "PMT",
                    "PYMT", "PPD", "DES", "WEB", "ONLINE", "EPAY", "PAYMENT", "DEBIT", "-", ","}
_CITY_PREFIX = {"EAST", "WEST", "NORTH", "SOUTH", "NEW", "SAINT", "ST", "LAKE", "MOUNT", "MT",
                "PORT", "FORT", "FT", "PT", "LITTLE", "OLD", "UPPER", "LOWER"}
_DOMAIN_RX = re.compile(r"^(?:WWW\.)?([A-Z0-9-]+)\.(?:COM|NET|ORG|IO|AI|CO|US)(?:/\S*)?$", re.I)


def _match_alias(norm: str):
    for rx, name, cat in _ALIASES:
        m = rx.search(norm)
        if m:
            return m, name, cat
    return None


def _clean_fallback(description: str) -> str:
    s = re.sub(r"\s+", " ", description or "").strip()
    s = re.sub(r"^&\s*", "", s)
    s = _ACH_ID_RX.sub(" ", s)
    s = _CARD_FMT_RX.sub("", s)
    s = _CARD_SUFFIX_RX.sub("", s)
    s = _PAYPAL_RX.sub("", s)
    s = _WALLET_RX.sub("", s)
    for _ in range(2):
        s2 = _PROC_RX.sub("", s)
        s2 = _SHOPIFY_RX.sub("", s2) if s2 == s else s2
        if s2 == s:
            break
        s = s2
    # remaining "BRAND*ref" -> keep the brand part when it is long enough
    m = re.match(r"^([^*]+?)\s*\*\s*(.*)$", s)
    if m:
        head, tail = m.group(1).strip(), m.group(2).strip()
        s = head if len(re.sub(r"[^A-Za-z]", "", head)) >= 4 or not tail else tail
    s = _EMAIL_RX.sub(" ", s)
    s = _PHONE_RX.sub(" ", s)
    toks = s.split()
    out: list[str] = []
    for i, t in enumerate(toks):
        dm = _DOMAIN_RX.match(t)
        if i == 0 and dm:
            out.append(dm.group(1))
            continue
        if i > 0 and (t.startswith("#") or t.isdigit() or len(re.findall(r"\d", t)) >= 3
                      or dm or "/" in t):
            break
        out.append(t)
    had_state = False
    while len(out) > 1 and (out[-1].upper() in _STATES or out[-1].upper() in _TRAILING_FILLER):
        had_state = had_state or out[-1].upper() in _STATES
        out.pop()
    if had_state and len(out) >= 3:
        # "JOES PIZZA ANYTOWN NJ": the word before the state is the city
        out.pop()
        if len(out) >= 3 and out[-1].upper() in _CITY_PREFIX:
            out.pop()
    while len(out) > 1 and out[-1].lower() in _SMALL_WORDS:
        out.pop()
    out = [t.strip(",;:") for t in out if t.strip(",;:-")]
    if not out:
        out = (description or "").split()[:3]
    out = out[:3]
    while len(out) > 1 and out[-1].lower() in _SMALL_WORDS:
        out.pop()
    return title_case(" ".join(out))


_BILLPAY_RX = re.compile(r"^ONLINE (?:PAYMENT|PMT) \d+ TO (?P<payee>.+?)(?: \d{1,2}/\d{1,2})?$", re.I)


def _unwrap_billpay(description: str) -> str:
    """Chase bill pay: "Online Payment 123456 To Verizon Wireless 09/14" -> "Verizon Wireless"."""
    s = re.sub(r"\s+", " ", description or "").strip()
    m = _BILLPAY_RX.match(s)
    return m.group("payee") if m else s


_CHECK_RX = re.compile(r"^(?:CHECK|CHK)\s*(?:#|NO\.?|NUMBER)?\s*(\d{2,})\b")


def merchant_name(description: str) -> str:
    """Clean display name for a raw statement description."""
    description = _unwrap_billpay(description)
    norm = _norm(description)
    if not norm:
        return "Unknown"
    if _CARD_PAYMENT_RX.search(norm):
        return "Autopay Payment" if re.search(r"AUTO", norm) else "Payment Received"
    # Every check goes to a different payee: keep the number so a category picked
    # for one check (and its merchant rule) never spreads to the others.
    chk = _CHECK_RX.match(norm)
    if chk:
        return f"Check #{chk.group(1)}"
    hit = _match_alias(norm)
    if hit:
        m, name, _ = hit
        return name(m, norm) if callable(name) else name
    return _clean_fallback(description)


# --------------------------------------------------------------------------------------
# Categorization
# --------------------------------------------------------------------------------------

def merchant_category(description: str) -> str | None:
    """Category implied by the merchant tables alone (None if nothing matches)."""
    norm = _norm(description)
    hit = _match_alias(norm)
    if hit and hit[2]:
        return hit[2]
    for rx, cat in _KEYWORDS:
        if rx.search(norm):
            return cat
    return None


def _auto_category(description: str, amount_cents: int, section: str) -> str:
    section = (section or "other").lower()
    if section in _BANK_SECTIONS:
        description = _unwrap_billpay(description)
    norm = _norm(description)

    if section == "payment":
        # Defensive: a known merchant mislabelled as a payment (e.g. Chase's
        # "AMAZON MKTPLACE PMTS" refunds) is a credit, not a card payment.
        cat = merchant_category(description)
        if cat and CATEGORY_KIND.get(cat) == "expense" and amount_cents > 0 \
                and not _CARD_PAYMENT_RX.search(norm):
            return cat
        return "Card Payment"

    if section in ("fee", "interest"):
        return "Fees & Interest"

    if section in ("credit", "other") and _CARD_PAYMENT_RX.search(norm):
        return "Card Payment"
    if section == "credit" and _REWARDS_RX.search(norm):
        return "Other Income"

    if section in _BANK_SECTIONS:
        sign = 1 if amount_cents > 0 else -1 if amount_cents < 0 else 0
        for rx, cat, need in _BANK_INCOME_FIRST:
            if need == sign and rx.search(norm):
                return cat
        hit = _match_alias(norm)
        # savings destinations, card payments and own transfers outrank everything else
        if hit and hit[2] in SAVING_CATEGORIES + TRANSFER_CATEGORIES:
            return hit[2]
        for rx, cat, need in _BANK_RULES:
            if (need == 0 or need == sign) and rx.search(norm):
                return cat
        cat = merchant_category(description)
        if cat:
            return cat
        return "Other Income" if amount_cents > 0 else "Other"

    # purchase / credit / cash_advance
    cat = merchant_category(description)
    if cat and CATEGORY_KIND.get(cat) != "income":
        return cat
    return "Other"


def categorize(txn: Any) -> tuple[str, str]:
    """(kind, category) for a txn-like object/dict with description, amount_cents, section."""
    description = _get(txn, "description", "") or ""
    amount = int(_get(txn, "amount_cents", 0) or 0)
    section = _get(txn, "section", "other") or "other"
    category = _auto_category(description, amount, section)
    return kind_for_category(category, amount), category


# --------------------------------------------------------------------------------------
# Transfers
# --------------------------------------------------------------------------------------

def match_transfers(txns: Iterable[Any], window_days: int = TRANSFER_WINDOW_DAYS) -> dict[int, int]:
    """Pair transfer-kind txns across different accounts.

    A pair is an opposite-signed, equal-absolute-amount transfer on a *different*
    account within ±window_days. Closest dates are paired first; each txn is matched
    at most once. Returns {id: counterpart_id} with both directions present.
    """
    items = []
    for t in txns:
        if _get(t, "kind") != "transfer":
            continue
        amt = int(_get(t, "amount_cents", 0) or 0)
        if amt == 0:
            continue
        items.append((int(_get(t, "id")), _get(t, "account_id"), _as_date(_get(t, "date")), amt))

    by_amount: dict[int, list] = defaultdict(list)
    for it in items:
        by_amount[abs(it[3])].append(it)

    candidates = []
    for group in by_amount.values():
        neg = [it for it in group if it[3] < 0]
        pos = [it for it in group if it[3] > 0]
        for a in neg:
            for b in pos:
                if a[1] == b[1]:
                    continue
                gap = abs((a[2] - b[2]).days)
                if gap <= window_days:
                    lo, hi = sorted((a[0], b[0]))
                    candidates.append((gap, min(a[2], b[2]), lo, hi, a[0], b[0]))
    candidates.sort()

    matched: dict[int, int] = {}
    for _gap, _d, _lo, _hi, a_id, b_id in candidates:
        if a_id in matched or b_id in matched:
            continue
        matched[a_id] = b_id
        matched[b_id] = a_id
    return matched


# --------------------------------------------------------------------------------------
# Recurring
# --------------------------------------------------------------------------------------

def _similar(a: int, b: int, tol: float = RECURRING_TOLERANCE) -> bool:
    a, b = abs(a), abs(b)
    return abs(a - b) <= tol * max(a, b)


def detect_recurring(txns: Iterable[Any]) -> set[int]:
    """Ids of expense txns whose merchant is charged in >= 2 distinct calendar months at
    a similar amount (±20%). Merchants charged more than RECURRING_MAX_PER_MONTH times
    in any one month (shopping trips) are skipped."""
    by_merchant: dict[str, list] = defaultdict(list)
    for t in txns:
        if _get(t, "kind") != "expense":
            continue
        merchant = (_get(t, "merchant") or "").strip().lower()
        if not merchant:
            continue
        d = _as_date(_get(t, "date"))
        by_merchant[merchant].append((int(_get(t, "id")), (d.year, d.month),
                                      int(_get(t, "amount_cents", 0) or 0)))

    out: set[int] = set()
    for rows in by_merchant.values():
        months = defaultdict(int)
        for _id, ym, _amt in rows:
            months[ym] += 1
        if len(months) < 2 or max(months.values()) > RECURRING_MAX_PER_MONTH:
            continue
        for tid, ym, amt in rows:
            if any(ym2 != ym and _similar(amt, amt2) for _id2, ym2, amt2 in rows):
                out.add(tid)
    return out
