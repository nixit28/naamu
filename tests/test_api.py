"""HTTP API tests via the Flask test client (synthetic parser with fictional data,
temp data dir)."""
import io
from datetime import date

import pytest

from namu import app as app_module
from namu.app import create_app
from namu.models import Check, ParsedStatement, ParsedTxn, UnsupportedStatement


def d(s):
    return date.fromisoformat(s)


def txn(day, desc, cents, section):
    return ParsedTxn(date=d(day), description=desc, amount_cents=cents, section=section)


STATEMENTS = {
    "chk": [ParsedStatement(
        institution="Chase", product="Total Checking", account_type="checking", last4="1111",
        holder="ALEX SAMPLE", period_start=d("2026-09-10"), period_end=d("2026-10-08"),
        parser="chase_bank",
        transactions=[
            txn("2026-09-11", "Acme Corp Payroll PPD ID: 1234567890", 300000, "deposit"),
            txn("2026-09-28", "Chase Credit Crd Autopay PPD ID: 1234567890", -120000, "withdrawal"),
            txn("2026-10-01", "Aqua Aqua Servi PPD ID: 1234567890", -6000, "withdrawal"),
        ],
        checks=[Check("Deposits and Additions", 300000, 300000)],
        summary={"opening_balance_cents": 500000, "closing_balance_cents": 674000})],
    "fr": [ParsedStatement(
        institution="Chase", product="Freedom Unlimited", account_type="credit", last4="3333",
        holder="ALEX SAMPLE", period_start=d("2026-09-02"), period_end=d("2026-10-01"),
        parser="chase_card",
        transactions=[
            txn("2026-09-25", "& AUTOMATIC PAYMENT - THANK YOU", 120000, "payment"),
            txn("2026-09-01", "& GOOGLE *Workspace_abcd cc@google.com CA", -1200, "purchase"),
            txn("2026-09-16", "& WAWA #1234 ANYTOWN NJ", -500, "purchase"),
            txn("2026-09-27", "& RAZ*Sarvam AI Bengaluru", 1, "credit"),
        ],
        checks=[Check("Purchases", 1700, 1700)],
        summary={"new_balance_cents": 150000, "minimum_due_cents": 4000,
                 "due_date": "2026-10-26", "credit_limit_cents": 1000000})],
}


def fake_parse(path):
    tag = path.read_bytes().decode().split("%")[2].strip()
    if tag == "unknown":
        raise UnsupportedStatement("no parser")
    return STATEMENTS[tag]


def pdf(tag):
    return f"%PDF-1.4\n%{tag}\n".encode()


