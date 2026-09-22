from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
import re

from estrattore.condominium_extraction import (
    find_condominium_candidates,
    is_civic_number_inside_condominium,
)
from estrattore.fiscal_validation import normalize_italian_tax_code
from estrattore.invoice_date_extraction import normalize_invoice_date
from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken


MIN_FUZZY_ANCHOR_SIMILARITY = 0.88
SHORT_ANCHOR_MAX_LEN = 4
MIN_SELECTED_SCORE = 120.0
MIN_CANDIDATE_TOKEN_HEIGHT = 4
MIN_CANDIDATE_TOKEN_WIDTH = 4
MIN_CANDIDATE_TOKEN_AREA = 24
BASE_CANDIDATE_SCORE = 50.0


@dataclass(frozen=True)
class InvoiceNumberCandidate:
    page: int
    raw_text: str
    normalized_value: str
    format_kind: str
    bbox: BoundingBox
    score: float
    selected_as_numero_fattura: bool
    anchor_context: str
    anchor_quality: int
    date_context: str
    excluded_reason: str
    nearby_text: str


@dataclass(frozen=True)
class _AnchorMatch:
    label: str
    score: float
    exact: bool
    distance_n: float
    spatial_relation: str
    geometry_score: float


@dataclass(frozen=True)
class _NearbyWord:
    word: str
    raw_text: str
    distance_n: float
    y1: int
    x1: int
    y2: int
    x2: int


@dataclass(frozen=True)
class _NearbyToken:
    token: OcrToken
    distance_n: float


def find_invoice_number_candidates(
    document: DocumentRepresentation,
) -> list[InvoiceNumberCandidate]:
    candidates = []
    condominium_candidates = find_condominium_candidates(document)

    for page in document.pages:
        for token in page.tokens:
            normalized_value = _normalize_invoice_number(token.text)
            if normalized_value is None:
                continue

            if _has_implausible_invoice_number_bbox(token):
                continue

            format_kind = _invoice_number_format_kind(normalized_value)
            if format_kind is None:
                continue

            nearby_tokens = _nearby_tokens(page, token)
            anchor_matches = _invoice_number_anchor_matches(token, nearby_tokens)
            anchor_quality = _anchor_match_quality(anchor_matches)
            date_context = _invoice_date_context(nearby_tokens)
            excluded_reason = _excluded_reason(
                page=page,
                token=token,
                normalized_value=normalized_value,
                nearby_tokens=nearby_tokens,
                condominium_candidates=condominium_candidates,
            )
            candidates.append(
                InvoiceNumberCandidate(
                    page=page.page_number,
                    raw_text=token.text,
                    normalized_value=normalized_value,
                    format_kind=format_kind,
                    bbox=token.bbox,
                    score=_score_invoice_number(
                        anchor_matches,
                        date_context=date_context,
                    ),
                    selected_as_numero_fattura=False,
                    anchor_context=_format_anchor_matches(anchor_matches),
                    anchor_quality=anchor_quality,
                    date_context=date_context,
                    excluded_reason=excluded_reason,
                    nearby_text=" ".join(
                        nearby.token.text for nearby in nearby_tokens
                    ),
                )
            )

    return _mark_selected_targets(candidates)


def select_invoice_number_target_per_page(
    candidates: list[InvoiceNumberCandidate],
    min_score: float = MIN_SELECTED_SCORE,
) -> dict[int, InvoiceNumberCandidate]:
    selected: dict[int, InvoiceNumberCandidate] = {}

    for page in sorted({candidate.page for candidate in candidates}):
        eligible_page_candidates = [
            candidate
            for candidate in candidates
            if candidate.page == page
            and candidate.score >= min_score
            and candidate.excluded_reason == ""
        ]
        dominated_fragments, composition_evidence = _fragment_composition_evidence(
            eligible_page_candidates
        )
        page_candidates = [
            candidate
            for candidate in eligible_page_candidates
            if candidate not in dominated_fragments
        ]
        if not page_candidates:
            continue

        selected[page] = max(
            page_candidates,
            key=lambda candidate: (
                composition_evidence.get(
                    candidate,
                    (candidate.anchor_quality, candidate.score),
                ),
                candidate.anchor_quality,
                candidate.score,
                -candidate.bbox.y1,
                -candidate.bbox.x1,
            ),
        )

    return selected


