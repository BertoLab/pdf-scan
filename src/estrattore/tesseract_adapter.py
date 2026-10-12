from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
import shutil
from typing import Any

from PIL import Image, ImageDraw

from estrattore.runtime_paths import bundled_assets_root
from estrattore.model import (
    BoundingBox,
    DetectedTableCellRegion,
    DetectedTableRegion,
    DocumentPage,
    DocumentRepresentation,
    OcrHoleRegion,
    OcrToken,
    PerceptionBackend,
)
from estrattore.text_layout import add_lines_to_document, add_spans_to_document
from estrattore.table_region_detection import (
    detect_table_regions_cells_and_ocr_regions,
)


MIN_FULL_PAGE_TOKENS_BEFORE_HOLE_OCR = 180
MAX_TABLE_COLUMN_OCR_REGIONS = 8
MIN_TABLE_COLUMN_CELLS = 3
FORCED_FULL_PAGE_CROP_SOURCE = "forced_full_page_crop_psm6"
DEFAULT_TESSERACT_LANG = "ita"
DEFAULT_TESSERACT_PROFILE = "fast"
TESSERACT_PROFILES = {"fast", "best"}


def analyze_pages_with_tesseract(
    images: list[Image.Image],
    source_file: Path,
    lang: str = DEFAULT_TESSERACT_LANG,
    ocr_profile: str = DEFAULT_TESSERACT_PROFILE,
    enable_crop_ocr: bool = True,
    force_crop_ocr: bool = False,
    first_page_number: int = 1,
) -> DocumentRepresentation:
    _configure_tesseract_command()
    _validate_tesseract_profile(ocr_profile)
    _validate_tesseract_languages(lang, ocr_profile=ocr_profile)
    pages = []

    for page_number, image in enumerate(images, start=first_page_number):
        (
            tokens,
            ocr_hole_regions,
            detected_table_regions,
            detected_table_cell_regions,
        ) = _tokens_and_regions_from_image(
            image,
            lang=lang,
            ocr_profile=ocr_profile,
            enable_crop_ocr=enable_crop_ocr,
            force_crop_ocr=force_crop_ocr,
        )
        page = DocumentPage(
            page_number=page_number,
            width=image.width,
            height=image.height,
            ocr_hole_regions=ocr_hole_regions,
            detected_table_regions=detected_table_regions,
            detected_table_cell_regions=detected_table_cell_regions,
        )
        page.tokens.extend(tokens)
        pages.append(page)

    document = DocumentRepresentation(
        source_file=source_file,
        pages=pages,
        backend=PerceptionBackend.TESSERACT,
    )

    add_lines_to_document(document)
    add_spans_to_document(document)
    return document


def _tokens_from_image(image: Image.Image, lang: str) -> list[OcrToken]:
    (
        tokens,
        _ocr_hole_regions,
        _detected_table_regions,
        _detected_table_cell_regions,
    ) = _tokens_and_regions_from_image(
        image,
        lang=lang,
        ocr_profile=DEFAULT_TESSERACT_PROFILE,
        enable_crop_ocr=True,
    )
    return tokens


def _tokens_and_regions_from_image(
    image: Image.Image,
    lang: str,
    ocr_profile: str,
    enable_crop_ocr: bool,
    force_crop_ocr: bool = False,
) -> tuple[
    list[OcrToken],
    list[OcrHoleRegion],
    list[DetectedTableRegion],
    list[DetectedTableCellRegion],
]:
    full_page_tokens = _tokens_from_image_region(
        image,
        lang=lang,
        ocr_profile=ocr_profile,
    )
    if not enable_crop_ocr:
        return full_page_tokens, [], [], []

    (
        detail_tokens,
        ocr_hole_regions,
        detected_table_regions,
        detected_table_cell_regions,
    ) = _tokens_from_invoice_detail_regions(
        image,
        lang=lang,
        ocr_profile=ocr_profile,
        full_page_tokens=full_page_tokens,
        force_crop_ocr=force_crop_ocr,
    )
    return (
        deduplicate_overlapping_tokens([*full_page_tokens, *detail_tokens]),
        ocr_hole_regions,
        detected_table_regions,
        detected_table_cell_regions,
    )


