from __future__ import annotations

import csv
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
import re

from estrattore.model import BoundingBox, DocumentPage, DocumentRepresentation, TextSpan


MIN_FUZZY_SIMILARITY = 0.88


@dataclass(frozen=True)
class CondominiumCandidate:
    page: int
    raw_text: str
    bbox: BoundingBox
    score: float
    selected_as_condominio: bool
    condominio_context: str
    anchor_context: str
    nearby_text: str


@dataclass(frozen=True)
class _AnchorMatch:
    label: str
    score: float
    exact: bool


def find_condominium_candidates(
    document: DocumentRepresentation,
) -> list[CondominiumCandidate]:
    candidates = []

    for page in document.pages:
        for span in page.spans:
            condominio_matches = _condominio_matches(span.text)
            if not condominio_matches:
                continue

            nearby_spans = _nearby_spans(page, span)
            anchor_matches = _anchor_matches(
                _normalize_context_text(
                    " ".join(nearby_span.text for nearby_span in nearby_spans)
                ),
                anchors=[
                    "intestatario",
                    "spett le",
                    "spettabile",
                    "destinatario",
                    "committente",
                    "cliente",
                    "indirizzo",
                ],
            )
            score = (
                80
                + max([match.score for match in anchor_matches] or [0])
                - _dirty_condominium_span_penalty(span.text)
            )
            candidates.append(
                CondominiumCandidate(
                    page=page.page_number,
                    raw_text=span.text,
                    bbox=span.bbox,
                    score=round(score, 2),
                    selected_as_condominio=False,
                    condominio_context=_format_anchor_matches(condominio_matches),
                    anchor_context=_format_anchor_matches(anchor_matches),
                    nearby_text=" ".join(nearby_span.text for nearby_span in nearby_spans),
                )
            )

    return _mark_selected_targets(candidates)


def selected_condominium_candidates_by_page(
    candidates: list[CondominiumCandidate],
    min_score: float = 80.0,
) -> dict[int, list[CondominiumCandidate]]:
    selected: dict[int, list[CondominiumCandidate]] = {}

    for page in sorted({candidate.page for candidate in candidates}):
        page_candidates = [
            candidate
            for candidate in candidates
            if candidate.page == page and candidate.score >= min_score
        ]
        if not page_candidates:
            continue

        selected[page] = [
            max(
                page_candidates,
                key=lambda candidate: (
                    _starts_like_condominio(candidate.raw_text),
                    candidate.score,
                    -candidate.bbox.y1,
                    -candidate.bbox.x1,
                ),
            )
        ]

    return selected


