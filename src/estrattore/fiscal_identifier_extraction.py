from __future__ import annotations

import csv
from difflib import SequenceMatcher
import re
from dataclasses import dataclass
from pathlib import Path

from estrattore.fiscal_validation import (
    is_valid_italian_tax_code,
    is_valid_italian_vat_number,
    normalize_italian_tax_code,
    normalize_italian_tax_code_with_anchored_ocr_correction,
)
from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, OcrToken
from estrattore.ocr_normalization import normalize_fiscal_anchor_text


MIN_FUZZY_ANCHOR_SIMILARITY = 0.88
SHORT_ANCHOR_MAX_LEN = 4
SAME_ROW_MIN_VERTICAL_OVERLAP_RATIO = 0.45
SAME_ROW_MAX_HORIZONTAL_GAP_N = 0.12
SAME_ROW_VERTICAL_DELTA_WEIGHT = 0.25


@dataclass(frozen=True)
class _AnchorMatch:
    label: str
    score: float
    exact: bool


@dataclass(frozen=True)
class FiscalIdentifierCandidate:
    page: int
    raw_text: str
    normalized_value: str
    bbox: BoundingBox
    is_valid_vat_number: bool
    is_valid_tax_code: bool
    p_iva_score: float
    codice_fiscale_score: float
    codice_fiscale_emittente_score: float
    campo_suggerito: str
    selected_as_p_iva: bool
    selected_as_codice_fiscale: bool
    selected_as_codice_fiscale_emittente: bool
    p_iva_context: str
    codice_fiscale_context: str
    codice_fiscale_emittente_context: str
    negative_context: str
    nearby_text: str


def find_fiscal_identifier_candidates(
    document: DocumentRepresentation,
) -> list[FiscalIdentifierCandidate]:
    candidates = []

    for page in document.pages:
        for token in page.tokens:
            normalized_value = normalize_italian_tax_code(token.text)
            nearby_tokens = _nearby_tokens(page, token)
            if normalized_value is None and _has_strong_tax_code_anchor(nearby_tokens):
                normalized_value = normalize_italian_tax_code_with_anchored_ocr_correction(
                    token.text
                )
            if normalized_value is None:
                continue

            is_valid_vat = is_valid_italian_vat_number(normalized_value)
            is_valid_tax_code = is_valid_italian_tax_code(normalized_value)
            if not is_valid_vat and not is_valid_tax_code:
                continue

            score = _score_fiscal_identifier(
                normalized_value=normalized_value,
                is_valid_vat_number=is_valid_vat,
                is_valid_tax_code=is_valid_tax_code,
                nearby_tokens=nearby_tokens,
            )

            candidates.append(
                FiscalIdentifierCandidate(
                    page=page.page_number,
                    raw_text=token.text,
                    normalized_value=normalized_value,
                    bbox=token.bbox,
                    is_valid_vat_number=is_valid_vat,
                    is_valid_tax_code=is_valid_tax_code,
                    p_iva_score=score.p_iva_score,
                    codice_fiscale_score=score.codice_fiscale_score,
                    codice_fiscale_emittente_score=0.0,
                    campo_suggerito=_suggested_field(
                        score.p_iva_score,
                        score.codice_fiscale_score,
                    ),
                    selected_as_p_iva=False,
                    selected_as_codice_fiscale=False,
                    selected_as_codice_fiscale_emittente=False,
                    p_iva_context=_format_anchor_matches(score.p_iva_matches),
                    codice_fiscale_context=_format_anchor_matches(
                        score.codice_fiscale_matches
                    ),
                    codice_fiscale_emittente_context="",
                    negative_context="|".join(score.negative_matches),
                    nearby_text=" ".join(
                        nearby.token.text for nearby in nearby_tokens
                    ),
                )
            )

    return _mark_selected_targets(candidates, document)


