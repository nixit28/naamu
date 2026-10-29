"""Ingest pipeline tests with synthetic ParsedStatements (no real parser needed).

All payees, holders, account numbers and amounts are fictional. The optional end-to-end
test at the bottom runs over whatever real PDFs sit in the gitignored samples/ folder.
"""
import json
from datetime import date
from pathlib import Path

import pytest

from naamu.ingest import Library, fingerprint, account_key
from naamu.models import Check, ParsedStatement, ParsedTxn, UnsupportedStatement


# --------------------------------------------------------------------------------------
# helpers: fake "PDFs" carry a tag that a fake parser maps to synthetic statements
# --------------------------------------------------------------------------------------

class FakeParser:
    def __init__(self):
        self.registry: dict[str, object] = {}
        self.calls = 0

    def pdf(self, tag: str, statements) -> bytes:
        self.registry[tag] = statements
        return f"%PDF-1.4\n%{tag}\n".encode()

    def __call__(self, path):
        self.calls += 1
        tag = path.read_bytes().decode().split("%")[2].strip()
        value = self.registry[tag]
        if isinstance(value, Exception):
            raise value
        return value() if callable(value) else value


def d(s: str) -> date:
    return date.fromisoformat(s)


def txn(day, desc, cents, section, **kw):
    return ParsedTxn(date=d(day), description=desc, amount_cents=cents, section=section, **kw)


def checking(start="2026-09-10", end="2026-10-08", txns=None, checks=None):
    return ParsedStatement(
        institution="Chase", product="Total Checking", account_type="checking", last4="1111",
        holder="ALEX SAMPLE", period_start=d(start), period_end=d(end), parser="chase_bank",
        transactions=txns if txns is not None else [
            txn("2026-09-11", "Acme Corp Payroll PPD ID: 1234567890", 300000, "deposit"),
            txn("2026-09-17", "American Express ACH Pmt A1234 Web ID: 1234567890", -30000, "withdrawal"),
            txn("2026-09-28", "Chase Credit Crd Autopay PPD ID: 1234567890", -120000, "withdrawal"),
            txn("2026-10-01", "Newrez-Shellpoin ACH Pmt PPD ID: 1234567890", -200000, "withdrawal"),
        ],
        checks=checks if checks is not None else [Check("Deposits", 300000, 300000)],
        summary={"opening_balance_cents": 500000, "closing_balance_cents": 450000},
    )


def amex(start="2026-09-03", end="2026-10-02", txns=None):
    return ParsedStatement(
        institution="American Express", product="Blue Cash Everyday", account_type="credit",
        last4="2222", holder="JORDAN SAMPLE", period_start=d(start), period_end=d(end),
        parser="amex", transactions=txns if txns is not None else [
            txn("2026-09-17", "AUTOPAY PAYMENT RECEIVED - THANK YOU", 30000, "payment"),
            txn("2026-09-19", "AplPay SP PUMPIN PAL", 2500, "credit"),
            txn("2026-09-21", "TARGET.COM 800-555-0108 MN", -3000, "purchase"),
        ],
        checks=[Check("Payments/Credits", 32500, 32500)],
        summary={"new_balance_cents": 3000, "due_date": "2026-10-27"},
    )


def freedom(start="2026-09-02", end="2026-10-01", txns=None):
    return ParsedStatement(
        institution="Chase", product="Freedom Unlimited", account_type="credit", last4="3333",
        holder="ALEX SAMPLE", period_start=d(start), period_end=d(end), parser="chase_card",
        transactions=txns if txns is not None else [
            txn("2026-09-25", "& AUTOMATIC PAYMENT - THANK YOU", 120000, "payment"),
            txn("2026-09-02", "& BJS WHOLESALE #0123 ANYTOWN NJ", -10000, "purchase"),
            txn("2026-09-16", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),
        ],
        checks=[Check("Purchases", 10500, 10500)],
    )


@pytest.fixture
def fake():
    return FakeParser()


@pytest.fixture
def lib(tmp_path, fake):
    return Library(tmp_path / "data", parse=fake)


def txns_by_desc(lib):
    return {t["description"]: t for t in lib.get_data()["transactions"]}


