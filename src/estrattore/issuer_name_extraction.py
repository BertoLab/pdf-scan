from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re

from estrattore.model import BoundingBox, DocumentRepresentation, TextSpan
from estrattore.ocr_normalization import SRL_OCR_PATTERN, normalize_company_legal_form


@dataclass(frozen=True)
class IssuerNameCandidate:
    page: int
    raw_text: str
    normalized_value: str
    bbox: BoundingBox
    accuracy: float
    occurrence_count: int
    score: float
    selected_as_nome_emittente: bool


def find_issuer_name_candidates(
    document: DocumentRepresentation,
) -> list[IssuerNameCandidate]:
    raw_candidates = [
        candidate
        for page in document.pages
        for span in page.spans
        if (candidate := _issuer_candidate_from_span(page.page_number, span))
    ]
    occurrence_count_by_name = {
        candidate.normalized_value: sum(
            1
            for other in raw_candidates
            if other.normalized_value == candidate.normalized_value
        )
        for candidate in raw_candidates
    }
    scored_candidates = [
        IssuerNameCandidate(
            page=candidate.page,
            raw_text=candidate.raw_text,
            normalized_value=candidate.normalized_value,
            bbox=candidate.bbox,
            accuracy=candidate.accuracy,
            occurrence_count=occurrence_count_by_name[candidate.normalized_value],
            score=round(
                occurrence_count_by_name[candidate.normalized_value] * 100
                + candidate.accuracy * 100,
                2,
            ),
            selected_as_nome_emittente=False,
        )
        for candidate in raw_candidates
    ]

    return _mark_selected_target(scored_candidates)


def save_issuer_name_candidates_csv(
    document: DocumentRepresentation,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    candidates = find_issuer_name_candidates(document)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "source_file",
                "page",
                "raw_text",
                "normalized_value",
                "accuracy",
                "occurrence_count",
                "score",
                "selected_as_nome_emittente",
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
                    "accuracy": candidate.accuracy,
                    "occurrence_count": candidate.occurrence_count,
                    "score": candidate.score,
                    "selected_as_nome_emittente": candidate.selected_as_nome_emittente,
                    "x1": candidate.bbox.x1,
                    "y1": candidate.bbox.y1,
                    "x2": candidate.bbox.x2,
                    "y2": candidate.bbox.y2,
                }
            )

    return output_path


def _issuer_candidate_from_span(
    page_number: int,
    span: TextSpan,
) -> IssuerNameCandidate | None:
    name = _issuer_name_from_text(span.text)
    if not name:
        return None

    return IssuerNameCandidate(
        page=page_number,
        raw_text=span.text,
        normalized_value=name,
        bbox=span.bbox,
        accuracy=_span_accuracy(span),
        occurrence_count=0,
        score=0.0,
        selected_as_nome_emittente=False,
    )


def _issuer_name_from_text(text: str) -> str | None:
    normalized = " ".join(text.replace("\n", " ").split())
    match = re.search(SRL_OCR_PATTERN, normalized, flags=re.IGNORECASE)
    if not match:
        return None

    before = normalized[: match.start()].strip(" -–—,;:")
    if not before:
        return None

    before = re.split(r"\b(?:P\.?\s*IVA|PARTITA\s+IVA|COD\.?\s+FISCALE|C\.?\s*F\.?)\b", before, flags=re.IGNORECASE)[0]
    before = before.strip(" -–—,;:")
    words = before.split()
    if not words:
        return None

    company_words = words[-5:]
    company_name = " ".join(company_words + ["SRL"])
    return _normalize_issuer_name(company_name)


def _normalize_issuer_name(text: str) -> str:
    text = normalize_company_legal_form(text)
    text = re.sub(r"[^0-9A-Za-z ]+", " ", text)
    return " ".join(text.upper().split())


def _span_accuracy(span: TextSpan) -> float:
    confidences = [
        token.confidence
        for token in span.tokens
        if token.confidence is not None
    ]
    if not confidences:
        return 0.0

    return round(sum(confidences) / len(confidences), 4)


def _mark_selected_target(
    candidates: list[IssuerNameCandidate],
) -> list[IssuerNameCandidate]:
    selected = max(
        candidates,
        key=lambda candidate: (
            candidate.occurrence_count,
            candidate.accuracy,
            candidate.score,
            -candidate.bbox.y1,
            -candidate.bbox.x1,
        ),
        default=None,
    )
    marked_candidates = []

    for candidate in candidates:
        marked_candidates.append(
            IssuerNameCandidate(
                page=candidate.page,
                raw_text=candidate.raw_text,
                normalized_value=candidate.normalized_value,
                bbox=candidate.bbox,
                accuracy=candidate.accuracy,
                occurrence_count=candidate.occurrence_count,
                score=candidate.score,
                selected_as_nome_emittente=candidate == selected,
            )
        )

    return marked_candidates
