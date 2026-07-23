from __future__ import annotations

from dataclasses import dataclass
import math

from PIL import Image

from estrattore.model import (
    BoundingBox,
    DetectedTableCellRegion,
    DetectedTableRegion,
    OcrHoleRegion,
    OcrToken,
    Point,
)


BLACK_PIXEL_THRESHOLD = 190
LINE_PIXEL_THRESHOLD = 210
MIN_HORIZONTAL_LINE_DARK_RATIO = 0.25
MIN_LINE_DARK_PIXELS = 120
MIN_HORIZONTAL_RUN_RATIO = 0.035
MIN_HORIZONTAL_RUN_PIXELS = 45
MIN_VERTICAL_RUN_RATIO = 0.025
MIN_VERTICAL_RUN_PIXELS = 28
MIN_LINE_RUN_DENSITY = 0.55
MAX_DARK_RUN_GAP = 3
MAX_DIRECT_LINE_THICKNESS = 8
MIN_THICK_LINE_ROW_COVERAGE = 0.45
MIN_THICK_LINE_ADJACENT_ROW_COVERAGE = 0.12
MIN_HORIZONTAL_LINES_FOR_TABLE = 4
MIN_VERTICAL_LINES_FOR_TABLE = 3
MAX_LINE_THICKNESS_GAP = 2
MAX_TABLE_LINE_GAP_RATIO = 0.18
MAX_TABLE_LINE_GAP_PIXELS = 90
MAX_TABLE_MERGE_GAP_RATIO = 0.38
MAX_TABLE_BRIDGED_MERGE_GAP_RATIO = 0.55
MIN_TABLE_MERGE_X_OVERLAP_RATIO = 0.65
MIN_VERTICAL_BRIDGE_LINES_FOR_MERGE = 3
VERTICAL_BRIDGE_TOLERANCE = 24
MAX_VERTICAL_BRIDGE_SEGMENT_GAP = 90
MAX_TABLE_LEVEL_CENTER_GAP = 6
TABLE_LINE_INTERSECTION_TOLERANCE = 8
MIN_HORIZONTAL_GRID_INTERSECTIONS = 2
MIN_HORIZONTAL_CELL_EDGE_COVERAGE = 0.7
MIN_VERTICAL_CELL_EDGE_COVERAGE = 0.2
MAX_IMPLIED_BORDER_DISTANCE = 36
MAX_IMPLIED_CORNER_DISTANCE = 14
TABLE_REGION_PADDING = 12
MIN_TABLE_WIDTH_RATIO = 0.15
MIN_TABLE_HEIGHT_RATIO = 0.05
MIN_VERTICAL_LINE_DARK_RATIO_IN_TABLE = 0.35
MIN_VERTICAL_LINE_DARK_PIXELS_IN_TABLE = 40
MIN_TABLE_VERTICAL_BAND_WIDTH_RATIO = 0.05
MIN_TABLE_HORIZONTAL_BAND_HEIGHT = 24
TABLE_VERTICAL_BAND_PADDING = 8
TABLE_HORIZONTAL_BAND_PADDING = 8
MAX_TABLE_OCR_REGIONS_PER_TABLE = 5
TABLE_CELL_INSET = 4
MIN_TABLE_CELL_LINE_CLEARANCE = 2
MIN_TABLE_CELL_WIDTH = 18
MIN_TABLE_CELL_HEIGHT = 12
MIN_TABLE_TITLE_CELL_HEIGHT = 8
MIN_TITLE_BAND_VERTICAL_LINES = 4
MIN_LINE_MODEL_SAMPLES = 6
LINE_MODEL_SAMPLE_COUNT = 24
LINE_MODEL_SEARCH_RADIUS = 5
MIN_LOCAL_LINE_MODEL_SLOPE = 0.002
ECONOMIC_ANCHORS = {
    "totale",
    "pagare",
    "netto",
    "iva",
    "ritenuta",
    "acconto",
    "abbuono",
    "omaggio",
    "spese",
}


@dataclass(frozen=True)
class _LineSegment:
    start: int
    end: int
    cross_start: int
    cross_end: int

    @property
    def center(self) -> int:
        return (self.start + self.end) // 2


@dataclass(frozen=True)
class _TableGeometry:
    region: DetectedTableRegion
    horizontal_lines: list[_LineSegment]
    vertical_lines: list[_LineSegment]


@dataclass(frozen=True)
class _CropCandidate:
    bbox: BoundingBox
    reason: str
    score: float
    token_count: int
    token_density: float


@dataclass(frozen=True)
class _RawTableCell:
    bbox: BoundingBox
    polygon: list[Point]


@dataclass(frozen=True)
class _LineModel:
    slope: float
    intercept: float

    def y_at(self, x: float) -> float:
        return self.slope * x + self.intercept


@dataclass(frozen=True)
class _VerticalLineModel:
    slope: float
    intercept: float

    def x_at(self, y: float) -> float:
        return self.slope * y + self.intercept


def detect_table_regions(image: Image.Image) -> list[DetectedTableRegion]:
    table_regions, _cell_regions, _ocr_regions = detect_table_regions_cells_and_ocr_regions(
        image
    )
    return table_regions


def detect_table_cell_regions(image: Image.Image) -> list[DetectedTableCellRegion]:
    _table_regions, cell_regions, _ocr_regions = detect_table_regions_cells_and_ocr_regions(
        image
    )
    return cell_regions


def detect_table_ocr_regions(image: Image.Image) -> list[OcrHoleRegion]:
    _table_regions, _cell_regions, ocr_regions = (
        detect_table_regions_cells_and_ocr_regions(image)
    )
    return ocr_regions


def detect_table_regions_and_ocr_regions(
    image: Image.Image,
    tokens: list[OcrToken] | None = None,
) -> tuple[list[DetectedTableRegion], list[OcrHoleRegion]]:
    table_regions, _cell_regions, ocr_regions = (
        detect_table_regions_cells_and_ocr_regions(image, tokens=tokens)
    )
    return table_regions, ocr_regions


def detect_table_regions_cells_and_ocr_regions(
    image: Image.Image,
    tokens: list[OcrToken] | None = None,
) -> tuple[list[DetectedTableRegion], list[DetectedTableCellRegion], list[OcrHoleRegion]]:
    geometries = _detect_table_geometries(image)
    table_regions = [geometry.region for geometry in geometries]
    cell_regions = [
        cell
        for table_index, geometry in enumerate(geometries)
        for cell in _cell_bboxes_for_table(
            image=image,
            geometry=geometry,
            table_index=table_index,
        )
    ]
    ocr_regions = [
        _ocr_region_from_crop_candidate(image, candidate)
        for geometry in geometries
        for candidate in _ocr_crop_candidates_for_table(
            image=image,
            geometry=geometry,
            tokens=tokens or [],
        )
    ]
    return table_regions, cell_regions, ocr_regions


def _ocr_region_from_crop_candidate(
    image: Image.Image,
    candidate: _CropCandidate,
) -> OcrHoleRegion:
    return OcrHoleRegion(
        bbox=candidate.bbox,
        reason=candidate.reason,
        ink_density=round(
            _dark_pixel_density(image.crop(_bbox_tuple(candidate.bbox))),
            6,
        ),
        token_density=round(candidate.token_density, 6),
        token_count=candidate.token_count,
    )


