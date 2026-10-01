# Format behavior

## PDF

- PDF conversion uses Docling's standard pipeline rather than PyMuPDF.
- Multi-PDF calls reuse one initialized `DocumentConverter`; each PDF and page range is processed sequentially through it.
- Source bytes are passed to Docling using an ASCII-only in-memory stream name. This avoids native PDF backend failures caused by Unicode filesystem paths.
- PDFs are processed in ordered page ranges (10 pages by default) and the Markdown fragments are joined in source order. This bounds peak memory use for long PDFs.
- Docling OCR, layout, and table stages use conservative batch and queue sizes to reduce memory spikes.
- When the Tesseract executable is available, use Docling's Tesseract CLI OCR backend; otherwise use RapidOCR with the bundled ONNX Runtime backend.
- Docling reconstructs reading order, headings, lists, tables, formulas, and OCR text where available.
- `--images placeholder` keeps lightweight image-position markers.
- `--images extract` asks Docling to write referenced image artifacts to the accompanying assets folder.
- Extracted image links are relative to the Markdown file. Docling emits absolute, percent-encoded URIs when handed an absolute artifacts directory, so the converter passes a relative one and rewrites any absolute link that remains.
- With more than one page batch, each batch's images go into a `pages-NNNN-NNNN` subfolder of the assets directory; a single batch writes them flat.
- `--ocr auto` OCRs bitmap areas only, `--ocr off` disables the OCR stage, and `--ocr force` OCRs every page.
- `RapidOCR returned empty result!` means a region held no recognizable text. It is not by itself evidence of lost content.
- Reading order, heading levels, and table cells come from Docling's layout models and can be wrong on dense pages. Page footers, signature blocks, and CSV stamps are not reproduced.
- `--images embed` writes base64 images into Markdown and can make the file very large.
- PDF extraction is computationally heavier than Office extraction and may download model files on first use.
- `--pdf-pages-per-batch 0` disables page batching. Smaller positive values trade speed for lower peak memory.
- `--formulas display` applies to formulas Docling detected but left without LaTeX: an empty formula `text` with non-empty original glyphs. Formulas Docling did decode keep their LaTeX.
- `--formulas all` also scans each Docling paragraph's characters, taken from the pypdfium2 text layer inside the paragraph's box:
  - Math is a run of characters in math fonts (`CMMI`, `CMSY`, `CMEX`, AMS, Euler, `Symbol`, `*Math`), or set at under 85% of the body size in a non-prose font. Non-prose Computer Modern roman characters, and prose-font digits and operators, join a run but cannot start one. Prose-font letters, such as the "st" in "1st", are never math.
  - Runs split at line breaks. Prose punctuation is trimmed from both ends. Brackets that frame the math in the prose font are dropped, as in "(mt)"; round and square brackets balance together, so `[0, 1)` stays whole.
  - A run is aligned with Docling's paragraph text, and the whole paragraph is skipped below 85% similarity. Each span is extended over detached accents (Docling writes ` ̂mt` for $\hat m_t$), and a span is skipped if it matches below 60%.
  - Automatic LaTeX is produced only when the span's characters equal Docling's text exactly; the text layer can miss glyphs Docling still has. It is limited to math-italic, `\mathbb` and `\mathcal` letters, Greek, a fixed operator table and single-level sub/superscripts chosen by baseline. Anything with `CMEX` glyphs (accents, radicals, big operators), upright letters or nested scripts is cropped.
  - Identical spans, with the same glyphs, fonts, sizes and baseline offsets, share one transcription through `same_as`.
  - Code blocks and tables are not scanned.
- Ids (`eq-0001`, …) follow document order across display and inline math and continue across page batches; the output is the same whatever `--pdf-pages-per-batch` is. A formula that Docling's serializer drops gets no placeholder and no manifest entry.
- Crops are rendered with pypdfium2 at 216 dpi from Docling's box, padded by 4 pt (inline formulas: their glyph boxes, padded by 1.5 pt). Padding that reaches into another formula's box on the same page is blanked; the formula's own box is never blanked. Where Docling's boxes overlap, a crop can still show a sliver of the next line.
- `--fill-formulas` writes display formulas as `$$` blocks on their own lines and inline formulas as `$...$`. It strips any `$`, `$$`, `\(\)` or `\[\]` the transcription already has, leaves unfilled placeholders unchanged, and can be re-run.

## DOCX

- Body paragraphs and tables are emitted in document order.
- Real Word heading styles map to Markdown headings.
- Character-level bold and italic formatting is retained where practical.
- Tables use the physical Word grid. Merged-cell text appears once and duplicate grid positions remain empty.
- Inline drawing extraction is best-effort when `--images extract` is selected.
- Headers, footers, comments, tracked-change history, text boxes, equations, and exact pagination are not reconstructed.

## PPTX

- Each slide becomes a level-one section.
- The title placeholder supplies the slide heading when present.
- Text boxes become paragraphs; tables become Markdown tables.
- Shapes are ordered top-to-bottom, then left-to-right, with original shape order as a tie-breaker.
- Pictures are extracted and linked only when `--images extract` is selected.
- Charts, SmartArt, transitions, animations, and visual positioning are not reproduced.
- Speaker notes are optional.

## XLSX

- Each included worksheet becomes a level-one section.
- The rectangular used range becomes one Markdown table.
- Empty trailing rows and columns are trimmed.
- Formulas are retained by default.
- `--values-only` requests cached formula results; results may be empty when the workbook was not recalculated before saving.
- Merged-cell content appears in the top-left cell and other positions remain empty.
- Styling, charts, conditional formatting, drawings, comments, and formulas' calculated display formatting are not reproduced.

## OpenDocument

- `.odt`, `.ods`, and `.odp` are read directly from `content.xml` with the standard library; no OpenDocument dependency is required.
- Encoded whitespace is preserved: `text:s` runs, `text:tab`, and `text:line-break`.
- `text:h` outline levels become Markdown heading levels. Bold paragraphs are not promoted.
- Lists become Markdown lists, nested by list depth.
- Tables use the stored grid. Repeated cells and rows are expanded, and cells covered by a merge stay empty.
- Sheets hidden with `table:display="false"` are skipped unless `--include-hidden` is passed, and each skipped sheet is reported as a warning.
- Each `draw:page` becomes a slide section, named by its `draw:name` when present.
- Images referenced from the archive are extracted only when `--images extract` is selected.
- Form controls, change tracking, styles, and page layout are not reconstructed.

## Batches and failures

- Each input is converted independently. A failure is recorded and the batch continues, so one unreadable file cannot discard work queued behind it.
- The CLI prints every failure to stderr and exits `1` when any input failed while others succeeded.
- `--strict` re-raises the first failure instead.
- The Markdown a run produced is always written, including when Docling wrote an intermediate file itself.

## Why this differs from Pandoc

Pandoc is a broad document converter. Office files often use tables and drawing objects for layout, which can lead to large HTML fragments in Markdown output. This converter uses Office-specific object models and deliberately emits a smaller Markdown vocabulary. It favors predictable structural fidelity over visual imitation or semantic guessing.
