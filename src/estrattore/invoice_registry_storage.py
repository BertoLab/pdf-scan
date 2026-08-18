from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from estrattore.invoice_pdf_archive import archive_invoice_pdf
from estrattore.processing_storage import load_review_invoice_ref, mark_review_invoice
from estrattore.runtime_paths import RuntimePaths, get_runtime_paths


INVOICE_REGISTRY_SCHEMA_VERSION = 1
INVOICE_EXPORT_COLUMNS = [
    ("saved_at", "Salvata il"),
    ("nome_emittente", "Nome emittente"),
    ("codice_fiscale_emittente", "C.F. emittente"),
    ("p_iva", "P.IVA emittente"),
    ("condominio", "Condominio"),
    ("codice_fiscale", "C.F. condominio"),
    ("numero_fattura", "Numero fattura"),
    ("data_fattura", "Data fattura"),
    ("totale_fattura", "Totale fattura"),
    ("ritenuta", "Ritenuta"),
    ("totale_netto_fattura", "Totale netto fattura"),
    ("pagata_il", "Data pagamento"),
    ("source_file_name", "File origine"),
    ("archived_pdf_path", "PDF archiviato"),
    ("archived_source_pdf_path", "PDF sorgente"),
]


def load_invoice_registry(
    runtime_paths: RuntimePaths | None = None,
) -> dict[str, Any]:
    paths = runtime_paths or get_runtime_paths()
    if not paths.invoice_registry_file.exists():
        return _empty_registry()

    payload = json.loads(paths.invoice_registry_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Registro fatture non valido")

    payload.setdefault("schema_version", INVOICE_REGISTRY_SCHEMA_VERSION)
    payload.setdefault("updated_at", "")
    payload.setdefault("records", [])
    return payload


def save_reviewed_invoice(
    payload: dict[str, Any],
    runtime_paths: RuntimePaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()

    invoice_payload = _invoice_payload(payload)
    review_batch_base_path = _required_string(payload, "review_batch_base_path")
    record_id = _record_id(invoice_payload, review_batch_base_path)
    raw_invoice_id = _raw_invoice_id(invoice_payload)
    invoice_ref = load_review_invoice_ref(review_batch_base_path, raw_invoice_id, paths)
    archived_pdf = archive_invoice_pdf(invoice_payload, invoice_ref, record_id, paths)
    saved_at = _timestamp(now)
    registry_record = {
        "record_id": record_id,
        "status": "saved",
        "saved_at": saved_at,
        "updated_at": saved_at,
        "review_batch_base_path": review_batch_base_path,
        "selected_batch_id": str(payload.get("selected_batch_id") or ""),
        "source_batch_id": invoice_ref.source_batch_id,
        "document_id": invoice_ref.document_id,
        "source_file_name": invoice_ref.source_file_name,
        "archived_pdf_path": archived_pdf.relative_path,
        "archived_pdf_url": f"/runtime/{archived_pdf.relative_path}",
        "archived_pdf_page_numbers": archived_pdf.page_numbers,
        "archived_source_pdf_path": archived_pdf.archived_source_relative_path,
        "archived_source_pdf_url": f"/runtime/{archived_pdf.archived_source_relative_path}",
        "invoice": invoice_payload,
    }

    registry = load_invoice_registry(paths)
    registry["records"] = _upsert_record(
        registry.get("records", []),
        registry_record,
    )
    registry["updated_at"] = saved_at
    _write_registry(paths.invoice_registry_file, registry)
    mark_review_invoice(
        review_batch_base_path,
        raw_invoice_id,
        "saved",
        paths,
    )
    return registry_record


def discard_reviewed_invoice(
    payload: dict[str, Any],
    runtime_paths: RuntimePaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()

    invoice_payload = _invoice_payload(payload)
    review_batch_base_path = _required_string(payload, "review_batch_base_path")
    record_id = _record_id(invoice_payload, review_batch_base_path)
    raw_invoice_id = _raw_invoice_id(invoice_payload)
    discarded_at = _timestamp(now)
    registry_record = {
        "record_id": record_id,
        "status": "discarded",
        "discarded_at": discarded_at,
        "updated_at": discarded_at,
        "review_batch_base_path": review_batch_base_path,
        "selected_batch_id": str(payload.get("selected_batch_id") or ""),
        "invoice": invoice_payload,
    }

    registry = load_invoice_registry(paths)
    registry["records"] = _upsert_record(
        registry.get("records", []),
        registry_record,
    )
    registry["updated_at"] = discarded_at
    _write_registry(paths.invoice_registry_file, registry)
    mark_review_invoice(
        review_batch_base_path,
        raw_invoice_id,
        "discarded",
        paths,
    )
    return registry_record


def save_invoice_registry_excel(
    output_path: Path | None = None,
    runtime_paths: RuntimePaths | None = None,
) -> Path:
    paths = runtime_paths or get_runtime_paths()
    registry = load_invoice_registry(paths)
    target_path = output_path or paths.invoice_registry_export_xlsx_file
    records = [
        record
        for record in registry.get("records", [])
        if isinstance(record, dict) and record.get("status") == "saved"
    ]

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Registro fatture"
    header_fill = PatternFill("solid", fgColor="DDEFE6")
    header_font = Font(bold=True, color="174C3A")
    for column_index, (_, label) in enumerate(INVOICE_EXPORT_COLUMNS, start=1):
        cell = sheet.cell(row=1, column=column_index, value=label)
        cell.fill = header_fill
        cell.font = header_font

    for row_index, record in enumerate(records, start=2):
        row_values = _invoice_export_row(record)
        for column_index, (column_key, _) in enumerate(INVOICE_EXPORT_COLUMNS, start=1):
            sheet.cell(row=row_index, column=column_index, value=row_values.get(column_key, ""))

    for column_cells in sheet.columns:
        max_length = max(
            len(str(cell.value)) if cell.value is not None else 0
            for cell in column_cells
        )
        sheet.column_dimensions[column_cells[0].column_letter].width = min(
            max(max_length + 2, 14),
            44,
        )
    sheet.freeze_panes = "A2"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(target_path)
    return target_path


def save_invoice_registry_records(
    records: list[dict[str, Any]],
    runtime_paths: RuntimePaths | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    paths = runtime_paths or get_runtime_paths()
    registry = load_invoice_registry(paths)
    existing_records = [
        record
        for record in registry.get("records", [])
        if isinstance(record, dict)
    ]
    existing_by_id = {
        str(record.get("record_id") or ""): record
        for record in existing_records
        if str(record.get("record_id") or "")
    }
    updated_at = _timestamp(now)
    next_records: list[dict[str, Any]] = []

    for record_payload in records:
        if not isinstance(record_payload, dict):
            continue
        record_id = str(record_payload.get("record_id") or "").strip()
        if not record_id or record_id not in existing_by_id:
            continue

        existing_record = existing_by_id[record_id]
        if existing_record.get("status") != "saved":
            next_records.append(existing_record)
            continue

        next_record = {
            **existing_record,
            "updated_at": updated_at,
            "invoice": _updated_invoice_payload(
                existing_record.get("invoice"),
                record_payload.get("invoice"),
            ),
        }
        next_records.append(next_record)

    submitted_record_ids = {
        str(record.get("record_id") or "")
        for record in records
        if isinstance(record, dict)
    }
    next_records.extend(
        record
        for record in existing_records
        if str(record.get("record_id") or "") not in submitted_record_ids
    )

    registry["records"] = next_records
    registry["updated_at"] = updated_at
    _write_registry(paths.invoice_registry_file, registry)
    return registry


def _invoice_payload(payload: dict[str, Any]) -> dict[str, Any]:
    invoice = payload.get("invoice")
    if not isinstance(invoice, dict):
        raise ValueError("Fattura da salvare mancante")

    invoice_id = str(invoice.get("invoiceId") or "").strip()
    if not invoice_id:
        raise ValueError("Identificativo fattura mancante")

    fields = invoice.get("fields", [])
    if not isinstance(fields, list):
        raise ValueError("Campi fattura non validi")

    return {
        "invoiceId": invoice_id,
        "manifestInvoiceId": str(invoice.get("manifestInvoiceId") or ""),
        "title": str(invoice.get("title") or ""),
        "documentId": str(invoice.get("documentId") or ""),
        "sourceFile": str(invoice.get("sourceFile") or ""),
        "basePath": str(invoice.get("basePath") or ""),
        "pageNumbers": _page_numbers_payload(invoice),
        "pages": invoice.get("pages", []),
        "fields": [
            field
            for field in fields
            if isinstance(field, dict) and str(field.get("kind") or "").strip()
        ],
    }


def _updated_invoice_payload(
    existing_invoice: object,
    submitted_invoice: object,
) -> dict[str, Any]:
    if not isinstance(existing_invoice, dict):
        existing_invoice = {}
    if not isinstance(submitted_invoice, dict):
        return dict(existing_invoice)

    existing_fields = existing_invoice.get("fields", [])
    submitted_fields = submitted_invoice.get("fields", [])
    if not isinstance(existing_fields, list) or not isinstance(submitted_fields, list):
        return dict(existing_invoice)

    submitted_values = {
        str(field.get("kind") or ""): str(field.get("value") or "")
        for field in submitted_fields
        if isinstance(field, dict) and str(field.get("kind") or "").strip()
    }
    next_fields = []
    existing_kinds: set[str] = set()
    for field in existing_fields:
        if not isinstance(field, dict):
            continue
        kind = str(field.get("kind") or "").strip()
        if not kind:
            continue
        existing_kinds.add(kind)
        next_fields.append({
            **field,
            "value": submitted_values.get(kind, str(field.get("value") or "")),
        })

    for field in submitted_fields:
        if not isinstance(field, dict):
            continue
        kind = str(field.get("kind") or "").strip()
        if not kind or kind in existing_kinds:
            continue
        next_fields.append({
            **field,
            "kind": kind,
            "value": str(field.get("value") or ""),
        })

    return {
        **existing_invoice,
        "fields": next_fields,
    }


def _record_id(invoice_payload: dict[str, Any], review_batch_base_path: str) -> str:
    return ":".join(
        [
            "invoice",
            review_batch_base_path.strip("/").replace("/", ":"),
            str(invoice_payload["invoiceId"]),
        ]
    )


def _raw_invoice_id(invoice_payload: dict[str, Any]) -> str:
    manifest_invoice_id = str(invoice_payload.get("manifestInvoiceId") or "").strip()
    if manifest_invoice_id:
        return manifest_invoice_id

    invoice_id = str(invoice_payload["invoiceId"])
    return invoice_id.rsplit(":", 1)[-1]


def _page_numbers_payload(invoice: dict[str, Any]) -> list[int]:
    raw_page_numbers = invoice.get("pageNumbers")
    if not isinstance(raw_page_numbers, list):
        return []

    page_numbers: list[int] = []
    for page_number in raw_page_numbers:
        try:
            page_numbers.append(int(page_number))
        except (TypeError, ValueError):
            continue
    return page_numbers


def _invoice_export_row(record: dict[str, Any]) -> dict[str, str]:
    invoice = record.get("invoice") if isinstance(record.get("invoice"), dict) else {}
    field_values = _field_values(invoice)
    return {
        "saved_at": _date_part(str(record.get("saved_at") or "")),
        "nome_emittente": field_values.get("nome_emittente", ""),
        "codice_fiscale_emittente": field_values.get("codice_fiscale_emittente", ""),
        "p_iva": field_values.get("p_iva", ""),
        "condominio": field_values.get("condominio", ""),
        "codice_fiscale": field_values.get("codice_fiscale", ""),
        "numero_fattura": field_values.get("numero_fattura", ""),
        "data_fattura": field_values.get("data_fattura", ""),
        "totale_fattura": field_values.get("totale_fattura", ""),
        "ritenuta": field_values.get("ritenuta", ""),
        "totale_netto_fattura": field_values.get("totale_netto_fattura", ""),
        "pagata_il": field_values.get("pagata_il", ""),
        "source_file_name": str(record.get("source_file_name") or ""),
        "archived_pdf_path": str(record.get("archived_pdf_path") or ""),
        "archived_source_pdf_path": str(record.get("archived_source_pdf_path") or ""),
    }


def _field_values(invoice: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    fields = invoice.get("fields", [])
    if not isinstance(fields, list):
        return values

    for field in fields:
        if not isinstance(field, dict):
            continue
        kind = str(field.get("kind") or "").strip()
        value = str(field.get("value") or "").strip()
        if kind:
            values[kind] = value
    return values


def _date_part(value: str) -> str:
    return value[:10] if len(value) >= 10 else value


def _upsert_record(
    records: object,
    next_record: dict[str, Any],
) -> list[dict[str, Any]]:
    current_records = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("record_id") != next_record["record_id"]
    ]
    current_records.append(next_record)
    return current_records


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"Campo obbligatorio mancante: {key}")
    return value


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now()).replace(microsecond=0).isoformat()


def _empty_registry() -> dict[str, Any]:
    return {
        "schema_version": INVOICE_REGISTRY_SCHEMA_VERSION,
        "updated_at": "",
        "records": [],
    }


def _write_registry(output_path: Path, payload: dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.writing")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
