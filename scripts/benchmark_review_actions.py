"""Profile group review persistence on synthetic data, never on user archives."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from pypdf import PdfWriter

from estrattore.batch_storage import import_pdf_batch
from estrattore.invoice_registry_storage import (
    discard_reviewed_invoice,
    save_reviewed_invoice,
)
from estrattore.processing_storage import process_imported_batch
from estrattore.runtime_paths import get_runtime_paths
from estrattore.source_archive_storage import promote_batch_sources


def run_case(
    root: Path, action: str, invoice_count: int, registry_mb: int,
) -> float:
    root.mkdir(parents=True)
    source = root / "synthetic.pdf"
    writer = PdfWriter()
    for _ in range(invoice_count * 2):
        writer.add_blank_page(width=595, height=842)
    with source.open("wb") as output:
        writer.write(output)

    paths = get_runtime_paths(root / "data")
    if registry_mb:
        paths.invoice_registry_dir.mkdir(parents=True, exist_ok=True)
        tokens = [
            {"text": f"SYNTHETIC-{index}", "bbox": [40, index, 150, index + 12], "score": 0.87}
            for index in range(500)
        ]
        template = {
            "status": "saved",
            "invoice": {"fields": [], "pageNumbers": [1], "pages": [{"page": 1, "tokens": tokens}]},
        }
        approximate_size = len(json.dumps(template, indent=2))
        record_count = max(1, registry_mb * 1024 * 1024 // approximate_size)
        records = [{**template, "record_id": f"synthetic-{index}"} for index in range(record_count)]
        paths.invoice_registry_file.write_text(
            json.dumps({"schema_version": 3, "updated_at": "", "records": records}, indent=2),
            encoding="utf-8",
        )
    batch = import_pdf_batch([source], runtime_paths=paths)

    def processor(_source: Path, output_dir: Path, first_number: int) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = output_dir / "viewer_manifest.json"
        invoices = []
        for index in range(invoice_count):
            number = first_number + index
            invoices.append({
                "invoice_id": f"invoice-{number:03d}",
                "title": f"Fattura {number:03d}",
                "page_numbers": [index * 2 + 1, index * 2 + 2],
                "fields": [],
            })
        manifest_path.write_text(
            json.dumps({"schema_version": 1, "source_file": source.name, "pages": [], "invoices": invoices}),
            encoding="utf-8",
        )
        return [manifest_path]

    run = process_imported_batch(batch.batch_id, runtime_paths=paths, document_processor=processor)
    promote_batch_sources(batch, run.process_id, paths)
    review = run.review_batches[0]
    operation = save_reviewed_invoice if action == "save" else discard_reviewed_invoice
    invoices = []
    for index in range(invoice_count):
        number = index + 1
        invoice_id = f"invoice-{number:03d}"
        invoices.append({
                "invoiceId": invoice_id,
                "manifestInvoiceId": invoice_id,
                "documentId": "doc_000001",
                "sourceFile": source.name,
                "basePath": f"/runtime/manifests/{run.process_id}/documents/doc_000001",
                "pageNumbers": [index * 2 + 1, index * 2 + 2],
                "fields": [{"kind": "numero_fattura", "label": "Numero fattura", "value": str(number)}],
        })
    started = time.perf_counter()
    for invoice in invoices:
        operation({
            "selected_batch_id": review.id,
            "review_batch_base_path": review.base_path,
            "invoice": invoice,
        }, runtime_paths=paths)
    return time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--registry-mb", type=int, default=0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="review-benchmark-") as temporary:
        root = Path(temporary)
        for action in ("save", "discard"):
            duration = run_case(root / action, action, args.count, args.registry_mb)
            print(f"{action}: {args.count} invoices in {duration:.3f}s ({duration / args.count:.3f}s/invoice)")


if __name__ == "__main__":
    main()