def save_condominium_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_condominium_candidates(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "raw_text",
                "score",
                "selected_as_condominio",
                "condominio_context",
                "anchor_context",
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
                    "score": candidate.score,
                    "selected_as_condominio": candidate.selected_as_condominio,
                    "condominio_context": candidate.condominio_context,
                    "anchor_context": candidate.anchor_context,
                    "nearby_text": candidate.nearby_text,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


def is_civic_number_inside_condominium(
    candidate_value: str,
    candidate_bbox: BoundingBox,
    page: int,
    condominium_candidates: list[CondominiumCandidate],
) -> bool:
    if not _looks_like_civic_number(candidate_value):
        return False

    center_x = (candidate_bbox.x1 + candidate_bbox.x2) / 2
    center_y = (candidate_bbox.y1 + candidate_bbox.y2) / 2
    for candidate in condominium_candidates:
        if candidate.page != page or not candidate.selected_as_condominio:
            continue

        expanded_bbox = _expand_bbox(candidate.bbox, padding=18)
        if (
            expanded_bbox.x1 <= center_x <= expanded_bbox.x2
            and expanded_bbox.y1 <= center_y <= expanded_bbox.y2
        ):
            return True

    return False


def _mark_selected_targets(
    candidates: list[CondominiumCandidate],
) -> list[CondominiumCandidate]:
    selected = selected_condominium_candidates_by_page(candidates)
    marked_candidates = []

    for candidate in candidates:
        marked_candidates.append(
            CondominiumCandidate(
                page=candidate.page,
                raw_text=candidate.raw_text,
                bbox=candidate.bbox,
                score=candidate.score,
                selected_as_condominio=candidate in selected.get(candidate.page, []),
                condominio_context=candidate.condominio_context,
                anchor_context=candidate.anchor_context,
                nearby_text=candidate.nearby_text,
            )
        )

    return marked_candidates


def _condominio_matches(text: str) -> list[_AnchorMatch]:
    normalized_text = _normalize_context_text(text)
    compact_text = normalized_text.replace(" ", "")
    words = normalized_text.split()
    matches = _anchor_matches(
        normalized_text,
        anchors=[
            "condominio",
            "condominio:cond minio",
            "condominio:condominio",
            "condominio:condominio",
        ],
    )

    if words and words[0] in {"cond", "condominio"}:
        matches.append(_AnchorMatch("condominio", 100.0, exact=True))

    if SequenceMatcher(None, compact_text, "condominio").ratio() >= MIN_FUZZY_SIMILARITY:
        matches.append(_AnchorMatch("condominio", 100.0, exact=compact_text == "condominio"))

    return _deduplicate_anchor_matches(matches)


def _starts_like_condominio(text: str) -> bool:
    words = _normalize_context_text(text).split()
    if not words:
        return False

    if words[0] in {"cond", "condominio"}:
        return True

    first_word_similarity = SequenceMatcher(None, words[0], "condominio").ratio()
    if first_word_similarity >= MIN_FUZZY_SIMILARITY:
        return True

    if len(words) >= 2:
        first_two_words = "".join(words[:2])
        return (
            SequenceMatcher(None, first_two_words, "condominio").ratio()
            >= MIN_FUZZY_SIMILARITY
        )

    return False


def _dirty_condominium_span_penalty(text: str) -> float:
    normalized_text = _normalize_context_text(text)
    words = normalized_text.split()
    if not words:
        return 0.0

    penalty = 0.0
    condominio_index = next(
        (
            index
            for index, word in enumerate(words)
            if word in {"cond", "condominio"}
            or SequenceMatcher(None, word, "condominio").ratio()
            >= MIN_FUZZY_SIMILARITY
        ),
        None,
    )

    text_before_condominio = " ".join(
        words[:condominio_index] if condominio_index is not None else words
    )
    compact_text = normalized_text.replace(" ", "")

    if "eur" in words:
        penalty += 35

    if re.search(r"\bIT[A-Z0-9]{12,}\b", text.upper()):
        penalty += 70

    if re.search(r"\d{12,}", compact_text):
        penalty += 70

    if condominio_index is not None and condominio_index >= 3:
        penalty += 45

    if re.search(r"\d{6,}", text_before_condominio.replace(" ", "")):
        penalty += 45

    return penalty


def _anchor_matches(text: str, anchors: list[str]) -> list[_AnchorMatch]:
    matches = []

    for anchor_definition in anchors:
        label, anchor = _parse_anchor_definition(anchor_definition)
        best_similarity, exact = _best_anchor_similarity(text, anchor)
        if best_similarity >= MIN_FUZZY_SIMILARITY:
            matches.append(_AnchorMatch(label, round(best_similarity * 100, 2), exact))

    return _deduplicate_anchor_matches(matches)


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


def _nearby_spans(
    page: DocumentPage,
    span: TextSpan,
    x_radius: float | None = None,
    y_radius: float | None = None,
) -> list[TextSpan]:
    x_radius = x_radius if x_radius is not None else page.width * 0.45
    y_radius = y_radius if y_radius is not None else page.height * 0.08
    span_center_x = (span.bbox.x1 + span.bbox.x2) / 2
    span_center_y = (span.bbox.y1 + span.bbox.y2) / 2
    nearby = []

    for other in page.spans:
        other_center_x = (other.bbox.x1 + other.bbox.x2) / 2
        other_center_y = (other.bbox.y1 + other.bbox.y2) / 2
        if (
            abs(other_center_x - span_center_x) <= x_radius
            and abs(other_center_y - span_center_y) <= y_radius
        ):
            nearby.append(other)

    return sorted(nearby, key=lambda nearby_span: (nearby_span.bbox.y1, nearby_span.bbox.x1))


def _normalize_context_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def _format_anchor_matches(matches: list[_AnchorMatch]) -> str:
    return "|".join(match.label for match in matches)


def _looks_like_civic_number(value: str) -> bool:
    return re.fullmatch(r"\d{1,4}/\d{1,4}[A-Z]?", value) is not None


def _expand_bbox(bbox: BoundingBox, padding: int) -> BoundingBox:
    return BoundingBox(
        x1=bbox.x1 - padding,
        y1=bbox.y1 - padding,
        x2=bbox.x2 + padding,
        y2=bbox.y2 + padding,
    )


def _deduplicate_anchor_matches(matches: list[_AnchorMatch]) -> list[_AnchorMatch]:
    best_by_label = {}
    for match in matches:
        current = best_by_label.get(match.label)
        if current is None or (match.exact, match.score) > (current.exact, current.score):
            best_by_label[match.label] = match

    return list(best_by_label.values())
