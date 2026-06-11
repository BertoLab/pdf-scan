from __future__ import annotations

import json
import csv
from dataclasses import dataclass
from dataclasses import asdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from estrattore.debug_flags import (
    DRAW_BLOCK_BOXES,
    DRAW_LEGACY_TARGET_BOXES,
    DRAW_RECOGNIZED_TARGETS,
    DRAW_SPAN_BOXES,
    DRAW_TABLE_BOXES,
    DRAW_TOKEN_BOXES,
)
from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation


TOKEN_COLOR = "#1f77b4"
SPAN_COLOR = "#ff7f0e"
HIGHLIGHT_COLOR = "#ffd400"
LOW_CONFIDENCE_HIGHLIGHT_COLOR = "#ff9f1a"
BLOCK_COLOR = "#2ca02c"
TABLE_COLOR = "#d62728"
LOW_CONFIDENCE_THRESHOLD = 0.9


@dataclass(frozen=True)
class DebugHighlight:
    bbox: BoundingBox
    label: str
    confidence_sensitive: bool = True


def save_document_debug_output(
    document: DocumentRepresentation,
    images: list[Image.Image],
    output_dir: Path,
    highlight_boxes_by_page: dict[int, list[BoundingBox]] | None = None,
    highlights_by_page: dict[int, list[DebugHighlight]] | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    saved_paths = [
        save_document_debug_json(document, output_dir / "document.json"),
        save_document_lines_csv(document, output_dir / "lines.csv"),
        save_document_spans_csv(document, output_dir / "spans.csv"),
    ]

    for page, image in zip(document.pages, images):
        highlight_boxes = (highlight_boxes_by_page or {}).get(page.page_number, [])
        highlights = (highlights_by_page or {}).get(page.page_number, [])
        saved_paths.append(
            save_page_debug_image(
                page=page,
                image=image,
                output_path=output_dir / f"page-{page.page_number:03d}-boxes.png",
                highlight_boxes=highlight_boxes,
                highlights=highlights,
            )
        )

    return saved_paths


def save_document_debug_json(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(_to_jsonable(document), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


def save_document_lines_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "line_id",
                "page_line_index",
                "text",
                "x1",
                "y1",
                "x2",
                "y2",
                "y_center",
                "y_center_n",
                "num_tokens",
            ],
        )
        writer.writeheader()

        for page in document.pages:
            for line in page.lines:
                writer.writerow(
                    {
                        "source_file": _csv_path(document.source_file),
                        "page": page.page_number,
                        "line_id": line.line_id,
                        "page_line_index": line.page_line_index,
                        "text": line.text,
                        "x1": line.bbox.x1,
                        "y1": line.bbox.y1,
                        "x2": line.bbox.x2,
                        "y2": line.bbox.y2,
                        "y_center": line.y_center,
                        "y_center_n": line.y_center_n,
                        "num_tokens": len(line.tokens),
                    }
                )

    return output_path


def save_document_spans_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "span_id",
                "line_id",
                "page_span_index",
                "text",
                "x1",
                "y1",
                "x2",
                "y2",
                "num_tokens",
            ],
        )
        writer.writeheader()

        for page in document.pages:
            for span in page.spans:
                writer.writerow(
                    {
                        "source_file": _csv_path(document.source_file),
                        "page": page.page_number,
                        "span_id": span.span_id,
                        "line_id": span.line_id,
                        "page_span_index": span.page_span_index,
                        "text": span.text,
                        "x1": span.bbox.x1,
                        "y1": span.bbox.y1,
                        "x2": span.bbox.x2,
                        "y2": span.bbox.y2,
                        "num_tokens": len(span.tokens),
                    }
                )

    return output_path


