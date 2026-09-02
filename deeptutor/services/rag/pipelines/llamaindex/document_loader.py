"""Document loading for the LlamaIndex RAG pipeline.

Parser-backed files (PDF / Office / e-book) are converted through the shared
document-parse bridge (``deeptutor/services/parsing``), so the engine the user
picked in Settings → Document Parsing (text-only, MinerU, Docling, markitdown,
PyMuPDF4LLM) owns extraction. This is the same seam LightRAG and GraphRAG use;
routing LlamaIndex through it too means the parse-engine choice is honored by
every local retrieval engine, and image-capable engines' extracted images flow
into the multimodal ``ImageNode`` path below.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import logging
import mimetypes
from pathlib import Path
import re
from typing import Any, Callable, Iterable

from llama_index.core import Document
from llama_index.core.schema import ImageNode

from deeptutor.services.embedding import get_embedding_client
from deeptutor.services.llm.client import get_llm_client
from deeptutor.services.rag.file_routing import FileTypeRouter
from deeptutor.utils.document_validator import DocumentValidator

#: Serialized-size budget (chars) for the doc-level di_block_meta payload.
#: LlamaIndex rejects a document whose metadata exceeds the chunk size, so
#: this must stay comfortably below the 1000-char chunk budget.
_DI_BLOCK_META_BUDGET = 600

#: Total serialized-size budget for a Document's metadata. LlamaIndex rejects
#: documents whose metadata exceeds the chunk size (1000), and metadata is
#: assembled from several sources (file identity, doc_intel classification,
#: tree, block bridge data) — enforce a hard cap here as the last line of
#: defence, shedding optional fields largest-first while keeping the file
#: identity keys intact.
_METADATA_TOTAL_BUDGET = 900


def _enforce_metadata_budget(metadata: dict) -> dict:
    """Shed optional metadata fields until the serialized size fits the budget."""
    import json as _json

    def _size(m: dict) -> int:
        return len(_json.dumps(m, ensure_ascii=False, default=str))

    if _size(metadata) <= _METADATA_TOTAL_BUDGET:
        return metadata
    slim = dict(metadata)
    # Shed largest-first among the known optional blobs.
    for shed_key in ("di_block_meta", "doc_tree", "doc_intel_classification"):
        if shed_key in slim and _size(slim) <= _METADATA_TOTAL_BUDGET:
            break
        slim.pop(shed_key, None)
    # Still over — truncate every non-identity value to a short head.
    if _size(slim) > _METADATA_TOTAL_BUDGET:
        identity = {
            k: slim[k]
            for k in ("file_name", "file_path")
            if k in slim
        }
        rest = {k: str(v)[:160] for k, v in slim.items() if k not in identity}
        slim = {**rest, **identity}
        while _size(slim) > _METADATA_TOTAL_BUDGET and len(rest) > 1:
            rest.pop(next(iter(rest)))
            slim = {**rest, **identity}
    return slim

from .config import image_description_limits

IMAGE_DESCRIPTION_SYSTEM_PROMPT = (
    "You describe images for a retrieval-augmented knowledge base. "
    "Be factual, concise, and include any visible text, labels, diagrams, "
    "tables, logos, or important visual relationships. Do not invent details."
)

IMAGE_DESCRIPTION_PROMPT = (
    "Describe this image so that a text-only answer generator can understand "
    "and cite it later. Include visible text/OCR if present, the main subject, "
    "and any educational or technical meaning. Keep the answer under 180 words."
)

# Markdown local-image inlining (upstream PR #525 + ZC hardening).
# Resolve `![alt](relative/path.png)` to data URIs so images that arrive in a
# zip upload survive indexing without broken links. Security rails — a
# markdown file must never become an arbitrary-file-read primitive:
#   1. absolute paths are rejected outright;
#   2. the resolved path must stay inside the markdown file's own directory
#      (traversal like `../../user/settings/model_catalog.json` is dropped);
#   3. only image extensions are inlined (defense in depth).
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_MAX_EMBED_IMAGE_SIZE = 500 * 1024  # 500 KB
_MD_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Volcano Ark embedding `input` strings cap at 100000 bytes; keep the base64
# image data comfortably below that (headroom for the data-URI prefix).
_MAX_IMAGE_DATA_URI_BYTES = 90_000


@dataclass(frozen=True)
class _ImageSource:
    """An image to embed as an ``ImageNode``, plus the document it came from.

    ``path`` is the image file on disk (what gets embedded and served).
    ``origin`` is the document it belongs to: the image itself for a standalone
    image file, or the source PDF/e-book for an image extracted during parsing —
    so retrieval cites the source document rather than an opaque cache asset.
    """

    path: Path
    origin: Path


class LlamaIndexDocumentLoader:
    """Convert source files into LlamaIndex ``Document`` / ``ImageNode`` objects."""

    def __init__(self, logger=None, image_concurrency: int = 6) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self.image_concurrency = max(1, int(image_concurrency))
        # 悦学 doc_intel: structured blocks of the most recent parsed/backfilled
        # document, consumed by _append_if_nonempty's enrich pass. Reset per
        # file so a cache miss never inherits a previous file's blocks.
        self._last_parsed_blocks = None

    async def load(
        self,
        file_paths: Iterable[str],
        image_progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[Any]:
        documents: list[Any] = []
        image_sources: list[_ImageSource] = []
        classification = FileTypeRouter.classify_files(list(file_paths))

        for file_path_str in classification.parser_files:
            file_path = Path(file_path_str)
            self.logger.info(f"Parsing document: {file_path.name}")
            # MinerU cloud parsing blocks end to end (upload + 300s polling +
            # archive download) on a synchronous httpx.Client — running it on
            # the event loop stalls every other request for the whole PDF
            # (same class of bug as upstream #761/#777). Hand it to a thread.
            text, extracted_images, parse_engine = await asyncio.to_thread(
                self._parse_document, file_path
            )
            self._append_if_nonempty(
                documents,
                file_path,
                text,
                parse_engine=parse_engine,
                extracted_image_count=len(extracted_images),
            )
            image_sources.extend(extracted_images)

        for file_path_str in classification.text_files:
            file_path = Path(file_path_str)
            self.logger.info(f"Parsing text: {file_path.name}")
            text = await FileTypeRouter.read_text_file(str(file_path))
            # 悦学 doc_intel: md files are direct-read so they never set
            # _last_parsed_blocks, and enrich stays skipped. The structured
            # sibling (content_list) may already sit in the parse cache under
            # the same source hash (MinerU products converted to md) — reuse it
            # so enrich has blocks to classify / build the doc tree. Fail-open:
            # a miss indexes plain text with clean metadata.
            self._last_parsed_blocks = self._lookup_cached_blocks(file_path)
            self._append_if_nonempty(documents, file_path, text)

        for file_path_str in classification.image_files:
            path = Path(file_path_str)
            from deeptutor.services.parsing import get_parse_service

            parse_service = get_parse_service()
            supports = getattr(parse_service, "supports", lambda _path: False)
            if supports(path):
                self.logger.info(f"Parsing image with active document parser: {path.name}")
                text, extracted_images, parse_engine = await asyncio.to_thread(
                    self._parse_document, path, parse_service
                )
                if text.strip() or extracted_images:
                    self._append_if_nonempty(
                        documents,
                        path,
                        text,
                        parse_engine=parse_engine,
                        extracted_image_count=len(extracted_images),
                    )
                    image_sources.extend(extracted_images)
                else:
                    # Preserve the pre-parser behavior when an image-capable
                    # engine fails or yields no usable IR.
                    image_sources.append(_ImageSource(path=path, origin=path))
            else:
                image_sources.append(_ImageSource(path=path, origin=path))

        if image_sources:
            documents.extend(
                await self._load_image_nodes(
                    image_sources, image_progress_callback=image_progress_callback
                )
            )

        for file_path_str in classification.unsupported:
            self.logger.warning(f"Skipped unsupported file: {Path(file_path_str).name}")

        return documents

    def _parse_document(
        self,
        file_path: Path,
        parse_service=None,  # noqa: ANN001
    ) -> tuple[str, list[_ImageSource], str]:
        """Parse a document through the shared, engine-pluggable parse layer.

        Returns ``(text, extracted_images, engine)``. A parse failure (engine
        unavailable, unsupported format for the active engine, or models not
        ready) is logged and the file is skipped — matching the sibling
        LightRAG/GraphRAG pipelines — rather than aborting the whole batch.
        """
        from deeptutor.services.parsing import ParserError, get_parse_service

        try:
            parsed = (parse_service or get_parse_service()).parse(file_path)
        except ParserError as exc:
            self.logger.warning(
                f"Skipped {file_path.name}: the active document-parsing engine could "
                f"not handle it ({exc}). Change the engine in Settings → Document Parsing."
            )
            return "", [], ""

        text = parsed.markdown.strip() or self._text_from_blocks(parsed.blocks)
        # 悦学 doc_intel: keep the structured blocks for _append_if_nonempty's
        # enrich pass (content_list carries heading levels / bboxes / page_idx).
        self._last_parsed_blocks = parsed.blocks
        images = self._collect_asset_images(parsed.asset_dir, origin=file_path)
        return text, images, str(parsed.engine or "")

    def _lookup_cached_blocks(self, file_path: Path) -> list[dict] | None:
        """Return content_list blocks cached for a text file, or ``None``.

        The parse cache is keyed by source bytes
        (``parse_cache/<hash[:2]>/<source_hash>/<signature>/``). md files in
        ``raw/`` are MinerU products converted to text, so their bytes hash to
        the entry created when the original document was cloud-parsed; every
        signature dir under that key is probed (any content_list works — we
        only want structure, never a re-parse). Fail-open by contract: any
        failure (missing cache, unreadable entry, corrupt JSON) returns
        ``None`` and the file is indexed as plain text.
        """
        from deeptutor.services import path_service
        from deeptutor.services.parsing import cache as parse_cache

        try:
            cache_root = path_service.get_path_service().get_parse_cache_root()
            source_hash = parse_cache.source_hash_from_path(file_path)
            source_dir = cache_root / source_hash[:2] / source_hash
            if not source_dir.is_dir():
                # Byte-hash miss is expected for MinerU products converted to
                # md (cache entries are keyed by the original PDF's bytes).
                # Fall back to a manifest source-name match so structure
                # survives the pdf→md conversion hop.
                source_dir = self._cache_dir_by_source_name(cache_root, file_path.stem)
            if source_dir is None:
                return None
            for sig_dir in sorted(source_dir.iterdir()):
                if not sig_dir.is_dir() or not parse_cache.is_ready(sig_dir):
                    continue
                _, blocks, _ = parse_cache.load_ir(sig_dir)
                if blocks:
                    self.logger.info(
                        "Loaded cached content_list for %s (doc_intel backfill)",
                        file_path.name,
                    )
                    return blocks
        except Exception as exc:  # noqa: BLE001 - fail-open lookup
            self.logger.debug(
                "parse_cache content_list lookup failed for %s: %s",
                file_path.name,
                exc,
            )
        return None

    @staticmethod
    def _cache_dir_by_source_name(cache_root: Path, stem: str) -> Path | None:
        """Find a parse-cache source dir whose manifest name matches ``stem``.

        Compares normalized forms (whitespace/extension stripped) because raw
        md stems like ``学程三  指导手册`` must match cached sources recorded
        as ``学程三  指导手册.pdf``. Returns the source dir of the first ready
        match, or ``None``. Scan is fail-open and best-effort.
        """
        import json as _json

        def _norm(s: str) -> str:
            return "".join(ch for ch in s if not ch.isspace())

        want = _norm(stem)
        if not want:
            return None
        try:
            for prefix_dir in sorted(cache_root.iterdir()):
                if not prefix_dir.is_dir():
                    continue
                for source_dir in sorted(prefix_dir.iterdir()):
                    if not source_dir.is_dir():
                        continue
                    for sig_dir in sorted(source_dir.iterdir()):
                        manifest = sig_dir / "manifest.json"
                        if not manifest.is_file():
                            continue
                        try:
                            record = _json.loads(manifest.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        cached_name = str(
                            record.get("source_name")
                            or record.get("original_name")
                            or ""
                        )
                        if cached_name and _norm(cached_name).startswith(want):
                            return source_dir
        except Exception:
            return None
        return None

    @staticmethod
    def _text_from_blocks(blocks: list[dict] | None) -> str:
        """Fall back to concatenating block text when an engine emits no markdown."""
        if not blocks:
            return ""
        parts = [
            str(block.get("text") or block.get("content") or "").strip()
            for block in blocks
            if isinstance(block, dict)
        ]
        return "\n\n".join(part for part in parts if part)

    def _collect_asset_images(self, asset_dir: Path | None, *, origin: Path) -> list[_ImageSource]:
        """Gather images the parse engine extracted into ``asset_dir``.

        Engines that don't extract images (text-only, markitdown) leave
        ``asset_dir`` empty, so this returns nothing and the document is indexed
        as text alone.
        """
        if not asset_dir or not Path(asset_dir).is_dir():
            return []
        images = [
            _ImageSource(path=child, origin=origin)
            for child in sorted(Path(asset_dir).iterdir())
            if child.is_file() and child.suffix.lower() in FileTypeRouter.IMAGE_EXTENSIONS
        ]
        if images:
            self.logger.info(
                f"Extracted {len(images)} image(s) from {origin.name} for multimodal indexing"
            )
        return images

    async def _load_image_nodes(
        self,
        sources: list[_ImageSource],
        *,
        image_progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[ImageNode]:
        try:
            embedding_client = get_embedding_client()
        except Exception as exc:
            self._log_skipped_images(sources, f"embedding client is unavailable ({exc})")
            return []
        if not embedding_client.supports_multimodal_contents():
            self._log_skipped_images(
                sources,
                "embedding provider/model does not support multimodal contents "
                f"(binding={embedding_client.config.binding}, "
                f"model={embedding_client.config.model})",
            )
            return []

        # Resolve the LLM only after the embedding prerequisite passes. This
        # keeps text-only embedding setups independent of LLM configuration and
        # reuses one client for the whole image batch.
        try:
            llm_client = get_llm_client()
        except Exception as exc:
            self._log_skipped_images(sources, f"LLM client is unavailable ({exc})")
            return []
        if not llm_client.supports_multimodal_images():
            self._log_skipped_images(
                sources,
                "LLM provider/model does not support multimodal image input "
                f"(binding={llm_client.config.binding}, model={llm_client.config.model})",
            )
            return []

        embedded: list[_ImageSource] = []
        descriptions: list[str] = []
        contents: list[dict[str, str]] = []
        completed = 0
        total = len(sources)
        concurrency, timeout_seconds = image_description_limits()
        semaphore = asyncio.Semaphore(concurrency)

        async def _describe_one(
            source: _ImageSource,
        ) -> tuple[_ImageSource, str, dict[str, str]] | None:
            nonlocal completed
            result: tuple[_ImageSource, str, dict[str, str]] | None = None
            try:
                try:
                    async with semaphore:
                        image_payload = self._load_image_payload(source.path)
                        description = await asyncio.wait_for(
                            self._describe_image(
                                llm_client,
                                source.path,
                                image_payload["base64"],
                                image_payload["mimetype"],
                            ),
                            timeout=timeout_seconds,
                        )
                except asyncio.TimeoutError:
                    self.logger.error(
                        "Image description timed out after %ss: %s",
                        timeout_seconds,
                        source.path.name,
                    )
                except OSError as exc:
                    self.logger.error(f"Failed to read image {source.path.name}: {exc}")
                except Exception as exc:
                    self.logger.error(
                        "Failed to describe image %s with configured multimodal LLM "
                        "(binding=%s, model=%s): %s",
                        source.path.name,
                        llm_client.config.binding,
                        llm_client.config.model,
                        exc,
                    )
                else:
                    if not description:
                        self.logger.warning(
                            "Skipped image because the configured multimodal LLM "
                            f"returned no description: {source.path.name}"
                        )
                    else:
                        result = (
                            source,
                            description,
                            {"image": image_payload["data_uri"]},
                        )
            finally:
                completed += 1
                if image_progress_callback:
                    try:
                        image_progress_callback(completed, total)
                    except Exception:
                        pass
            return result

        # gather preserves input order, so embedded/descriptions/contents stay
        # aligned regardless of completion order.
        results = await asyncio.gather(*(_describe_one(source) for source in sources))
        for result in results:
            if result is None:
                continue
            embedded.append(result[0])
            descriptions.append(result[1])
            contents.append(result[2])

        described = [r for r in results if r is not None]
        description_failures = [
            source.path.name
            for source, result in zip(sources, results)
            if result is None
        ]
        if description_failures:
            self.logger.warning("Image description failures: %s", ", ".join(description_failures))
        if not described:
            return []

        embedding_semaphore = asyncio.Semaphore(self.image_concurrency)

        async def embed(
            item: tuple[_ImageSource, str, str],
        ) -> tuple[_ImageSource, str, list[float]] | None:
            source, description, content = item
            async with embedding_semaphore:
                try:
                    vectors = await embedding_client.embed_contents([content])
                    return (source, description, vectors[0]) if vectors else None
                except Exception as exc:
                    self.logger.error("Failed to embed image %s: %s", source.path.name, exc)
                    return None

        embedding_results = await asyncio.gather(*(embed(item) for item in described))
        embedded = [result for result in embedding_results if result is not None]
        embedding_failures = [
            item[0].path.name
            for item, result in zip(described, embedding_results)
            if result is None
        ]
        if embedding_failures:
            self.logger.warning("Image embedding failures: %s", ", ".join(embedding_failures))

        nodes: list[ImageNode] = []
        for source, description, embedding in embedded:
            mimetype = mimetypes.guess_type(source.path.name)[0] or "application/octet-stream"
            nodes.append(
                ImageNode(
                    text=f"[Image] {source.origin.name}\n\n{description}",
                    image_path=str(source.path),
                    image_mimetype=mimetype,
                    metadata={
                        "file_name": source.origin.name,
                        "file_path": str(source.origin),
                        "content_type": "image",
                        "image_description": description,
                    },
                    embedding=embedding,
                )
            )
            self.logger.info(f"Loaded image: {source.path.name} ({len(embedding)}D vector)")
        return nodes

    def _log_skipped_images(self, sources: list[_ImageSource], reason: str) -> None:
        for source in sources:
            self.logger.warning(
                "Skipped image because image indexing requires both multimodal "
                f"embedding and multimodal LLM support; {reason}: {source.path.name}"
            )

    async def _describe_image(
        self, llm_client: Any, file_path: Path, image_base64: str, mimetype: str
    ) -> str:
        response = await llm_client.complete(
            IMAGE_DESCRIPTION_PROMPT,
            system_prompt=IMAGE_DESCRIPTION_SYSTEM_PROMPT,
            image_data=image_base64,
            image_mime_type=mimetype,
            image_filename=file_path.name,
        )
        return response.strip()

    def _load_image_payload(self, file_path: Path) -> dict[str, str]:
        size = file_path.stat().st_size
        if size > DocumentValidator.MAX_FILE_SIZE:
            raise OSError(
                f"image file too large: {size} bytes; "
                f"maximum allowed: {DocumentValidator.MAX_FILE_SIZE} bytes"
            )
        mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        payload = file_path.read_bytes()
        # Volcano Ark's embedding endpoint rejects `input` strings over
        # 100000 bytes; big textbook images exceed that as base64. Downscale
        # (and re-encode as JPEG when lossy-friendly) to stay under the cap.
        encoded = base64.b64encode(payload).decode("ascii")
        if len(encoded) > _MAX_IMAGE_DATA_URI_BYTES:
            for max_edge in (1024, 768, 512, 256):
                resized = self._downscale_image_bytes(payload, max_edge=max_edge)
                encoded = base64.b64encode(resized).decode("ascii")
                if len(encoded) <= _MAX_IMAGE_DATA_URI_BYTES:
                    break
            mimetype = "image/jpeg"
        return {
            "base64": encoded,
            "data_uri": f"data:{mimetype};base64,{encoded}",
            "mimetype": mimetype,
        }

    @staticmethod
    def _downscale_image_bytes(data: bytes, max_edge: int = 1024, quality: int = 85) -> bytes:
        """Resize an image so its base64 form fits Ark's input byte cap."""
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.thumbnail((max_edge, max_edge))
            out = BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()

    def _resolve_markdown_images(self, text: str, md_file_path: Path) -> str:
        """Inline local image references in Markdown as base64 data URIs.

        Hardened variant of upstream PR #525: absolute paths and references
        that escape the markdown file's directory are left untouched (never
        inlined), and only image extensions are eligible.
        """
        base_dir = md_file_path.parent.resolve()

        def _replace(m: re.Match) -> str:
            alt_text = m.group(1)
            img_path = m.group(2).strip()
            # Skip external URLs and existing data URIs
            if img_path.startswith(("http://", "https://", "data:")):
                return m.group(0)
            # Rail 1: absolute paths never resolve against the md directory
            if Path(img_path).is_absolute():
                return m.group(0)
            resolved = (base_dir / img_path).resolve()
            # Rail 2: the file must live inside the md's own directory tree
            try:
                resolved.relative_to(base_dir)
            except ValueError:
                return m.group(0)
            # Rail 3: images only — no config/credential files by extension
            if resolved.suffix.lower() not in _MD_IMAGE_EXTS:
                return m.group(0)
            if not resolved.exists() or not resolved.is_file():
                return m.group(0)
            try:
                img_bytes = resolved.read_bytes()
                if len(img_bytes) > _MAX_EMBED_IMAGE_SIZE:
                    self.logger.debug(
                        f"Image too large to embed ({len(img_bytes)}B): {img_path}"
                    )
                    return m.group(0)
                mime = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
                b64 = base64.b64encode(img_bytes).decode("ascii")
                self.logger.info(f"Embedded image: {img_path} ({len(img_bytes)}B) as data URI")
                return f"![{alt_text}](data:{mime};base64,{b64})"
            except OSError:
                return m.group(0)

        return _MD_IMAGE_RE.sub(_replace, text)

    def _append_if_nonempty(
        self,
        documents: list[Any],
        file_path: Path,
        text: str,
        *,
        parse_engine: str = "",
        extracted_image_count: int = 0,
    ) -> None:
        if text.strip():
            # Markdown local-image inlining must run BEFORE the doc_intel
            # enrich pass below — enrich derives block_meta from this text.
            is_markdown = file_path.suffix.lower() in (".md", ".markdown")
            if is_markdown:
                text = self._resolve_markdown_images(text, file_path)
            metadata = {
                "file_name": file_path.name,
                "file_path": str(file_path),
            }
            # 悦学 doc_intel: doc-level classification from the parse cache's
            # content_list (fail-open — enrich never blocks indexing).
            try:
                from deeptutor.knowledge.doc_intel import enrich as _doc_intel_enrich

                _blocks = getattr(self, "_last_parsed_blocks", None)
                if _blocks is not None:
                    # doc_id keys stable_node_id so same-titled chapters in
                    # different books of one KB never collide (review round 2).
                    payload = _doc_intel_enrich(
                        _blocks, text, file_path.name, doc_id=file_path.stem
                    )
                    metadata.update(payload["classification"].as_metadata())
                    if payload.get("tree"):
                        import json as _json

                        # Same chunk-budget rule as di_block_meta (review
                        # follow-up): a parsed PDF's full tree can exceed the
                        # chunk size and LlamaIndex rejects the whole document.
                        # Store a slimmed tree — title/level/id keys only.
                        def _slim_tree(node: dict) -> dict:
                            slim = {
                                k: node[k]
                                for k in ("title", "level", "node_id", "struct_path")
                                if node.get(k) not in (None, "")
                            }
                            kids = node.get("children") or []
                            if kids:
                                slim["children"] = [_slim_tree(c) for c in kids]
                            return slim

                        tree_blob = _json.dumps(
                            _slim_tree(payload["tree"]), ensure_ascii=False
                        )
                        if len(tree_blob) > _DI_BLOCK_META_BUDGET * 4:
                            # Still too fat (huge outlines) — collapse to a
                            # chapter title list only so indexing never fails
                            # on tree size; the full tree stays in the API's
                            # textbook-tree view (rebuilt from cache on demand).
                            top_titles = [
                                c.get("title", "")[:40]
                                for c in (payload["tree"].get("children") or [])
                            ]
                            metadata["doc_tree"] = _json.dumps(
                                {"title": payload["tree"].get("title", ""),
                                 "chapters": top_titles},
                                ensure_ascii=False,
                            )
                        else:
                            metadata["doc_tree"] = tree_blob
                    # Per-block struct_path/textbook_node_id/q_id: chunk-level
                    # bridge data. The doc-level Document's metadata carries
                    # them for downstream chunkers that split this document —
                    # the T022 enricher reads struct_path/node_id from chunks.
                    # Slimmed hard (review follow-up): raw block text is the
                    # bulk of the payload and LlamaIndex rejects documents
                    # whose metadata exceeds the chunk size — keep only the
                    # bridge keys, drop previews beyond a short head, and cap
                    # the serialized size so huge docs (a 3500-word vocab list
                    # has thousands of blocks) never blow the chunk budget.
                    block_meta = payload.get("block_meta") or []
                    if block_meta:
                        import json as _json

                        slim = []
                        budget = _DI_BLOCK_META_BUDGET
                        for m in block_meta:
                            if not isinstance(m, dict):
                                continue
                            entry = {
                                k: m[k]
                                for k in (
                                    "q_id",
                                    "q_type",
                                    "is_question",
                                    "struct_path",
                                    "textbook_node_id",
                                )
                                if m.get(k) not in (None, "")
                            }
                            head = str(m.get("text") or "")[:64]
                            if head:
                                entry["text"] = head
                            blob = _json.dumps(entry, ensure_ascii=False)
                            if len(blob) > budget:
                                break
                            budget -= len(blob) + 1
                            slim.append(entry)
                        if slim:
                            metadata["di_block_meta"] = _json.dumps(
                                slim, ensure_ascii=False
                            )
                            question_ids = [
                                str(e["q_id"]) for e in slim if e.get("q_id")
                            ]
                            if question_ids:
                                metadata["di_q_ids"] = ",".join(question_ids)
            except Exception:
                self.logger.debug("doc_intel enrich skipped for %s", file_path.name)
            documents.append(
                Document(
                    text=text,
                    metadata=_enforce_metadata_budget(metadata),
                )
            )
            self.logger.info(
                f"Loaded: {file_path.name} ({len(text)} chars)"
            )
        else:
            if file_path.suffix.lower() == ".pdf" and extracted_image_count:
                engine_label = parse_engine or "the active parser"
                self.logger.warning(
                    "Skipped empty document: %s. The %s engine extracted %d image(s) "
                    "but no text. This is usually a scanned PDF; use an OCR-capable "
                    "parsing engine such as MinerU or Docling with OCR enabled. "
                    "Change the engine in Settings, Document Parsing.",
                    file_path.name,
                    engine_label,
                    extracted_image_count,
                )
            else:
                self.logger.warning(f"Skipped empty document: {file_path.name}")
