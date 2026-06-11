import argparse
from pathlib import Path

from estrattore.model import PerceptionBackend
from estrattore.pipeline import process_invoice


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "file_path",
        nargs="?",
        default="data/input/fattura.pdf",
        help="PDF or image file to process.",
    )
    parser.add_argument(
        "--backend",
        choices=[backend.value for backend in PerceptionBackend],
        default=PerceptionBackend.EMPTY.value,
        help="Perception backend to use.",
    )
    args = parser.parse_args()

    result = process_invoice(
        Path(args.file_path),
        perception_backend=PerceptionBackend(args.backend),
    )

    print(result)


if __name__ == "__main__":
    main()
