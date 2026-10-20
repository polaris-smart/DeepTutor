"""Shared text-window helpers for source-grounded reading extensions."""

from __future__ import annotations

import json
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deeptutor.reading.extensions import ReadingContext

MAX_GROUNDING_CONTEXT_CHARS = 6_000


def normalized_with_map(value: str) -> tuple[str, list[int]]:
    """Collapse whitespace while retaining an index for every output character."""
    normalized: list[str] = []
    source_positions: list[int] = []
    for index, character in enumerate(value):
        if character.isspace():
            if normalized and normalized[-1] != " ":
                normalized.append(" ")
                source_positions.append(index)
            continue
        normalized.append(character)
        source_positions.append(index)
    if normalized and normalized[-1] == " ":
        normalized.pop()
        source_positions.pop()
    return "".join(normalized), source_positions


def selection_range(text: str, selection: str) -> tuple[int, int] | None:
    if not selection:
        return None
    exact = text.find(selection)
    if exact >= 0:
        return exact, exact + len(selection)

    normalized_text, positions = normalized_with_map(text)
    normalized_selection, _ = normalized_with_map(selection)
    if not normalized_selection:
        return None
    found = normalized_text.find(normalized_selection)
    if found < 0 or found + len(normalized_selection) > len(positions):
        return None
    start = positions[found]
    end = positions[found + len(normalized_selection) - 1] + 1
    return start, end


_LINE_NUMBER = re.compile(r"^[ \t]*\d{1,4}[ \t]*$", re.MULTILINE)
_SPACING = re.compile(r"[\s\-\u00ad\u2010\u2011]+")

# Math materials reach the extractor as LaTeX source, and a model quoting a
# formula drifts in formatting that changes nothing the text says: it may
# switch delimiters ("\\(x\\)" against "$x$"), brace or unbrace a single
# superscript ("x^{2}" against "x^2"), write spacing commands the extractor
# drops, or answer in half-width punctuation where the page is full-width.
# Those are presentation, not quotation, so the key removes them on both
# sides before comparing — the quote itself still has to be there.
_MATH_DELIMITERS = re.compile(r"\$\$|\$|\\\(|\\\)|\\\[|\\\]")
_RENDER_ONLY_COMMANDS = re.compile(
    r"\\(?:left|right|displaystyle|limits|nolimits|quad|qquad|bigl|bigr"
    r"|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big|bigg|Bigg)(?![a-zA-Z])"
)
_STYLED_COMMANDS = re.compile(
    r"\\(?:mathrm|mathbf|mathit|mathcal|mathsf|mathtt|mathbb|mathfrak"
    r"|text|textrm|textbf|textit|textsf|texttt|operatorname)(?![a-zA-Z])"
)
_FRAC_COMMANDS = re.compile(r"\\(?:dfrac|tfrac|frac)(?![a-zA-Z])")
_BRACES = re.compile(r"[{}]")
_ANY_BACKSLASH = re.compile(r"\\")
_FULLWIDTH_PUNCTUATION = str.maketrans(
    {
        "\u3002": ".",  # 。
        "\u3001": ",",  # 、
        "\u201c": '"',
        "\u201d": '"',
        "\u2018": "'",
        "\u2019": "'",
    }
)
_SYMBOL_COMMANDS = {
    "\\alpha": "\u03b1",
    "\\beta": "\u03b2",
    "\\gamma": "\u03b3",
    "\\delta": "\u03b4",
    "\\epsilon": "\u03f5",
    "\\varepsilon": "\u03b5",
    "\\zeta": "\u03b6",
    "\\eta": "\u03b7",
    "\\theta": "\u03b8",
    "\\iota": "\u03b9",
    "\\kappa": "\u03ba",
    "\\lambda": "\u03bb",
    "\\mu": "\u03bc",
    "\\nu": "\u03bd",
    "\\xi": "\u03be",
    "\\pi": "\u03c0",
    "\\rho": "\u03c1",
    "\\sigma": "\u03c3",
    "\\tau": "\u03c4",
    "\\upsilon": "\u03c5",
    "\\phi": "\u03d5",
    "\\varphi": "\u03c6",
    "\\chi": "\u03c7",
    "\\psi": "\u03c8",
    "\\omega": "\u03c9",
    "\\Gamma": "\u0393",
    "\\Delta": "\u0394",
    "\\Theta": "\u0398",
    "\\Lambda": "\u039b",
    "\\Xi": "\u039e",
    "\\Pi": "\u03a0",
    "\\Sigma": "\u03a3",
    "\\Phi": "\u03a6",
    "\\Psi": "\u03a8",
    "\\Omega": "\u03a9",
    "\\times": "\u00d7",
    "\\cdot": "\u00b7",
    "\\div": "\u00f7",
    "\\pm": "\u00b1",
    "\\mp": "\u2213",
    "\\leq": "\u2264",
    "\\le": "\u2264",
    "\\geq": "\u2265",
    "\\ge": "\u2265",
    "\\neq": "\u2260",
    "\\ne": "\u2260",
    "\\approx": "\u2248",
    "\\infty": "\u221e",
    "\\rightarrow": "\u2192",
    "\\to": "\u2192",
    "\\degree": "\u00b0",
    "\\prime": "\u2032",
    "\\sum": "\u2211",
    "\\prod": "\u220f",
    "\\int": "\u222b",
    "\\sqrt": "\u221a",
}
_SYMBOL_COMMAND_PATTERN = re.compile(
    "|".join(
        re.escape(command)
        for command in sorted(_SYMBOL_COMMANDS, key=len, reverse=True)
    )
    + r"(?![a-zA-Z])"
)


