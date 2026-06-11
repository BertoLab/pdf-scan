from __future__ import annotations

from pathlib import Path

from PIL import Image

from estrattore.model import DocumentPage, DocumentRepresentation, PerceptionBackend


def analyze_pages(
    images: list[Image.Image],
    source_file: Path,
    backend: PerceptionBackend = PerceptionBackend.EMPTY,
    ocr_lang: str | None = None,
    ocr_profile: str = "fast",
    enable_crop_ocr: bool = True,
) -> DocumentRepresentation:
    if backend == PerceptionBackend.TESSERACT:
        from estrattore.tesseract_adapter import analyze_pages_with_tesseract

        return analyze_pages_with_tesseract(
            images,
            source_file,
            lang=ocr_lang or "ita",
            ocr_profile=ocr_profile,
            enable_crop_ocr=enable_crop_ocr,
        )

    pages = [
        DocumentPage(
            page_number=index,
            width=image.width,
            height=image.height,
        )
        for index, image in enumerate(images, start=1)
    ]

    return DocumentRepresentation(
        source_file=source_file,
        pages=pages,
        backend=backend,
    )