def _line_model_from_dark_pixel_centers(
    image: Image.Image,
    line: _LineSegment,
) -> _LineModel | None:
    pixels = image.load()
    x_start = line.cross_start
    x_end = line.cross_end
    if x_end <= x_start:
        return None

    points: list[tuple[float, float]] = []
    sample_count = min(LINE_MODEL_SAMPLE_COUNT, max(2, x_end - x_start + 1))
    for index in range(sample_count):
        x = round(x_start + (x_end - x_start) * index / (sample_count - 1))
        dark_y_values = [
            y
            for y in range(
                max(0, line.center - LINE_MODEL_SEARCH_RADIUS),
                min(image.height, line.center + LINE_MODEL_SEARCH_RADIUS + 1),
            )
            if pixels[x, y] < LINE_PIXEL_THRESHOLD
        ]
        if not dark_y_values:
            continue

        points.append((x, sum(dark_y_values) / len(dark_y_values)))

    if len(points) < MIN_LINE_MODEL_SAMPLES:
        return None

    slope = _linear_regression_slope(points)
    if abs(slope) < MIN_LOCAL_LINE_MODEL_SLOPE:
        return _horizontal_line_model(line)

    x_mean = sum(point[0] for point in points) / len(points)
    y_mean = sum(point[1] for point in points) / len(points)
    return _LineModel(
        slope=slope,
        intercept=y_mean - slope * x_mean,
    )


def _vertical_line_model_from_dark_pixel_centers(
    image: Image.Image,
    line: _LineSegment,
) -> _VerticalLineModel | None:
    pixels = image.load()
    y_start = line.cross_start
    y_end = line.cross_end
    if y_end <= y_start:
        return None

    points: list[tuple[float, float]] = []
    sample_count = min(LINE_MODEL_SAMPLE_COUNT, max(2, y_end - y_start + 1))
    for index in range(sample_count):
        y = round(y_start + (y_end - y_start) * index / (sample_count - 1))
        dark_x_values = [
            x
            for x in range(
                max(0, line.center - LINE_MODEL_SEARCH_RADIUS),
                min(image.width, line.center + LINE_MODEL_SEARCH_RADIUS + 1),
            )
            if pixels[x, y] < LINE_PIXEL_THRESHOLD
        ]
        if not dark_x_values:
            continue

        points.append((y, sum(dark_x_values) / len(dark_x_values)))

    if len(points) < MIN_LINE_MODEL_SAMPLES:
        return None

    slope = _linear_regression_slope(points)
    if abs(slope) < MIN_LOCAL_LINE_MODEL_SLOPE:
        return _vertical_line_model(line)

    y_mean = sum(point[0] for point in points) / len(points)
    x_mean = sum(point[1] for point in points) / len(points)
    return _VerticalLineModel(
        slope=slope,
        intercept=x_mean - slope * y_mean,
    )


def _linear_regression_slope(points: list[tuple[float, float]]) -> float:
    x_mean = sum(point[0] for point in points) / len(points)
    y_mean = sum(point[1] for point in points) / len(points)
    denominator = sum((point[0] - x_mean) ** 2 for point in points)
    if denominator == 0:
        return 0.0

    numerator = sum((point[0] - x_mean) * (point[1] - y_mean) for point in points)
    return numerator / denominator


def _detect_table_geometries(image: Image.Image) -> list[_TableGeometry]:
    grayscale = image.convert("L")
    horizontal_lines = _detect_horizontal_line_segments(grayscale)
    return _table_geometries_from_horizontal_lines(
        grayscale,
        horizontal_lines,
    )


def _detect_horizontal_line_segments(image: Image.Image) -> list[_LineSegment]:
    width, height = image.size
    pixels = image.load()
    min_run_length = max(
        int(width * MIN_HORIZONTAL_RUN_RATIO),
        MIN_HORIZONTAL_RUN_PIXELS,
    )
    candidate_rows: list[tuple[int, int, int]] = []

    for y in range(height):
        for run_start, run_end in _dark_runs_in_row(
            pixels=pixels,
            width=width,
            y=y,
            min_run_length=min_run_length,
        ):
            candidate_rows.append((y, run_start, run_end))

    return [
        _refine_thick_horizontal_line_segment(
            image=image,
            segment=segment,
            min_run_length=min_run_length,
        )
        for segment in _group_adjacent_line_candidates(candidate_rows)
    ]


def _refine_thick_horizontal_line_segment(
    image: Image.Image,
    segment: _LineSegment,
    min_run_length: int,
) -> _LineSegment:
    if segment.end - segment.start + 1 <= MAX_DIRECT_LINE_THICKNESS:
        return segment

    pixels = image.load()
    width = max(1, segment.cross_end - segment.cross_start + 1)
    row_candidates: list[tuple[int, int, int, int, float]] = []
    for y in range(segment.start, segment.end + 1):
        runs = _dark_runs_in_row(
            pixels=pixels,
            width=image.width,
            y=y,
            min_run_length=min_run_length,
        )
        overlapping_runs = [
            (run_start, run_end)
            for run_start, run_end in runs
            if _range_overlap(
                run_start,
                run_end,
                segment.cross_start,
                segment.cross_end,
            )
        ]
        if not overlapping_runs:
            continue

        covered_pixels = sum(
            _range_overlap(
                run_start,
                run_end,
                segment.cross_start,
                segment.cross_end,
            )
            for run_start, run_end in overlapping_runs
        )
        coverage = covered_pixels / width
        row_candidates.append(
            (
                y,
                min(run_start for run_start, _run_end in overlapping_runs),
                max(run_end for _run_start, run_end in overlapping_runs),
                covered_pixels,
                coverage,
            )
        )

    if not row_candidates:
        return segment

    best_index = max(
        range(len(row_candidates)),
        key=lambda index: row_candidates[index][3],
    )
    if row_candidates[best_index][4] < MIN_THICK_LINE_ROW_COVERAGE:
        return segment

    selected_rows = [row_candidates[best_index]]

    index = best_index - 1
    while (
        index >= 0
        and selected_rows[0][0] - row_candidates[index][0] <= MAX_LINE_THICKNESS_GAP + 1
        and row_candidates[index][4] >= MIN_THICK_LINE_ADJACENT_ROW_COVERAGE
    ):
        selected_rows.insert(0, row_candidates[index])
        index -= 1

    index = best_index + 1
    while (
        index < len(row_candidates)
        and row_candidates[index][0] - selected_rows[-1][0] <= MAX_LINE_THICKNESS_GAP + 1
        and row_candidates[index][4] >= MIN_THICK_LINE_ADJACENT_ROW_COVERAGE
    ):
        selected_rows.append(row_candidates[index])
        index += 1

    return _LineSegment(
        start=min(row[0] for row in selected_rows),
        end=max(row[0] for row in selected_rows),
        cross_start=min(row[1] for row in selected_rows),
        cross_end=max(row[2] for row in selected_rows),
    )


def _refine_horizontal_line_levels(
    image: Image.Image,
    lines: list[_LineSegment],
) -> list[_LineSegment]:
    min_run_length = max(
        int(image.width * MIN_HORIZONTAL_RUN_RATIO),
        MIN_HORIZONTAL_RUN_PIXELS,
    )
    return [
        _refine_thick_horizontal_line_segment(
            image=image,
            segment=line,
            min_run_length=min_run_length,
        )
        for line in lines
    ]


