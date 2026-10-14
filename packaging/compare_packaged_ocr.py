"""Compare the same PDF through source and installed document workers."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIR = PROJECT_ROOT / "build"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from estrattore.document_worker import (  # noqa: E402
    DOCUMENT_WORKER_SCHEMA_VERSION,
    DocumentWorkerRequest,
    load_worker_result,
    save_worker_request,
)


def _run_worker(
    label: str, command: list[str], source_pdf: Path, test_root: Path, timeout: int
) -> tuple[dict, float]:
    print(f"Running {label} worker", flush=True)
    work = test_root / label
    work.mkdir()
    request_path = work / "request.json"
    result_path = work / "result.json"
    gate_path = work / "start.ok"
    output_dir = work / "output"
    save_worker_request(
        DocumentWorkerRequest(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            source_path=str(source_pdf),
            working_output_dir=str(work / "output.working"),
            output_dir=str(output_dir),
            document_id="ocr-parity-document",
            first_invoice_number=1,
        ),
        request_path,
    )
    gate_path.touch()
    environment = os.environ.copy()
    environment["ESTRATTORE_SCAN_DATA_DIR"] = str(work / "data")
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(PROJECT_ROOT / "src"), str(PROJECT_ROOT), environment.get("PYTHONPATH", ""))
    )

    started = time.perf_counter()
    completed = subprocess.run(
        [*command, "--request", str(request_path), "--result", str(result_path),
         "--start-gate", str(gate_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    elapsed = time.perf_counter() - started
    if not result_path.is_file():
        raise RuntimeError(
            f"{label}: worker exited {completed.returncode} without a result; "
            f"stderr: {completed.stderr[-1000:]}"
        )
    result = load_worker_result(result_path)
    if completed.returncode or result.status != "completed":
        raise RuntimeError(
            f"{label}: {result.error or completed.stderr[-1000:]}\n{result.traceback}"
        )
    manifest_path = output_dir / "viewer_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"{label}: worker completed without viewer_manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8")), elapsed


def _fields(invoice: dict) -> dict[str, dict]:
    return {
        str(field["kind"]): field
        for field in invoice.get("extraction_fields", invoice.get("fields", []))
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare(source: dict, frozen: dict) -> list[str]:
    differences: list[str] = []
    source_pages, frozen_pages = source["pages"], frozen["pages"]
    source_invoices, frozen_invoices = source["invoices"], frozen["invoices"]
    if len(source_pages) != len(frozen_pages):
        differences.append(f"pages: {len(source_pages)} vs {len(frozen_pages)}")
    if len(source_invoices) != len(frozen_invoices):
        differences.append(f"invoices: {len(source_invoices)} vs {len(frozen_invoices)}")

    for index, (left, right) in enumerate(zip(source_pages, frozen_pages), start=1):
        dimensions_left = (left["width"], left["height"])
        dimensions_right = (right["width"], right["height"])
        if dimensions_left != dimensions_right:
            differences.append(f"page {index} dimensions: {dimensions_left} vs {dimensions_right}")
        a, b = left["tokens"], right["tokens"]
        if [t["text"] for t in a] != [t["text"] for t in b]:
            removed = Counter(t["text"] for t in a) - Counter(t["text"] for t in b)
            added = Counter(t["text"] for t in b) - Counter(t["text"] for t in a)
            differences.append(
                f"page {index} token text/order: {len(a)} vs {len(b)} tokens; "
                f"source-only {sum(removed.values())}, packaged-only {sum(added.values())}"
            )
        if [t.get("bbox") for t in a] != [t.get("bbox") for t in b]:
            differences.append(f"page {index} token geometry differs")
        if [t.get("confidence") for t in a] != [t.get("confidence") for t in b]:
            differences.append(f"page {index} OCR confidence differs")
        if [(t.get("source"), t.get("source_id")) for t in a] != [
            (t.get("source"), t.get("source_id")) for t in b
        ]:
            differences.append(f"page {index} token provenance differs")
        for key in sorted((left.keys() | right.keys()) - {"tokens", "image"}):
            if left.get(key) != right.get(key):
                differences.append(f"page {index} intermediate {key} differs")

    for key in ("ocr_lang", "ocr_profile", "fallback_ocr_events", "quality_fallback_candidates"):
        if source.get(key) != frozen.get(key):
            differences.append(f"manifest {key} differs")

    for index, (left, right) in enumerate(zip(source_invoices, frozen_invoices), start=1):
        if left.get("page_numbers") != right.get("page_numbers"):
            differences.append(
                f"invoice {index} pages: {left.get('page_numbers')} vs {right.get('page_numbers')}"
            )
        a, b = _fields(left), _fields(right)
        if a.keys() != b.keys():
            differences.append(f"invoice {index} field kinds differ")
        for kind in sorted(a.keys() | b.keys()):
            old, new = a.get(kind, {}), b.get(kind, {})
            if old.get("value") != new.get("value"):
                differences.append(
                    f"invoice {index} {kind}: {old.get('value')!r} vs {new.get('value')!r}"
                )
            elif (old.get("page"), old.get("bbox"), old.get("score")) != (
                new.get("page"), new.get("bbox"), new.get("score")
            ):
                differences.append(f"invoice {index} {kind}: evidence/score differs")
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Existing PDF, read only")
    parser.add_argument("--frozen-exe", required=True, type=Path)
    parser.add_argument("--packaged-first", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    pdf, frozen_exe = args.pdf.resolve(), args.frozen_exe.resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        parser.error("pdf must be an existing PDF")
    if not frozen_exe.is_file():
        parser.error("--frozen-exe must point to an existing executable")
    BUILD_DIR.mkdir(exist_ok=True)

    print(f"PDF: {pdf.name} ({_sha256(pdf)[:12]})", flush=True)
    with tempfile.TemporaryDirectory(prefix="ocr-parity-", dir=BUILD_DIR) as temporary:
        test_root = Path(temporary).resolve()
        if BUILD_DIR.resolve() not in test_root.parents:
            raise RuntimeError("Temporary test root escaped build directory")
        runs = (
            ("source", [sys.executable, "-m", "estrattore.document_worker"]),
            ("packaged", [str(frozen_exe), "--document-worker"]),
        )
        if args.packaged_first:
            runs = tuple(reversed(runs))
        results = {
            label: _run_worker(label, command, pdf, test_root, args.timeout)
            for label, command in runs
        }
        source, source_seconds = results["source"]
        frozen, frozen_seconds = results["packaged"]
        differences = _compare(source, frozen)
        print(
            f"Source: {len(source['pages'])} pages, {len(source['invoices'])} invoices, "
            f"{sum(len(page['tokens']) for page in source['pages'])} tokens, {source_seconds:.1f}s"
        )
        print(
            f"Packaged: {len(frozen['pages'])} pages, {len(frozen['invoices'])} invoices, "
            f"{sum(len(page['tokens']) for page in frozen['pages'])} tokens, {frozen_seconds:.1f}s"
        )
        if differences:
            print("Differences:\n- " + "\n- ".join(differences[:40]))
            if len(differences) > 40:
                print(f"... and {len(differences) - 40} more")
            return 1
        print("OCR tokens, geometry, invoice grouping and extraction fields: IDENTICAL")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
