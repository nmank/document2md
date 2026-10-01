"""Formula crops: placeholders, crops, manifest, and filling.

Documents are real docling_core objects built by hand, so the sentinel's trip
through Docling's own Markdown serializer is exercised without any models.
"""

from __future__ import annotations

import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

docling_doc = pytest.importorskip("docling_core.types.doc")

from document2md import converter as converter_module
from document2md import formulas
from document2md.converter import ConversionOptions, convert_file

ROOT = Path(__file__).resolve().parents[1]
SKILL_SCRIPT = ROOT / "skills" / "document2md" / "scripts" / "convert_document.py"
PAGE = (612.0, 792.0)


def _prov(page_no: int, left: float, bottom: float, right: float, top: float):
    bbox = docling_doc.BoundingBox(l=left, t=top, r=right, b=bottom, coord_origin=docling_doc.CoordOrigin.BOTTOMLEFT)
    return docling_doc.ProvenanceItem(page_no=page_no, bbox=bbox, charspan=(0, 0))


def _document(pages: list[int]):
    """Per page: prose, an undecoded display formula, a decoded one, and an undecoded inline one."""
    document = docling_doc.DoclingDocument(name="test")
    for page in pages:
        document.add_page(page_no=page, size=docling_doc.Size(width=PAGE[0], height=PAGE[1]))
        document.add_text(label=docling_doc.DocItemLabel.TEXT, text=f"Prose on page {page}.")
        document.add_formula(text="", orig=f"vt = sum {page}", prov=_prov(page, 100, 600, 400, 640))
        document.add_formula(text="a^2 + b^2", orig="a2 + b2", prov=_prov(page, 100, 500, 400, 540))
        group = document.add_inline_group()
        document.add_text(label=docling_doc.DocItemLabel.TEXT, text="where", parent=group)
        document.add_formula(text="", orig="x i", prov=_prov(page, 100, 400, 140, 412), parent=group)
        document.add_text(label=docling_doc.DocItemLabel.TEXT, text="is given.", parent=group)
    return document


def _write_pdf(path: Path, pages: int) -> None:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(*PAGE)
    buffer = BytesIO()
    document.save(buffer)
    document.close()
    path.write_bytes(buffer.getvalue())


# --- Tagging and placeholders -----------------------------------------------


def test_only_undecoded_formulas_are_tagged_with_global_ids() -> None:
    first, second = _document([1]), _document([2])

    tagged = formulas.tag_formulas(first, 0)
    tagged += formulas.tag_formulas(second, len(tagged))

    assert [(f.id, f.page, f.hint) for f in tagged] == [
        ("eq-0001", 1, "vt = sum 1"),
        ("eq-0002", 1, "x i"),
        ("eq-0003", 2, "vt = sum 2"),
        ("eq-0004", 2, "x i"),
    ]
    # Top-left origin: a box from y=600..640 (bottom-left) on a 792pt page.
    assert tagged[0].bbox == (100, 152, 400, 192)


def test_placeholders_record_display_and_inline_from_the_serializer() -> None:
    document = _document([3])
    tagged = formulas.tag_formulas(document, 0)

    markdown, placed = formulas.place_placeholders(document.export_to_markdown(), tagged)

    assert "<!-- formula eq-0001 (display, page 3) -->" in markdown
    assert "where <!-- formula eq-0002 (inline, page 3) --> is given." in markdown
    assert "$$a^2 + b^2$$" in markdown  # decoded formulas are untouched
    assert "D2MEQ" not in markdown and "formula-not-decoded" not in markdown
    assert [(f.id, f.display) for f in placed] == [("eq-0001", True), ("eq-0002", False)]


def test_formulas_the_serializer_drops_are_left_out_of_the_manifest() -> None:
    document = _document([1])
    tagged = formulas.tag_formulas(document, 0)

    _markdown, placed = formulas.place_placeholders("Only $$D2MEQ0001$$ survived.", tagged)

    assert [f.id for f in placed] == ["eq-0001"]


# --- Crops and sheets -------------------------------------------------------


def _formula(number: int, page: int, bbox) -> formulas.Formula:
    return formulas.Formula(f"eq-{number:04d}", page, bbox, "hint")


