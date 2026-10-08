from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from estrattore.registry import RegistryCache
from estrattore.registry_enrichment import enrich_invoice_fields_from_registry


INVOICE_FIELD_DEFINITIONS = [
    ("codice_fiscale_emittente", "Codice fiscale emittente"),
    ("nome_emittente", "Nome emittente"),
    ("p_iva", "P.IVA"),
    ("codice_fiscale", "C.F. Condominio"),
    ("condominio", "Condominio"),
    ("numero_fattura", "Numero fattura"),
    ("data_fattura", "Data fattura"),
    ("pagata_il", "Pagata il"),
    ("economic_summary.totale_fattura.value", "Totale fattura"),
    ("economic_summary.ritenuta.value", "Ritenuta"),
    ("economic_summary.totale_netto_fattura.value", "Totale netto fattura"),
]
MIN_INVOICE_START_SIGNAL_COUNT = 5
MIN_INVOICE_START_CHANGED_SIGNAL_COUNT = 3


def build_viewer_invoices(
    page_manifests: list[dict],
    first_invoice_number: int = 1,
    registry_cache: RegistryCache | None = None,
) -> list[dict]:
    invoices = []
    current_invoice_pages = []
    current_invoice_signature = {}
    current_invoice_has_payment_summary = False

    for page_manifest in page_manifests:
        page_manifest["invoice_header_regions"] = _invoice_header_regions(
            page_manifest
        )
        page_signature = _page_invoice_signature(page_manifest)
        start_decision = _invoice_start_decision(
            page_manifest=page_manifest,
            page_signature=page_signature,
            current_invoice_signature=current_invoice_signature,
            current_invoice_has_payment_summary=current_invoice_has_payment_summary,
            is_first_invoice_page=not current_invoice_pages,
        )
        starts_invoice = not current_invoice_pages or start_decision["starts_invoice"]

        if starts_invoice and current_invoice_pages:
            invoice_number = first_invoice_number + len(invoices)
            invoices.append(
                _viewer_invoice(
                    invoice_number,
                    current_invoice_pages,
                    registry_cache=registry_cache,
                )
            )
            current_invoice_pages = []
            current_invoice_signature = {}
            current_invoice_has_payment_summary = False

        page_manifest["invoice_signature"] = {
            "fields": page_signature,
            "signal_count": len(page_signature),
        }
        page_manifest["invoice_start_decision"] = start_decision
        current_invoice_pages.append(page_manifest)
        current_invoice_signature = _merge_invoice_signatures(
            current_invoice_signature,
            page_signature,
        )
        current_invoice_has_payment_summary = (
            current_invoice_has_payment_summary
            or _is_bank_payment_summary_page(page_manifest)
        )

    if current_invoice_pages:
        invoice_number = first_invoice_number + len(invoices)
        invoices.append(
            _viewer_invoice(
                invoice_number,
                current_invoice_pages,
                registry_cache=registry_cache,
            )
        )

    link_shared_payment_dates(invoices, page_manifests)
    for invoice in invoices:
        extraction_fields = deepcopy(invoice.get("fields", []))
        invoice["extraction_fields"] = extraction_fields
        invoice["fields"] = enrich_invoice_fields_from_registry(
            extraction_fields,
            registry_cache,
        )
    return invoices


def _page_invoice_signature(page_manifest: dict) -> dict[str, str]:
    signature = {}
    for kind, _label in INVOICE_FIELD_DEFINITIONS:
        value = _invoice_field_value_from_page(page_manifest, kind)
        if value:
            signature[kind] = value

    invoice_header = _invoice_header_signature(page_manifest)
    if invoice_header:
        signature["invoice_header"] = invoice_header

    return signature


def _invoice_header_signature(page_manifest: dict) -> str | None:
    region = _best_invoice_header_region(page_manifest)
    if not region:
        return None

    return "|".join(
        [
            ",".join(region["labels"]),
            region["values"].get("numero_fattura", ""),
            region["values"].get("data_fattura", ""),
            region["values"].get("p_iva", ""),
            region["values"].get("totale_fattura", ""),
        ]
    )


def _best_invoice_header_region(page_manifest: dict) -> dict | None:
    regions = page_manifest.get("invoice_header_regions") or _invoice_header_regions(
        page_manifest
    )
    return max(regions, key=lambda region: region["score"], default=None)


def _invoice_header_regions(page_manifest: dict) -> list[dict]:
    label_hits = []
    for span in page_manifest.get("spans", []):
        label = _invoice_header_label(span["text"])
        if label:
            label_hits.append((label, span))

    regions = []
    for _label, span in label_hits:
        x1 = span["bbox"]["x1"]
        aligned_spans = [
            (label, other_span)
            for label, other_span in label_hits
            if abs(other_span["bbox"]["x1"] - x1) <= 36
        ]
        labels = sorted({label for label, _span in aligned_spans})
        if len(labels) < 4:
            continue

        values = _invoice_header_values(page_manifest)
        value_count = len([value for value in values.values() if value])
        if value_count < 2:
            continue

        bbox = _manifest_bbox_around([span["bbox"] for _label, span in aligned_spans])
        regions.append(
            {
                "labels": labels,
                "label_count": len(labels),
                "values": values,
                "value_count": value_count,
                "score": len(labels) * 25 + value_count * 35,
                "bbox": bbox,
            }
        )

    unique_regions = {}
    for region in regions:
        key = (
            tuple(region["labels"]),
            region["bbox"]["x1"],
            region["bbox"]["y1"],
            region["bbox"]["x2"],
            region["bbox"]["y2"],
        )
        current = unique_regions.get(key)
        if current is None or region["score"] > current["score"]:
            unique_regions[key] = region

    return sorted(
        unique_regions.values(),
        key=lambda region: region["score"],
        reverse=True,
    )


