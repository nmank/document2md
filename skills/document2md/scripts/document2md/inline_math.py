"""Inline math located through the PDF text layer.

Docling flattens math inside a sentence into prose (``β1`` for β₁). In a
born-digital PDF the text layer still says which characters are math: they
use math fonts (CMMI, CMSY, CMEX, Cambria Math, ...) or are set smaller than
the surrounding prose. Runs of such characters are aligned with Docling's
paragraph text and replaced by formula sentinels, so the regular formula crop
workflow (``formulas.py``) handles them: simple spans get their LaTeX from the
glyphs directly, repeated spans share one transcription, and the rest are
cropped for a reader.

Docling's prose is never rewritten: only the aligned math spans change, and a
span that does not align well is left as Docling wrote it.
"""

from __future__ import annotations

import ctypes
import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .formulas import Formula, sentinel

STRONG_FONT = re.compile(r"^(CMMI|CMSY|CMEX|CMBSY|CMMIB|MSAM|MSBM|EUFM|EUSM|RSFS|Symbol|.*Math)", re.I)
CM_ROMAN_FONT = re.compile(r"^(CMR|CMBX|CMTI|CMSL|LMRoman)", re.I)
PROSE_FONT_MATH = set("=+<>±×()[]|/.,0123456789")
LEADING_JUNK = ",.;:)]"
TRAILING_JUNK = ",.;:(["
SCRIPT_RATIO = 0.85  # a glyph this much smaller than its neighbour is a sub/superscript
MIN_PARAGRAPH_RATIO = 0.85
MIN_SPAN_RATIO = 0.6
INLINE_PADDING = 1.5

GREEK = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ϵ": r"\epsilon", "ε": r"\varepsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ϑ": r"\vartheta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ϖ": r"\varpi", "ρ": r"\rho",
    "ϱ": r"\varrho", "σ": r"\sigma", "ς": r"\varsigma", "τ": r"\tau", "υ": r"\upsilon", "ϕ": r"\phi",
    "φ": r"\varphi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega", "Γ": r"\Gamma", "Δ": r"\Delta",
    "∆": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda", "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma",
    "Υ": r"\Upsilon", "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}


@dataclass(slots=True)
class Char:
    ch: str
    font: str
    size: float
    box: tuple[float, float, float, float]  # left, bottom, right, top in PDF points
    origin: tuple[float, float]  # baseline origin in PDF points
    page: int = 0  # 1-based page number, once read from a document


# --- Classification and spans -------------------------------------------------


def classify(chars: list[Char]) -> tuple[list[str], float] | None:
    """Kinds per char: S (math), W (math only next to S) or P (prose); plus body size.

    Returns None when the paragraph has no prose font to compare against.
    """
    prose = Counter(c.font for c in chars if c.ch.isalpha() and not STRONG_FONT.match(c.font))
    if not prose:
        return None
    prose_font = prose.most_common(1)[0][0]
    body = Counter(round(c.size * 2) / 2 for c in chars if c.font == prose_font and c.ch.isalpha()).most_common(1)[0][0]

    kinds = []
    for c in chars:
        if c.font == prose_font:
            kinds.append("W" if c.ch in PROSE_FONT_MATH else "P")
        elif STRONG_FONT.match(c.font) or c.size < SCRIPT_RATIO * body:
            kinds.append("S")
        elif CM_ROMAN_FONT.match(c.font):
            kinds.append("W")
        else:
            kinds.append("P")
    return kinds, body


def find_spans(chars: list[Char], kinds: list[str], body: float) -> list[tuple[int, int]]:
    """Maximal math runs with at least one S char, one line each, edges trimmed."""
    runs: list[tuple[int, int]] = []
    start = None
    for index, kind in enumerate(kinds):
        new_line = index > 0 and _new_line(chars[index - 1], chars[index], body)
        new_line = new_line or (index > 0 and chars[index - 1].page != chars[index].page)
        if start is not None and (kind == "P" or new_line):
            runs.append((start, index))
            start = None
        if kind != "P" and start is None:
            start = index
    if start is not None:
        runs.append((start, len(chars)))

    spans = []
    for begin, end in runs:
        begin, end = _trim(chars, kinds, begin, end)
        if begin < end and "S" in kinds[begin:end]:
            spans.append((begin, end))
    return spans


def _new_line(previous: Char, current: Char, body: float) -> bool:
    """Consecutive glyphs on different lines, in either direction.

    Box geometry rather than baselines, because big operators (CMEX) do not
    sit on the text baseline. Content streams can also jump back and forth
    between lines. A fraction's numerator and denominator, or an operator and
    its limits, stay together because they never sit 8 body widths apart.
    """
    vertical = abs((current.box[1] + current.box[3]) / 2 - (previous.box[1] + previous.box[3]) / 2)
    horizontal = abs(current.box[0] - previous.box[0])
    return vertical > 0.8 * body and horizontal > 8 * body


