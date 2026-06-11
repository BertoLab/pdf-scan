import argparse
import json
from pathlib import Path
from time import perf_counter

from debug_words import (
    QUALITY_FALLBACK_MODES,
    clean_viewer_sample_root,
    debug_words_file,
    is_supported_input_file,
)


COMPARISON_VARIANTS = [
    {
        "label": "no_fallback",
        "output_suffix": "__no_fallback",
        "quality_fallback_mode": "none",
    },
    {
        "label": "fallback_best_crop",
        "output_suffix": "__fallback_best_crop",
        "quality_fallback_mode": "best-crop",
    },
    {
        "label": "fallback_crop_only",
        "output_suffix": "__fallback_crop_only",
        "quality_fallback_mode": "crop-only",
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run word-level OCR debug output for every supported file in a folder."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default="data/input",
        help="Directory containing PDF or image files.",
    )
    parser.add_argument(
        "--ocr-lang",
        default=None,
        help="Tesseract language setting, for example `ita` or `ita+eng`.",
    )
    parser.add_argument(
        "--output-suffix",
        default=None,
        help="Suffix appended to every default viewer sample directory name.",
    )
    parser.add_argument(
        "--disable-quality-fallback",
        action="store_true",
        help="Disable best/crop OCR fallback after the first full-page OCR pass.",
    )
    parser.add_argument(
        "--quality-fallback-mode",
        choices=sorted(QUALITY_FALLBACK_MODES),
        default="crop-only",
        help="Fallback strategy for the normal non-comparison run.",
    )
    parser.add_argument(
        "--comparison-suite",
        action="store_true",
        help=(
            "Generate normal samples first, then comparison samples only for "
            "documents that actually trigger OCR fallback."
        ),
    )
    parser.add_argument(
        "--no-clean-sample",
        action="store_true",
        help="Do not delete previous viewer sample folders before generating output.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        raise SystemExit(f"Input directory does not exist: {input_dir}")

    input_files = sorted(
        file_path for file_path in input_dir.iterdir() if is_supported_input_file(file_path)
    )
    if not input_files:
        raise SystemExit(f"No supported input files found in: {input_dir}")

    if not args.no_clean_sample:
        print("Cleaning viewer sample folders...")
        clean_viewer_sample_root()

    processed = 0
    failed = 0
    next_invoice_number = 1
    batch_started_at = perf_counter()

    for index, file_path in enumerate(input_files, start=1):
        file_started_at = perf_counter()
        print(f"[{index}/{len(input_files)}] processing {file_path.name}")
        try:
            if args.comparison_suite:
                saved_paths, invoice_count = _debug_words_comparison_suite(
                    file_path=file_path,
                    ocr_lang=args.ocr_lang,
                    first_invoice_number=next_invoice_number,
                )
                next_invoice_number += invoice_count
            else:
                quality_fallback_mode = (
                    "none"
                    if args.disable_quality_fallback
                    else args.quality_fallback_mode
                )
                saved_paths = debug_words_file(
                    file_path,
                    first_invoice_number=next_invoice_number,
                    ocr_lang=args.ocr_lang,
                    output_suffix=args.output_suffix,
                    quality_fallback_mode=quality_fallback_mode,
                )
        except Exception as error:
            failed += 1
            print(f"  failed after {perf_counter() - file_started_at:.2f}s: {error}")
            continue

        if not args.comparison_suite:
            next_invoice_number += _count_saved_invoices(saved_paths)
        processed += 1
        print(f"  saved: {saved_paths[0].parent}")
        print(f"  elapsed: {perf_counter() - file_started_at:.2f}s")

    print(f"Processed: {processed}")
    print(f"Failed: {failed}")
    print(f"Elapsed: {perf_counter() - batch_started_at:.2f}s")


def _debug_words_comparison_suite(
    file_path: Path,
    ocr_lang: str | None,
    first_invoice_number: int,
) -> tuple[list[Path], int]:
    no_fallback_variant = COMPARISON_VARIANTS[0]
    print(f"  variant: {no_fallback_variant['label']}")
    no_fallback_paths = debug_words_file(
        file_path,
        first_invoice_number=first_invoice_number,
        ocr_lang=ocr_lang,
        output_suffix=no_fallback_variant["output_suffix"],
        quality_fallback_mode=no_fallback_variant["quality_fallback_mode"],
    )
    all_saved_paths = [*no_fallback_paths]
    invoice_count = _count_saved_invoices(no_fallback_paths)

    if not _manifest_has_fallback_candidates(no_fallback_paths):
        print("  comparison skipped: no_fallback run is not below fallback threshold")
        return all_saved_paths, invoice_count

    for variant in COMPARISON_VARIANTS[1:]:
        label = variant["label"]
        print(f"  variant: {label}")
        saved_paths = debug_words_file(
            file_path,
            first_invoice_number=first_invoice_number,
            ocr_lang=ocr_lang,
            output_suffix=variant["output_suffix"],
            quality_fallback_mode=variant["quality_fallback_mode"],
        )
        all_saved_paths.extend(saved_paths)

    return all_saved_paths, invoice_count


def _manifest_has_fallback_candidates(saved_paths: list[Path]) -> bool:
    manifest_path = next(
        (path for path in saved_paths if path.name == "viewer_manifest.json"),
        None,
    )
    if manifest_path is None:
        return False

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return bool(manifest.get("quality_fallback_candidates"))


def _count_saved_invoices(saved_paths: list[Path]) -> int:
    manifest_path = next(
        (path for path in saved_paths if path.name == "viewer_manifest.json"),
        None,
    )
    if manifest_path is None:
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return len(manifest.get("invoices", []))


if __name__ == "__main__":
    main()