def _invoice_header_values(page_manifest: dict) -> dict[str, str]:
    return {
        "numero_fattura": _best_target_value(page_manifest, "numero_fattura") or "",
        "data_fattura": _best_target_value(page_manifest, "data_fattura") or "",
        "p_iva": _best_target_value(page_manifest, "p_iva") or "",
        "codice_fiscale": _best_target_value(page_manifest, "codice_fiscale") or "",
        "totale_fattura": _best_target_value(
            page_manifest,
            "economic_summary.totale_fattura.value",
        )
        or "",
    }


def _manifest_bbox_around(bboxes: list[dict]) -> dict:
    return {
        "x1": min(bbox["x1"] for bbox in bboxes),
        "y1": min(bbox["y1"] for bbox in bboxes),
        "x2": max(bbox["x2"] for bbox in bboxes),
        "y2": max(bbox["y2"] for bbox in bboxes),
    }


def _invoice_header_label(text: str) -> str | None:
    normalized = _normalize_manifest_text(text)
    compact = normalized.replace(" ", "")

    if _contains_words(normalized, ["fattura", "numero"]):
        return "fattura_numero"

    if normalized == "del" or normalized.startswith("del "):
        return "del"

    if "importo" in normalized and "pagare" in normalized:
        return "importo_da_pagare"

    if _contains_words(normalized, ["entro", "il"]):
        return "entro_il"

    if "condizioni" in normalized and "pagamento" in normalized:
        return "condizioni_pagamento"

    if (
        ("cod" in normalized or "codice" in normalized)
        and "fiscale" in normalized
        and ("piva" in compact or "iva" in normalized)
    ):
        return "codice_fiscale_piva"

    return None


def _normalize_manifest_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def _contains_words(text: str, words: list[str]) -> bool:
    text_words = text.split()
    return all(word in text_words for word in words)


def _invoice_start_decision(
    page_manifest: dict,
    page_signature: dict[str, str],
    current_invoice_signature: dict[str, str],
    current_invoice_has_payment_summary: bool = False,
    is_first_invoice_page: bool = False,
) -> dict:
    changed_signals = _changed_invoice_signals(
        page_signature=page_signature,
        current_invoice_signature=current_invoice_signature,
    )
    has_enough_signals = len(page_signature) >= MIN_INVOICE_START_SIGNAL_COUNT
    has_enough_changes = (
        len(changed_signals) >= MIN_INVOICE_START_CHANGED_SIGNAL_COUNT
    )
    has_post_payment_split_signals = (
        current_invoice_has_payment_summary and len(changed_signals) >= 2
    )
    has_strong_invoice_identity_change = _has_strong_invoice_identity_change(
        page_manifest=page_manifest,
        page_signature=page_signature,
        current_invoice_signature=current_invoice_signature,
    )
    reasons = []
    if is_first_invoice_page:
        reasons.append("prima_pagina")
    if has_enough_signals:
        reasons.append("almeno_5_segnali")
    if has_enough_changes:
        reasons.append("almeno_3_segnali_diversi")
    if has_post_payment_split_signals:
        reasons.append("dopo_riepilogo_bancario_almeno_2_segnali_diversi")
    if has_strong_invoice_identity_change:
        reasons.append("numero_fattura_forte_diverso")
    starts_invoice = bool(
        is_first_invoice_page
        or (current_invoice_signature and has_strong_invoice_identity_change)
        or (current_invoice_signature and has_enough_signals and has_enough_changes)
        or (current_invoice_signature and has_post_payment_split_signals)
    )

    return {
        "starts_invoice": starts_invoice,
        "signal_count": len(page_signature),
        "changed_signal_count": len(changed_signals),
        "changed_signals": changed_signals,
        "reasons": reasons,
    }


def _has_strong_invoice_identity_change(
    page_manifest: dict,
    page_signature: dict[str, str],
    current_invoice_signature: dict[str, str],
) -> bool:
    page_invoice_number = page_signature.get("numero_fattura", "")
    current_invoice_number = current_invoice_signature.get("numero_fattura", "")
    if (
        not page_invoice_number
        or not current_invoice_number
        or page_invoice_number == current_invoice_number
    ):
        return False

    return any(
        candidate.get("selected_as_numero_fattura") is True
        and candidate.get("normalized_value") == page_invoice_number
        and int(candidate.get("anchor_quality") or 0) >= 4
        for candidate in page_manifest.get("candidate_tables", {}).get(
            "invoice_numbers",
            [],
        )
    )