def select_fiscal_identifier_targets_per_page(
    candidates: list[FiscalIdentifierCandidate],
    document: DocumentRepresentation | None = None,
    min_score: float = 1,
) -> dict[int, dict[str, FiscalIdentifierCandidate]]:
    selected: dict[int, dict[str, FiscalIdentifierCandidate]] = {}
    pages_by_number = {
        page.page_number: page for page in document.pages
    } if document is not None else {}

    for page in sorted({candidate.page for candidate in candidates}):
        page_candidates = [
            candidate for candidate in candidates if candidate.page == page
        ]
        selected[page] = {}

        p_iva_candidate = _best_positive_candidate(
            page_candidates,
            target="p_iva",
            min_score=min_score,
        )
        if p_iva_candidate is not None:
            selected[page]["p_iva"] = p_iva_candidate

        document_page = pages_by_number.get(page)
        codice_fiscale_candidate = _best_recipient_tax_code_candidate(
            page=document_page,
            candidates=page_candidates,
            p_iva_candidate=p_iva_candidate,
            min_score=min_score,
        )
        if codice_fiscale_candidate is not None:
            selected[page]["codice_fiscale"] = codice_fiscale_candidate
            codice_fiscale_emittente_candidate = _best_issuer_tax_code_candidate(
                page=document_page,
                candidates=page_candidates,
                recipient_candidate=codice_fiscale_candidate,
                p_iva_candidate=p_iva_candidate,
            )
            if codice_fiscale_emittente_candidate is not None:
                selected[page]["codice_fiscale_emittente"] = (
                    codice_fiscale_emittente_candidate
                )

    return selected


def _mark_selected_targets(
    candidates: list[FiscalIdentifierCandidate],
    document: DocumentRepresentation,
) -> list[FiscalIdentifierCandidate]:
    selected = select_fiscal_identifier_targets_per_page(candidates, document=document)
    marked_candidates = []
    pages_by_number = {page.page_number: page for page in document.pages}

    for candidate in candidates:
        selected_for_page = selected.get(candidate.page, {})
        issuer_score, issuer_context = _issuer_tax_code_score(
            page=pages_by_number.get(candidate.page),
            candidate=candidate,
            recipient_candidate=selected_for_page.get("codice_fiscale"),
            p_iva_candidate=selected_for_page.get("p_iva"),
        )
        marked_candidates.append(
            FiscalIdentifierCandidate(
                page=candidate.page,
                raw_text=candidate.raw_text,
                normalized_value=candidate.normalized_value,
                bbox=candidate.bbox,
                is_valid_vat_number=candidate.is_valid_vat_number,
                is_valid_tax_code=candidate.is_valid_tax_code,
                p_iva_score=candidate.p_iva_score,
                codice_fiscale_score=candidate.codice_fiscale_score,
                codice_fiscale_emittente_score=issuer_score,
                campo_suggerito=candidate.campo_suggerito,
                selected_as_p_iva=selected_for_page.get("p_iva") == candidate,
                selected_as_codice_fiscale=(
                    selected_for_page.get("codice_fiscale") == candidate
                ),
                selected_as_codice_fiscale_emittente=(
                    selected_for_page.get("codice_fiscale_emittente") == candidate
                ),
                p_iva_context=candidate.p_iva_context,
                codice_fiscale_context=candidate.codice_fiscale_context,
                codice_fiscale_emittente_context=issuer_context,
                negative_context=candidate.negative_context,
                nearby_text=candidate.nearby_text,
            )
        )

    return marked_candidates


def _best_recipient_tax_code_candidate(
    page: DocumentPage | None,
    candidates: list[FiscalIdentifierCandidate],
    p_iva_candidate: FiscalIdentifierCandidate | None,
    min_score: float,
) -> FiscalIdentifierCandidate | None:
    valid_candidates = [
        candidate
        for candidate in candidates
        if candidate.is_valid_tax_code
        and candidate != p_iva_candidate
        and _recipient_tax_code_score(page, candidate, p_iva_candidate) >= min_score
    ]
    if not valid_candidates:
        return None

    return max(
        valid_candidates,
        key=lambda candidate: (
            _recipient_tax_code_score(page, candidate, p_iva_candidate),
            -candidate.bbox.y1,
            -candidate.bbox.x1,
        ),
    )


