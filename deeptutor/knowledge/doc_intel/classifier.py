"""Doc-level classification: subject / doc_type / grade.

Rules first (filename lexicon + title-page features), LLM sampling only when
rules are inconclusive. Results are cached per document so re-indexing never
re-classifies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

# ── subject lexicon ──
_SUBJECT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("数学", re.compile(r"数学|几何|代数|函数|数列|概率|统计|圆锥曲线|导数|向量|不等式|三角|集合|方程|复数|立体|排列组合|二项式|苏教|人教[AB]?|北师大版数学")),
    ("语文", re.compile(r"语文|文言|古诗文|阅读与写作|作文|现代文")),
    ("英语", re.compile(r"英语|English|完形填空|阅读理解")),
    ("物理", re.compile(r"物理|力学|电磁|牛顿|电路")),
    ("化学", re.compile(r"化学|元素|化学反应|有机|无机")),
    ("生物", re.compile(r"生物|细胞|遗传|生态|光合")),
    ("政治", re.compile(r"政治|中国特色社会主义|经济与社会|哲学与文化|当代国际|法律与生活|逻辑与思维|唯物|辩证|中国共产党|人民当家作主|依法治国|人民代表大会|民主")),
    ("历史", re.compile(r"历史|中国古代|近代史|现代史|中外历史|文明演进|工业革命|辛亥革命")),
    ("地理", re.compile(r"地理|自然地理|人文地理|区域地理|地貌|气候|洋流")),
]

# ── doc_type lexicon ──
# Applied to filename + title; body-text markers like 【答案】/【详解】 inside
# teacher editions must NOT flip the type — a 教师版 exam is an exam whose
# answers are inline, not an answer book.
_DOC_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("answer_book", re.compile(r"答案|解析版|详解|参考答案")),
    ("textbook", re.compile(r"必修|选择性必修|选修|教材|教师教学用书|读本")),
    ("exam", re.compile(r"试卷|真题|模拟|联考|统考|月考|期中|期末|测试卷|练习卷|高考")),
    ("workbook", re.compile(r"练习|教辅|学案|课时作业|小题练习|提分|一轮|二轮|复习讲义")),
    ("lecture", re.compile(r"讲义|课件|教案|笔记|学程|指导手册")),
]

# ── grade lexicon ──
_GRADE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("高三", re.compile(r"高三|高考|一轮|二轮|三轮|总复习")),
    ("高二", re.compile(r"高二|选择性必修")),
    ("高一", re.compile(r"高一|必修[一二三]")),
]

DOC_TYPES = ("textbook", "exam", "workbook", "lecture", "answer_book", "other")


@dataclass
class DocClassification:
    """Immutable classification result attached to every node of a doc."""

    subject: str = ""
    doc_type: str = "other"
    grade: str = ""
    # Which signals fired, for debugging / acceptance sampling.
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, str]:
        md: dict[str, str] = {}
        if self.subject:
            md["doc_subject"] = self.subject
        md["doc_type"] = self.doc_type
        if self.grade:
            md["doc_grade"] = self.grade
        return md


def _match_first(text: str, table: list[tuple[str, re.Pattern[str]]]) -> str | None:
    for label, pattern in table:
        if pattern.search(text):
            return label
    return None


def classify_by_rules(
    filename: str,
    *,
    first_page_text: str = "",
    title_text: str = "",
) -> DocClassification:
    """Pure-local classification from filename + sampled document text.

    ``first_page_text`` / ``title_text`` come from the parse cache (first ~2
    pages of blocks). Empty samples are fine — filename alone often decides.
    """
    ev: dict[str, Any] = {}
    # Filename gets heaviest weight (uploaders name files meaningfully).
    subject = _match_first(filename, _SUBJECT_PATTERNS)
    if subject is None:
        subject = _match_first(title_text, _SUBJECT_PATTERNS)
        ev["subject_src"] = "title"
    if subject is None:
        subject = _match_first(first_page_text, _SUBJECT_PATTERNS)
        ev["subject_src"] = "content"
    else:
        ev["subject_src"] = "filename"
    if subject is None:
        # LLM pass decides later; rules stay silent.
        return DocClassification(evidence=ev)

    # doc_type: filename + title only (body 【答案】 markers are inline-answer
    # teacher editions, not answer books). answer markers still beat exam
    # markers when they appear in the NAME ("...解析版.docx" is an answer book).
    name_corpus = " ".join(part for part in (filename, title_text) if part)
    doc_type = _match_first(name_corpus, _DOC_TYPE_PATTERNS)
    if doc_type is None:
        body_corpus = first_page_text[:400]
        # Body fallback: the three structural types announce themselves.
        doc_type = _match_first(body_corpus, _DOC_TYPE_PATTERNS[1:]) or None
    doc_type = doc_type or "other"
    grade = _match_first(" ".join(part for part in (filename, title_text, first_page_text[:400]) if part),
                         _GRADE_PATTERNS) or ""

    ev.update(subject=subject, doc_type=doc_type, grade=grade)
    return DocClassification(subject=subject, doc_type=doc_type, grade=grade, evidence=ev)


def needs_llm(result: DocClassification) -> bool:
    """True when rules could not even pin the subject — LLM sampling due."""
    return not result.subject


# ── LLM pass (doubao-lite JSON; scheduled as a background task) ──

_CLASSIFY_PROMPT = """你是教育资料分类器。根据文件名和文档开头内容，判断学科、资料类型、年级。

文件名：{filename}
文档开头：
{sample}

只输出 JSON（不要多余文字）：
{{"subject": "数学|语文|英语|物理|化学|生物|政治|历史|地理|未知",
 "doc_type": "textbook|exam|workbook|lecture|answer_book|other",
 "grade": "高一|高二|高三|初中|未知"}}
"""


async def classify_by_llm(
    filename: str,
    sample: str,
    *,
    chat_fn=None,
) -> DocClassification | None:
    """LLM sampling pass. ``chat_fn`` is an async callable(prompt)->str wired
    to the configured chat model by the caller (keeps this module import-light
    and testable with a stub). Returns None on any failure — degrade to
    rules-only classification.
    """
    if chat_fn is None:
        return None
    import json as _json

    try:
        raw = await chat_fn(_CLASSIFY_PROMPT.format(filename=filename, sample=sample[:1500]))
        payload = _json.loads(_json.loads('"' + raw.strip().replace('"', '\\"').replace("\n", "\\n") + '"')
                              if raw.strip().startswith('"') else raw.strip())
        subject = payload.get("subject", "")
        if subject in ("未知", ""):
            return None
        return DocClassification(
            subject=subject,
            doc_type=payload.get("doc_type", "other") or "other",
            grade=payload.get("grade", "") or "",
            evidence={"subject_src": "llm"},
        )
    except Exception:
        return None
