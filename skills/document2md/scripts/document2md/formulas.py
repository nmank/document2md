"""Formula crops for math Docling cannot turn into LaTeX.

Docling's standard pipeline finds display formulas but leaves their LaTeX
empty, which serializes as ``<!-- formula-not-decoded -->``, and it flattens
inline math into prose. With ``--formulas display`` each undecoded formula, and
with ``--formulas all`` each inline math span found by ``inline_math.py``, gets
a numbered placeholder and an entry in ``<stem>_formulas/formulas.json``:

- spans simple enough to read off the glyphs (``\\beta_1``) are filled at once;
- a span identical to an earlier one points at it with ``same_as``;
- every other formula gets a tight PNG crop on a labelled contact sheet.

A reader (Claude, in the skill workflow) writes the LaTeX for the cropped
formulas into the manifest, and ``--fill-formulas`` patches it into the
Markdown. Nothing is guessed: unfilled formulas stay visible as placeholders.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

FORMULA_MODES = {"off", "display", "all"}
RENDER_SCALE = 3  # 216 dpi: small subscripts stay legible.
PADDING = 4  # points around Docling's display box, which can clip accents and limits.
SHEET_MAX_CROPS = 24
# Image readers downscale anything much over ~1568 px; stay under it.
SHEET_MAX_SIZE = 1500
LABEL_HEIGHT = 40
CELL_GAP = 24
MANIFEST = "formulas.json"

_SENTINEL = re.compile(r"(\$\$|\$)?D2MEQ(\d{5})(?(1)\1)")
_PLACEHOLDER = re.compile(r"<!-- formula (eq-\d+) \((display|inline), page (\d+)\) -->")


@dataclass(slots=True)
class Formula:
    id: str
    page: int
    bbox: tuple[float, float, float, float]  # left, top, right, bottom in PDF points, top-left origin
    hint: str  # Docling's flattened text for the formula
    display: bool = True
    source: str = "crop"  # crop (needs a reader), auto (read off the glyphs) or same (see same_as)
    same_as: str | None = None
    latex: str | None = None
    image: str = ""
    sheet: str = ""


def sentinel(number: int) -> str:
    """A token Docling's serializer passes through untouched; fixed width so digits after it stay text."""
    return f"D2MEQ{number:05d}"


def formulas_dir(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}_formulas")


def tag_formulas(document, start_index: int, inline=None) -> list[Formula]:
    """Mark every undecoded formula with a sentinel the serializer will keep.

    These are exactly the items Docling would print as
    ``<!-- formula-not-decoded -->``: empty text, non-empty original glyphs.
    With an ``InlineTagger``, inline math in other items is tagged in the same
    pass, so ids follow document order whatever the page batching.
    """
    from docling_core.types.doc import FormulaItem

    found = []
    for item, _level in document.iterate_items():
        if not isinstance(item, FormulaItem):
            if inline is not None:
                found.extend(inline.tag_item(item, start_index + len(found)))
            continue
        if item.text.strip() or not item.orig or not item.prov:
            continue
        prov = item.prov[0]
        page = document.pages.get(prov.page_no)
        if page is None or page.size is None:
            continue
        number = start_index + len(found) + 1
        box = prov.bbox.to_top_left_origin(page_height=page.size.height)
        found.append(Formula(f"eq-{number:04d}", prov.page_no, (box.l, box.t, box.r, box.b), item.orig))
        item.text = sentinel(number)
    return found


def place_placeholders(markdown: str, formulas: list[Formula]) -> tuple[str, list[Formula]]:
    """Swap sentinels for readable placeholders; keep only formulas that appear."""
    by_number = {int(formula.id[3:]): formula for formula in formulas}
    placed: list[Formula] = []

    def replace(match: re.Match[str]) -> str:
        formula = by_number.get(int(match.group(2)))
        if formula is None:
            return match.group(0)
        formula.display = match.group(1) == "$$"
        placed.append(formula)
        return _placeholder(formula)

    markdown = _SENTINEL.sub(replace, markdown)
    _promote_orphaned_duplicates(placed)
    return markdown, placed


