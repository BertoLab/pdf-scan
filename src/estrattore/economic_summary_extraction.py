from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from pathlib import Path
import re

from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken, TextSpan


MIN_FUZZY_ANCHOR_SIMILARITY = 0.88
MIN_SELECTED_SCORE = 80.0
CENT_TOLERANCE = Decimal("0.01")
ALLOWED_RITENUTA_PERCENTAGES = (0, 4, 10)
MONEY_AMOUNT_PATTERN = re.compile(
    r"-?\d{1,3}(?:\.\d{3})*,\d{2}|-?\d+,\d{2}|-?\d+\.\d{2}"
)
TABLE_CROP_TOKEN_SOURCE = "table_column_masked_psm12"
MIN_TABLE_CROP_PAIR_SCORE = 0.42
MIN_ACCOUNTING_DERIVATION_ANCHOR_SCORE = 0.70


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


@dataclass(frozen=True)
class _EconomicValueCandidate:
    text: str
    value: Decimal
    bbox: BoundingBox
    confidence: float
    source: str
    anchor_text: str = ""
    anchor_score: float = 0.0


@dataclass(frozen=True)
class _EconomicValueCombination:
    values: dict[str, _EconomicValueCandidate]
    score: float
    notes: tuple[str, ...]


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
            for token in _money_value_tokens_with_split_repairs(page.tokens, page)
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
                anchor_hits=region_anchor_hits,
                amount_tokens=region_amount_tokens,
            )
            if value_token is None:
                continue
            if (
                target == "ritenuta"
                and "%" in anchor_hit.text
                and _ritenuta_percent_hint(anchor_hit.text) is None
                and not _is_same_visual_row(anchor_hit.bbox, value_token.bbox)
            ):
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
        document_page = next(
            (document_page for document_page in document.pages if document_page.page_number == page),
            None,
        )
        rows.append(_resolve_economic_summary_row(region, fields, document_page))

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
    page: DocumentPage | None = None,
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
    totale_fattura_source_override = ""
    totale_fattura_text_override = ""
    totale_imponibile_text_override = ""
    selected_percent = percent_hint

    if totale_imponibile is None and page is not None:
        structural_taxable = _structural_taxable_column_candidate(
            page=page,
            region=region,
        )
        if structural_taxable is not None:
            totale_imponibile = structural_taxable.value
            totale_imponibile_text_override = structural_taxable.text
            notes.append("totale_imponibile_from_structural_column")

    if (
        totale_fattura is not None
        and totale_netto is not None
        and totale_fattura + CENT_TOLERANCE < totale_netto
    ):
        totale_fattura = None
        totale_fattura_source_override = "rejected_gross_below_net"
        notes.append("totale_fattura_rejected_below_net")

    if selected_percent is None and page is not None and ritenuta_field is not None:
        selected_percent = _ritenuta_percent_from_same_row_percent_column(
            page,
            ritenuta_field,
        )
        if selected_percent is not None:
            notes.append("ritenuta_percent_from_same_row_percent_column")

    if ritenuta_abs is None:
        derived_from_net = _derive_ritenuta_from_totale_netto(
            totale_fattura,
            totale_netto,
        )
        if derived_from_net is not None:
            ritenuta_abs = derived_from_net
            ritenuta_source = "derived_from_totale_fattura_minus_netto"
            notes.append("ritenuta_derived_from_totale_netto")

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
            elif percent_hint is not None and ritenuta_abs is not None:
                notes.append("ritenuta_kept_from_direct_value_with_percent_hint")

    combination = (
        _best_economic_value_combination(
            page=page,
            region=region,
            fields=fields,
            percent=selected_percent,
        )
        if page is not None
        else None
    )
    combination_values = combination.values if combination is not None else {}
    if totale_imponibile is None and "totale_imponibile" in combination_values:
        totale_imponibile = combination_values["totale_imponibile"].value
        notes.append("totale_imponibile_from_combinatorial_candidates")
    if totale_fattura is None and "totale_fattura" in combination_values:
        totale_fattura = combination_values["totale_fattura"].value
        notes.append("totale_fattura_from_combinatorial_candidates")
    if ritenuta_abs is None and "ritenuta" in combination_values:
        ritenuta_abs = abs(combination_values["ritenuta"].value)
        ritenuta_source = "combinatorial"
        notes.append("ritenuta_from_combinatorial_candidates")
    if totale_netto is None and "totale_netto_fattura" in combination_values:
        totale_netto = combination_values["totale_netto_fattura"].value
        notes.append("totale_netto_from_combinatorial_candidates")
    if combination is not None and combination.notes:
        notes.extend(combination.notes)

    if (
        totale_fattura is None
        and totale_netto is not None
        and ritenuta_abs is not None
        and ritenuta_source == "direct"
        and fields.get("totale_netto_fattura") is not None
        and page is not None
    ):
        expected_totale_fattura = _money(totale_netto + ritenuta_abs)
        matching_total_token = _matching_money_token_in_region(
            page=page,
            region=region,
            expected_value=expected_totale_fattura,
        )
        if matching_total_token is not None:
            totale_fattura = expected_totale_fattura
            totale_fattura_text_override = (
                _clean_money_amount_text(matching_total_token.text)
                or matching_total_token.text
            )
            totale_fattura_source_override = (
                "regional_token_matching_netto_plus_ritenuta"
            )
            notes.append("totale_fattura_matched_netto_plus_ritenuta")

    if (
        totale_fattura is None
        and totale_netto is not None
        and ritenuta_abs is None
        and page is not None
        and _page_has_forfettario_tax_regime(page)
    ):
        totale_fattura = totale_netto
        totale_fattura_source_override = "derived_from_netto_forfettario_without_ritenuta"
        notes.append("totale_fattura_from_netto_forfettario_without_ritenuta")

    resolved_netto = totale_netto
    netto_source = "direct" if resolved_netto is not None else ""
    if fields.get("totale_netto_fattura") is None and "totale_netto_fattura" in combination_values:
        netto_source = "combinatorial"
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
    elif (
        totale_fattura is not None
        and resolved_netto is not None
        and ritenuta_abs is None
        and page is not None
        and _page_has_forfettario_tax_regime(page)
        and _money_matches(totale_fattura, resolved_netto)
    ):
        ritenuta_abs = Decimal("0.00")
        ritenuta_source = "implicit_zero_forfettario"
        selected_percent = 0
        math_check = "ok"
        notes.append("ritenuta_zero_forfettario_totale_matches_netto")
    else:
        math_check = "uncertain"

    if selected_percent is None:
        selected_percent = _infer_allowed_ritenuta_percent(
            totale_imponibile=totale_imponibile,
            totale_fattura=totale_fattura,
            ritenuta_abs=ritenuta_abs,
            resolved_netto=resolved_netto,
            percent_candidates=percent_candidates,
            page=page,
            region=region,
        )
        if selected_percent is not None:
            notes.append("ritenuta_percent_inferred_from_accounting_values")

    signed_ritenuta = None
    if ritenuta_abs is not None:
        signed_ritenuta = -ritenuta_abs

    return EconomicSummaryResolvedRow(
        page=region.page,
        bbox=region.bbox,
        totale_imponibile_text=_field_or_combination_value_text(
            fields.get("totale_imponibile"),
            combination_values.get("totale_imponibile"),
        )
        or totale_imponibile_text_override,
        totale_imponibile_value=_format_decimal(totale_imponibile),
        totale_fattura_text=(
            ""
            if totale_fattura_source_override.startswith("rejected_")
            else _field_or_combination_value_text(
                fields.get("totale_fattura"),
                combination_values.get("totale_fattura"),
            )
            or totale_fattura_text_override
        ),
        totale_fattura_value=_format_decimal(totale_fattura),
        totale_fattura_source=(
            totale_fattura_source_override
            if totale_fattura_source_override
            else
            "direct"
            if fields.get("totale_fattura") is not None
            else "combinatorial"
            if "totale_fattura" in combination_values
            else ""
        ),
        ritenuta_label_text=ritenuta_field.label_text if ritenuta_field else "",
        ritenuta_text=_field_or_combination_value_text(
            ritenuta_field,
            combination_values.get("ritenuta"),
        ),
        ritenuta_value=_format_decimal(signed_ritenuta),
        ritenuta_percentuale=f"{selected_percent}%" if selected_percent is not None else "",
        ritenuta_source=ritenuta_source,
        totale_netto_fattura_text=_field_or_combination_value_text(
            fields.get("totale_netto_fattura"),
            combination_values.get("totale_netto_fattura"),
        ),
        totale_netto_fattura_value=_format_decimal(resolved_netto),
        totale_netto_fattura_source=netto_source,
        math_check=math_check,
        notes="|".join(notes),
    )


