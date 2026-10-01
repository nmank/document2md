from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import formulas as formula_crops
from .converter import OCR_MODES, ConversionOptions, SUPPORTED_SUFFIXES, convert_files


def _configure_text_streams() -> None:
    """Prefer UTF-8 console output on Windows terminals with legacy code pages."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="document2md",
        description="Convert PDF, Office and OpenDocument files to readable Markdown.",
    )
    parser.add_argument("inputs", nargs="+", help="PDF, Office or OpenDocument files to convert.")
    parser.add_argument("-o", "--output", help="Exact .md path for one input, or an output directory.")
    parser.add_argument("--assets-dir", help="Asset directory for one input.")
    parser.add_argument("--values-only", action="store_true", help="XLSX: export cached values instead of formulas.")
    parser.add_argument("--include-hidden", action="store_true", help="XLSX/ODS: include hidden worksheets.")
    parser.add_argument("--include-notes", action="store_true", help="PPTX: include speaker notes.")
    parser.add_argument(
        "--images",
        choices=["placeholder", "extract", "embed"],
        default="placeholder",
        help="Image handling: placeholders, extracted files, or PDF-only base64 embedding.",
    )
    parser.add_argument(
        "--ocr",
        choices=sorted(OCR_MODES),
        default="auto",
        help="PDF OCR: auto (bitmap areas only), off (digital text only, much faster), or force (every page).",
    )
    parser.add_argument(
        "--formulas",
        choices=sorted(formula_crops.FORMULA_MODES),
        default="off",
        help="PDF: crop math Docling cannot decode to <stem>_formulas/ for LaTeX transcription: "
        "display formulas, or all to add inline math found in the text layer (default: off).",
    )
    parser.add_argument(
        "--fill-formulas",
        action="store_true",
        help="Treat inputs as converted .md files and patch in the LaTeX from their <stem>_formulas/formulas.json.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing Markdown outputs.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Stop at the first failing input instead of converting the rest of the batch.",
    )
    parser.add_argument(
        "--pdf-pages-per-batch",
        type=int,
        default=10,
        metavar="N",
        help="PDF pages processed at once (default: 10; use 0 for the whole PDF).",
    )
    parser.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="Do not create or re-exec into the skill's own virtual environment.",
    )
    return parser


def _input_paths(raw_inputs: list[str]) -> list[Path]:
    paths = []
    for raw in raw_inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            paths.extend(
                child for child in sorted(path.iterdir())
                if child.is_file() and child.suffix.lower() in SUPPORTED_SUFFIXES
            )
        else:
            paths.append(path)
    if not paths:
        raise FileNotFoundError("No convertible inputs found.")
    return paths


def main(argv: list[str] | None = None) -> int:
    _configure_text_streams()
    args = build_parser().parse_args(argv)
    if args.fill_formulas:
        return _fill_formulas(args.inputs)
    try:
        inputs = _input_paths(args.inputs)
        output = Path(args.output).expanduser() if args.output else None
        if len(inputs) > 1 and output and output.suffix.lower() == ".md":
            raise ValueError("For multiple inputs, --output must be a directory.")
        if len(inputs) > 1 and args.assets_dir:
            raise ValueError("--assets-dir can only be used with one input.")

        targets = []
        for source in inputs:
            if output and len(inputs) == 1 and output.suffix.lower() == ".md":
                target = output
            elif output:
                output.mkdir(parents=True, exist_ok=True)
                target = output / f"{source.stem}.md"
            else:
                target = source.with_suffix(".md")
            targets.append(target)

        options = ConversionOptions(
            values_only=args.values_only,
            include_hidden=args.include_hidden,
            include_notes=args.include_notes,
            images=args.images,
            force=args.force,
            assets_dir=Path(args.assets_dir).expanduser().resolve() if args.assets_dir else None,
            pdf_pages_per_batch=args.pdf_pages_per_batch,
            ocr=args.ocr,
            formulas=args.formulas,
        )
        batch = convert_files(inputs, targets, options, strict=args.strict)
        for result in batch.results:
            print(result.output_path)
            for asset in result.assets:
                print(asset)
            for warning in result.warnings:
                print(f"WARNING: {warning}", file=sys.stderr)
        for failure in batch.failures:
            print(f"ERROR: {failure.input_path}: {failure.error}", file=sys.stderr)
        if batch.failures:
            print(
                f"ERROR: {len(batch.failures)} of {len(inputs)} inputs failed; "
                f"{len(batch.results)} converted.",
                file=sys.stderr,
            )
            return 1
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


def _fill_formulas(raw_inputs: list[str]) -> int:
    incomplete = False
    for raw in raw_inputs:
        path = Path(raw).expanduser()
        try:
            filled, missing = formula_crops.fill(path)
        except (FileNotFoundError, ValueError) as error:
            print(f"ERROR: {path}: {error}", file=sys.stderr)
            return 2
        print(f"{path}: filled {len(filled)} formulas, {len(missing)} still missing")
        if missing:
            incomplete = True
            print(f"WARNING: {path}: no LaTeX yet for {', '.join(missing)}", file=sys.stderr)
    return 1 if incomplete else 0


if __name__ == "__main__":
    raise SystemExit(main())