def _is_bank_payment_summary_page(page_manifest: dict) -> bool:
    page_text = " ".join(
        span.get("text", "")
        for span in page_manifest.get("spans", [])
    )
    normalized = _normalize_manifest_text(page_text)
    return (
        "bonifico" in normalized
        and (
            "beneficiario" in normalized
            or "pagamento fattura" in normalized
            or ("identificativo" in normalized and "fiscale" in normalized)
        )
    )


def _changed_invoice_signals(
    page_signature: dict[str, str],
    current_invoice_signature: dict[str, str],
) -> list[str]:
    changed_signals = []
    for kind, value in page_signature.items():
        current_value = current_invoice_signature.get(kind)
        if current_value and value != current_value:
            changed_signals.append(kind)
        elif kind == "invoice_header" and value and not current_value:
            changed_signals.append(kind)

    return changed_signals


def _merge_invoice_signatures(
    current_invoice_signature: dict[str, str],
    page_signature: dict[str, str],
) -> dict[str, str]:
    merged_signature = dict(current_invoice_signature)
    for kind, value in page_signature.items():
        merged_signature.setdefault(kind, value)

    return merged_signature


def _viewer_invoice(
    invoice_index: int,
    page_manifests: list[dict],
    registry_cache: RegistryCache | None = None,
) -> dict:
    return {
        "invoice_id": f"invoice-{invoice_index:03d}",
        "display_number": invoice_index,
        "title": f"Fattura {invoice_index:03d}",
        "page_numbers": [page["page"] for page in page_manifests],
        "fields": _viewer_invoice_fields(page_manifests),
    }


def link_shared_payment_dates(
    invoices: list[dict],
    page_manifests: list[dict],
) -> None:
    payment_candidates = _shared_payment_candidates(page_manifests)
    if not payment_candidates:
        return

    for invoice in invoices:
        invoice_number = _invoice_field_value(invoice, "numero_fattura")
        if not invoice_number or _invoice_field_value(invoice, "pagata_il"):
            continue

        normalized_invoice_number = _normalize_invoice_date_link_text(invoice_number)
        matching_payment = max(
            [
                payment
                for payment in payment_candidates
                if normalized_invoice_number in payment["referenced_invoice_numbers"]
            ],
            key=lambda payment: payment["score"],
            default=None,
        )
        if matching_payment:
            _replace_invoice_field(invoice, "pagata_il", matching_payment)
            _add_invoice_page_number(invoice, matching_payment.get("page"))


def _shared_payment_candidates(page_manifests: list[dict]) -> list[dict]:
    candidates = []
    for page_manifest in page_manifests:
        referenced_invoice_numbers = _referenced_invoice_numbers_from_page(
            page_manifest
        )
        if len(referenced_invoice_numbers) < 2:
            continue

        for candidate in page_manifest.get("candidate_tables", {}).get(
            "invoice_dates",
            [],
        ):
            paid_score = _paid_date_score(candidate, page_manifest)
            if paid_score <= 0:
                continue

            value = candidate.get("normalized_value", "")
            if not value:
                continue

            candidates.append(
                {
                    "kind": "pagata_il",
                    "label": "Pagata il",
                    "value": value,
                    "score": paid_score + len(referenced_invoice_numbers) * 25,
                    "ocr_confidence": candidate.get("ocr_confidence"),
                    "page": page_manifest.get("page"),
                    "bbox": candidate.get("bbox"),
                    "role": "payment_event",
                    "referenced_invoice_numbers": referenced_invoice_numbers,
                }
            )

    return candidates


def _referenced_invoice_numbers_from_page(page_manifest: dict) -> set[str]:
    referenced_invoice_numbers = set()

    for table_name in ("invoice_numbers", "invoice_dates"):
        for candidate in page_manifest.get("candidate_tables", {}).get(table_name, []):
            referenced_invoice_numbers.update(_referenced_invoice_numbers(candidate))

    for span in page_manifest.get("spans", []):
        referenced_invoice_numbers.update(
            _referenced_invoice_numbers({"nearby_text": span.get("text", "")})
        )

    return referenced_invoice_numbers


def _referenced_invoice_numbers(candidate: dict) -> set[str]:
    text = " ".join(
        [
            candidate.get("nearby_text", ""),
            candidate.get("raw_text", ""),
        ]
    )
    referenced_invoice_numbers = set()

    for match in re.finditer(
        r"\bn\.?\s*([A-Za-z0-9][A-Za-z0-9./-]*)\s+del\s+"
        r"\d{1,2}[./-]\d{1,2}[./-](\d{2}|\d{4})\b",
        text,
        flags=re.IGNORECASE,
    ):
        invoice_number = _normalize_invoice_date_link_text(match.group(1))
        if not invoice_number:
            continue
        referenced_invoice_numbers.add(invoice_number)
        year = match.group(2)
        if len(year) == 2:
            year = f"20{year}"
        if not invoice_number.endswith(year):
            referenced_invoice_numbers.add(f"{invoice_number}{year}")

    for raw_token in re.findall(r"[A-Za-z0-9.]+", text):
        value = _normalize_invoice_date_link_text(raw_token)
        if value.startswith("N") and len(value) > 1:
            value = value[1:]

        if (
            6 <= len(value) <= 20
            and any(char.isalpha() for char in value)
            and any(char.isdigit() for char in value)
        ):
            referenced_invoice_numbers.add(value)

    return referenced_invoice_numbers


