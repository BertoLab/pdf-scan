from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from estrattore import io, tesseract_adapter


BUILD_DIR = Path(__file__).resolve().parents[1] / "build"
ASSETS_ROOT = BUILD_DIR / "desktop-assets"


def main() -> None:
    if not (ASSETS_ROOT / "vendor" / "poppler" / "bin" / "pdfinfo.exe").is_file():
        raise SystemExit("Run packaging/prepare_native.py first")

    with tempfile.TemporaryDirectory(prefix="native smoke ", dir=BUILD_DIR) as temporary:
        pdf = Path(temporary) / "ocr test.pdf"
        image = Image.new("RGB", (1200, 300), "white")
        font = ImageFont.truetype("arial.ttf", 72)
        ImageDraw.Draw(image).text((75, 85), "FATTURA 123", fill="black", font=font)
        image.save(pdf, "PDF", resolution=200)

        with patch.object(io, "bundled_assets_root", return_value=ASSETS_ROOT), patch.object(
            tesseract_adapter, "bundled_assets_root", return_value=ASSETS_ROOT
        ), patch.object(io.sys, "frozen", True, create=True):
            pages = io.load_scan(pdf)
            assert pages is not None and len(pages) == 1
            tesseract_adapter._configure_tesseract_command()
            tesseract_adapter._validate_tesseract_languages("ita")
            import pytesseract

            text = pytesseract.image_to_string(pages[0], lang="ita")
            assert "FATTURA" in text.upper() and "123" in text, text
        print(f"Private Poppler -> PDF image -> private Tesseract: {text.strip()}")


if __name__ == "__main__":
    main()
