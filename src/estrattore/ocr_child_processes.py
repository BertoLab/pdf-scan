"""Windows process policy for native PDF and OCR tools inside the worker."""

from __future__ import annotations

import ntpath
import os
import subprocess


_OCR_TOOLS = frozenset({"pdfinfo.exe", "pdftoppm.exe", "pdftocairo.exe", "tesseract.exe"})
_original_popen = subprocess.Popen


def _is_ocr_tool(command: object) -> bool:
    if not isinstance(command, (list, tuple)) or not command:
        return False
    executable = command[0]
    return isinstance(executable, (str, os.PathLike)) and ntpath.basename(
        os.fspath(executable)
    ).lower() in _OCR_TOOLS


class _HiddenOcrPopen(_original_popen):
    def __init__(self, *popenargs, **kwargs):
        command = popenargs[0] if popenargs else kwargs.get("args")
        if os.name == "nt" and _is_ocr_tool(command):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        super().__init__(*popenargs, **kwargs)


def install_worker_ocr_process_policy() -> None:
    """Install before third-party OCR libraries import their Popen aliases."""
    if os.name == "nt" and subprocess.Popen is not _HiddenOcrPopen:
        subprocess.Popen = _HiddenOcrPopen
