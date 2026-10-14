"""Create isolated synthetic desktop data for visual checks.

Windows: build\\packaging-venv\\Scripts\\python.exe scripts\\ui_demo.py
Use --reset after closing the demo window to restore the initial fixture.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from openpyxl import Workbook


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from estrattore.batch_storage import import_pdf_batch
from estrattore.invoice_export import export_invoices
from estrattore.invoice_registry_storage import (
    discard_reviewed_invoice,
    load_invoice_registry,
    save_reviewed_invoice,
)
from estrattore.processing_storage import list_review_batches, process_imported_batch
from estrattore.registry import APP_REGISTRY_EXCEL_HEADERS, build_registry_cache_from_excel
from estrattore.runtime_paths import get_runtime_paths
from estrattore.source_archive_storage import promote_batch_sources


DEMO_ROOT = (PROJECT_ROOT / "build" / "ui-demo-data").resolve()
MARKER = ".ui-demo-v1.json"
PAGE_SIZE = (1240, 1754)
DEMO_DATE = datetime(2026, 10, 13, 10, 0)
FIELD_VALUES = (
    ("codice_fiscale_emittente", "C.F. emittente", "12345678903"),
    ("nome_emittente", "Nome emittente", "IMPRESA DIMOSTRATIVA SRL"),
    ("p_iva", "P.IVA", "12345678903"),
    ("codice_fiscale", "C.F. condominio", "81005680533"),
    ("condominio", "Condominio", "CONDOMINIO ESEMPIO"),
    ("numero_fattura", "Numero fattura", ""),
    ("data_fattura", "Data fattura", "13-10-2026"),
    ("pagata_il", "Pagata il", ""),
    ("totale_fattura", "Totale fattura", "125.00"),
    ("ritenuta", "Ritenuta", "5.00"),
    ("totale_netto_fattura", "Totale netto fattura", "120.00"),
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _page(number: int) -> Image.Image:
    image = Image.new("RGB", PAGE_SIZE, "white")
    draw = ImageDraw.Draw(image)
    draw.text((95, 90), "DOCUMENTO DIMOSTRATIVO - NON REALE", fill="#27634d", font=_font(34))
    draw.line((95, 170, 1140, 170), fill="#27634d", width=4)
    draw.text((95, 245), "IMPRESA DIMOSTRATIVA SRL", fill="#222222", font=_font(45))
    draw.text((95, 320), "C.F. / P.IVA 12345678903", fill="#444444", font=_font(28))
    draw.text((95, 480), "FATTURA", fill="#222222", font=_font(52))
    draw.text((95, 565), f"Numero: DEMO-{number:03d}     Data: 13/10/2026", fill="#333333", font=_font(30))
    draw.text((95, 720), "Destinatario: CONDOMINIO ESEMPIO", fill="#333333", font=_font(30))
    draw.text((95, 775), "C.F. 81005680533", fill="#333333", font=_font(28))
    draw.line((95, 970, 1140, 970), fill="#777777", width=2)
    draw.text((95, 1020), "Servizio dimostrativo", fill="#333333", font=_font(30))
    draw.text((820, 1020), "125.00", fill="#333333", font=_font(30))
    draw.line((95, 1150, 1140, 1150), fill="#777777", width=2)
    draw.text((650, 1220), "Totale fattura", fill="#333333", font=_font(29))
    draw.text((960, 1220), "125.00", fill="#333333", font=_font(29))
    draw.text((650, 1280), "Ritenuta", fill="#333333", font=_font(29))
    draw.text((977, 1280), "5.00", fill="#333333", font=_font(29))
    draw.text((650, 1340), "Netto", fill="#333333", font=_font(29))
    draw.text((960, 1340), "120.00", fill="#333333", font=_font(29))
    draw.text((95, 1650), "SOLO PER TEST GRAFICI", fill="#777777", font=_font(24))
    return image


def _fields(number: int) -> list[dict]:
    return [
        {
            "kind": kind,
            "label": label,
            "value": f"DEMO-{number:03d}" if kind == "numero_fattura" else value,
            "score": 0.96,
            "page": number,
            "bbox": None,
        }
        for kind, label, value in FIELD_VALUES
    ]


def _seed_registry(paths) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(APP_REGISTRY_EXCEL_HEADERS)
    sheet.append(["D-001", "IMPRESA DIMOSTRATIVA SRL", "12345678903", "12345678903", "Altro soggetto", "13-10-2026", "demo"])
    sheet.append(["D-002", "CONDOMINIO ESEMPIO", "81005680533", "", "Condominio", "13-10-2026", "demo"])
    paths.registry_input_file.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(paths.registry_input_file)
    workbook.close()
    build_registry_cache_from_excel(paths.registry_input_file, paths.registry_cache_file)


def _seed_review(paths, source_pdf: Path, pages: list[Image.Image]):
    imported = import_pdf_batch([source_pdf], runtime_paths=paths, now=DEMO_DATE)

    def processor(_source_path: Path, output_dir: Path, first_invoice_number: int) -> list[Path]:
        output_dir.mkdir(parents=True)
        viewer_pages = []
        invoices = []
        for index, image in enumerate(pages, start=1):
            image_name = f"page-{index}.png"
            image.save(output_dir / image_name)
            viewer_pages.append({
                "page": index, "width": PAGE_SIZE[0], "height": PAGE_SIZE[1],
                "image": image_name, "tokens": [], "spans": [], "targets": [],
            })
            display_number = first_invoice_number + index - 1
            invoices.append({
                "invoice_id": f"invoice-{display_number:03d}",
                "display_number": display_number,
                "title": f"Fattura {display_number:03d}",
                "page_numbers": [index],
                "fields": _fields(index),
            })
        manifest = output_dir / "viewer_manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "source_file": source_pdf.name,
            "pages": viewer_pages,
            "invoices": invoices,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return [manifest]

    run = process_imported_batch(
        imported.batch_id, runtime_paths=paths, document_processor=processor,
        now=DEMO_DATE, review_batch_size=15,
    )
    if run.status != "processed" or len(run.review_batches) != 1:
        raise RuntimeError(f"Revisione dimostrativa non creata: {run.errors}")
    promote_batch_sources(imported, run.process_id, paths)
    return imported, run


def _review_payload(review, number: int) -> dict:
    invoice_id = f"invoice-{number:03d}"
    return {
        "review_batch_base_path": review.base_path,
        "selected_batch_id": review.id,
        "invoice": {
            "invoiceId": invoice_id,
            "manifestInvoiceId": invoice_id,
            "title": f"Fattura {number:03d}",
            "documentId": "doc_000001",
            "sourceFile": "fatture_dimostrative.pdf",
            "pageNumbers": [number],
            "fields": _fields(number),
        },
    }


def create_demo_data(root: Path = DEMO_ROOT) -> Path:
    root = root.resolve()
    marker = root / MARKER
    if marker.is_file():
        return root
    if root.exists() and any(root.iterdir()):
        raise RuntimeError(f"Cartella demo non vuota e senza marcatore: {root}")
    root.mkdir(parents=True, exist_ok=True)
    paths = get_runtime_paths(root)
    paths.ensure_directories()
    _seed_registry(paths)

    pages = [_page(number) for number in range(1, 5)]
    source_pdf = root / "fatture_dimostrative.pdf"
    pages[0].save(source_pdf, format="PDF", save_all=True, append_images=pages[1:])
    imported, run = _seed_review(paths, source_pdf, pages)
    review = run.review_batches[0]
    unexported = save_reviewed_invoice(_review_payload(review, 2), paths)
    exported = save_reviewed_invoice(_review_payload(review, 3), paths)
    discarded = discard_reviewed_invoice(_review_payload(review, 4), paths)
    export_invoices([exported["record_id"]], "workbook", paths)

    job = {
        "job_id": "job_demo_completed", "batch_id": imported.batch_id,
        "process_id": run.process_id, "status": "completed",
        "message": "Processamento completato", "created_at": DEMO_DATE.isoformat(),
        "progress_current": 1, "progress_total": 1,
        "files": [{"document_id": "doc_000001", "source_id": "", "name": source_pdf.name, "size_bytes": source_pdf.stat().st_size}],
        "source_kind": "external_upload", "archive_sources": True,
        "result": {"invoice_count": 4, "review_batch_count": 1},
    }
    (paths.config_dir / "processing_jobs.json").write_text(json.dumps({
        "schema_version": 1, "active_job_id": None, "queued_job_ids": [], "jobs": [job],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    registry = load_invoice_registry(paths)
    if len(list_review_batches(paths)) != 1 or len(registry["records"]) != 3:
        raise RuntimeError("Fixture demo incompleta")
    marker.write_text(json.dumps({
        "version": 1, "review": review.id,
        "records": [unexported["record_id"], exported["record_id"], discarded["record_id"]],
    }, indent=2), encoding="utf-8")
    return root


def _reset_demo_data() -> None:
    if DEMO_ROOT.parent != (PROJECT_ROOT / "build").resolve() or not (DEMO_ROOT / MARKER).is_file():
        raise RuntimeError("Reset ammesso solo per la fixture generata in build/ui-demo-data")
    lock_path = DEMO_ROOT / "config" / "desktop.lock"
    if lock_path.is_file():
        import msvcrt

        with lock_path.open("r+b") as handle:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Chiudi la finestra demo prima di ripristinare i dati") from error
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    shutil.rmtree(DEMO_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apri la finestra desktop con dati sintetici isolati")
    parser.add_argument("--prepare-only", action="store_true", help="Crea i dati senza aprire la finestra")
    parser.add_argument("--reset", action="store_true", help="Ricrea i dati dimostrativi iniziali")
    args = parser.parse_args()
    if args.reset:
        _reset_demo_data()
    root = create_demo_data()
    print(f"Dati sintetici: {root}", flush=True)
    if args.prepare_only:
        return 0
    if os.name != "nt":
        raise RuntimeError("La finestra desktop richiede Windows")
    from scripts.run_app import _viewer_build_is_missing_or_stale, _build_viewer
    if _viewer_build_is_missing_or_stale():
        _build_viewer()
    return subprocess.call([
        sys.executable, str(PROJECT_ROOT / "scripts" / "desktop_app.py"),
        "--data-dir", str(root), "--dev-instance",
    ], cwd=PROJECT_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
