from __future__ import annotations

from pathlib import Path

from estrattore.io import load_scan
from estrattore.model import (
    DocumentStatus,
    InvoiceResult,
    PerceptionBackend,
    init_invoice_result,
)
from estrattore.perception import analyze_pages


def process_invoice(
    file_path: Path,
    perception_backend: PerceptionBackend = PerceptionBackend.EMPTY,
) -> InvoiceResult:
    result = init_invoice_result(file_path)

    try:
        images = load_scan(file_path)
    except Exception as error:
        result.status = DocumentStatus.REJECTED
        result.rejection_reason = f"scan load failed: {error}"
        return result

    if not images:
        result.status = DocumentStatus.REJECTED
        result.rejection_reason = "scan load returned no images"
        return result

    result.status = DocumentStatus.LOADED
    try:
        result.document = analyze_pages(images, file_path, backend=perception_backend)
    except Exception as error:
        result.status = DocumentStatus.REJECTED
        result.rejection_reason = f"perception failed: {error}"
        return result

    result.status = DocumentStatus.ANALYZED
    return result
