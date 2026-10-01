---
name: document2md
description: Convert PDF, Microsoft Office and OpenDocument files (.pdf, .docx, .pptx, .xlsx, .odt, .ods, .odp) to faithful, readable Markdown using Docling and format-aware Python libraries instead of Pandoc or PyMuPDF. Use when Codex needs an editable or reviewable Markdown representation of PDFs, Word documents, PowerPoint presentations, Excel workbooks, or OpenDocument files while preserving tables and source order without inventing semantic structure.
---

# document2md

Convert documents to Markdown with the bundled format-aware converter. Treat conversion as extraction, not reinterpretation: preserve encoded structure and do not infer headings, fields, checklist controls, or relationships that the source does not explicitly represent.

## Workflow

1. Confirm the input extension is `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.odt`, `.ods`, or `.odp`.
2. Choose image handling:
   - Use `--images placeholder` for text-first review, small output, or when images are decorative.
   - Use `--images extract` when figures, diagrams, screenshots, scanned material, or visual evidence matter. Return the generated asset folder with the Markdown.
   - Use `--images embed` only for a self-contained PDF Markdown file when its larger size is acceptable.
3. Run:

```bash
python <skill>/scripts/convert_document.py path/to/input.ext --images placeholder
```

   Run it with any Python 3.11+ interpreter. On its first run the script creates
   `<skill>/.venv`, installs `scripts/requirements.txt` into it, and re-executes
   itself there; every later run reuses that same environment automatically. Do
   not hunt for an interpreter, hardcode one, or activate anything by hand. Pass
   `--no-bootstrap` to stay in the current interpreter when its dependencies are
   already installed.
   PDFs are read through an ASCII-named in-memory stream, so Unicode characters in the source path do not reach Docling's native backend. PDFs are processed in ordered 10-page batches by default to cap memory use.

4. For several inputs, pass all paths and an output directory:

```bash
python <skill>/scripts/convert_document.py first.docx second.pptx book.xlsx --output converted
```

   The CLI batches all PDF inputs through one shared Docling `DocumentConverter`. Do not invoke the script separately in a loop for multiple PDFs.
   An input that fails is reported on stderr and the batch continues, so a single unreadable file does not cost the rest of the run. Exit code `1` means some inputs failed while others were converted; `2` means the invocation itself was rejected. Read stderr and report which inputs failed rather than assuming a whole-batch success.