def _invoice_field_value(invoice: dict, kind: str) -> str:
    field = _invoice_field(invoice, kind)
    return field.get("value", "") if field else ""


def _invoice_field(invoice: dict, kind: str) -> dict | None:
    return next(
        (field for field in invoice.get("fields", []) if field.get("kind") == kind),
        None,
    )


def _replace_invoice_field(invoice: dict, kind: str, replacement: dict) -> None:
    field = _invoice_field(invoice, kind)
    if field is None:
        return

    for key in ("value", "score", "ocr_confidence", "page", "bbox", "role"):
        field[key] = replacement.get(key)


def _add_invoice_page_number(invoice: dict, page_number: int | None) -> None:
    if not page_number:
        return

    page_numbers = invoice.get("page_numbers", [])
    if page_number not in page_numbers:
        invoice["page_numbers"] = sorted([*page_numbers, page_number])


def _viewer_invoice_fields(page_manifests: list[dict]) -> list[dict]:
    targets = [
        target
        for page_manifest in page_manifests
        for target in page_manifest["targets"]
    ]
    reconciled_economic_targets = _reconcile_invoice_economic_targets(
        page_manifests,
        targets,
    )

    fields = []
    for kind, label in INVOICE_FIELD_DEFINITIONS:
        best_target = reconciled_economic_targets.get(kind)
        if best_target is None:
            best_target = _best_invoice_field_target(page_manifests, targets, kind)
        fields.append(
            {
                "kind": kind,
                "label": label,
                "value": best_target["value"] if best_target else "",
                "score": best_target["score"] if best_target else 0,
                "ocr_confidence": (
                    best_target.get("ocr_confidence") if best_target else None
                ),
                "page": best_target.get("page") if best_target else None,
                "bbox": best_target.get("bbox") if best_target else None,
                "role": best_target.get("role") if best_target else None,
            }
        )

    return fields


def _reconcile_invoice_economic_targets(
    page_manifests: list[dict],
    targets: list[dict],
) -> dict[str, dict]:
    """Promote direct cross-page evidence only when all accounting checks agree."""
    totals = _money_targets(
        targets,
        "economic_summary.totale_fattura.value",
    )
    net_totals = _money_targets(
        targets,
        "economic_summary.totale_netto_fattura.value",
    )
    taxable_amounts = _money_targets(
        targets,
        "economic_summary.totale_imponibile.value",
    )
    withholding_candidates = _direct_withholding_candidates(page_manifests, targets)
    combinations = []

    for total_target, total in totals:
        for net_target, net_total in net_totals:
            for withholding_target, withholding, percent in withholding_candidates:
                if not _money_matches_manifest(total - withholding, net_total):
                    continue

                matching_taxable_targets = (
                    [
                        (target, taxable)
                        for target, taxable in taxable_amounts
                        if _money_matches_manifest(
                            taxable * Decimal(percent) / Decimal("100"),
                            withholding,
                        )
                    ]
                    if percent is not None
                    else []
                )

                score = sum(
                    float(target.get("score", 0) or 0)
                    for target in (total_target, net_target, withholding_target)
                )
                if matching_taxable_targets:
                    score += max(
                        float(target.get("score", 0) or 0)
                        for target, _value in matching_taxable_targets
                    )
                combinations.append(
                    (
                        score,
                        total_target,
                        net_target,
                        withholding_target,
                    )
                )

    if not combinations:
        return {}

    _score, total_target, net_target, withholding_target = max(
        combinations,
        key=lambda combination: combination[0],
    )
    selected_pages = {
        target.get("page")
        for target in (total_target, net_target, withholding_target)
    }
    crosses_pages = len(selected_pages) > 1
    return {
        "economic_summary.totale_fattura.value": {
            **total_target,
            "role": (
                "invoice_reconciled"
                if crosses_pages
                else total_target.get("role")
            ),
        },
        "economic_summary.ritenuta.value": withholding_target,
        "economic_summary.totale_netto_fattura.value": {
            **net_target,
            "role": (
                "invoice_reconciled"
                if crosses_pages
                else net_target.get("role")
            ),
        },
    }


def _money_targets(targets: list[dict], kind: str) -> list[tuple[dict, Decimal]]:
    candidates = []
    for target in targets:
        if target.get("kind") != kind:
            continue
        value = _manifest_money_value(str(target.get("value", "")))
        if value is not None:
            candidates.append((target, abs(_money(value))))
    return candidates


