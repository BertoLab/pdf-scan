import argparse
import json
import re
from pathlib import Path

from debug_words import (
    QUALITY_FALLBACK_MODES,
    VIEWER_SAMPLE_ROOT,
    debug_words_file,
    is_supported_input_file,
    natural_sort_key,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Process one or more yearly PDF batches into viewer manifests. "
            "Example: year 2025 batch 1 -> data/input/2025/batch_2025_1."
        )
    )
    parser.add_argument(
        "--input-root",
        default="data/input",
        help="Root folder containing year folders. Default: data/input.",
    )
    parser.add_argument(
        "--year",
        default=None,
        help="Year folder to process, for example 2025.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Single batch number to process, for example 1.",
    )
    parser.add_argument(
        "--batch-range",
        default=None,
        help="Inclusive batch range to process, for example 1-10.",
    )
    parser.add_argument(
        "--file-index",
        type=int,
        default=None,
        help=(
            "Regenerate only one file inside the selected batch, using 1-based "
            "order within the batch folder."
        ),
    )
    parser.add_argument(
        "--ocr-lang",
        default=None,
        help="Forwarded to debug_words_batch.py, for example ita or ita+eng.",
    )
    parser.add_argument(
        "--quality-fallback-mode",
        choices=sorted(QUALITY_FALLBACK_MODES),
        default="crop-only",
        help="Forwarded to debug_words_batch.py.",
    )
    parser.add_argument(
        "--comparison-suite",
        action="store_true",
        help="Forwarded to debug_words_batch.py.",
    )
    args = parser.parse_args()

    year = args.year or input("Anno da processare, es. 2025: ").strip()
    if not re.fullmatch(r"\d{4}", year):
        raise SystemExit("Year must be exactly 4 digits, for example 2025.")

    batch_numbers = _batch_numbers_from_args(args)
    if args.file_index is not None and len(batch_numbers) != 1:
        raise SystemExit("--file-index can only be used with a single --batch.")
    if args.file_index is not None and args.comparison_suite:
        raise SystemExit("--file-index is not supported with --comparison-suite.")

    input_root = Path(args.input_root)

    processed = 0
    failed = 0
    for batch_number in batch_numbers:
        batch_id = f"batch_{year}_{batch_number}"
        batch_dir = input_root / year / batch_id
        if not batch_dir.is_dir():
            failed += 1
            print(f"[skip] missing folder: {batch_dir}")
            continue

        if args.file_index is not None:
            ok = _process_single_file_in_batch(
                batch_dir=batch_dir,
                batch_id=batch_id,
                file_index=args.file_index,
                ocr_lang=args.ocr_lang,
                quality_fallback_mode=args.quality_fallback_mode,
            )
            if ok:
                processed += 1
            else:
                failed += 1
            continue

        print(f"[batch] {batch_id}")
        ok = _process_full_batch(
            batch_dir=batch_dir,
            batch_id=batch_id,
            ocr_lang=args.ocr_lang,
            quality_fallback_mode=args.quality_fallback_mode,
            comparison_suite=args.comparison_suite,
        )
        if ok:
            processed += 1
        else:
            failed += 1
            print(f"[failed] {batch_id}")

    print(f"Processed batches: {processed}")
    print(f"Failed/skipped batches: {failed}")


def _process_full_batch(
    batch_dir: Path,
    batch_id: str,
    ocr_lang: str | None,
    quality_fallback_mode: str,
    comparison_suite: bool,
) -> bool:
    from debug_words_batch import _debug_words_comparison_suite, _count_saved_invoices

    input_files = _input_files(batch_dir)
    if not input_files:
        print(f"[skip] no supported input files in: {batch_dir}")
        return False

    batch_output_root = VIEWER_SAMPLE_ROOT / batch_id
    next_invoice_number = 1

    for index, file_path in enumerate(input_files, start=1):
        print(f"[{index}/{len(input_files)}] processing {file_path.name}")
        try:
            if comparison_suite:
                saved_paths, invoice_count = _debug_words_comparison_suite(
                    file_path=file_path,
                    ocr_lang=ocr_lang,
                    first_invoice_number=next_invoice_number,
                    batch_output_root=batch_output_root,
                )
                next_invoice_number += invoice_count
                continue

            saved_paths = debug_words_file(
                file_path,
                output_dir=_output_dir_for_file(batch_output_root, file_path),
                first_invoice_number=next_invoice_number,
                ocr_lang=ocr_lang,
                quality_fallback_mode=quality_fallback_mode,
            )
            next_invoice_number += _count_saved_invoices(saved_paths)
        except Exception as error:
            print(f"[failed] {file_path.name}: {error}")
            return False

    return True


def _process_single_file_in_batch(
    batch_dir: Path,
    batch_id: str,
    file_index: int,
    ocr_lang: str | None,
    quality_fallback_mode: str,
) -> bool:
    if file_index < 1:
        raise SystemExit("--file-index must be >= 1.")

    input_files = _input_files(batch_dir)
    if not input_files:
        print(f"[skip] no supported input files in: {batch_dir}")
        return False
    if file_index > len(input_files):
        print(
            f"[skip] file index {file_index} outside batch size "
            f"({len(input_files)} files)."
        )
        return False

    file_path = input_files[file_index - 1]
    batch_output_root = VIEWER_SAMPLE_ROOT / batch_id
    first_invoice_number = _first_invoice_number_for_file(
        batch_output_root=batch_output_root,
        input_files=input_files,
        file_index=file_index,
    )

    print(
        f"[batch] {batch_id} file {file_index}/{len(input_files)}: "
        f"{file_path.name} first_invoice_number={first_invoice_number}"
    )
    try:
        debug_words_file(
            file_path,
            output_dir=_output_dir_for_file(batch_output_root, file_path),
            first_invoice_number=first_invoice_number,
            ocr_lang=ocr_lang,
            quality_fallback_mode=quality_fallback_mode,
        )
    except Exception as error:
        print(f"[failed] {file_path.name}: {error}")
        return False

    return True


def _input_files(batch_dir: Path) -> list[Path]:
    return sorted(
        (
            file_path
            for file_path in batch_dir.iterdir()
            if is_supported_input_file(file_path)
        ),
        key=natural_sort_key,
    )


def _output_dir_for_file(batch_output_root: Path, file_path: Path) -> Path:
    return batch_output_root / file_path.stem


def _first_invoice_number_for_file(
    batch_output_root: Path,
    input_files: list[Path],
    file_index: int,
) -> int:
    first_invoice_number = 1
    for previous_file in input_files[: file_index - 1]:
        manifest_path = (
            _output_dir_for_file(batch_output_root, previous_file)
            / "viewer_manifest.json"
        )
        if not manifest_path.exists():
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        first_invoice_number += len(manifest.get("invoices", []))

    return first_invoice_number


def _batch_numbers_from_args(args: argparse.Namespace) -> list[int]:
    if args.batch is not None and args.batch_range:
        raise SystemExit("Use either --batch or --batch-range, not both.")

    if args.batch is not None:
        if args.batch < 1:
            raise SystemExit("--batch must be >= 1.")
        return [args.batch]

    batch_range = args.batch_range or input("Batch singolo o range, es. 1 oppure 1-10: ").strip()
    match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", batch_range)
    if not match:
        raise SystemExit("Batch must be a number or inclusive range, for example 1 or 1-10.")

    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    if start < 1 or end < 1:
        raise SystemExit("Batch numbers must be >= 1.")
    if end < start:
        raise SystemExit("Batch range end must be >= start.")

    return list(range(start, end + 1))


if __name__ == "__main__":
    main()
