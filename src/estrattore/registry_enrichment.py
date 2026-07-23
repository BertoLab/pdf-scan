from __future__ import annotations

from dataclasses import dataclass

from estrattore.fiscal_validation import (
    is_valid_italian_vat_number,
    normalize_italian_tax_code,
    normalize_italian_vat_number,
)
from estrattore.registry import RegistryCache, RegistryRecord, normalize_registry_name


ISSUER_NAME_FIELD = "nome_emittente"
ISSUER_TAX_CODE_FIELD = "codice_fiscale_emittente"
ISSUER_VAT_FIELD = "p_iva"
CONDOMINIUM_NAME_FIELD = "condominio"
CONDOMINIUM_TAX_CODE_FIELD = "codice_fiscale"


@dataclass(frozen=True)
class _RegistryFieldSpec:
    invoice_field: str
    registry_field: str


@dataclass(frozen=True)
class _RegistryMatchGroup:
    name: str
    field_specs: tuple[_RegistryFieldSpec, ...]


@dataclass(frozen=True)
class _RegistryEvidence:
    record: RegistryRecord
    matched_field: str


@dataclass(frozen=True)
class _RegistryMatchDecision:
    record: RegistryRecord | None
    matched_fields: tuple[str, ...] = ()
    conflict: bool = False


SUPPLIER_GROUP = _RegistryMatchGroup(
    name="supplier",
    field_specs=(
        _RegistryFieldSpec(ISSUER_NAME_FIELD, "nominativo"),
        _RegistryFieldSpec(ISSUER_TAX_CODE_FIELD, "codice_fiscale"),
        _RegistryFieldSpec(ISSUER_VAT_FIELD, "partita_iva"),
    ),
)
CONDOMINIUM_GROUP = _RegistryMatchGroup(
    name="condominium",
    field_specs=(
        _RegistryFieldSpec(CONDOMINIUM_NAME_FIELD, "nominativo"),
        _RegistryFieldSpec(CONDOMINIUM_TAX_CODE_FIELD, "codice_fiscale"),
    ),
)


def enrich_invoice_fields_from_registry(
    fields: list[dict],
    registry_cache: RegistryCache | None,
) -> list[dict]:
    if registry_cache is None:
        return fields

    enriched_fields = [dict(field) for field in fields]
    by_kind = {field.get("kind"): field for field in enriched_fields}

    for group in (SUPPLIER_GROUP, CONDOMINIUM_GROUP):
        decision = _registry_match_decision(
            group=group,
            fields=by_kind,
            records=registry_cache.records,
        )
        if decision.conflict:
            _mark_registry_conflict(by_kind, group)
            continue

        if decision.record is None:
            continue

        _complete_group_fields_from_registry(by_kind, group, decision)

    return enriched_fields


def _registry_match_decision(
    group: _RegistryMatchGroup,
    fields: dict[str | None, dict],
    records: list[RegistryRecord],
) -> _RegistryMatchDecision:
    evidences = _registry_evidences(group, fields, records)
    if not evidences:
        return _RegistryMatchDecision(record=None)

    evidences_by_record: dict[tuple[str, int], list[_RegistryEvidence]] = {}
    for evidence in evidences:
        evidences_by_record.setdefault(_record_key(evidence.record), []).append(evidence)

    if len(evidences_by_record) != 1:
        return _RegistryMatchDecision(record=None, conflict=True)

    record_evidences = next(iter(evidences_by_record.values()))
    matched_fields = tuple(
        sorted({evidence.matched_field for evidence in record_evidences})
    )
    return _RegistryMatchDecision(
        record=record_evidences[0].record,
        matched_fields=matched_fields,
    )


