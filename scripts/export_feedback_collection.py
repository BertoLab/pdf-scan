from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from estrattore.feedback_collection import collection_stats, export_feedback_bundle


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Esporta una copia consistente dei dati raccolti per il training."
    )
    parser.add_argument("output_dir", type=Path, nargs="?")
    parser.add_argument(
        "--without-pdfs",
        action="store_true",
        help="Esporta database ed evidenze OCR senza copiare i PDF disponibili.",
    )
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Mostra lo stato della raccolta senza creare un export.",
    )
    args = parser.parse_args()
    stats = collection_stats()
    if args.status_only:
        _print_stats(stats)
        return
    if args.output_dir is None:
        parser.error("output_dir e obbligatoria salvo l'uso di --status-only")
    output = export_feedback_bundle(
        args.output_dir.resolve(),
        include_pdfs=not args.without_pdfs,
    )
    print(f"Collection esportata in: {output}")
    _print_stats(stats)


def _print_stats(stats: dict[str, int]) -> None:
    print(
        "Run completi: {runs}; run temporanei: {staged_runs}; fatture: {invoices}; "
        "revisioni: {revisions}; operazioni pendenti: {pending}; anomalie: {failed}; "
        "sorgenti immagine non disponibili: {image_unavailable}".format(**stats)
    )


if __name__ == "__main__":
    main()
