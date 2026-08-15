"""Deterministic answer grading + coarse error classification for Mastery Path."""

from __future__ import annotations

from difflib import SequenceMatcher
import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deeptutor.learning.models import ErrorType


# ---------------------------------------------------------------------------
# Math answer normalization layer
#
# Short answers are often mathematically equivalent without being textually
# equal ("1/2" vs "0.5", "√2/2" vs "二分之√2", "x=1" vs "1", "1<x<2" vs
# "(1,2)", "y=2x+1" vs "2x+1=y"). ``_normalize_math_answer`` maps such forms
# onto one of three canonical kinds:
#
#   ("num", float)                     pure numeric value (tolerance compare)
#   ("interval", (lo, a, b, ro))       interval bounds (per-bound compare)
#   ("expr", str)                      canonical expression string (exact)
#
# Anything it cannot recognize returns None and ``grade_answer`` falls back to
# the legacy string/similarity logic, so existing behavior is preserved.
# ---------------------------------------------------------------------------

#: Absolute tolerance for numeric equivalence.
_NUM_TOLERANCE = 1e-6

#: Full-width (U+FF01-U+FF5E) -> ASCII, plus ideographic space.
_FULLWIDTH_TRANS = {0xFF01 + i: 0x21 + i for i in range(0x5E)} | {0x3000: 0x20}

#: Unicode fraction/superscript/operator glyphs -> ASCII spellings.
_UNICODE_MATH_MAP = str.maketrans(
    {
        "½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4",
        "⅕": "1/5", "⅖": "2/5", "⅗": "3/5", "⅘": "4/5", "⅙": "1/6",
        "⅚": "5/6", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8",
        "⅐": "1/7", "⅑": "1/9", "⅒": "1/10",
        "¹": "^1", "²": "^2", "³": "^3",
        "×": "*", "÷": "/", "⋅": "*", "·": "*",
    }
)

#: Unit tokens stripped from numeric answers (longest first in the alternation).
_UNIT_TOKENS = [
    "千米", "公里", "厘米", "毫米", "分米", "微米", "纳米", "千克", "公斤",
    "毫克", "小时", "分钟", "毫升", "km/h", "m/s", "cm", "mm", "dm", "km",
    "mg", "ml", "min", "kg", "℃", "°c", "米", "克", "吨", "升", "秒", "毫秒",
    "元", "角", "分", "个", "只", "次", "名", "人", "岁", "天", "周", "月",
    "年", "倍", "度", "点", "t", "h", "ms", "°", "m", "g", "s", "l",
]
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNIT_TOKENS, key=len, reverse=True))
_UNIT_SUFFIX_RE = re.compile(r"^(.+?)\s*(" + _UNIT_ALT + r")$")

_PLAIN_NUM_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_NUM_FRACTION_RE = re.compile(r"^([+-]?\d+)\s*/\s*([+-]?\d+)$")
_NUM_PERCENT_RE = re.compile(r"^(" + _PLAIN_NUM_RE.pattern + r")\s*%$")
_NUM_WAN_RE = re.compile(r"^(" + _PLAIN_NUM_RE.pattern + r")\s*([万亿])$")

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_ALGEBRA_PREFIX_RE = re.compile(
    r"^(?:[a-zA-Z]|f\s*\(\s*x\s*\)|g\s*\(\s*x\s*\)|h\s*\(\s*x\s*\))\s*=\s*(.+)$"
)
_ALGEBRA_TRAILING_RE = re.compile(r"^(.+?)\s*=\s*y$")

_CHINESE_FRACTION_RE = re.compile(r"^(.+?)分之(.+)$")

_RADICAL_PAREN_RE = re.compile(r"(?:√|根号)\s*\(([^()]*)\)")
_RADICAL_BARE_RE = re.compile(r"(√|根号|sqrt)\s*([0-9零一二三四五六七八九十百千两]+)")

_INF_TOKENS = {"∞", "+∞", "inf", "+inf", "infinity", "正无穷", "无穷大"}
_NEG_INF_TOKENS = {"-∞", "-inf", "负无穷"}

_Interval = tuple[bool, float, float, bool]
#: kind value for "num": bare float, or (value, unit) where the unit is part
#: of the comparison key — 20厘米 vs 20米 must NOT compare equal.
_NumValue = float | tuple[float, str]
_Normalized = tuple[str, _NumValue | _Interval | str]


def _clean_text(text: str) -> str:
    """Lowercase, full-width -> ASCII, and normalize punctuation/space."""
    t = text.strip().lower()
    t = t.translate(_FULLWIDTH_TRANS)
    t = t.translate(_UNICODE_MATH_MAP)
    t = t.replace("，", ",").replace("；", ";").replace("（", "(").replace("）", ")")
    t = t.replace("。", ".").replace("：", ":").replace("　", " ")
    return re.sub(r"\s+", " ", t).strip()


