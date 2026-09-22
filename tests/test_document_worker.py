from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path

import pytest

from estrattore import document_supervisor
from estrattore.document_supervisor import (
    DocumentExecution,
    WorkerCancelled,
    run_document_worker,
)
from estrattore.document_worker import (
    DOCUMENT_WORKER_SCHEMA_VERSION,
    DocumentWorkerRequest,
    process_document_request,
)


def test_document_request_publishes_only_complete_output(tmp_path: Path) -> None:
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"source")
    working_output_dir = tmp_path / "document.working"
    output_dir = tmp_path / "document"

    def fake_processor(source: Path, output: Path, first_number: int) -> list[Path]:
        assert source == source_path
        assert first_number == 7
        output.mkdir(parents=True)
        manifest_path = output / "viewer_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "invoices": [
                        {"invoice_id": "invoice-007"},
                        {"invoice_id": "invoice-008"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        return [manifest_path]

    result = process_document_request(
        DocumentWorkerRequest(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            source_path=str(source_path),
            working_output_dir=str(working_output_dir),
            output_dir=str(output_dir),
            document_id="document-001",
            first_invoice_number=7,
        ),
        processor=fake_processor,
    )

    assert result.status == "completed"
    assert result.invoice_count == 2
    assert not working_output_dir.exists()
    assert (output_dir / "viewer_manifest.json").is_file()
    assert (output_dir / "document_complete.ok").is_file()


def test_document_request_discards_partial_output_on_error(tmp_path: Path) -> None:
    working_output_dir = tmp_path / "document.working"

    def failing_processor(source: Path, output: Path, first_number: int) -> list[Path]:
        del source, first_number
        output.mkdir(parents=True)
        (output / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("OCR failed")

    result = process_document_request(
        DocumentWorkerRequest(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            source_path=str(tmp_path / "source.pdf"),
            working_output_dir=str(working_output_dir),
            output_dir=str(tmp_path / "document"),
            document_id="document-001",
            first_invoice_number=1,
        ),
        processor=failing_processor,
    )

    assert result.status == "error"
    assert result.error == "OCR failed"
    assert not working_output_dir.exists()
    assert not (tmp_path / "document").exists()


def test_document_request_reuses_completed_output(tmp_path: Path) -> None:
    output_dir = tmp_path / "document"
    output_dir.mkdir()
    (output_dir / "viewer_manifest.json").write_text(
        json.dumps({"invoices": [{"invoice_id": "invoice-001"}]}),
        encoding="utf-8",
    )
    (output_dir / "document_complete.ok").write_text(
        json.dumps({"document_id": "document-001", "invoice_count": 1}),
        encoding="utf-8",
    )

    def unexpected_processor(source: Path, output: Path, first_number: int) -> list[Path]:
        del source, output, first_number
        raise AssertionError("Completed output must not be processed again")

    result = process_document_request(
        DocumentWorkerRequest(
            schema_version=DOCUMENT_WORKER_SCHEMA_VERSION,
            source_path=str(tmp_path / "source.pdf"),
            working_output_dir=str(tmp_path / "document.working"),
            output_dir=str(output_dir),
            document_id="document-001",
            first_invoice_number=1,
        ),
        processor=unexpected_processor,
    )

    assert result.status == "completed"
    assert result.invoice_count == 1


def test_supervisor_cancels_active_worker_without_waiting_for_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_worker_command(
        request_path: Path,
        result_path: Path,
        start_gate: Path,
    ) -> list[str]:
        del request_path, result_path
        script = (
            "import pathlib,time; "
            f"gate=pathlib.Path({str(start_gate)!r}); "
            "\nwhile not gate.exists(): time.sleep(0.01)"
            "\ntime.sleep(60)"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(document_supervisor, "_worker_command", slow_worker_command)
    started_at = time.monotonic()

    with pytest.raises(WorkerCancelled):
        run_document_worker(
            DocumentExecution(
                source_path=tmp_path / "source.pdf",
                working_output_dir=tmp_path / "document.working",
                output_dir=tmp_path / "document",
                document_id="document-001",
                first_invoice_number=1,
                control_dir=tmp_path / "control",
            ),
            should_cancel=lambda: time.monotonic() - started_at >= 0.2,
            poll_interval_seconds=0.02,
        )

    assert time.monotonic() - started_at < 3


def test_supervisor_uses_completion_marker_if_worker_exits_after_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "document"

    def committed_worker_command(
        request_path: Path,
        result_path: Path,
        start_gate: Path,
    ) -> list[str]:
        del request_path, result_path
        manifest_payload = json.dumps({"invoices": [{"invoice_id": "invoice-001"}]})
        marker_payload = json.dumps(
            {"document_id": "document-001", "invoice_count": 1}
        )
        script = (
            "import pathlib,time; "
            f"gate=pathlib.Path({str(start_gate)!r}); "
            f"output=pathlib.Path({str(output_dir)!r}); "
            "\nwhile not gate.exists(): time.sleep(0.01)"
            "\noutput.mkdir(parents=True)"
            f"\n(output / 'viewer_manifest.json').write_text({manifest_payload!r}, encoding='utf-8')"
            f"\n(output / 'document_complete.ok').write_text({marker_payload!r}, encoding='utf-8')"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(
        document_supervisor,
        "_worker_command",
        committed_worker_command,
    )

    result = run_document_worker(
        DocumentExecution(
            source_path=tmp_path / "source.pdf",
            working_output_dir=tmp_path / "document.working",
            output_dir=output_dir,
            document_id="document-001",
            first_invoice_number=1,
            control_dir=tmp_path / "control",
        ),
        poll_interval_seconds=0.02,
    )

    assert result.status == "completed"
    assert result.invoice_count == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object contract")
def test_supervisor_cancels_worker_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child_pid_path = tmp_path / "child.pid"

    def descendant_worker_command(
        request_path: Path,
        result_path: Path,
        start_gate: Path,
    ) -> list[str]:
        del request_path, result_path
        child_script = "import time; time.sleep(60)"
        script = (
            "import pathlib,subprocess,sys,time; "
            f"gate=pathlib.Path({str(start_gate)!r}); "
            f"pid_path=pathlib.Path({str(child_pid_path)!r}); "
            "\nwhile not gate.exists(): time.sleep(0.01)"
            f"\nchild=subprocess.Popen([sys.executable, '-c', {child_script!r}])"
            "\npid_path.write_text(str(child.pid), encoding='utf-8')"
            "\ntime.sleep(60)"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(
        document_supervisor,
        "_worker_command",
        descendant_worker_command,
    )

    with pytest.raises(WorkerCancelled):
        run_document_worker(
            DocumentExecution(
                source_path=tmp_path / "source.pdf",
                working_output_dir=tmp_path / "document.working",
                output_dir=tmp_path / "document",
                document_id="document-001",
                first_invoice_number=1,
                control_dir=tmp_path / "control",
            ),
            should_cancel=lambda: child_pid_path.is_file(),
            poll_interval_seconds=0.02,
        )

    child_pid = int(child_pid_path.read_text(encoding="utf-8"))
    assert not _windows_process_is_active(child_pid)


def _windows_process_is_active(process_id: int) -> bool:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.OpenProcess(
        process_query_limited_information,
        False,
        process_id,
    )
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