def _fragment_composition_evidence(
    candidates: list[InvoiceNumberCandidate],
) -> tuple[
    set[InvoiceNumberCandidate],
    dict[InvoiceNumberCandidate, tuple[int, float]],
]:
    dominated: set[InvoiceNumberCandidate] = set()
    evidence: dict[InvoiceNumberCandidate, tuple[int, float]] = {}

    for complete in candidates:
        fragments = [
            candidate
            for candidate in candidates
            if candidate != complete
            and _is_strictly_inside_candidate(candidate, complete)
        ]
        for fragment_count in range(2, min(4, len(fragments)) + 1):
            for fragment_group in combinations(fragments, fragment_count):
                ordered = sorted(fragment_group, key=lambda candidate: candidate.bbox.x1)
                if not _fragments_reconstruct_complete_candidate(ordered, complete):
                    continue
                dominated.update(ordered)
                evidence[complete] = max(
                    evidence.get(
                        complete,
                        (complete.anchor_quality, complete.score),
                    ),
                    *(
                        (fragment.anchor_quality, fragment.score)
                        for fragment in ordered
                    ),
                )
                break
            else:
                continue
            break

    return dominated, evidence


def _is_strictly_inside_candidate(
    fragment: InvoiceNumberCandidate,
    complete: InvoiceNumberCandidate,
) -> bool:
    fragment_width = fragment.bbox.x2 - fragment.bbox.x1
    complete_width = complete.bbox.x2 - complete.bbox.x1
    if fragment_width >= complete_width:
        return False

    tolerance = max(2, round((complete.bbox.y2 - complete.bbox.y1) * 0.15))
    return (
        fragment.bbox.x1 >= complete.bbox.x1 - tolerance
        and fragment.bbox.x2 <= complete.bbox.x2 + tolerance
        and fragment.bbox.y1 >= complete.bbox.y1 - tolerance
        and fragment.bbox.y2 <= complete.bbox.y2 + tolerance
    )


def _fragments_reconstruct_complete_candidate(
    fragments: list[InvoiceNumberCandidate],
    complete: InvoiceNumberCandidate,
) -> bool:
    if "".join(fragment.normalized_value for fragment in fragments) != complete.normalized_value:
        return False

    complete_height = max(1, complete.bbox.y2 - complete.bbox.y1)
    edge_tolerance = max(3, round(complete_height * 0.25))
    if (
        abs(fragments[0].bbox.x1 - complete.bbox.x1) > edge_tolerance
        or abs(fragments[-1].bbox.x2 - complete.bbox.x2) > edge_tolerance
    ):
        return False

    max_gap = max(4, round(complete_height * 0.5))
    for left, right in zip(fragments, fragments[1:]):
        gap = right.bbox.x1 - left.bbox.x2
        if gap < -edge_tolerance or gap > max_gap:
            return False

        left_center_y = (left.bbox.y1 + left.bbox.y2) / 2
        right_center_y = (right.bbox.y1 + right.bbox.y2) / 2
        if abs(left_center_y - right_center_y) > complete_height * 0.35:
            return False

    return True


