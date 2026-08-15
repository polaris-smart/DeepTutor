"""Tests for math-equivalence normalization in short-answer grading.

Regression for the 教研评审 finding: ``grade_answer`` graded short answers by
string equality or 0.85 similarity, so mathematically equivalent forms such as
"1/2" vs "0.5", "√2/2" vs "二分之√2", "x=1" vs "1", "1<x<2" vs "(1,2)" and
"y=2x+1" vs "2x+1=y" were all marked wrong.

The normalization layer ``_normalize_math_answer`` recognizes numeric, algebra,
interval, and radical forms. Anything it cannot normalize falls back to the
legacy string/similarity logic, so existing behavior is preserved.
"""

from deeptutor.learning.grading import _normalize_math_answer, grade_answer


class TestNumericEquivalence:
    """① Pure numeric parsing (int/float, fraction, percent, units) with 1e-6 tolerance."""

    def test_fraction_vs_decimal(self):
        assert grade_answer("1/2", "0.5", "short") is True
        assert grade_answer("0.5", "1/2", "short") is True

    def test_integer_vs_float(self):
        assert grade_answer("2", "2.0", "short") is True
        assert grade_answer("1e3", "1000", "short") is True

    def test_percent(self):
        assert grade_answer("50%", "0.5", "short") is True
        assert grade_answer("12.5%", "0.125", "short") is True

    def test_units_stripped(self):
        assert grade_answer("5cm", "5", "short") is True
        assert grade_answer("5厘米", "5cm", "short") is True
        assert grade_answer("3.5千克", "3.5kg", "short") is True

    def test_tolerance(self):
        assert grade_answer("1/3", "0.3333333", "short") is True
        assert grade_answer("0.3333333333333333", "1/3", "short") is True

    def test_chinese_fraction_words(self):
        assert grade_answer("二分之一", "0.5", "short") is True
        assert grade_answer("百分之五十", "50%", "short") is True

    def test_negative(self):
        assert grade_answer("-3/4", "-0.75", "short") is True

    def test_fullwidth_input(self):
        assert grade_answer("１／２", "0.5", "short") is True
        assert grade_answer("５０％", "0.5", "short") is True

    def test_non_equivalent(self):
        assert grade_answer("1/2", "1/3", "short") is False
        assert grade_answer("0.5", "0.333", "short") is False
        assert grade_answer("2", "3", "short") is False


class TestAlgebraicEquivalence:
    """② Simple algebra: strip y=/f(x)=/x= prefixes and trailing =y."""

    def test_y_prefix(self):
        assert grade_answer("y=2x+1", "2x+1", "short") is True

    def test_eq_y_suffix(self):
        assert grade_answer("2x+1=y", "2x+1", "short") is True
        assert grade_answer("y=2x+1", "2x+1=y", "short") is True

    def test_function_prefix(self):
        assert grade_answer("f(x)=x^2+1", "x^2+1", "short") is True

    def test_x_equals_number(self):
        assert grade_answer("x=1", "1", "short") is True
        assert grade_answer("x=1", "1.0", "short") is True

    def test_non_equivalent(self):
        assert grade_answer("y=2x+1", "2x+2", "short") is False
        assert grade_answer("x=1", "2", "short") is False


class TestIntervalEquivalence:
    """③ Interval notation: 1<x<2 / (1,2) / x∈(1,2) share one canonical form."""

    def test_inequality_vs_bracket(self):
        assert grade_answer("1<x<2", "(1,2)", "short") is True
        assert grade_answer("(1,2)", "1<x<2", "short") is True

    def test_membership_form(self):
        assert grade_answer("x∈(1,2)", "(1,2)", "short") is True
        assert grade_answer("x∈(1,2)", "1<x<2", "short") is True

    def test_fullwidth_brackets(self):
        assert grade_answer("（1，2）", "(1,2)", "short") is True

    def test_closed_interval(self):
        assert grade_answer("1≤x≤2", "[1,2]", "short") is True
        assert grade_answer("[1,2]", "1≤x≤2", "short") is True

    def test_half_open(self):
        assert grade_answer("1≤x<2", "[1,2)", "short") is True
        assert grade_answer("1<x≤2", "(1,2]", "short") is True

    def test_reversed_inequality(self):
        assert grade_answer("2>x>1", "(1,2)", "short") is True

    def test_non_equivalent(self):
        assert grade_answer("(1,2)", "(1,3)", "short") is False
        assert grade_answer("(1,2)", "[1,2]", "short") is False
        assert grade_answer("1<x<2", "1<x≤2", "short") is False