def _direct_withholding_candidates(
    page_manifests: list[dict],
    targets: list[dict],
) -> list[tuple[dict, Decimal, int | None]]:
    candidates = []
    direct_targets = _money_targets(
        targets,
        "economic_summary.ritenuta.value",
    )
    for page_manifest in page_manifests:
        page_number = page_manifest.get("page")
        rows = page_manifest.get("candidate_tables", {}).get(
            "economic_summary_resolved_rows",
            [],
        )
        for row in rows:
            if row.get("ritenuta_source") != "direct":
                continue
            withholding = _manifest_money_value(str(row.get("ritenuta_value", "")))
            percent = _ritenuta_percent_value(str(row.get("ritenuta_percentuale", "")))
            if withholding is None:
                continue
            withholding = abs(_money(withholding))
            source_target = max(
                [
                    target
                    for target, target_value in direct_targets
                    if target.get("page") == page_number
                    and _money_matches_manifest(target_value, withholding)
                ],
                key=lambda target: target.get("score", 0),
                default=None,
            )
            matching_token = _money_token_matching_value(page_manifest, withholding)
            candidates.append(
                (
                    {
                        "kind": "economic_summary.ritenuta.value",
                        "value": _format_manifest_money(withholding),
                        "score": (
                            float(source_target.get("score", 0) or 0)
                            if source_target
                            else 0
                        )
                        + 120,
                        "ocr_confidence": (
                            source_target.get("ocr_confidence")
                            if source_target
                            else matching_token.get("confidence")
                            if matching_token
                            else None
                        ),
                        "page": page_number,
                        "bbox": (
                            source_target.get("bbox")
                            if source_target
                            else matching_token.get("bbox")
                            if matching_token
                            else row.get("bbox")
                        ),
                        "role": (
                            "resolved"
                            if row.get("math_check") == "ok"
                            else "invoice_reconciled"
                        ),
                    },
                    withholding,
                    percent,
                )
            )
    return candidates


def _ritenuta_percent_value(value: str) -> int | None:
    match = re.fullmatch(r"\s*(0|4|10)\s*%?\s*", value)
    return int(match.group(1)) if match else None


def _money_matches_manifest(left: Decimal, right: Decimal) -> bool:
    return abs(_money(left) - _money(right)) <= Decimal("0.01")


def build_viewer_invoice_fields_for_pages(
    page_manifests: list[dict],
    page_numbers: list[int],
) -> list[dict]:
    """Rebuild invoice fields for an explicit, user-approved page group."""
    selected_page_numbers = set(page_numbers)
    selected_pages = [
        page_manifest
        for page_manifest in page_manifests
        if page_manifest.get("page") in selected_page_numbers
    ]
    return _viewer_invoice_fields(selected_pages)


def _best_invoice_field_target(
    page_manifests: list[dict],
    targets: list[dict],
    kind: str,
) -> dict | None:
    if kind == "economic_summary.ritenuta.value":
        return _best_ritenuta_value_target(page_manifests, targets)

    if kind == "data_fattura":
        return _best_invoice_date_target(page_manifests, targets)

    if kind == "pagata_il":
        return _best_paid_date_target(page_manifests, targets)

    if kind == "economic_summary.totale_netto_fattura.value":
        return _best_net_total_target(page_manifests, targets)

    candidates = [target for target in targets if target["kind"] == kind]
    return max(candidates, key=lambda target: target["score"], default=None)


def _best_invoice_date_target(
    page_manifests: list[dict],
    targets: list[dict],
) -> dict | None:
    invoice_number = _best_invoice_number_for_date_link(targets)
    candidates = []
    for page_manifest in page_manifests:
        header_date_values = {
            region.get("values", {}).get("data_fattura", "")
            for region in page_manifest.get("invoice_header_regions", [])
        }
        invoice_dates = page_manifest.get("candidate_tables", {}).get(
            "invoice_dates",
            [],
        )
        for candidate in invoice_dates:
            value = candidate.get("normalized_value", "")
            if not value:
                continue
            priority = _invoice_date_candidate_priority(candidate, page_manifest)
            if priority < 0:
                continue

            source_target = _matching_target(
                targets=targets,
                kind="data_fattura",
                page=page_manifest.get("page"),
                value=value,
                bbox=candidate.get("bbox"),
            )
            candidates.append(
                {
                    "kind": "data_fattura",
                    "value": value,
                    "score": candidate.get("score", 0),
                    "ocr_confidence": (
                        source_target.get("ocr_confidence")
                        if source_target
                        else candidate.get("ocr_confidence")
                    ),
                    "page": page_manifest.get("page"),
                    "bbox": candidate.get("bbox"),
                    "role": "target",
                    "priority": priority,
                    "matches_invoice_header": value in header_date_values,
                    "follows_invoice_number": _date_candidate_follows_invoice_number(
                        page_manifest,
                        candidate,
                        invoice_number,
                    ),
                }
            )

    if candidates:
        return max(
            candidates,
            key=lambda candidate: (
                candidate["priority"],
                candidate["follows_invoice_number"],
                candidate["matches_invoice_header"],
                candidate["score"],
            ),
        )

    fallback_candidates = [
        target for target in targets if target["kind"] == "data_fattura"
    ]
    return max(fallback_candidates, key=lambda target: target["score"], default=None)