def _field_value_text(candidate: EconomicSummaryFieldCandidate | None) -> str:
    return candidate.value_text if candidate is not None else ""


def _field_or_combination_value_text(
    field_candidate: EconomicSummaryFieldCandidate | None,
    combination_candidate: _EconomicValueCandidate | None,
) -> str:
    if field_candidate is not None:
        return field_candidate.value_text
    if combination_candidate is not None:
        return combination_candidate.text
    return ""


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
        if word.startswith("10"):
            return 10
        if word.startswith("4"):
            return 4
        if word.startswith("0"):
            return 0

    return None


def _percent_candidates(percent_hint: int | None) -> list[int]:
    if percent_hint in ALLOWED_RITENUTA_PERCENTAGES:
        return [
            percent_hint,
            *[
                percent
                for percent in ALLOWED_RITENUTA_PERCENTAGES
                if percent != percent_hint
            ],
        ]

    return [4, 0, 10]


def _page_has_forfettario_tax_regime(page: DocumentPage) -> bool:
    text = _normalize_context_text(
        " ".join(
            [
                *(token.text for token in page.tokens),
                *(span.text for span in page.spans),
            ]
        )
    )
    words = set(text.split())
    if words.intersection({"forf", "forfetario", "forfetari", "forfettario", "forfettari"}):
        return True
    return "190 2014" in text or "208 2015" in text


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


