# document2md

`document2md` converts `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.odt`, `.ods`, and `.odp` files into readable Markdown without using Pandoc. PDF conversion uses Docling rather than PyMuPDF; OpenDocument files are read straight from their XML.

The repository is skill-first: the canonical implementation lives inside `skills/document2md`, where Codex can use it directly. The optional CLI package installs the same code.

## Principles

- Preserve source structure instead of guessing intent.
- Keep Word and PowerPoint tables as Markdown tables.
- Keep worksheets separated and represent their used ranges as tables.
- Use real Office metadata, such as Word heading styles and PowerPoint titles, when available.
- Do not hard-wrap prose.
- Extract embedded images to a neighboring assets directory.
- Let callers choose placeholder, extracted, or PDF-embedded image handling.
- Make lossy decisions explicit and deterministic.
- Link extracted images relatively, so a Markdown file and its assets folder can be moved together.
- Never report success for a file that was not written.
- Let one unreadable input fail on its own without discarding the rest of a batch.

## CLI

```powershell
python -m pip install -e .
document2md document.pdf --images extract
document2md scanned.pdf --ocr force
document2md born-digital.pdf --ocr off
document2md long-document.pdf --pdf-pages-per-batch 5
document2md paper.pdf --formulas crop
document2md --fill-formulas paper.md
document2md document.docx
document2md deck.pptx workbook.xlsx --output converted
document2md workbook.xlsx --values-only --force
document2md form.odt notes.odp
```

The CLI writes one Markdown file per input. For a single input, `--output` may be an exact `.md` path. For multiple inputs, it must be a directory.

A file that cannot be converted is reported on stderr and the batch continues; the exit code is `1` when any input failed, `2` when the whole invocation was rejected. Use `--strict` to stop at the first failure instead.

`--ocr` controls the PDF OCR stage: `auto` (default) runs OCR on bitmap areas, `off` skips it entirely and is much faster on born-digital PDFs, and `force` OCRs every page, which is what scans need.

PDF inputs are passed to Docling through an ASCII-named memory stream, so paths containing spaces, accents, or other Unicode characters are supported. Long PDFs are processed in ordered 10-page ranges by default to limit peak memory use. Use `--pdf-pages-per-batch 5` (or `1`) on a constrained machine, and `--pdf-pages-per-batch 0` to disable batching.

### Equations

Docling detects formulas but does not decode them, so by default each one becomes `<!-- formula-not-decoded -->`. With `--formulas crop`:
- each such formula becomes a numbered placeholder (`<!-- formula eq-0007 (display, page 3) -->`);
- `paper_formulas/` gets a 216 dpi PNG crop of each formula, labelled contact sheets of up to 8 crops, and `formulas.json` with `latex: null` for each id.

Whoever reads the crops (in the skill, Claude) writes LaTeX into the manifest. `document2md --fill-formulas paper.md` then replaces each filled placeholder with `$$` display math or `$...$` inline math. It reports the ones still missing and exits `1` while any remain. This needs no additional model or GPU. Inline math that Docling leaves as flattened text inside a sentence is not covered.

The script creates and reuses its own `.venv` (see [Skill](#skill)), so for local development the gitignored `.venv` already contains all runtime and test dependencies:

```powershell
.\.venv\Scripts\document2md.exe input.pdf --images extract
.\.venv\Scripts\python.exe -m pytest
```

## Skill

The installable skill is in [`skills/document2md`](skills/document2md). Its bundled script works without installing this repository as a package:

```powershell
python skills/document2md/scripts/convert_document.py input.docx
```

On its first run the script creates a virtual environment at `skills/document2md/.venv`, installs `scripts/requirements.txt` into it, and re-executes itself with that interpreter. Every later run finds the same environment, so callers never have to locate or activate it, and no caller needs to hardcode an interpreter path. Pass `--no-bootstrap` to stay in the current interpreter.