def _invoice_date_candidate_priority(
    candidate: dict,
    page_manifest: dict | None = None,
) -> int:
    anchor_context = candidate.get("anchor_context", "")
    negative_context = _invoice_date_negative_context(candidate)
    if "entro" in negative_context:
        return -1
    if "riferimento normativo" in negative_context:
        return -1
    if "periodo fatturazione" in negative_context:
        return -1
    if "data pagamento" in negative_context:
        return -1

    row_text = _payment_date_row_text(candidate, page_manifest)
    if row_text:
        row_words = row_text.split()
        if _has_payment_execution_context(row_words):
            return -1
        if (
            _has_adjacent_words(row_words, "data", "documento")
            or _has_adjacent_words(row_words, "data", "doc")
            or _has_adjacent_words(row_words, "data", "fattura")
        ):
            return 5
        if "fattura" in row_words and "del" in row_words:
            return 4
        if "data" in row_words:
            return 1
        return 0

    if "data documento" in anchor_context or "data doc" in anchor_context:
        return 4

    if "del" in anchor_context and "fattura" in anchor_context:
        return 3

    if "del" in anchor_context:
        return 2

    if "fattura" in anchor_context or "numero_fattura" in anchor_context:
        return 1

    return 0


def _invoice_date_negative_context(candidate: dict) -> str:
    negative_context = str(candidate.get("negative_context") or "")
    if negative_context:
        return negative_context

    nearby_text = _normalize_payment_context_text(candidate.get("nearby_text", ""))
    words = nearby_text.split()
    compact_words = {re.sub(r"[^0-9a-zA-Z]+", "", word) for word in words}
    has_law_reference = any(
        word.startswith("l") and word[1:].isdigit() for word in compact_words
    ) or any(left == "l" and right.isdigit() for left, right in zip(words, words[1:]))
    if (
        has_law_reference
        or "contribuenti" in words
        or "forfetari" in words
        or "forfettari" in words
    ):
        return "riferimento normativo"

    if (
        _has_adjacent_words(words, "periodo", "fatturazione")
        or _has_words_in_order(words, ["periodo", "di", "fatturazione"])
        or ("fatturazione" in words and ("dal" in words or "al" in words))
    ):
        return "periodo fatturazione"

    return ""


def _best_invoice_number_for_date_link(targets: list[dict]) -> str:
    invoice_number = max(
        [target for target in targets if target["kind"] == "numero_fattura"],
        key=lambda target: target["score"],
        default=None,
    )
    if invoice_number is None:
        return ""

    return _normalize_invoice_date_link_text(invoice_number.get("value", ""))


def _best_paid_date_target(page_manifests: list[dict], targets: list[dict]) -> dict | None:
    invoice_date_target = _best_invoice_date_target(page_manifests, targets)
    invoice_date_value = invoice_date_target.get("value") if invoice_date_target else ""
    candidates = []

    for page_manifest in page_manifests:
        invoice_dates = page_manifest.get("candidate_tables", {}).get(
            "invoice_dates",
            [],
        )
        for candidate in invoice_dates:
            value = candidate.get("normalized_value", "")
            if not value:
                continue

            if value == invoice_date_value:
                row = _payment_date_row_text(candidate, page_manifest)
                context = row or _normalize_payment_context_text(
                    candidate.get("nearby_text", "")
                )
                words = context.split()
                same_occurrence = (
                    invoice_date_target.get("page") == page_manifest.get("page")
                    and invoice_date_target.get("bbox") == candidate.get("bbox")
                )
                if same_occurrence or not _has_payment_execution_context(words):
                    continue

            paid_score = _paid_date_score(candidate, page_manifest)
            if paid_score <= 0:
                continue

            candidates.append(
                {
                    "kind": "pagata_il",
                    "value": value,
                    "score": paid_score,
                    "ocr_confidence": candidate.get("ocr_confidence"),
                    "page": page_manifest.get("page"),
                    "bbox": candidate.get("bbox"),
                    "role": "target",
                }
            )

    return max(candidates, key=lambda target: target["score"], default=None)


def _best_net_total_target(page_manifests: list[dict], targets: list[dict]) -> dict | None:
    candidates = [
        target
        for target in targets
        if target["kind"] == "economic_summary.totale_netto_fattura.value"
    ]
    if not candidates:
        return None

    expected_net_total = _expected_net_total_from_targets(page_manifests, targets)
    if expected_net_total is None:
        return max(candidates, key=lambda target: target["score"], default=None)

    return max(
        candidates,
        key=lambda target: (
            _target_money_matches(target, expected_net_total),
            target["score"],
        ),
    )


