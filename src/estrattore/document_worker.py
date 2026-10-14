from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


DOCUMENT_WORKER_SCHEMA_VERSION = 1
DOCUMENT_COMPLETE_FILE_NAME = "document_complete.ok"

DocumentProcessor = Callable[[Path, Path, int], list[Path]]


@dataclass(frozen=True)
class DocumentWorkerRequest:
    schema_version: int
    source_path: str
    working_output_dir: str
    output_dir: str
    document_id: str
    first_invoice_number: int


@dataclass(frozen=True)
class DocumentWorkerResult:
    schema_version: int
    status: str
    document_id: str
    invoice_count: int = 0
    error: str = ""
    traceback: str = ""


def save_worker_request(request: DocumentWorkerRequest, path: Path) -> None:
    _write_json_atomic(path, asdict(request))


def load_worker_request(path: Path) -> DocumentWorkerRequest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    request = DocumentWorkerRequest(**payload)
    if request.schema_version != DOCUMENT_WORKER_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported document worker schema: {request.schema_version}"
        )
    return request


def save_worker_result(result: DocumentWorkerResult, path: Path) -> None:
    _write_json_atomic(path, asdict(result))


def load_worker_result(path: Path) -> DocumentWorkerResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = DocumentWorkerResult(**payload)
    if result.schema_version != DOCUMENT_WORKER_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported document worker schema: {result.schema_version}"
        )
    return result


def process_document_request(
    request: DocumentWorkerRequest,
    processor: DocumentProcessor | None = None,
) -> DocumentWorkerResult:
    source_path = Path(request.source_path)
    working_output_dir = Path(request.working_output_dir)
    output_dir = Path(request.output_dir)
    selected_processor = processor or _default_document_processor

    existing_result = _completed_result(output_dir, request.document_id)
    if existing_result is not None:
        return existing_result

    shutil.rmtree(working_output_dir, ignore_errors=True)
    if output_dir.exists() and not _is_document_complete(output_dir):
        shutil.rmtree(output_dir, ignore_errors=True)

    try:
        saved_paths = selected_processor(
            source_path,
            working_output_dir,
            request.first_invoice_number,
        )
        manifest_path = _manifest_path(saved_paths, working_output_dir)
        invoice_count = _validate_manifest(manifest_path)

        shutil.rmtree(output_dir, ignore_errors=True)
        working_output_dir.replace(output_dir)
        _mark_document_complete(
            output_dir=output_dir,
            document_id=request.document_id,
            invoice_count=invoice_count,
        )
        return DocumentWorkerResult(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            status="completed",
            document_id=request.document_id,
            invoice_count=invoice_count,
        )
    except Exception as error:
        shutil.rmtree(working_output_dir, ignore_errors=True)
        return DocumentWorkerResult(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            status="error",
            document_id=request.document_id,
            error=str(error),
            traceback=traceback.format_exc(),
        )


def run_worker(request_path: Path, result_path: Path, start_gate: Path) -> int:
    try:
        request = load_worker_request(request_path)
        _wait_for_start_gate(start_gate)
        result = process_document_request(request)
    except Exception as error:
        result = DocumentWorkerResult(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            status="error",
            document_id="",
            error=str(error),
            traceback=traceback.format_exc(),
        )

    save_worker_result(result, result_path)
    return 0 if result.status == "completed" else 1


def _wait_for_start_gate(start_gate: Path) -> None:
    while not start_gate.is_file():
        time.sleep(0.02)


def _default_document_processor(
    source_path: Path,
    output_dir: Path,
    first_invoice_number: int,
) -> list[Path]:
    from scripts.debug_words import debug_words_file

    return debug_words_file(
        source_path,
        output_dir=output_dir,
        first_invoice_number=first_invoice_number,
        quality_fallback_mode="crop-only",
    )


def _manifest_path(saved_paths: list[Path], output_dir: Path) -> Path:
    manifest_path = next(
        (path for path in saved_paths if path.name == "viewer_manifest.json"),
        output_dir / "viewer_manifest.json",
    )
    if not manifest_path.is_file():
        raise RuntimeError("viewer_manifest.json non generato")
    return manifest_path


def _validate_manifest(manifest_path: Path) -> int:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    invoices = payload.get("invoices")
    if not isinstance(invoices, list):
        raise ValueError("Manifest documento privo della lista fatture")
    return len(
        [
            invoice
            for invoice in invoices
            if isinstance(invoice, dict) and invoice.get("invoice_id")
        ]
    )


def _mark_document_complete(
    output_dir: Path,
    document_id: str,
    invoice_count: int,
) -> None:
    _write_json_atomic(
        output_dir / DOCUMENT_COMPLETE_FILE_NAME,
        {
            "document_id": document_id,
            "invoice_count": invoice_count,
            "completed_at": datetime.now().replace(microsecond=0).isoformat(),
        },
    )


def _is_document_complete(output_dir: Path) -> bool:
    return (
        output_dir.is_dir()
        and (output_dir / "viewer_manifest.json").is_file()
        and (output_dir / DOCUMENT_COMPLETE_FILE_NAME).is_file()
    )


def _completed_result(
    output_dir: Path,
    document_id: str,
) -> DocumentWorkerResult | None:
    if not _is_document_complete(output_dir):
        return None
    payload = json.loads(
        (output_dir / DOCUMENT_COMPLETE_FILE_NAME).read_text(encoding="utf-8")
    )
    return DocumentWorkerResult(
        schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
        status="completed",
        document_id=str(payload.get("document_id") or document_id),
        invoice_count=int(payload.get("invoice_count") or 0),
    )


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.writing")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Internal document OCR worker")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--start-gate", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    from estrattore.ocr_child_processes import install_worker_ocr_process_policy

    install_worker_ocr_process_policy()
    args = _parse_args()
    return run_worker(args.request, args.result, args.start_gate)


if __name__ == "__main__":
    sys.exit(main())