def _dark_runs_in_row(
    pixels,
    width: int,
    y: int,
    min_run_length: int,
) -> list[tuple[int, int]]:
    runs = []
    run_start: int | None = None
    last_dark: int | None = None
    dark_count = 0
    gap = 0

    for x in range(width):
        if pixels[x, y] < LINE_PIXEL_THRESHOLD:
            if run_start is None:
                run_start = x
            last_dark = x
            dark_count += 1
            gap = 0
            continue

        if run_start is None:
            continue

        gap += 1
        if gap <= MAX_DARK_RUN_GAP:
            continue

        if (
            last_dark is not None
            and _is_dense_line_run(run_start, last_dark, dark_count, min_run_length)
        ):
            runs.append((run_start, last_dark))
        run_start = None
        last_dark = None
        dark_count = 0
        gap = 0

    if (
        run_start is not None
        and last_dark is not None
        and _is_dense_line_run(run_start, last_dark, dark_count, min_run_length)
    ):
        runs.append((run_start, last_dark))

    return runs


def _is_dense_line_run(
    run_start: int,
    run_end: int,
    dark_count: int,
    min_run_length: int,
) -> bool:
    run_length = run_end - run_start + 1
    if run_length < min_run_length:
        return False

    return dark_count / run_length >= MIN_LINE_RUN_DENSITY


def _group_adjacent_line_candidates(
    candidates: list[tuple[int, int, int]],
    max_gap: int = MAX_LINE_THICKNESS_GAP,
) -> list[_LineSegment]:
    if not candidates:
        return []

    segments: list[_LineSegment] = []
    start, cross_start, cross_end = candidates[0]
    previous = start

    for position, candidate_cross_start, candidate_cross_end in candidates[1:]:
        if position <= previous + max_gap and _ranges_are_close(
            cross_start,
            cross_end,
            candidate_cross_start,
            candidate_cross_end,
            max_gap=max_gap,
        ):
            previous = position
            cross_start = min(cross_start, candidate_cross_start)
            cross_end = max(cross_end, candidate_cross_end)
            continue

        segments.append(
            _LineSegment(
                start=start,
                end=previous,
                cross_start=cross_start,
                cross_end=cross_end,
            )
        )
        start = position
        cross_start = candidate_cross_start
        cross_end = candidate_cross_end
        previous = position

    segments.append(
        _LineSegment(
            start=start,
            end=previous,
            cross_start=cross_start,
            cross_end=cross_end,
        )
    )
    return segments


def _table_geometries_from_horizontal_lines(
    image: Image.Image,
    horizontal_lines: list[_LineSegment],
) -> list[_TableGeometry]:
    width, height = image.size
    table_geometries = []
    horizontal_clusters = _cluster_lines_by_axis_gap(
        horizontal_lines,
        max_gap=min(int(height * MAX_TABLE_LINE_GAP_RATIO), MAX_TABLE_LINE_GAP_PIXELS),
    )
    horizontal_clusters = _merge_horizontal_clusters_by_grid_continuity(
        image,
        horizontal_clusters,
        height=height,
    )

    for horizontal_cluster in horizontal_clusters:
        matching_verticals = _detect_vertical_line_segments_inside_cluster(
            image,
            horizontal_cluster,
        )
        matching_verticals = _merge_line_levels(matching_verticals)
        horizontal_cluster = _supported_horizontal_table_levels(
            _refine_horizontal_line_levels(image, _merge_line_levels(horizontal_cluster)),
            matching_verticals,
        )
        horizontal_cluster, matching_verticals = _add_implied_table_border_lines(
            horizontal_lines=horizontal_cluster,
            vertical_lines=matching_verticals,
        )
        if (
            len(horizontal_cluster) < MIN_HORIZONTAL_LINES_FOR_TABLE
            or len(matching_verticals) < MIN_VERTICAL_LINES_FOR_TABLE
        ):
            continue

        bbox = _bbox_from_table_lines(
            horizontal_lines=horizontal_cluster,
            vertical_lines=matching_verticals,
            width=width,
            height=height,
        )
        if bbox is None:
            continue

        table_geometries.append(
            _TableGeometry(
                region=DetectedTableRegion(
                    bbox=bbox,
                    horizontal_line_count=len(horizontal_cluster),
                    vertical_line_count=len(matching_verticals),
                ),
                horizontal_lines=horizontal_cluster,
                vertical_lines=matching_verticals,
            )
        )

    return _deduplicate_table_geometries(
        _merge_vertically_continuous_table_geometries(
            image=image,
            geometries=table_geometries,
            width=width,
            height=height,
        )
    )


def _merge_horizontal_clusters_by_grid_continuity(
    image: Image.Image,
    clusters: list[list[_LineSegment]],
    height: int,
) -> list[list[_LineSegment]]:
    if len(clusters) < 2:
        return clusters

    merged: list[list[_LineSegment]] = []
    current = clusters[0]
    for next_cluster in clusters[1:]:
        if _should_merge_horizontal_clusters(
            image=image,
            upper=current,
            lower=next_cluster,
            height=height,
        ):
            current = [*current, *next_cluster]
            continue

        merged.append(current)
        current = next_cluster

    merged.append(current)
    return merged


def _should_merge_horizontal_clusters(
    image: Image.Image,
    upper: list[_LineSegment],
    lower: list[_LineSegment],
    height: int,
) -> bool:
    upper_bbox = _bbox_from_horizontal_cluster(upper, image)
    lower_bbox = _bbox_from_horizontal_cluster(lower, image)
    gap = lower_bbox.y1 - upper_bbox.y2
    if gap < 0:
        return True
    if gap > height * MAX_TABLE_BRIDGED_MERGE_GAP_RATIO:
        return False
    if _x_overlap_ratio(upper_bbox, lower_bbox) < MIN_TABLE_MERGE_X_OVERLAP_RATIO:
        return False

    combined_lines = _merge_line_levels([*upper, *lower])
    raw_vertical_lines = _detect_vertical_line_segments_inside_cluster(image, combined_lines)
    bridged_vertical_lines = _vertical_bridge_lines_between_horizontal_clusters(
        vertical_lines=raw_vertical_lines,
        upper=upper,
        lower=lower,
    )
    if (
        _horizontal_cluster_can_form_table(image, upper)
        and _horizontal_cluster_can_form_table(image, lower)
        and len(bridged_vertical_lines) < MIN_VERTICAL_BRIDGE_LINES_FOR_MERGE
    ):
        return False

    vertical_lines = _merge_line_levels(raw_vertical_lines)
    if len(vertical_lines) < MIN_VERTICAL_LINES_FOR_TABLE:
        return False

    if gap <= height * MAX_TABLE_MERGE_GAP_RATIO:
        return True

    return len(bridged_vertical_lines) >= MIN_VERTICAL_BRIDGE_LINES_FOR_MERGE


def _vertical_bridge_lines_between_horizontal_clusters(
    vertical_lines: list[_LineSegment],
    upper: list[_LineSegment],
    lower: list[_LineSegment],
) -> list[_LineSegment]:
    upper_bottom = max(line.center for line in upper)
    lower_top = min(line.center for line in lower)
    return [
        line
        for line in _connected_vertical_line_tracks(vertical_lines)
        if line.cross_start <= upper_bottom + VERTICAL_BRIDGE_TOLERANCE
        and line.cross_end >= lower_top - VERTICAL_BRIDGE_TOLERANCE
    ]


