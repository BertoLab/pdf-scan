from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from pathlib import Path
import re

from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken, TextSpan


MIN_FUZZY_ANCHOR_SIMILARITY = 0.88
MIN_SELECTED_SCORE = 80.0
CENT_TOLERANCE = Decimal("0.01")
MONEY_AMOUNT_PATTERN = re.compile(
    r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2}"
)


@dataclass(frozen=True)
class EconomicSummaryRegionCandidate:
    page: int
    bbox: BoundingBox
    score: float
    selected_as_economic_summary: bool
    anchor_context: str
    amount_count: int
    amount_context: str


@dataclass(frozen=True)
class EconomicSummaryFieldCandidate:
    page: int
    target: str
    label_text: str
    value_text: str
    normalized_value: str
    bbox: BoundingBox
    label_bbox: BoundingBox
    value_bbox: BoundingBox
    score: float
    selected_as_field: bool


@dataclass(frozen=True)
class EconomicSummaryResolvedRow:
    page: int
    bbox: BoundingBox
    totale_imponibile_text: str
    totale_imponibile_value: str
    totale_fattura_text: str
    totale_fattura_value: str
    totale_fattura_source: str
    ritenuta_label_text: str
    ritenuta_text: str
    ritenuta_value: str
    ritenuta_percentuale: str
    ritenuta_source: str
    totale_netto_fattura_text: str
    totale_netto_fattura_value: str
    totale_netto_fattura_source: str
    math_check: str
    notes: str


@dataclass(frozen=True)
class _AnchorHit:
    label: str
    score: float
    bbox: BoundingBox
    text: str


def find_economic_summary_regions(
    document: DocumentRepresentation,
) -> list[EconomicSummaryRegionCandidate]:
    candidates = []

    for page in document.pages:
        anchor_hits = _economic_anchor_hits(page)
        if len({hit.label for hit in anchor_hits}) < 2:
            continue

        amount_tokens = _amount_tokens_near_anchors(page, anchor_hits)
        if len(amount_tokens) < 2:
            continue

        bbox = _region_bbox(
            [hit.bbox for hit in anchor_hits] + [token.bbox for token in amount_tokens],
            page=page,
        )
        candidates.append(
            EconomicSummaryRegionCandidate(
                page=page.page_number,
                bbox=bbox,
                score=_score_region(page, anchor_hits, amount_tokens),
                selected_as_economic_summary=False,
                anchor_context="|".join(hit.label for hit in anchor_hits),
                amount_count=len(amount_tokens),
                amount_context="|".join(
                    _clean_money_amount_text(token.text) or token.text
                    for token in amount_tokens
                ),
            )
        )

    return _mark_selected_targets(candidates)


def select_economic_summary_region_per_page(
    candidates: list[EconomicSummaryRegionCandidate],
    min_score: float = MIN_SELECTED_SCORE,
) -> dict[int, EconomicSummaryRegionCandidate]:
    selected = {}

    for page in sorted({candidate.page for candidate in candidates}):
        page_candidates = [
            candidate
            for candidate in candidates
            if candidate.page == page and candidate.score >= min_score
        ]
        if not page_candidates:
            continue

        selected[page] = max(
            page_candidates,
            key=lambda candidate: (
                candidate.score,
                candidate.amount_count,
                candidate.bbox.y1,
            ),
        )

    return selected


