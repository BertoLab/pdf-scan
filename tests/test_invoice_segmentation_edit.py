import pytest

from estrattore.invoice_segmentation_edit import (
    SegmentationEditError,
    move_boundary_page,
    split_invoice,
)


def _field(kind: str, value: str, page: int) -> dict:
    return {
        "kind": kind,
        "label": kind,
        "value": value,
        "score": 0.9,
        "page": page,
        "bbox": {"x1": 0, "y1": 0, "x2": 1, "y2": 1},
    }


def _manifest(page_groups: list[list[int]]) -> dict:
    pages = [
        {
            "page": page_number,
            "targets": [_field("numero_fattura", f"N-{page_number}", page_number)],
        }
        for page_number in range(1, sum(len(group) for group in page_groups) + 1)
    ]
    return {
        "pages": pages,
        "invoices": [
            {
                "invoice_id": f"invoice-{index:03d}",
                "display_number": index,
                "title": f"Fattura {index:03d}",
                "page_numbers": group,
                "fields": [],
            }
            for index, group in enumerate(page_groups, start=1)
        ],
    }


def test_single_page_boundary_move_removes_empty_invoice() -> None:
    result = move_boundary_page(
        _manifest([[1, 2], [3], [4]]),
        "invoice-002",
        "first_to_previous",
    )

    assert [invoice["invoice_id"] for invoice in result.manifest["invoices"]] == [
        "invoice-001",
        "invoice-003",
    ]
    assert result.manifest["invoices"][0]["page_numbers"] == [1, 2, 3]
    assert result.removed_invoice_id == "invoice-002"
    assert result.selected_invoice_id == "invoice-001"
    assert result.numbering_threshold == 2
    assert result.numbering_delta == -1


def test_boundary_move_rebuilds_fields_from_the_remaining_pages() -> None:
    result = move_boundary_page(
        _manifest([[1, 2], [3, 4]]),
        "invoice-002",
        "first_to_previous",
    )

    first, second = result.manifest["invoices"]
    assert first["page_numbers"] == [1, 2, 3]
    assert second["page_numbers"] == [4]
    assert next(field for field in second["fields"] if field["kind"] == "numero_fattura")[
        "value"
    ] == "N-4"


def test_split_creates_a_new_stable_invoice_between_existing_invoices() -> None:
    result = split_invoice(
        _manifest([[1, 2, 3, 4], [5]]),
        "invoice-001",
        2,
        "invoice-manual-stable",
    )

    assert [invoice["invoice_id"] for invoice in result.manifest["invoices"]] == [
        "invoice-001",
        "invoice-manual-stable",
        "invoice-002",
    ]
    assert [invoice["page_numbers"] for invoice in result.manifest["invoices"]] == [
        [1, 2],
        [3, 4],
        [5],
    ]
    assert result.numbering_threshold == 1
    assert result.numbering_delta == 1


@pytest.mark.parametrize("after_position", [0, 4])
def test_split_rejects_boundaries_outside_the_invoice(after_position: int) -> None:
    with pytest.raises(SegmentationEditError, match="punto di divisione"):
        split_invoice(
            _manifest([[1, 2, 3, 4]]),
            "invoice-001",
            after_position,
            "invoice-manual-stable",
        )


def test_shared_source_page_is_an_independent_invoice_membership() -> None:
    manifest = _manifest([[1, 2], [3]])
    manifest["invoices"][0]["page_numbers"].append(3)

    result = move_boundary_page(manifest, "invoice-002", "first_to_previous")

    assert [invoice["invoice_id"] for invoice in result.manifest["invoices"]] == [
        "invoice-001"
    ]
    assert result.manifest["invoices"][0]["page_numbers"] == [1, 2, 3]
    assert result.removed_invoice_id == "invoice-002"


def test_split_preserves_a_source_page_copy_owned_by_another_invoice() -> None:
    manifest = _manifest([[1, 2, 3], [4]])
    manifest["invoices"][1]["page_numbers"] = [3, 4]

    result = split_invoice(
        manifest,
        "invoice-001",
        2,
        "invoice-manual-stable",
    )

    assert [invoice["page_numbers"] for invoice in result.manifest["invoices"]] == [
        [1, 2],
        [3],
        [3, 4],
    ]


def test_split_reconciles_a_bank_page_shared_by_both_invoice_numbers() -> None:
    manifest = {
        "pages": [
            {
                "page": 1,
                "spans": [],
                "tokens": [],
                "targets": [_field("numero_fattura", "7/2025", 1)],
            },
            {
                "page": 2,
                "spans": [],
                "tokens": [],
                "targets": [_field("numero_fattura", "202/2024", 2)],
            },
            {
                "page": 3,
                "spans": [
                    {
                        "text": "Data di addebito",
                        "bbox": {"x1": 100, "y1": 100, "x2": 250, "y2": 120},
                    },
                    {
                        "text": (
                            "Pagamento fatt. n.202 del 09-12-24 e "
                            "n.7 del 10-01-25"
                        ),
                        "bbox": {"x1": 100, "y1": 200, "x2": 800, "y2": 220},
                    },
                ],
                "tokens": [],
                "targets": [],
                "candidate_tables": {
                    "invoice_dates": [
                        {
                            "normalized_value": "2025-01-16",
                            "score": 70,
                            "ocr_confidence": 0.96,
                            "nearby_text": "Bonifico EUR",
                            "bbox": {"x1": 900, "y1": 100, "x2": 1000, "y2": 120},
                        }
                    ]
                },
            },
        ],
        "invoices": [
            {
                "invoice_id": "invoice-001",
                "display_number": 1,
                "title": "Fattura 001",
                "page_numbers": [1, 2, 3],
                "fields": [],
            }
        ],
    }

    result = split_invoice(
        manifest,
        "invoice-001",
        1,
        "invoice-manual-stable",
    )

    assert [invoice["page_numbers"] for invoice in result.manifest["invoices"]] == [
        [1, 3],
        [2, 3],
    ]