def _promote_orphaned_duplicates(placed: list[Formula]) -> None:
    """A duplicate whose original the serializer dropped becomes the original."""
    present = {formula.id for formula in placed}
    promoted: dict[str, str] = {}
    for formula in placed:
        if formula.source != "same" or formula.same_as in present:
            continue
        if formula.same_as in promoted:
            formula.same_as = promoted[formula.same_as]
        else:
            promoted[formula.same_as] = formula.id
            formula.source, formula.same_as = "crop", None
            present.add(formula.id)


def _placeholder(formula: Formula) -> str:
    kind = "display" if formula.display else "inline"
    return f"<!-- formula {formula.id} ({kind}, page {formula.page}) -->"


def crop_formulas(pdf_path: Path, formulas: list[Formula], out_dir: Path) -> list[Path]:
    """Crop every formula whose source is "crop", plus labelled contact sheets.

    All formulas, cropped or not, are blanked out of each other's crops.
    """
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in [*out_dir.glob("eq-*.png"), *out_dir.glob("sheet-*.png")]:
        stale.unlink()

    wanted = [formula for formula in formulas if formula.source == "crop"]
    crops = []
    document = pdfium.PdfDocument(pdf_path.read_bytes())
    try:
        for page_number in sorted({formula.page for formula in wanted}):
            page = document[page_number - 1]
            width, height = page.get_width(), page.get_height()
            image = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
            on_page = [f for f in formulas if f.page == page_number]
            for formula in (f for f in wanted if f.page == page_number):
                pad = PADDING if formula.display else 0.0  # inline boxes arrive padded
                left, top, right, bottom = formula.bbox
                box = (max(0.0, left - pad), max(0.0, top - pad), min(width, right + pad), min(height, bottom + pad))
                crop = _isolate(image, box, formula, on_page)
                target = out_dir / f"{formula.id}.png"
                crop.save(target)
                formula.image = target.name
                crops.append((formula, crop))
    finally:
        document.close()

    return [out_dir / formula.image for formula, _crop in crops] + _write_sheets(crops, out_dir)


def _isolate(image, box, formula: Formula, on_page: list[Formula]):
    """Crop with padding, blanking neighbouring formulas that the padding reaches.

    Stacked display equations sit points apart and Docling's boxes can
    overlap, so a padded crop would otherwise show fragments of the next line.
    The formula's own box is pasted back last, so none of its ink is lost.
    """
    from PIL import ImageDraw

    def pixels(rect):
        return tuple(round(value * RENDER_SCALE) for value in rect)

    crop_box = pixels(box)
    crop = image.crop(crop_box)
    draw = ImageDraw.Draw(crop)
    for other in on_page:
        if other is formula:
            continue
        left, top, right, bottom = pixels(other.bbox)
        draw.rectangle((left - crop_box[0], top - crop_box[1], right - crop_box[0], bottom - crop_box[1]), fill="white")
    own = pixels(formula.bbox)
    crop.paste(image.crop(own), (own[0] - crop_box[0], own[1] - crop_box[1]))
    return crop


def _write_sheets(crops, out_dir: Path) -> list[Path]:
    """Shelf-pack labelled crops into sheets of at most SHEET_MAX_SIZE pixels a side."""
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=28)
    except TypeError:  # Pillow without FreeType sizing.
        font = ImageFont.load_default()
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    limit = SHEET_MAX_SIZE - LABEL_HEIGHT
    cells = []
    for formula, crop in crops:
        scale = min(1.0, SHEET_MAX_SIZE / crop.width, limit / crop.height)
        if scale < 1.0:
            crop = crop.resize((max(1, round(crop.width * scale)), max(1, round(crop.height * scale))))
        label_width = round(probe.textlength(formula.id, font=font)) + 16
        cells.append((formula, crop, max(crop.width, label_width), crop.height + LABEL_HEIGHT))

    # Lay out: rows left to right, sheets top to bottom.
    sheets: list[list[tuple]] = [[]]
    x = y = row_height = 0
    for formula, crop, width, height in cells:
        if x and x + width > SHEET_MAX_SIZE:
            x, y, row_height = 0, y + row_height + CELL_GAP, 0
        if sheets[-1] and (len(sheets[-1]) == SHEET_MAX_CROPS or y + height > SHEET_MAX_SIZE):
            sheets.append([])
            x = y = row_height = 0
        sheets[-1].append((formula, crop, x, y, width, height))
        x += width + CELL_GAP
        row_height = max(row_height, height)

    paths = []
    for index, placed in enumerate((sheet for sheet in sheets if sheet), start=1):
        width = max(x + w for _f, _c, x, _y, w, _h in placed)
        height = max(y + h for _f, _c, _x, y, _w, h in placed)
        sheet = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sheet)
        for formula, crop, x, y, cell_width, _h in placed:
            draw.rectangle((x, y, x + cell_width - 1, y + LABEL_HEIGHT - 1), fill=(225, 232, 245))
            draw.text((x + 8, y + 4), formula.id, fill="black", font=font)
            sheet.paste(crop, (x, y + LABEL_HEIGHT))
            formula.sheet = f"sheet-{index:02d}.png"
        target = out_dir / f"sheet-{index:02d}.png"
        sheet.save(target)
        paths.append(target)
    return paths