def _ritenuta_percent_from_same_row_percent_column(
    page: DocumentPage,
    ritenuta_field: EconomicSummaryFieldCandidate,
) -> int | None:
    value_bbox = ritenuta_field.value_bbox
    candidates = []
    for token in page.tokens:
        if token.bbox == value_bbox:
            continue
        if token.bbox.x2 > value_bbox.x1:
            continue
        if value_bbox.x1 - token.bbox.x2 > max(260, int(page.width * 0.18)):
            continue
        if not _is_same_visual_row(token.bbox, value_bbox):
            continue

        percent = _allowed_percent_from_token_text(token.text)
        if percent is None:
            continue
        if "%" not in token.text and not _has_percent_header_above(page, token):
            continue

        candidates.append((percent, token.bbox.x1))

    if not candidates:
        return None

    return max(candidates, key=lambda candidate: candidate[1])[0]


def _infer_allowed_ritenuta_percent(
    totale_imponibile: Decimal | None,
    totale_fattura: Decimal | None,
    ritenuta_abs: Decimal | None,
    resolved_netto: Decimal | None,
    percent_candidates: list[int],
    page: DocumentPage | None = None,
    region: EconomicSummaryRegionCandidate | None = None,
) -> int | None:
    if ritenuta_abs is None and totale_fattura is not None and resolved_netto is not None:
        ritenuta_abs = _derive_ritenuta_from_totale_netto(
            totale_fattura,
            resolved_netto,
        )

    if ritenuta_abs is None:
        return None

    if _money_matches(ritenuta_abs, Decimal("0.00")):
        return 0

    if totale_imponibile is not None:
        return _percent_matching_ritenuta(
            totale_imponibile,
            ritenuta_abs,
            percent_candidates,
        )

    if page is not None and region is not None:
        return _percent_matching_page_amount(
            page=page,
            region=region,
            ritenuta_abs=ritenuta_abs,
            percent_candidates=percent_candidates,
        )

    return None


def _percent_matching_page_amount(
    page: DocumentPage,
    region: EconomicSummaryRegionCandidate,
    ritenuta_abs: Decimal,
    percent_candidates: list[int],
) -> int | None:
    matches: dict[int, int] = defaultdict(int)
    vertical_margin = max(80, int(page.height * 0.04))
    min_y = max(0, region.bbox.y1 - vertical_margin)
    max_y = min(page.height, region.bbox.y2 + vertical_margin)

    for token in _money_value_tokens_with_split_repairs(page.tokens, page):
        center_y = (token.bbox.y1 + token.bbox.y2) / 2
        if center_y < min_y or center_y > max_y:
            continue

        try:
            value = Decimal(_normalize_money_amount(token.text))
        except InvalidOperation:
            continue

        if _money_matches(value, ritenuta_abs):
            continue

        for percent in percent_candidates:
            if percent == 0:
                continue
            if _money_matches(_ritenuta_from_imponibile(value, percent), ritenuta_abs):
                matches[percent] += 1

    if len(matches) != 1:
        return None

    return next(iter(matches))


def _allowed_percent_from_token_text(text: str) -> int | None:
    cleaned_text = text.strip().replace("%", "").replace(",", ".")
    try:
        value = Decimal(cleaned_text)
    except InvalidOperation:
        return None

    if value != value.to_integral_value():
        return None

    percent = int(value)
    return percent if percent in ALLOWED_RITENUTA_PERCENTAGES else None


def _has_percent_header_above(page: DocumentPage, value_token: OcrToken) -> bool:
    token_center_x = (value_token.bbox.x1 + value_token.bbox.x2) / 2
    max_dx = max(32, page.width * 0.035)
    max_dy = max(120, page.height * 0.06)
    for span in page.spans:
        if span.bbox.y1 >= value_token.bbox.y1:
            continue
        if value_token.bbox.y1 - span.bbox.y2 > max_dy:
            continue
        span_center_x = (span.bbox.x1 + span.bbox.x2) / 2
        if abs(span_center_x - token_center_x) > max_dx:
            continue
        normalized = _normalize_context_text(span.text)
        if "%" in span.text or "percent" in normalized:
            return True

    return False


def _derive_ritenuta_from_totale_netto(
    totale_fattura: Decimal | None,
    totale_netto: Decimal | None,
) -> Decimal | None:
    if totale_fattura is None or totale_netto is None:
        return None

    if totale_fattura + CENT_TOLERANCE < totale_netto:
        return None

    return _money(totale_fattura - totale_netto)