def _connected_vertical_line_tracks(
    vertical_lines: list[_LineSegment],
) -> list[_LineSegment]:
    if not vertical_lines:
        return []

    tracks: list[_LineSegment] = []
    for x_group in _line_groups_by_center(vertical_lines):
        connected_segments: list[_LineSegment] = []
        current = sorted(x_group, key=lambda line: line.cross_start)[0]
        for line in sorted(x_group, key=lambda line: line.cross_start)[1:]:
            if line.cross_start <= current.cross_end + MAX_VERTICAL_BRIDGE_SEGMENT_GAP:
                current = _LineSegment(
                    start=min(current.start, line.start),
                    end=max(current.end, line.end),
                    cross_start=min(current.cross_start, line.cross_start),
                    cross_end=max(current.cross_end, line.cross_end),
                )
                continue

            connected_segments.append(current)
            current = line

        connected_segments.append(current)
        tracks.extend(connected_segments)

    return tracks


def _line_groups_by_center(lines: list[_LineSegment]) -> list[list[_LineSegment]]:
    groups: list[list[_LineSegment]] = []
    for line in sorted(lines, key=lambda item: item.center):
        if groups and line.center - groups[-1][-1].center <= MAX_TABLE_LEVEL_CENTER_GAP:
            groups[-1].append(line)
            continue

        groups.append([line])

    return groups


def _horizontal_cluster_can_form_table(
    image: Image.Image,
    horizontal_cluster: list[_LineSegment],
) -> bool:
    vertical_lines = _merge_line_levels(
        _detect_vertical_line_segments_inside_cluster(image, horizontal_cluster)
    )
    horizontal_lines = _supported_horizontal_table_levels(
        _refine_horizontal_line_levels(image, _merge_line_levels(horizontal_cluster)),
        vertical_lines,
    )
    horizontal_lines, vertical_lines = _add_implied_table_border_lines(
        horizontal_lines=horizontal_lines,
        vertical_lines=vertical_lines,
    )
    if (
        len(horizontal_lines) < MIN_HORIZONTAL_LINES_FOR_TABLE
        or len(vertical_lines) < MIN_VERTICAL_LINES_FOR_TABLE
    ):
        return False

    return (
        _bbox_from_table_lines(
            horizontal_lines=horizontal_lines,
            vertical_lines=vertical_lines,
            width=image.width,
            height=image.height,
        )
        is not None
    )


def _bbox_from_horizontal_cluster(
    horizontal_lines: list[_LineSegment],
    image: Image.Image,
) -> BoundingBox:
    return BoundingBox(
        x1=max(0, min(line.cross_start for line in horizontal_lines)),
        y1=max(0, min(line.center for line in horizontal_lines)),
        x2=min(image.width, max(line.cross_end for line in horizontal_lines)),
        y2=min(image.height, max(line.center for line in horizontal_lines)),
    )


def _add_implied_table_border_lines(
    horizontal_lines: list[_LineSegment],
    vertical_lines: list[_LineSegment],
) -> tuple[list[_LineSegment], list[_LineSegment]]:
    if len(horizontal_lines) < 2 or len(vertical_lines) < 2:
        return horizontal_lines, vertical_lines

    horizontal_lines = sorted(horizontal_lines, key=lambda line: line.center)
    vertical_lines = sorted(vertical_lines, key=lambda line: line.center)

    implied_left_x = _robust_outer_endpoint(
        [line.cross_start for line in horizontal_lines],
        use_lower_edge=True,
    )
    implied_right_x = _robust_outer_endpoint(
        [line.cross_end for line in horizontal_lines],
        use_lower_edge=False,
    )
    implied_top_y = min(line.cross_start for line in vertical_lines)
    implied_bottom_y = max(line.cross_end for line in vertical_lines)

    vertical_lines = _add_or_snap_implied_vertical_border_line(
        vertical_lines,
        x=implied_left_x,
        top_y=implied_top_y,
        bottom_y=implied_bottom_y,
    )
    vertical_lines = _add_or_snap_implied_vertical_border_line(
        vertical_lines,
        x=implied_right_x,
        top_y=implied_top_y,
        bottom_y=implied_bottom_y,
    )
    horizontal_lines = _add_implied_horizontal_border_line(
        horizontal_lines,
        y=implied_top_y,
        left_x=implied_left_x,
        right_x=implied_right_x,
    )
    horizontal_lines = _add_implied_horizontal_border_line(
        horizontal_lines,
        y=implied_bottom_y,
        left_x=implied_left_x,
        right_x=implied_right_x,
    )

    return (
        sorted(horizontal_lines, key=lambda line: line.center),
        sorted(vertical_lines, key=lambda line: line.center),
    )


def _robust_outer_endpoint(values: list[int], use_lower_edge: bool) -> int:
    if not values:
        return 0

    edge = min(values) if use_lower_edge else max(values)
    nearby_values = sorted(
        value
        for value in values
        if abs(value - edge) <= MAX_IMPLIED_BORDER_DISTANCE
    )
    if not nearby_values:
        return edge

    midpoint = len(nearby_values) // 2
    if len(nearby_values) % 2:
        return nearby_values[midpoint]

    return round((nearby_values[midpoint - 1] + nearby_values[midpoint]) / 2)


def _merge_vertically_continuous_table_geometries(
    image: Image.Image,
    geometries: list[_TableGeometry],
    width: int,
    height: int,
) -> list[_TableGeometry]:
    if len(geometries) < 2:
        return geometries

    merged: list[_TableGeometry] = []

    for geometry in sorted(geometries, key=lambda item: item.region.bbox.y1):
        if (
            merged
            and _should_merge_table_geometries_by_vertical_continuity(
                image=image,
                upper=merged[-1],
                lower=geometry,
                height=height,
            )
        ):
            merged[-1] = _merge_table_geometry_pair(
                image=image,
                upper=merged[-1],
                lower=geometry,
                width=width,
                height=height,
            )
            continue

        merged.append(geometry)

    return merged


def _should_merge_table_geometries_by_vertical_continuity(
    image: Image.Image,
    upper: _TableGeometry,
    lower: _TableGeometry,
    height: int,
) -> bool:
    upper_bbox = upper.region.bbox
    lower_bbox = lower.region.bbox
    gap = lower_bbox.y1 - upper_bbox.y2
    if gap < 0:
        return True
    if gap > height * MAX_TABLE_MERGE_GAP_RATIO:
        return False
    if _x_overlap_ratio(upper_bbox, lower_bbox) < MIN_TABLE_MERGE_X_OVERLAP_RATIO:
        return False

    return (
        len(_vertical_bridge_lines(image=image, upper=upper, lower=lower))
        >= MIN_VERTICAL_BRIDGE_LINES_FOR_MERGE
    )


def _merge_table_geometry_pair(
    image: Image.Image,
    upper: _TableGeometry,
    lower: _TableGeometry,
    width: int,
    height: int,
) -> _TableGeometry:
    horizontal_lines = _merge_line_levels([*upper.horizontal_lines, *lower.horizontal_lines])
    vertical_lines = _merge_line_levels(
        _detect_vertical_line_segments_inside_cluster(image, horizontal_lines)
    )
    horizontal_lines = _supported_horizontal_table_levels(
        _refine_horizontal_line_levels(image, _merge_line_levels(horizontal_lines)),
        vertical_lines,
    )
    horizontal_lines, vertical_lines = _add_implied_table_border_lines(
        horizontal_lines=horizontal_lines,
        vertical_lines=vertical_lines,
    )
    bbox = _bbox_from_table_lines(
        horizontal_lines=horizontal_lines,
        vertical_lines=vertical_lines,
        width=width,
        height=height,
    )
    if bbox is None:
        return upper

    return _TableGeometry(
        region=DetectedTableRegion(
            bbox=bbox,
            horizontal_line_count=len(horizontal_lines),
            vertical_line_count=len(vertical_lines),
        ),
        horizontal_lines=horizontal_lines,
        vertical_lines=vertical_lines,
    )


