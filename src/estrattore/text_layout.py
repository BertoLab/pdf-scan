from __future__ import annotations

from statistics import median as statistics_median

from estrattore.model import (
    BoundingBox,
    DocumentPage,
    DocumentRepresentation,
    OcrToken,
    TextLine,
    TextSpan,
)


def add_lines_to_document(
    document: DocumentRepresentation,
    y_tol_factor: float = 0.5,
) -> DocumentRepresentation:
    next_line_id = 1

    for page in document.pages:
        page.lines = group_page_tokens_into_lines(
            page=page,
            start_line_id=next_line_id,
            y_tol_factor=y_tol_factor,
        )
        next_line_id += len(page.lines)

    return document


def add_spans_to_document(
    document: DocumentRepresentation,
    gap_alpha: float = 3,
    y_tol_factor: float = 0.5,
) -> DocumentRepresentation:
    if any(page.tokens and not page.lines for page in document.pages):
        add_lines_to_document(document, y_tol_factor=y_tol_factor)

    next_span_id = 1
    for page in document.pages:
        page.spans = []
        page_span_index = 1

        for line in page.lines:
            line_spans = split_line_into_spans(
                line=line,
                start_span_id=next_span_id,
                start_page_span_index=page_span_index,
                gap_alpha=gap_alpha,
            )
            page.spans.extend(line_spans)
            next_span_id += len(line_spans)
            page_span_index += len(line_spans)

    return document


def group_page_tokens_into_lines(
    page: DocumentPage,
    start_line_id: int = 1,
    y_tol_factor: float = 0.5,
) -> list[TextLine]:
    if not page.tokens:
        return []

    token_heights = [token.bbox.y2 - token.bbox.y1 for token in page.tokens]
    typical_height = median([height for height in token_heights if height > 0])
    y_tolerance = typical_height * y_tol_factor

    tokens_sorted = sorted(
        page.tokens,
        key=lambda token: (
            _token_y_center(token),
            token.bbox.x1,
        ),
    )

    line_token_groups: list[list[OcrToken]] = []
    line_y_centers: list[float] = []

    for token in tokens_sorted:
        token_y_center = _token_y_center(token)
        matching_index = _find_matching_line_index(
            line_y_centers,
            token_y_center,
            y_tolerance,
        )

        if matching_index is None:
            line_token_groups.append([token])
            line_y_centers.append(token_y_center)
            continue

        line_token_groups[matching_index].append(token)
        line_y_centers[matching_index] = _average_y_center(line_token_groups[matching_index])

    lines = []
    for page_line_index, tokens in enumerate(line_token_groups, start=1):
        sorted_tokens = sorted(tokens, key=lambda token: token.bbox.x1)
        bbox = _bbox_around_tokens(sorted_tokens)
        y_center = _average_y_center(sorted_tokens)

        lines.append(
            TextLine(
                line_id=start_line_id + page_line_index - 1,
                page_line_index=page_line_index,
                text=" ".join(token.text for token in sorted_tokens),
                bbox=bbox,
                y_center=y_center,
                y_center_n=y_center / page.height,
                tokens=sorted_tokens,
            )
        )

    return lines


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median() received an empty list")

    sorted_values = sorted(values)
    midpoint = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[midpoint]

    return (sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2


def split_line_into_spans(
    line: TextLine,
    start_span_id: int = 1,
    start_page_span_index: int = 1,
    gap_alpha: float = 3,
) -> list[TextSpan]:
    tokens = sorted(line.tokens, key=lambda token: token.bbox.x1)
    if not tokens:
        return []

    if len(tokens) == 1:
        return [
            _build_span(
                tokens=tokens,
                span_id=start_span_id,
                line_id=line.line_id,
                page_span_index=start_page_span_index,
            )
        ]

    gaps = _positive_horizontal_gaps(tokens)
    if not gaps:
        return [
            _build_span(
                tokens=tokens,
                span_id=start_span_id,
                line_id=line.line_id,
                page_span_index=start_page_span_index,
            )
        ]

    space_unit = _line_space_unit(tokens, gaps)
    current_group = [tokens[0]]
    groups: list[list[OcrToken]] = []

    for index, gap in enumerate(_horizontal_gaps(tokens)):
        if gap <= gap_alpha * space_unit:
            current_group.append(tokens[index + 1])
            continue

        groups.append(current_group)
        current_group = [tokens[index + 1]]

    groups.append(current_group)

    return [
        _build_span(
            tokens=group,
            span_id=start_span_id + index,
            line_id=line.line_id,
            page_span_index=start_page_span_index + index,
        )
        for index, group in enumerate(groups)
    ]


def _line_space_unit(tokens: list[OcrToken], positive_gaps: list[float]) -> float:
    old_datev_space_unit = min(statistics_median(positive_gaps), 5)
    typical_height = median(
        [
            token.bbox.y2 - token.bbox.y1
            for token in tokens
            if token.bbox.y2 > token.bbox.y1
        ]
    )

    return max(old_datev_space_unit, typical_height * 0.25)


def _horizontal_gaps(tokens: list[OcrToken]) -> list[float]:
    return [
        next_token.bbox.x1 - current_token.bbox.x2
        for current_token, next_token in zip(tokens, tokens[1:])
    ]


def _positive_horizontal_gaps(tokens: list[OcrToken]) -> list[float]:
    return [gap for gap in _horizontal_gaps(tokens) if gap > 0]


def _build_span(
    tokens: list[OcrToken],
    span_id: int,
    line_id: int,
    page_span_index: int,
) -> TextSpan:
    return TextSpan(
        span_id=span_id,
        line_id=line_id,
        page_span_index=page_span_index,
        text=" ".join(token.text for token in tokens),
        bbox=_bbox_around_tokens(tokens),
        tokens=tokens,
    )


def _find_matching_line_index(
    line_y_centers: list[float],
    token_y_center: float,
    y_tolerance: float,
) -> int | None:
    for index in reversed(range(len(line_y_centers))):
        line_y_center = line_y_centers[index]
        if token_y_center < line_y_center - y_tolerance:
            break

        if abs(line_y_center - token_y_center) <= y_tolerance:
            return index

    return None


def _token_y_center(token: OcrToken) -> float:
    return (token.bbox.y1 + token.bbox.y2) / 2


def _average_y_center(tokens: list[OcrToken]) -> float:
    return sum(_token_y_center(token) for token in tokens) / len(tokens)


def _bbox_around_tokens(tokens: list[OcrToken]) -> BoundingBox:
    return BoundingBox(
        x1=min(token.bbox.x1 for token in tokens),
        y1=min(token.bbox.y1 for token in tokens),
        x2=max(token.bbox.x2 for token in tokens),
        y2=max(token.bbox.y2 for token in tokens),
    )