# --------------------------------------------------------------------------------------

def test_import_and_enrich(lib, fake, tmp_path):
    r1 = lib.ingest_file(fake.pdf("chk", [checking()]), "checking-statement.pdf")
    r2 = lib.ingest_file(fake.pdf("amex", [amex()]), "amex.pdf")
    assert r1["status"] == "imported" and r1["new_txns"] == 4 and r1["duplicate_txns"] == 0
    assert r1["label"] == "Chase Total Checking ••1111"
    assert r1["period_start"] == "2026-09-10" and r1["period_end"] == "2026-10-08"
    assert r1["reconciled"] is True
    assert r2["label"] == "American Express Blue Cash Everyday ••2222"

    # original stored under uploads/<sha256>.pdf, DB in the data dir (not the project's)
    uploads = list((tmp_path / "data" / "uploads").glob("*.pdf"))
    assert len(uploads) == 2 and all(len(p.stem) == 64 for p in uploads)
    assert (tmp_path / "data" / "naamu.db").exists()

    t = txns_by_desc(lib)
    pay_chk = t["American Express ACH Pmt A1234 Web ID: 1234567890"]
    pay_card = t["AUTOPAY PAYMENT RECEIVED - THANK YOU"]
    assert pay_chk["kind"] == pay_card["kind"] == "transfer"
    assert pay_chk["transfer_match_id"] == pay_card["id"]
    assert pay_card["transfer_match_id"] == pay_chk["id"]
    # Freedom not uploaded yet: its autopay on checking is an unmatched transfer
    autopay = t["Chase Credit Crd Autopay PPD ID: 1234567890"]
    assert autopay["kind"] == "transfer" and autopay["transfer_match_id"] is None
    assert t["Acme Corp Payroll PPD ID: 1234567890"]["category"] == "Salary"
    assert t["AplPay SP PUMPIN PAL"]["kind"] == "refund"

    # uploading the other side later matches it
    lib.ingest_file(fake.pdf("fr", [freedom()]), "freedom.pdf")
    t = txns_by_desc(lib)
    assert t["Chase Credit Crd Autopay PPD ID: 1234567890"]["transfer_match_id"] == \
        t["& AUTOMATIC PAYMENT - THANK YOU"]["id"]


def test_duplicate_file_by_sha256(lib, fake):
    data = fake.pdf("chk", [checking()])
    assert lib.ingest_file(data, "a.pdf")["status"] == "imported"
    r = lib.ingest_file(data, "renamed.pdf")
    assert r["status"] == "duplicate"
    assert r["message"].startswith("Already imported on ")
    assert fake.calls == 1                       # not even parsed again
    assert len(lib.get_data()["transactions"]) == 4


def test_same_statement_different_bytes_is_duplicate(lib, fake):
    lib.ingest_file(fake.pdf("v1", [checking()]), "a.pdf")
    r = lib.ingest_file(fake.pdf("v2", [checking()]), "a (1).pdf")
    assert r["status"] == "duplicate" and "same account and period" in r["message"]
    assert len(lib.get_data()["statements"]) == 1


def test_overlapping_statements_dedupe_by_fingerprint(lib, fake):
    a = freedom("2026-09-02", "2026-10-01", [
        txn("2026-09-02", "& BJS WHOLESALE #0123 ANYTOWN NJ", -10000, "purchase"),
        txn("2026-09-20", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),
        txn("2026-09-20", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),   # identical twin
    ])
    b = freedom("2026-09-15", "2026-10-14", [
        txn("2026-09-20", "WAWA #1234 ANYTOWN NJ", -500, "purchase"),     # no '&' this time
        txn("2026-09-20", "WAWA #1234 ANYTOWN NJ", -500, "purchase"),
        txn("2026-09-20", "WAWA #1234 ANYTOWN NJ", -500, "purchase"),     # a third, genuinely new
        txn("2026-10-05", "VISIBLE 800-555-0103 CO", -2500, "purchase"),
    ])
    ra = lib.ingest_file(fake.pdf("a", [a]), "a.pdf")
    rb = lib.ingest_file(fake.pdf("b", [b]), "b.pdf")
    assert (ra["new_txns"], ra["duplicate_txns"]) == (3, 0)
    assert (rb["new_txns"], rb["duplicate_txns"]) == (2, 2)
    data = lib.get_data()
    assert len(data["transactions"]) == 5
    assert sum(t["amount"] for t in data["transactions"]) == pytest.approx(-(100 + 3 * 5 + 25))