def _structural_taxable_column_candidate(
    page: DocumentPage,
    region: EconomicSummaryRegionCandidate,
) -> _EconomicValueCandidate | None:
    """Read a value below an explicit taxable column in a summary header row."""
    candidates: list[tuple[float, _EconomicValueCandidate]] = []
    for header in page.spans:
        if _normalize_context_text(header.text) != "imponibile":
            continue
        if not _bbox_center_inside(header.bbox, region.bbox):
            continue
        if not _is_economic_column_header_row(page, header):
            continue

        header_center_x = (header.bbox.x1 + header.bbox.x2) / 2
        max_horizontal_offset = max(28, page.width * 0.025)
        max_vertical_gap = max(90, page.height * 0.065)
        for token in _money_value_tokens_with_split_repairs(page.tokens, page):
            if token.bbox.y1 < header.bbox.y2 - 2:
                continue
            vertical_gap = token.bbox.y1 - header.bbox.y2
            if vertical_gap > max_vertical_gap:
                continue
            token_center_x = (token.bbox.x1 + token.bbox.x2) / 2
            horizontal_offset = abs(token_center_x - header_center_x)
            if horizontal_offset > max_horizontal_offset:
                continue
            if not _bbox_center_inside(token.bbox, region.bbox):
                continue

            value_text = _clean_money_amount_text(token.text)
            if value_text is None:
                continue
            try:
                value = abs(_money(Decimal(_normalize_money_amount(value_text))))
            except InvalidOperation:
                continue

            distance = vertical_gap / page.height + horizontal_offset / page.width
            candidates.append(
                (
                    distance,
                    _EconomicValueCandidate(
                        text=value_text,
                        value=value,
                        bbox=token.bbox,
                        confidence=token.confidence if token.confidence is not None else 0.55,
                        source="structural_taxable_column",
                        anchor_text=header.text,
                        anchor_score=1.0,
                    ),
                )
            )

    if not candidates:
        return None

    return min(candidates, key=lambda item: item[0])[1]


def _is_economic_column_header_row(page: DocumentPage, header: TextSpan) -> bool:
    companion_words: set[str] = set()
    header_center_y = (header.bbox.y1 + header.bbox.y2) / 2
    row_tolerance = max(16, page.height * 0.012)
    for span in page.spans:
        if span is header:
            continue
        span_center_y = (span.bbox.y1 + span.bbox.y2) / 2
        if abs(span_center_y - header_center_y) > row_tolerance:
            continue
        companion_words.update(_normalize_context_text(span.text).split())

    return bool(companion_words.intersection({"percentuale", "ritenuta", "imposta"}))


def _matching_money_token_in_region(
    page: DocumentPage,
    region: EconomicSummaryRegionCandidate,
    expected_value: Decimal,
) -> OcrToken | None:
    matches = []
    for token in _money_value_tokens_with_split_repairs(page.tokens, page):
        if not _bbox_center_inside(token.bbox, region.bbox):
            continue
        value_text = _clean_money_amount_text(token.text)
        if value_text is None:
            continue
        try:
            value = abs(_money(Decimal(_normalize_money_amount(value_text))))
        except InvalidOperation:
            continue
        if _money_matches(value, expected_value):
            matches.append(token)

    return max(
        matches,
        key=lambda token: token.confidence if token.confidence is not None else 0.0,
        default=None,
    )


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


def _best_economic_value_combination(
    page: DocumentPage | None,
    region: EconomicSummaryRegionCandidate,
    fields: dict[str, EconomicSummaryFieldCandidate],
    percent: int | None,
) -> _EconomicValueCombination | None:
    if page is None:
        return None

    combination_bbox = _economic_combination_bbox(page, region.bbox)
    crop_candidates = _table_crop_value_candidates_by_target(page, combination_bbox)
    if not any(crop_candidates.values()):
        return None

    anchor_hits = [
        hit
        for hit in _economic_anchor_hits(page)
        if _bbox_center_inside(hit.bbox, combination_bbox)
    ]
    candidates_by_target = {
        target: _target_value_candidates(
            target=target,
            crop_candidates=crop_candidates.get(target, []),
            field_candidate=fields.get(target),
            anchor_hits=anchor_hits,
            page=page,
        )
        for target in _economic_targets()
    }
    _add_derived_combination_candidates(candidates_by_target)
    existing_values = {
        target: _field_decimal(field)
        for target, field in fields.items()
        if _field_decimal(field) is not None
    }
    best: _EconomicValueCombination | None = None

    for imponibile in candidates_by_target["totale_imponibile"]:
        for imposta in candidates_by_target["totale_imposta"]:
            for totale in candidates_by_target["totale_fattura"]:
                for ritenuta in candidates_by_target["ritenuta"]:
                    for netto in candidates_by_target["totale_netto_fattura"]:
                        values = {
                            "totale_imponibile": imponibile,
                            "totale_imposta": imposta,
                            "totale_fattura": totale,
                            "ritenuta": ritenuta,
                            "totale_netto_fattura": netto,
                        }
                        combination = _score_economic_value_combination(
                            values=values,
                            existing_values=existing_values,
                            percent=percent,
                        )
                        if combination is None:
                            continue
                        if best is None or combination.score > best.score:
                            best = combination

    return best


