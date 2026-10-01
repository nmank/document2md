"""Formula crops for equations Docling detects but cannot decode.

Docling's standard pipeline finds formulas but leaves their LaTeX empty, which
serializes as ``<!-- formula-not-decoded -->``. With ``--formulas crop`` each
such formula instead gets a numbered placeholder, a tight PNG crop of the page,
and an entry in ``<stem>_formulas/formulas.json``. A reader (Claude, in the
skill workflow) writes the LaTeX into the manifest, and ``--fill-formulas``
patches it into the Markdown. Nothing is guessed: unfilled formulas stay
visible as placeholders.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

FORMULA_MODES = {"off", "crop"}
RENDER_SCALE = 3  # 216 dpi: small subscripts stay legible.
PADDING = 4  # points around Docling's box, which can clip accents and limits.
SHEET_MAX_CROPS = 8
# Image readers downscale anything much over ~1568 px; stay under it.
SHEET_MAX_SIZE = 1500
LABEL_HEIGHT = 40
MANIFEST = "formulas.json"

_SENTINEL = re.compile(r"(\$\$?)D2MEQ(\d+)\1")
_PLACEHOLDER = re.compile(r"<!-- formula (eq-\d+) \((display|inline), page (\d+)\) -->")


@dataclass(slots=True)
class Formula:
    id: str
    page: int
    bbox: tuple[float, float, float, float]  # left, top, right, bottom in PDF points, top-left origin
    hint: str  # Docling's flattened text for the formula
    display: bool = True
    image: str = ""
    sheet: str = ""


def formulas_dir(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}_formulas")


def tag_formulas(document, start_index: int) -> list[Formula]:
    """Mark every undecoded formula with a sentinel the serializer will keep.

    These are exactly the items Docling would print as
    ``<!-- formula-not-decoded -->``: empty text, non-empty original glyphs.
    """
    from docling_core.types.doc import FormulaItem

    found = []
    for item, _level in document.iterate_items():
        if not isinstance(item, FormulaItem) or item.text.strip() or not item.orig or not item.prov:
            continue
        prov = item.prov[0]
        page = document.pages.get(prov.page_no)
        if page is None or page.size is None:
            continue
        number = start_index + len(found) + 1
        box = prov.bbox.to_top_left_origin(page_height=page.size.height)
        found.append(Formula(f"eq-{number:04d}", prov.page_no, (box.l, box.t, box.r, box.b), item.orig))
        item.text = f"D2MEQ{number:04d}"
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

    return _SENTINEL.sub(replace, markdown), placed


def _placeholder(formula: Formula) -> str:
    kind = "display" if formula.display else "inline"
    return f"<!-- formula {formula.id} ({kind}, page {formula.page}) -->"


def crop_formulas(pdf_path: Path, formulas: list[Formula], out_dir: Path) -> list[Path]:
    """Write one PNG per formula plus labelled contact sheets."""
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in [*out_dir.glob("eq-*.png"), *out_dir.glob("sheet-*.png")]:
        stale.unlink()

    crops = []
    document = pdfium.PdfDocument(pdf_path.read_bytes())
    try:
        for page_number in sorted({formula.page for formula in formulas}):
            page = document[page_number - 1]
            width, height = page.get_width(), page.get_height()
            image = page.render(scale=RENDER_SCALE).to_pil().convert("RGB")
            on_page = [f for f in formulas if f.page == page_number]
            for formula in on_page:
                left, top, right, bottom = formula.bbox
                box = (
                    max(0.0, left - PADDING),
                    max(0.0, top - PADDING),
                    min(width, right + PADDING),
                    min(height, bottom + PADDING),
                )
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
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=28)
    except TypeError:  # Pillow without FreeType sizing.
        font = ImageFont.load_default()

    sheets: list[list] = [[]]
    used_height = 0
    for formula, crop in crops:
        if crop.width > SHEET_MAX_SIZE:
            crop = crop.resize((SHEET_MAX_SIZE, max(1, round(crop.height * SHEET_MAX_SIZE / crop.width))))
        if crop.height + LABEL_HEIGHT > SHEET_MAX_SIZE - LABEL_HEIGHT:
            crop = crop.resize((max(1, round(crop.width * (SHEET_MAX_SIZE - 2 * LABEL_HEIGHT) / crop.height)), SHEET_MAX_SIZE - 2 * LABEL_HEIGHT))
        needed = crop.height + LABEL_HEIGHT
        if sheets[-1] and (len(sheets[-1]) == SHEET_MAX_CROPS or used_height + needed > SHEET_MAX_SIZE):
            sheets.append([])
            used_height = 0
        sheets[-1].append((formula, crop))
        used_height += needed

    paths = []
    for index, entries in enumerate(sheets, start=1):
        if not entries:
            continue
        width = max(crop.width for _formula, crop in entries)
        height = sum(crop.height + LABEL_HEIGHT for _formula, crop in entries)
        sheet = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(sheet)
        y = 0
        for formula, crop in entries:
            draw.rectangle((0, y, width, y + LABEL_HEIGHT - 1), fill=(225, 232, 245))
            draw.text((8, y + 4), f"{formula.id}  (page {formula.page})", fill="black", font=font)
            y += LABEL_HEIGHT
            sheet.paste(crop, (0, y))
            y += crop.height
            formula.sheet = f"sheet-{index:02d}.png"
        target = out_dir / f"sheet-{index:02d}.png"
        sheet.save(target)
        paths.append(target)
    return paths


def write_manifest(formulas: list[Formula], out_dir: Path) -> Path:
    entries = []
    for formula in formulas:
        entry = asdict(formula)
        entry.pop("bbox")
        entry["display"] = formula.display
        entry["latex"] = None
        entries.append(entry)
    target = out_dir / MANIFEST
    target.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def finish(pdf_path: Path, markdown: str, formulas: list[Formula], output_path: Path) -> tuple[str, list[Path], list[str]]:
    """Place placeholders, crop, and write the manifest for one converted PDF."""
    markdown, placed = place_placeholders(markdown, formulas)
    if not placed:
        return markdown, [], []
    out_dir = formulas_dir(output_path)
    files = crop_formulas(pdf_path, placed, out_dir)
    files.append(write_manifest(placed, out_dir))
    warning = f"{len(placed)} formulas need LaTeX: see {out_dir / MANIFEST}, then run --fill-formulas."
    return markdown, files, [warning]


def fill(markdown_path: Path) -> tuple[list[str], list[str]]:
    """Replace placeholders that have LaTeX in the manifest; return (filled, still missing)."""
    manifest = formulas_dir(markdown_path) / MANIFEST
    if not manifest.is_file():
        raise FileNotFoundError(f"No formula manifest at {manifest}.")
    latex = {
        entry["id"]: _strip_delimiters(entry.get("latex") or "")
        for entry in json.loads(manifest.read_text(encoding="utf-8"))
    }
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

    markdown = markdown_path.read_text(encoding="utf-8")
    updated = _PLACEHOLDER.sub(replace, markdown)
    if updated != markdown:
        markdown_path.write_text(updated, encoding="utf-8", newline="\n")
    return filled, missing


def _strip_delimiters(latex: str) -> str:
    """Accept LaTeX written with its own $, $$, \\( \\) or \\[ \\] delimiters."""
    body = latex.strip()
    for opening, closing in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if body.startswith(opening) and body.endswith(closing) and len(body) > len(opening) + len(closing):
            return body[len(opening) : -len(closing)].strip()
    return body