def _best_ritenuta_value_target(
    page_manifests: list[dict],
    _targets: list[dict],
) -> dict | None:
    candidates = []
    for page_manifest in page_manifests:
        resolved_rows = page_manifest.get("candidate_tables", {}).get(
            "economic_summary_resolved_rows",
            [],
        )
        for row in resolved_rows:
            if row.get("math_check") != "ok":
                continue

            ritenuta_value = _manifest_money_value(str(row.get("ritenuta_value", "")))
            if ritenuta_value is None:
                continue
            ritenuta_amount = abs(_money(ritenuta_value))
            matching_token = _money_token_matching_value(
                page_manifest,
                ritenuta_amount,
            )
            bbox = matching_token.get("bbox") if matching_token else row.get("bbox")
            confidence = (
                matching_token.get("confidence")
                if matching_token
                else None
            )
            candidates.append(
                {
                    "kind": "economic_summary.ritenuta.value",
                    "value": _format_manifest_money(ritenuta_amount),
                    "score": 220,
                    "ocr_confidence": confidence,
                    "page": page_manifest.get("page"),
                    "bbox": bbox,
                    "role": "resolved",
                }
            )

    return max(candidates, key=lambda target: target["score"], default=None)


def _valid_ritenuta_percentuale(value: str) -> bool:
    match = re.fullmatch(r"\s*(0|4|10)\s*%?\s*", value)
    return match is not None


def _best_ritenuta_amount_target(
    page_manifests: list[dict],
) -> dict | None:
    candidates = []
    for page_manifest in page_manifests:
        resolved_rows = page_manifest.get("candidate_tables", {}).get(
            "economic_summary_resolved_rows",
            [],
        )
        for row in resolved_rows:
            if row.get("math_check") != "ok":
                continue
            ritenuta_value = _manifest_money_value(str(row.get("ritenuta_value", "")))
            if ritenuta_value is None:
                continue
            matching_token = _money_token_matching_value(
                page_manifest,
                abs(ritenuta_value),
            )
            candidates.append(
                {
                    "kind": "economic_summary.ritenuta.value",
                    "value": _format_manifest_money(abs(ritenuta_value)),
                    "score": 90,
                    "ocr_confidence": (
                        matching_token.get("confidence")
                        if matching_token
                        else None
                    ),
                    "page": page_manifest.get("page"),
                    "bbox": matching_token.get("bbox") if matching_token else row.get("bbox"),
                    "role": "resolved_amount",
                }
            )

    return max(candidates, key=lambda target: target["score"], default=None)


def _expected_net_total_from_targets(
    page_manifests: list[dict],
    targets: list[dict],
) -> Decimal | None:
    total_invoice = _best_money_target(
        targets,
        "economic_summary.totale_fattura.value",
    )
    withholding_target = _best_ritenuta_amount_target(page_manifests)
    withholding = (
        _manifest_money_value(str(withholding_target.get("value", "")))
        if withholding_target
        else _best_money_target(
            [
                target
                for target in targets
                if not _valid_ritenuta_percentuale(str(target.get("value", "")))
            ],
            "economic_summary.ritenuta.value",
        )
    )
    if total_invoice is None or withholding is None:
        return None

    return _money(total_invoice - abs(withholding))


def _best_money_target(targets: list[dict], kind: str) -> Decimal | None:
    candidates = [
        (target, _manifest_money_value(target.get("value", "")))
        for target in targets
        if target["kind"] == kind
    ]
    valid_candidates = [
        (target, value)
        for target, value in candidates
        if value is not None
    ]
    if not valid_candidates:
        return None

    _target, value = max(
        valid_candidates,
        key=lambda item: item[0].get("score", 0),
    )
    return value


def _money_token_matching_value(page_manifest: dict, expected_value: Decimal) -> dict | None:
    candidates = [
        token
        for token in page_manifest.get("tokens", [])
        if _manifest_money_value(token.get("text", "")) is not None
        and abs(_money(_manifest_money_value(token.get("text", "")))) == _money(expected_value)
    ]
    return max(candidates, key=lambda token: token.get("confidence") or 0, default=None)


def _format_manifest_money(value: Decimal) -> str:
    return f"{_money(value):.2f}".replace(".", ",")


def _target_money_matches(target: dict, expected_value: Decimal) -> bool:
    value = _manifest_money_value(target.get("value", ""))
    if value is None:
        return False

    return abs(_money(value) - expected_value) <= Decimal("0.01")


