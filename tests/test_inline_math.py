"""Inline math found through the PDF text layer.

Classification, spans, alignment and automatic LaTeX are tested on synthetic
characters; the tagger is tested end to end on a generated PDF whose text
layer uses Times-Roman for prose and Symbol for Greek.
"""

from __future__ import annotations

import ctypes
import json
from io import BytesIO
from pathlib import Path

import pytest

docling_doc = pytest.importorskip("docling_core.types.doc")

from document2md import formulas
from document2md.inline_math import Char, InlineTagger, align, auto_latex, classify, find_spans

TIMES, CMR, CMR7, CMMI, CMMI7, CMSY, CMEX = "NimbusRomNo9L-Regu", "CMR10", "CMR7", "CMMI10", "CMMI7", "CMSY10", "CMEX10"
SMALL = {CMR7: 7.0, CMMI7: 7.0, "CMSY7": 7.0}


def chars(*runs) -> list[Char]:
    """Runs of (text, font) or (text, font, baseline shift); spaces only advance x."""
    out, x = [], 100.0
    for run in runs:
        text, font, shift = (*run, 0.0) if len(run) == 2 else run
        size = SMALL.get(font, 10.0)
        for ch in text:
            if ch != " ":
                out.append(Char(ch, font, size, (x, 700 + shift, x + size * 0.5, 700 + shift + size * 0.7), (x, 700 + shift)))
            x += size * 0.5
    return out


def spans_of(cs: list[Char]) -> list[str]:
    kinds, body = classify(cs)
    return ["".join(c.ch for c in cs[a:b]) for a, b in find_spans(cs, kinds, body)]


# --- Spans ------------------------------------------------------------------


def test_math_fonts_in_times_prose_form_spans() -> None:
    cs = chars(("When ", TIMES), ("(1", CMR), ("−", CMSY), ("β", CMMI), ("1", CMR7, -1.5), (") = ", CMR), ("α", CMMI), (" we have", TIMES))

    assert spans_of(cs) == ["(1−β1)=α"]


def test_all_computer_modern_paper_keeps_operators_and_numbers() -> None:
    cs = chars(("Set ", CMR), ("β", CMMI), ("1", CMR7, -1.5), (" = 0.9, and go", CMR))

    assert spans_of(cs) == ["β1=0.9"]


def test_ordinals_and_prose_parentheses_are_not_math() -> None:
    cs = chars(("the 1", TIMES), ("st", TIMES, 3.0), (" moment (", TIMES), ("m", CMMI), ("t", CMMI7, -1.5), (") is", TIMES))
    for c in cs:
        if c.ch in "st" and c.origin[1] > 700:
            c.size = 7.0

    assert spans_of(cs) == ["mt"]


def test_half_open_intervals_keep_their_bracket() -> None:
    cs = chars(("β", CMMI), (" ∈ ", CMSY), ("[0", CMR), (",", CMMI), (" 1)", CMR), (": decay rates", TIMES))

    assert spans_of(cs) == ["β∈[0,1)"]


def test_spans_split_at_line_breaks() -> None:
    first = chars(("x", CMMI), (" + ", CMR), ("y", CMMI))
    for c in first:  # end of a line, far right
        c.origin = (c.origin[0] + 300, c.origin[1])
        c.box = (c.box[0] + 300, c.box[1], c.box[2] + 300, c.box[3])
    second = chars(("z", CMMI))
    for c in second:  # next line, back at the left margin
        c.origin = (100.0, 686.0)
        c.box = (100.0, 686.0, 105.0, 693.0)
    cs = first + second + chars((" are", TIMES))

    assert spans_of(cs) == ["x+y", "z"]


def test_inline_fractions_are_not_mistaken_for_line_breaks() -> None:
    numerator = chars(("1", CMR7, 3.5))
    denominator = chars(("n", CMMI7, -3.0))
    for c in denominator:  # stacked under the numerator, a little to the left
        c.origin = (c.origin[0] - 3, c.origin[1])
        c.box = (c.box[0] - 3, 694.0, c.box[2] - 3, 697.0)
    cs = chars(("so ", TIMES)) + numerator + denominator + chars((" of", TIMES))

    assert spans_of(cs) == ["1n"]


# --- Alignment with Docling's text -------------------------------------------


