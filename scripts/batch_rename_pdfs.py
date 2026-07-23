import argparse
import math
import re
from pathlib import Path


BATCH_SIZE = 10


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rename all PDFs in a folder to pdf_<year>_<n>.pdf and split them "
            "into batch_<year>_<n> folders of 10 files each."
        )
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Folder containing the PDFs to rename and split.",
    )
    parser.add_argument(
        "--year",
        default=None,
        help="Year used in generated names, for example 2025.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned changes without renaming or moving files.",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"Input folder does not exist: {input_dir}")

    year = args.year or input("Anno da usare nei nomi, es. 2025: ").strip()
    if not re.fullmatch(r"\d{4}", year):
        raise SystemExit("Year must be exactly 4 digits, for example 2025.")

    pdfs = sorted(
        [path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"],
        key=lambda path: path.name.lower(),
    )
    if not pdfs:
        raise SystemExit(f"No PDF files found in: {input_dir}")

    batch_count = math.ceil(len(pdfs) / BATCH_SIZE)
    renamed_paths = [
        input_dir / f"pdf_{year}_{index}.pdf"
        for index in range(1, len(pdfs) + 1)
    ]
    batch_dirs = [
        input_dir / f"batch_{year}_{index}"
        for index in range(1, batch_count + 1)
    ]

    _validate_targets(
        original_paths=pdfs,
        renamed_paths=renamed_paths,
        batch_dirs=batch_dirs,
    )

    print(f"Folder: {input_dir}")
    print(f"Year: {year}")
    print(f"PDF count: {len(pdfs)}")
    print(f"Batch count: {batch_count}")
    for batch_index in range(batch_count):
        start = batch_index * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(pdfs))
        print(
            f"- {batch_dirs[batch_index].name}: "
            f"{renamed_paths[start].name} ... {renamed_paths[end - 1].name}"
        )

    if args.dry_run:
        return

    temp_paths = _rename_to_temporary_paths(pdfs, year)
    try:
        for temp_path, final_path in zip(temp_paths, renamed_paths, strict=True):
            temp_path.rename(final_path)

        for batch_dir in batch_dirs:
            batch_dir.mkdir()

        for index, final_path in enumerate(renamed_paths):
            batch_dir = batch_dirs[index // BATCH_SIZE]
            final_path.rename(batch_dir / final_path.name)
    except Exception:
        print("Operation failed after changes had started. Check the folder manually.")
        raise

    print("Done.")


def _validate_targets(
    original_paths: list[Path],
    renamed_paths: list[Path],
    batch_dirs: list[Path],
) -> None:
    original_set = {path.resolve() for path in original_paths}

    for target in renamed_paths:
        if target.exists() and target.resolve() not in original_set:
            raise SystemExit(f"Target PDF already exists: {target}")

    for batch_dir in batch_dirs:
        if batch_dir.exists():
            raise SystemExit(f"Target batch folder already exists: {batch_dir}")


def _rename_to_temporary_paths(paths: list[Path], year: str) -> list[Path]:
    temp_paths = []
    for index, path in enumerate(paths, start=1):
        temp_path = path.with_name(f".__batch_rename_tmp_{year}_{index}.pdf")
        if temp_path.exists():
            raise SystemExit(f"Temporary file already exists: {temp_path}")
        path.rename(temp_path)
        temp_paths.append(temp_path)

    return temp_paths


if __name__ == "__main__":
    main()
