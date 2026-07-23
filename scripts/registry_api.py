from __future__ import annotations

import json
import tempfile
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from estrattore.registry import (
    DEFAULT_REGISTRY_CACHE_PATH,
    DEFAULT_REGISTRY_INPUT_PATH,
    RegistryCache,
    RegistryRecord,
    clean_registry_cache_duplicates,
    extract_registry_fiscal_identifiers,
    import_registry_excel,
    extract_registry_code,
    load_registry_cache,
    normalize_registry_name,
    save_registry_cache,
)
from estrattore.fiscal_validation import (
    normalize_italian_tax_code,
    normalize_italian_vat_number,
)


HOST = "127.0.0.1"
PORT = 8765
UPLOAD_FIELD_NAME = "file"


class RegistryApiHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:
        self._send_empty_response(204)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json_response(200, {"ok": True})
            return

        if self.path == "/registry/status":
            self._send_json_response(200, _registry_status_payload())
            return

        if self.path == "/registry/file":
            self._send_registry_file_response()
            return

        if self.path == "/registry/records":
            self._send_json_response(200, _registry_records_payload())
            return

        self._send_json_response(404, {"error": "Endpoint non trovato"})

    def do_POST(self) -> None:
        if self.path == "/registry/records":
            try:
                payload = self._read_json_body()
                cache = _save_registry_records_payload(payload)
                self._send_json_response(200, _registry_records_payload(cache))
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path == "/registry/clean-duplicates":
            try:
                cache = _clean_registry_duplicates()
                self._send_json_response(200, cache)
            except Exception as error:
                self._send_json_response(400, {"ok": False, "error": str(error)})
            return

        if self.path != "/registry/import":
            self._send_json_response(404, {"error": "Endpoint non trovato"})
            return

        try:
            upload_path = self._save_uploaded_file()
            try:
                cache = import_registry_excel(upload_path)
            finally:
                upload_path.unlink(missing_ok=True)

            self._send_json_response(
                200,
                {
                    "ok": True,
                    "record_count": len(cache.records),
                    "warning_counts": _warning_counts(cache.records),
                    "source_file": cache.source_file,
                },
            )
        except Exception as error:
            self._send_json_response(400, {"ok": False, "error": str(error)})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[registry-api] {self.address_string()} - {format % args}")

    def _save_uploaded_file(self) -> Path:
        content_type = self.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("La richiesta deve essere multipart/form-data")

        content_length = int(self.headers.get("content-length", "0"))
        if content_length <= 0:
            raise ValueError("File anagrafica mancante")

        raw_body = self.rfile.read(content_length)
        message = BytesParser(policy=default).parsebytes(
            b"Content-Type: "
            + content_type.encode("utf-8")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + raw_body
        )
        file_part = next(
            (
                part
                for part in message.iter_parts()
                if part.get_param("name", header="content-disposition")
                == UPLOAD_FIELD_NAME
            ),
            None,
        )
        if file_part is None:
            raise ValueError("File anagrafica mancante")

        filename = Path(file_part.get_filename() or "").name
        if Path(filename).suffix.lower() != ".xlsx":
            raise ValueError("Il file anagrafica deve essere un Excel .xlsx")

        payload = file_part.get_payload(decode=True)
        if not payload:
            raise ValueError("File anagrafica vuoto")

        upload_dir = Path("outputs") / "registry_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=upload_dir,
            suffix=".xlsx",
            delete=False,
        ) as temporary_file:
            temporary_file.write(payload)
            return Path(temporary_file.name)

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("content-length", "0"))
        if content_length <= 0:
            raise ValueError("Richiesta vuota")

        raw_body = self.rfile.read(content_length)
        return json.loads(raw_body.decode("utf-8"))

    def _send_empty_response(self, status_code: int) -> None:
        self.send_response(status_code)
        self._send_cors_headers()
        self.end_headers()

    def _send_json_response(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_registry_file_response(self) -> None:
        if not DEFAULT_REGISTRY_INPUT_PATH.exists():
            self._send_json_response(404, {"ok": False, "error": "Nessuna anagrafica in memoria"})
            return

        body = DEFAULT_REGISTRY_INPUT_PATH.read_bytes()
        self.send_response(200)
        self._send_cors_headers()
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header(
            "Content-Disposition",
            f'inline; filename="{DEFAULT_REGISTRY_INPUT_PATH.name}"',
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


def _warning_counts(records: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        for warning in record.warnings or []:
            counts[warning] = counts.get(warning, 0) + 1
    return counts


def _registry_status_payload() -> dict[str, Any]:
    if not DEFAULT_REGISTRY_INPUT_PATH.exists():
        return {"ok": True, "has_file": False}

    stat = DEFAULT_REGISTRY_INPUT_PATH.stat()
    payload: dict[str, Any] = {
        "ok": True,
        "has_file": True,
        "file_name": DEFAULT_REGISTRY_INPUT_PATH.name,
        "file_size": stat.st_size,
        "file_url": "/registry/file",
    }

    if DEFAULT_REGISTRY_CACHE_PATH.exists():
        try:
            cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
            payload["record_count"] = len(cache.records)
        except Exception as error:
            payload["cache_error"] = str(error)

    return payload


def _registry_records_payload(cache: RegistryCache | None = None) -> dict[str, Any]:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        return {"ok": True, "records": [], "record_count": 0}

    cache = cache or load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    return {
        "ok": True,
        "record_count": len(cache.records),
        "records": [
            {
                "registry_id": record.registry_id,
                "source_row": record.source_row,
                "codice_anagrafica": record.codice_anagrafica,
                "nominativo": record.normalized_name or record.nominativo,
                "codice_fiscale": record.codice_fiscale,
                "partita_iva": record.partita_iva,
                "tipologia": record.tipologia,
                "warnings": [_registry_warning_label(warning) for warning in record.warnings or []],
            }
            for record in cache.records
        ],
    }


def _save_registry_records_payload(payload: dict[str, Any]) -> RegistryCache:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        raise ValueError("Anagrafica non caricata")

    existing_cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    records_payload = payload.get("records")
    if not isinstance(records_payload, list):
        raise ValueError("Formato record anagrafica non valido")

    existing_by_key = {
        (record.registry_id, record.source_row): record
        for record in existing_cache.records
    }
    updated_records: list[RegistryRecord] = []

    for item in records_payload:
        if not isinstance(item, dict):
            raise ValueError("Record anagrafica non valido")

        registry_id = str(item.get("registry_id") or "").strip()
        source_row = int(item.get("source_row") or 0)
        existing_record = existing_by_key.get((registry_id, source_row))
        if existing_record is None:
            raise ValueError("Record anagrafica non riconosciuto")

        nominativo = str(item.get("nominativo") or "").strip()
        codice_anagrafica_raw = str(item.get("codice_anagrafica") or "").strip()
        codice_fiscale_raw = str(item.get("codice_fiscale") or "").strip()
        partita_iva_raw = str(item.get("partita_iva") or "").strip()
        tipologia = str(item.get("tipologia") or "").strip()
        normalized_name = normalize_registry_name(nominativo)
        codice_anagrafica = (
            codice_anagrafica_raw
            or extract_registry_code(nominativo)
            or existing_record.codice_anagrafica
        )
        extracted_codice_fiscale, extracted_partita_iva = (
            extract_registry_fiscal_identifiers(nominativo)
        )
        codice_fiscale = (
            _normalized_edit_tax_code(codice_fiscale_raw)
            or extracted_codice_fiscale
        )
        partita_iva = (
            _normalized_edit_vat_number(partita_iva_raw)
            or extracted_partita_iva
        )

        updated_records.append(
            RegistryRecord(
                registry_id=existing_record.registry_id,
                source_row=existing_record.source_row,
                codice_anagrafica=codice_anagrafica,
                nominativo=nominativo,
                normalized_name=normalized_name,
                codice_fiscale_raw=codice_fiscale_raw,
                codice_fiscale=codice_fiscale,
                partita_iva_raw=partita_iva_raw,
                partita_iva=partita_iva,
                tipologia=tipologia,
                ultima_modifica=existing_record.ultima_modifica,
                utente=existing_record.utente,
                warnings=_registry_record_warnings(
                    normalized_name=normalized_name,
                    codice_fiscale_raw=codice_fiscale_raw,
                    codice_fiscale=codice_fiscale,
                    partita_iva_raw=partita_iva_raw,
                    partita_iva=partita_iva,
                ),
            )
        )

    cache = RegistryCache(
        schema_version=existing_cache.schema_version,
        source_file=existing_cache.source_file,
        records=updated_records,
    )
    save_registry_cache(cache, DEFAULT_REGISTRY_CACHE_PATH)
    return cache


def _clean_registry_duplicates() -> dict[str, Any]:
    if not DEFAULT_REGISTRY_CACHE_PATH.exists():
        raise ValueError("Anagrafica non caricata")

    existing_cache = load_registry_cache(DEFAULT_REGISTRY_CACHE_PATH)
    result = clean_registry_cache_duplicates(existing_cache)
    save_registry_cache(result.cache, DEFAULT_REGISTRY_CACHE_PATH)
    payload = _registry_records_payload(result.cache)
    payload["cleaning_report"] = result.report
    return payload


def _normalized_edit_tax_code(value: str) -> str | None:
    normalized = normalize_italian_tax_code(value)
    if normalized in {None, "0", "00000000000", "000000000000"}:
        return None
    return normalized


def _normalized_edit_vat_number(value: str) -> str | None:
    normalized = normalize_italian_vat_number(value)
    if normalized in {None, "00000000000"}:
        return None
    return normalized


def _registry_record_warnings(
    normalized_name: str,
    codice_fiscale_raw: str,
    codice_fiscale: str | None,
    partita_iva_raw: str,
    partita_iva: str | None,
) -> list[str]:
    warnings = []
    if not normalized_name:
        warnings.append("missing_normalized_name")
    if codice_fiscale_raw and codice_fiscale is None:
        warnings.append("invalid_codice_fiscale")
    if partita_iva_raw and partita_iva is None:
        warnings.append("invalid_partita_iva")
    return warnings


def _registry_warning_label(warning: str) -> str:
    labels = {
        "missing_normalized_name": "Nominativo mancante o non leggibile",
        "invalid_codice_fiscale": "Codice fiscale non valido",
        "invalid_partita_iva": "Partita IVA non valida",
        "merged_duplicate_fiscal_identifier": "Duplicato fiscale unito automaticamente",
        "conflicting_duplicate_fiscal_identifier": "Duplicato fiscale da controllare",
    }
    return labels.get(warning, warning.replace("_", " "))


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), RegistryApiHandler)
    print(f"Registry API attiva su http://{HOST}:{PORT}")
    print("Endpoint import: POST /registry/import")
    server.serve_forever()


if __name__ == "__main__":
    main()
