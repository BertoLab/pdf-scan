from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from uuid import uuid4

from estrattore.document_worker import (
    DOCUMENT_WORKER_SCHEMA_VERSION,
    DocumentWorkerRequest,
    DocumentWorkerResult,
    load_worker_result,
    save_worker_request,
)
from estrattore.process_tree import ProcessTree, create_process_tree
from estrattore.runtime_paths import project_root


class WorkerCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class DocumentExecution:
    source_path: Path
    working_output_dir: Path
    output_dir: Path
    document_id: str
    first_invoice_number: int
    control_dir: Path


def run_document_worker(
    execution: DocumentExecution,
    should_cancel: Callable[[], bool] | None = None,
    poll_interval_seconds: float = 0.1,
) -> DocumentWorkerResult:
    attempt_dir = execution.control_dir / f"attempt_{uuid4().hex}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    request_path = attempt_dir / "request.json"
    result_path = attempt_dir / "result.json"
    start_gate = attempt_dir / "start.ok"
    log_path = attempt_dir / "worker.log"
    request = DocumentWorkerRequest(
        schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
        source_path=str(execution.source_path.resolve()),
        working_output_dir=str(execution.working_output_dir.resolve()),
        output_dir=str(execution.output_dir.resolve()),
        document_id=execution.document_id,
        first_invoice_number=execution.first_invoice_number,
    )
    save_worker_request(request, request_path)

    process_tree = create_process_tree()
    process: subprocess.Popen[bytes] | None = None
    completed = False
    try:
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                _worker_command(request_path, result_path, start_gate),
                cwd=project_root(),
                env=_worker_environment(),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
            )
            try:
                process_tree.attach(process)
            except Exception:
                process.kill()
                process.wait(timeout=5)
                raise

            start_gate.touch()
            while process.poll() is None:
                if should_cancel and should_cancel():
                    _stop_worker(process_tree, process)
                    raise WorkerCancelled("Document processing cancelled")
                time.sleep(poll_interval_seconds)

        result = (
            load_worker_result(result_path)
            if result_path.is_file()
            else _completed_result_from_marker(execution)
        )
        if result is None:
            raise RuntimeError(
                f"Document worker exited without a result (code {process.returncode})"
            )
        if result.status != "completed":
            raise RuntimeError(result.error or "Document worker failed")
        completed = True
        return result
    except WorkerCancelled:
        shutil.rmtree(attempt_dir, ignore_errors=True)
        raise
    finally:
        process_tree.close()
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if completed:
            shutil.rmtree(attempt_dir, ignore_errors=True)


def _stop_worker(process_tree: ProcessTree, process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            process_tree.terminate()
        except OSError:
            process_tree.close()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _worker_command(
    request_path: Path,
    result_path: Path,
    start_gate: Path,
) -> list[str]:
    arguments = [
        "--request",
        str(request_path),
        "--result",
        str(result_path),
        "--start-gate",
        str(start_gate),
    ]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--document-worker", *arguments]
    return [sys.executable, "-m", "estrattore.document_worker", *arguments]


def _worker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    source_dir = str(project_root() / "src")
    root_dir = str(project_root())
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (source_dir, root_dir, existing) if path
    )
    return environment


def _completed_result_from_marker(
    execution: DocumentExecution,
) -> DocumentWorkerResult | None:
    marker_path = execution.output_dir / "document_complete.ok"
    manifest_path = execution.output_dir / "viewer_manifest.json"
    if not marker_path.is_file() or not manifest_path.is_file():
        return None
    payload = json.loads(marker_path.read_text(encoding="utf-8"))
    return DocumentWorkerResult(
        schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
        status="completed",
        document_id=str(payload.get("document_id") or execution.document_id),
        invoice_count=int(payload.get("invoice_count") or 0),
    )
