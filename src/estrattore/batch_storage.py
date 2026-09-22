from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from estrattore.runtime_paths import RuntimePaths, get_runtime_paths


BATCH_INDEX_FILE_NAME = "batch_index.json"
BATCH_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PdfBatchSource:
    path: Path
    original_file_name: str
    parent_invoice_record_id: str = ""
    source_page_numbers: tuple[int, ...] = ()


@dataclass(frozen=True)
class ImportedBatchDocument:
    document_id: str
    original_file_name: str
    stored_file_name: str
    relative_path: str
    size_bytes: int
    status: str
    parent_invoice_record_id: str = ""
    source_page_numbers: tuple[int, ...] = ()


@dataclass(frozen=True)
class ImportedBatchIndex:
    schema_version: int
    batch_id: str
    label: str
    source_label: str
    created_at: str
    document_count: int
    documents: list[ImportedBatchDocument]


def import_pdf_batch(
    source_files: list[Path | PdfBatchSource],
    runtime_paths: RuntimePaths | None = None,
    now: datetime | None = None,
    source_label: str | None = None,
) -> ImportedBatchIndex:
    if not source_files:
        raise ValueError("Seleziona almeno un PDF da caricare")

    paths = runtime_paths or get_runtime_paths()
    paths.ensure_directories()
    created_at = (now or datetime.now()).replace(microsecond=0)
    batch_id = _new_batch_id(created_at, paths.batches_dir)
    batch_dir = paths.batches_dir / batch_id
    input_dir = batch_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=False)

    documents: list[ImportedBatchDocument] = []
    try:
        for document_index, source_file in enumerate(source_files, start=1):
            (
                source_path,
                original_file_name,
                parent_invoice_record_id,
                source_page_numbers,
            ) = _source_file_parts(source_file)
            if not source_path.is_file():
                raise ValueError(f"File non trovato: {source_path}")
            if Path(original_file_name).suffix.lower() != ".pdf":
                raise ValueError("Puoi caricare solo file PDF")

            document_id = f"doc_{document_index:06d}"
            stored_file_name = f"{document_id}.pdf"
            target_path = input_dir / stored_file_name
            shutil.copy2(source_path, target_path)
            documents.append(
                ImportedBatchDocument(
                    document_id=document_id,
                    original_file_name=original_file_name,
                    stored_file_name=stored_file_name,
                    relative_path=f"input/{stored_file_name}",
                    size_bytes=target_path.stat().st_size,
                    status="imported",
                    parent_invoice_record_id=parent_invoice_record_id,
                    source_page_numbers=source_page_numbers,
                )
            )

        batch_index = ImportedBatchIndex(
            schema_version=BATCH_SCHEMA_VERSION,
            batch_id=batch_id,
            label=_batch_label(created_at, len(documents), source_label),
            source_label=_batch_source_label(source_label, len(documents)),
            created_at=created_at.isoformat(),
            document_count=len(documents),
            documents=documents,
        )
        save_batch_index(batch_index, batch_dir)
        return batch_index
    except Exception:
        shutil.rmtree(batch_dir, ignore_errors=True)
        raise


def list_imported_batches(
    runtime_paths: RuntimePaths | None = None,
) -> list[ImportedBatchIndex]:
    paths = runtime_paths or get_runtime_paths()
    if not paths.batches_dir.exists():
        return []

    batches: list[ImportedBatchIndex] = []
    for index_path in paths.batches_dir.glob(f"*/{BATCH_INDEX_FILE_NAME}"):
        try:
            batches.append(load_batch_index(index_path.parent))
        except (OSError, ValueError, json.JSONDecodeError):
            continue

    return sorted(batches, key=lambda batch: batch.created_at, reverse=True)


def delete_imported_batch(
    batch_id: str,
    runtime_paths: RuntimePaths | None = None,
) -> None:
    safe_id = safe_batch_id(batch_id)
    if not safe_id or safe_id != batch_id:
        raise ValueError("Gruppo PDF non valido")

    paths = runtime_paths or get_runtime_paths()
    batch_dir = paths.batches_dir / safe_id
    if not batch_dir.exists():
        raise ValueError("Gruppo PDF non trovato")
    if not batch_dir.is_dir():
        raise ValueError("Gruppo PDF non valido")
    if not _is_inside(batch_dir, paths.batches_dir):
        raise ValueError("Percorso gruppo PDF non sicuro")

    shutil.rmtree(batch_dir)


