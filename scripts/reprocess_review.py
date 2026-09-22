from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from estrattore.processing_storage import (  # noqa: E402
    reprocess_review_batch,
    review_batch_dir_from_base_path,
    review_batch_base_path_by_number,
)
from estrattore.runtime_paths import get_runtime_paths  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Riprocessa una revisione esistente in-place. "
            "Tool interno di sviluppo: non crea una nuova revisione."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--review",
        type=int,
        help="Numero revisione, per esempio 3 per Revisione 003.",
    )
    target_group.add_argument(
        "--base-path",
        help="Base path runtime completo della revisione.",
    )
    parser.add_argument(
        "--invoice",
        help=(
            "Fattura da riprocessare. Accetta numero visualizzato, id tecnico, "
            "titolo o posizione nella revisione, per esempio 32, invoice-032, "
            "'Fattura 032' o 2."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Mostra le fatture presenti nella revisione senza riprocessare.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Cartella dati runtime alternativa. Se omessa usa AppData.",
    )
    args = parser.parse_args()

    runtime_paths = get_runtime_paths(args.data_dir)
    runtime_paths.ensure_directories()

    review_batch_base_path = args.base_path
    if args.review is not None:
        review_batch_base_path = review_batch_base_path_by_number(
            args.review,
            runtime_paths=runtime_paths,
        )

    invoice_entries = _load_review_invoice_entries(review_batch_base_path, runtime_paths)
    if args.list:
        _print_review_invoices(review_batch_base_path, invoice_entries)
        return

    invoice_id = None
    if args.invoice:
        invoice_id = _resolve_invoice_selector(args.invoice, invoice_entries)
        if invoice_id != args.invoice:
            print(f"Fattura selezionata: {args.invoice} -> {invoice_id}")

    result = reprocess_review_batch(
        review_batch_base_path=review_batch_base_path,
        invoice_id=invoice_id,
        runtime_paths=runtime_paths,
    )

    print(f"Revisione aggiornata: {result.review_batch_id}")
    print(f"Base path: {result.review_batch_base_path}")
    print(
        "Documenti riprocessati: "
        f"{', '.join(result.reprocessed_document_ids) or '-'}"
    )
    print(
        "Fatture aggiornate: "
        f"{', '.join(result.updated_invoice_ids) or '-'}"
    )
    if result.unmapped_invoice_ids:
        print(
            "Fatture non rimappate: "
            f"{', '.join(result.unmapped_invoice_ids)}"
        )
    if result.new_invoice_ids_not_in_review:
        print(
            "Nuove fatture generate ma non inserite nella revisione: "
            f"{', '.join(result.new_invoice_ids_not_in_review)}"
        )


def _load_review_invoice_entries(
    review_batch_base_path: str,
    runtime_paths,
) -> list[dict]:
    review_batch_dir = review_batch_dir_from_base_path(
        review_batch_base_path,
        runtime_paths,
    )
    payload = json.loads(
        (review_batch_dir / "review_batch_index.json").read_text(encoding="utf-8")
    )
    return _unique_invoice_entries([
        invoice
        for invoice in payload.get("invoices", [])
        if isinstance(invoice, dict)
    ])


def _print_review_invoices(
    review_batch_base_path: str,
    invoice_entries: list[dict],
) -> None:
    print(f"Revisione: {review_batch_base_path}")
    for index, invoice in enumerate(invoice_entries, start=1):
        print(
            f"{index:02d}. {invoice.get('invoice_id')} | "
            f"{invoice.get('title')} | "
            f"{invoice.get('source_file_name')} | "
            f"{invoice.get('status', 'pending')}"
        )


def _resolve_invoice_selector(selector: str, invoice_entries: list[dict]) -> str:
    normalized_selector = selector.strip()
    if not normalized_selector:
        raise ValueError("Fattura da riprocessare non valida")

    invoice_entries = _unique_invoice_entries(invoice_entries)
    for invoice in invoice_entries:
        invoice_id = str(invoice.get("invoice_id") or "")
        title = str(invoice.get("title") or "")
        if normalized_selector in {invoice_id, title}:
            return invoice_id

    compact_selector = _compact_invoice_selector(normalized_selector)
    for invoice in invoice_entries:
        invoice_id = str(invoice.get("invoice_id") or "")
        title = str(invoice.get("title") or "")
        if compact_selector in {
            _compact_invoice_selector(invoice_id),
            _compact_invoice_selector(title),
        }:
            return invoice_id

    selector_number = _trailing_number(normalized_selector)
    matching_display_ids = {
        str(invoice.get("invoice_id") or "")
        for invoice in invoice_entries
        if _invoice_display_number(invoice) == selector_number
        and str(invoice.get("invoice_id") or "")
    }
    if len(matching_display_ids) == 1:
        return matching_display_ids.pop()
    if len(matching_display_ids) > 1:
        matches = ", ".join(sorted(matching_display_ids))
        raise ValueError(
            f"Numero fattura ambiguo nella revisione: {selector}. "
            f"Corrispondenze: {matches}"
        )

    if selector_number is not None and 1 <= selector_number <= len(invoice_entries):
        return str(invoice_entries[selector_number - 1].get("invoice_id") or "")

    available = ", ".join(
        str(invoice.get("invoice_id") or "-") for invoice in invoice_entries
    )
    raise ValueError(
        f"Fattura non presente nella revisione: {selector}. "
        f"Fatture disponibili: {available}"
    )


def _compact_invoice_selector(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _trailing_number(value: str) -> int | None:
    match = re.search(r"(\d+)$", value.strip())
    if not match:
        return None
    return int(match.group(1))


def _invoice_display_number(invoice: dict) -> int | None:
    display_number = invoice.get("display_number")
    try:
        if display_number is not None and str(display_number).strip():
            return int(display_number)
    except (TypeError, ValueError):
        pass

    for field in (invoice.get("title"), invoice.get("invoice_id")):
        number = _trailing_number(str(field or ""))
        if number is not None:
            return number
    return None


def _unique_invoice_entries(invoice_entries: list[dict]) -> list[dict]:
    unique_entries = []
    seen_ids = set()
    for invoice in invoice_entries:
        invoice_id = str(invoice.get("invoice_id") or "")
        identity = invoice_id or id(invoice)
        if identity in seen_ids:
            continue
        seen_ids.add(identity)
        unique_entries.append(invoice)
    return unique_entries


if __name__ == "__main__":
    main()
