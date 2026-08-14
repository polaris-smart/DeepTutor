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

    def __init__(self, logger=None) -> None:
        self.logger = logger or logging.getLogger(__name__)

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

        embedded: list[_ImageSource] = []
        descriptions: list[str] = []
        contents = []
        for source in sources:
            try:
                image_payload = self._load_image_payload(source.path)
                description = await self._describe_image(
                    source.path,
                    image_payload["base64"],
                    image_payload["mimetype"],
                )
                if not description:
                    self.logger.warning(
                        "Skipped image because the configured multimodal LLM "
                        f"returned no description: {source.path.name}"
                    )
                    continue
                contents.append({"image": image_payload["data_uri"]})
                embedded.append(source)
                descriptions.append(description)
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

        if not contents:
            return []

        try:
            embeddings = await embedding_client.embed_contents(contents)
        except Exception as exc:
            self.logger.error(
                "Failed to embed image contents with configured multimodal embedding "
                "provider/model (binding=%s, model=%s): %s",
                embedding_client.config.binding,
                embedding_client.config.model,
                exc,
            )
            return []
        nodes: list[ImageNode] = []
        for source, description, embedding in zip(embedded, descriptions, embeddings):
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
                    payload = _doc_intel_enrich(_blocks, text, file_path.name)
                    metadata.update(payload["classification"].as_metadata())
                    if payload.get("tree"):
                        import json as _json

                        metadata["doc_tree"] = _json.dumps(
                            payload["tree"], ensure_ascii=False
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
