from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class FieldStatus(StrEnum):
    ACCEPTED = "accepted"
    UNCERTAIN = "uncertain"
    MISSING = "missing"
    CONFLICT = "conflict"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    LOADED = "loaded"
    ANALYZED = "analyzed"
    PROCESSED = "processed"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class LayoutBlockType(StrEnum):
    TEXT = "text"
    TITLE = "title"
    TABLE = "table"
    FIGURE = "figure"
    UNKNOWN = "unknown"


class PerceptionBackend(StrEnum):
    EMPTY = "empty"
    TESSERACT = "tesseract"


@dataclass(frozen=True)
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass
class InvoiceField:
    name: str
    value: str | None
    status: FieldStatus
    confidence: float | None = None
    page: int | None = None
    bbox: BoundingBox | None = None
    evidence: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass
class OcrToken:
    text: str
    bbox: BoundingBox
    confidence: float | None = None
    source: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class OcrHoleRegion:
    bbox: BoundingBox
    reason: str
    ink_density: float
    token_density: float
    token_count: int


@dataclass
class LayoutBlock:
    block_type: LayoutBlockType
    bbox: BoundingBox
    confidence: float | None = None
    text: str | None = None


@dataclass
class TableRegion:
    bbox: BoundingBox
    confidence: float | None = None
    html: str | None = None
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class TextLine:
    line_id: int
    page_line_index: int
    text: str
    bbox: BoundingBox
    y_center: float
    y_center_n: float
    tokens: list[OcrToken] = field(default_factory=list)


@dataclass
class TextSpan:
    span_id: int
    line_id: int
    page_span_index: int
    text: str
    bbox: BoundingBox
    tokens: list[OcrToken] = field(default_factory=list)


@dataclass(frozen=True)
class DetectedTableRegion:
    bbox: BoundingBox
    horizontal_line_count: int
    vertical_line_count: int


@dataclass(frozen=True)
class DetectedTableCellRegion:
    bbox: BoundingBox
    table_index: int
    row_index: int
    column_index: int
    polygon: list[Point] = field(default_factory=list)


@dataclass
class DocumentPage:
    page_number: int
    width: int
    height: int
    tokens: list[OcrToken] = field(default_factory=list)
    ocr_hole_regions: list[OcrHoleRegion] = field(default_factory=list)
    lines: list[TextLine] = field(default_factory=list)
    spans: list[TextSpan] = field(default_factory=list)
    blocks: list[LayoutBlock] = field(default_factory=list)
    tables: list[TableRegion] = field(default_factory=list)
    detected_table_regions: list[DetectedTableRegion] = field(default_factory=list)
    detected_table_cell_regions: list[DetectedTableCellRegion] = field(
        default_factory=list
    )


@dataclass
class DocumentRepresentation:
    source_file: Path
    pages: list[DocumentPage] = field(default_factory=list)
    backend: PerceptionBackend = PerceptionBackend.EMPTY


@dataclass
class InvoiceResult:
    source_file: Path
    status: DocumentStatus
    fields: dict[str, InvoiceField] = field(default_factory=dict)
    document: DocumentRepresentation | None = None
    handwritten_date_crop_path: Path | None = None
    rejection_reason: str | None = None
    template_id: str | None = None


def init_invoice_result(file_path: Path) -> InvoiceResult:
    return InvoiceResult(
        source_file=file_path,
        status=DocumentStatus.PENDING,
    )