def _chinese_numeral_to_int(text: str) -> int | None:
    """Convert a Chinese numeral (up to 万) to an int, or None when not one."""
    s = text.replace("两", "二")
    if not s or any(ch not in _CN_DIGITS and ch not in "十百千万" for ch in s):
        return None
    total, section, digit = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch == "十":
            section += (digit if digit else 1) * 10
            digit = 0
        elif ch == "百":
            section += (digit if digit else 1) * 100
            digit = 0
        elif ch == "千":
            section += (digit if digit else 1) * 1000
            digit = 0
        elif ch == "万":
            section += digit if digit else 1
            total += section * 10000
            section, digit = 0, 0
    return total + section + digit


def _to_arabic_int(text: str) -> int | None:
    t = text.strip()
    if re.fullmatch(r"\d+", t):
        return int(t)
    return _chinese_numeral_to_int(t)


def _to_arabic(text: str) -> str:
    n = _to_arabic_int(text)
    return str(n) if n is not None else text


def _parse_number_with_unit(text: str) -> _NumValue | None:
    """Parse a numeric token keeping its unit in the comparison key.

    ``20厘米`` → ``(20.0, "厘米")``; ``0.5`` → ``0.5``. The unit is normalized
    (lowercased, whitespace-stripped) but **kept**: two quantities only match
    when both value and unit match, so ``3kg`` vs ``3g`` stays unequal.
    Bare-unit trailing letters that look like algebra (e.g. ``4s`` where the
    expected answer is ``4t``) no longer swallow the letter — the whole token
    fails numeric parsing and falls back to the expression path.
    """
    t = text.strip()
    if not t:
        return None
    m = _UNIT_SUFFIX_RE.fullmatch(t)
    if m and m.group(1).strip():
        inner = _parse_number_bare(m.group(1).strip())
        if inner is not None:
            return (inner, m.group(2).strip().lower())
    return _parse_number_bare(t)


def _parse_number_bare(text: str) -> float | None:
    """Parse a pure numeric token: int, float, fraction, percent, 万/亿."""
    t = text.strip()
    if not t:
        return None
    m = _NUM_PERCENT_RE.fullmatch(t)
    if m:
        return float(m.group(1)) / 100.0
    m = _NUM_WAN_RE.fullmatch(t)
    if m:
        return float(m.group(1)) * (1e4 if m.group(2) == "万" else 1e8)
    m = _NUM_FRACTION_RE.fullmatch(t)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return None
        return num / den
    if _PLAIN_NUM_RE.fullmatch(t):
        return float(t)
    arabic = _chinese_numeral_to_int(t)
    if arabic is not None:
        return float(arabic)
    return None


def _interval_bound(token: str) -> float | None:
    t = token.strip()
    if t in _INF_TOKENS:
        return math.inf
    if t in _NEG_INF_TOKENS:
        return -math.inf
    # Interval bounds are bare numbers; a unit there (rare) is left to the
    # expression path instead of silently swallowing it.
    return _parse_number_bare(t)


def _normalize_interval(text: str) -> _Interval | None:
    """Normalize interval forms to ``(left_open, a, b, right_open)``.

    Accepts bracket forms ``(1,2)`` / ``[1,2]`` (optionally prefixed by
    ``x∈``) and inequality forms ``1<x<2`` / ``1≤x≤2`` (either direction).
    """
    m = re.fullmatch(r"(?:x\s*∈\s*)?\s*([\[\(])\s*(.+?)\s*[,，]\s*(.+?)\s*([\]\)])\s*", text)
    if m:
        a, b = _interval_bound(m.group(2)), _interval_bound(m.group(3))
        if a is not None and b is not None:
            return (m.group(1) == "(", a, b, m.group(4) == ")")
        return None
    m = re.fullmatch(r"(.+?)\s*(<=|≤|<)\s*x\s*(<=|≤|<)\s*(.+)", text)
    if m:
        a, b = _interval_bound(m.group(1)), _interval_bound(m.group(4))
        if a is None or b is None:
            return None
        return (m.group(2) not in ("<=", "≤"), a, b, m.group(3) not in ("<=", "≤"))
    m = re.fullmatch(r"(.+?)\s*(>=|≥|>)\s*x\s*(>=|≥|>)\s*(.+)", text)
    if m:
        a, b = _interval_bound(m.group(4)), _interval_bound(m.group(1))
        if a is None or b is None:
            return None
        return (m.group(3) not in (">=", "≥"), a, b, m.group(2) not in (">=", "≥"))
    return None


def _strip_algebra_wrapper(text: str) -> str:
    """Strip a leading ``y=``/``f(x)=``/``x=`` prefix or trailing ``=y``."""
    m = _ALGEBRA_PREFIX_RE.match(text)
    if m:
        return m.group(1)
    m = _ALGEBRA_TRAILING_RE.fullmatch(text)
    if m:
        return m.group(1)
    return text


def _normalize_chinese_fraction(text: str) -> str:
    """Rewrite the Chinese verbal fraction ``a分之b`` as ``b/a``."""
    m = _CHINESE_FRACTION_RE.fullmatch(text)
    if not m:
        return text
    denominator = _to_arabic_int(m.group(1))
    if denominator is None:
        return text
    numerator = m.group(2)
    arabic = _to_arabic_int(numerator)
    if arabic is not None:
        numerator = str(arabic)
    return f"{numerator}/{denominator}"