def _vertical_bridge_lines(
    image: Image.Image,
    upper: _TableGeometry,
    lower: _TableGeometry,
) -> list[_LineSegment]:
    horizontal_lines = _merge_line_levels([*upper.horizontal_lines, *lower.horizontal_lines])
    vertical_lines = _detect_vertical_line_segments_inside_cluster(image, horizontal_lines)
    upper_bottom = max(line.center for line in upper.horizontal_lines)
    lower_top = min(line.center for line in lower.horizontal_lines)

    return [
        line
        for line in vertical_lines
        if line.cross_start <= upper_bottom + VERTICAL_BRIDGE_TOLERANCE
        and line.cross_end >= lower_top - VERTICAL_BRIDGE_TOLERANCE
    ]


def _x_overlap_ratio(first: BoundingBox, second: BoundingBox) -> float:
    overlap = _range_overlap(first.x1, first.x2, second.x1, second.x2)
    narrower_width = max(1, min(first.x2 - first.x1, second.x2 - second.x1))
    return overlap / narrower_width


def _add_or_snap_implied_vertical_border_line(
    vertical_lines: list[_LineSegment],
    x: int,
    top_y: int,
    bottom_y: int,
) -> list[_LineSegment]:
    nearest = min(vertical_lines, key=lambda line: abs(line.center - x))
    if abs(nearest.center - x) <= MAX_IMPLIED_BORDER_DISTANCE:
        if nearest.end - nearest.start + 1 > MAX_DIRECT_LINE_THICKNESS:
            return [
                _LineSegment(
                    start=x,
                    end=x,
                    cross_start=min(line.cross_start, top_y),
                    cross_end=max(line.cross_end, bottom_y),
                )
                if line == nearest
                else line
                for line in vertical_lines
            ]

        return [
            _LineSegment(
                start=line.start,
                end=line.end,
                cross_start=min(line.cross_start, top_y),
                cross_end=max(line.cross_end, bottom_y),
            )
            if line == nearest
            else line
            for line in vertical_lines
        ]

    return [
        *vertical_lines,
        _LineSegment(start=x, end=x, cross_start=top_y, cross_end=bottom_y),
    ]


def _add_implied_horizontal_border_line(
    horizontal_lines: list[_LineSegment],
    y: int,
    left_x: int,
    right_x: int,
) -> list[_LineSegment]:
    nearest_distance = min(abs(line.center - y) for line in horizontal_lines)
    if nearest_distance <= MAX_IMPLIED_BORDER_DISTANCE:
        return horizontal_lines

    return [
        *horizontal_lines,
        _LineSegment(start=y, end=y, cross_start=left_x, cross_end=right_x),
    ]


def _merge_line_levels(lines: list[_LineSegment]) -> list[_LineSegment]:
    if not lines:
        return []

    merged: list[_LineSegment] = []
    current_group = [sorted(lines, key=lambda line: line.center)[0]]

    for line in sorted(lines, key=lambda line: line.center)[1:]:
        if line.center - current_group[-1].center <= MAX_TABLE_LEVEL_CENTER_GAP:
            current_group.append(line)
            continue

        merged.append(_merge_line_group(current_group))
        current_group = [line]

    merged.append(_merge_line_group(current_group))
    return merged


def _merge_line_group(lines: list[_LineSegment]) -> _LineSegment:
    return _LineSegment(
        start=min(line.start for line in lines),
        end=max(line.end for line in lines),
        cross_start=min(line.cross_start for line in lines),
        cross_end=max(line.cross_end for line in lines),
    )


def _supported_horizontal_table_levels(
    horizontal_lines: list[_LineSegment],
    vertical_lines: list[_LineSegment],
) -> list[_LineSegment]:
    return [
        line
        for line in horizontal_lines
        if _horizontal_line_has_grid_support(line, vertical_lines)
    ]


def _horizontal_line_has_grid_support(
    horizontal_line: _LineSegment,
    vertical_lines: list[_LineSegment],
) -> bool:
    if (
        _horizontal_grid_intersection_count(horizontal_line, vertical_lines)
        >= MIN_HORIZONTAL_GRID_INTERSECTIONS
    ):
        return True

    return _horizontal_line_endpoints_match_vertical_borders(
        horizontal_line,
        vertical_lines,
    )


def _horizontal_line_endpoints_match_vertical_borders(
    horizontal_line: _LineSegment,
    vertical_lines: list[_LineSegment],
) -> bool:
    matching_left = [
        line
        for line in vertical_lines
        if abs(line.center - horizontal_line.cross_start) <= MAX_IMPLIED_BORDER_DISTANCE
        and _vertical_line_crosses_y(line, horizontal_line.center)
    ]
    matching_right = [
        line
        for line in vertical_lines
        if abs(line.center - horizontal_line.cross_end) <= MAX_IMPLIED_BORDER_DISTANCE
        and _vertical_line_crosses_y(line, horizontal_line.center)
    ]
    return any(left.center < right.center for left in matching_left for right in matching_right)


def _vertical_line_crosses_y(line: _LineSegment, y: int) -> bool:
    return (
        line.cross_start - TABLE_LINE_INTERSECTION_TOLERANCE
        <= y
        <= line.cross_end + TABLE_LINE_INTERSECTION_TOLERANCE
    )


def _horizontal_grid_intersection_count(
    horizontal_line: _LineSegment,
    vertical_lines: list[_LineSegment],
) -> int:
    return sum(
        1
        for vertical_line in vertical_lines
        if horizontal_line.cross_start - TABLE_LINE_INTERSECTION_TOLERANCE
        <= vertical_line.center
        <= horizontal_line.cross_end + TABLE_LINE_INTERSECTION_TOLERANCE
        and vertical_line.cross_start - TABLE_LINE_INTERSECTION_TOLERANCE
        <= horizontal_line.center
        <= vertical_line.cross_end + TABLE_LINE_INTERSECTION_TOLERANCE
    )


def _vertical_band_bboxes_for_table(
    geometry: _TableGeometry,
    page_width: int,
) -> list[BoundingBox]:
    vertical_lines = sorted(geometry.vertical_lines, key=lambda line: line.center)
    if len(vertical_lines) < 2:
        return []

    min_width = page_width * MIN_TABLE_VERTICAL_BAND_WIDTH_RATIO
    bboxes = []
    table_bbox = geometry.region.bbox

    for left_line, right_line in zip(vertical_lines, vertical_lines[1:]):
        x1 = max(table_bbox.x1, left_line.center - TABLE_VERTICAL_BAND_PADDING)
        x2 = min(table_bbox.x2, right_line.center + TABLE_VERTICAL_BAND_PADDING)
        if x2 - x1 < min_width:
            continue

        bboxes.append(
            BoundingBox(
                x1=x1,
                y1=table_bbox.y1,
                x2=x2,
                y2=table_bbox.y2,
            )
        )

    return bboxes


def _horizontal_band_bboxes_for_table(geometry: _TableGeometry) -> list[BoundingBox]:
    horizontal_lines = sorted(geometry.horizontal_lines, key=lambda line: line.center)
    if len(horizontal_lines) < 2:
        return []

    bboxes = []
    table_bbox = geometry.region.bbox

    for top_line, bottom_line in zip(horizontal_lines, horizontal_lines[1:]):
        y1 = max(table_bbox.y1, top_line.center - TABLE_HORIZONTAL_BAND_PADDING)
        y2 = min(table_bbox.y2, bottom_line.center + TABLE_HORIZONTAL_BAND_PADDING)
        if y2 - y1 < MIN_TABLE_HORIZONTAL_BAND_HEIGHT:
            continue

        bboxes.append(
            BoundingBox(
                x1=table_bbox.x1,
                y1=y1,
                x2=table_bbox.x2,
                y2=y2,
            )
        )

    return bboxes


