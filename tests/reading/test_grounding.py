from deeptutor.reading._grounding import evidence_key, grounding_context, selection_range


def test_selection_range_maps_collapsed_whitespace_back_to_source() -> None:
    text = "before\n\tverified   phrase after"

    bounds = selection_range(text, "verified phrase")

    assert bounds is not None
    assert text[bounds[0] : bounds[1]] == "verified   phrase"


def test_grounding_context_keeps_a_late_selection_inside_the_bound() -> None:
    text = "prefix " * 2_000 + "verified phrase" + " suffix" * 2_000

    context = grounding_context(text, "verified phrase")

    assert len(context) == 6_000
    assert "verified phrase" in context


def test_evidence_key_keeps_margin_number_rejoin_and_casefold() -> None:
    assert evidence_key("DeepTu-\n4\ntor is open") == evidence_key(
        "deeptutor is  open"
    )


def test_evidence_key_matches_math_delimiter_drift() -> None:
    page = "设函数 \\(f(x) = x^{2} + 3x\\)，求 \\(f'(x)\\) 的最小值。"

    assert evidence_key(page) == evidence_key(
        "设函数 $f(x) = x^{2} + 3x$，求 $f'(x)$ 的最小值。"
    )
    assert evidence_key("f(x) = x^{2} + 3x") in evidence_key(page)


def test_evidence_key_matches_superscript_brace_drift() -> None:
    assert evidence_key("x^2 + y^{2} = r^2") == evidence_key("x^{2} + y^2 = r^{2}")


def test_evidence_key_keeps_multitoken_exponent_grouping() -> None:
    """A multi-token exponent groups its tokens: "2^{n+1}" is a different
    formula from "2^n+1", so brace folding may not erase the distinction —
    while single-token braces and command arguments keep folding away."""
    assert evidence_key("2^{n+1}") != evidence_key("2^n+1")
    assert evidence_key("x^{23}") != evidence_key("x^23")
    assert evidence_key("a_{12}") != evidence_key("a_12")
    assert evidence_key("x^2") == evidence_key("x^{2}")
    assert evidence_key(r"\frac{a}{b} + \mathrm{PV} = nRT") == evidence_key("/ab + PV = nRT")
    assert evidence_key("所以序列 2^{n+1} 收敛") in evidence_key("证明：所以序列 2^{n+1} 收敛。")


def test_evidence_key_matches_render_only_commands_and_styling() -> None:
    page = r"\[ \left( \dfrac{a}{b} \right) \cdot \alpha \]"
    quote = r"$(\frac{a}{b}) \cdot \alpha$"

    assert evidence_key(quote) in evidence_key(page)


def test_evidence_key_matches_rendered_symbol_spellings() -> None:
    page = r"当 \(a \geq 0\) 且 \(a \neq 1\) 时，\(a \times b\)"

    assert evidence_key("当 $a\\ge 0$ 且 $a \\ne 1$ 时，$a\\times b$") in evidence_key(
        page
    )
    assert evidence_key("当 a ≥ 0 且 a ≠ 1 时，a × b") in evidence_key(page)


def test_evidence_key_matches_fullwidth_against_halfwidth() -> None:
    page = "已知 a=2，b=3。求 c。"

    assert evidence_key("已知 a=2, b=3. 求 c.") in evidence_key(page)
    assert evidence_key("已知 a=2，b=3。求 c。") in evidence_key(page)


def test_evidence_key_matches_displaystyle_and_mathrm_content() -> None:
    page = r"\[ \displaystyle \mathrm{PV} = nRT \]"

    assert evidence_key("PV = nRT") in evidence_key(page)


def test_evidence_key_still_rejects_fabricated_math() -> None:
    page = "设函数 \\(f(x) = x^{2} + 3x\\)，求 \\(f'(x)\\) 的最小值。"

    assert evidence_key("f(x) = x^{2} + 5x") not in evidence_key(page)
    assert evidence_key("f(x) = x^{2} + 3x + 1") not in evidence_key(page)
    assert evidence_key("g(x) = x^{2} + 3x") not in evidence_key(page)
    assert evidence_key("f(x) = 3x + x^{2}") not in evidence_key(page)
