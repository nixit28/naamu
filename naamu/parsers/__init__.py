"""PDF statement parsers: ``parse_pdf(path) -> list[ParsedStatement]``.

Deterministic, offline. The issuer is detected from the text content (never the
filename); unknown layouts fall back to a heuristic generic parser whose output
carries no checks (shown as unverified).
"""
from __future__ import annotations

import logging
from pathlib import Path

from naamu.models import ParsedStatement, UnsupportedStatement

from . import amex, chase_bank, chase_card, citi, generic
from .common import extract_pages

# Most specific signatures first. Statements mention other banks in descriptions
# ("American Express ACH Pmt" on Chase checking), so detectors key on layout
# markers (URLs, summary labels), not bare names.
PARSERS = (amex, citi, chase_card, chase_bank)

__all__ = ["parse_pdf", "parse_pages", "UnsupportedStatement"]

log = logging.getLogger(__name__)


def parse_pages(pages: list[str]) -> list[ParsedStatement]:
    """Parse already-extracted page texts (one string per page)."""
    text = "\n".join(pages)
    error: Exception | None = None
    for mod in PARSERS:
        if mod.detect(text):
            try:
                return mod.parse(pages)
            except UnsupportedStatement:
                raise
            except Exception as exc:  # layout drifted: try the generic parser
                log.warning("%s parser failed (%s); falling back to generic", mod.PARSER_ID, exc)
                error = exc
                break
    try:
        return generic.parse(pages)
    except UnsupportedStatement as exc:
        if error is not None:
            raise UnsupportedStatement(f"{error} (generic fallback: {exc})") from error
        raise


def parse_pdf(path: str | Path) -> list[ParsedStatement]:
    """Parse a statement PDF into one ParsedStatement per account it covers.

    Raises UnsupportedStatement when no parser recognizes it.
    """
    return parse_pages(extract_pages(path))