def save_economic_summary_regions_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_economic_summary_regions(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "score",
                "selected_as_economic_summary",
                "anchor_context",
                "amount_count",
                "amount_context",
                "x1",
                "y1",
                "x2",
                "y2",
            ],
        )
        writer.writeheader()

        for candidate in candidates:
            writer.writerow(
                {
                    "source_file": document.source_file.as_posix(),
                    "page": candidate.page,
                    "score": candidate.score,
                    "selected_as_economic_summary": (
                        candidate.selected_as_economic_summary
                    ),
                    "anchor_context": candidate.anchor_context,
                    "amount_count": candidate.amount_count,
                    "amount_context": candidate.amount_context,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


def find_economic_summary_field_candidates(
    document: DocumentRepresentation,
) -> list[EconomicSummaryFieldCandidate]:
    candidates = []
    selected_regions = select_economic_summary_region_per_page(
        find_economic_summary_regions(document)
    )

    for page in document.pages:
        region = selected_regions.get(page.page_number)
        if region is None:
            continue

        region_anchor_hits = [
            hit
            for hit in _economic_anchor_hits(page)
            if _bbox_center_inside(hit.bbox, region.bbox)
        ]
        region_amount_tokens = [
            token
            for token in page.tokens
            if _looks_like_money_amount(token.text)
            and _bbox_center_inside(token.bbox, region.bbox)
            and not any(
                _bbox_center_inside(token.bbox, hit.bbox)
                for hit in region_anchor_hits
            )
        ]

        for target in _economic_targets():
            anchor_hit = _best_anchor_hit_for_target(region_anchor_hits, target)
            if anchor_hit is None:
                continue

            value_token = _nearest_amount_to_the_right(
                page=page,
                anchor_hit=anchor_hit,
                amount_tokens=region_amount_tokens,
            )
            if value_token is None:
                continue

            value_text = _clean_money_amount_text(value_token.text)
            if value_text is None:
                continue

            candidates.append(
                EconomicSummaryFieldCandidate(
                    page=page.page_number,
                    target=target,
                    label_text=anchor_hit.text,
                    value_text=value_text,
                    normalized_value=_normalize_money_amount(value_text),
                    bbox=_merge_bboxes([anchor_hit.bbox, value_token.bbox]),
                    label_bbox=anchor_hit.bbox,
                    value_bbox=value_token.bbox,
                    score=_score_field_link(page, anchor_hit, value_token),
                    selected_as_field=False,
                )
            )

    return _mark_selected_fields(candidates)


def save_economic_summary_field_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_economic_summary_field_candidates(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "target",
                "label_text",
                "value_text",
                "normalized_value",
                "score",
                "selected_as_field",
                "x1",
                "y1",
                "x2",
                "y2",
                "label_x1",
                "label_y1",
                "label_x2",
                "label_y2",
                "value_x1",
                "value_y1",
                "value_x2",
                "value_y2",
            ],
        )
        writer.writeheader()

        for candidate in candidates:
            writer.writerow(
                {
                    "source_file": document.source_file.as_posix(),
                    "page": candidate.page,
                    "target": candidate.target,
                    "label_text": candidate.label_text,
                    "value_text": candidate.value_text,
                    "normalized_value": candidate.normalized_value,
                    "score": candidate.score,
                    "selected_as_field": candidate.selected_as_field,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                    "label_x1": candidate.label_bbox.x1,
                    "label_y1": candidate.label_bbox.y1,
                    "label_x2": candidate.label_bbox.x2,
                    "label_y2": candidate.label_bbox.y2,
                    "value_x1": candidate.value_bbox.x1,
                    "value_y1": candidate.value_bbox.y1,
                    "value_x2": candidate.value_bbox.x2,
                    "value_y2": candidate.value_bbox.y2,
                }
            )

    return output_path


def resolve_economic_summary_rows(
    document: DocumentRepresentation,
) -> list[EconomicSummaryResolvedRow]:
    selected_regions = select_economic_summary_region_per_page(
        find_economic_summary_regions(document)
    )
    selected_fields = [
        candidate
        for candidate in find_economic_summary_field_candidates(document)
        if candidate.selected_as_field
    ]
    rows = []

    for page, region in selected_regions.items():
        fields = {
            candidate.target: candidate
            for candidate in selected_fields
            if candidate.page == page
        }
        rows.append(_resolve_economic_summary_row(region, fields))

    return rows


def save_economic_summary_resolved_rows_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = resolve_economic_summary_rows(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "totale_imponibile_text",
                "totale_imponibile_value",
                "totale_fattura_text",
                "totale_fattura_value",
                "totale_fattura_source",
                "ritenuta_label_text",
                "ritenuta_text",
                "ritenuta_value",
                "ritenuta_percentuale",
                "ritenuta_source",
                "totale_netto_fattura_text",
                "totale_netto_fattura_value",
                "totale_netto_fattura_source",
                "math_check",
                "notes",
                "x1",
                "y1",
                "x2",
                "y2",
            ],
        )
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "source_file": document.source_file.as_posix(),
                    "page": row.page,
                    "totale_imponibile_text": row.totale_imponibile_text,
                    "totale_imponibile_value": row.totale_imponibile_value,
                    "totale_fattura_text": row.totale_fattura_text,
                    "totale_fattura_value": row.totale_fattura_value,
                    "totale_fattura_source": row.totale_fattura_source,
                    "ritenuta_label_text": row.ritenuta_label_text,
                    "ritenuta_text": row.ritenuta_text,
                    "ritenuta_value": row.ritenuta_value,
                    "ritenuta_percentuale": row.ritenuta_percentuale,
                    "ritenuta_source": row.ritenuta_source,
                    "totale_netto_fattura_text": row.totale_netto_fattura_text,
                    "totale_netto_fattura_value": row.totale_netto_fattura_value,
                    "totale_netto_fattura_source": (
                        row.totale_netto_fattura_source
                    ),
                    "math_check": row.math_check,
                    "notes": row.notes,
                    "x1": row.bbox.x1,
                    "y1": row.bbox.y1,
                    "x2": row.bbox.x2,
                    "y2": row.bbox.y2,
                }
            )

    return output_path