def _table_crop_value_candidates_by_target(
    page: DocumentPage,
    region_bbox: BoundingBox,
) -> dict[str, list[_EconomicValueCandidate]]:
    candidates_by_target: dict[str, list[_EconomicValueCandidate]] = {
        target: [] for target in _economic_targets()
    }
    tokens_by_source_id: dict[str, list[OcrToken]] = defaultdict(list)
    for token in page.tokens:
        if token.source != TABLE_CROP_TOKEN_SOURCE or not token.source_id:
            continue
        if not _bbox_center_inside(token.bbox, region_bbox):
            continue
        tokens_by_source_id[token.source_id].append(token)

    seen: set[tuple[str, Decimal, BoundingBox, str]] = set()
    for source_tokens in tokens_by_source_id.values():
        for value_token in _money_value_tokens_with_split_repairs(source_tokens, page):
            value_text = _clean_money_amount_text(value_token.text)
            if value_text is None:
                continue
            try:
                value = abs(_money(Decimal(_normalize_money_amount(value_text))))
            except InvalidOperation:
                continue

            context_tokens = _nearby_crop_context_tokens(
                source_tokens,
                value_token,
                page=page,
            )
            context_text = " ".join(token.text for token in context_tokens)
            target_scores = _table_crop_pair_target_scores(context_text)
            for target, anchor_score in target_scores.items():
                if anchor_score < MIN_TABLE_CROP_PAIR_SCORE:
                    continue
                confidence = _table_crop_pair_confidence(
                    token_confidence=value_token.confidence,
                    anchor_score=anchor_score,
                    context_tokens=context_tokens,
                    value_token=value_token,
                    page=page,
                )
                key = (target, value, value_token.bbox, context_text)
                if key in seen:
                    continue
                seen.add(key)
                candidates_by_target[target].append(
                    _EconomicValueCandidate(
                        text=value_text,
                        value=value,
                        bbox=value_token.bbox,
                        confidence=confidence,
                        source="table_crop_pair",
                        anchor_text=context_text,
                        anchor_score=anchor_score,
                    )
                )

    return candidates_by_target


def _money_value_tokens_with_split_repairs(
    source_tokens: list[OcrToken],
    page: DocumentPage,
) -> list[OcrToken]:
    value_tokens = list(source_tokens)
    ordered_tokens = sorted(source_tokens, key=lambda token: (token.bbox.y1, token.bbox.x1))

    for left_index, left_token in enumerate(ordered_tokens):
        left_text = left_token.text.strip()
        if _looks_like_money_amount(left_text):
            continue
        if not re.fullmatch(r"-?\d{1,3}(?:\.\d{3})*|-?\d+", left_text):
            continue

        for right_token in ordered_tokens[left_index + 1 : left_index + 6]:
            right_text = right_token.text.strip().lstrip(".,")
            if _looks_like_money_amount(right_token.text):
                continue
            if not re.fullmatch(r"\d{2}", right_text):
                continue
            if not _is_same_visual_row(left_token.bbox, right_token.bbox):
                continue
            gap = right_token.bbox.x1 - left_token.bbox.x2
            if gap < -2 or gap > max(18, int(page.width * 0.014)):
                continue

            repaired_text = f"{left_text},{right_text}"
            if _clean_money_amount_text(repaired_text) is None:
                continue

            value_tokens.append(
                OcrToken(
                    text=repaired_text,
                    bbox=BoundingBox(
                        x1=min(left_token.bbox.x1, right_token.bbox.x1),
                        y1=min(left_token.bbox.y1, right_token.bbox.y1),
                        x2=max(left_token.bbox.x2, right_token.bbox.x2),
                        y2=max(left_token.bbox.y2, right_token.bbox.y2),
                    ),
                    confidence=min(
                        left_token.confidence if left_token.confidence is not None else 0.55,
                        right_token.confidence if right_token.confidence is not None else 0.55,
                        0.78,
                    ),
                    source=left_token.source,
                    source_id=left_token.source_id,
                )
            )
            break

    return value_tokens


def _economic_combination_bbox(
    page: DocumentPage,
    region_bbox: BoundingBox,
) -> BoundingBox:
    return BoundingBox(
        x1=max(0, region_bbox.x1 - int(page.width * 0.02)),
        y1=max(0, region_bbox.y1 - int(page.height * 0.015)),
        x2=min(page.width, region_bbox.x2 + int(page.width * 0.02)),
        y2=min(page.height, region_bbox.y2 + int(page.height * 0.11)),
    )


def _nearby_crop_context_tokens(
    source_tokens: list[OcrToken],
    value_token: OcrToken,
    page: DocumentPage,
) -> list[OcrToken]:
    value_center_y = (value_token.bbox.y1 + value_token.bbox.y2) / 2
    row_tolerance = max(
        10,
        (value_token.bbox.y2 - value_token.bbox.y1) * 1.4,
        page.height * 0.006,
    )
    context_tokens = [
        token
        for token in source_tokens
        if token is not value_token
        and _clean_money_amount_text(token.text) is None
        and abs(((token.bbox.y1 + token.bbox.y2) / 2) - value_center_y) <= row_tolerance
        and token.bbox.x1 <= value_token.bbox.x2 + int(page.width * 0.03)
    ]
    if context_tokens:
        return sorted(context_tokens, key=lambda token: (token.bbox.y1, token.bbox.x1))

    fallback_tokens = [
        token
        for token in source_tokens
        if token is not value_token
        and _clean_money_amount_text(token.text) is None
        and abs(((token.bbox.y1 + token.bbox.y2) / 2) - value_center_y) <= page.height * 0.035
        and token.bbox.x1 <= value_token.bbox.x2 + int(page.width * 0.04)
    ]
    return sorted(fallback_tokens, key=lambda token: (token.bbox.y1, token.bbox.x1))[:8]


