from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from estrattore.fiscal_validation import (
    is_valid_italian_vat_number,
    normalize_italian_vat_number,
)
from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken
from estrattore.ocr_normalization import normalize_fiscal_anchor_text


@dataclass(frozen=True)
class VatCandidate:
    page: int
    raw_text: str
    normalized_value: str
    bbox: BoundingBox
    score: float
    positive_context: str
    negative_context: str
    nearby_text: str


def find_vat_candidates(document: DocumentRepresentation) -> list[VatCandidate]:
    candidates = []

    for page in document.pages:
        for token in page.tokens:
            normalized_value = normalize_italian_vat_number(token.text)
            if normalized_value is None:
                continue

            if not is_valid_italian_vat_number(normalized_value):
                continue

            nearby_tokens = _nearby_tokens(page, token)
            score, positive_context, negative_context = _score_vat_candidate(
                token,
                nearby_tokens,
            )

            candidates.append(
                VatCandidate(
                    page=page.page_number,
                    raw_text=token.text,
                    normalized_value=normalized_value,
                    bbox=token.bbox,
                    score=score,
                    positive_context=positive_context,
                    negative_context=negative_context,
                    nearby_text=" ".join(nearby.text for nearby in nearby_tokens),
                )
            )

    return candidates


def select_best_vat_candidate_per_page(
    candidates: list[VatCandidate],
    min_score: float = 50,
    conflict_delta: float = 10,
) -> dict[int, VatCandidate | None]:
    by_page: dict[int, list[VatCandidate]] = {}
    for candidate in candidates:
        by_page.setdefault(candidate.page, []).append(candidate)

    selected: dict[int, VatCandidate | None] = {}
    for page, page_candidates in by_page.items():
        sorted_candidates = sorted(
            page_candidates,
            key=lambda candidate: candidate.score,
            reverse=True,
        )
        best = sorted_candidates[0]

        if best.score < min_score:
            selected[page] = None
            continue

        if len(sorted_candidates) > 1:
            second_best = sorted_candidates[1]
            if best.score - second_best.score < conflict_delta:
                selected[page] = None
                continue

        selected[page] = best

    return selected


def save_vat_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_vat_candidates(document)
    selected_by_page = select_best_vat_candidate_per_page(candidates)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "raw_text",
                "normalized_value",
                "score",
                "selected_for_page",
                "positive_context",
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
            selected = selected_by_page.get(candidate.page)
            writer.writerow(
                {
                    "source_file": document.source_file.as_posix(),
                    "page": candidate.page,
                    "raw_text": candidate.raw_text,
                    "normalized_value": candidate.normalized_value,
                    "score": candidate.score,
                    "selected_for_page": selected == candidate,
                    "positive_context": candidate.positive_context,
                    "negative_context": candidate.negative_context,
                    "nearby_text": candidate.nearby_text,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


def _nearby_tokens(
    page: DocumentPage,
    token: OcrToken,
    x_radius: float | None = None,
    y_radius: float | None = None,
) -> list[OcrToken]:
    x_radius = x_radius if x_radius is not None else page.width * 0.30
    y_radius = y_radius if y_radius is not None else page.height * 0.06
    token_center_x = (token.bbox.x1 + token.bbox.x2) / 2
    token_center_y = (token.bbox.y1 + token.bbox.y2) / 2
    nearby = []

    for other in page.tokens:
        if other is token:
            continue

        other_center_x = (other.bbox.x1 + other.bbox.x2) / 2
        other_center_y = (other.bbox.y1 + other.bbox.y2) / 2

        is_near_x = abs(other_center_x - token_center_x) <= x_radius
        is_near_y = abs(other_center_y - token_center_y) <= y_radius
        if is_near_x and is_near_y:
            nearby.append(other)

    return sorted(nearby, key=lambda item: (item.bbox.y1, item.bbox.x1))


def _score_vat_candidate(
    token: OcrToken,
    nearby_tokens: list[OcrToken],
) -> tuple[float, str, str]:
    nearby_text = _normalize_context_text(
        " ".join(token.text for token in nearby_tokens)
    )
    nearby_words = nearby_text.split()
    positive_matches = _positive_anchor_matches(nearby_words)
    negative_matches = _negative_anchor_matches(nearby_words)

    score = 10.0
    if positive_matches:
        score += 100
    if "partita iva" in positive_matches:
        score += 80
    if "iva" in nearby_words:
        score += 30
    if negative_matches:
        score -= 30 if positive_matches else 80

    return score, "|".join(positive_matches), "|".join(negative_matches)


def _normalize_context_text(text: str) -> str:
    return normalize_fiscal_anchor_text(text)


def _positive_anchor_matches(words: list[str]) -> list[str]:
    matches = []

    if _has_adjacent_words(words, "partita", "iva"):
        matches.append("partita iva")

    if "piva" in words or _has_adjacent_words(words, "p", "iva"):
        matches.append("piva")

    if "pva" in words:
        matches.append("pva")

    if _has_adjacent_words(words, "p", "i"):
        matches.append("p.i.")

    return matches


def _negative_anchor_matches(words: list[str]) -> list[str]:
    matches = []

    if _has_adjacent_words(words, "codice", "fiscale"):
        matches.append("codice fiscale")

    if _has_adjacent_words(words, "cod", "fisca"):
        matches.append("cod fisca")

    if _has_adjacent_words(words, "c", "f"):
        matches.append("c.f.")

    for anchor in ("cf", "iban", "banca", "conto"):
        if anchor in words:
            matches.append(anchor)

    return matches


def _has_adjacent_words(words: list[str], first: str, second: str) -> bool:
    return any(
        left == first and right == second
        for left, right in zip(words, words[1:])
    )