def test_alignment_takes_docling_detached_hats_into_the_span() -> None:
    cs = chars(("we have ", TIMES), ("m", CMMI), ("b", CMEX), ("t", CMMI7, -1.5), (" here", TIMES))
    kinds, body = classify(cs)
    text = "we have ̂mt here"

    [(start, end, _a, _b)] = align(cs, text, find_spans(cs, kinds, body))

    assert text[start:end] == "̂mt"


def test_spans_docling_lost_are_left_alone() -> None:
    cs = chars(("so that ", TIMES), ("x", CMMI), ("2", CMR7, 3.0), (" holds for all inputs", TIMES))
    kinds, body = classify(cs)

    assert align(cs, "so that holds for all inputs", find_spans(cs, kinds, body)) == []


def test_paragraphs_that_do_not_align_are_skipped() -> None:
    cs = chars(("so that ", TIMES), ("x", CMMI), (" holds", TIMES))
    kinds, body = classify(cs)

    assert align(cs, "an entirely different paragraph", find_spans(cs, kinds, body)) == []


# --- Automatic LaTeX ---------------------------------------------------------


@pytest.mark.parametrize(
    ("runs", "latex"),
    [
        ([("β", CMMI), ("1", CMR7, -1.5)], r"\beta_1"),
        ([("β", CMMI), ("t", CMMI7, 3.0), ("2", CMR7, -1.5)], r"\beta_2^t"),
        ([("α", CMMI), (" = 0.001", CMR)], r"\alpha = 0.001"),
        ([("θ", CMMI), ("t−1", CMMI7, -1.5)], r"\theta_{t - 1}"),
        ([("∇", CMSY), ("θ", CMMI7, -1.5), ("f", CMMI), ("t", CMMI7, -1.5), ("(", CMR), ("θ", CMMI), (")", CMR)], r"\nabla_{\theta}f_t(\theta)"),
        ([("E", "MSBM10"), ("[", CMR), ("v", CMMI), ("t", CMMI7, -1.5), ("]", CMR)], r"\mathbb{E}[v_t]"),
        ([("g", CMMI), ("1", CMR7, -1.5), (", ..., ", CMMI), ("g", CMMI), ("T", CMMI7, -1.5)], r"g_1, \dots, g_T"),
        ([("z", CMMI), ("′", "CMSY7", 3.0), ("j", CMMI7, -1.5)], r"z'_j"),
        ([("T", CMMI), ("′", CMSY)], r"T'"),
        ([("C", CMMI), ("x", CMMI7, -1.5), (":=", CMR), ("S", CMMI)], r"C_x := S"),
    ],
)
def test_auto_latex_reads_unambiguous_glyphs(runs, latex) -> None:
    assert auto_latex(chars(*runs)) == latex


@pytest.mark.parametrize(
    "runs",
    [
        [("m", CMMI), ("b", CMEX), ("t", CMMI7, -1.5)],  # hat accent
        [("√", CMSY), ("x", CMMI)],  # radical needs its extent
        [("d", CMR), ("x", CMMI)],  # upright letter
        [("x", CMMI), (" = ", CMR), ("2", CMR7, 3.0)],  # script after an operator
    ],
)
def test_auto_latex_leaves_layout_dependent_math_to_a_reader(runs) -> None:
    assert auto_latex(chars(*runs)) is None


# --- End to end on a generated PDF --------------------------------------------


def _pdf_with_text(path: Path, runs: list[tuple[str, str, float, float, float]]) -> None:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    document = pdfium.PdfDocument.new()
    page = document.new_page(612, 792)
    for text, font, size, x, y in runs:
        obj = raw.FPDFPageObj_NewTextObj(document.raw, font.encode(), size)
        wide = (text + "\0").encode("utf-16-le")
        raw.FPDFText_SetText(obj, ctypes.cast(ctypes.c_char_p(wide), ctypes.POINTER(raw.FPDF_WCHAR)))
        raw.FPDFPageObj_Transform(obj, 1, 0, 0, 1, x, y)
        raw.FPDFPage_InsertObject(page.raw, obj)
    raw.FPDFPage_GenerateContent(page.raw)
    buffer = BytesIO()
    document.save(buffer)
    path.write_bytes(buffer.getvalue())