def evidence_key(value: str) -> str:
    """A comparison form for "is this quote from the text?".

    Review copies and legal texts number their lines in the margin, and the
    extractor puts each number on a line of its own ("fall short in\n3\n
    delivering"). A model quoting the passage leaves them out, and it rejoins
    a word the line broke with a hyphen ("DeepTu-\n4\ntor"). Neither is a
    difference in what the text says, so the key drops number-only lines,
    spacing and hyphens before comparing.

    Math pages add a second layer of presentation drift — LaTeX delimiter
    and command spellings, braces around single tokens, full-width against
    half-width punctuation — which the key also folds away, identically on
    both sides. The semantic characters of the quote still have to appear,
    in order, in the source; only the spelling of the formula may differ.
    """
    text = unicodedata.normalize("NFKC", value).casefold()
    text = text.translate(_FULLWIDTH_PUNCTUATION)
    text = _LINE_NUMBER.sub(" ", text)
    text = _MATH_DELIMITERS.sub("", text)
    text = _RENDER_ONLY_COMMANDS.sub("", text)
    text = _STYLED_COMMANDS.sub("", text)
    text = _SYMBOL_COMMAND_PATTERN.sub(
        lambda match: _SYMBOL_COMMANDS[match.group(0)], text
    )
    text = _FRAC_COMMANDS.sub("/", text)
    text = _BRACES.sub("", text)
    # Whatever commands survive the tables above (\sin, \log, \overline, an
    # escaped "\%"...) lose their backslash, so their remaining letters are
    # compared like any other letters on the page.
    text = _ANY_BACKSLASH.sub("", text)
    return _SPACING.sub("", text)


def grounding_context(
    text: str,
    selection: str,
    *,
    max_chars: int = MAX_GROUNDING_CONTEXT_CHARS,
) -> str:
    """Return a bounded source window centered on the verified selection."""
    if max_chars <= 0:
        return ""
    bounds = selection_range(text, selection) if selection else None
    if bounds is None or len(text) <= max_chars:
        return text[:max_chars]
    start, end = bounds
    midpoint = (start + end) // 2
    window_start = max(0, midpoint - max_chars // 2)
    window_end = min(len(text), window_start + max_chars)
    window_start = max(0, window_end - max_chars)
    return text[window_start:window_end]


def grounded_prompt(context: ReadingContext) -> str:
    """The user-turn payload every source-grounded reading extension sends.

    All four of them ask the same question of the same two fields — the
    selection, and a bounded window of the page around it — so they ask it
    in one place.
    """
    return json.dumps(
        {
            "selection": context.selection,
            "surrounding_context": grounding_context(
                context.visible_text,
                context.selection,
            ),
        },
        ensure_ascii=False,
    )


__all__ = [
    "MAX_GROUNDING_CONTEXT_CHARS",
    "evidence_key",
    "grounded_prompt",
    "grounding_context",
    "normalized_with_map",
    "selection_range",
]
