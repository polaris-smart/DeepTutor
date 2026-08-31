"""Textbook canonicalization orchestrator — a thin loop over the DT API.

This script owns **no** parsing, splitting, or structure logic. Everything it
needs already runs natively inside DeepTutor:

    upload → MinerU parse (auto-splits >200-page PDFs)
           → doc_intel enrich (页脚法 gate + printed_page + struct_path)
           → KB index (embedding)
           → POST /book/books/canonicalize  (book + spine + verbatim pages)

The orchestrator's whole job is: push each file in, poll until the native
pipeline is done, read the structure back out, hand it to canonicalize, and
write an acceptance report. When a book fails, it says so and moves to the
next one — a shelf of textbooks must not be held hostage by one bad PDF.

Usage::

    python scripts/canonicalize_textbooks.py --kb 数学 ~/books/*.pdf
    python scripts/canonicalize_textbooks.py --kb 数学 --dry-run ~/books/a.pdf
    python scripts/canonicalize_textbooks.py --kb 数学 --report out.json ~/books/

Exit code is 0 only when every book canonicalized.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any


def banner(title: str, lines: list[str] | tuple[str, ...] = ()) -> None:
    print(f"== {title} ==")
    for line in lines:
        print(str(line))


def log_success(message: str) -> None:
    print(f"[ok] {message}")


def log_error(message: str) -> None:
    print(f"[error] {message}")


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
#: How long to wait for the native pipeline (parse + index) on one document.
DEFAULT_INDEX_TIMEOUT = 3600.0
#: Gap between progress polls. The pipeline reports in coarse stages, so a
#: tight loop only burns requests.
POLL_INTERVAL = 10.0
#: Transient-failure retries per API call (network hiccup, worker restart).
MAX_RETRIES = 3
RETRY_BACKOFF = 5.0

PDF_SUFFIXES = {".pdf", ".epub", ".docx", ".md", ".txt"}


class OrchestratorError(RuntimeError):
    """A step failed in a way that ends this book (not the whole run)."""


# ─────────────────────────────────────────────────────────────────────────────
# HTTP plumbing
# ─────────────────────────────────────────────────────────────────────────────


class ApiClient:
    """Minimal DT API client with retry on transient failures."""

    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        import httpx

        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._httpx = httpx

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Call the API, retrying transient failures, and return parsed JSON.

        A 4xx is the caller's fault and is never retried — retrying a
        rejected payload just multiplies the same error.
        """
        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except self._httpx.HTTPError as exc:
                last_error = exc
            else:
                if response.status_code < 400:
                    return response.json() if response.content else {}
                if response.status_code < 500:
                    raise OrchestratorError(
                        f"{method} {url} → {response.status_code}: {response.text[:300]}"
                    )
                last_error = OrchestratorError(
                    f"{method} {url} → {response.status_code}: {response.text[:300]}"
                )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
        raise OrchestratorError(f"{method} {url} failed after {MAX_RETRIES} tries: {last_error}")

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self.request("POST", url, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline steps — each one is a single API call plus its wait condition
# ─────────────────────────────────────────────────────────────────────────────


def upload_document(api: ApiClient, kb: str, path: Path) -> None:
    """Push one file into the KB; the native pipeline takes over from here."""
    with path.open("rb") as handle:
        api.post(
            f"/api/v1/knowledge/{kb}/upload",
            files={"files": (path.name, handle, "application/octet-stream")},
        )


def wait_for_index(api: ApiClient, kb: str, *, timeout: float) -> dict[str, Any]:
    """Block until the KB reports it is no longer processing.

    Returns the final progress payload. Raises on timeout so a stuck parse
    surfaces in the report instead of silently producing an empty book.
    """
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = api.get(f"/api/v1/knowledge/{kb}/progress") or {}
        stage = str(last.get("stage") or last.get("status") or "").lower()
        if last.get("error"):
            raise OrchestratorError(f"indexing failed: {last.get('error')}")
        if stage in ("", "idle", "done", "completed", "ready", "finished"):
            return last
        time.sleep(POLL_INTERVAL)
    raise OrchestratorError(f"indexing did not finish within {timeout:.0f}s (last={last})")


def read_textbook_tree(api: ApiClient, kb: str, file_name: str) -> dict[str, Any]:
    """Return the doc_intel structure tree DT built for ``file_name``."""
    payload = api.get(f"/api/v1/knowledge/{kb}/textbook-tree") or {}
    for entry in payload.get("textbooks") or []:
        if str(entry.get("file_name") or "") == file_name:
            return entry
    raise OrchestratorError(
        f"no doc_intel tree for {file_name} — the parse produced no structure "
        "(scanned PDF without OCR, or 页脚法 found no running headers)"
    )


def tree_to_toc(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a doc_intel tree into the canonicalize endpoint's TOC shape."""

    def convert(node: dict[str, Any]) -> dict[str, Any] | None:
        title = str(node.get("title") or "").strip()
        if not title:
            return None
        item: dict[str, Any] = {"title": title}
        children = [
            converted
            for child in node.get("children") or []
            if isinstance(child, dict) and (converted := convert(child)) is not None
        ]
        if children:
            item["children"] = children
        return item

    roots = [
        converted
        for child in tree.get("children") or []
        if isinstance(child, dict) and (converted := convert(child)) is not None
    ]
    return roots


def fetch_chapter_pages(
    api: ApiClient, kb: str, toc: list[dict[str, Any]], *, limit_per_chapter: int
) -> list[dict[str, Any]]:
    """Read each top-level chapter's verbatim prose as importable page specs.

    Verbatim only: every block is a ``reading`` block carrying the textbook's
    own text. Nothing here invents or rewrites content.
    """
    groups: list[dict[str, Any]] = []
    for index, chapter in enumerate(toc):
        title = str(chapter.get("title") or "")
        payload = api.get(
            f"/api/v1/knowledge/{kb}/docs/by-struct",
            params={"path": title, "limit": limit_per_chapter, "full_text": "true"},
        ) or {}
        blocks = [
            {
                "block_type": "reading",
                "params": {
                    "body": text,
                    "variant": "prose",
                    "source_label": str(node.get("struct_path") or title),
                },
            }
            for node in payload.get("nodes") or []
            if (text := str(node.get("text") or "").strip())
        ]
        if not blocks:
            continue
        groups.append(
            {"chapter_index": index, "pages": [{"title": title, "blocks": blocks}]}
        )
    return groups


def canonicalize(
    api: ApiClient,
    *,
    title: str,
    toc: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    kb: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """One call: book + spine + verbatim pages, all deterministic."""
    return api.post(
        "/api/v1/book/books/canonicalize",
        json={
            "title": title,
            "toc": toc,
            "source": "toc_json",
            "language": "zh",
            "knowledge_bases": [kb],
            "metadata": metadata,
            "chapters": chapters,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Per-book driver
# ─────────────────────────────────────────────────────────────────────────────


def process_book(
    api: ApiClient,
    kb: str,
    path: Path,
    *,
    index_timeout: float,
    blocks_per_chapter: int,
    dry_run: bool,
) -> dict[str, Any]:
    """Run one textbook end to end and return its report row."""
    started = time.monotonic()
    report: dict[str, Any] = {"file": str(path), "title": path.stem, "ok": False}
    try:
        if not dry_run:
            upload_document(api, kb, path)
            wait_for_index(api, kb, timeout=index_timeout)

        entry = read_textbook_tree(api, kb, path.name)
        toc = tree_to_toc(entry.get("tree") or {})
        if not toc:
            raise OrchestratorError("doc_intel tree has no usable chapter titles")
        report["chapters_detected"] = len(toc)
        report["subject"] = entry.get("subject") or ""

        chapters = fetch_chapter_pages(api, kb, toc, limit_per_chapter=blocks_per_chapter)
        report["chapters_with_content"] = len(chapters)

        if dry_run:
            report["ok"] = True
            report["dry_run"] = True
            return report

        result = canonicalize(
            api,
            title=path.stem,
            toc=toc,
            chapters=chapters,
            kb=kb,
            metadata={"source_file": path.name},
        )
        report["book_id"] = result.get("book", {}).get("id", "")
        report["pages_created"] = result.get("pages_created", 0)
        report["book_status"] = result.get("book", {}).get("status", "")
        # A book with a spine but no pages is a failed import, not a success:
        # it would sit in the library advertising content it does not have.
        if not report["pages_created"]:
            raise OrchestratorError("canonicalize produced 0 pages (no verbatim prose imported)")
        report["ok"] = True
    except OrchestratorError as exc:
        report["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - one bad book must not end the run
        report["error"] = f"unexpected: {exc}"
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return report


def collect_inputs(paths: list[str]) -> list[Path]:
    """Expand directories into the documents inside them, sorted."""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            found.extend(
                child
                for child in sorted(path.rglob("*"))
                if child.is_file() and child.suffix.lower() in PDF_SUFFIXES
            )
        elif path.is_file():
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="textbook files or directories")
    parser.add_argument("--kb", required=True, help="target knowledge base name")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--report", help="write the acceptance report here (JSON)")
    parser.add_argument("--index-timeout", type=float, default=DEFAULT_INDEX_TIMEOUT)
    parser.add_argument(
        "--blocks-per-chapter",
        type=int,
        default=100,
        help="max verbatim nodes imported per chapter (API caps at 100)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="skip upload and canonicalize; only read back what DT already has",
    )
    args = parser.parse_args(argv)

    books = collect_inputs(args.paths)
    if not books:
        log_error("no input documents found")
        return 2

    banner(
        "Textbook canonicalization",
        [f"kb={args.kb}", f"books={len(books)}", f"dry_run={args.dry_run}"],
    )

    rows: list[dict[str, Any]] = []
    with ApiClient(args.base_url) as api:
        for position, path in enumerate(books, start=1):
            print(f"[{position}/{len(books)}] {path.name}")
            row = process_book(
                api,
                args.kb,
                path,
                index_timeout=args.index_timeout,
                blocks_per_chapter=args.blocks_per_chapter,
                dry_run=args.dry_run,
            )
            rows.append(row)
            if row["ok"]:
                log_success(
                    f"{path.name}: {row.get('chapters_detected', 0)} chapters, "
                    f"{row.get('pages_created', 0)} pages ({row['elapsed_seconds']}s)"
                )
            else:
                log_error(f"{path.name}: {row.get('error', 'unknown failure')}")

    succeeded = [r for r in rows if r["ok"]]
    summary = {
        "kb": args.kb,
        "total": len(rows),
        "succeeded": len(succeeded),
        "failed": len(rows) - len(succeeded),
        "pages_created": sum(r.get("pages_created", 0) for r in rows),
        "books": rows,
    }
    banner(
        "Acceptance",
        [
            f"succeeded: {summary['succeeded']}/{summary['total']}",
            f"pages created: {summary['pages_created']}",
        ],
    )
    for row in rows:
        if not row["ok"]:
            print(f"  FAILED {Path(row['file']).name}: {row.get('error', '')}")

    if args.report:
        report_path = Path(args.report).expanduser()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log_success(f"report written to {report_path}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