def save_page_debug_image(
    page: DocumentPage,
    image: Image.Image,
    output_path: Path,
    highlight_boxes: list[BoundingBox] | None = None,
    highlights: list[DebugHighlight] | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    annotated = image.convert("RGB").copy()
    draw = ImageDraw.Draw(annotated)

    if DRAW_BLOCK_BOXES:
        for block in page.blocks:
            label = f"block:{block.block_type.value}"
            _draw_labeled_box(draw, block.bbox, label, BLOCK_COLOR, width=3)

    if DRAW_SPAN_BOXES:
        for span in page.spans:
            _draw_box(draw, span.bbox, SPAN_COLOR, width=2)

    if DRAW_TABLE_BOXES:
        for table in page.tables:
            _draw_labeled_box(draw, table.bbox, "table", TABLE_COLOR, width=4)

    if DRAW_TOKEN_BOXES:
        for token in page.tokens:
            label = token.text
            _draw_labeled_box(draw, token.bbox, label, TOKEN_COLOR, width=1)

    if DRAW_LEGACY_TARGET_BOXES:
        for bbox in highlight_boxes or []:
            _draw_box(draw, _expand_bbox(bbox, padding=2), HIGHLIGHT_COLOR, width=2)

    if DRAW_RECOGNIZED_TARGETS:
        for highlight in highlights or []:
            highlight_bbox = _expand_bbox(highlight.bbox, padding=2)
            color = (
                LOW_CONFIDENCE_HIGHLIGHT_COLOR
                if highlight.confidence_sensitive
                and _contains_low_confidence_token(page, highlight_bbox)
                else HIGHLIGHT_COLOR
            )
            _draw_labeled_box(
                draw,
                highlight_bbox,
                highlight.label,
                color,
                width=3,
                text_fill="black",
            )

    annotated.save(output_path)
    return output_path


def _draw_labeled_box(
    draw: ImageDraw.ImageDraw,
    bbox: BoundingBox,
    label: str,
    color: str,
    width: int,
    text_fill: str = "white",
) -> None:
    if bbox.x2 <= bbox.x1 or bbox.y2 <= bbox.y1:
        return

    draw.rectangle((bbox.x1, bbox.y1, bbox.x2, bbox.y2), outline=color, width=width)

    if not label:
        return

    text = label[:40]
    text_bbox = draw.textbbox((bbox.x1, bbox.y1), text)
    label_height = text_bbox[3] - text_bbox[1] + 4
    label_width = text_bbox[2] - text_bbox[0] + 6
    label_y1 = max(0, bbox.y1 - label_height)

    draw.rectangle(
        (bbox.x1, label_y1, bbox.x1 + label_width, label_y1 + label_height),
        fill=color,
    )
    draw.text((bbox.x1 + 3, label_y1 + 2), text, fill=text_fill)


def _draw_box(
    draw: ImageDraw.ImageDraw,
    bbox: BoundingBox,
    color: str,
    width: int,
) -> None:
    if bbox.x2 <= bbox.x1 or bbox.y2 <= bbox.y1:
        return

    draw.rectangle((bbox.x1, bbox.y1, bbox.x2, bbox.y2), outline=color, width=width)


def _expand_bbox(bbox: BoundingBox, padding: int) -> BoundingBox:
    return BoundingBox(
        x1=max(0, bbox.x1 - padding),
        y1=max(0, bbox.y1 - padding),
        x2=bbox.x2 + padding,
        y2=bbox.y2 + padding,
    )


def _contains_low_confidence_token(
    page: DocumentPage,
    bbox: BoundingBox,
) -> bool:
    return any(
        token.confidence is not None
        and token.confidence < LOW_CONFIDENCE_THRESHOLD
        and _bbox_contains_token_center(bbox, token.bbox)
        for token in page.tokens
    )


def _bbox_contains_token_center(bbox: BoundingBox, token_bbox: BoundingBox) -> bool:
    token_center_x = (token_bbox.x1 + token_bbox.x2) / 2
    token_center_y = (token_bbox.y1 + token_bbox.y2) / 2
    return (
        bbox.x1 <= token_center_x <= bbox.x2
        and bbox.y1 <= token_center_y <= bbox.y2
    )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)

    if isinstance(value, StrEnum):
        return value.value

    if hasattr(value, "__dataclass_fields__"):
        return _to_jsonable(asdict(value))

    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]

    return value


def _csv_path(path: Path) -> str:
    return path.as_posix()