def _trim(chars: list[Char], kinds: list[str], begin: int, end: int) -> tuple[int, int]:
    """Drop prose punctuation and brackets that only frame the math."""
    pairs = {"(": ")", "[": "]"}
    while begin < end:
        text = "".join(c.ch for c in chars[begin:end])
        first, last = text[0], text[-1]
        if first in LEADING_JUNK:
            begin += 1
        elif last in TRAILING_JUNK:
            end -= 1
        elif first in pairs and _opens(text) > _closes(text):
            begin += 1
        elif last in pairs.values() and _closes(text) > _opens(text):
            end -= 1
        elif (
            first in pairs
            and last == pairs[first]
            and end - begin > 2
            and kinds[begin] == kinds[end - 1] == "W"
            and chars[begin].font == chars[end - 1].font
            and not STRONG_FONT.match(chars[begin].font)
            and _balanced("".join(c.ch for c in chars[begin + 1 : end - 1]))
            and _is_prose_bracket(chars, begin)
        ):
            # "(mt)" in running text: the parentheses are prose, the math is m_t.
            begin, end = begin + 1, end - 1
        else:
            break
    return begin, end


def _opens(text: str) -> int:
    return text.count("(") + text.count("[")


def _closes(text: str) -> int:
    return text.count(")") + text.count("]")


def _balanced(text: str) -> bool:
    """Round and square brackets count together, so half-open [0, 1) balances."""
    depth = 0
    for ch in text:
        depth += ch in "(["
        depth -= ch in ")]"
        if depth < 0:
            return False
    return depth == 0


def _is_prose_bracket(chars: list[Char], index: int) -> bool:
    """A bracket is prose when it uses the same font as the paragraph's letters around it."""
    letters = [c.font for c in chars if c.ch.isalpha() and not STRONG_FONT.match(c.font)]
    return bool(letters) and Counter(letters).most_common(1)[0][0] == chars[index].font


# --- Alignment with Docling's text -------------------------------------------


def _norm(ch: str) -> str:
    return unicodedata.normalize("NFKC", {"−": "-", "∗": "*"}.get(ch, ch))