def test_fingerprint_is_account_scoped_and_stable():
    k1 = account_key("Chase", "credit", "3333")
    k2 = account_key("Chase", "credit", "4444")
    a = fingerprint(k1, date(2026, 9, 2), -100, "& BJS  WHOLESALE #0123", 0)
    assert a == fingerprint(k1, "2026-09-02", -100, "bjs wholesale 0123", 0)
    assert a != fingerprint(k2, date(2026, 9, 2), -100, "BJS WHOLESALE #0123", 0)
    assert a != fingerprint(k1, date(2026, 9, 2), -100, "BJS WHOLESALE #0123", 1)


def test_override_survives_reparse(lib, fake):
    lib.ingest_file(fake.pdf("fr", [freedom()]), "freedom.pdf")
    wawa = txns_by_desc(lib)["& WAWA #1234 ANYTOWN NJ"]
    assert wawa["category"] == "Dining"
    assert lib.set_category(wawa["id"], "Gas & Auto") == [wawa["id"]]
    calls = fake.calls
    out = lib.reparse_all()
    assert out == {"statements": 1, "errors": []}
    assert fake.calls == calls + 1
    data = lib.get_data()
    live = {s["id"] for s in data["statements"]}
    assert {t["statement_id"] for t in data["transactions"]} <= live   # old rows cascaded away
    w = txns_by_desc(lib)["& WAWA #1234 ANYTOWN NJ"]
    assert (w["category"], w["category_source"], w["kind"]) == ("Gas & Auto", "user", "expense")


def test_reparse_picks_up_parser_changes(lib, fake):
    lib.ingest_file(fake.pdf("fr", [freedom()]), "freedom.pdf")
    st = freedom()
    st.transactions.append(txn("2026-09-30", "& NETFLIX.COM 800-555-0112 CA", -1500, "purchase"))
    fake.registry["fr"] = [st]
    lib.reparse_all()
    assert "& NETFLIX.COM 800-555-0112 CA" in txns_by_desc(lib)


def test_reparse_failure_keeps_previous_data(lib, fake):
    lib.ingest_file(fake.pdf("fr", [freedom()]), "freedom.pdf")
    fake.registry["fr"] = RuntimeError("parser exploded")
    out = lib.reparse_all()
    assert out["statements"] == 1 and len(out["errors"]) == 1
    assert len(lib.get_data()["transactions"]) == 3


def test_override_survives_delete_and_reupload(lib, fake):
    data = fake.pdf("fr", [freedom()])
    lib.ingest_file(data, "freedom.pdf")
    bjs = txns_by_desc(lib)["& BJS WHOLESALE #0123 ANYTOWN NJ"]
    lib.set_category(bjs["id"], "Shopping")
    sid = lib.get_data()["statements"][0]["id"]
    assert lib.delete_statement(sid) is True
    after = lib.get_data()
    assert after["transactions"] == [] and after["statements"] == [] and after["accounts"] == []
    assert list((lib.uploads).glob("*.pdf")) == []          # stored PDF removed too
    r = lib.ingest_file(data, "freedom.pdf")                 # same bytes are importable again
    assert r["status"] == "imported"
    b = txns_by_desc(lib)["& BJS WHOLESALE #0123 ANYTOWN NJ"]
    assert (b["category"], b["category_source"]) == ("Shopping", "user")


def test_delete_missing_statement(lib):
    assert lib.delete_statement(999) is False