def _cell_bboxes_for_table(
    image: Image.Image,
    geometry: _TableGeometry,
    table_index: int,
) -> list[DetectedTableCellRegion]:
    horizontal_lines = sorted(geometry.horizontal_lines, key=lambda line: line.center)
    vertical_lines = sorted(geometry.vertical_lines, key=lambda line: line.center)
    if len(horizontal_lines) < 2 or len(vertical_lines) < 2:
        return []

    grayscale = image.convert("L")
    horizontal_line_models = {
        line: model
        for line in horizontal_lines
        if (model := _line_model_from_dark_pixel_centers(grayscale, line)) is not None
    }
    vertical_line_models = {
        line: model
        for line in vertical_lines
        if (model := _vertical_line_model_from_dark_pixel_centers(grayscale, line))
        is not None
    }
    raw_cells: list[_RawTableCell] = []
    seen_cells: set[tuple[int, int, int, int]] = set()

    for top_index, top_line in enumerate(horizontal_lines[:-1]):
        for bottom_line in horizontal_lines[top_index + 1 :]:
            band_y1 = top_line.center + TABLE_CELL_INSET
            band_y2 = bottom_line.center - TABLE_CELL_INSET
            band_height = band_y2 - band_y1
            if band_height < MIN_TABLE_TITLE_CELL_HEIGHT:
                continue

            row_vertical_lines = _vertical_lines_supporting_band(
                vertical_lines,
                top_line=top_line,
                bottom_line=bottom_line,
                y1=band_y1,
                y2=band_y2,
            )
            if len(row_vertical_lines) < 2:
                continue
            if band_height < MIN_TABLE_CELL_HEIGHT and not _is_title_band_candidate(
                row_vertical_lines
            ):
                continue

            for left_line, right_line in zip(row_vertical_lines, row_vertical_lines[1:]):
                x1 = left_line.center + TABLE_CELL_INSET
                x2 = right_line.center - TABLE_CELL_INSET
                if x2 - x1 < MIN_TABLE_CELL_WIDTH:
                    continue
                if not _horizontal_edges_cover_column(
                    top_line=top_line,
                    bottom_line=bottom_line,
                    left_line=left_line,
                    right_line=right_line,
                    x1=x1,
                    x2=x2,
                ):
                    continue
                if _has_intermediate_horizontal_edge(
                    horizontal_lines=horizontal_lines,
                    top_line=top_line,
                    bottom_line=bottom_line,
                    x1=x1,
                    x2=x2,
                ):
                    continue

                raw_cell = _cell_from_local_edges(
                    top_line=top_line,
                    bottom_line=bottom_line,
                    left_line=left_line,
                    right_line=right_line,
                    x1=x1,
                    x2=x2,
                    horizontal_line_models=horizontal_line_models,
                    vertical_line_models=vertical_line_models,
                )
                bbox = raw_cell.bbox
                if bbox.y2 - bbox.y1 < MIN_TABLE_TITLE_CELL_HEIGHT:
                    continue
                if bbox.x2 - bbox.x1 < MIN_TABLE_CELL_WIDTH:
                    continue

                cell_key = (bbox.x1, bbox.y1, bbox.x2, bbox.y2)
                if cell_key in seen_cells:
                    continue

                raw_cells.append(raw_cell)
                seen_cells.add(cell_key)

    return _indexed_table_cells(raw_cells, table_index=table_index)


def _cell_from_local_edges(
    top_line: _LineSegment,
    bottom_line: _LineSegment,
    left_line: _LineSegment,
    right_line: _LineSegment,
    x1: int,
    x2: int,
    horizontal_line_models: dict[_LineSegment, _LineModel],
    vertical_line_models: dict[_LineSegment, _VerticalLineModel],
) -> _RawTableCell:
    top_model = horizontal_line_models.get(top_line, _horizontal_line_model(top_line))
    bottom_model = horizontal_line_models.get(
        bottom_line,
        _horizontal_line_model(bottom_line),
    )

    left_model = vertical_line_models.get(left_line, _vertical_line_model(left_line))
    right_model = vertical_line_models.get(right_line, _vertical_line_model(right_line))
    left_offset = _table_cell_line_clearance(left_line)
    right_offset = _table_cell_line_clearance(right_line)
    top_offset = _table_cell_line_clearance(top_line)
    bottom_offset = _table_cell_line_clearance(bottom_line)

    top_left_xy = _offset_line_intersection(
        vertical_model=left_model,
        vertical_offset=left_offset,
        horizontal_model=top_model,
        horizontal_offset=top_offset,
    )
    top_right_xy = _offset_line_intersection(
        vertical_model=right_model,
        vertical_offset=-right_offset,
        horizontal_model=top_model,
        horizontal_offset=top_offset,
    )
    bottom_right_xy = _offset_line_intersection(
        vertical_model=right_model,
        vertical_offset=-right_offset,
        horizontal_model=bottom_model,
        horizontal_offset=-bottom_offset,
    )
    bottom_left_xy = _offset_line_intersection(
        vertical_model=left_model,
        vertical_offset=left_offset,
        horizontal_model=bottom_model,
        horizontal_offset=-bottom_offset,
    )

    top_left = Point(x=math.ceil(top_left_xy[0]), y=math.ceil(top_left_xy[1]))
    top_right = Point(x=math.floor(top_right_xy[0]), y=math.ceil(top_right_xy[1]))
    bottom_right = Point(
        x=math.floor(bottom_right_xy[0]),
        y=math.floor(bottom_right_xy[1]),
    )
    bottom_left = Point(x=math.ceil(bottom_left_xy[0]), y=math.floor(bottom_left_xy[1]))
    polygon = [top_left, top_right, bottom_right, bottom_left]
    bbox = BoundingBox(
        x1=min(point.x for point in polygon),
        y1=min(point.y for point in polygon),
        x2=max(point.x for point in polygon),
        y2=max(point.y for point in polygon),
    )
    return _RawTableCell(bbox=bbox, polygon=polygon)


def _table_cell_line_clearance(line: _LineSegment) -> int:
    stroke_width = line.end - line.start + 1
    return min(
        TABLE_CELL_INSET,
        max(MIN_TABLE_CELL_LINE_CLEARANCE, math.ceil(stroke_width / 2) + 1),
    )


def _offset_line_intersection(
    vertical_model: _VerticalLineModel,
    vertical_offset: float,
    horizontal_model: _LineModel,
    horizontal_offset: float,
) -> tuple[float, float]:
    denominator = 1 - horizontal_model.slope * vertical_model.slope
    if abs(denominator) < 1e-6:
        y = horizontal_model.intercept + horizontal_offset
        x = vertical_model.x_at(y) + vertical_offset
        return x, y

    y = (
        horizontal_model.slope * (vertical_model.intercept + vertical_offset)
        + horizontal_model.intercept
        + horizontal_offset
    ) / denominator
    x = vertical_model.x_at(y) + vertical_offset
    return x, y


def _horizontal_line_model(line: _LineSegment) -> _LineModel:
    return _LineModel(slope=0.0, intercept=float(line.center))