def _mark_selected_targets(
    candidates: list[EconomicSummaryRegionCandidate],
) -> list[EconomicSummaryRegionCandidate]:
    selected = select_economic_summary_region_per_page(candidates)
    marked_candidates = []

    for candidate in candidates:
        marked_candidates.append(
            EconomicSummaryRegionCandidate(
                page=candidate.page,
                bbox=candidate.bbox,
                score=candidate.score,
                selected_as_economic_summary=selected.get(candidate.page)
                == candidate,
                anchor_context=candidate.anchor_context,
                amount_count=candidate.amount_count,
                amount_context=candidate.amount_context,
            )
        )

    return marked_candidates


def _resolve_economic_summary_row(
    region: EconomicSummaryRegionCandidate,
    fields: dict[str, EconomicSummaryFieldCandidate],
) -> EconomicSummaryResolvedRow:
    totale_imponibile = _field_decimal(fields.get("totale_imponibile"))
    totale_fattura = _field_decimal(fields.get("totale_fattura"))
    ritenuta_direct = _field_decimal(fields.get("ritenuta"))
    totale_netto = _field_decimal(fields.get("totale_netto_fattura"))
    ritenuta_field = fields.get("ritenuta")
    percent_hint = _ritenuta_percent_hint(ritenuta_field.label_text if ritenuta_field else "")
    percent_candidates = _percent_candidates(percent_hint)
    notes = []

    ritenuta_abs = abs(ritenuta_direct) if ritenuta_direct is not None else None
    ritenuta_source = "direct" if ritenuta_abs is not None else ""
    selected_percent = percent_candidates[0]

    if totale_imponibile is not None:
        matched_percent = _percent_matching_ritenuta(
            totale_imponibile,
            ritenuta_abs,
            percent_candidates,
        )
        if matched_percent is not None:
            selected_percent = matched_percent
            notes.append("ritenuta_matches_imponibile_percent")
        else:
            derived_from_net = _derive_ritenuta_from_totale_netto(
                totale_fattura,
                totale_netto,
            )
            matched_percent = _percent_matching_ritenuta(
                totale_imponibile,
                derived_from_net,
                percent_candidates,
            )
            if matched_percent is not None and derived_from_net is not None:
                selected_percent = matched_percent
                ritenuta_abs = derived_from_net
                ritenuta_source = "derived_from_totale_fattura_minus_netto"
                notes.append("ritenuta_derived_from_totale_netto")
            elif ritenuta_abs is None:
                ritenuta_abs = _ritenuta_from_imponibile(
                    totale_imponibile,
                    selected_percent,
                )
                ritenuta_source = "calculated_from_imponibile"
                notes.append("ritenuta_calculated_from_default_or_hint_percent")
            elif percent_hint is not None:
                notes.append("ritenuta_kept_from_direct_value_with_percent_hint")

    if percent_hint is None:
        notes.append("ritenuta_percent_default_4")

    resolved_netto = totale_netto
    netto_source = "direct" if resolved_netto is not None else ""
    if totale_fattura is not None and ritenuta_abs is not None:
        calculated_netto = _money(totale_fattura - ritenuta_abs)
        if resolved_netto is None:
            resolved_netto = calculated_netto
            netto_source = "calculated_from_totale_fattura_minus_ritenuta"
            notes.append("netto_calculated")
        elif _money_matches(resolved_netto, calculated_netto):
            notes.append("netto_matches_totale_minus_ritenuta")
        else:
            notes.append("netto_mismatch_totale_minus_ritenuta")

    if (
        totale_fattura is not None
        and ritenuta_abs is not None
        and resolved_netto is not None
        and _money_matches(_money(totale_fattura - ritenuta_abs), resolved_netto)
    ):
        math_check = "ok"
    else:
        math_check = "uncertain"

    signed_ritenuta = None
    if ritenuta_abs is not None:
        signed_ritenuta = -ritenuta_abs

    return EconomicSummaryResolvedRow(
        page=region.page,
        bbox=region.bbox,
        totale_imponibile_text=_field_value_text(fields.get("totale_imponibile")),
        totale_imponibile_value=_format_decimal(totale_imponibile),
        totale_fattura_text=_field_value_text(fields.get("totale_fattura")),
        totale_fattura_value=_format_decimal(totale_fattura),
        totale_fattura_source="direct" if totale_fattura is not None else "",
        ritenuta_label_text=ritenuta_field.label_text if ritenuta_field else "",
        ritenuta_text=_field_value_text(ritenuta_field),
        ritenuta_value=_format_decimal(signed_ritenuta),
        ritenuta_percentuale=f"{selected_percent}%",
        ritenuta_source=ritenuta_source,
        totale_netto_fattura_text=_field_value_text(
            fields.get("totale_netto_fattura")
        ),
        totale_netto_fattura_value=_format_decimal(resolved_netto),
        totale_netto_fattura_source=netto_source,
        math_check=math_check,
        notes="|".join(notes),
    )


