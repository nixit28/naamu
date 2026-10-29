"""Flask app: serves the dashboard (static/) and the JSON API from docs/CONTRACT.md.

Run with ``python -m namu.app``; it binds to 127.0.0.1 only (never 0.0.0.0).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from .db import ROOT
from .ingest import Library, ParseFn

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
PORT = 8765
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}

_PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>Namu</title>
<body style="font-family:system-ui;margin:3rem;background:#fff;color:#111">
<h1>Namu</h1><p>The dashboard (static/index.html) has not been built yet.
The API is available at <code>/api/data</code>.</p></body>"""


def _hostname(netloc: str) -> str:
    if not netloc:
        return ""
    return (urlsplit(f"//{netloc}").hostname or "").lower()


def create_app(data_dir: str | Path | None = None, parse: ParseFn | None = None,
               static_dir: str | Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.json.sort_keys = False
    lib = Library(data_dir, parse)
    app.extensions["namu"] = lib
    try:
        lib.recompute_all()          # apply current built-in rules to an existing DB
    except Exception:
        log.exception("startup recompute failed; serving stored values")
    static_root = Path(static_dir) if static_dir else ROOT / "static"

    # -- local-only guard: blocks DNS-rebinding and cross-site requests to the API -------
    @app.before_request
    def _guard():
        if not request.path.startswith("/api/"):
            return None
        if _hostname(request.host) not in _LOCAL_HOSTS:
            return jsonify(error="Forbidden host"), 403
        origin = request.headers.get("Origin")
        if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
            if _hostname(urlsplit(origin).netloc) not in _LOCAL_HOSTS:
                return jsonify(error="Cross-origin request refused"), 403
        return None

    # -- static dashboard ---------------------------------------------------------------
    @app.get("/")
    def index():
        if (static_root / "index.html").is_file():
            resp = send_from_directory(static_root, "index.html")
            resp.headers["Cache-Control"] = "no-cache"
            return resp
        return _PLACEHOLDER, 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.get("/<path:filename>")
    def static_file(filename: str):
        if filename.startswith("api/"):
            abort(404)
        resp = send_from_directory(static_root, filename)
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    # -- API ----------------------------------------------------------------------------
    @app.get("/api/data")
    def api_data():
        return jsonify(lib.get_data())

    @app.post("/api/upload")
    def api_upload():
        files = request.files.getlist("files")
        if not files:
            return jsonify(error="No files uploaded (expected multipart field 'files')"), 400
        results = []
        for f in files:
            name = Path(f.filename or "statement.pdf").name or "statement.pdf"
            try:
                results.append(lib.ingest_file(f.read(), name))
            except Exception as exc:  # never let one bad file sink the batch
                log.exception("upload failed for %s", name)
                results.append({"filename": name, "status": "error",
                                "message": f"Import failed ({type(exc).__name__})"})
        return jsonify(results=results)

    @app.patch("/api/transactions/<int:txn_id>")
    def api_patch_txn(txn_id: int):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "category" not in body:
            return jsonify(error="Expected JSON body with 'category'"), 400
        category = body.get("category")
        if category is not None and not isinstance(category, str):
            return jsonify(error="'category' must be a string or null"), 400
        try:
            updated = lib.set_category(txn_id, category or None,
                                       bool(body.get("apply_to_merchant", False)))
        except KeyError:
            return jsonify(error="Transaction not found"), 404
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        return jsonify(updated=updated)

    @app.delete("/api/statements/<int:statement_id>")
    def api_delete_statement(statement_id: int):
        if not lib.delete_statement(statement_id):
            return jsonify(error="Statement not found"), 404
        return jsonify(deleted=True)

    @app.post("/api/reparse")
    def api_reparse():
        return jsonify(lib.reparse_all())

    # -- JSON errors --------------------------------------------------------------------
    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        if exc.code == 413:
            return jsonify(error="Upload too large (limit is 50 MB)"), 413
        if request.path.startswith("/api/"):
            return jsonify(error=exc.description or exc.name), exc.code
        return exc

    @app.errorhandler(Exception)
    def _internal_error(exc: Exception):
        log.exception("unhandled error on %s", request.path)
        return jsonify(error="Internal server error"), 500

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("NAMU_PORT", PORT))
    app = create_app()
    print(f"Namu is running at http://{HOST}:{port}  (Ctrl+C to stop)", flush=True)
    app.run(host=HOST, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
