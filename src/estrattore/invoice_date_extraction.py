from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
import re

from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken


MIN_FUZZY_ANCHOR_SIMILARITY = 0.88
MIN_SELECTED_SCORE = 120.0


@dataclass(frozen=True)
class InvoiceDateCandidate:
    page: int
    raw_text: str
    normalized_value: str
    bbox: BoundingBox
    score: float
    selected_as_data_fattura: bool
    anchor_context: str
    negative_context: str
    nearby_text: str


@dataclass(frozen=True)
class _AnchorMatch:
    label: str
    score: float
    exact: bool
    distance_n: float


@dataclass(frozen=True)
class _NearbyToken:
    token: OcrToken
    distance_n: float


def find_invoice_date_candidates(
    document: DocumentRepresentation,
) -> list[InvoiceDateCandidate]:
    candidates = []

    for page in document.pages:
        for token in page.tokens:
            row_tokens = _same_row_tokens(page, token)
            normalized_value = normalize_invoice_date(token.text)
            if normalized_value is None:
                normalized_value = _normalize_compact_issue_date(
                    token.text,
                    row_tokens,
                )
            if normalized_value is None:
                continue

            nearby_tokens = _nearby_tokens(page, token)
            row_anchor_matches = _invoice_date_anchor_matches(row_tokens)
            anchor_matches = row_anchor_matches or _invoice_date_anchor_matches(
                nearby_tokens
            )
            negative_context = _negative_date_context(row_tokens)
            candidates.append(
                InvoiceDateCandidate(
                    page=page.page_number,
                    raw_text=token.text,
                    normalized_value=normalized_value,
                    bbox=token.bbox,
                    score=_score_invoice_date(anchor_matches, negative_context),
                    selected_as_data_fattura=False,
                    anchor_context=_format_anchor_matches(anchor_matches),
                    negative_context=negative_context,
                    nearby_text=" ".join(
                        nearby.token.text for nearby in nearby_tokens
                    ),
                )
            )

    return _mark_selected_targets(candidates)


def select_invoice_date_target_per_page(
    candidates: list[InvoiceDateCandidate],
    min_score: float = MIN_SELECTED_SCORE,
) -> dict[int, InvoiceDateCandidate]:
    selected: dict[int, InvoiceDateCandidate] = {}

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
                -candidate.bbox.y1,
                -candidate.bbox.x1,
            ),
        )

    return selected


def save_invoice_date_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_invoice_date_candidates(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "raw_text",
                "normalized_value",
                "score",
                "selected_as_data_fattura",
                "anchor_context",
                "negative_context",
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
                    "score": candidate.score,
                    "selected_as_data_fattura": candidate.selected_as_data_fattura,
                    "anchor_context": candidate.anchor_context,
                    "negative_context": candidate.negative_context,
                    "nearby_text": candidate.nearby_text,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


def normalize_invoice_date(text: str) -> str | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^[^0-9]+|[^0-9]+$", "", cleaned)
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})", cleaned)
    if match is None:
        return None

    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    if year < 100:
        year += 2000

    try:
        parsed = date(year, month, day)
    except ValueError:
        return None

    return parsed.isoformat()


def _normalize_compact_issue_date(
    text: str,
    row_tokens: list[_NearbyToken],
) -> str | None:
    """Recover DDMM-YYYY only when its row explicitly identifies an issue date."""
    row_words = _normalized_context_words(row_tokens)
    if not _has_issue_date_anchor(row_words):
        return None

    cleaned = re.sub(r"^[^0-9]+|[^0-9]+$", "", text.strip())
    match = re.fullmatch(r"(\d{2})(\d{2})[./-](\d{2}|\d{4})", cleaned)
    if match is None:
        return None

    return normalize_invoice_date(
        f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    )


def _mark_selected_targets(
    candidates: list[InvoiceDateCandidate],
) -> list[InvoiceDateCandidate]:
    selected = select_invoice_date_target_per_page(candidates)
    marked_candidates = []

    for candidate in candidates:
        marked_candidates.append(
            InvoiceDateCandidate(
                page=candidate.page,
                raw_text=candidate.raw_text,
                normalized_value=candidate.normalized_value,
                bbox=candidate.bbox,
                score=candidate.score,
                selected_as_data_fattura=selected.get(candidate.page) == candidate,
                anchor_context=candidate.anchor_context,
                negative_context=candidate.negative_context,
                nearby_text=candidate.nearby_text,
            )
        )

    return marked_candidates