def _best_issuer_tax_code_candidate(
    page: DocumentPage | None,
    candidates: list[FiscalIdentifierCandidate],
    recipient_candidate: FiscalIdentifierCandidate,
    p_iva_candidate: FiscalIdentifierCandidate | None,
    min_score: float = 80.0,
) -> FiscalIdentifierCandidate | None:
    valid_candidates = [
        candidate
        for candidate in candidates
        if candidate.is_valid_tax_code
        and candidate.normalized_value != recipient_candidate.normalized_value
        and candidate != p_iva_candidate
        and _issuer_tax_code_score(
            page=page,
            candidate=candidate,
            recipient_candidate=recipient_candidate,
            p_iva_candidate=p_iva_candidate,
        )[0]
        >= min_score
    ]
    if not valid_candidates:
        return None

    return max(
        valid_candidates,
        key=lambda candidate: (
            _issuer_tax_code_score(
                page=page,
                candidate=candidate,
                recipient_candidate=recipient_candidate,
                p_iva_candidate=p_iva_candidate,
            )[0],
            -candidate.bbox.y1,
            -candidate.bbox.x1,
        ),
    )


def _best_positive_candidate(
    candidates: list[FiscalIdentifierCandidate],
    target: str,
    min_score: float,
) -> FiscalIdentifierCandidate | None:
    score_getter = (
        (lambda candidate: candidate.p_iva_score)
        if target == "p_iva"
        else (lambda candidate: candidate.codice_fiscale_score)
    )
    valid_candidates = [
        candidate
        for candidate in candidates
        if score_getter(candidate) >= min_score
    ]
    if not valid_candidates:
        return None

    return max(
        valid_candidates,
        key=lambda candidate: (
            score_getter(candidate),
            -candidate.bbox.y1,
            -candidate.bbox.x1,
        ),
    )


def _recipient_tax_code_score(
    page: DocumentPage | None,
    candidate: FiscalIdentifierCandidate,
    p_iva_candidate: FiscalIdentifierCandidate | None = None,
) -> float:
    score = candidate.codice_fiscale_score
    contexts = _role_context_distances(page, candidate)

    if _has_separate_page_vat_and_mixed_context(candidate, p_iva_candidate):
        score += 45.0
    if contexts.condominio_distance_n is not None:
        score += _distance_bonus(contexts.condominio_distance_n, max_bonus=95.0)
    if contexts.destinatario_distance_n is not None:
        score += _distance_bonus(contexts.destinatario_distance_n, max_bonus=80.0)
    if contexts.p_iva_distance_n is not None:
        score -= _distance_bonus(contexts.p_iva_distance_n, max_bonus=45.0)

    return round(max(score, 0.0), 2)


def _has_separate_page_vat_and_mixed_context(
    candidate: FiscalIdentifierCandidate,
    p_iva_candidate: FiscalIdentifierCandidate | None,
) -> bool:
    if p_iva_candidate is None:
        return False
    if candidate.normalized_value == p_iva_candidate.normalized_value:
        return False

    contexts = "|".join(
        [
            candidate.p_iva_context,
            candidate.codice_fiscale_context,
        ]
    )
    return "cod. fiscale / p.iva" in contexts


