# Vendored Tesseract

Build input: place the pinned portable Windows Tesseract 5.4.0.20240606 runtime
here before preparing a desktop release. `packaging/prepare_native.py` verifies
the selected executable, DLLs, models and notices against a fixed fingerprint.
The ignored local files are build inputs, not a runtime prerequisite on the
recipient's PC.

Expected layout:

```text
vendor/tesseract/
|-- tesseract.exe
`-- tessdata/
    |-- ita.traineddata
    `-- eng.traineddata
```

Source mode looks here before trying a system-wide Tesseract installation. The
frozen app uses only its staged private copy.