def _paragraph_document(text: str, code: bool = False):
    document = docling_doc.DoclingDocument(name="test")
    document.add_page(page_no=1, size=docling_doc.Size(width=612, height=792))
    bbox = docling_doc.BoundingBox(l=90, t=720, r=400, b=690, coord_origin=docling_doc.CoordOrigin.BOTTOMLEFT)
    prov = docling_doc.ProvenanceItem(page_no=1, bbox=bbox, charspan=(0, len(text)))
    if code:
        document.add_code(text=text, prov=prov)
    else:
        document.add_text(label=docling_doc.DocItemLabel.TEXT, text=text, prov=prov)
    return document


RUNS = [
    ("Let", "Times-Roman", 10, 100, 700),
    ("β", "Symbol", 10, 118, 700),
    ("1", "Times-Roman", 7, 124, 698),
    ("be fixed and", "Times-Roman", 10, 130, 700),
    ("β", "Symbol", 10, 186, 700),
    ("1", "Times-Roman", 7, 192, 698),
    ("small.", "Times-Roman", 10, 198, 700),
]


def test_tagger_fills_simple_inline_math_and_keeps_the_prose(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    _pdf_with_text(source, RUNS)
    document = _paragraph_document("Let β1 be fixed and β1 small.")
    tagger = InlineTagger(source.read_bytes())

    tagged = tagger.tag(document, 0)
    tagger.close()
    markdown, files, warnings = formulas.finish(source, document.export_to_markdown(), tagged, tmp_path / "paper.md")

    assert markdown.strip() == r"Let $\beta_1$ be fixed and $\beta_1$ small."
    assert [(f.source, f.latex) for f in tagged] == [("auto", r"\beta_1"), ("auto", r"\beta_1")]
    assert [path.name for path in files] == ["formulas.json"]  # nothing left to crop
    assert warnings == ["2 formulas filled from the text layer; none need transcription."]


# Two adjacent math tokens, as in "g_t ⊙ g_t" where the text layer lost the ⊙.
PAIR_RUNS = [
    ("Let", "Times-Roman", 10, 100, 700),
    ("β", "Symbol", 10, 118, 700),
    ("1", "Times-Roman", 7, 124, 698),
    ("β", "Symbol", 10, 136, 700),
    ("2", "Times-Roman", 7, 142, 698),
    ("be fixed and", "Times-Roman", 10, 150, 700),
    ("β", "Symbol", 10, 206, 700),
    ("1", "Times-Roman", 7, 212, 698),
    ("β", "Symbol", 10, 224, 700),
    ("2", "Times-Roman", 7, 230, 698),
    ("small.", "Times-Roman", 10, 238, 700),
]


def test_glyphs_missing_from_the_text_layer_send_the_span_to_a_reader(tmp_path: Path) -> None:
    # Docling's parser sees an ⊙ the text layer lacks: reading LaTeX off the glyphs would drop it.
    source = tmp_path / "paper.pdf"
    _pdf_with_text(source, PAIR_RUNS)
    document = _paragraph_document("Let β1 ⊙ β2 be fixed and β1β2 small.")
    tagger = InlineTagger(source.read_bytes())

    tagged = tagger.tag(document, 0)
    tagger.close()

    assert [(f.source, f.hint, f.latex) for f in tagged] == [
        ("crop", "β1 ⊙ β2", None),
        ("auto", "β1β2", r"\beta_1\beta_2"),
    ]


def test_repeated_spans_share_one_transcription(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    _pdf_with_text(source, PAIR_RUNS)
    document = _paragraph_document("Let β1 ⊙ β2 be fixed and β1 ⊙ β2 small.")
    tagger = InlineTagger(source.read_bytes())

    tagged = tagger.tag(document, 0)
    tagger.close()
    output = tmp_path / "paper.md"
    markdown, files, _warnings = formulas.finish(source, document.export_to_markdown(), tagged, output)
    output.write_text(markdown, encoding="utf-8")
    manifest_path = tmp_path / "paper_formulas" / "formulas.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert [(e["source"], e["same_as"]) for e in manifest] == [("crop", None), ("same", "eq-0001")]
    assert [path.name for path in files] == ["eq-0001.png", "sheet-01.png", "formulas.json"]
    manifest[0]["latex"] = r"\beta_1 \odot \beta_2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert formulas.fill(output) == (["eq-0001", "eq-0002"], [])
    assert output.read_text(encoding="utf-8").count(r"$\beta_1 \odot \beta_2$") == 2


def test_code_blocks_are_left_alone(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    _pdf_with_text(source, RUNS)
    document = _paragraph_document("Let β1 be fixed and β1 small.", code=True)
    tagger = InlineTagger(source.read_bytes())

    assert tagger.tag(document, 0) == []
    tagger.close()


def test_pages_without_a_text_layer_are_reported(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    _pdf_with_text(source, [])
    tagger = InlineTagger(source.read_bytes())

    tagger.tag(_paragraph_document("A scanned β1."), 0)
    tagger.close()

    assert tagger.pages_without_text == [1]


def test_display_and_inline_ids_follow_document_order(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    _pdf_with_text(source, RUNS)
    document = docling_doc.DoclingDocument(name="test")
    document.add_page(page_no=1, size=docling_doc.Size(width=612, height=792))
    paragraph_box = docling_doc.BoundingBox(l=90, t=720, r=400, b=690, coord_origin=docling_doc.CoordOrigin.BOTTOMLEFT)
    formula_box = docling_doc.BoundingBox(l=90, t=600, r=400, b=560, coord_origin=docling_doc.CoordOrigin.BOTTOMLEFT)
    document.add_text(
        label=docling_doc.DocItemLabel.TEXT,
        text="Let β1 be fixed and β1 small.",
        prov=docling_doc.ProvenanceItem(page_no=1, bbox=paragraph_box, charspan=(0, 0)),
    )
    document.add_formula(text="", orig="x = y", prov=docling_doc.ProvenanceItem(page_no=1, bbox=formula_box, charspan=(0, 0)))
    tagger = InlineTagger(source.read_bytes())

    tagged = formulas.tag_formulas(document, 0, tagger)
    tagger.close()

    assert [(f.id, f.display, f.hint) for f in tagged] == [
        ("eq-0001", False, "β1"),
        ("eq-0002", False, "β1"),
        ("eq-0003", True, "x = y"),
    ]


def test_glyphs_jumping_back_up_a_line_split_the_span() -> None:
    # A content stream that draws the next line's ∑ before the current line's last symbol.
    lower_sum = chars(("P", CMEX))
    for c in lower_sum:
        c.origin, c.box = (92.0, 680.0), (92.0, 678.0, 102.0, 688.0)
    end_of_line = chars(("Z", CMSY))
    for c in end_of_line:
        c.origin, c.box = (267.0, 700.0), (267.0, 700.0, 274.0, 707.0)
    cs = chars(("in a space ", TIMES)) + lower_sum + end_of_line + chars((". We", TIMES))

    assert spans_of(cs) == ["P", "Z"]


def test_paragraphs_with_several_boxes_are_read_in_full(tmp_path: Path) -> None:
    # Docling gives a paragraph interrupted by a display equation one box per piece.
    source = tmp_path / "paper.pdf"
    _pdf_with_text(source, RUNS + [("so", "Times-Roman", 10, 100, 600), ("β", "Symbol", 10, 112, 600), ("2", "Times-Roman", 7, 118, 598)])
    document = docling_doc.DoclingDocument(name="test")
    document.add_page(page_no=1, size=docling_doc.Size(width=612, height=792))
    boxes = [(90, 720, 400, 690), (90, 620, 400, 590)]
    item = document.add_text(
        label=docling_doc.DocItemLabel.TEXT,
        text="Let β1 be fixed and β1 small. so β2",
        prov=docling_doc.ProvenanceItem(page_no=1, bbox=docling_doc.BoundingBox(l=90, t=720, r=400, b=690, coord_origin=docling_doc.CoordOrigin.BOTTOMLEFT), charspan=(0, 0)),
    )
    item.prov.append(docling_doc.ProvenanceItem(page_no=1, bbox=docling_doc.BoundingBox(l=90, t=620, r=400, b=590, coord_origin=docling_doc.CoordOrigin.BOTTOMLEFT), charspan=(0, 0)))
    tagger = InlineTagger(source.read_bytes())

    tagged = tagger.tag(document, 0)
    tagger.close()

    assert [f.latex for f in tagged] == [r"\beta_1", r"\beta_1", r"\beta_2"]