def _tokens_from_image_region(
    image: Image.Image,
    lang: str,
    ocr_profile: str = DEFAULT_TESSERACT_PROFILE,
    config: str = "",
) -> list[OcrToken]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as error:
        raise RuntimeError(
            "pytesseract is not installed. Install OCR dependencies with "
            "`pip install -e .[ocr]`."
        ) from error

    try:
        tessdata_config = _tessdata_config(ocr_profile)
        effective_config = " ".join(
            part for part in [tessdata_config, config.strip()] if part
        )
        data = pytesseract.image_to_data(
            image.convert("RGB"),
            lang=lang,
            config=effective_config,
            output_type=Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as error:
        raise RuntimeError(
            "Tesseract executable was not found. Put a portable Tesseract runtime "
            "in `vendor/tesseract/tesseract.exe`, install Tesseract in "
            "`C:\\Program Files\\Tesseract-OCR`, or make `tesseract.exe` available "
            "on PATH."
        ) from error
    except pytesseract.TesseractError as error:
        raise RuntimeError(f"Tesseract OCR failed: {error}") from error

    return tesseract_data_to_tokens(data)


def _tokens_from_invoice_detail_regions(
    image: Image.Image,
    lang: str,
    ocr_profile: str,
    full_page_tokens: list[OcrToken],
    force_crop_ocr: bool = False,
) -> tuple[
    list[OcrToken],
    list[OcrHoleRegion],
    list[DetectedTableRegion],
    list[DetectedTableCellRegion],
]:
    """Run a second OCR pass on regions where full-page OCR likely left holes.

    The normal full-page OCR remains the primary source, but some scanned invoices
    are heavily tabular: thick grid lines, small text inside cells, and compressed
    document headers. In those pages Tesseract's default full-page segmentation can
    skip entire visual bands even though the text is readable. The concrete case
    that motivated this fallback is a page where the full-page OCR missed the
    header cells containing "Data doc.", "Numero doc.", and the values under them;
    running Tesseract on just that band with `--psm 6` recovered those tokens.

    The crop selection must not be hardcoded to fixed invoice areas. Instead we
    divide the rendered page into a grid and look for OCR holes: cells with enough
    visible ink/lines to suggest content, but with low OCR-token coverage from the
    full-page pass. Adjacent suspicious cells are merged into crop boxes. Each crop
    is OCRed as a uniform text block (`--psm 6`), then its bounding boxes are
    translated back into page coordinates and merged with the full-page tokens.
    When the page also has line-based table regions, we derive additional OCR
    crop bands from those regions. The first useful refinement is vertical bands:
    on tabular invoices, totals and withholding values often live in a narrow
    right-side block, so a whole-table crop is too noisy while a vertical band can
    isolate the relevant evidence without going all the way to per-cell OCR.
    This gives downstream extractors more raw evidence without replacing the
    original OCR result or hardcoding a supplier-specific layout.
    """
    if len(full_page_tokens) >= MIN_FULL_PAGE_TOKENS_BEFORE_HOLE_OCR and not force_crop_ocr:
        return [], [], [], []

    detail_tokens: list[OcrToken] = []
    (
    detected_table_regions,
        detected_table_cell_regions,
        _table_ocr_regions,
    ) = detect_table_regions_cells_and_ocr_regions(image, tokens=full_page_tokens)
    ocr_hole_regions = [
        *(
            [_forced_full_page_crop_region(image, full_page_tokens)]
            if force_crop_ocr
            else []
        ),
        *_low_token_density_regions(image, full_page_tokens),
    ]
    detail_tokens.extend(
        _tokens_from_page_regions(
            image,
            regions=[region.bbox for region in ocr_hole_regions],
            lang=lang,
            ocr_profile=ocr_profile,
            source=FORCED_FULL_PAGE_CROP_SOURCE if force_crop_ocr else None,
        )
    )
    table_column_tokens, table_column_regions = _tokens_from_table_column_regions(
        image=image,
        cell_regions=detected_table_cell_regions,
        full_page_tokens=full_page_tokens,
        lang=lang,
        ocr_profile=ocr_profile,
    )
    detail_tokens.extend(table_column_tokens)
    ocr_hole_regions.extend(table_column_regions)

    return (
        detail_tokens,
        ocr_hole_regions,
        detected_table_regions,
        detected_table_cell_regions,
    )


def _tokens_from_table_column_regions(
    image: Image.Image,
    cell_regions: list[DetectedTableCellRegion],
    full_page_tokens: list[OcrToken],
    lang: str,
    ocr_profile: str,
) -> tuple[list[OcrToken], list[OcrHoleRegion]]:
    detail_tokens: list[OcrToken] = []
    ocr_regions: list[OcrHoleRegion] = []

    for cells in _table_column_cell_groups(cell_regions):
        crop, bbox = _masked_table_cells_crop(image, cells)
        source = "table_column_masked_psm12"
        source_id = f"{source}:{bbox.x1}:{bbox.y1}:{bbox.x2}:{bbox.y2}"
        crop_tokens = _tokens_from_image_region(
            crop,
            lang=lang,
            ocr_profile=ocr_profile,
            config="--psm 12",
        )
        detail_tokens.extend(
            _offset_tokens(
                crop_tokens,
                x_offset=bbox.x1,
                y_offset=bbox.y1,
                source=source,
                source_id=source_id,
            )
        )
        token_area, token_count = _token_stats_inside(
            full_page_tokens,
            x1=bbox.x1,
            y1=bbox.y1,
            x2=bbox.x2,
            y2=bbox.y2,
        )
        area = max(1, (bbox.x2 - bbox.x1) * (bbox.y2 - bbox.y1))
        ocr_regions.append(
            OcrHoleRegion(
                bbox=bbox,
                reason=source,
                ink_density=round(_dark_pixel_density(crop.convert("L")), 6),
                token_density=round(token_area / area, 6),
                token_count=token_count,
            )
        )

    return detail_tokens, ocr_regions


def _table_column_cell_groups(
    cell_regions: list[DetectedTableCellRegion],
) -> list[list[DetectedTableCellRegion]]:
    by_column: dict[tuple[int, int], list[DetectedTableCellRegion]] = defaultdict(list)
    for cell in cell_regions:
        if not cell.polygon:
            continue
        by_column[(cell.table_index, cell.column_index)].append(cell)

    groups = [
        sorted(cells, key=lambda cell: cell.row_index)
        for cells in by_column.values()
        if len(cells) >= MIN_TABLE_COLUMN_CELLS
    ]
    groups = sorted(
        groups,
        key=lambda cells: _bbox_area(_bbox_union([cell.bbox for cell in cells])),
        reverse=True,
    )
    return groups[:MAX_TABLE_COLUMN_OCR_REGIONS]


def _masked_table_cells_crop(
    image: Image.Image,
    cells: list[DetectedTableCellRegion],
) -> tuple[Image.Image, BoundingBox]:
    bbox = _pad_bbox(_bbox_union([cell.bbox for cell in cells]), image.size, padding=2)
    crop = Image.new("RGB", (bbox.x2 - bbox.x1, bbox.y2 - bbox.y1), "white")
    mask = Image.new("L", crop.size, 0)
    draw = ImageDraw.Draw(mask)

    for cell in cells:
        points = [(point.x - bbox.x1, point.y - bbox.y1) for point in cell.polygon]
        if len(points) >= 3:
            draw.polygon(points, fill=255)
            continue

        draw.rectangle(
            (
                cell.bbox.x1 - bbox.x1,
                cell.bbox.y1 - bbox.y1,
                cell.bbox.x2 - bbox.x1,
                cell.bbox.y2 - bbox.y1,
            ),
            fill=255,
        )

    crop.paste(image.crop((bbox.x1, bbox.y1, bbox.x2, bbox.y2)), (0, 0), mask)
    return crop, bbox


def _bbox_union(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x1=min(box.x1 for box in boxes),
        y1=min(box.y1 for box in boxes),
        x2=max(box.x2 for box in boxes),
        y2=max(box.y2 for box in boxes),
    )


def _pad_bbox(
    bbox: BoundingBox,
    image_size: tuple[int, int],
    padding: int,
) -> BoundingBox:
    width, height = image_size
    return BoundingBox(
        x1=max(0, bbox.x1 - padding),
        y1=max(0, bbox.y1 - padding),
        x2=min(width, bbox.x2 + padding),
        y2=min(height, bbox.y2 + padding),
    )


def _bbox_area(bbox: BoundingBox) -> int:
    return max(0, bbox.x2 - bbox.x1) * max(0, bbox.y2 - bbox.y1)


def _tokens_from_page_regions(
    image: Image.Image,
    regions: list[BoundingBox],
    lang: str,
    ocr_profile: str,
    source: str | None = None,
) -> list[OcrToken]:
    detail_tokens: list[OcrToken] = []

    for region in regions:
        crop = image.crop((region.x1, region.y1, region.x2, region.y2))
        crop_tokens = _tokens_from_image_region(
            crop,
            lang=lang,
            ocr_profile=ocr_profile,
            config="--psm 6",
        )
        detail_tokens.extend(
            _offset_tokens(
                crop_tokens,
                x_offset=region.x1,
                y_offset=region.y1,
                source=source,
                source_id=(
                    f"{source}:{region.x1}:{region.y1}:{region.x2}:{region.y2}"
                    if source
                    else None
                ),
            )
        )

    return detail_tokens


def _forced_full_page_crop_region(
    image: Image.Image,
    tokens: list[OcrToken],
) -> OcrHoleRegion:
    bbox = BoundingBox(0, 0, image.width, image.height)
    token_area, token_count = _token_stats_inside(
        tokens,
        x1=bbox.x1,
        y1=bbox.y1,
        x2=bbox.x2,
        y2=bbox.y2,
    )
    area = max(1, image.width * image.height)
    return OcrHoleRegion(
        bbox=bbox,
        reason=FORCED_FULL_PAGE_CROP_SOURCE,
        ink_density=round(_dark_pixel_density(image.convert("L")), 6),
        token_density=round(token_area / area, 6),
        token_count=token_count,
    )


def _low_token_density_regions(
    image: Image.Image,
    tokens: list[OcrToken],
    rows: int = 12,
    columns: int = 4,
    max_regions: int = 8,
) -> list[OcrHoleRegion]:
    width, height = image.size
    grayscale = image.convert("L")
    suspicious_cells: list[dict] = []

    for row in range(rows):
        for column in range(columns):
            x1 = int(width * column / columns)
            y1 = int(height * row / rows)
            x2 = int(width * (column + 1) / columns)
            y2 = int(height * (row + 1) / rows)
            cell_area = max(1, (x2 - x1) * (y2 - y1))
            token_area, token_count = _token_stats_inside(
                tokens,
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
            token_density = token_area / cell_area
            ink_density = _dark_pixel_density(grayscale.crop((x1, y1, x2, y2)))

            if ink_density < 0.006:
                continue
            if token_count > 6:
                continue
            if token_density > 0.012:
                continue

            suspicious_cells.append(
                {
                    "bbox": (x1, y1, x2, y2),
                    "ink_density": ink_density,
                    "token_density": token_density,
                    "token_count": token_count,
                }
            )

    regions = _merge_grid_cells_by_row(suspicious_cells)
    regions = sorted(regions, key=_region_area, reverse=True)
    return [
        OcrHoleRegion(
            bbox=BoundingBox(*_pad_region(region["bbox"], width=width, height=height)),
            reason="low_token_density_with_ink",
            ink_density=round(region["ink_density"], 6),
            token_density=round(region["token_density"], 6),
            token_count=region["token_count"],
        )
        for region in regions[:max_regions]
    ]


def _low_token_density_region_boxes(
    image: Image.Image,
    tokens: list[OcrToken],
) -> list[tuple[int, int, int, int]]:
    return [
        (region.bbox.x1, region.bbox.y1, region.bbox.x2, region.bbox.y2)
        for region in _low_token_density_regions(image, tokens)
    ]


def _token_stats_inside(
    tokens: list[OcrToken],
    x1: int,
    y1: int,
    x2: int,
    y2: int,
) -> tuple[int, int]:
    area = 0
    count = 0
    for token in tokens:
        center_x = (token.bbox.x1 + token.bbox.x2) / 2
        center_y = (token.bbox.y1 + token.bbox.y2) / 2
        if not (x1 <= center_x <= x2 and y1 <= center_y <= y2):
            continue
        count += 1
        area += max(0, token.bbox.x2 - token.bbox.x1) * max(
            0,
            token.bbox.y2 - token.bbox.y1,
        )
    return area, count


def _dark_pixel_density(image: Image.Image, threshold: int = 220) -> float:
    pixels = image.getdata()
    total = max(1, image.width * image.height)
    dark_count = sum(1 for pixel in pixels if pixel < threshold)
    return dark_count / total


def _merge_grid_cells_by_row(
    cells: list[dict],
) -> list[dict]:
    rows: dict[tuple[int, int], list[dict]] = {}
    for cell in cells:
        x1, y1, x2, y2 = cell["bbox"]
        rows.setdefault((y1, y2), []).append(cell)

    regions = []
    for (_y1, _y2), row_cells in rows.items():
        row_cells = sorted(row_cells, key=lambda cell: cell["bbox"][0])
        current = None
        for cell in row_cells:
            x1, y1, x2, y2 = cell["bbox"]
            if current is None:
                current = {
                    "bbox": [x1, y1, x2, y2],
                    "ink_density": cell["ink_density"],
                    "token_density": cell["token_density"],
                    "token_count": cell["token_count"],
                    "cell_count": 1,
                }
                continue

            if x1 <= current["bbox"][2] + 1:
                current["bbox"][2] = x2
                current["ink_density"] += cell["ink_density"]
                current["token_density"] += cell["token_density"]
                current["token_count"] += cell["token_count"]
                current["cell_count"] += 1
                continue

            regions.append(_finalize_region(current))
            current = {
                "bbox": [x1, y1, x2, y2],
                "ink_density": cell["ink_density"],
                "token_density": cell["token_density"],
                "token_count": cell["token_count"],
                "cell_count": 1,
            }

        if current is not None:
            regions.append(_finalize_region(current))

    return regions


def _finalize_region(region: dict) -> dict:
    cell_count = max(1, region["cell_count"])
    return {
        "bbox": tuple(region["bbox"]),
        "ink_density": region["ink_density"] / cell_count,
        "token_density": region["token_density"] / cell_count,
        "token_count": region["token_count"],
    }


def _pad_region(
    region: tuple[int, int, int, int],
    width: int,
    height: int,
    padding: int = 64,
) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = region
    return (
        max(0, x1 - padding),
        max(0, y1 - padding),
        min(width, x2 + padding),
        min(height, y2 + padding),
    )


def _region_area(region: tuple[int, int, int, int]) -> int:
    if isinstance(region, dict):
        region = region["bbox"]
    x1, y1, x2, y2 = region
    return max(0, x2 - x1) * max(0, y2 - y1)


def _offset_tokens(
    tokens: list[OcrToken],
    x_offset: int,
    y_offset: int,
    source: str | None = None,
    source_id: str | None = None,
) -> list[OcrToken]:
    return [
        OcrToken(
            text=token.text,
            bbox=BoundingBox(
                x1=token.bbox.x1 + x_offset,
                y1=token.bbox.y1 + y_offset,
                x2=token.bbox.x2 + x_offset,
                y2=token.bbox.y2 + y_offset,
            ),
            confidence=token.confidence,
            source=source or token.source,
            source_id=source_id or token.source_id,
        )
        for token in tokens
    ]


def _configure_tesseract_command() -> None:
    try:
        import pytesseract
    except ImportError as error:
        raise RuntimeError(
            "pytesseract is not installed. Install OCR dependencies with "
            "`pip install -e .[ocr]`."
        ) from error

    command = resolve_tesseract_cmd()
    if command is not None:
        pytesseract.pytesseract.tesseract_cmd = str(command)
        _configure_tessdata_prefix(command)


def resolve_tesseract_cmd() -> Path | None:
    if getattr(sys, "frozen", False):
        command = bundled_assets_root() / "vendor" / "tesseract" / "tesseract.exe"
        if not command.is_file():
            raise RuntimeError("OCR non disponibile: manca Tesseract nel pacchetto dell'app.")
        return command

    candidates = [
        _project_root() / "vendor" / "tesseract" / "tesseract.exe",
        Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
        Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
    ]

    for candidate in candidates:
        if candidate.exists():
            return candidate

    path_command = shutil.which("tesseract")
    if path_command:
        return Path(path_command)

    return None


def _configure_tessdata_prefix(command: Path) -> None:
    tessdata_dir = command.parent / "tessdata"
    if tessdata_dir.exists():
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)


def _validate_tesseract_profile(ocr_profile: str) -> None:
    if ocr_profile not in TESSERACT_PROFILES:
        allowed_profiles = ", ".join(sorted(TESSERACT_PROFILES))
        raise RuntimeError(
            f"Unsupported Tesseract OCR profile `{ocr_profile}`. "
            f"Expected one of: {allowed_profiles}."
        )


def _tessdata_dir_for_profile(ocr_profile: str) -> Path | None:
    _validate_tesseract_profile(ocr_profile)
    command = resolve_tesseract_cmd()
    if command is None:
        return None

    if ocr_profile == "best":
        return command.parent / "tessdata_best"

    return command.parent / "tessdata"


def _tessdata_config(ocr_profile: str) -> str:
    tessdata_dir = _tessdata_dir_for_profile(ocr_profile)
    if tessdata_dir is None or not tessdata_dir.exists():
        return ""

    if os.name == "nt":
        os.environ["TESSDATA_PREFIX"] = str(tessdata_dir)
        return ""
    return f'--tessdata-dir "{tessdata_dir.as_posix()}"'


def _validate_tesseract_languages(
    lang: str,
    ocr_profile: str = DEFAULT_TESSERACT_PROFILE,
) -> None:
    try:
        import pytesseract
    except ImportError as error:
        raise RuntimeError(
            "pytesseract is not installed. Install OCR dependencies with "
            "`pip install -e .[ocr]`."
        ) from error

    try:
        available_languages = pytesseract.get_languages(
            config=_tessdata_config(ocr_profile)
        )
    except pytesseract.TesseractNotFoundError as error:
        raise RuntimeError(
            "Tesseract executable was not found. Put a portable Tesseract runtime "
            "in `vendor/tesseract/tesseract.exe`, install Tesseract in "
            "`C:\\Program Files\\Tesseract-OCR`, or make `tesseract.exe` available "
            "on PATH."
        ) from error
    except pytesseract.TesseractError as error:
        raise RuntimeError(f"Could not read Tesseract language data: {error}") from error

    missing_languages = _missing_tesseract_languages(lang, available_languages)
    if missing_languages:
        missing = ", ".join(missing_languages)
        tessdata_dir = _tessdata_dir_for_profile(ocr_profile)
        raise RuntimeError(
            "Tesseract language data is incomplete. "
            f"Requested `{lang}`, but missing: {missing}. "
            f"OCR profile: {ocr_profile}. "
            f"Tessdata dir: {tessdata_dir or '<not set>'}. "
            "Add the missing `.traineddata` files under the selected tessdata dir."
        )


def _missing_tesseract_languages(
    lang: str,
    available_languages: list[str],
) -> list[str]:
    requested_languages = {
        language.strip()
        for language in lang.split("+")
        if language.strip()
    }
    available = set(available_languages)
    return sorted(
        language for language in requested_languages if language not in available
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def tesseract_data_to_tokens(data: dict[str, list[Any]]) -> list[OcrToken]:
    tokens = []
    total_items = len(data.get("text", []))

    for index in range(total_items):
        text = str(data["text"][index]).strip()
        if not text:
            continue

        confidence = _confidence_or_none(_optional_item_at(data, "conf", index))
        if confidence is not None and confidence < 0:
            continue

        try:
            left = int(float(data["left"][index]))
            top = int(float(data["top"][index]))
            width = int(float(data["width"][index]))
            height = int(float(data["height"][index]))
        except (KeyError, IndexError, TypeError, ValueError):
            continue

        if width <= 0 or height <= 0:
            continue

        tokens.append(
            OcrToken(
                text=text,
                bbox=BoundingBox(
                    x1=left,
                    y1=top,
                    x2=left + width,
                    y2=top + height,
                ),
                confidence=confidence,
            )
        )

    return deduplicate_overlapping_tokens(tokens)


def deduplicate_overlapping_tokens(tokens: list[OcrToken]) -> list[OcrToken]:
    deduplicated: list[OcrToken] = []

    for token in tokens:
        duplicate_index = _find_duplicate_token_index(deduplicated, token)
        if duplicate_index is None:
            deduplicated.append(token)
            continue

        current = deduplicated[duplicate_index]
        if _token_quality_key(token) > _token_quality_key(current):
            deduplicated[duplicate_index] = token

    return deduplicated


def _find_duplicate_token_index(
    tokens: list[OcrToken],
    candidate: OcrToken,
) -> int | None:
    for index, token in enumerate(tokens):
        same_position = _same_bbox(token.bbox, candidate.bbox)
        same_area = _bbox_iou(token.bbox, candidate.bbox) >= 0.95
        if same_position or same_area:
            return index

    return None


def _same_bbox(first: BoundingBox, second: BoundingBox, tolerance: int = 2) -> bool:
    return (
        abs(first.x1 - second.x1) <= tolerance
        and abs(first.y1 - second.y1) <= tolerance
        and abs(first.x2 - second.x2) <= tolerance
        and abs(first.y2 - second.y2) <= tolerance
    )


def _bbox_iou(first: BoundingBox, second: BoundingBox) -> float:
    intersection_x1 = max(first.x1, second.x1)
    intersection_y1 = max(first.y1, second.y1)
    intersection_x2 = min(first.x2, second.x2)
    intersection_y2 = min(first.y2, second.y2)

    intersection_width = max(0, intersection_x2 - intersection_x1)
    intersection_height = max(0, intersection_y2 - intersection_y1)
    intersection_area = intersection_width * intersection_height
    if intersection_area == 0:
        return 0

    first_area = max(0, first.x2 - first.x1) * max(0, first.y2 - first.y1)
    second_area = max(0, second.x2 - second.x1) * max(0, second.y2 - second.y1)
    union_area = first_area + second_area - intersection_area
    if union_area <= 0:
        return 0

    return intersection_area / union_area


def _token_quality_key(token: OcrToken) -> tuple[float, int]:
    confidence = token.confidence if token.confidence is not None else -1
    return confidence, len(token.text.strip())


def _confidence_or_none(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(value) / 100
    except (TypeError, ValueError):
        return None


def _optional_item_at(data: dict[str, list[Any]], key: str, index: int) -> Any:
    values = data.get(key)
    if values is None or index >= len(values):
        return None

    return values[index]