def _normalize_radical_notation(text: str) -> str:
    """Map sqrt2/√2/根号2 (and parenthesized forms) onto ``sqrt(...)``."""
    text = _RADICAL_PAREN_RE.sub(r"sqrt(\1)", text)
    return _RADICAL_BARE_RE.sub(lambda m: f"sqrt({_to_arabic(m.group(2))})", text)


def _compact_expression(text: str) -> str:
    """Collapse whitespace in a math expression for canonical comparison."""
    return re.sub(r"\s+", "", text)


def _normalize_math_answer(text: str) -> _Normalized | None:
    """Return a canonical math form for ``text``, or None when it is not math.

    The returned tuple is ``(kind, value)`` where kind is one of ``"num"``
    (compare with tolerance), ``"interval"`` (compare per bound), or
    ``"expr"`` (compare exactly). Expression answers are only claimed when
    they contain a digit, so plain words keep flowing through the legacy
    string/similarity path unchanged.
    """
    t = _clean_text(text)
    if not t:
        return None

    interval = _normalize_interval(t)
    if interval is not None:
        return ("interval", interval)

    t = _strip_algebra_wrapper(t)
    if not t:
        return None

    interval = _normalize_interval(t)
    if interval is not None:
        return ("interval", interval)

    t = _normalize_chinese_fraction(t)
    t = _normalize_radical_notation(t)

    number = _parse_number_with_unit(t)
    if number is not None:
        return ("num", number)

    expr = _compact_expression(t)
    if not expr or not re.search(r"\d", expr):
        return None
    return ("expr", expr)


def _numbers_close(a: float, b: float) -> bool:
    if math.isinf(a) or math.isinf(b):
        return a == b
    return abs(a - b) <= _NUM_TOLERANCE


def _num_values_match(a: _NumValue, b: _NumValue) -> bool:
    """Compare numeric answers; a (value, unit) pair must match on both parts.

    ``20厘米`` vs ``20米`` → units differ → not equal. A bare number matches
    only another bare number (``5`` vs ``5米`` is a missing unit, judged
    unequal — expected answers should carry the unit they require).
    """
    if isinstance(a, tuple) or isinstance(b, tuple):
        if not (isinstance(a, tuple) and isinstance(b, tuple)):
            return False
        va, ua = a
        vb, ub = b
        return ua == ub and _numbers_close(va, vb)
    return _numbers_close(a, b)  # type: ignore[arg-type]


def _math_answers_match(user_norm: _Normalized, expected_norm: _Normalized) -> bool:
    """Compare two normalized math answers of the same kind."""
    kind, user_val = user_norm
    expected_val = expected_norm[1]
    if kind == "num":
        return _num_values_match(user_val, expected_val)  # type: ignore[arg-type]
    if kind == "interval":
        lo_u, a_u, b_u, ro_u = user_val  # type: ignore[misc]
        lo_e, a_e, b_e, ro_e = expected_val  # type: ignore[misc]
        return (
            lo_u == lo_e
            and ro_u == ro_e
            and _numbers_close(a_u, a_e)
            and _numbers_close(b_u, b_e)
        )
    return user_val == expected_val


def grade_answer(user_answer: str, expected_answer: str, question_type: str = "short") -> bool:
    """Grade user answer against expected answer.

    Args:
        user_answer: The user's submitted answer.
        expected_answer: The stored expected answer.
        question_type: One of "choice", "short", "open".

    Returns:
        True if answer is correct.
    """
    user = user_answer.strip().lower()
    expected = expected_answer.strip().lower()

    if not expected:
        return False

    if question_type == "choice":
        user_norm = user.replace(" ", "")
        expected_norm = expected.replace(" ", "")
        return user_norm == expected_norm

    if question_type == "short":
        user_math = _normalize_math_answer(user)
        expected_math = _normalize_math_answer(expected)
        if (
            user_math is not None
            and expected_math is not None
            and user_math[0] == expected_math[0]
        ):
            return _math_answers_match(user_math, expected_math)
        if user == expected:
            return True
        if len(expected) <= 30:
            return SequenceMatcher(None, user, expected).ratio() >= 0.85
        return False

    if question_type == "open":
        keywords = [k.strip() for k in re.split(r"[,;，；。\n]+", expected) if k.strip()]
        if not keywords:
            return False
        matched = sum(1 for kw in keywords if kw in user)
        return matched / len(keywords) >= 0.6

    return False


def classify_error(user_answer: str) -> ErrorType:
    """Coarse error classification for a wrong answer.

    A blank answer signals the student did not know (metacognitive); anything
    else is treated as a wrong application. The richer four-type taxonomy is
    assigned later by the LLM in the error-diagnosis stage.
    """
    from deeptutor.learning.models import ErrorType

    return ErrorType.METACOGNITIVE if not user_answer.strip() else ErrorType.APPLICATION_ERROR


__all__ = ["grade_answer", "classify_error"]