@pytest.fixture
def client(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>Namu</title>dashboard")
    (static / "app.js").write_text("console.log('hi')")
    app = create_app(data_dir=tmp_path / "data", parse=fake_parse, static_dir=static)
    app.config["TESTING"] = True
    return app.test_client()


def upload(client, *files):
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    return client.post("/api/upload", data=data, content_type="multipart/form-data")


def test_binds_to_localhost_only():
    assert app_module.HOST == "127.0.0.1" and app_module.PORT == 8765


def test_static_files(client):
    r = client.get("/")
    assert r.status_code == 200 and b"dashboard" in r.data
    assert client.get("/app.js").status_code == 200
    assert client.get("/missing.css").status_code == 404
    assert client.get("/../namu/app.py").status_code == 404


def test_placeholder_when_dashboard_missing(tmp_path):
    app = create_app(data_dir=tmp_path / "data", parse=fake_parse, static_dir=tmp_path / "none")
    r = app.test_client().get("/")
    assert r.status_code == 200 and b"Namu" in r.data


def test_empty_data(client):
    r = client.get("/api/data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["accounts"] == [] and body["transactions"] == [] and body["statements"] == []
    assert body["rules"] == []
    assert body["categories"][0] == {"name": "Housing", "kind": "expense"}
    assert {"name": "Card Payment", "kind": "transfer"} in body["categories"]
    assert {"name": "Savings & Investments", "kind": "saving"} in body["categories"]
    assert len(body["categories"]) == 24


def test_upload_flow_and_data_shape(client):
    r = upload(client, ("statement-3333.pdf", pdf("fr")), ("chk.pdf", pdf("chk")),
               ("notes.txt", b"not a pdf"), ("mystery.pdf", pdf("unknown")))
    assert r.status_code == 200
    res = r.get_json()["results"]
    assert [x["status"] for x in res] == ["imported", "imported", "error", "error"]
    fr = res[0]
    assert fr["filename"] == "statement-3333.pdf"
    assert fr["label"] == "Chase Freedom Unlimited ••3333"
    assert (fr["period_start"], fr["period_end"]) == ("2026-09-02", "2026-10-01")
    assert (fr["new_txns"], fr["duplicate_txns"], fr["reconciled"]) == (4, 0, True)
    assert isinstance(fr["statement_ids"], list) and len(fr["statement_ids"]) == 1
    assert res[2]["message"] == "Only PDF files are supported"
    assert res[3]["message"] == "Unrecognized statement format"

    dup = upload(client, ("again.pdf", pdf("fr"))).get_json()["results"][0]
    assert dup["status"] == "duplicate" and dup["message"].startswith("Already imported on ")

    body = client.get("/api/data").get_json()
    acct = {a["last4"]: a for a in body["accounts"]}
    assert acct["3333"] == {"id": acct["3333"]["id"], "institution": "Chase",
                            "product": "Freedom Unlimited", "type": "credit", "last4": "3333",
                            "holder": "ALEX SAMPLE", "label": "Chase Freedom Unlimited ••3333"}

    st = {s["parser"]: s for s in body["statements"]}
    card = st["chase_card"]
    assert card["filename"] == "statement-3333.pdf"
    assert card["txn_count"] == 4 and card["reconciled"] is True
    assert card["checks"] == [{"label": "Purchases", "expected": 17.0, "parsed": 17.0, "ok": True}]
    assert card["summary"] == {"new_balance": 1500.0, "minimum_due": 40.0,
                               "due_date": "2026-10-26", "credit_limit": 10000.0}
    assert st["chase_bank"]["summary"] == {"opening_balance": 5000.0, "closing_balance": 6740.0}
    assert len(card["uploaded_at"]) == 19 and card["uploaded_at"][10] == "T"

    txns = body["transactions"]
    keys = {"id", "account_id", "statement_id", "date", "description", "merchant", "amount",
            "kind", "category", "category_source", "cardholder", "transfer_match_id", "recurring"}
    assert all(keys <= set(t) for t in txns)
    # sorted by date desc, then id desc
    order = [(t["date"], t["id"]) for t in txns]
    assert order == sorted(order, reverse=True)
    g = next(t for t in txns if t["merchant"] == "Google Workspace")
    assert g["amount"] == -12.0 and isinstance(g["amount"], float)
    assert (g["kind"], g["category"], g["category_source"]) == ("expense", "Subscriptions", "auto")
    sarvam = next(t for t in txns if t["merchant"] == "Sarvam AI")
    assert sarvam["amount"] == 0.01 and sarvam["kind"] == "refund"
    pay = next(t for t in txns if t["description"].startswith("& AUTOMATIC PAYMENT"))
    chk = next(t for t in txns if t["description"].startswith("Chase Credit Crd"))
    assert pay["transfer_match_id"] == chk["id"] and chk["transfer_match_id"] == pay["id"]

    # dashboard math from the contract
    money_in = sum(t["amount"] for t in txns if t["kind"] == "income")
    money_out = -sum(t["amount"] for t in txns if t["kind"] in ("expense", "refund"))
    assert money_in == pytest.approx(3000.00)
    assert money_out == pytest.approx(12.00 + 5.00 + 60.00 - 0.01)


def test_patch_transaction(client):
    upload(client, ("fr.pdf", pdf("fr")))
    txns = client.get("/api/data").get_json()["transactions"]
    wawa = next(t for t in txns if t["merchant"] == "Wawa")

    r = client.patch(f"/api/transactions/{wawa['id']}", json={"category": "Gas & Auto"})
    assert r.status_code == 200 and r.get_json() == {"updated": [wawa["id"]]}
    t = next(t for t in client.get("/api/data").get_json()["transactions"] if t["id"] == wawa["id"])
    assert (t["category"], t["category_source"]) == ("Gas & Auto", "user")

    r = client.patch(f"/api/transactions/{wawa['id']}",
                     json={"category": "Dining", "apply_to_merchant": True})
    assert r.status_code == 200 and wawa["id"] in r.get_json()["updated"]
    body = client.get("/api/data").get_json()
    assert body["rules"] == [{"merchant": "Wawa", "category": "Dining"}]
    t = next(t for t in body["transactions"] if t["id"] == wawa["id"])
    assert (t["category"], t["category_source"]) == ("Dining", "rule")

    assert client.patch(f"/api/transactions/{wawa['id']}", json={"category": "Nope"}).status_code == 400
    assert client.patch(f"/api/transactions/{wawa['id']}", json={}).status_code == 400
    assert client.patch(f"/api/transactions/{wawa['id']}", data="x").status_code == 400
    r = client.patch("/api/transactions/99999", json={"category": "Dining"})
    assert r.status_code == 404 and r.get_json()["error"]


def test_delete_statement(client):
    res = upload(client, ("fr.pdf", pdf("fr"))).get_json()["results"][0]
    sid = res["statement_ids"][0]
    r = client.delete(f"/api/statements/{sid}")
    assert r.status_code == 200 and r.get_json() == {"deleted": True}
    assert client.get("/api/data").get_json()["transactions"] == []
    r = client.delete(f"/api/statements/{sid}")
    assert r.status_code == 404 and "error" in r.get_json()
    # re-upload after delete works (not a duplicate)
    assert upload(client, ("fr.pdf", pdf("fr"))).get_json()["results"][0]["status"] == "imported"


def test_reparse(client):
    upload(client, ("fr.pdf", pdf("fr")), ("chk.pdf", pdf("chk")))
    r = client.post("/api/reparse")
    assert r.status_code == 200
    assert r.get_json()["statements"] == 2
    assert len(client.get("/api/data").get_json()["transactions"]) == 7


def test_upload_requires_files(client):
    r = client.post("/api/upload", data={}, content_type="multipart/form-data")
    assert r.status_code == 400 and "error" in r.get_json()


def test_upload_size_cap(client):
    assert client.application.config["MAX_CONTENT_LENGTH"] == 50 * 1024 * 1024
    client.application.config["MAX_CONTENT_LENGTH"] = 1024
    r = upload(client, ("big.pdf", b"%PDF-1.4\n" + b"0" * 4096))
    assert r.status_code == 413 and "50 MB" in r.get_json()["error"]


def test_json_errors(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.is_json and "error" in r.get_json()
    r = client.post("/api/data")
    assert r.status_code == 405 and r.is_json


def test_local_only_guard(client):
    r = client.get("/api/data", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    r = client.post("/api/reparse", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = client.post("/api/reparse", headers={"Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200
    assert client.get("/api/data", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_startup_recomputes_existing_db(tmp_path):
    import sqlite3
    data_dir = tmp_path / "data"
    first = create_app(data_dir=data_dir, parse=fake_parse).test_client()
    upload(first, ("chk.pdf", pdf("chk")), ("fr.pdf", pdf("fr")))
    txns = first.get("/api/data").get_json()["transactions"]
    wawa = next(t for t in txns if t["merchant"] == "Wawa")
    first.patch(f"/api/transactions/{wawa['id']}", json={"category": "Groceries"})
    # simulate a DB written by older rules: stale kinds/categories/matches
    conn = sqlite3.connect(data_dir / "namu.db")
    conn.execute("UPDATE transactions SET kind='expense', category='Other', "
                 "category_source='auto', transfer_match_id=NULL, merchant='stale'")
    conn.commit()
    conn.close()
    body = create_app(data_dir=data_dir, parse=fake_parse).test_client().get("/api/data").get_json()
    by_desc = {t["description"]: t for t in body["transactions"]}
    assert by_desc["Aqua Aqua Servi PPD ID: 1234567890"]["category"] == "Utilities"
    assert by_desc["Chase Credit Crd Autopay PPD ID: 1234567890"]["transfer_match_id"] is not None
    w = by_desc["& WAWA #1234 ANYTOWN NJ"]
    assert (w["merchant"], w["category"], w["category_source"]) == ("Wawa", "Groceries", "user")


def test_startup_without_db_creates_nothing(tmp_path):
    create_app(data_dir=tmp_path / "data", parse=fake_parse)
    assert not (tmp_path / "data" / "namu.db").exists()