def _score_invoice_date(
    anchor_matches: list[_AnchorMatch],
    negative_context: str,
) -> float:
    if not anchor_matches:
        score = 70.0
    else:
        score = 70 + max(match.score for match in anchor_matches)

    if negative_context == "entro il":
        score -= 90
    elif negative_context == "entro":
        score -= 70
    elif negative_context == "riferimento normativo":
        score -= 120
    elif negative_context == "periodo fatturazione":
        score -= 90
    elif negative_context == "data pagamento":
        score -= 120

    return round(max(0, score), 2)


def _invoice_date_anchor_matches(
    nearby_tokens: list[_NearbyToken],
) -> list[_AnchorMatch]:
    nearby_text = _normalize_context_text(
        " ".join(nearby.token.text for nearby in nearby_tokens)
    )
    matches = _anchor_matches(
        nearby_text,
        anchors=[
            "data documento",
            "data doc",
            "data fattura",
            "fattura data",
            "periodo di fatturazione",
            "periodo fatturazione",
            "data",
            "fattura",
            "del",
        ],
    )
    if _has_billing_period_context(nearby_text.split()):
        matches.append(
            _AnchorMatch(
                "periodo di fatturazione",
                90.0,
                exact=False,
                distance_n=0.0,
            )
        )
    if any(_looks_like_invoice_number_reference(nearby.token.text) for nearby in nearby_tokens):
        matches.append(_AnchorMatch("numero_fattura", 60.0, exact=True, distance_n=0.0))
    return _deduplicate_anchor_matches(matches)


def _has_billing_period_context(words: list[str]) -> bool:
    return (
        _has_adjacent_words(words, "periodo", "fatturazione")
        or _has_words_in_order(words, ["periodo", "di", "fatturazione"])
        or ("fatturazione" in words and ("dal" in words or "al" in words))
    )


def _negative_date_context(row_tokens: list[_NearbyToken]) -> str:
    words = _normalized_context_words(row_tokens)
    has_del = "del" in words
    if _has_due_date_context(words) and not has_del:
        return "entro il"

    if _has_payment_execution_context(words):
        return "data pagamento"

    if _has_legal_reference_context(words):
        return "riferimento normativo"

    if _has_billing_period_context(words):
        return "periodo fatturazione"

    return ""


def _normalized_context_words(tokens: list[_NearbyToken]) -> list[str]:
    return _normalize_context_text(
        " ".join(nearby.token.text for nearby in tokens)
    ).split()


def _has_issue_date_anchor(words: list[str]) -> bool:
    return (
        "del" in words
        or _has_adjacent_words(words, "data", "documento")
        or _has_adjacent_words(words, "data", "fattura")
        or _has_adjacent_words(words, "fattura", "data")
    )


def _has_due_date_context(words: list[str]) -> bool:
    if _has_adjacent_words(words, "entro", "il") or "entroil" in words:
        return True

    for index in range(len(words)):
        for width in (1, 2):
            compact = "".join(words[index : index + width])
            if not 6 <= len(compact) <= 8:
                continue
            if SequenceMatcher(None, compact, "entroil").ratio() >= 0.70:
                return True
    return False


def _has_payment_execution_context(words: list[str]) -> bool:
    return (
        _has_adjacent_words(words, "data", "esecuzione")
        or _has_adjacent_words(words, "data", "addebito")
        or _has_words_in_order(words, ["data", "di", "addebito"])
    )


def _has_legal_reference_context(words: list[str]) -> bool:
    legal_markers = {
        "legge",
        "art",
        "articolo",
        "comma",
        "contribuenti",
        "forfetari",
        "forfettari",
    }
    compact_words = {re.sub(r"[^0-9a-zA-Z]+", "", word) for word in words}
    has_law_number = any(word.startswith("l") and word[1:].isdigit() for word in compact_words)
    has_split_law_number = any(
        left == "l" and right.isdigit()
        for left, right in zip(words, words[1:])
    )
    return (
        has_law_number
        or has_split_law_number
        or bool(legal_markers.intersection(words))
        or _has_adjacent_words(words, "contribuenti", "forfetari")
        or _has_adjacent_words(words, "contribuenti", "forfettari")
    )


def _looks_like_invoice_number_reference(text: str) -> bool:
    value = text.strip().upper()
    value = re.sub(r"^[^A-Z0-9]+|[^A-Z0-9]+$", "", value)
    if re.fullmatch(r"\d{1,6}/\d{2,4}", value):
        return True

    return (
        re.fullmatch(r"[A-Z0-9][A-Z0-9./-]{4,24}", value) is not None
        and any(char.isdigit() for char in value)
        and any(char.isalpha() for char in value)
    )