def _table_crop_pair_target_scores(context_text: str) -> dict[str, float]:
    normalized_text = _normalize_context_text(context_text)
    if not normalized_text:
        return {}

    scores: dict[str, float] = {}
    for target, anchor in _anchor_definitions():
        if _is_ambiguous_economic_anchor(target, normalized_text):
            continue
        score = _best_anchor_similarity(normalized_text, anchor)
        if score > scores.get(target, 0.0):
            scores[target] = score

    words = set(normalized_text.split())
    keyword_scores = {
        "totale_imponibile": _keyword_score(words, required={"imponibile"}, helpful={"totale", "tot"}),
        "totale_imposta": _keyword_score(words, required={"iva"}, helpful={"totale", "tot", "imposta"}),
        "totale_fattura": _keyword_score(words, required={"totale"}, helpful={"fattura", "documento", "euro"}),
        "ritenuta": _keyword_score(words, required={"ritenuta"}, helpful={"rit", "acconto"}),
        "totale_netto_fattura": _keyword_score(
            words,
            required={"pagare", "dovuto", "importo"},
            helpful={"netto", "saldo"},
        ),
    }
    for target, score in keyword_scores.items():
        if score > scores.get(target, 0.0):
            scores[target] = score

    return scores


def _keyword_score(
    words: set[str],
    required: set[str],
    helpful: set[str],
) -> float:
    if not words.intersection(required):
        return 0.0
    score = 0.46
    score += min(0.22, 0.08 * len(words.intersection(helpful)))
    return score


def _table_crop_pair_confidence(
    token_confidence: float | None,
    anchor_score: float,
    context_tokens: list[OcrToken],
    value_token: OcrToken,
    page: DocumentPage,
) -> float:
    confidence = (token_confidence if token_confidence is not None else 0.55) * 0.45
    confidence += anchor_score * 0.45
    if context_tokens and any(_is_same_visual_row(token.bbox, value_token.bbox) for token in context_tokens):
        confidence += 0.06
    center_x = (value_token.bbox.x1 + value_token.bbox.x2) / 2
    center_y = (value_token.bbox.y1 + value_token.bbox.y2) / 2
    if center_x / page.width >= 0.58:
        confidence += 0.02
    if center_y / page.height >= 0.50:
        confidence += 0.02
    return round(min(1.0, confidence), 4)


def _target_value_candidates(
    target: str,
    crop_candidates: list[_EconomicValueCandidate],
    field_candidate: EconomicSummaryFieldCandidate | None,
    anchor_hits: list[_AnchorHit],
    page: DocumentPage,
) -> list[_EconomicValueCandidate]:
    candidates = list(crop_candidates)
    if field_candidate is not None:
        value = _field_decimal(field_candidate)
        if value is not None:
            candidates.append(
                _EconomicValueCandidate(
                    text=field_candidate.value_text,
                    value=abs(_money(value)),
                    bbox=field_candidate.value_bbox,
                    confidence=1.0,
                    source="selected_field",
                )
            )

    candidates = sorted(
        _deduplicate_value_candidates(candidates),
        key=lambda candidate: _score_value_candidate_for_target(
            candidate=candidate,
            target=target,
            anchor_hits=anchor_hits,
            page=page,
        ),
        reverse=True,
    )
    return candidates[:8]


def _add_derived_combination_candidates(
    candidates_by_target: dict[str, list[_EconomicValueCandidate]],
) -> None:
    observed_totals = [
        candidate
        for candidate in candidates_by_target["totale_fattura"]
        if _is_reliable_accounting_derivation_source(candidate)
    ]
    observed_net_totals = [
        candidate
        for candidate in candidates_by_target["totale_netto_fattura"]
        if _is_reliable_accounting_derivation_source(candidate)
    ]
    for totale in observed_totals:
        for netto in observed_net_totals:
            if totale.bbox == netto.bbox:
                continue
            if netto.value > totale.value:
                continue
            withholding_value = _money(totale.value - netto.value)
            candidates_by_target["ritenuta"].append(
                _EconomicValueCandidate(
                    text=_format_decimal(withholding_value),
                    value=withholding_value,
                    bbox=_union_candidate_bbox([totale, netto]),
                    confidence=min(totale.confidence, netto.confidence, 0.68),
                    source="table_crop_calculated_totale_minus_netto",
                    anchor_text="",
                    anchor_score=0.0,
                )
            )

    for imponibile in candidates_by_target["totale_imponibile"]:
        for imposta in candidates_by_target["totale_imposta"]:
            total_value = _money(imponibile.value + imposta.value)
            candidates_by_target["totale_fattura"].append(
                _EconomicValueCandidate(
                    text=_format_decimal(total_value),
                    value=total_value,
                    bbox=_union_candidate_bbox([imponibile, imposta]),
                    confidence=min(imponibile.confidence, imposta.confidence, 0.72),
                    source="table_crop_calculated_imponibile_plus_imposta",
                    anchor_text="",
                    anchor_score=min(imponibile.anchor_score, imposta.anchor_score),
                )
            )

    reliable_totals = [
        candidate
        for candidate in candidates_by_target["totale_fattura"]
        if _is_reliable_accounting_derivation_source(candidate)
    ]
    reliable_withholdings = [
        candidate
        for candidate in candidates_by_target["ritenuta"]
        if _is_reliable_accounting_derivation_source(candidate)
    ]
    for totale in reliable_totals:
        for ritenuta in reliable_withholdings:
            if ritenuta.value >= totale.value:
                continue
            netto_value = _money(totale.value - ritenuta.value)
            candidates_by_target["totale_netto_fattura"].append(
                _EconomicValueCandidate(
                    text=_format_decimal(netto_value),
                    value=netto_value,
                    bbox=_union_candidate_bbox([totale, ritenuta]),
                    confidence=min(totale.confidence, ritenuta.confidence, 0.64),
                    source="table_crop_calculated_totale_minus_ritenuta",
                    anchor_text="",
                    anchor_score=min(totale.anchor_score, ritenuta.anchor_score),
                )
            )

    for target in _economic_targets():
        candidates_by_target[target] = sorted(
            _deduplicate_value_candidates(candidates_by_target[target]),
            key=lambda candidate: candidate.confidence,
            reverse=True,
        )[:10]