def _field_value_text(candidate: EconomicSummaryFieldCandidate | None) -> str:
    return candidate.value_text if candidate is not None else ""


def _field_decimal(candidate: EconomicSummaryFieldCandidate | None) -> Decimal | None:
    if candidate is None:
        return None

    try:
        return Decimal(candidate.normalized_value)
    except InvalidOperation:
        return None


def _ritenuta_percent_hint(label_text: str) -> int | None:
    words = _normalize_context_text(label_text).split()
    for word in words:
        if word.startswith("20"):
            return 20
        if word.startswith("2") and word != "22":
            return 20
        if word.startswith("4"):
            return 4

    return None


def _percent_candidates(percent_hint: int | None) -> list[int]:
    if percent_hint == 20:
        return [20, 4]

    return [4, 20]


def _percent_matching_ritenuta(
    imponibile: Decimal,
    ritenuta_abs: Decimal | None,
    percent_candidates: list[int],
) -> int | None:
    if ritenuta_abs is None:
        return None

    for percent in percent_candidates:
        if _money_matches(_ritenuta_from_imponibile(imponibile, percent), ritenuta_abs):
            return percent

    return None


def _derive_ritenuta_from_totale_netto(
    totale_fattura: Decimal | None,
    totale_netto: Decimal | None,
) -> Decimal | None:
    if totale_fattura is None or totale_netto is None:
        return None

    return abs(_money(totale_fattura - totale_netto))


