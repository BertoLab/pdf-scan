# Vendored Tesseract

Place a portable Windows Tesseract runtime here when packaging the app for non-technical users.

Expected layout:

```text
vendor/tesseract/
|-- tesseract.exe
`-- tessdata/
    |-- ita.traineddata
    `-- eng.traineddata
```

The application looks here before trying a system-wide Tesseract installation.
