"""Upload pipeline: dedupe -> store PDF -> parse -> insert -> recompute derived fields.

All public operations are available both as ``Library`` methods (bound to one data
directory) and as module-level functions taking ``data_dir=`` / ``parse=`` keywords.

Derived fields (merchant, kind, category, transfer matches, recurring) are recomputed
over the whole database after every change; at household scale that is instant and
keeps the logic trivially consistent. Precedence for categories:
user override (per txn fingerprint) > user merchant rule > built-in rules.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections import Counter
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from . import db, enrich
from .models import SECTIONS, ParsedStatement, UnsupportedStatement

log = logging.getLogger(__name__)

ParseFn = Callable[[Path], "list[ParsedStatement]"]

_LOCK = threading.RLock()        # serializes all writers (Flask runs threaded)


def _default_parse(path: Path) -> list[ParsedStatement]:
    from .parsers import parse_pdf  # imported lazily so the app starts without parsers
    return parse_pdf(path)


# --------------------------------------------------------------------------------------
# Keys & fingerprints
# --------------------------------------------------------------------------------------

def account_key(institution: str, account_type: str, last4: str) -> str:
    return f"{(institution or '').strip().lower()}|{(account_type or '').strip().lower()}|{(last4 or '').strip()}"


def normalize_description(description: str | None) -> str:
    s = (description or "").upper()
    s = re.sub(r"^\s*&\s*", "", s)
    return re.sub(r"[^A-Z0-9]+", " ", s).strip()


def fingerprint(acct_key: str, txn_date: date | str, amount_cents: int, description: str,
                occurrence: int) -> str:
    d = txn_date.isoformat() if isinstance(txn_date, date) else str(txn_date)[:10]
    raw = f"{acct_key}|{d}|{int(amount_cents)}|{normalize_description(description)}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def statement_key(st: ParsedStatement) -> str:
    return f"{account_key(st.institution, st.account_type, st.last4)}|{_iso(st.period_start)}|{_iso(st.period_end)}"


def account_label(institution: str, product: str, account_type: str, last4: str) -> str:
    institution = (institution or "").strip()
    product = (product or "").strip()
    if product and institution and product.lower().startswith(institution.lower()):
        name = product
    elif product:
        name = f"{institution} {product}".strip()
    else:
        name = f"{institution} {(account_type or '').title()}".strip()
    return f"{name} ••{last4}" if last4 else name


def _iso(d: Any) -> str | None:
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.isoformat()[:10]
    return str(d)[:10]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _dollars(cents: int | float | None) -> float | None:
    if cents is None:
        return None
    return round(cents / 100, 2)


def looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def _jsonable(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return v


# --------------------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------------------

class Library:
    def __init__(self, data_dir: str | Path | None = None, parse: ParseFn | None = None):
        self.data_dir = Path(data_dir) if data_dir else db.default_data_dir()
        self.db_path = db.db_path_for(self.data_dir)
        self.uploads = db.uploads_dir_for(self.data_dir)
        self._parse = parse or _default_parse

    # -- plumbing ---------------------------------------------------------------------

    def connect(self):
        return db.connect(self.db_path)

    def _stored_path(self, stored: str) -> Path:
        p = Path(stored)
        return p if p.is_absolute() else self.data_dir / p

    def _write_upload(self, sha: str, data: bytes) -> tuple[Path, bool]:
        """Store bytes at uploads/<sha>.pdf. Returns (path, created_now)."""
        self.uploads.mkdir(parents=True, exist_ok=True)
        path = self.uploads / f"{sha}.pdf"
        if path.exists():
            return path, False
        fd, tmp = tempfile.mkstemp(dir=self.uploads, suffix=".part")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
        return path, True

    # -- ingest -----------------------------------------------------------------------

    def ingest_file(self, source: str | Path | bytes, filename: str | None = None) -> dict:
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
        else:
            data = Path(source).read_bytes()
            filename = filename or Path(source).name
        filename = Path(filename or "statement.pdf").name or "statement.pdf"

        if not looks_like_pdf(data):
            return {"filename": filename, "status": "error",
                    "message": "Only PDF files are supported"}
        sha = hashlib.sha256(data).hexdigest()

        with _LOCK, closing(self.connect()) as conn:
            existing = conn.execute("SELECT * FROM files WHERE sha256 = ?", (sha,)).fetchone()
            if existing and not json.loads(existing["excluded_json"] or "[]"):
                return {"filename": filename, "status": "duplicate",
                        "message": f"Already imported on {existing['uploaded_at'][:10]}"}

            path, created = self._write_upload(sha, data)

            def fail(message: str) -> dict:
                if created and not existing:
                    path.unlink(missing_ok=True)
                return {"filename": filename, "status": "error", "message": message}

            try:
                statements = list(self._parse(path) or [])
            except UnsupportedStatement:
                return fail("Unrecognized statement format")
            except Exception as exc:  # parser bug or unreadable PDF
                log.exception("parse failed for %s", filename)
                return fail(f"Could not read this PDF ({type(exc).__name__}: {exc})")
            if not statements:
                return fail("No statements found in this PDF")

            # statement-level duplicates: same account + same period already imported
            fresh, dup_dates = [], []
            for st in statements:
                prior = self._find_statement(conn, st)
                if prior is None:
                    fresh.append(st)
                else:
                    dup_dates.append(prior["uploaded_at"][:10])
            if not fresh:
                if created and not existing:
                    path.unlink(missing_ok=True)
                return {"filename": filename, "status": "duplicate",
                        "message": f"Already imported on {min(dup_dates)} (same account and period)"}

            with conn:
                if existing:
                    file_id = existing["id"]
                    conn.execute("UPDATE files SET excluded_json = '[]' WHERE id = ?", (file_id,))
                else:
                    file_id = conn.execute(
                        "INSERT INTO files (sha256, filename, uploaded_at, stored_path) VALUES (?,?,?,?)",
                        (sha, filename, _now(), f"uploads/{sha}.pdf"),
                    ).lastrowid
                ids, new_total, dup_total = [], 0, 0
                for st in fresh:
                    sid, new, dup = self._insert_statement(conn, file_id, st)
                    ids.append(sid)
                    new_total += new
                    dup_total += dup
                self.recompute(conn)

        labels = list(dict.fromkeys(
            account_label(st.institution, st.product, st.account_type, st.last4) for st in fresh))
        return {
            "filename": filename,
            "status": "imported",
            "statement_ids": ids,
            "label": " + ".join(labels),
            "period_start": min(_iso(st.period_start) for st in fresh),
            "period_end": max(_iso(st.period_end) for st in fresh),
            "new_txns": new_total,
            "duplicate_txns": dup_total,
            "reconciled": _combined_reconciled(fresh),
        }

    def _find_statement(self, conn, st: ParsedStatement):
        return conn.execute(
            """SELECT s.id, f.uploaded_at FROM statements s
               JOIN accounts a ON a.id = s.account_id JOIN files f ON f.id = s.file_id
               WHERE a.institution = ? AND a.type = ? AND a.last4 = ?
                 AND s.period_start = ? AND s.period_end = ?""",
            (st.institution, st.account_type, st.last4, _iso(st.period_start), _iso(st.period_end)),
        ).fetchone()

    def _upsert_account(self, conn, st: ParsedStatement) -> int:
        conn.execute(
            """INSERT INTO accounts (institution, product, type, last4, holder) VALUES (?,?,?,?,?)
               ON CONFLICT (institution, type, last4) DO UPDATE SET
                 product = CASE WHEN excluded.product <> '' THEN excluded.product ELSE accounts.product END,
                 holder = COALESCE(excluded.holder, accounts.holder)""",
            (st.institution, st.product or "", st.account_type, st.last4, st.holder),
        )
        return conn.execute(
            "SELECT id FROM accounts WHERE institution = ? AND type = ? AND last4 = ?",
            (st.institution, st.account_type, st.last4),
        ).fetchone()["id"]

    def _insert_statement(self, conn, file_id: int, st: ParsedStatement) -> tuple[int, int, int]:
        acct_id = self._upsert_account(conn, st)
        akey = account_key(st.institution, st.account_type, st.last4)
        occurrences: Counter = Counter()
        rows = []
        for t in st.transactions:
            desc = re.sub(r"\s+", " ", t.description or "").strip()
            k = (_iso(t.date), int(t.amount_cents), normalize_description(desc))
            occ = occurrences[k]
            occurrences[k] += 1
            rows.append({
                "fingerprint": fingerprint(akey, t.date, t.amount_cents, desc, occ),
                "date": _iso(t.date),
                "post_date": _iso(t.post_date),
                "description": desc,
                "extra": t.extra,
                "amount_cents": int(t.amount_cents),
                "section": t.section if t.section in SECTIONS else "other",
                "cardholder": t.cardholder,
            })
        checks = [{"label": c.label, "expected_cents": int(c.expected_cents),
                   "parsed_cents": int(c.parsed_cents)} for c in st.checks]
        summary = {k: _jsonable(v) for k, v in (st.summary or {}).items()}
        rec = st.reconciled
        sid = conn.execute(
            """INSERT INTO statements (file_id, account_id, period_start, period_end, parser,
                   reconciled, checks_json, summary_json, txns_json)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (file_id, acct_id, _iso(st.period_start), _iso(st.period_end), st.parser,
             None if rec is None else int(rec), json.dumps(checks), json.dumps(summary),
             json.dumps(rows)),
        ).lastrowid
        new, dup = self._insert_txns(conn, acct_id, sid, rows)
        return sid, new, dup

    @staticmethod
    def _insert_txns(conn, account_id: int, statement_id: int, rows: list[dict]) -> tuple[int, int]:
        new = dup = 0
        for r in rows:
            kind, category = enrich.categorize(r)
            cur = conn.execute(
                """INSERT OR IGNORE INTO transactions (fingerprint, account_id, statement_id, date,
                       post_date, description, extra, merchant, amount_cents, section, kind,
                       category, category_source, cardholder)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'auto',?)""",
                (r["fingerprint"], account_id, statement_id, r["date"], r["post_date"],
                 r["description"], r["extra"], enrich.merchant_name(r["description"]),
                 r["amount_cents"], r["section"], kind, category, r["cardholder"]),
            )
            if cur.rowcount == 1:
                new += 1
            else:
                dup += 1
        return new, dup

    def _restore_missing(self, conn) -> None:
        """Every parsed txn of every live statement must exist once. After a delete (or a
        reparse) re-insert rows that were previously de-duplicated into another statement."""
        for st in conn.execute("SELECT id, account_id, txns_json FROM statements ORDER BY id").fetchall():
            self._insert_txns(conn, st["account_id"], st["id"], json.loads(st["txns_json"] or "[]"))

    @staticmethod
    def _prune_accounts(conn) -> None:
        conn.execute("DELETE FROM transactions WHERE account_id NOT IN (SELECT account_id FROM statements)")
        conn.execute("DELETE FROM accounts WHERE id NOT IN (SELECT account_id FROM statements)")

    # -- derived fields ---------------------------------------------------------------

    def recompute(self, conn) -> None:
        rows = conn.execute(
            "SELECT id, fingerprint, account_id, date, description, amount_cents, section "
            "FROM transactions").fetchall()
        overrides = {r["fingerprint"]: r["category"]
                     for r in conn.execute("SELECT fingerprint, category FROM overrides")}
        rules = {r["merchant"].lower(): r["category"]
                 for r in conn.execute("SELECT merchant, category FROM rules")}
        derived = []
        for r in rows:
            merchant = enrich.merchant_name(r["description"])
            ov = overrides.get(r["fingerprint"])
            rule = rules.get(merchant.lower())
            if enrich.is_category(ov):
                category, source = ov, "user"
            elif enrich.is_category(rule):
                category, source = rule, "rule"
            else:
                _, category = enrich.categorize(dict(r))
                source = "auto"
            derived.append({
                "id": r["id"], "account_id": r["account_id"], "date": r["date"],
                "amount_cents": r["amount_cents"], "merchant": merchant, "category": category,
                "kind": enrich.kind_for_category(category, r["amount_cents"]), "source": source,
            })
        matches = enrich.match_transfers(derived)
        recurring = enrich.detect_recurring(derived)
        conn.executemany(
            """UPDATE transactions SET merchant = ?, kind = ?, category = ?, category_source = ?,
                   transfer_match_id = ?, recurring = ? WHERE id = ?""",
            [(d["merchant"], d["kind"], d["category"], d["source"], matches.get(d["id"]),
              int(d["id"] in recurring), d["id"]) for d in derived],
        )

    def recompute_all(self) -> bool:
        """Re-derive merchant/kind/category/matches/recurring for an existing database (run at
        app startup so rule changes apply without re-uploading). Overrides and merchant rules
        still win. Returns False when there is no database yet."""
        if not self.db_path.exists():
            return False
        with _LOCK, closing(self.connect()) as conn:
            with conn:
                self.recompute(conn)
        return True

    # -- maintenance ------------------------------------------------------------------

    def reparse_all(self) -> dict:
        """Re-run parsers + enrichment over every stored PDF, keeping overrides and rules.
        Files that fail to parse keep their previous statements."""
        with _LOCK, closing(self.connect()) as conn:
            files = conn.execute("SELECT * FROM files ORDER BY id").fetchall()
            parsed, errors = {}, []
            for f in files:
                path = self._stored_path(f["stored_path"])
                if not path.exists():
                    errors.append({"filename": f["filename"], "message": "Stored PDF is missing"})
                    continue
                try:
                    result = list(self._parse(path) or [])
                    if not result:
                        raise UnsupportedStatement("parser returned no statements")
                    parsed[f["id"]] = result
                except Exception as exc:
                    log.exception("reparse failed for %s", f["filename"])
                    errors.append({"filename": f["filename"],
                                   "message": "Unrecognized statement format"
                                   if isinstance(exc, UnsupportedStatement)
                                   else f"{type(exc).__name__}: {exc}"})
            with conn:
                for fid in parsed:
                    conn.execute("DELETE FROM statements WHERE file_id = ?", (fid,))
                for f in files:
                    if f["id"] not in parsed:
                        continue
                    excluded = set(json.loads(f["excluded_json"] or "[]"))
                    for st in parsed[f["id"]]:
                        if statement_key(st) in excluded or self._find_statement(conn, st):
                            continue
                        self._insert_statement(conn, f["id"], st)
                self._restore_missing(conn)
                self._prune_accounts(conn)
                self.recompute(conn)
            n = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
        return {"statements": n, "errors": errors}

    def delete_statement(self, statement_id: int) -> bool:
        to_unlink: Path | None = None
        with _LOCK, closing(self.connect()) as conn:
            st = conn.execute(
                """SELECT s.*, a.institution, a.type, a.last4 FROM statements s
                   JOIN accounts a ON a.id = s.account_id WHERE s.id = ?""",
                (statement_id,)).fetchone()
            if st is None:
                return False
            with conn:
                conn.execute("DELETE FROM transactions WHERE statement_id = ?", (statement_id,))
                conn.execute("DELETE FROM statements WHERE id = ?", (statement_id,))
                f = conn.execute("SELECT * FROM files WHERE id = ?", (st["file_id"],)).fetchone()
                remaining = conn.execute("SELECT COUNT(*) FROM statements WHERE file_id = ?",
                                         (st["file_id"],)).fetchone()[0]
                if f is not None and remaining == 0:
                    conn.execute("DELETE FROM files WHERE id = ?", (f["id"],))
                    to_unlink = self._stored_path(f["stored_path"])
                elif f is not None:
                    excluded = json.loads(f["excluded_json"] or "[]")
                    key = f"{account_key(st['institution'], st['type'], st['last4'])}|" \
                          f"{st['period_start']}|{st['period_end']}"
                    if key not in excluded:
                        excluded.append(key)
                    conn.execute("UPDATE files SET excluded_json = ? WHERE id = ?",
                                 (json.dumps(excluded), f["id"]))
                self._restore_missing(conn)
                self._prune_accounts(conn)
                self.recompute(conn)
        if to_unlink is not None:
            try:
                to_unlink.unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove %s", to_unlink)
        return True

    def set_category(self, txn_id: int, category: str | None,
                     apply_to_merchant: bool = False) -> list[int]:
        """Recategorize a txn (category=None clears the override/rule). Returns changed ids.
        Raises KeyError if the txn does not exist, ValueError for an unknown category."""
        if category is not None and not enrich.is_category(category):
            raise ValueError(f"Unknown category: {category}")
        with _LOCK, closing(self.connect()) as conn:
            row = conn.execute("SELECT id, fingerprint, merchant FROM transactions WHERE id = ?",
                               (txn_id,)).fetchone()
            if row is None:
                raise KeyError(txn_id)
            before = {r["id"]: (r["category"], r["kind"]) for r in
                      conn.execute("SELECT id, category, kind FROM transactions")}
            now = _now()
            with conn:
                if apply_to_merchant:
                    if category is None:
                        conn.execute("DELETE FROM rules WHERE merchant = ?", (row["merchant"],))
                    else:
                        conn.execute(
                            """INSERT INTO rules (merchant, category, created_at) VALUES (?,?,?)
                               ON CONFLICT (merchant) DO UPDATE SET category = excluded.category,
                                 created_at = excluded.created_at""",
                            (row["merchant"], category, now))
                    # the edited txn follows the merchant rule from now on
                    conn.execute("DELETE FROM overrides WHERE fingerprint = ?", (row["fingerprint"],))
                elif category is None:
                    conn.execute("DELETE FROM overrides WHERE fingerprint = ?", (row["fingerprint"],))
                else:
                    conn.execute(
                        """INSERT INTO overrides (fingerprint, category, created_at) VALUES (?,?,?)
                           ON CONFLICT (fingerprint) DO UPDATE SET category = excluded.category,
                             created_at = excluded.created_at""",
                        (row["fingerprint"], category, now))
                self.recompute(conn)
            after = {r["id"]: (r["category"], r["kind"]) for r in
                     conn.execute("SELECT id, category, kind FROM transactions")}
        changed = {i for i, v in after.items() if before.get(i) != v}
        changed.add(txn_id)
        return sorted(changed)

    # -- export -----------------------------------------------------------------------

    def get_data(self) -> dict:
        with closing(self.connect()) as conn:
            accounts = [{
                "id": a["id"], "institution": a["institution"], "product": a["product"],
                "type": a["type"], "last4": a["last4"], "holder": a["holder"],
                "label": account_label(a["institution"], a["product"], a["type"], a["last4"]),
            } for a in conn.execute("SELECT * FROM accounts ORDER BY id")]

            statements = []
            for s in conn.execute(
                    """SELECT s.*, f.filename, f.uploaded_at FROM statements s
                       JOIN files f ON f.id = s.file_id
                       ORDER BY s.period_end DESC, s.id DESC"""):
                checks = json.loads(s["checks_json"] or "[]")
                summary = {}
                for k, v in json.loads(s["summary_json"] or "{}").items():
                    if k.endswith("_cents") and isinstance(v, (int, float)):
                        summary[k[: -len("_cents")]] = _dollars(v)
                    else:
                        summary[k] = v
                statements.append({
                    "id": s["id"], "account_id": s["account_id"], "filename": s["filename"],
                    "period_start": s["period_start"], "period_end": s["period_end"],
                    "uploaded_at": s["uploaded_at"], "parser": s["parser"],
                    "txn_count": len(json.loads(s["txns_json"] or "[]")),
                    "reconciled": None if s["reconciled"] is None else bool(s["reconciled"]),
                    "checks": [{"label": c["label"], "expected": _dollars(c["expected_cents"]),
                                "parsed": _dollars(c["parsed_cents"]),
                                "ok": c["expected_cents"] == c["parsed_cents"]} for c in checks],
                    "summary": summary,
                })

            transactions = [{
                "id": t["id"], "account_id": t["account_id"], "statement_id": t["statement_id"],
                "date": t["date"], "post_date": t["post_date"], "description": t["description"],
                "extra": t["extra"], "merchant": t["merchant"], "amount": _dollars(t["amount_cents"]),
                "section": t["section"], "kind": t["kind"], "category": t["category"],
                "category_source": t["category_source"], "cardholder": t["cardholder"],
                "transfer_match_id": t["transfer_match_id"], "recurring": bool(t["recurring"]),
            } for t in conn.execute("SELECT * FROM transactions ORDER BY date DESC, id DESC")]

            rules = [{"merchant": r["merchant"], "category": r["category"]}
                     for r in conn.execute("SELECT merchant, category FROM rules ORDER BY merchant")]

        return {
            "accounts": accounts,
            "statements": statements,
            "transactions": transactions,
            "categories": [{"name": n, "kind": k} for n, k in enrich.CATEGORIES],
            "rules": rules,
        }