def _vertical_line_model(line: _LineSegment) -> _VerticalLineModel:
    return _VerticalLineModel(slope=0.0, intercept=float(line.center))


def _vertical_x_at(
    line: _LineSegment,
    models: dict[_LineSegment, _VerticalLineModel],
    y: float,
) -> float:
    if (
        y < line.cross_start - TABLE_LINE_INTERSECTION_TOLERANCE
        or y > line.cross_end + TABLE_LINE_INTERSECTION_TOLERANCE
    ):
        return float(line.center)

    return models.get(line, _vertical_line_model(line)).x_at(y)


def _is_title_band_candidate(vertical_lines: list[_LineSegment]) -> bool:
    return len(vertical_lines) >= MIN_TITLE_BAND_VERTICAL_LINES


def _horizontal_edges_cover_column(
    top_line: _LineSegment,
    bottom_line: _LineSegment,
    left_line: _LineSegment,
    right_line: _LineSegment,
    x1: int,
    x2: int,
) -> bool:
    return _horizontal_line_covers_or_is_implied_by_corners(
        line=top_line,
        left_line=left_line,
        right_line=right_line,
        x1=x1,
        x2=x2,
    ) and _horizontal_line_covers_or_is_implied_by_corners(
        line=bottom_line,
        left_line=left_line,
        right_line=right_line,
        x1=x1,
        x2=x2,
    )


def _horizontal_line_covers_or_is_implied_by_corners(
    line: _LineSegment,
    left_line: _LineSegment,
    right_line: _LineSegment,
    x1: int,
    x2: int,
) -> bool:
    if _horizontal_line_covers_range(line, x1=x1, x2=x2):
        return True

    return _line_y_matches_vertical_corner(
        y=line.center,
        vertical_line=left_line,
    ) and _line_y_matches_vertical_corner(
        y=line.center,
        vertical_line=right_line,
    )


def _line_y_matches_vertical_corner(y: int, vertical_line: _LineSegment) -> bool:
    return (
        abs(y - vertical_line.cross_start) <= MAX_IMPLIED_CORNER_DISTANCE
        or abs(y - vertical_line.cross_end) <= MAX_IMPLIED_CORNER_DISTANCE
    )


def _horizontal_line_covers_range(
    line: _LineSegment,
    x1: int,
    x2: int,
) -> bool:
    cell_width = max(1, x2 - x1)
    overlap = _range_overlap(line.cross_start, line.cross_end, x1, x2)
    return overlap / cell_width >= MIN_HORIZONTAL_CELL_EDGE_COVERAGE


def _vertical_lines_supporting_band(
    vertical_lines: list[_LineSegment],
    top_line: _LineSegment,
    bottom_line: _LineSegment,
    y1: int,
    y2: int,
) -> list[_LineSegment]:
    return [
        line
        for line in vertical_lines
        if _vertical_line_supports_range(line, y1=y1, y2=y2)
        or _vertical_line_is_implied_by_horizontal_corners(
            line=line,
            top_line=top_line,
            bottom_line=bottom_line,
        )
    ]


def _vertical_line_is_implied_by_horizontal_corners(
    line: _LineSegment,
    top_line: _LineSegment,
    bottom_line: _LineSegment,
) -> bool:
    return _line_x_matches_horizontal_corner(
        x=line.center,
        horizontal_line=top_line,
    ) and _line_x_matches_horizontal_corner(
        x=line.center,
        horizontal_line=bottom_line,
    )


def _line_x_matches_horizontal_corner(x: int, horizontal_line: _LineSegment) -> bool:
    return (
        abs(x - horizontal_line.cross_start) <= MAX_IMPLIED_CORNER_DISTANCE
        or abs(x - horizontal_line.cross_end) <= MAX_IMPLIED_CORNER_DISTANCE
    )


def _vertical_line_supports_range(
    line: _LineSegment,
    y1: int,
    y2: int,
) -> bool:
    cell_height = max(1, y2 - y1)
    overlap = _range_overlap(line.cross_start, line.cross_end, y1, y2)
    return overlap >= max(8, int(cell_height * MIN_VERTICAL_CELL_EDGE_COVERAGE))


def _has_intermediate_horizontal_edge(
    horizontal_lines: list[_LineSegment],
    top_line: _LineSegment,
    bottom_line: _LineSegment,
    x1: int,
    x2: int,
) -> bool:
    return any(
        top_line.center < line.center < bottom_line.center
        and _horizontal_line_covers_range(line, x1=x1, x2=x2)
        for line in horizontal_lines
    )


def _indexed_table_cells(
    raw_cells: list[_RawTableCell],
    table_index: int,
) -> list[DetectedTableCellRegion]:
    if not raw_cells:
        return []

    row_groups: list[list[_RawTableCell]] = []
    for cell in sorted(raw_cells, key=lambda item: (item.bbox.y1, item.bbox.x1)):
        if (
            row_groups
            and abs(row_groups[-1][0].bbox.y1 - cell.bbox.y1)
            <= MAX_TABLE_LEVEL_CENTER_GAP
        ):
            row_groups[-1].append(cell)
            continue

        row_groups.append([cell])

    cells: list[DetectedTableCellRegion] = []
    for row_index, row_cells in enumerate(row_groups):
        for column_index, cell in enumerate(
            sorted(row_cells, key=lambda item: item.bbox.x1)
        ):
            cells.append(
                DetectedTableCellRegion(
                    bbox=cell.bbox,
                    table_index=table_index,
                    row_index=row_index,
                    column_index=column_index,
                    polygon=cell.polygon,
                )
            )

    return cells


def _ocr_crop_candidates_for_table(
    image: Image.Image,
    geometry: _TableGeometry,
    tokens: list[OcrToken],
) -> list[_CropCandidate]:
    candidates = [
        _crop_candidate(
            image=image,
            bbox=bbox,
            reason="table_horizontal_band",
            tokens=tokens,
            orientation_weight=0.72,
        )
        for bbox in _horizontal_band_bboxes_for_table(geometry)
    ]
    candidates.extend(
        _crop_candidate(
            image=image,
            bbox=bbox,
            reason="table_vertical_band",
            tokens=tokens,
            orientation_weight=0.62,
        )
        for bbox in _vertical_band_bboxes_for_table(geometry, image.width)
    )
    return _select_table_crop_candidates(candidates)


def _crop_candidate(
    image: Image.Image,
    bbox: BoundingBox,
    reason: str,
    tokens: list[OcrToken],
    orientation_weight: float,
) -> _CropCandidate:
    crop_tokens = _tokens_inside_bbox(tokens, bbox)
    anchor_count = _economic_anchor_count(crop_tokens)
    token_density = _token_density(crop_tokens, bbox)
    ink_density = _dark_pixel_density(image.crop(_bbox_tuple(bbox)))
    is_economic_vertical = reason == "table_vertical_band" and anchor_count > 0
    final_reason = f"{reason}_economic" if is_economic_vertical else reason
    score = (
        orientation_weight
        + min(0.25, ink_density * 1.6)
        + min(0.18, token_density * 12)
        + min(0.45, anchor_count * 0.15)
    )

    return _CropCandidate(
        bbox=bbox,
        reason=final_reason,
        score=score,
        token_count=len(crop_tokens),
        token_density=token_density,
    )


