from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from pdf2image import convert_from_path

from estrattore.runtime_paths import bundled_assets_root

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}


def load_scan(file_path: Path) -> list[Image.Image] | None:
    if file_path.suffix.lower() == ".pdf":
        poppler_path = resolve_poppler_path()
        return convert_from_path(file_path, poppler_path=poppler_path)

    if file_path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(file_path) as image:
            return [image.copy()]

    return None


def resolve_poppler_path() -> Path | None:
    bin_dir = bundled_assets_root() / "vendor" / "poppler" / "bin"
    if all((bin_dir / name).is_file() for name in ("pdfinfo.exe", "pdftoppm.exe")):
        return bin_dir
    if getattr(sys, "frozen", False):
        raise RuntimeError("Conversione PDF non disponibile: manca Poppler nel pacchetto dell'app.")
    return None


def preprocessing_image(
    images: list[Image.Image],
    enable_binarization: bool = False,
) -> list[Image.Image]:
    threshold = 128
    preprocessed_images = []

    for img in images:
        img = img.convert("L")
        if enable_binarization:
            img = img.point(lambda x: 0 if x < threshold else 255, "1")
        preprocessed_images.append(img)

    return preprocessed_images