def _ritenuta_from_imponibile(imponibile: Decimal, percent: int) -> Decimal:
    return _money(imponibile * Decimal(percent) / Decimal("100"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money_matches(left: Decimal, right: Decimal) -> bool:
    return abs(_money(left) - _money(right)) <= CENT_TOLERANCE


def _format_decimal(value: Decimal | None) -> str:
    if value is None:
        return ""

    return f"{_money(value):.2f}"


def _mark_selected_fields(
    candidates: list[EconomicSummaryFieldCandidate],
) -> list[EconomicSummaryFieldCandidate]:
    selected_by_page_and_target = {}

    for candidate in candidates:
        key = (candidate.page, candidate.target)
        current = selected_by_page_and_target.get(key)
        if current is None or candidate.score > current.score:
            selected_by_page_and_target[key] = candidate

    marked_candidates = []
    for candidate in candidates:
        marked_candidates.append(
            EconomicSummaryFieldCandidate(
                page=candidate.page,
                target=candidate.target,
                label_text=candidate.label_text,
                value_text=candidate.value_text,
                normalized_value=candidate.normalized_value,
                bbox=candidate.bbox,
                label_bbox=candidate.label_bbox,
                value_bbox=candidate.value_bbox,
                score=candidate.score,
                selected_as_field=selected_by_page_and_target.get(
                    (candidate.page, candidate.target)
                )
                == candidate,
            )
        )

    return marked_candidates


def _economic_targets() -> list[str]:
    return [
        "totale_imponibile",
        "totale_imposta",
        "totale_fattura",
        "ritenuta",
        "totale_netto_fattura",
    ]


def _best_anchor_hit_for_target(
    anchor_hits: list[_AnchorHit],
    target: str,
) -> _AnchorHit | None:
    target_hits = [hit for hit in anchor_hits if hit.label == target]
    if not target_hits:
        return None

    return max(
        target_hits,
        key=lambda hit: (
            hit.score,
            -hit.bbox.y1,
            -hit.bbox.x1,
        ),
    )


def _nearest_amount_to_the_right(
    page: DocumentPage,
    anchor_hit: _AnchorHit,
    amount_tokens: list[OcrToken],
) -> OcrToken | None:
    candidate_tokens = [
        token
        for token in amount_tokens
        if token.bbox.x1 >= anchor_hit.bbox.x1
        and token.bbox.y1 >= anchor_hit.bbox.y1 - max(6, int(page.height * 0.004))
        and not _bbox_center_inside(token.bbox, anchor_hit.bbox)
    ]
    if not candidate_tokens:
        return None

    same_row_tokens = [
        token
        for token in candidate_tokens
        if _is_same_visual_row(anchor_hit.bbox, token.bbox)
    ]
    if same_row_tokens:
        return min(
            same_row_tokens,
            key=lambda token: max(0, token.bbox.x1 - anchor_hit.bbox.x1),
        )

    return min(
        candidate_tokens,
        key=lambda token: _field_link_distance(page, anchor_hit.bbox, token.bbox),
    )


def _is_same_visual_row(label_bbox: BoundingBox, value_bbox: BoundingBox) -> bool:
    label_center_y = (label_bbox.y1 + label_bbox.y2) / 2
    value_center_y = (value_bbox.y1 + value_bbox.y2) / 2
    label_height = label_bbox.y2 - label_bbox.y1
    value_height = value_bbox.y2 - value_bbox.y1
    return abs(label_center_y - value_center_y) <= max(
        8,
        min(label_height, value_height) * 0.85,
    )


def _score_field_link(
    page: DocumentPage,
    anchor_hit: _AnchorHit,
    value_token: OcrToken,
) -> float:
    distance = _field_link_distance(page, anchor_hit.bbox, value_token.bbox)
    proximity_score = max(0, 100 - distance * 220)
    return round(anchor_hit.score + proximity_score, 2)


def _field_link_distance(
    page: DocumentPage,
    label_bbox: BoundingBox,
    value_bbox: BoundingBox,
) -> float:
    label_x = label_bbox.x1
    label_y = (label_bbox.y1 + label_bbox.y2) / 2
    value_x = value_bbox.x1
    value_y = (value_bbox.y1 + value_bbox.y2) / 2
    x_distance = max(0, value_x - label_x) / page.width
    y_distance = abs(value_y - label_y) / page.height * 2.7
    return (x_distance**2 + y_distance**2) ** 0.5


def _economic_anchor_hits(page: DocumentPage) -> list[_AnchorHit]:
    hits = []

    for span in page.spans:
        normalized_text = _normalize_context_text(span.text)
        for label, anchor in _anchor_definitions():
            if _is_ambiguous_economic_anchor(label, normalized_text):
                continue

            similarity = _best_anchor_similarity(normalized_text, anchor)
            if similarity >= MIN_FUZZY_ANCHOR_SIMILARITY:
                hits.append(
                    _AnchorHit(
                        label=label,
                        score=round(similarity * 100, 2),
                        bbox=span.bbox,
                        text=span.text,
                    )
                )

    return _deduplicate_anchor_hits(hits)


def _anchor_definitions() -> list[tuple[str, str]]:
    return [
        ("totale_imponibile", "totale imponibile"),
        ("totale_imponibile", "tot imponibile"),
        ("totale_imposta", "totale imposta"),
        ("totale_imposta", "tot iva"),
        ("totale_fattura", "totale fattura euro"),
        ("totale_fattura", "totale fattura"),
        ("totale_fattura", "tot documento"),
        ("ritenuta", "ritenuta"),
        ("ritenuta", "ritenuta 4"),
        ("ritenuta", "ritenuta 20"),
        ("ritenuta", "rit acconto"),
        ("ritenuta", "rit acconto 4"),
        ("ritenuta", "rit acconto 20"),
        ("totale_netto_fattura", "totale netto fattura euro"),
        ("totale_netto_fattura", "totale netto fattura"),
        ("totale_netto_fattura", "netto fattura"),
        ("totale_netto_fattura", "netto a pagare"),
        ("totale_netto_fattura", "importo da pagare"),
        ("totale_netto_fattura", "tot dovuto"),
    ]


def _is_ambiguous_economic_anchor(label: str, normalized_text: str) -> bool:
    words = normalized_text.split()
    if label == "totale_imposta" and "imponibile" in words:
        return True

    if label == "totale_imponibile" and "imposta" in words:
        return True

    return False


def _amount_tokens_near_anchors(
    page: DocumentPage,
    anchor_hits: list[_AnchorHit],
) -> list[OcrToken]:
    amount_tokens = []

    for token in page.tokens:
        if not _looks_like_money_amount(token.text):
            continue

        if any(_is_near_anchor(page, token, hit) for hit in anchor_hits):
            amount_tokens.append(token)

    return sorted(
        _deduplicate_tokens(amount_tokens),
        key=lambda token: (token.bbox.y1, token.bbox.x1),
    )


def _is_near_anchor(page: DocumentPage, token: OcrToken, hit: _AnchorHit) -> bool:
    token_center_x = (token.bbox.x1 + token.bbox.x2) / 2
    token_center_y = (token.bbox.y1 + token.bbox.y2) / 2
    anchor_center_x = (hit.bbox.x1 + hit.bbox.x2) / 2
    anchor_center_y = (hit.bbox.y1 + hit.bbox.y2) / 2

    is_reasonably_close = (
        abs(token_center_x - anchor_center_x) <= page.width * 0.45
        and abs(token_center_y - anchor_center_y) <= page.height * 0.09
    )
    is_to_the_right_or_below = (
        token.bbox.x1 >= hit.bbox.x1 - page.width * 0.04
        and token.bbox.y1 >= hit.bbox.y1 - page.height * 0.025
    )
    return is_reasonably_close and is_to_the_right_or_below


def _score_region(
    page: DocumentPage,
    anchor_hits: list[_AnchorHit],
    amount_tokens: list[OcrToken],
) -> float:
    labels = {hit.label for hit in anchor_hits}
    score = 0.0

    if "totale_imponibile" in labels:
        score += 35
    if "totale_imposta" in labels:
        score += 25
    if "totale_fattura" in labels:
        score += 35
    if "ritenuta" in labels:
        score += 25
    if "totale_netto_fattura" in labels:
        score += 40

    score += min(len(amount_tokens), 5) * 8
    if _amounts_are_right_aligned(amount_tokens):
        score += 15

    min_y = min(hit.bbox.y1 for hit in anchor_hits)
    if min_y / page.height >= 0.55:
        score += 10

    return round(score, 2)


def _amounts_are_right_aligned(amount_tokens: list[OcrToken]) -> bool:
    if len(amount_tokens) < 2:
        return False

    x2_values = [token.bbox.x2 for token in amount_tokens]
    return max(x2_values) - min(x2_values) <= 140


def _region_bbox(boxes: list[BoundingBox], page: DocumentPage) -> BoundingBox:
    padding_x = int(page.width * 0.012)
    padding_y = int(page.height * 0.009)
    return BoundingBox(
        x1=max(0, min(box.x1 for box in boxes) - padding_x),
        y1=max(0, min(box.y1 for box in boxes) - padding_y),
        x2=min(page.width, max(box.x2 for box in boxes) + padding_x),
        y2=min(page.height, max(box.y2 for box in boxes) + padding_y),
    )


def _bbox_center_inside(bbox: BoundingBox, container: BoundingBox) -> bool:
    center_x = (bbox.x1 + bbox.x2) / 2
    center_y = (bbox.y1 + bbox.y2) / 2
    return (
        container.x1 <= center_x <= container.x2
        and container.y1 <= center_y <= container.y2
    )


def _merge_bboxes(boxes: list[BoundingBox]) -> BoundingBox:
    return BoundingBox(
        x1=min(box.x1 for box in boxes),
        y1=min(box.y1 for box in boxes),
        x2=max(box.x2 for box in boxes),
        y2=max(box.y2 for box in boxes),
    )


def _clean_money_amount_text(text: str) -> str | None:
    normalized = text.strip()
    normalized = normalized.replace("€", "")
    match = MONEY_AMOUNT_PATTERN.search(normalized)
    if match is None:
        return None
    return match.group(0)


def _looks_like_money_amount(text: str) -> bool:
    return _clean_money_amount_text(text) is not None

    normalized = normalized.replace("€", "")


def _normalize_money_amount(text: str) -> str:
    cleaned = _clean_money_amount_text(text)
    if cleaned is not None:
        if "," in cleaned:
            cleaned = cleaned.replace(".", "")
            return cleaned.replace(",", ".")
        return cleaned

    normalized = text.strip()
    normalized = normalized.replace("€", "")
    normalized = re.sub(r"^[^0-9,-]+|[^0-9]+$", "", normalized)
    normalized = normalized.replace(".", "")
    return normalized.replace(",", ".")


def _normalize_context_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def _best_anchor_similarity(text: str, anchor: str) -> float:
    anchor_words = anchor.split()
    text_words = text.split()
    if len(text_words) < len(anchor_words):
        return 0.0

    best = 0.0
    for index in range(len(text_words) - len(anchor_words) + 1):
        window = " ".join(text_words[index : index + len(anchor_words)])
        best = max(best, SequenceMatcher(None, window, anchor).ratio())

    return best


def _deduplicate_anchor_hits(hits: list[_AnchorHit]) -> list[_AnchorHit]:
    best_by_label_and_box = {}
    for hit in hits:
        key = (hit.label, hit.bbox)
        current = best_by_label_and_box.get(key)
        if current is None or hit.score > current.score:
            best_by_label_and_box[key] = hit

    return sorted(
        best_by_label_and_box.values(),
        key=lambda hit: (hit.bbox.y1, hit.bbox.x1, hit.label),
    )


def _deduplicate_tokens(tokens: list[OcrToken]) -> list[OcrToken]:
    deduplicated = []
    seen = set()
    for token in tokens:
        key = (token.text, token.bbox)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(token)

    return deduplicated