def _is_reliable_accounting_derivation_source(
    candidate: _EconomicValueCandidate,
) -> bool:
    return candidate.source == "selected_field" or candidate.source.startswith(
        "table_crop_calculated_"
    ) or (
        candidate.source == "table_crop_pair"
        and candidate.anchor_score >= MIN_ACCOUNTING_DERIVATION_ANCHOR_SCORE
    )


def _union_candidate_bbox(candidates: list[_EconomicValueCandidate]) -> BoundingBox:
    return BoundingBox(
        x1=min(candidate.bbox.x1 for candidate in candidates),
        y1=min(candidate.bbox.y1 for candidate in candidates),
        x2=max(candidate.bbox.x2 for candidate in candidates),
        y2=max(candidate.bbox.y2 for candidate in candidates),
    )


def _score_value_candidate_for_target(
    candidate: _EconomicValueCandidate,
    target: str,
    anchor_hits: list[_AnchorHit],
    page: DocumentPage,
) -> float:
    score = candidate.confidence * 40
    score += candidate.anchor_score * 55
    if candidate.source == "table_crop_pair":
        score += 18
    center_x = (candidate.bbox.x1 + candidate.bbox.x2) / 2
    center_y = (candidate.bbox.y1 + candidate.bbox.y2) / 2
    if center_x / page.width >= 0.58:
        score += 12
    if center_y / page.height >= 0.55:
        score += 10

    target_anchors = [hit for hit in anchor_hits if hit.label == target]
    if target_anchors:
        best_distance = min(
            _field_link_distance(page, hit.bbox, candidate.bbox)
            for hit in target_anchors
        )
        score += max(0, 65 - best_distance * 220)

    return score


def _score_economic_value_combination(
    values: dict[str, _EconomicValueCandidate],
    existing_values: dict[str, Decimal | None],
    percent: int | None,
) -> _EconomicValueCombination | None:
    notes = []
    score = sum(candidate.confidence * 12 for candidate in values.values())

    for target, existing_value in existing_values.items():
        if existing_value is None or target not in values:
            continue
        if _money_matches(abs(existing_value), values[target].value):
            score += 80
        else:
            score -= 120

    imponibile = values["totale_imponibile"].value
    imposta = values["totale_imposta"].value
    totale = values["totale_fattura"].value
    ritenuta = values["ritenuta"].value
    netto = values["totale_netto_fattura"].value
    if any(
        not _is_reliable_accounting_derivation_source(values[target])
        for target in (
            "totale_fattura",
            "ritenuta",
            "totale_netto_fattura",
        )
    ):
        return None
    if totale < imponibile or imposta > totale or ritenuta >= totale or netto <= 0 or netto > totale:
        return None
    if _has_reused_incompatible_value_candidate(values):
        return None

    if _money_matches(_money(imponibile + imposta), totale):
        score += 120
        notes.append("combinatorial_imponibile_plus_imposta_matches_totale")
    else:
        score -= 40

    if not _money_matches(_money(totale - ritenuta), netto):
        return None
    score += 140
    notes.append("combinatorial_totale_minus_ritenuta_matches_netto")

    if percent is not None and _money_matches(
        _ritenuta_from_imponibile(imponibile, percent),
        ritenuta,
    ):
        score += 120
        notes.append("combinatorial_ritenuta_matches_imponibile_percent")

    if _values_are_column_aligned(values):
        score += 30
        notes.append("combinatorial_values_column_aligned")

    if score < 260:
        return None

    return _EconomicValueCombination(values=values, score=round(score, 2), notes=tuple(notes))