def _select_table_crop_candidates(
    candidates: list[_CropCandidate],
) -> list[_CropCandidate]:
    sorted_candidates = sorted(
        candidates,
        key=lambda candidate: (
            candidate.reason.endswith("_economic"),
            candidate.reason.startswith("table_horizontal_band"),
            candidate.score,
        ),
        reverse=True,
    )
    selected: list[_CropCandidate] = []

    for candidate in sorted_candidates:
        if any(_bbox_iou(candidate.bbox, existing.bbox) > 0.82 for existing in selected):
            continue

        selected.append(candidate)
        if len(selected) >= MAX_TABLE_OCR_REGIONS_PER_TABLE:
            break

    return selected


def _tokens_inside_bbox(tokens: list[OcrToken], bbox: BoundingBox) -> list[OcrToken]:
    return [
        token
        for token in tokens
        if _bbox_center_inside(token.bbox, bbox)
    ]


def _bbox_center_inside(inner: BoundingBox, outer: BoundingBox) -> bool:
    center_x = (inner.x1 + inner.x2) / 2
    center_y = (inner.y1 + inner.y2) / 2
    return outer.x1 <= center_x <= outer.x2 and outer.y1 <= center_y <= outer.y2


def _economic_anchor_count(tokens: list[OcrToken]) -> int:
    return sum(
        1
        for token in tokens
        if _normalize_anchor_token(token.text) in ECONOMIC_ANCHORS
    )


def _normalize_anchor_token(text: str) -> str:
    return "".join(character.lower() for character in text if character.isalnum())


def _token_density(tokens: list[OcrToken], bbox: BoundingBox) -> float:
    area = max(1, (bbox.x2 - bbox.x1) * (bbox.y2 - bbox.y1))
    token_area = sum(
        max(0, token.bbox.x2 - token.bbox.x1)
        * max(0, token.bbox.y2 - token.bbox.y1)
        for token in tokens
    )
    return token_area / area


def _dark_pixel_density(image: Image.Image) -> float:
    grayscale = image.convert("L")
    total = max(1, grayscale.width * grayscale.height)
    dark_pixels = sum(
        1 for pixel in grayscale.getdata() if pixel < BLACK_PIXEL_THRESHOLD
    )
    return dark_pixels / total


def _bbox_tuple(bbox: BoundingBox) -> tuple[int, int, int, int]:
    return bbox.x1, bbox.y1, bbox.x2, bbox.y2


def _deduplicate_table_geometries(
    geometries: list[_TableGeometry],
) -> list[_TableGeometry]:
    deduplicated: list[_TableGeometry] = []

    for geometry in geometries:
        if any(
            _bbox_iou(geometry.region.bbox, existing.region.bbox) > 0.9
            for existing in deduplicated
        ):
            continue

        deduplicated.append(geometry)

    return deduplicated


def _detect_vertical_line_segments_inside_cluster(
    image: Image.Image,
    horizontal_lines: list[_LineSegment],
) -> list[_LineSegment]:
    pixels = image.load()
    y1 = min(line.center for line in horizontal_lines)
    y2 = max(line.center for line in horizontal_lines)
    x1 = min(line.cross_start for line in horizontal_lines)
    x2 = max(line.cross_end for line in horizontal_lines)
    band_height = max(1, y2 - y1)
    min_run_length = max(
        int(band_height * MIN_VERTICAL_RUN_RATIO),
        MIN_VERTICAL_RUN_PIXELS,
    )
    candidate_columns: list[tuple[int, int, int]] = []

    for x in range(x1, x2 + 1):
        for run_start, run_end in _dark_runs_in_column(
            pixels=pixels,
            x=x,
            y1=y1,
            y2=y2,
            min_run_length=min_run_length,
        ):
            candidate_columns.append((x, run_start, run_end))

    return _group_adjacent_line_candidates(candidate_columns, max_gap=4)


def _dark_runs_in_column(
    pixels,
    x: int,
    y1: int,
    y2: int,
    min_run_length: int,
) -> list[tuple[int, int]]:
    runs = []
    run_start: int | None = None
    last_dark: int | None = None
    dark_count = 0
    gap = 0

    for y in range(y1, y2 + 1):
        if pixels[x, y] < LINE_PIXEL_THRESHOLD:
            if run_start is None:
                run_start = y
            last_dark = y
            dark_count += 1
            gap = 0
            continue

        if run_start is None:
            continue

        gap += 1
        if gap <= MAX_DARK_RUN_GAP:
            continue

        if (
            last_dark is not None
            and _is_dense_line_run(run_start, last_dark, dark_count, min_run_length)
        ):
            runs.append((run_start, last_dark))
        run_start = None
        last_dark = None
        dark_count = 0
        gap = 0

    if (
        run_start is not None
        and last_dark is not None
        and _is_dense_line_run(run_start, last_dark, dark_count, min_run_length)
    ):
        runs.append((run_start, last_dark))

    return runs


def _cluster_lines_by_axis_gap(
    lines: list[_LineSegment],
    max_gap: int,
) -> list[list[_LineSegment]]:
    if not lines:
        return []

    sorted_lines = sorted(lines, key=lambda line: line.center)
    clusters: list[list[_LineSegment]] = [[sorted_lines[0]]]

    for line in sorted_lines[1:]:
        current_cluster = clusters[-1]
        if line.center - current_cluster[-1].center <= max_gap:
            current_cluster.append(line)
            continue

        clusters.append([line])

    return clusters


def _bbox_from_table_lines(
    horizontal_lines: list[_LineSegment],
    vertical_lines: list[_LineSegment],
    width: int,
    height: int,
    padding: int = TABLE_REGION_PADDING,
) -> BoundingBox | None:
    x1 = min(line.cross_start for line in horizontal_lines)
    x2 = max(line.cross_end for line in horizontal_lines)
    y1 = min(line.center for line in horizontal_lines)
    y2 = max(line.center for line in horizontal_lines)

    if x2 - x1 < width * MIN_TABLE_WIDTH_RATIO:
        return None
    if y2 - y1 < height * MIN_TABLE_HEIGHT_RATIO:
        return None

    return BoundingBox(
        x1=max(0, x1 - padding),
        y1=max(0, y1 - padding),
        x2=min(width, x2 + padding),
        y2=min(height, y2 + padding),
    )


def _range_overlap(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> int:
    return max(0, min(first_end, second_end) - max(first_start, second_start))


def _ranges_are_close(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
    max_gap: int,
) -> bool:
    if _range_overlap(first_start, first_end, second_start, second_end) > 0:
        return True

    return (
        min(abs(first_end - second_start), abs(second_end - first_start))
        <= max_gap
    )


def _deduplicate_table_regions(
    regions: list[DetectedTableRegion],
) -> list[DetectedTableRegion]:
    deduplicated: list[DetectedTableRegion] = []

    for region in regions:
        if any(
            _bbox_iou(region.bbox, existing.bbox) > 0.9
            for existing in deduplicated
        ):
            continue

        deduplicated.append(region)

    return deduplicated


def _bbox_iou(first: BoundingBox, second: BoundingBox) -> float:
    intersection_x1 = max(first.x1, second.x1)
    intersection_y1 = max(first.y1, second.y1)
    intersection_x2 = min(first.x2, second.x2)
    intersection_y2 = min(first.y2, second.y2)

    intersection_area = max(0, intersection_x2 - intersection_x1) * max(
        0,
        intersection_y2 - intersection_y1,
    )
    if intersection_area == 0:
        return 0

    first_area = max(0, first.x2 - first.x1) * max(0, first.y2 - first.y1)
    second_area = max(0, second.x2 - second.x1) * max(0, second.y2 - second.y1)
    union_area = first_area + second_area - intersection_area
    if union_area <= 0:
        return 0

    return intersection_area / union_area