def align(chars: list[Char], text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Map char spans to (text_start, text_end, char_start, char_end); drop doubtful ones."""
    pdf = [_norm(c.ch) for c in chars]
    positions = [k for k, ch in enumerate(text) if not ch.isspace()]
    docling = [_norm(text[k]) for k in positions]
    matcher = difflib.SequenceMatcher(None, pdf, docling, autojunk=False)
    if matcher.ratio() < MIN_PARAGRAPH_RATIO:
        return []
    mapping: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        for offset in range(i2 - i1):
            if tag == "equal":
                mapping[i1 + offset] = j1 + offset
            elif tag == "replace":
                mapping[i1 + offset] = min(j1 + offset * (j2 - j1) // (i2 - i1), j2 - 1)

    aligned = []
    last_end = 0
    for begin, end in spans:
        mapped = [mapping[k] for k in range(begin, end) if k in mapping]
        if not mapped:
            continue
        text_start, text_end = positions[min(mapped)], positions[max(mapped)] + 1
        text_start, text_end = _extend_over_accents(text, text_start, text_end)
        found = re.sub(r"\s+", "", text[text_start:text_end])
        expected = "".join(pdf[begin:end])
        if (
            text_start < last_end
            or not any(ch.isalnum() for ch in found)
            or difflib.SequenceMatcher(None, expected, "".join(_norm(ch) for ch in found)).ratio() < MIN_SPAN_RATIO
        ):
            continue
        aligned.append((text_start, text_end, begin, end))
        last_end = text_end
    return aligned


def _is_accent(ch: str) -> bool:
    return unicodedata.category(ch) in {"Mn", "Sk"}


def _extend_over_accents(text: str, start: int, end: int) -> tuple[int, int]:
    """Docling puts a detached hat before its letter (`` ̂mt``); keep it with the span."""
    while start > 0 and _is_accent(text[start - 1]):
        start -= 1
    while end < len(text) and _is_accent(text[end]):
        end += 1
    return start, end


# --- LaTeX for simple spans, and duplicate detection --------------------------


OPERATORS = {
    "=": "=", "+": "+", "-": "-", "−": "-", "<": "<", ">": ">", "≤": r"\leq", "≥": r"\geq",
    "≈": r"\approx", "≠": r"\neq", "≡": r"\equiv", "∼": r"\sim", "≃": r"\simeq", "∝": r"\propto",
    "⪅": r"\lessapprox", "⪆": r"\gtrapprox", "←": r"\leftarrow", "→": r"\rightarrow",
    "⇒": r"\Rightarrow", "↦": r"\mapsto", "·": r"\cdot", "×": r"\times", "±": r"\pm", "∓": r"\mp",
    "∈": r"\in", "∉": r"\notin", "⊂": r"\subset", "⊆": r"\subseteq", "∪": r"\cup", "∩": r"\cap",
    "⊙": r"\odot", "⊗": r"\otimes", "⊕": r"\oplus", "∘": r"\circ", "∞": r"\infty", "∇": r"\nabla",
    "∂": r"\partial", "∀": r"\forall", "∃": r"\exists", "|": "|", "‖": r"\|", "(": "(", ")": ")",
    "[": "[", "]": "]", "/": "/", ",": ",", ".": ".", "!": "!", "′": "'", "∗": "*", ":": ":",
}
SPACED = {"=", "<", ">", "+", "-"} | {v for v in OPERATORS.values() if v.startswith("\\") and v not in {r"\infty", r"\nabla", r"\partial", r"\|"}}


def _glyph(c: Char) -> str | None:
    """LaTeX for one glyph whose meaning its font makes unambiguous, else None."""
    font = c.font.upper()
    if font.startswith(("CMEX", "MSAM")):
        return None  # accents, radicals and big operators need their layout
    if c.ch in GREEK:
        return GREEK[c.ch]
    if c.ch.isdigit():
        return c.ch
    if c.ch.isascii() and c.ch.isalpha():
        if font.startswith(("CMMI", "CAMBRIAMATH", "LMMATH")) or font.endswith("MATH"):
            return c.ch
        if font.startswith("MSBM"):
            return rf"\mathbb{{{c.ch}}}"
        if font.startswith("CMSY") and c.ch.isupper():
            return rf"\mathcal{{{c.ch}}}"
        return None  # upright, bold or prose-font letters: their meaning varies
    return OPERATORS.get(c.ch)


def auto_latex(chars: list[Char]) -> str | None:
    """LaTeX for a span built only from glyphs with an unambiguous meaning, else None.

    Glyphs set smaller than the span's main size are attached to the preceding
    glyph as a subscript or superscript by their baseline. Anything needing
    two-dimensional layout (roots, fractions, accents, nested scripts) is left
    for a reader.
    """
    main = max(c.size for c in chars)
    atoms: list[list] = []  # [latex, char, subscript tokens, superscript tokens]
    script_sizes = set()
    for c in chars:
        token = _glyph(c)
        if token is None:
            return None
        if c.size >= SCRIPT_RATIO * main:
            atoms.append([token, c, [], []])
            continue
        if not atoms or atoms[-1][0] in SPACED or atoms[-1][0] in "([|,":
            return None
        shift = c.origin[1] - atoms[-1][1].origin[1]
        if abs(shift) < 0.3:
            return None
        script_sizes.add(round(c.size, 1))
        atoms[-1][3 if shift > 0 else 2].append(token)
    if len(script_sizes) > 1:
        return None  # scripts of scripts

    parts = []
    for token, _c, subscript, superscript in atoms:
        latex = token
        # A raised prime is written z'_j, not z^'_j (which KaTeX rejects).
        while superscript and superscript[0] == "'":
            latex += "'"
            superscript = superscript[1:]
        if token == "'" and parts:
            parts[-1] += "'"
            continue
        for marker, group in (("_", subscript), ("^", superscript)):
            if group:
                body = _join(group)
                latex += f"{marker}{body}" if len(body) == 1 else f"{marker}{{{body}}}"
        parts.append(latex)
    latex = _join(parts)
    return re.sub(r"\.\s*\.\s*\.", r"\\dots", latex)


def _join(tokens: list[str]) -> str:
    out = ""
    for token in tokens:
        if token == "=" and out.rstrip().endswith(":"):
            out = out.rstrip()[:-1].rstrip() + " := "  # := stays one symbol
        elif token in SPACED:
            out = out.rstrip() + f" {token} "
        elif out and re.search(r"\\[A-Za-z]+$", out) and token[:1].isalpha():
            out += " " + token
        elif token == ",":
            out = out.rstrip() + ", "
        else:
            out += token
    return re.sub(r"\s+", " ", out).strip()


def signature(chars: list[Char]) -> tuple:
    base_y = chars[0].origin[1]
    return tuple((c.ch, c.font, round(c.size, 1), round(c.origin[1] - base_y, 1)) for c in chars)


# --- Reading the PDF ---------------------------------------------------------


class InlineTagger:
    """Tags inline math in successive Docling batches of one PDF."""

    def __init__(self, pdf_bytes: bytes) -> None:
        import pypdfium2 as pdfium

        self.pdf = pdfium.PdfDocument(pdf_bytes)
        self.pages: dict[int, tuple[list[Char], float]] = {}
        self.seen: dict[tuple, Formula] = {}
        self.pages_without_text: list[int] = []

    def close(self) -> None:
        self.pdf.close()

    def page(self, page_number: int) -> tuple[list[Char], float]:
        if page_number not in self.pages:
            chars, height = _read_page(self.pdf[page_number - 1])
            for c in chars:
                c.page = page_number
            self.pages[page_number] = (chars, height)
            if not self.pages[page_number][0]:
                self.pages_without_text.append(page_number)
        return self.pages[page_number]

    def tag(self, document, start_index: int) -> list[Formula]:
        """Tag every paragraph of a Docling document."""
        return self._tag_items((item for item, _level in document.iterate_items()), start_index)

    def tag_item(self, item, start_index: int) -> list[Formula]:
        """Tag one Docling item; ids continue from start_index."""
        return self._tag_items([item], start_index)

    def _tag_items(self, items, start_index: int) -> list[Formula]:
        from docling_core.types.doc import CodeItem, FormulaItem, TextItem

        found: list[Formula] = []
        for item in items:
            # Code blocks render $...$ literally, so their math stays as Docling wrote it.
            if not isinstance(item, TextItem) or isinstance(item, (FormulaItem, CodeItem)) or not item.prov or not item.text:
                continue
            # A paragraph that continues past a display equation, a column or a
            # page has one provenance box per piece; read them all, in order.
            chars = []
            for prov in item.prov:
                page_chars, _height = self.page(prov.page_no)
                x0, y0, x1, y1 = prov.bbox.as_tuple()
                left, right, bottom, top = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
                chars.extend(
                    c for c in page_chars
                    if left <= (c.box[0] + c.box[2]) / 2 <= right and bottom <= (c.box[1] + c.box[3]) / 2 <= top
                )
            classified = classify(chars) if chars else None
            if classified is None:
                continue
            kinds, body = classified
            spans = find_spans(chars, kinds, body)
            if not spans:
                continue
            text = item.text
            replacements = []
            for text_start, text_end, begin, end in align(chars, text, spans):
                span = chars[begin:end]
                number = start_index + len(found) + 1
                formula = Formula(
                    f"eq-{number:04d}",
                    span[0].page,
                    _top_left_box(span, self.page(span[0].page)[1]),
                    text[text_start:text_end],
                    display=False,
                )
                key = signature(span)
                # The text layer can miss glyphs (an ⊙ that Docling's parser still sees);
                # read LaTeX off the glyphs only when both sources agree exactly.
                latex = auto_latex(span) if _same_glyphs(span, text[text_start:text_end]) else None
                if latex is not None:
                    formula.source, formula.latex = "auto", latex
                elif key in self.seen:
                    formula.source, formula.same_as = "same", self.seen[key].id
                else:
                    self.seen[key] = formula
                found.append(formula)
                replacements.append((text_start, text_end, sentinel(number)))
            for text_start, text_end, token in reversed(replacements):
                text = text[:text_start] + token + text[text_end:]
            item.text = text
        return found


def _same_glyphs(span: list[Char], docling_text: str) -> bool:
    return "".join(_norm(c.ch) for c in span) == "".join(_norm(ch) for ch in docling_text if not ch.isspace())


def _top_left_box(chars: list[Char], page_height: float) -> tuple[float, float, float, float]:
    left = min(c.box[0] for c in chars) - INLINE_PADDING
    right = max(c.box[2] for c in chars) + INLINE_PADDING
    bottom = min(c.box[1] for c in chars) - INLINE_PADDING
    top = max(c.box[3] for c in chars) + INLINE_PADDING
    return (left, page_height - top, right, page_height - bottom)


def _read_page(page) -> tuple[list[Char], float]:
    import pypdfium2.raw as pdfium_c

    textpage = page.get_textpage()
    chars: list[Char] = []
    buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    left, right, bottom, top = (ctypes.c_double() for _ in range(4))
    x, y = ctypes.c_double(), ctypes.c_double()
    try:
        for index in range(pdfium_c.FPDFText_CountChars(textpage.raw)):
            if pdfium_c.FPDFText_IsGenerated(textpage.raw, index) == 1:
                continue
            code = pdfium_c.FPDFText_GetUnicode(textpage.raw, index)
            if not code:
                continue
            ch = chr(code)
            if ch.isspace():
                continue
            pdfium_c.FPDFText_GetFontInfo(textpage.raw, index, buffer, len(buffer), ctypes.byref(flags))
            pdfium_c.FPDFText_GetCharBox(textpage.raw, index, left, right, bottom, top)
            pdfium_c.FPDFText_GetCharOrigin(textpage.raw, index, x, y)
            chars.append(
                Char(
                    ch,
                    buffer.value.decode("utf-8", "replace").split("+")[-1],
                    pdfium_c.FPDFText_GetFontSize(textpage.raw, index),
                    (left.value, bottom.value, right.value, top.value),
                    (x.value, y.value),
                )
            )
    finally:
        textpage.close()
    return chars, page.get_height()
