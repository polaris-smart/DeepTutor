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
from typing import Any, Iterable

from llama_index.core import Document
from llama_index.core.schema import ImageNode

from deeptutor.services.embedding import get_embedding_client
from deeptutor.services.llm.client import get_llm_client
from deeptutor.services.rag.file_routing import FileTypeRouter
from deeptutor.utils.document_validator import DocumentValidator

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

    async def load(self, file_paths: Iterable[str]) -> list[Any]:
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
            text, extracted_images = await asyncio.to_thread(
                self._parse_document, file_path
            )
            self._append_if_nonempty(documents, file_path, text)
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
            image_sources.append(_ImageSource(path=path, origin=path))

        if image_sources:
            documents.extend(await self._load_image_nodes(image_sources))

        for file_path_str in classification.unsupported:
            self.logger.warning(f"Skipped unsupported file: {Path(file_path_str).name}")

        return documents

    def _parse_document(self, file_path: Path) -> tuple[str, list[_ImageSource]]:
        """Parse a document through the shared, engine-pluggable parse layer.

        Returns ``(text, extracted_images)``. A parse failure (engine
        unavailable, unsupported format for the active engine, or models not
        ready) is logged and the file is skipped — matching the sibling
        LightRAG/GraphRAG pipelines — rather than aborting the whole batch.
        """
        from deeptutor.services.parsing import ParserError, get_parse_service

        try:
            parsed = get_parse_service().parse(file_path)
        except ParserError as exc:
            self.logger.warning(
                f"Skipped {file_path.name}: the active document-parsing engine could "
                f"not handle it ({exc}). Change the engine in Settings → Document Parsing."
            )
            return "", []

        text = parsed.markdown.strip() or self._text_from_blocks(parsed.blocks)
        # 悦学 doc_intel: keep the structured blocks for _append_if_nonempty's
        # enrich pass (content_list carries heading levels / bboxes / page_idx).
        self._last_parsed_blocks = parsed.blocks
        images = self._collect_asset_images(parsed.asset_dir, origin=file_path)
        return text, images

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

    async def _load_image_nodes(self, sources: list[_ImageSource]) -> list[ImageNode]:
        embedding_client = get_embedding_client()
        llm_client = get_llm_client()

        unsupported_reasons = []
        if not embedding_client.supports_multimodal_contents():
            unsupported_reasons.append(
                "embedding provider/model does not support multimodal contents "
                f"(binding={embedding_client.config.binding}, "
                f"model={embedding_client.config.model})"
            )
        if not llm_client.supports_multimodal_images():
            unsupported_reasons.append(
                "LLM provider/model does not support multimodal image input "
                f"(binding={llm_client.config.binding}, model={llm_client.config.model})"
            )
        if unsupported_reasons:
            reason_text = "; ".join(unsupported_reasons)
            for source in sources:
                self.logger.warning(
                    "Skipped image because image indexing requires both "
                    f"multimodal embedding and multimodal LLM support; {reason_text}: "
                    f"{source.path.name}"
                )
            return []

        description_semaphore = asyncio.Semaphore(self.image_concurrency)

        async def describe(source: _ImageSource) -> tuple[_ImageSource, str, str] | None:
            async with description_semaphore:
                try:
                    payload = self._load_image_payload(source.path)
                    description = await self._describe_image(
                        source.path, payload["base64"], payload["mimetype"]
                    )
                    return (source, description, payload["data_uri"]) if description else None
                except Exception as exc:
                    self.logger.error("Failed to describe image %s: %s", source.path.name, exc)
                    return None

        description_results = await asyncio.gather(*(describe(source) for source in sources))
        described = [result for result in description_results if result is not None]
        description_failures = [
            source.path.name
            for source, result in zip(sources, description_results)
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
            source, description, data_uri = item
            async with embedding_semaphore:
                try:
                    vectors = await embedding_client.embed_contents([{"image": data_uri}])
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

    async def _describe_image(self, file_path: Path, image_base64: str, mimetype: str) -> str:
        llm_client = get_llm_client()
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

    def _append_if_nonempty(self, documents: list[Any], file_path: Path, text: str) -> None:
        if text.strip():
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

                        metadata["doc_tree"] = _json.dumps(
                            payload["tree"], ensure_ascii=False
                        )
                    # Per-block struct_path/textbook_node_id/q_id: chunk-level
                    # bridge data. The doc-level Document's metadata carries
                    # them for downstream chunkers that split this document —
                    # the T022 enricher reads struct_path/node_id from chunks.
                    block_meta = payload.get("block_meta") or []
                    if block_meta:
                        import json as _json

                        metadata["di_block_meta"] = _json.dumps(
                            block_meta, ensure_ascii=False
                        )
                        question_ids = [
                            m.get("q_id") for m in block_meta if m.get("q_id")
                        ]
                        if question_ids:
                            metadata["di_q_ids"] = ",".join(
                                str(q) for q in question_ids
                            )
            except Exception:
                self.logger.debug("doc_intel enrich skipped for %s", file_path.name)
            documents.append(
                Document(
                    text=text,
                    metadata=metadata,
                )
            )
            self.logger.info(f"Loaded: {file_path.name} ({len(text)} chars)")
        else:
            self.logger.warning(f"Skipped empty document: {file_path.name}")