5. Choose OCR handling for PDFs with `--ocr`:
   - `auto` (default) OCRs bitmap areas only.
   - `off` skips OCR entirely. Much faster, and correct for born-digital PDFs.
   - `force` OCRs every page. Use it for scans and for pages whose text layer is missing or wrong.
   When the user needs the equations in a PDF, add `--formulas all` (or `display` for display equations only) and follow [Recovering equations](#recovering-equations).
6. Inspect the Markdown for structural completeness. Compare the number and order of source tables, slides, and worksheets when fidelity matters.
7. Return the Markdown and its generated assets directory when `--images extract` was selected.

## Conversion rules

- Preserve paragraphs in source order and do not hard-wrap them.
- Convert PDFs with Docling's standard pipeline, including OCR and layout-aware table reconstruction when needed.
- Convert every Word or PowerPoint table to a Markdown table.
- Represent merged table cells once; leave repeated grid positions blank because Markdown has no native row/column spans.
- When a Word table is nested inside another table cell, render the nested table as compact inline text inside that cell. Markdown cannot represent true nested tables, so preserve source order and cell content instead of emitting escaped table syntax that looks like a broken table.
- Use Word heading styles as Markdown headings. Do not promote bold paragraphs to headings.
- Represent each PowerPoint slide as a section, using its title placeholder when available.
- Preserve slide reading order using shape position, then shape order.
- Represent each non-empty worksheet as a section and its used range as a Markdown table.
- Preserve formulas by default. Use `--values-only` only when the user wants cached values.
- Represent each OpenDocument heading, list, paragraph, and table in stored order; `.ods` sheets and `.odp` slides become sections like their Office equivalents.
- Extract PDF/DOCX/PPTX/ODF images beside the Markdown and link them relatively only when `--images extract` is selected. Image links are always relative to the Markdown file, so the file and its assets folder can be moved together.
- Do not silently repair text split across tables, slides, cells, or paragraphs.

## Options

```text
--output PATH       Exact .md path for one input, or output directory
--assets-dir PATH   Asset directory for one input
--values-only       XLSX: export cached values instead of formulas
--include-hidden    XLSX/ODS: include hidden worksheets
--include-notes     PPTX: include speaker notes
--images MODE       placeholder (default), extract, or embed (PDF only)
--ocr MODE          PDF OCR: auto (default), off, or force
--strict            Stop at the first failing input instead of converting the rest
--no-bootstrap      Do not create or re-exec into the skill's own virtual environment
--pdf-pages-per-batch N
                    PDF pages processed at once (default: 10; 0 disables batching)
--formulas MODE     PDF: off (default), display (undecoded display formulas), or all (display plus inline math)
--fill-formulas     Patch LaTeX from <stem>_formulas/formulas.json into the given .md files
--force             Overwrite existing outputs
```

## PDF troubleshooting

- Keep the default `--pdf-pages-per-batch 10` for long or layout-heavy PDFs.
- If memory is unusually constrained, retry with `--pdf-pages-per-batch 5` or `1`.
- Use `--pdf-pages-per-batch 0` only when whole-document processing is specifically needed and sufficient memory is available.
- Do not manually rename or copy a PDF solely because its path contains spaces, accents, or other Unicode characters; the converter handles this internally.
- Use `--ocr off` for born-digital PDFs. It removes the OCR stage, which dominates runtime, and silences `RapidOCR returned empty result!` notices that do not indicate lost text.
- Docling reconstructs reading order and tables statistically. Heading order and table cells can be wrong on dense layouts, and signature blocks, footers, and CSV stamps are not carried over. Check the original before relying on such a passage; do not repair it silently in the Markdown.

## Recovering equations

Without help, Docling leaves each display formula as `<!-- formula-not-decoded -->` and flattens math inside sentences into prose (`βt1` for $\beta_1^t$). With `--formulas`, the converter does what it can deterministically, and you transcribe the rest from images:

1. Convert with `--formulas all`, or `--formulas display` to leave inline math alone. Math the converter could not fill becomes a placeholder such as `<!-- formula eq-0007 (display, page 3) -->` or `<!-- formula eq-0012 (inline, page 3) -->`. Beside the Markdown, `<stem>_formulas/` holds `formulas.json`, with one entry per formula. Its `source` field says what is needed:
   - `auto`: inline math read directly off the PDF's glyphs (`\beta_1`, `\alpha = 0.001`). Its LaTeX is already in the Markdown, so skip it.
   - `same`: identical to the formula named in `same_as`, which is filled at the same time. Skip it.
   - `crop`: needs you. There is a crop (`eq-0007.png`) on a labelled contact sheet (`sheet-NN.png`, up to 24 crops each); `hint` is Docling's flattened text, only a clue.
2. View each `sheet-NN.png`. Use the single `eq-NNNN.png` crops only when a sheet is unclear.
3. For each `crop` entry, set `latex` in `formulas.json` to KaTeX-compatible LaTeX for exactly what the crop shows:
   - Transcribe; do not correct, simplify, complete or renumber.
   - Write an equation number as `\tag{n}`.
   - Ignore fragments of neighbouring lines cut off at a crop's top or bottom edge.
   - A formula that continues a previous line (starting with `=`) stays as it is.
   - Mark an unreadable part as `\text{[illegible]}`; never guess.
   - Leave out `$` delimiters; the fill step adds them.
4. Run `python <skill>/scripts/convert_document.py --fill-formulas path/to/output.md`. It reports how many formulas it filled. Exit code `1` means some still have no LaTeX, and those stay as placeholders. Fill them, or tell the user which remain.
5. Compare a few filled formulas against their crops before returning the Markdown. Return the Markdown without the `_formulas` folder unless the user wants it.

Batching and limits:
- For more than about 60 formulas, work a few sheets at a time and run `--fill-formulas` after each batch. Re-running is safe; filled formulas are not touched again.
- Inline math is found through the PDF's text layer: characters in math fonts (Computer Modern math, AMS, Cambria Math, Symbol) or set smaller than the prose. It works on born-digital PDFs only. On scans the converter warns that a page has no text layer, and that page's inline math stays as Docling wrote it. Tell the user.
- Inline math that the text layer and Docling's text do not agree on is left as Docling's flattened text rather than guessed at, so some inline math can remain flattened.
- Math inside code blocks (for example algorithm pseudo-code) is left alone, because `$...$` would show literally there.
- If an `auto` entry you happen to see is wrong, correct it in the Markdown directly; `--fill-formulas` only fills placeholders.
- Docling decides the order of stacked equations, and it can swap lines. Keep the order it gives and mention any swap you notice rather than reordering silently.

Read [references/format-behavior.md](references/format-behavior.md) when the user asks about fidelity, limitations, or the exact mapping for a format.
