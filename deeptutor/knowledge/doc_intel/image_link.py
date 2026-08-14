"""Attach images (and tables) to their owning question or section.

MinerU content_list blocks carry ``bbox`` = (x0, y0, x1, y1) in page pixels
and ``page_idx``. An image belongs to the block that *precedes* it in reading
order when the surrounding text references it (如图 / 见图 / 图中), else to the
nearest block by vertical distance on the same page — in practice, the
current question's stem or the current section body.

Strategy (cheap, no geometry math beyond page + reading order):
* track the nearest "attachable" block (question stem, analysis, section body)
  as the scan proceeds;
* ``如图``-style references in the *following* text confirm the link and can
  pull the image forward to a later block when the reference appears there.

Emits per-image-block metadata: ``linked_images: [img_path,...]`` written on
the *owning text block*, and ``image_of`` (role + q_id) on the image node.
"""

from __future__ import annotations

import re
from typing import Any

FIG_REF_RE = re.compile(r"如[下图图]|如[上下]图|图中|见图|如图所示")
IMAGE_TYPES = {"image", "table"}


def _text_v1(block: dict) -> str:
    return (block.get("text") or "").strip()


def _text_v2(block: dict) -> str:
    content = block.get("content") or {}
    parts = content.get("title_content") or content.get("paragraph_content") or []
    return "".join(p.get("content", "") for p in parts if isinstance(p, dict)).strip()


def _image_id_v1(block: dict) -> str | None:
    path = block.get("img_path")
    return path or None


def _image_id_v2(block: dict) -> str | None:
    path = (block.get("content") or {}).get("img_path") or block.get("img_path")
    return path or None


def link_images(
    blocks: list[dict],
    roles: list[str],
    question_blocks: dict[int, str],  # block_idx -> q_id
    *,
    text_fn=_text_v1,
    image_id_fn=_image_id_v1,
) -> tuple[dict[int, list[str]], dict[int, dict[str, Any]]]:
    """Return ``(owner_idx -> [img ids], image_idx -> {image_of, q_id})``."""
    owner_images: dict[int, list[str]] = {}
    image_meta: dict[int, dict[str, Any]] = {}

    last_owner: int | None = None          # attachable text block idx
    last_owner_role: str = ""
    last_owner_qid: str | None = None
    pending_images: list[tuple[int, str]] = []  # (idx, img_id) awaiting an owner

    for idx, block in enumerate(blocks):
        btype = block.get("type", "")
        if btype in IMAGE_TYPES:
            img_id = image_id_fn(block)
            if img_id:
                pending_images.append((idx, img_id))
            continue

        text = text_fn(block)
        if not text:
            continue
        role = roles[idx] if idx < len(roles) else ""
        attachable = role in ("question", "analysis", "answer") or role == ""
        if not attachable:
            # Heading / non-owning block — flush pending images to the previous
            # owner (figures before a new section belong to the old section).
            if pending_images and last_owner is not None:
                _flush(pending_images, last_owner, last_owner_role, last_owner_qid,
                       owner_images, image_meta)
                pending_images = []
            continue

        # New attachable block: images seen since the last one belong to the
        # previous owner unless this block references them.
        if pending_images and last_owner is not None and not FIG_REF_RE.search(text):
            _flush(pending_images, last_owner, last_owner_role, last_owner_qid,
                   owner_images, image_meta)
            pending_images = []

        last_owner = idx
        last_owner_role = role or "body"
        last_owner_qid = question_blocks.get(idx)
        # Text referencing figures claims any still-pending images.
        if pending_images and FIG_REF_RE.search(text):
            _flush(pending_images, idx, last_owner_role, last_owner_qid,
                   owner_images, image_meta)
            pending_images = []

    # Trailing images → last owner.
    if pending_images and last_owner is not None:
        _flush(pending_images, last_owner, last_owner_role, last_owner_qid,
               owner_images, image_meta)

    return owner_images, image_meta


def _flush(
    pending: list[tuple[int, str]],
    owner_idx: int,
    owner_role: str,
    owner_qid: str | None,
    owner_images: dict[int, list[str]],
    image_meta: dict[int, dict[str, Any]],
) -> None:
    bucket = owner_images.setdefault(owner_idx, [])
    for img_idx, img_id in pending:
        if img_id not in bucket:
            bucket.append(img_id)
        meta: dict[str, Any] = {"image_of": owner_role or "body"}
        if owner_qid:
            meta["q_id"] = owner_qid
        image_meta[img_idx] = meta