def test_delete_restores_overlapping_txns(lib, fake):
    a = freedom("2026-09-02", "2026-10-01", [
        txn("2026-09-02", "& BJS WHOLESALE #0123 ANYTOWN NJ", -10000, "purchase"),
        txn("2026-09-20", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),
    ])
    b = freedom("2026-09-15", "2026-10-14", [
        txn("2026-09-20", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),
        txn("2026-10-05", "& VISIBLE 800-555-0103 CO", -2500, "purchase"),
    ])
    ra = lib.ingest_file(fake.pdf("a", [a]), "a.pdf")
    rb = lib.ingest_file(fake.pdf("b", [b]), "b.pdf")
    lib.delete_statement(ra["statement_ids"][0])
    data = lib.get_data()
    descs = sorted(t["description"] for t in data["transactions"])
    assert descs == ["& VISIBLE 800-555-0103 CO", "& WAWA #1234 ANYTOWN NJ"]
    assert {t["statement_id"] for t in data["transactions"]} == {rb["statement_ids"][0]}
    assert data["statements"][0]["txn_count"] == 2


def test_multi_statement_file_delete_one(lib, fake):
    savings = ParsedStatement(
        institution="Chase", product="Savings", account_type="savings", last4="4444",
        holder="ALEX SAMPLE", period_start=d("2026-09-10"), period_end=d("2026-10-08"),
        parser="chase_bank", transactions=[], checks=[],
        summary={"opening_balance_cents": 50000, "closing_balance_cents": 50000})
    data = fake.pdf("combo", [checking(), savings])
    r = lib.ingest_file(data, "combo.pdf")
    assert len(r["statement_ids"]) == 2
    assert r["label"] == "Chase Total Checking ••1111 + Chase Savings ••4444"
    assert r["reconciled"] is True               # empty savings statement has nothing to verify
    sav_id = r["statement_ids"][1]
    lib.delete_statement(sav_id)
    assert len(lib.get_data()["statements"]) == 1
    lib.reparse_all()                            # must not resurrect the deleted statement
    assert [s["account_id"] for s in lib.get_data()["statements"]] != [] and \
        len(lib.get_data()["statements"]) == 1
    r2 = lib.ingest_file(data, "combo.pdf")      # re-upload restores it
    assert r2["status"] == "imported" and len(r2["statement_ids"]) == 1
    assert len(lib.get_data()["statements"]) == 2


def test_merchant_rule_applies_to_future_uploads(lib, fake):
    lib.ingest_file(fake.pdf("fr", [freedom()]), "freedom.pdf")
    wawa = txns_by_desc(lib)["& WAWA #1234 ANYTOWN NJ"]
    lib.set_category(wawa["id"], "Gas & Auto", apply_to_merchant=True)
    data = lib.get_data()
    assert data["rules"] == [{"merchant": "Wawa", "category": "Gas & Auto"}]
    later = freedom("2026-10-02", "2026-11-01", [
        txn("2026-10-10", "& WAWA #5678 OTHERTOWN NJ", -2500, "purchase")])
    lib.ingest_file(fake.pdf("fr2", [later]), "freedom-nov.pdf")
    t = txns_by_desc(lib)
    assert (t["& WAWA #5678 OTHERTOWN NJ"]["category"],
            t["& WAWA #5678 OTHERTOWN NJ"]["category_source"]) == ("Gas & Auto", "rule")
    assert t["& WAWA #1234 ANYTOWN NJ"]["category_source"] == "rule"


def test_override_beats_rule_and_clearing(lib, fake):
    lib.ingest_file(fake.pdf("fr", [freedom(txns=[
        txn("2026-09-10", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),
        txn("2026-09-11", "& WAWA #1234 ANYTOWN NJ", -900, "purchase")])]), "f.pdf")
    t1, t2 = sorted(lib.get_data()["transactions"], key=lambda t: t["date"])
    lib.set_category(t1["id"], "Groceries")                       # per-txn override
    changed = lib.set_category(t2["id"], "Gas & Auto", apply_to_merchant=True)
    assert changed == [t2["id"]]
    by_id = {t["id"]: t for t in lib.get_data()["transactions"]}
    assert by_id[t1["id"]]["category"] == "Groceries"          # override > rule
    assert by_id[t2["id"]]["category"] == "Gas & Auto"
    lib.set_category(t1["id"], None)                              # clear override -> rule
    assert {t["id"]: t["category"] for t in lib.get_data()["transactions"]}[t1["id"]] == "Gas & Auto"