def _has_reused_incompatible_value_candidate(
    values: dict[str, _EconomicValueCandidate],
) -> bool:
    incompatible_pairs = [
        ("totale_fattura", "ritenuta"),
        ("totale_fattura", "totale_netto_fattura"),
        ("ritenuta", "totale_netto_fattura"),
        ("totale_imponibile", "ritenuta"),
        ("totale_imponibile", "totale_fattura"),
    ]
    for left, right in incompatible_pairs:
        left_candidate = values[left]
        right_candidate = values[right]
        if left_candidate.value == right_candidate.value and left_candidate.bbox == right_candidate.bbox:
            return True

    return False


def _values_are_column_aligned(values: dict[str, _EconomicValueCandidate]) -> bool:
    x2_values = [candidate.bbox.x2 for candidate in values.values()]
    return max(x2_values) - min(x2_values) <= 180


def _deduplicate_value_candidates(
    candidates: list[_EconomicValueCandidate],
) -> list[_EconomicValueCandidate]:
    best_by_value_and_box: dict[tuple[Decimal, BoundingBox], _EconomicValueCandidate] = {}
    for candidate in candidates:
        key = (candidate.value, candidate.bbox)
        current = best_by_value_and_box.get(key)
        if current is None or candidate.confidence > current.confidence:
            best_by_value_and_box[key] = candidate

    return list(best_by_value_and_box.values())


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
    anchor_hits: list[_AnchorHit],
    amount_tokens: list[OcrToken],
) -> OcrToken | None:
    column_bounds = _economic_column_bounds(anchor_hit, anchor_hits)
    candidate_tokens = [
        token
        for token in amount_tokens
        if token.bbox.x1 >= anchor_hit.bbox.x1
        and token.bbox.y1 >= anchor_hit.bbox.y1 - max(6, int(page.height * 0.004))
        and not _bbox_center_inside(token.bbox, anchor_hit.bbox)
        and _bbox_center_x_inside(token.bbox, column_bounds)
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


def _economic_column_bounds(
    anchor_hit: _AnchorHit,
    anchor_hits: list[_AnchorHit],
) -> tuple[float | None, float | None]:
    """Infer local column ownership from aligned, non-overlapping labels."""
    aligned_hits = [
        hit
        for hit in anchor_hits
        if hit.bbox != anchor_hit.bbox
        and _is_same_visual_row(anchor_hit.bbox, hit.bbox)
        and (
            hit.bbox.x2 <= anchor_hit.bbox.x1
            or hit.bbox.x1 >= anchor_hit.bbox.x2
        )
    ]
    if not aligned_hits:
        return (None, None)

    anchor_center = _bbox_center_x(anchor_hit.bbox)
    centers = sorted({_bbox_center_x(hit.bbox) for hit in aligned_hits})
    left_centers = [center for center in centers if center < anchor_center]
    right_centers = [center for center in centers if center > anchor_center]
    left_bound = (
        (max(left_centers) + anchor_center) / 2 if left_centers else None
    )
    right_bound = (
        (anchor_center + min(right_centers)) / 2 if right_centers else None
    )
    return (left_bound, right_bound)


def _bbox_center_x(bbox: BoundingBox) -> float:
    return (bbox.x1 + bbox.x2) / 2


def _bbox_center_x_inside(
    bbox: BoundingBox,
    bounds: tuple[float | None, float | None],
) -> bool:
    center_x = _bbox_center_x(bbox)
    left_bound, right_bound = bounds
    return (
        (left_bound is None or center_x >= left_bound)
        and (right_bound is None or center_x < right_bound)
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
        ("totale_fattura", "totale iva inclusa"),
        ("totale_fattura", "totale fattura euro"),
        ("totale_fattura", "totale fattura"),
        ("totale_fattura", "tot fattura"),
        ("totale_fattura", "totale documento"),
        ("totale_fattura", "tot documento"),
        ("ritenuta", "ritenuta"),
        ("ritenuta", "ritenuta 4"),
        ("ritenuta", "ritenuta 10"),
        ("ritenuta", "ra"),
        ("ritenuta", "rit acconto"),
        ("ritenuta", "rit acconto 4"),
        ("ritenuta", "rit acconto 10"),
        ("totale_netto_fattura", "totale netto fattura euro"),
        ("totale_netto_fattura", "totale netto fattura"),
        ("totale_netto_fattura", "netto fattura"),
        ("totale_netto_fattura", "netto a pagare"),
        ("totale_netto_fattura", "importo da pagare"),
        ("totale_netto_fattura", "importo scadenza"),
        ("totale_netto_fattura", "totale dovuto"),
        ("totale_netto_fattura", "tot dovuto"),
    ]


def _is_ambiguous_economic_anchor(label: str, normalized_text: str) -> bool:
    words = normalized_text.split()
    if (
        label == "totale_imposta"
        and "iva" in words
        and "inclusa" in words
        and ("totale" in words or "tot" in words)
    ):
        return True

    if label == "totale_imposta" and "imponibile" in words:
        return True

    if label == "totale_imponibile" and "imposta" in words:
        return True

    if label == "totale_fattura" and "documento" in words:
        if "tipo" in words or "numero" in words:
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
