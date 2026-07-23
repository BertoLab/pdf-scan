from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
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
    date_context: str
    excluded_reason: str
    nearby_text: str


@dataclass(frozen=True)
class _AnchorMatch:
    label: str
    score: float
    exact: bool
    distance_n: float


@dataclass(frozen=True)
class _NearbyWord:
    word: str
    raw_text: str
    distance_n: float
    y1: int
    x1: int


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
                        format_kind,
                        anchor_matches,
                        date_context=date_context,
                    ),
                    selected_as_numero_fattura=False,
                    anchor_context=_format_anchor_matches(anchor_matches),
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
        page_candidates = [
            candidate
            for candidate in candidates
            if candidate.page == page and candidate.score >= min_score
            and candidate.excluded_reason == ""
        ]
        if not page_candidates:
            continue

        selected[page] = max(
            page_candidates,
            key=lambda candidate: (
                candidate.score,
                -candidate.bbox.y1,
                -candidate.bbox.x1,
            ),
        )

    return selected


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
    format_kind: str,
    anchor_matches: list[_AnchorMatch],
    date_context: str,
) -> float:
    date_bonus = 24.0 if date_context else 0.0
    if not anchor_matches:
        return _format_score(format_kind) + date_bonus

    anchor_score = min(sum(match.score for match in anchor_matches), 135.0)
    return round(_format_score(format_kind) + anchor_score + date_bonus, 2)


def _format_score(format_kind: str) -> float:
    if format_kind == "slash_progressivo":
        return 70.0

    if format_kind == "slash_alfanumerico":
        return 75.0

    if format_kind == "progressivo_numerico":
        return 35.0

    return 60.0


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
        best_similarity, exact, distance, anchor_is_before_candidate = _best_anchor_similarity(
            candidate_token,
            nearby_words,
            anchor,
        )
        if best_similarity >= _min_similarity_for_anchor(anchor):
            proximity_score = _proximity_adjusted_score(
                anchor=anchor,
                similarity=best_similarity,
                distance_n=distance,
                anchor_is_before_candidate=anchor_is_before_candidate,
            )
            if proximity_score <= 0:
                continue
            matches.append(
                _AnchorMatch(
                    label=anchor,
                    score=round(proximity_score, 2),
                    exact=exact,
                    distance_n=distance,
                )
            )

    return matches


def _best_anchor_similarity(
    candidate_token: OcrToken,
    nearby_words: list[_NearbyWord],
    anchor: str,
) -> tuple[float, bool, float, bool]:
    anchor_words = anchor.split()
    if len(nearby_words) < len(anchor_words):
        return 0.0, False, 1.0, False

    best_similarity = 0.0
    best_exact = False
    best_distance = 1.0
    best_anchor_is_before_candidate = False
    for index in range(len(nearby_words) - len(anchor_words) + 1):
        window_words = nearby_words[index : index + len(anchor_words)]
        window = " ".join(word.word for word in window_words)
        similarity = SequenceMatcher(None, window, anchor).ratio()
        distance = min(word.distance_n for word in window_words)
        exact = window == anchor
        candidate_height = candidate_token.bbox.y2 - candidate_token.bbox.y1
        same_visual_line = abs(
            min(word.y1 for word in window_words) - candidate_token.bbox.y1
        ) <= max(28, candidate_height * 1.25)
        anchor_is_before_candidate = (
            max(word.x1 for word in window_words) < candidate_token.bbox.x1
            and same_visual_line
        )
        if (similarity, exact, -distance) > (
            best_similarity,
            best_exact,
            -best_distance,
        ):
            best_similarity = similarity
            best_exact = exact
            best_distance = distance
            best_anchor_is_before_candidate = anchor_is_before_candidate

    return best_similarity, best_exact, best_distance, best_anchor_is_before_candidate


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
    distance_n: float,
    anchor_is_before_candidate: bool,
) -> float:
    if anchor in {"n", "nr", "numero"} and not anchor_is_before_candidate:
        return 0.0

    if anchor == "fattura" and not _is_usable_single_fattura_anchor(
        distance_n=distance_n,
        anchor_is_before_candidate=anchor_is_before_candidate,
    ):
        return 0.0

    max_distance_n = 0.28
    distance_factor = max(0.55, 1 - min(distance_n / max_distance_n, 1) * 0.45)
    anchor_weight = _anchor_weight(anchor)
    reading_order_bonus = 18.0 if anchor_is_before_candidate and anchor not in {"n", "nr"} else 0.0
    return anchor_weight * similarity * distance_factor + reading_order_bonus


def _is_usable_single_fattura_anchor(
    distance_n: float,
    anchor_is_before_candidate: bool,
) -> bool:
    return anchor_is_before_candidate or distance_n <= 0.035


def _anchor_weight(anchor: str) -> float:
    if anchor == "fattura":
        return 60.0

    if anchor in {"n", "nr"}:
        return 45.0

    if anchor == "numero":
        return 65.0

    return 100.0


def _deduplicate_anchor_matches(matches: list[_AnchorMatch]) -> list[_AnchorMatch]:
    best_by_label = {}
    for match in matches:
        current = best_by_label.get(match.label)
        if current is None or (match.score, match.exact) > (
            current.score,
            current.exact,
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