def write_manifest(formulas: list[Formula], out_dir: Path) -> Path:
    entries = [
        {
            "id": formula.id,
            "page": formula.page,
            "kind": "display" if formula.display else "inline",
            "source": formula.source,
            "same_as": formula.same_as,
            "hint": formula.hint,
            "image": formula.image or None,
            "sheet": formula.sheet or None,
            "latex": formula.latex,
        }
        for formula in formulas
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / MANIFEST
    target.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def finish(pdf_path: Path, markdown: str, formulas: list[Formula], output_path: Path) -> tuple[str, list[Path], list[str]]:
    """Place placeholders, crop, write the manifest, and fill what is already known."""
    markdown, placed = place_placeholders(markdown, formulas)
    if not placed:
        return markdown, [], []
    out_dir = formulas_dir(output_path)
    files = crop_formulas(pdf_path, placed, out_dir)
    manifest = write_manifest(placed, out_dir)
    files.append(manifest)
    markdown, _filled, missing = _apply(markdown, json.loads(manifest.read_text(encoding="utf-8")))

    needed = sum(1 for formula in placed if formula.source == "crop")
    automatic = sum(1 for formula in placed if formula.source == "auto")
    if not missing:
        return markdown, files, [f"{automatic} formulas filled from the text layer; none need transcription."]
    warning = (
        f"{needed} formulas need LaTeX ({len(missing)} placeholders including repeats; "
        f"{automatic} filled from the text layer): see {manifest}, then run --fill-formulas."
    )
    return markdown, files, [warning]


def fill(markdown_path: Path) -> tuple[list[str], list[str]]:
    """Replace placeholders that have LaTeX in the manifest; return (filled, still missing)."""
    manifest = formulas_dir(markdown_path) / MANIFEST
    if not manifest.is_file():
        raise FileNotFoundError(f"No formula manifest at {manifest}.")
    markdown = markdown_path.read_text(encoding="utf-8")
    updated, filled, missing = _apply(markdown, json.loads(manifest.read_text(encoding="utf-8")))
    if updated != markdown:
        markdown_path.write_text(updated, encoding="utf-8", newline="\n")
    return filled, missing


def _apply(markdown: str, entries: list[dict]) -> tuple[str, list[str], list[str]]:
    latex = {entry["id"]: _strip_delimiters(entry.get("latex") or "") for entry in entries}
    for entry in entries:
        if entry.get("same_as") and not latex.get(entry["id"]):
            latex[entry["id"]] = latex.get(entry["same_as"], "")
    filled: list[str] = []
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        identifier, kind = match.group(1), match.group(2)
        body = latex.get(identifier, "")
        if not body:
            missing.append(identifier)
            return match.group(0)
        filled.append(identifier)
        if kind == "display":
            return f"$$\n{body}\n$$"
        return "$" + re.sub(r"\s+", " ", body) + "$"

    return _PLACEHOLDER.sub(replace, markdown), filled, missing


def _strip_delimiters(latex: str) -> str:
    """Accept LaTeX written with its own $, $$, \\( \\) or \\[ \\] delimiters."""
    body = latex.strip()
    for opening, closing in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if body.startswith(opening) and body.endswith(closing) and len(body) > len(opening) + len(closing):
            return body[len(opening) : -len(closing)].strip()
    return body