def _issuer_tax_code_score(
    page: DocumentPage | None,
    candidate: FiscalIdentifierCandidate,
    recipient_candidate: FiscalIdentifierCandidate | None,
    p_iva_candidate: FiscalIdentifierCandidate | None,
) -> tuple[float, str]:
    if not candidate.is_valid_tax_code:
        return 0.0, ""

    if (
        recipient_candidate is None
        or candidate.normalized_value == recipient_candidate.normalized_value
        or candidate == p_iva_candidate
    ):
        return 0.0, ""

    score = 0.0
    context = []
    role_distances = _role_context_distances(page, candidate)

    if _is_alphanumeric_tax_code(candidate.normalized_value):
        score += 170.0
        context.append("codice_fiscale_alfanumerico")
    else:
        score += max(candidate.codice_fiscale_score, 20.0)
        context.append("codice_fiscale_numerico_diverso")

    if role_distances.p_iva_distance_n is not None:
        score += _distance_bonus(role_distances.p_iva_distance_n, max_bonus=90.0)
        context.append("vicino_p_iva")

    if p_iva_candidate is not None:
        p_iva_distance = _candidate_distance_n(page, candidate, p_iva_candidate)
        if p_iva_distance is not None:
            score += _distance_bonus(p_iva_distance, max_bonus=55.0)
            context.append("vicino_valore_p_iva")

    if role_distances.condominio_distance_n is not None:
        score -= _distance_bonus(role_distances.condominio_distance_n, max_bonus=75.0)
    if role_distances.destinatario_distance_n is not None:
        score -= _distance_bonus(role_distances.destinatario_distance_n, max_bonus=60.0)

    if recipient_candidate is not None:
        recipient_contexts = _role_context_distances(page, recipient_candidate)
        if _is_closer_to_condominium(candidate, recipient_candidate, page):
            score -= 70.0
        elif _is_closer_to_condominium(recipient_candidate, candidate, page):
            score += 45.0
            context.append("altro_cf_piu_vicino_condominio")

        if (
            recipient_contexts.destinatario_distance_n is not None
            and role_distances.destinatario_distance_n is None
        ):
            score += 20.0

    return round(max(score, 0.0), 2), "|".join(dict.fromkeys(context))


@dataclass(frozen=True)
class _RoleContextDistances:
    condominio_distance_n: float | None
    destinatario_distance_n: float | None
    p_iva_distance_n: float | None


def _role_context_distances(
    page: DocumentPage | None,
    candidate: FiscalIdentifierCandidate,
) -> _RoleContextDistances:
    if page is None:
        return _RoleContextDistances(None, None, None)

    return _RoleContextDistances(
        condominio_distance_n=_closest_span_distance_n(
            page=page,
            candidate=candidate,
            predicate=_span_looks_like_condominio,
        ),
        destinatario_distance_n=_closest_text_distance_n(
            page=page,
            candidate=candidate,
            accepted_words={"destinatario", "intestatario", "spettabile", "spett"},
        ),
        p_iva_distance_n=_closest_p_iva_context_distance_n(page, candidate),
    )


def _closest_span_distance_n(
    page: DocumentPage,
    candidate: FiscalIdentifierCandidate,
    predicate,
) -> float | None:
    distances = [
        _bbox_distance_n(page, candidate.bbox, span.bbox)
        for span in page.spans
        if predicate(span.text)
    ]
    return min(distances) if distances else None


def _closest_text_distance_n(
    page: DocumentPage,
    candidate: FiscalIdentifierCandidate,
    accepted_words: set[str],
) -> float | None:
    distances = []

    for token in page.tokens:
        words = set(_normalize_context_text(token.text).split())
        if words & accepted_words:
            distances.append(_bbox_distance_n(page, candidate.bbox, token.bbox))

    for span in page.spans:
        words = set(_normalize_context_text(span.text).split())
        if words & accepted_words:
            distances.append(_bbox_distance_n(page, candidate.bbox, span.bbox))

    return min(distances) if distances else None


def _closest_p_iva_context_distance_n(
    page: DocumentPage,
    candidate: FiscalIdentifierCandidate,
) -> float | None:
    distances = [
        _bbox_distance_n(page, candidate.bbox, token.bbox)
        for token in page.tokens
        if _token_anchor_family(token) == "p_iva"
    ]
    return min(distances) if distances else None


def _candidate_distance_n(
    page: DocumentPage | None,
    first: FiscalIdentifierCandidate,
    second: FiscalIdentifierCandidate,
) -> float | None:
    if page is None:
        return None

    return _bbox_distance_n(page, first.bbox, second.bbox)


