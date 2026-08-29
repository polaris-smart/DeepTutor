# Proposal: Textbook Import Extension Point for Books

> 中文摘要：Book 现有逻辑是"基于 KB 摘要的教学讲义生成器"（SpineAgent 编目录 + 编译 agent 写讲义），
> 对 K12 教材正典场景缺两块：①真实目录树的一等导入；②原文保真的正文块。
> 本提案给 Book 加一个**教材导入扩展点**（纯增量、复用现有 confirm-spine / insert-block 基建），
> 让"教材进 Book"成为官方能力。实测依据来自一个真实样本：一本 4 课的政治教材，
> 自动生成的书 4 个章名全错、全书仅引用 20 个 KB 片段、正文为模型改写（详见"Motivation"）。

## Motivation (real-world sample)

A real user textbook (人教版高中政治必修1, 4 chapters + 综合探究) was pushed through the
standard Book flow (`POST /books` with a politics KB → confirm-proposal → confirm-spine
auto spine → compile). Results, verified via the API:

| Expectation (textbook) | Generated book |
|---|---|
| 第一课 社会主义从空想到科学、从理论到实践的发展 | 第一课：中国特色社会主义的开创与发展 |
| 第二课 只有社会主义才能救中国 | 第二课：中国特色社会主义的创立与发展 |
| 第三课 只有中国特色社会主义才能发展中国 | 第三课：中国特色社会主义的实践与成就 |
| 第四课 只有坚持和发展中国特色社会主义才能实现中华民族伟大复兴 | 第四课：中国特色社会主义的未来发展 |

- All 4 chapter titles were **invented** by SpineAgent (`book.metadata.source_quality.chunk_count = 20`:
  the whole book was grounded on 20 KB chunks).
- Page blocks were **agent-synthesized prose** (section/figure/callout), not the textbook's text.
  No fidelity channel exists except `user_note` (which renders as a note card, not textbook prose).
- Block planning decorated a **politics** page with `desmos` / `geogebra` / `code` / `formula`
  blocks (template-driven, not subject-aware).

Books are excellent as **generated teaching materials** (guided tour, lecture notes, quizzes,
visualizations). But for textbook-canonical use cases the missing piece is an
**import path for the canon**: the real TOC as the spine, and verbatim content as pages.

## Proposed extension (additive, reuses existing primitives)

### 1. Spine import — `POST /api/v1/book/books/{book_id}/spine/import`

Accepts a real TOC as JSON (the same shape `confirm-spine` already takes), validates it
(page budget, id uniqueness, order continuity), and installs it server-side:

```json
{
  "source": "toc_json",
  "spine": { "chapters": [ { "title": "第一课 社会主义从空想到科学、从理论到实践的发展",
                              "content_type": "theory", "children": [...] } ] },
  "auto_overview": true,
  "auto_compile": false
}
```

- UI: an "Import TOC" affordance on the spine confirmation step (+ JSON template download).
- Today this is achievable by hand-editing the spine via the API (we verified it works:
  pilot book `bk_f2008e68ca` carries the exact real TOC). The gap is a first-class,
  validated affordance instead of a raw API call.

### 2. Verbatim content block — new native block type `reading`

Generalizes the proven `user_note` channel (params.body + compile_now=false → READY,
zero agent calls) into a textbook-prose block:

- `block_type: "reading"`, `params: { body, variant?, anchor? }`, `compile_now: false`
  → `status: READY`, `payload: markdown` (verbatim, no LLM in the loop).
- Renderer: textbook-style heading + paragraph flow (vs. user_note's card chrome);
  optional `variant` maps textbook 栏目 boxes (e.g. 探究与分享/相关链接) to styled callouts.
- Every reading block carries `source_anchors` (KB doc id + page), so KB remains the
  canonical store and Book pages stay traceable.

### 3. Bulk page import — `POST /api/v1/book/books/{book_id}/pages/import`

Deterministic, zero-agent channel for canonical imports:

```json
{ "chapter_id": "ch_xxx",
  "pages": [ { "title": "...", "blocks": [ {"block_type": "reading", "params": {"body": "…"}} ] } ] }
```

- Server assembles blocks verbatim (same contract as `question_block_ids` assembly used
  elsewhere: LLM may annotate, server assembles).
- Agent compilation stays **opt-in per page** — generate the lecture/quiz layer on top of
  the canon when the user asks for it, never instead of it.

## Why this fits DeepTutor

- **Zero new infrastructure**: confirm-spine already accepts edited spines; insert-block
  already has the compile_now=false READY path; KB docstore already keeps the canonical text.
- **Separates canon from pedagogy**: the canon (spine + verbatim pages) is imported and
  frozen; the pedagogy layer (讲义/quiz/可视化) stays generative — the best of both.
- **Unlocks K12 textbook scenarios** where verbatim fidelity is a hard requirement
  (teachers compare page-by-page; regulators check for content drift).

## Verified feasibility (already exercised against a live instance)

1. Real-TOC spine via confirm-spine → rendered correctly (pilot book).
2. `user_note` verbatim blocks → status READY, payload = 原文 100% intact, zero agent cost.
3. Retrievable canon: after KB re-index, top-1 retrieval on 政治 KB returns the textbook's
   real TOC — anchors for reading blocks exist.

We (YueXue team) are happy to implement this on the official extension points and send it
upstream as PRs. — 2026-08-29
