"""doc_intel — document intelligence layer for the ingestion pipeline.

悦学二开：在 DT 摄入管道内自动完成教育语义标注，全程零人工、零外部脚本。

Consumes the parse cache IR (markdown + content_list blocks + images) and
returns per-block metadata that ``document_loader`` injects into LlamaIndex
nodes:

* :mod:`.classifier`  — subject / doc_type / grade (rules first, LLM sample
  only when rules are inconclusive)
* :mod:`.structure`   — textbook tree (单元 → 课 → 节) from title levels,
  each block tagged with its ``struct_path``
* :mod:`.qa_split`    — question/answer/analysis separation (题号 patterns +
  【答案】/【详解】 markers + answer-section fallback)
* :mod:`.image_link`  — attach images to the nearest question/section by bbox

The public surface is intentionally two functions so the hook in
``document_loader`` stays a three-line, fail-open call: ``enrich()`` runs the
pure-local pipeline synchronously (no network, <10ms per 100 blocks), and
``classify_document_async()`` is the optional LLM pass scheduled as a
background task. Any internal error degrades to "no metadata added" — the
index build must never fail because of doc_intel.
"""

from .enrich import classify_document_async, enrich

__all__ = ["enrich", "classify_document_async"]