def _bbox_distance_n(page: DocumentPage, first: BoundingBox, second: BoundingBox) -> float:
    first_center_x = (first.x1 + first.x2) / 2
    first_center_y = (first.y1 + first.y2) / 2
    second_center_x = (second.x1 + second.x2) / 2
    second_center_y = (second.y1 + second.y2) / 2
    return _normalized_distance(
        page=page,
        first_x=first_center_x,
        first_y=first_center_y,
        second_x=second_center_x,
        second_y=second_center_y,
    )


def _distance_bonus(
    distance_n: float,
    max_bonus: float,
    max_distance_n: float = 0.22,
) -> float:
    distance_factor = max(0.0, 1 - min(distance_n / max_distance_n, 1))
    return max_bonus * distance_factor


def _span_looks_like_condominio(text: str) -> bool:
    normalized_text = _normalize_context_text(text)
    compact_text = normalized_text.replace(" ", "")
    if "condominio" in normalized_text.split():
        return True

    return SequenceMatcher(None, compact_text, "condominio").ratio() >= 0.88


def _is_closer_to_condominium(
    first: FiscalIdentifierCandidate,
    second: FiscalIdentifierCandidate,
    page: DocumentPage | None,
) -> bool:
    first_distance = _role_context_distances(page, first).condominio_distance_n
    second_distance = _role_context_distances(page, second).condominio_distance_n
    if first_distance is None:
        return False
    if second_distance is None:
        return True

    return first_distance + 0.03 < second_distance


def _is_alphanumeric_tax_code(value: str) -> bool:
    return len(value) == 16 and any(char.isalpha() for char in value)