def _registry_evidences(
    group: _RegistryMatchGroup,
    fields: dict[str | None, dict],
    records: list[RegistryRecord],
) -> list[_RegistryEvidence]:
    match_values = {
        spec.registry_field: _normalized_match_value(
            registry_field=spec.registry_field,
            value=_field_value(fields.get(spec.invoice_field)),
        )
        for spec in group.field_specs
    }
    match_values = {
        registry_field: value
        for registry_field, value in match_values.items()
        if value
    }
    if not match_values:
        return []

    evidences = []
    for record in records:
        for registry_field, value in match_values.items():
            if _record_value(record, registry_field) == value:
                evidences.append(
                    _RegistryEvidence(
                        record=record,
                        matched_field=registry_field,
                    )
                )

    return evidences


def _complete_group_fields_from_registry(
    fields: dict[str | None, dict],
    group: _RegistryMatchGroup,
    decision: _RegistryMatchDecision,
) -> None:
    if decision.record is None:
        return

    for spec in group.field_specs:
        _complete_field_from_registry(
            field=fields.get(spec.invoice_field),
            registry_value=_valid_record_value(decision.record, spec.registry_field),
            group=group,
            decision=decision,
        )


def _complete_field_from_registry(
    field: dict | None,
    registry_value: str | None,
    group: _RegistryMatchGroup,
    decision: _RegistryMatchDecision,
) -> None:
    if field is None or registry_value is None:
        return

    current_value = str(field.get("value") or "").strip()
    if current_value and _field_value_is_valid(field.get("kind"), current_value):
        return

    field["value"] = registry_value
    field["score"] = max(float(field.get("score") or 0), 1000.0)
    field["role"] = "registry_enriched"
    field["registry_match_group"] = group.name
    field["registry_match_fields"] = list(decision.matched_fields)
    field["registry_id"] = decision.record.registry_id if decision.record else None
    field["registry_source_row"] = (
        decision.record.source_row if decision.record else None
    )


def _mark_registry_conflict(
    fields: dict[str | None, dict],
    group: _RegistryMatchGroup,
) -> None:
    for spec in group.field_specs:
        field = fields.get(spec.invoice_field)
        if field is None:
            continue

        field["registry_match_group"] = group.name
        field["registry_conflict"] = True


def _field_value_is_valid(kind: str | None, value: str) -> bool:
    if kind in {ISSUER_NAME_FIELD, CONDOMINIUM_NAME_FIELD}:
        return bool(normalize_registry_name(value))

    if kind in {ISSUER_TAX_CODE_FIELD, CONDOMINIUM_TAX_CODE_FIELD}:
        return normalize_italian_tax_code(value) is not None

    if kind == ISSUER_VAT_FIELD:
        return is_valid_italian_vat_number(value)

    return bool(value.strip())


def _normalized_match_value(registry_field: str, value: str) -> str | None:
    if registry_field == "nominativo":
        normalized_name = normalize_registry_name(value)
        return normalized_name or None

    if registry_field == "codice_fiscale":
        return normalize_italian_tax_code(value)

    if registry_field == "partita_iva":
        return normalize_italian_vat_number(value)

    return None


def _valid_record_value(record: RegistryRecord, registry_field: str) -> str | None:
    if registry_field == "nominativo":
        return record.normalized_name or None

    if registry_field == "codice_fiscale":
        if record.codice_fiscale is None:
            return None
        return (
            record.codice_fiscale
            if normalize_italian_tax_code(record.codice_fiscale)
            else None
        )

    if registry_field == "partita_iva":
        if record.partita_iva is None:
            return None
        return record.partita_iva if is_valid_italian_vat_number(record.partita_iva) else None

    return None


def _record_value(record: RegistryRecord, registry_field: str) -> str | None:
    if registry_field == "nominativo":
        return record.normalized_name or None

    if registry_field == "codice_fiscale":
        return record.codice_fiscale

    if registry_field == "partita_iva":
        return record.partita_iva

    return None


def _record_key(record: RegistryRecord) -> tuple[str, int]:
    return record.registry_id, record.source_row


def _field_value(field: dict | None) -> str:
    if field is None:
        return ""

    return str(field.get("value") or "").strip()