def _manifest_money_value(value: str) -> Decimal | None:
    match = re.search(r"-?\d+(?:[.,]\d{2})", value)
    if not match:
        return None

    cleaned_value = match.group(0)
    if not cleaned_value:
        return None

    if "," in cleaned_value:
        cleaned_value = cleaned_value.replace(".", "").replace(",", ".")

    try:
        return Decimal(cleaned_value)
    except InvalidOperation:
        return None


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _paid_date_score(candidate: dict, page_manifest: dict | None = None) -> float:
    nearby_text = _normalize_payment_context_text(candidate.get("nearby_text", ""))
    row_text = _payment_date_row_text(candidate, page_manifest)
    role_words = (row_text or nearby_text).split()

    has_debit_label = _has_words_in_order(
        role_words,
        ["data", "di", "addebito"],
    ) or _has_adjacent_words(role_words, "data", "addebito")
    has_credit_label = _has_words_in_order(
        role_words,
        ["data", "di", "accredito"],
    ) or _has_adjacent_words(role_words, "data", "accredito")
    if has_credit_label and not has_debit_label:
        return 0.0

    has_execution_label = _has_adjacent_words(
        role_words,
        "data",
        "esecuzione",
    )
    if (
        row_text
        and "fattura" in role_words
        and "del" in role_words
        and not has_debit_label
        and not has_execution_label
    ):
        return 0.0

    words = nearby_text.split()
    score = 0.0

    if has_debit_label:
        score += 250

    if has_execution_label:
        score += 150
    elif "esecuzione" in role_words:
        score += 115

    if _has_adjacent_words(role_words, "data", "ora"):
        score += 95

    if "bonifico" in words:
        score += 70
    if _has_words_in_order(words, ["credit", "transfer"]):
        score += 60
    if "disposizione" in words or "disposizioni" in words:
        score += 45
    if "spedita" in words or "inoltrata" in words:
        score += 35
    if "pagamento" in words or "pagam" in words:
        score += 30
    if "intesa" in words and "sanpaolo" in words:
        score += 25

    if _has_adjacent_words(words, "data", "scadenza") or "scadenza" in words:
        score -= 100
    if "fattura" in words and score < 150:
        score -= 45
    if "entro" in words:
        score -= 70

    extractor_score = float(candidate.get("score", 0) or 0)
    score += min(extractor_score, 100) * 0.15
    ocr_confidence = candidate.get("ocr_confidence")
    if isinstance(ocr_confidence, int | float):
        score += ocr_confidence * 20

    if score < 120:
        return 0.0

    return round(score, 2)


def _payment_date_row_text(
    candidate: dict,
    page_manifest: dict | None,
) -> str:
    if not page_manifest:
        return ""

    candidate_bbox = candidate.get("bbox")
    if not candidate_bbox:
        return ""

    row_parts = []
    for span in page_manifest.get("spans", []):
        span_bbox = span.get("bbox")
        if not span_bbox or not _vertically_overlapping(candidate_bbox, span_bbox):
            continue
        row_parts.append(span.get("text", ""))

    return _normalize_payment_context_text(" ".join(row_parts))


def _vertically_overlapping(left: dict, right: dict) -> bool:
    overlap = min(left["y2"], right["y2"]) - max(left["y1"], right["y1"])
    if overlap <= 0:
        return False

    smaller_height = min(left["y2"] - left["y1"], right["y2"] - right["y1"])
    return smaller_height > 0 and overlap / smaller_height >= 0.4


def _normalize_payment_context_text(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zA-Z]+", " ", text.lower()).split())


def _has_payment_execution_context(words: list[str]) -> bool:
    return (
        _has_adjacent_words(words, "data", "esecuzione")
        or _has_adjacent_words(words, "data", "addebito")
        or _has_words_in_order(words, ["data", "di", "addebito"])
    )


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


def _date_candidate_follows_invoice_number(
    page_manifest: dict,
    candidate: dict,
    invoice_number: str,
) -> bool:
    if not invoice_number:
        return False

    candidate_bbox = candidate.get("bbox")
    if not candidate_bbox:
        return False

    for token in page_manifest.get("tokens", []):
        token_text = _normalize_invoice_date_link_text(token.get("text", ""))
        if invoice_number not in token_text:
            continue

        token_bbox = token.get("bbox", {})
        if not token_bbox:
            continue

        vertically_aligned = (
            token_bbox.get("y1", 0) <= candidate_bbox.get("y2", 0)
            and token_bbox.get("y2", 0) >= candidate_bbox.get("y1", 0)
        )
        is_left_of_date = token_bbox.get("x2", 0) <= candidate_bbox.get("x1", 0) + 8
        if vertically_aligned and is_left_of_date:
            return True

    return False


def _normalize_invoice_date_link_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", text).upper()


def _matching_target(
    targets: list[dict],
    kind: str,
    page: int | None,
    value: str,
    bbox: dict | None,
) -> dict | None:
    candidates = [
        target
        for target in targets
        if target.get("kind") == kind
        and target.get("page") == page
        and target.get("value") == value
    ]
    if not candidates:
        return None

    if bbox:
        bbox_match = next(
            (
                target
                for target in candidates
                if _same_bbox(target.get("bbox"), bbox)
            ),
            None,
        )
        if bbox_match:
            return bbox_match

    return max(candidates, key=lambda target: target.get("score", 0), default=None)


def _same_bbox(left: dict | None, right: dict | None) -> bool:
    if not left or not right:
        return False

    return all(
        abs(left.get(key, 0) - right.get(key, 0)) <= 1
        for key in ("x1", "y1", "x2", "y2")
    )


def _invoice_field_value_from_page(page_manifest: dict, kind: str) -> str | None:
    if kind == "economic_summary.ritenuta.value":
        target = _best_ritenuta_value_target(
            [page_manifest],
            page_manifest.get("targets", []),
        )
        return target["value"] if target else None

    return _best_target_value(page_manifest, kind)


def _best_target_value(page_manifest: dict, kind: str) -> str | None:
    best_target = _best_target(page_manifest, kind)
    if not best_target:
        return None

    return best_target["value"]


def _best_target(page_manifest: dict, kind: str) -> dict | None:
    candidates = [
        target for target in page_manifest["targets"] if target["kind"] == kind
    ]
    return max(candidates, key=lambda target: target["score"], default=None)
