from __future__ import annotations

import os
import subprocess

import pytest

from estrattore.ocr_child_processes import _HiddenOcrPopen, _is_ocr_tool


def test_ocr_tool_recognition_is_bounded_to_known_executables() -> None:
    assert _is_ocr_tool([r"C:\vendor\poppler\bin\pdfinfo.exe", "-v"])
    assert _is_ocr_tool([r"C:\vendor\tesseract\TESSERACT.EXE", "--version"])
    assert not _is_ocr_tool([r"C:\Windows\System32\notepad.exe"])
    assert not _is_ocr_tool("pdfinfo.exe -v")


@pytest.mark.skipif(os.name != "nt", reason="Windows process creation flag")
def test_native_ocr_children_receive_no_window_flag(monkeypatch) -> None:
    calls = []

    def record_init(self, *args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(subprocess.Popen, "__init__", record_init)
    _HiddenOcrPopen([r"C:\vendor\poppler\bin\pdfinfo.exe", "-v"], creationflags=0x10)
    _HiddenOcrPopen([r"C:\Windows\System32\notepad.exe"], creationflags=0x10)

    assert calls[0][0][0][-1] == "-v"
    assert calls[0][1]["creationflags"] == 0x10 | subprocess.CREATE_NO_WINDOW
    assert calls[1][1]["creationflags"] == 0x10