def save_fiscal_identifier_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_fiscal_identifier_candidates(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "raw_text",
                "normalized_value",
                "is_valid_vat_number",
                "is_valid_tax_code",
                "p_iva_score",
                "codice_fiscale_score",
                "codice_fiscale_emittente_score",
                "campo_suggerito",
                "selected_as_p_iva",
                "selected_as_codice_fiscale",
                "selected_as_codice_fiscale_emittente",
                "p_iva_context",
                "codice_fiscale_context",
                "codice_fiscale_emittente_context",
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
                    "is_valid_vat_number": candidate.is_valid_vat_number,
                    "is_valid_tax_code": candidate.is_valid_tax_code,
                    "p_iva_score": candidate.p_iva_score,
                    "codice_fiscale_score": candidate.codice_fiscale_score,
                    "codice_fiscale_emittente_score": (
                        candidate.codice_fiscale_emittente_score
                    ),
                    "campo_suggerito": candidate.campo_suggerito,
                    "selected_as_p_iva": candidate.selected_as_p_iva,
                    "selected_as_codice_fiscale": (
                        candidate.selected_as_codice_fiscale
                    ),
                    "selected_as_codice_fiscale_emittente": (
                        candidate.selected_as_codice_fiscale_emittente
                    ),
                    "p_iva_context": candidate.p_iva_context,
                    "codice_fiscale_context": candidate.codice_fiscale_context,
                    "codice_fiscale_emittente_context": (
                        candidate.codice_fiscale_emittente_context
                    ),
                    "negative_context": candidate.negative_context,
                    "nearby_text": candidate.nearby_text,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


@dataclass(frozen=True)
class _FiscalIdentifierScore:
    p_iva_score: float
    codice_fiscale_score: float
    p_iva_matches: list[_AnchorMatch]
    codice_fiscale_matches: list[_AnchorMatch]
    negative_matches: list[str]


def _score_fiscal_identifier(
    normalized_value: str,
    is_valid_vat_number: bool,
    is_valid_tax_code: bool,
    nearby_tokens: list["_NearbyToken"],
) -> _FiscalIdentifierScore:
    raw_nearby_text = _normalize_context_text(
        " ".join(nearby.token.text for nearby in nearby_tokens)
    )
    if not _has_mixed_fiscal_vat_anchor(raw_nearby_text.split()):
        nearby_tokens = _dominant_context_tokens(nearby_tokens)

    nearby_text = _normalize_context_text(
        " ".join(nearby.token.text for nearby in nearby_tokens)
    )
    nearby_words = nearby_text.split()
    p_iva_matches = _vat_anchor_matches(nearby_text)
    codice_fiscale_matches = _tax_code_anchor_matches(nearby_text)
    negative_matches = _negative_anchor_matches(nearby_words)
    has_mixed_anchor = _has_mixed_fiscal_vat_anchor(nearby_words)
    has_ambiguous_9000_vat_anchor = (
        _looks_like_9000_numeric_tax_code(normalized_value)
        and bool(p_iva_matches)
        and not codice_fiscale_matches
    )
    if has_ambiguous_9000_vat_anchor:
        codice_fiscale_matches.append(
            _AnchorMatch("cod. fiscale / p.iva", 100.0, exact=True)
        )
        has_mixed_anchor = True

    p_iva_score = _base_target_score(
        target="p_iva",
        normalized_value=normalized_value,
        is_valid=is_valid_vat_number,
        matches=p_iva_matches,
        has_mixed_anchor=has_mixed_anchor,
    )
    codice_fiscale_score = _base_target_score(
        target="codice_fiscale",
        normalized_value=normalized_value,
        is_valid=is_valid_tax_code,
        matches=codice_fiscale_matches,
        has_mixed_anchor=has_mixed_anchor,
    )

    p_iva_labels = [match.label for match in p_iva_matches]
    if is_valid_vat_number and "partita iva" in p_iva_labels and not has_mixed_anchor:
        p_iva_score += 80
    if is_valid_vat_number and "iva" in nearby_words and not has_mixed_anchor:
        p_iva_score += 30

    return _FiscalIdentifierScore(
        p_iva_score=p_iva_score,
        codice_fiscale_score=codice_fiscale_score,
        p_iva_matches=p_iva_matches,
        codice_fiscale_matches=codice_fiscale_matches,
        negative_matches=negative_matches,
    )


def _base_target_score(
    target: str,
    normalized_value: str,
    is_valid: bool,
    matches: list[_AnchorMatch],
    has_mixed_anchor: bool,
) -> float:
    if not is_valid or not matches:
        return 0.0

    best_anchor_score = max(match.score for match in matches)

    if has_mixed_anchor:
        if target == "codice_fiscale" and _looks_like_9000_numeric_tax_code(
            normalized_value
        ):
            return best_anchor_score
        return 0.0

    return best_anchor_score


def _has_strong_tax_code_anchor(nearby_tokens: list["_NearbyToken"]) -> bool:
    nearby_text = _normalize_context_text(
        " ".join(nearby.token.text for nearby in nearby_tokens)
    )
    return any(match.score >= 100.0 for match in _tax_code_anchor_matches(nearby_text))


def _format_anchor_matches(matches: list[_AnchorMatch]) -> str:
    return "|".join(match.label for match in matches)


def _looks_like_9000_numeric_tax_code(value: str) -> bool:
    return len(value) == 11 and value.isdigit() and value.startswith("9000")


def _suggested_field(
    p_iva_score: float,
    codice_fiscale_score: float,
    min_score: float = 1,
    conflict_delta: float = 10,
) -> str:
    best_score = max(p_iva_score, codice_fiscale_score)
    if best_score < min_score:
        return "uncertain"

    if abs(p_iva_score - codice_fiscale_score) < conflict_delta:
        return "conflict"

    if p_iva_score > codice_fiscale_score:
        return "p_iva"

    return "codice_fiscale"


@dataclass(frozen=True)
class _NearbyToken:
    token: OcrToken
    distance_n: float


def _nearby_tokens(
    page: DocumentPage,
    token: OcrToken,
    x_radius: float | None = None,
    y_radius: float | None = None,
    max_distance_n: float = 0.22,
    max_tokens: int = 12,
) -> list[_NearbyToken]:
    x_radius = x_radius if x_radius is not None else page.width * 0.30
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
            distance = _fiscal_context_distance_n(page, token.bbox, other.bbox)
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


def _fiscal_context_distance_n(
    page: DocumentPage,
    value_bbox: BoundingBox,
    context_bbox: BoundingBox,
) -> float:
    if _looks_like_same_visual_row(value_bbox, context_bbox):
        horizontal_gap_n = _horizontal_gap(value_bbox, context_bbox) / page.width
        if horizontal_gap_n <= SAME_ROW_MAX_HORIZONTAL_GAP_N:
            vertical_delta_n = (
                abs(_bbox_center_y(value_bbox) - _bbox_center_y(context_bbox))
                / page.height
            )
            return (
                horizontal_gap_n
                + vertical_delta_n * SAME_ROW_VERTICAL_DELTA_WEIGHT
            )

    return _bbox_distance_n(page, value_bbox, context_bbox)


def _looks_like_same_visual_row(first: BoundingBox, second: BoundingBox) -> bool:
    return _vertical_overlap_ratio(first, second) >= SAME_ROW_MIN_VERTICAL_OVERLAP_RATIO


def _vertical_overlap_ratio(first: BoundingBox, second: BoundingBox) -> float:
    overlap = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
    shortest_height = max(1, min(first.y2 - first.y1, second.y2 - second.y1))
    return overlap / shortest_height


def _horizontal_gap(first: BoundingBox, second: BoundingBox) -> int:
    if first.x2 < second.x1:
        return second.x1 - first.x2
    if second.x2 < first.x1:
        return first.x1 - second.x2
    return 0


def _bbox_center_y(bbox: BoundingBox) -> float:
    return (bbox.y1 + bbox.y2) / 2


def _dominant_context_tokens(
    nearby_tokens: list[_NearbyToken],
    dominance_delta_n: float = 0.08,
) -> list[_NearbyToken]:
    closest_p_iva = _closest_anchor_distance(nearby_tokens, "p_iva")
    closest_codice_fiscale = _closest_anchor_distance(nearby_tokens, "codice_fiscale")

    if closest_p_iva is None or closest_codice_fiscale is None:
        return nearby_tokens

    if closest_p_iva + dominance_delta_n < closest_codice_fiscale:
        return [
            item
            for item in nearby_tokens
            if _token_anchor_family(item.token) != "codice_fiscale"
        ]

    if closest_codice_fiscale + dominance_delta_n < closest_p_iva:
        return [
            item
            for item in nearby_tokens
            if _token_anchor_family(item.token) != "p_iva"
        ]

    return nearby_tokens


def _closest_anchor_distance(
    nearby_tokens: list[_NearbyToken],
    family: str,
) -> float | None:
    distances = [
        item.distance_n
        for item in nearby_tokens
        if _token_anchor_family(item.token) == family
    ]
    return min(distances) if distances else None


def _token_anchor_family(token: OcrToken) -> str | None:
    words = _normalize_context_text(token.text).split()

    if any(word in {"piva", "pva", "iva"} for word in words):
        return "p_iva"

    if _has_adjacent_words(words, "p", "iva") or _has_adjacent_words(words, "p", "i"):
        return "p_iva"

    if any(word in {"cf", "codice", "fiscale", "fisc", "fisca"} for word in words):
        return "codice_fiscale"

    if _has_adjacent_words(words, "c", "f"):
        return "codice_fiscale"

    return None


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


def _normalize_context_text(text: str) -> str:
    return normalize_fiscal_anchor_text(text)


def _vat_anchor_matches(text: str) -> list[_AnchorMatch]:
    words = text.split()
    matches = _anchor_matches(
        text=text,
        anchors=[
            "partita iva",
            "piva",
            "pva",
            "p.i.:p i",
            "piva:p iva",
        ],
    )

    if _has_mixed_fiscal_vat_anchor(words):
        matches.append(_AnchorMatch("cod. fiscale / p.iva", 100.0, exact=True))

    return _deduplicate_anchor_matches(matches)


def _tax_code_anchor_matches(text: str) -> list[_AnchorMatch]:
    words = text.split()
    matches = _anchor_matches(
        text=text,
        anchors=[
            "codice fiscale",
            "cod fiscale",
            "cod fisc",
            "cod fisc:fisc cod",
            "cod fisca",
            "c.f.:c f",
            "cf",
        ],
    )

    if _has_mixed_fiscal_vat_anchor(words):
        matches.append(_AnchorMatch("cod. fiscale / p.iva", 100.0, exact=True))

    return _filter_subsumed_tax_code_matches(_deduplicate_anchor_matches(matches))


def _anchor_matches(text: str, anchors: list[str]) -> list[_AnchorMatch]:
    matches = []

    for anchor_definition in anchors:
        label, anchor = _parse_anchor_definition(anchor_definition)
        best_similarity, exact = _best_anchor_similarity(text, anchor)
        if best_similarity >= _min_similarity_for_anchor(anchor):
            matches.append(_AnchorMatch(label, round(best_similarity * 100, 2), exact))

    return matches


def _parse_anchor_definition(anchor_definition: str) -> tuple[str, str]:
    if ":" not in anchor_definition:
        return anchor_definition, anchor_definition

    label, anchor = anchor_definition.split(":", maxsplit=1)
    return label, anchor


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
    compact_anchor = anchor.replace(" ", "")
    if len(compact_anchor) <= SHORT_ANCHOR_MAX_LEN:
        return 1.0

    return MIN_FUZZY_ANCHOR_SIMILARITY


def _deduplicate_anchor_matches(matches: list[_AnchorMatch]) -> list[_AnchorMatch]:
    best_by_label = {}
    for match in matches:
        current = best_by_label.get(match.label)
        if current is None or (match.exact, match.score) > (current.exact, current.score):
            best_by_label[match.label] = match

    return list(best_by_label.values())


def _filter_subsumed_tax_code_matches(
    matches: list[_AnchorMatch],
) -> list[_AnchorMatch]:
    labels = {match.label for match in matches}
    exact_labels = {match.label for match in matches if match.exact}

    if "codice fiscale" in exact_labels:
        labels_to_remove = {"cod fiscale", "cod fisc", "cod fisca"}
    elif "cod fiscale" in exact_labels:
        labels_to_remove = {"codice fiscale", "cod fisc", "cod fisca"}
    elif "cod fisc" in exact_labels:
        labels_to_remove = {"codice fiscale", "cod fiscale", "cod fisca"}
    elif "cod fisca" in exact_labels:
        labels_to_remove = {"codice fiscale", "cod fiscale", "cod fisc"}
    elif "codice fiscale" in labels:
        labels_to_remove = {"cod fiscale", "cod fisc", "cod fisca"}
    else:
        labels_to_remove = set()

    return [match for match in matches if match.label not in labels_to_remove]


def _replace_anchor_label(
    matches: list[_AnchorMatch],
    source_labels: set[str],
    target_label: str,
) -> list[_AnchorMatch]:
    return [
        _AnchorMatch(target_label, match.score, match.exact)
        if match.label in source_labels
        else match
        for match in matches
    ]


def _negative_anchor_matches(words: list[str]) -> list[str]:
    return [anchor for anchor in ("iban", "banca", "conto") if anchor in words]


def _has_mixed_fiscal_vat_anchor(words: list[str]) -> bool:
    has_fiscal = (
        _has_adjacent_words(words, "cod", "fiscale")
        or _has_adjacent_words(words, "cod", "fisc")
        or _has_adjacent_words(words, "cod", "fisca")
        or _has_adjacent_words(words, "codice", "fiscale")
        or "fiscale" in words
        or "fisca" in words
    )
    has_vat = (
        "piva" in words
        or "pva" in words
        or _has_adjacent_words(words, "p", "iva")
        or _has_adjacent_words(words, "p", "i")
        or _has_adjacent_words(words, "partita", "iva")
    )
    return has_fiscal and has_vat


def _has_adjacent_words(words: list[str], first: str, second: str) -> bool:
    return any(
        left == first and right == second
        for left, right in zip(words, words[1:])
    )