def _combined_reconciled(statements: list[ParsedStatement]) -> bool | None:
    """File-level reconciliation. Statements with neither txns nor checks (e.g. an idle
    savings account bundled in a checking PDF) have nothing to verify and are ignored."""
    relevant = [st.reconciled for st in statements if st.transactions or st.checks]
    if not relevant:
        return None
    if any(r is False for r in relevant):
        return False
    if all(r is True for r in relevant):
        return True
    return None


# --------------------------------------------------------------------------------------
# Module-level convenience API
# --------------------------------------------------------------------------------------

def ingest_file(source: str | Path | bytes, filename: str | None = None, *,
                data_dir: str | Path | None = None, parse: ParseFn | None = None) -> dict:
    return Library(data_dir, parse).ingest_file(source, filename)


def reparse_all(*, data_dir: str | Path | None = None, parse: ParseFn | None = None) -> dict:
    return Library(data_dir, parse).reparse_all()


def delete_statement(statement_id: int, *, data_dir: str | Path | None = None) -> bool:
    return Library(data_dir).delete_statement(statement_id)


def set_category(txn_id: int, category: str | None, apply_to_merchant: bool = False, *,
                 data_dir: str | Path | None = None) -> list[int]:
    return Library(data_dir).set_category(txn_id, category, apply_to_merchant)


def get_data(*, data_dir: str | Path | None = None) -> dict:
    return Library(data_dir).get_data()
