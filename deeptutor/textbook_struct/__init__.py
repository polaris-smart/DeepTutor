"""Deterministic layered chapter rebuild from MinerU ``layout.json``.

Deterministic layered criteria (column blacklist → regex → position →
adjacent merge), zero LLM. K12 fork adds a TOC cross-check layer and a
three-channel fallback chain (footer/header → overview anchoring, English
books only).

Shared asset: consumed by the DT textbook-import path and the self-built
reader alike. Nothing in this package imports deeptutor (asset red line #1).
"""

from .chapter_rebuild import Chapter, rebuild, rebuild_from_headers_level, verify_offset
from .column_blacklist import COLUMN_BLACKLIST
from .page_headers import rebuild_from_headers
from .toc_crosscheck import cross_check_with_toc, extract_toc_entries

__all__ = [
    "COLUMN_BLACKLIST",
    "Chapter",
    "rebuild",
    "rebuild_from_headers",
    "rebuild_from_headers_level",
    "verify_offset",
    "cross_check_with_toc",
    "extract_toc_entries",
]