def import_pdf_folder(
    folder_path: Path,
    runtime_paths: RuntimePaths | None = None,
    now: datetime | None = None,
) -> ImportedBatchIndex:
    if not folder_path.is_dir():
        raise ValueError(f"Cartella non trovata: {folder_path}")

    pdf_files = sorted(
        (path for path in folder_path.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda path: path.relative_to(folder_path).as_posix().lower(),
    )
    if not pdf_files:
        raise ValueError("La cartella selezionata non contiene PDF")

    return import_pdf_batch(
        [
            PdfBatchSource(
                path=pdf_file,
                original_file_name=pdf_file.relative_to(folder_path).as_posix(),
            )
            for pdf_file in pdf_files
        ],
        runtime_paths=runtime_paths,
        now=now,
        source_label=folder_path.name,
    )


def load_batch_index(batch_dir: Path) -> ImportedBatchIndex:
    payload = json.loads((batch_dir / BATCH_INDEX_FILE_NAME).read_text(encoding="utf-8"))
    documents = [
        ImportedBatchDocument(
            document_id=str(document.get("document_id") or ""),
            original_file_name=str(document.get("original_file_name") or ""),
            stored_file_name=str(document.get("stored_file_name") or ""),
            relative_path=str(document.get("relative_path") or ""),
            size_bytes=int(document.get("size_bytes") or 0),
            status=str(document.get("status") or "imported"),
            parent_invoice_record_id=str(
                document.get("parent_invoice_record_id") or ""
            ),
            source_page_numbers=tuple(
                int(page_number)
                for page_number in document.get("source_page_numbers", [])
                if isinstance(page_number, int | float | str)
                and str(page_number).strip().isdigit()
            ),
        )
        for document in payload.get("documents", [])
        if isinstance(document, dict)
    ]
    return ImportedBatchIndex(
        schema_version=int(payload.get("schema_version") or BATCH_SCHEMA_VERSION),
        batch_id=str(payload.get("batch_id") or batch_dir.name),
        label=str(payload.get("label") or batch_dir.name),
        source_label=str(payload.get("source_label") or payload.get("label") or batch_dir.name),
        created_at=str(payload.get("created_at") or ""),
        document_count=int(payload.get("document_count") or len(documents)),
        documents=documents,
    )


def save_batch_index(batch_index: ImportedBatchIndex, batch_dir: Path) -> Path:
    output_path = batch_dir / BATCH_INDEX_FILE_NAME
    temporary_path = output_path.with_name(f".{output_path.name}.writing")
    temporary_path.write_text(
        json.dumps(asdict(batch_index), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path


def _new_batch_id(created_at: datetime, batches_dir: Path) -> str:
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    base_id = f"batch_{timestamp}"
    if not (batches_dir / base_id).exists():
        return base_id

    return f"{base_id}_{uuid4().hex[:8]}"


def _batch_label(
    created_at: datetime,
    document_count: int,
    source_label: str | None = None,
) -> str:
    date_label = created_at.strftime("%d-%m-%Y %H:%M")
    document_word = "PDF" if document_count == 1 else "PDF"
    clean_source_label = _clean_batch_source_label(source_label)
    if clean_source_label:
        return f"{clean_source_label} - {document_count} {document_word}"
    return f"{date_label} - {document_count} {document_word}"


def _batch_source_label(source_label: str | None, document_count: int) -> str:
    clean_source_label = _clean_batch_source_label(source_label)
    if clean_source_label:
        return clean_source_label
    return f"{document_count} PDF selezionati"


def _clean_batch_source_label(source_label: str | None) -> str | None:
    if not source_label:
        return None
    clean_source_label = str(source_label).strip().strip("\\/")
    if not clean_source_label:
        return None
    return Path(clean_source_label).name or clean_source_label


def _source_file_parts(
    source_file: Path | PdfBatchSource,
) -> tuple[Path, str, str, tuple[int, ...]]:
    if isinstance(source_file, PdfBatchSource):
        return (
            source_file.path,
            _safe_original_file_name(source_file.original_file_name),
            source_file.parent_invoice_record_id,
            source_file.source_page_numbers,
        )

    return source_file, source_file.name, "", ()


def _safe_original_file_name(value: str) -> str:
    normalized_value = str(value).replace("\\", "/").strip("/")
    safe_parts = [
        part
        for part in normalized_value.split("/")
        if part and part not in {".", ".."}
    ]
    if not safe_parts:
        return Path(value).name
    return "/".join(safe_parts)


def safe_batch_id(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z_-]+", "_", value.strip())
    return normalized.strip("_")


def _is_inside(path: Path, root: Path) -> bool:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    return resolved_path == resolved_root or resolved_root in resolved_path.parents