def test_crops_cover_the_padded_box_at_render_scale(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    _write_pdf(source, 2)
    tagged = [_formula(1, 2, (100, 152, 400, 192))]

    files = formulas.crop_formulas(source, tagged, tmp_path / "out")

    crop = Image.open(tmp_path / "out" / "eq-0001.png")
    pad = formulas.PADDING * formulas.RENDER_SCALE
    assert crop.size == (300 * formulas.RENDER_SCALE + 2 * pad, 40 * formulas.RENDER_SCALE + 2 * pad)
    assert [path.name for path in files] == ["eq-0001.png", "sheet-01.png"]
    assert tagged[0].image == "eq-0001.png" and tagged[0].sheet == "sheet-01.png"


def test_neighbouring_formulas_are_blanked_but_the_own_box_is_kept(tmp_path: Path) -> None:
    import pypdfium2 as pdfium

    # A page that is black everywhere: any white pixel in the crop was blanked.
    document = pdfium.PdfDocument.new()
    page = document.new_page(*PAGE)
    rectangle = pdfium.raw.FPDFPageObj_CreateNewRect(0, 0, PAGE[0], PAGE[1])
    pdfium.raw.FPDFPageObj_SetFillColor(rectangle, 0, 0, 0, 255)
    pdfium.raw.FPDFPath_SetDrawMode(rectangle, pdfium.raw.FPDF_FILLMODE_ALTERNATE, False)
    pdfium.raw.FPDFPage_InsertObject(page.raw, rectangle)
    pdfium.raw.FPDFPage_GenerateContent(page.raw)
    buffer = BytesIO()
    document.save(buffer)
    source = tmp_path / "black.pdf"
    source.write_bytes(buffer.getvalue())

    own = _formula(1, 1, (100, 100, 400, 140))
    below = _formula(2, 1, (100, 138, 400, 180))  # overlaps own box by 2pt
    formulas.crop_formulas(source, [own, below], tmp_path / "out")

    crop = Image.open(tmp_path / "out" / "eq-0001.png").convert("L")
    scale, pad = formulas.RENDER_SCALE, formulas.PADDING * formulas.RENDER_SCALE
    assert crop.getpixel((pad + 10, pad + 39 * scale)) < 50  # inside own box, even where boxes overlap
    assert crop.getpixel((pad + 10, crop.height - 2)) > 200  # padding reaching into the neighbour is blank


def test_sheets_hold_at_most_eight_crops_and_stay_readable(tmp_path: Path) -> None:
    source = tmp_path / "doc.pdf"
    _write_pdf(source, 1)
    tagged = [_formula(n, 1, (50, 20 + 30 * n, 550, 40 + 30 * n)) for n in range(1, 11)]

    files = formulas.crop_formulas(source, tagged, tmp_path / "out")

    sheets = [Image.open(path) for path in files if path.name.startswith("sheet-")]
    assert len(sheets) == 2
    assert all(max(sheet.size) <= formulas.SHEET_MAX_SIZE for sheet in sheets)
    assert [f.sheet for f in tagged] == ["sheet-01.png"] * 8 + ["sheet-02.png"] * 2


# --- Conversion -------------------------------------------------------------


def _install_fake_docling(monkeypatch, pages: int) -> list:
    calls = []

    class FakeConverter:
        def convert(self, stream, *, page_range):
            calls.append(page_range)
            return SimpleNamespace(document=_document(list(range(page_range[0], page_range[1] + 1))))

    monkeypatch.setattr(
        converter_module,
        "_create_pdf_converter",
        lambda options: (FakeConverter(), docling_doc.ImageRefMode),
    )
    monkeypatch.setattr(converter_module, "_pdf_page_count", lambda path: pages)
    return calls


def test_crop_mode_numbers_formulas_across_page_batches(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "paper.pdf"
    _write_pdf(source, 3)
    calls = _install_fake_docling(monkeypatch, 3)

    result = convert_file(source, tmp_path / "paper.md", ConversionOptions(formulas="crop", pdf_pages_per_batch=2))

    markdown = result.output_path.read_text(encoding="utf-8")
    assert calls == [(1, 2), (3, 3)]
    assert [line for line in markdown.splitlines() if "(display" in line] == [
        "<!-- formula eq-0001 (display, page 1) -->",
        "<!-- formula eq-0003 (display, page 2) -->",
        "<!-- formula eq-0005 (display, page 3) -->",
    ]
    manifest = json.loads((tmp_path / "paper_formulas" / "formulas.json").read_text(encoding="utf-8"))
    assert [entry["id"] for entry in manifest] == [f"eq-{n:04d}" for n in range(1, 7)]
    assert all(entry["latex"] is None for entry in manifest)
    assert (tmp_path / "paper_formulas" / "eq-0006.png") in result.assets
    assert result.warnings == [
        f"6 formulas need LaTeX: see {tmp_path / 'paper_formulas' / 'formulas.json'}, then run --fill-formulas."
    ]


def test_off_mode_output_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "paper.pdf"
    _write_pdf(source, 1)
    _install_fake_docling(monkeypatch, 1)

    result = convert_file(source, tmp_path / "paper.md", ConversionOptions())

    assert result.output_path.read_text(encoding="utf-8") == _document([1]).export_to_markdown().rstrip() + "\n"
    assert not (tmp_path / "paper_formulas").exists()


def test_formula_mode_is_validated(tmp_path: Path) -> None:
    source = tmp_path / "paper.pdf"
    _write_pdf(source, 1)
    with pytest.raises(ValueError, match="formulas"):
        convert_file(source, options=ConversionOptions(formulas="ocr"))


# --- Filling ----------------------------------------------------------------


def _converted(tmp_path: Path, entries: list[dict]) -> Path:
    markdown = tmp_path / "paper.md"
    markdown.write_text(
        "Intro.\n\n<!-- formula eq-0001 (display, page 1) -->\n\n"
        "where <!-- formula eq-0002 (inline, page 1) --> holds.\n\n"
        "<!-- formula eq-0003 (display, page 2) -->\n",
        encoding="utf-8",
    )
    (tmp_path / "paper_formulas").mkdir()
    (tmp_path / "paper_formulas" / "formulas.json").write_text(json.dumps(entries), encoding="utf-8")
    return markdown


def test_fill_writes_display_and_inline_math_and_reports_the_rest(tmp_path: Path) -> None:
    markdown = _converted(
        tmp_path,
        [
            {"id": "eq-0001", "latex": "v_t = \\sum_{i=1}^t g_i \\tag{1}"},
            {"id": "eq-0002", "latex": "\\(x_i\n\\)"},
            {"id": "eq-0003", "latex": None},
        ],
    )

    filled, missing = formulas.fill(markdown)

    assert (filled, missing) == (["eq-0001", "eq-0002"], ["eq-0003"])
    assert markdown.read_text(encoding="utf-8") == (
        "Intro.\n\n$$\nv_t = \\sum_{i=1}^t g_i \\tag{1}\n$$\n\n"
        "where $x_i$ holds.\n\n"
        "<!-- formula eq-0003 (display, page 2) -->\n"
    )


def test_fill_can_be_rerun_as_more_latex_arrives(tmp_path: Path) -> None:
    markdown = _converted(tmp_path, [{"id": "eq-0001", "latex": "$$a$$"}])
    formulas.fill(markdown)
    manifest = tmp_path / "paper_formulas" / "formulas.json"
    manifest.write_text(json.dumps([{"id": "eq-0001", "latex": "a"}, {"id": "eq-0002", "latex": "b"}, {"id": "eq-0003", "latex": "c"}]))

    assert formulas.fill(markdown) == (["eq-0002", "eq-0003"], [])
    assert formulas.fill(markdown) == ([], [])
    assert markdown.read_text(encoding="utf-8").count("$$\na\n$$") == 1


def test_cli_fill_exits_non_zero_while_formulas_are_missing(tmp_path: Path) -> None:
    markdown = _converted(tmp_path, [{"id": "eq-0001", "latex": "a"}])

    result = subprocess.run(
        [sys.executable, str(SKILL_SCRIPT), "--fill-formulas", str(markdown), "--no-bootstrap"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "filled 1 formulas, 2 still missing" in result.stdout
    assert "eq-0002, eq-0003" in result.stderr


def test_cli_fill_without_a_manifest_is_rejected(tmp_path: Path) -> None:
    markdown = tmp_path / "plain.md"
    markdown.write_text("No formulas.\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SKILL_SCRIPT), "--fill-formulas", str(markdown), "--no-bootstrap"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "No formula manifest" in result.stderr