def test_recategorize_to_transfer_and_back(lib, fake):
    lib.ingest_file(fake.pdf("fr", [freedom()]), "f.pdf")
    wawa = txns_by_desc(lib)["& WAWA #1234 ANYTOWN NJ"]
    lib.set_category(wawa["id"], "Transfer")
    assert txns_by_desc(lib)["& WAWA #1234 ANYTOWN NJ"]["kind"] == "transfer"
    with pytest.raises(ValueError):
        lib.set_category(wawa["id"], "Not A Category")
    with pytest.raises(KeyError):
        lib.set_category(12345, "Dining")


def test_unsupported_and_errors(lib, fake):
    r = lib.ingest_file(fake.pdf("x", UnsupportedStatement("nope")), "x.pdf")
    assert r == {"filename": "x.pdf", "status": "error", "message": "Unrecognized statement format"}
    r = lib.ingest_file(fake.pdf("boom", RuntimeError("bad table")), "boom.pdf")
    assert r["status"] == "error" and "RuntimeError" in r["message"]
    r = lib.ingest_file(fake.pdf("empty", []), "empty.pdf")
    assert r["status"] == "error"
    r = lib.ingest_file(b"hello, not a pdf", "notes.pdf")
    assert r == {"filename": "notes.pdf", "status": "error", "message": "Only PDF files are supported"}
    assert list(lib.uploads.glob("*")) == []                 # nothing left behind
    assert lib.get_data()["statements"] == []


def test_reconciled_states(lib, fake):
    bad = checking(checks=[Check("Deposits", 100, 99)])
    r = lib.ingest_file(fake.pdf("bad", [bad]), "bad.pdf")
    assert r["reconciled"] is False
    generic = amex(txns=[txn("2026-09-21", "TARGET.COM", -3000, "purchase")])
    generic.checks = []
    r = lib.ingest_file(fake.pdf("gen", [generic]), "gen.pdf")
    assert r["reconciled"] is None
    sts = {s["parser"]: s for s in lib.get_data()["statements"]}
    assert sts["chase_bank"]["reconciled"] is False
    assert sts["chase_bank"]["checks"] == [
        {"label": "Deposits", "expected": 1.0, "parsed": 0.99, "ok": False}]
    assert sts["amex"]["reconciled"] is None


def test_recurring_across_statements(lib, fake):
    sep = freedom("2026-08-02", "2026-09-01", [
        txn("2026-08-08", "& APPLE.COM/BILL 800-555-0105 CA", -299, "purchase")])
    oct_ = freedom("2026-09-02", "2026-10-01", [
        txn("2026-09-08", "& APPLE.COM/BILL 800-555-0105 CA", -299, "purchase"),
        txn("2026-09-09", "& BAPS SHAYONA-CHICAGO ANYTOWN IL", -800, "purchase")])
    lib.ingest_file(fake.pdf("s", [sep]), "s.pdf")
    lib.ingest_file(fake.pdf("o", [oct_]), "o.pdf")
    rec = {t["description"]: t["recurring"] for t in lib.get_data()["transactions"]}
    assert rec == {"& APPLE.COM/BILL 800-555-0105 CA": True,
                   "& BAPS SHAYONA-CHICAGO ANYTOWN IL": False}


def test_ingest_from_path(lib, fake, tmp_path):
    p = tmp_path / "statement-1111.pdf"
    p.write_bytes(fake.pdf("chk", [checking()]))
    r = lib.ingest_file(p)
    assert r["status"] == "imported" and r["filename"] == "statement-1111.pdf"


# --------------------------------------------------------------------------------------
# End-to-end on the real statement PDFs in samples/ (gitignored, never committed).
#
# The test itself is value-agnostic: it checks invariants that must hold for any set of
# statements. Exact numbers live in the optional, gitignored samples/expected_ingest.json;
# (re)generate it after an intentional rule change with:
#     .venv/bin/python -m tests.test_ingest --write-expected
# --------------------------------------------------------------------------------------

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
EXPECTED = SAMPLES / "expected_ingest.json"


def _sample_pdfs() -> list[Path]:
    return sorted(SAMPLES.glob("*.pdf")) if SAMPLES.is_dir() else []