def save_invoice_number_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_invoice_number_candidates(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "raw_text",
                "normalized_value",
                "format_kind",
                "score",
                "selected_as_numero_fattura",
                "anchor_context",
                "date_context",
                "excluded_reason",
                "nearby_text",
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
                    "raw_text": candidate.raw_text,
                    "normalized_value": candidate.normalized_value,
                    "format_kind": candidate.format_kind,
                    "score": candidate.score,
                    "selected_as_numero_fattura": (
                        candidate.selected_as_numero_fattura
                    ),
                    "anchor_context": candidate.anchor_context,
                    "date_context": candidate.date_context,
                    "excluded_reason": candidate.excluded_reason,
                    "nearby_text": candidate.nearby_text,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


def _mark_selected_targets(
    candidates: list[InvoiceNumberCandidate],
) -> list[InvoiceNumberCandidate]:
    selected = select_invoice_number_target_per_page(candidates)
    marked_candidates = []

    for candidate in candidates:
        marked_candidates.append(
            InvoiceNumberCandidate(
                page=candidate.page,
                raw_text=candidate.raw_text,
                normalized_value=candidate.normalized_value,
                format_kind=candidate.format_kind,
                bbox=candidate.bbox,
                score=candidate.score,
                selected_as_numero_fattura=selected.get(candidate.page) == candidate,
                anchor_context=candidate.anchor_context,
                anchor_quality=candidate.anchor_quality,
                date_context=candidate.date_context,
                excluded_reason=candidate.excluded_reason,
                nearby_text=candidate.nearby_text,
            )
        )

    return marked_candidates


def _normalize_invoice_number(text: str) -> str | None:
    normalized = text.strip().upper()
    normalized = normalized.replace("\\", "/")
    normalized = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", normalized)
    normalized = normalized.replace(" ", "")
    normalized = re.sub(r"^(?:N|NR|NO|NUM|NUMERO)[.°:/-]+", "", normalized)

    if not normalized:
        return None

    return normalized


def _invoice_number_format_kind(value: str) -> str | None:
    if _looks_like_fiscal_identifier(value):
        return None

    if _looks_like_amount(value) or _looks_like_date(value):
        return None

    if re.fullmatch(r"\d{1,6}/\d{2,4}", value):
        return "slash_progressivo"

    if re.fullmatch(r"\d{1,6}/[A-Z]{1,3}", value):
        return "slash_alfanumerico"

    if re.fullmatch(r"\d{1,6}", value):
        return "progressivo_numerico"

    if (
        re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{4,24}", value)
        and any(char.isdigit() for char in value)
        and any(char.isalpha() for char in value)
    ):
        return "alfanumerico"

    return None


def _looks_like_fiscal_identifier(value: str) -> bool:
    normalized_tax_code = normalize_italian_tax_code(value)
    return normalized_tax_code is not None and normalized_tax_code == value


def _looks_like_amount(value: str) -> bool:
    return re.fullmatch(r"\d+[,.]\d{2}", value) is not None


def _looks_like_date(value: str) -> bool:
    return (
        re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", value) is not None
        or re.fullmatch(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", value) is not None
    )


def _score_invoice_number(
    anchor_matches: list[_AnchorMatch],
    date_context: str,
) -> float:
    date_bonus = 24.0 if date_context else 0.0
    if not anchor_matches:
        return BASE_CANDIDATE_SCORE + date_bonus

    anchor_score = min(sum(match.score for match in anchor_matches), 135.0)
    return round(BASE_CANDIDATE_SCORE + anchor_score + date_bonus, 2)


def _has_implausible_invoice_number_bbox(token: OcrToken) -> bool:
    width = token.bbox.x2 - token.bbox.x1
    height = token.bbox.y2 - token.bbox.y1
    return (
        width < MIN_CANDIDATE_TOKEN_WIDTH
        or height < MIN_CANDIDATE_TOKEN_HEIGHT
        or width * height < MIN_CANDIDATE_TOKEN_AREA
    )


def _format_anchor_matches(matches: list[_AnchorMatch]) -> str:
    return "|".join(match.label for match in matches)


def _excluded_reason(
    page: DocumentPage,
    token: OcrToken,
    normalized_value: str,
    nearby_tokens: list["_NearbyToken"],
    condominium_candidates: list,
) -> str:
    if is_civic_number_inside_condominium(
        candidate_value=normalized_value,
        candidate_bbox=token.bbox,
        page=page.page_number,
        condominium_candidates=condominium_candidates,
    ):
        return "numero_civico_in_span_condominio"

    if _looks_like_date_suffix_fragment(normalized_value, nearby_tokens):
        return "frammento_data"

    return ""


def _looks_like_date_suffix_fragment(
    value: str,
    nearby_tokens: list["_NearbyToken"],
) -> bool:
    if re.fullmatch(r"\d{1,2}/\d{4}", value) is None:
        return False

    for nearby in nearby_tokens:
        normalized_date = normalize_invoice_date(nearby.token.text)
        if not normalized_date:
            continue

        year, month, _day = normalized_date.split("-")
        if value == f"{int(month)}/{year}" or value == f"{month}/{year}":
            return True

    return False


def _invoice_date_context(nearby_tokens: list["_NearbyToken"]) -> str:
    has_date = any(normalize_invoice_date(nearby.token.text) for nearby in nearby_tokens)
    if not has_date:
        return ""

    nearby_text = _normalize_context_text(
        " ".join(nearby.token.text for nearby in nearby_tokens)
    )
    nearby_words = nearby_text.split()
    has_fattura = "fattura" in nearby_words
    has_data = "data" in nearby_words or "del" in nearby_words
    if has_fattura and has_data:
        return "fattura_data"

    if has_data:
        return "data"

    if has_fattura:
        return "fattura"

    return ""


def _nearby_tokens(
    page: DocumentPage,
    token: OcrToken,
    x_radius: float | None = None,
    y_radius: float | None = None,
    max_distance_n: float = 0.28,
    max_tokens: int = 32,
) -> list[_NearbyToken]:
    x_radius = x_radius if x_radius is not None else page.width * 0.38
    y_radius = y_radius if y_radius is not None else page.height * 0.08
    token_center_x = (token.bbox.x1 + token.bbox.x2) / 2
    token_center_y = (token.bbox.y1 + token.bbox.y2) / 2
    nearby: list[_NearbyToken] = []

    for other in page.tokens:
        if other is token:
            continue

        other_center_x = (other.bbox.x1 + other.bbox.x2) / 2
        other_center_y = (other.bbox.y1 + other.bbox.y2) / 2

        is_near_x = abs(other_center_x - token_center_x) <= x_radius
        is_near_y = abs(other_center_y - token_center_y) <= y_radius
        if is_near_x and is_near_y:
            distance = _normalized_distance(
                page=page,
                first_x=token_center_x,
                first_y=token_center_y,
                second_x=other_center_x,
                second_y=other_center_y,
            )
            if distance <= max_distance_n:
                nearby.append(_NearbyToken(token=other, distance_n=distance))

    closest_tokens = sorted(
        nearby,
        key=lambda nearby_token: nearby_token.distance_n,
    )[:max_tokens]

    return sorted(
        closest_tokens,
        key=lambda item: (item.token.bbox.y1, item.token.bbox.x1),
    )


def _normalized_distance(
    page: DocumentPage,
    first_x: float,
    first_y: float,
    second_x: float,
    second_y: float,
) -> float:
    x_distance = abs(first_x - second_x) / page.width
    y_distance = abs(first_y - second_y) / page.height
    return (x_distance**2 + y_distance**2) ** 0.5


def _invoice_number_anchor_matches(
    candidate_token: OcrToken,
    nearby_tokens: list[_NearbyToken],
) -> list[_AnchorMatch]:
    nearby_words = _nearby_words(nearby_tokens)
    matches = _anchor_matches(
        candidate_token=candidate_token,
        nearby_words=nearby_words,
        anchors=[
            "fattura numero",
            "numero fattura",
            "fattura n",
            "n fattura",
            "nr fattura",
            "fattura nr",
            "numero documento",
            "documento numero",
            "documento n",
            "n documento",
            "numero doc",
            "doc numero",
            "doc n",
            "n doc",
            "numero",
            "nr",
            "n",
            "fattura",
        ],
    )
    return _deduplicate_anchor_matches(matches)


def _nearby_words(nearby_tokens: list[_NearbyToken]) -> list[_NearbyWord]:
    words = []

    for nearby in nearby_tokens:
        normalized_words = _normalize_context_text(nearby.token.text).split()
        for word in normalized_words:
            words.append(
                _NearbyWord(
                    word=word,
                    raw_text=nearby.token.text,
                    distance_n=nearby.distance_n,
                    y1=nearby.token.bbox.y1,
                    x1=nearby.token.bbox.x1,
                    y2=nearby.token.bbox.y2,
                    x2=nearby.token.bbox.x2,
                )
            )

    return sorted(words, key=lambda word: (word.y1, word.x1))


def _normalize_context_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def _anchor_matches(
    candidate_token: OcrToken,
    nearby_words: list[_NearbyWord],
    anchors: list[str],
) -> list[_AnchorMatch]:
    matches = []

    for anchor in anchors:
        (
            best_similarity,
            exact,
            distance,
            spatial_relation,
            geometry_score,
        ) = _best_anchor_similarity(candidate_token, nearby_words, anchor)
        if best_similarity >= _min_similarity_for_anchor(anchor):
            proximity_score = _proximity_adjusted_score(
                anchor=anchor,
                similarity=best_similarity,
                exact=exact,
                geometry_score=geometry_score,
            )
            if proximity_score <= 0:
                continue
            matches.append(
                _AnchorMatch(
                    label=anchor,
                    score=round(proximity_score, 2),
                    exact=exact,
                    distance_n=distance,
                    spatial_relation=spatial_relation,
                    geometry_score=geometry_score,
                )
            )

    return matches


def _anchor_match_quality(matches: list[_AnchorMatch]) -> int:
    if not matches:
        return 0

    best_quality = 0
    for match in matches:
        word_count = len(match.label.split())
        is_specific_anchor = word_count >= 2
        if match.exact and is_specific_anchor:
            best_quality = max(best_quality, 4)
        elif is_specific_anchor:
            best_quality = max(best_quality, 2)
        elif match.exact:
            best_quality = max(best_quality, 1)

    return best_quality


def _best_anchor_similarity(
    candidate_token: OcrToken,
    nearby_words: list[_NearbyWord],
    anchor: str,
) -> tuple[float, bool, float, str, float]:
    anchor_words = anchor.split()
    if len(nearby_words) < len(anchor_words):
        return 0.0, False, 1.0, "", 0.0

    best_similarity = 0.0
    best_exact = False
    best_distance = 1.0
    best_spatial_relation = ""
    best_geometry_score = 0.0
    for index in range(len(nearby_words) - len(anchor_words) + 1):
        window_words = nearby_words[index : index + len(anchor_words)]
        window = " ".join(word.word for word in window_words)
        similarity = SequenceMatcher(None, window, anchor).ratio()
        distance = min(word.distance_n for word in window_words)
        spatial_relation, geometry_score = _anchor_geometry(
            candidate_token,
            window_words,
            distance_n=distance,
        )
        exact = window == anchor
        if (exact, similarity, geometry_score, -distance) > (
            best_exact,
            best_similarity,
            best_geometry_score,
            -best_distance,
        ):
            best_similarity = similarity
            best_exact = exact
            best_distance = distance
            best_spatial_relation = spatial_relation
            best_geometry_score = geometry_score

    return (
        best_similarity,
        best_exact,
        best_distance,
        best_spatial_relation,
        best_geometry_score,
    )


def _anchor_geometry(
    candidate_token: OcrToken,
    anchor_words: list[_NearbyWord],
    distance_n: float,
) -> tuple[str, float]:
    if not anchor_words:
        return "", 0.0

    anchor_x1 = min(word.x1 for word in anchor_words)
    anchor_y1 = min(word.y1 for word in anchor_words)
    anchor_x2 = max(word.x2 for word in anchor_words)
    anchor_y2 = max(word.y2 for word in anchor_words)
    candidate = candidate_token.bbox

    anchor_height = max(1, anchor_y2 - anchor_y1)
    candidate_height = max(1, candidate.y2 - candidate.y1)
    anchor_center_x = (anchor_x1 + anchor_x2) / 2
    anchor_center_y = (anchor_y1 + anchor_y2) / 2
    candidate_center_x = (candidate.x1 + candidate.x2) / 2
    candidate_center_y = (candidate.y1 + candidate.y2) / 2

    vertical_projection_gap = _interval_gap(
        anchor_y1,
        anchor_y2,
        candidate.y1,
        candidate.y2,
    )
    row_tolerance = max(3.0, min(anchor_height, candidate_height) * 0.35)
    same_row = vertical_projection_gap <= row_tolerance

    if same_row and candidate_center_x >= anchor_center_x:
        relation = "same_row_after"
        axis_alignment = _projection_alignment(
            anchor_y1,
            anchor_y2,
            candidate.y1,
            candidate.y2,
        )
        direction_score = 1.0
        alignment_evidence = axis_alignment
    elif same_row:
        relation = "same_row_before"
        axis_alignment = _projection_alignment(
            anchor_y1,
            anchor_y2,
            candidate.y1,
            candidate.y2,
        )
        direction_score = 0.15
        alignment_evidence = axis_alignment * 0.15
    elif candidate_center_y > anchor_center_y:
        relation = "below"
        axis_alignment = _projection_alignment(
            anchor_x1,
            anchor_x2,
            candidate.x1,
            candidate.x2,
        )
        direction_score = 0.9
        alignment_evidence = 0.2 + axis_alignment * 0.8
    else:
        relation = "above"
        axis_alignment = _projection_alignment(
            anchor_x1,
            anchor_x2,
            candidate.x1,
            candidate.x2,
        )
        direction_score = 0.1
        alignment_evidence = axis_alignment * 0.15

    proximity_score = max(0.0, 1.0 - min(distance_n / 0.28, 1.0))
    phrase_coherence = _anchor_phrase_row_coherence(anchor_words)
    if relation in {"same_row_after", "below"}:
        geometry_score = (
            0.08
            + proximity_score * 0.25
            + alignment_evidence * 0.52
            + direction_score * 0.15
        )
    else:
        geometry_score = (
            0.03
            + proximity_score * 0.10
            + alignment_evidence * 0.20
            + direction_score * 0.05
        )
    geometry_score *= 0.35 + phrase_coherence * 0.65
    return relation, round(min(max(geometry_score, 0.0), 1.0), 4)


def _projection_alignment(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> float:
    gap = _interval_gap(first_start, first_end, second_start, second_end)
    first_size = max(1, first_end - first_start)
    second_size = max(1, second_end - second_start)
    scale = max(4.0, min(first_size, second_size) * 0.5)
    return max(0.0, 1.0 - gap / (scale * 2.0))


def _interval_gap(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> float:
    return float(max(first_start - second_end, second_start - first_end, 0))


def _anchor_phrase_row_coherence(words: list[_NearbyWord]) -> float:
    if len(words) <= 1:
        return 1.0

    heights = [max(1, word.y2 - word.y1) for word in words]
    centers_y = [(word.y1 + word.y2) / 2 for word in words]
    spread = max(centers_y) - min(centers_y)
    scale = max(4.0, max(heights) * 2.0)
    return max(0.0, 1.0 - spread / scale)


def _min_similarity_for_anchor(anchor: str) -> float:
    if any(len(word) <= 2 for word in anchor.split()):
        return 1.0

    compact_anchor = anchor.replace(" ", "")
    if len(compact_anchor) <= SHORT_ANCHOR_MAX_LEN:
        return 1.0

    return MIN_FUZZY_ANCHOR_SIMILARITY


def _proximity_adjusted_score(
    anchor: str,
    similarity: float,
    exact: bool,
    geometry_score: float,
) -> float:
    anchor_weight = _anchor_weight(anchor)
    exact_bonus = 12.0 if exact else 0.0
    return anchor_weight * similarity * geometry_score + exact_bonus


def _anchor_weight(anchor: str) -> float:
    if anchor == "fattura":
        return 66.0

    if anchor in {"n", "nr"}:
        return 45.0

    if anchor == "numero":
        return 65.0

    return 100.0


def _deduplicate_anchor_matches(matches: list[_AnchorMatch]) -> list[_AnchorMatch]:
    best_by_label = {}
    for match in matches:
        current = best_by_label.get(match.label)
        if current is None or (match.exact, match.score) > (
            current.exact,
            current.score,
        ):
            best_by_label[match.label] = match

    best_matches = list(best_by_label.values())
    compound_words = set()
    for match in best_matches:
        words = set(match.label.split())
        if len(words) > 1:
            compound_words.update(words)

    return [
        match
        for match in best_matches
        if not (match.label in {"fattura", "numero", "nr", "n"} and match.label in compound_words)
    ]