class TestRadicalEquivalence:
    """④ Radical notation: sqrt2 / √2 / 根号2 normalize to the same form."""

    def test_sqrt_notations(self):
        assert grade_answer("√2", "sqrt2", "short") is True
        assert grade_answer("根号2", "√2", "short") is True
        assert grade_answer("根号二", "sqrt(2)", "short") is True
        assert grade_answer("√(2)", "sqrt(2)", "short") is True

    def test_radical_fraction(self):
        assert grade_answer("√2/2", "二分之√2", "short") is True
        assert grade_answer("二分之根号2", "sqrt(2)/2", "short") is True
        assert grade_answer("sqrt(2)/2", "√2/2", "short") is True

    def test_non_equivalent(self):
        assert grade_answer("√2/2", "√3/2", "short") is False
        assert grade_answer("√2", "2", "short") is False


class TestNormalizeMathAnswer:
    """Unit-level checks of the normalization layer itself."""

    def test_number_kind(self):
        assert _normalize_math_answer("1/2") == ("num", 0.5)
        assert _normalize_math_answer("50%") == ("num", 0.5)
        assert _normalize_math_answer("5cm") == ("num", 5.0)

    def test_interval_kind(self):
        assert _normalize_math_answer("1<x<2") == ("interval", (True, 1.0, 2.0, True))
        assert _normalize_math_answer("x∈(1,2)") == ("interval", (True, 1.0, 2.0, True))
        assert _normalize_math_answer("1≤x<2") == ("interval", (False, 1.0, 2.0, True))

    def test_expr_kind(self):
        assert _normalize_math_answer("y=2x+1") == ("expr", "2x+1")
        assert _normalize_math_answer("√2/2") == ("expr", "sqrt(2)/2")
        assert _normalize_math_answer("二分之√2") == ("expr", "sqrt(2)/2")

    def test_non_math_is_none(self):
        assert _normalize_math_answer("photosynthesis") is None
        assert _normalize_math_answer("") is None


class TestLegacyBehaviorPreserved:
    """⑤ Fallback: non-math short answers keep the old string/similarity logic."""

    def test_exact_match(self):
        assert grade_answer("photosynthesis", "photosynthesis", "short") is True

    def test_fuzzy_pass(self):
        assert grade_answer("photosynthesi", "photosynthesis", "short") is True

    def test_fuzzy_fail(self):
        assert grade_answer("completely different", "photosynthesis", "short") is False

    def test_long_expected_no_fuzzy(self):
        long_expected = "a" * 31  # >30 chars, no fuzzy
        assert grade_answer(long_expected, long_expected, "short") is True
        assert grade_answer("something else entirely", long_expected, "short") is False

    def test_empty_expected_fail_closed(self):
        assert grade_answer("anything", "", "short") is False
        assert grade_answer("anything", "  ", "short") is False

    def test_empty_user_answer(self):
        assert grade_answer("", "expected", "short") is False

    def test_fail_closed_when_only_one_side_is_math(self):
        # Numeric expected answer vs prose user answer must not pass.
        assert grade_answer("half of one", "0.5", "short") is False

    def test_choice_unchanged(self):
        assert grade_answer("A", "A", "choice") is True
        assert grade_answer("b", "B", "choice") is True
        assert grade_answer("C", "A", "choice") is False

    def test_open_unchanged(self):
        expected = "cell membrane, nucleus, mitochondria"
        user = "The cell has a cell membrane and nucleus, with mitochondria for energy"
        assert grade_answer(user, expected, "open") is True

    def test_unknown_type_fail_closed(self):
        assert grade_answer("a", "a", "unknown") is False