def _cents_sum(txns) -> float:
    return round(sum(round(t["amount"] * 100) for t in txns) / 100, 2)


def ingest_samples(data_dir: Path) -> tuple[list[dict], dict, dict]:
    """Ingest every sample PDF with the real parsers; return (results, data, summary)."""
    lib = Library(data_dir)
    results = [lib.ingest_file(p) for p in _sample_pdfs()]
    data = lib.get_data()
    txns = data["transactions"]
    last4 = {a["id"]: a["last4"] for a in data["accounts"]}
    by_id = {t["id"]: t for t in txns}

    def key(t):
        return [last4[t["account_id"]], t["date"], t["amount"]]

    saving = [t for t in txns if t["kind"] == "saving"]
    money_in = _cents_sum(t for t in txns if t["kind"] == "income")
    spent = -_cents_sum(t for t in txns if t["kind"] in ("expense", "refund"))
    saved = -_cents_sum(saving)
    summary = {
        "statuses": {r["filename"]: r["status"] for r in results},
        "statements": len(data["statements"]),
        "transactions": len(txns),
        "money_in": money_in,
        "spent": spent,
        "saved": saved,
        "put_in": -_cents_sum(t for t in saving if t["amount"] < 0),
        "taken_out": _cents_sum(t for t in saving if t["amount"] > 0),
        "left_in_checking": round(money_in - spent - saved, 2),
        "matched_pairs": sorted([key(t), key(by_id[t["transfer_match_id"]])] for t in txns
                                if t["transfer_match_id"] and t["amount"] < 0),
        "unmatched_transfers": sorted(key(t) for t in txns
                                      if t["kind"] == "transfer" and not t["transfer_match_id"]),
        "saving": sorted([t["merchant"], t["amount"]] for t in saving),
        "categories": sorted({(t["merchant"], t["category"]) for t in txns}),
    }
    summary["categories"] = [list(p) for p in summary["categories"]]
    return results, data, summary


@pytest.mark.skipif(not _sample_pdfs(), reason="no sample statements in samples/")
def test_samples_end_to_end(tmp_path):
    pytest.importorskip("naamu.parsers")
    results, data, summary = ingest_samples(tmp_path / "data")
    txns = data["transactions"]
    by_id = {t["id"]: t for t in txns}

    assert all(r["status"] in ("imported", "duplicate") for r in results), results
    assert any(r["status"] == "imported" for r in results)
    assert data["statements"] and all(s["reconciled"] is True for s in data["statements"])

    # every card-side payment is a transfer
    assert all(t["kind"] == "transfer" for t in txns if t["section"] == "payment")

    # matched pairs: mutual, opposite and equal amounts, different accounts, within 6 days
    for t in txns:
        if t["transfer_match_id"] is None:
            continue
        o = by_id[t["transfer_match_id"]]
        assert o["transfer_match_id"] == t["id"]
        assert t["kind"] == o["kind"] == "transfer"
        assert t["amount"] == -o["amount"] != 0
        assert t["account_id"] != o["account_id"]
        assert abs((date.fromisoformat(t["date"]) - date.fromisoformat(o["date"])).days) <= 6
    assert all(t["transfer_match_id"] is None for t in txns if t["kind"] == "saving")

    # dashboard math is self-consistent: what is left in checking is exactly the net of all
    # non-transfer cash flows, and saved = put in - taken out
    assert summary["left_in_checking"] == _cents_sum(t for t in txns if t["kind"] != "transfer")
    assert summary["saved"] == round(summary["put_in"] - summary["taken_out"], 2)
    assert summary["money_in"] >= 0

    if EXPECTED.exists():
        assert summary == json.loads(EXPECTED.read_text())


if __name__ == "__main__":
    import sys
    import tempfile

    if "--write-expected" not in sys.argv[1:]:
        sys.exit("usage: python -m tests.test_ingest --write-expected")
    if not _sample_pdfs():
        sys.exit(f"no PDFs in {SAMPLES}")
    with tempfile.TemporaryDirectory() as tmp:
        _, _, summary = ingest_samples(Path(tmp) / "data")
    EXPECTED.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"wrote {EXPECTED} ({summary['transactions']} transactions)")