def _nearby_tokens(
    page: DocumentPage,
    token: OcrToken,
    x_radius: float | None = None,
    y_radius: float | None = None,
    max_distance_n: float = 0.36,
    max_tokens: int = 20,
) -> list[_NearbyToken]:
    x_radius = x_radius if x_radius is not None else page.width * 0.50
    y_radius = y_radius if y_radius is not None else page.height * 0.06
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


def _same_row_tokens(
    page: DocumentPage,
    token: OcrToken,
    x_radius: float | None = None,
) -> list[_NearbyToken]:
    """Return horizontally relevant tokens whose vertical glyph bands overlap."""
    x_radius = x_radius if x_radius is not None else page.width * 0.45
    token_center_x = (token.bbox.x1 + token.bbox.x2) / 2
    token_center_y = (token.bbox.y1 + token.bbox.y2) / 2
    token_height = max(1.0, token.bbox.y2 - token.bbox.y1)
    row_tokens: list[_NearbyToken] = []

    for other in page.tokens:
        if other is token:
            continue

        other_center_x = (other.bbox.x1 + other.bbox.x2) / 2
        if abs(other_center_x - token_center_x) > x_radius:
            continue

        other_center_y = (other.bbox.y1 + other.bbox.y2) / 2
        other_height = max(1.0, other.bbox.y2 - other.bbox.y1)
        overlap = max(
            0.0,
            min(token.bbox.y2, other.bbox.y2)
            - max(token.bbox.y1, other.bbox.y1),
        )
        overlap_ratio = overlap / min(token_height, other_height)
        center_tolerance = max(4.0, min(token_height, other_height) * 0.75)
        if (
            overlap_ratio < 0.35
            and abs(other_center_y - token_center_y) > center_tolerance
        ):
            continue

        row_tokens.append(
            _NearbyToken(
                token=other,
                distance_n=_normalized_distance(
                    page=page,
                    first_x=token_center_x,
                    first_y=token_center_y,
                    second_x=other_center_x,
                    second_y=other_center_y,
                ),
            )
        )

    return sorted(
        row_tokens,
        key=lambda item: (item.token.bbox.x1, item.token.bbox.y1),
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


def _anchor_matches(text: str, anchors: list[str]) -> list[_AnchorMatch]:
    matches = []

    for anchor in anchors:
        best_similarity, exact = _best_anchor_similarity(text, anchor)
        if best_similarity >= _min_similarity_for_anchor(anchor):
            anchor_score = _anchor_score(anchor, best_similarity)
            matches.append(
                _AnchorMatch(
                    label=anchor,
                    score=round(anchor_score, 2),
                    exact=exact,
                    distance_n=0.0,
                )
            )

    return matches


def _best_anchor_similarity(text: str, anchor: str) -> tuple[float, bool]:
    anchor_words = anchor.split()
    text_words = text.split()
    if len(text_words) < len(anchor_words):
        return 0.0, False

    best = 0.0
    exact = False
    for index in range(len(text_words) - len(anchor_words) + 1):
        window = " ".join(text_words[index : index + len(anchor_words)])
        similarity = SequenceMatcher(None, window, anchor).ratio()
        if window == anchor:
            exact = True
        best = max(best, similarity)

    return best, exact


def _min_similarity_for_anchor(anchor: str) -> float:
    if len(anchor) <= 4:
        return 1.0

    return MIN_FUZZY_ANCHOR_SIMILARITY


def _anchor_score(anchor: str, similarity: float) -> float:
    if anchor == "del":
        return similarity * 60

    if anchor == "fattura":
        return similarity * 60

    if anchor in {"periodo di fatturazione", "periodo fatturazione"}:
        return similarity * 35

    return similarity * 100


def _normalize_context_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def _has_adjacent_words(words: list[str], first: str, second: str) -> bool:
    return any(
        left == first and right == second
        for left, right in zip(words, words[1:])
    )


def _has_words_in_order(words: list[str], expected_words: list[str]) -> bool:
    if len(words) < len(expected_words):
        return False

    return any(
        words[index : index + len(expected_words)] == expected_words
        for index in range(len(words) - len(expected_words) + 1)
    )


def _format_anchor_matches(matches: list[_AnchorMatch]) -> str:
    return "|".join(match.label for match in matches)


def _deduplicate_anchor_matches(matches: list[_AnchorMatch]) -> list[_AnchorMatch]:
    best_by_label = {}
    for match in matches:
        current = best_by_label.get(match.label)
        if current is None or (match.exact, match.score) > (current.exact, current.score):
            best_by_label[match.label] = match

    return list(best_by_label.values())
