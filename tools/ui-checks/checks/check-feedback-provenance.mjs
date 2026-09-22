import assert from "node:assert/strict";
import { chromium } from "playwright";

const appUrl = process.argv[2] ?? "http://127.0.0.1:4173";
const browser = await chromium.launch();
let savedInvoicePayload = null;
let resolveSaveRequest;
const saveRequest = new Promise((resolve) => {
  resolveSaveRequest = resolve;
});

const fields = [
  field("codice_fiscale_emittente", "Codice fiscale emittente", "01445070532"),
  field("nome_emittente", "Nome emittente", "PRIMO SOLE"),
  field("p_iva", "P.IVA", "01445070533"),
  field("codice_fiscale", "C.F. Condominio", "81005680533"),
  field("condominio", "Condominio", "CONDOMINIO VIA DUE GIUGNO 1"),
  field("numero_fattura", "Numero fattura", "OLD-1"),
  field("data_fattura", "Data fattura", "21-12-2024"),
  field("pagata_il", "Pagata il", "21-12-2024"),
  field("economic_summary.totale_fattura.value", "Totale fattura", "100.00"),
  field("economic_summary.ritenuta.value", "Ritenuta", "0.00"),
  field("economic_summary.totale_netto_fattura.value", "Totale netto fattura", "100.00"),
];

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/viewer/review-batches") {
      return json(route, {
        ok: true,
        batches: [
          {
            id: "review_batch_0001",
            label: "Revisione 001",
            base_path: "/runtime/manifests/review_batch_0001",
            invoice_count: 1,
          },
        ],
      });
    }
    if (path === "/runtime/manifests/review_batch_0001/viewer_index.json") {
      return json(route, {
        documents: [
          {
            id: "doc_000001",
            base_path: "/runtime/manifests/process_feedback/documents/doc_000001",
            review_invoice_ids: ["invoice-001"],
          },
        ],
      });
    }
    if (
      path ===
      "/runtime/manifests/process_feedback/documents/doc_000001/viewer_manifest.json"
    ) {
      return json(route, {
        schema_version: 1,
        source_file: "source.pdf",
        pages: [],
        invoices: [
          {
            invoice_id: "invoice-001",
            display_number: 1,
            title: "Fattura 001",
            page_numbers: [1],
            extraction_fields: fields,
            fields,
          },
        ],
      });
    }
    if (path === "/viewer/drafts") {
      if (request.method() === "GET") {
        return json(route, { ok: true, schema_version: 1, drafts: [] });
      }
      return json(route, { ok: true });
    }
    if (path === "/registry/status") {
      return json(route, { ok: true, has_file: true, record_count: 2 });
    }
    if (path === "/registry/records") {
      return json(route, {
        ok: true,
        record_count: 2,
        records: [
          registryRecord(
            "issuer-1",
            1,
            "PRIMO SOLE SOCIETA COOPERATIVA",
            "01445070532",
            "01445070533",
            "Altro soggetto",
          ),
          registryRecord(
            "condominium-1",
            2,
            "CONDOMINIO VIA DUE GIUGNO 1",
            "81005680533",
            null,
            "Condominio",
          ),
        ],
      });
    }
    if (path === "/processing/jobs") {
      return json(route, { ok: true, jobs: [] });
    }
    if (path === "/invoices/save") {
      savedInvoicePayload = request.postDataJSON();
      resolveSaveRequest();
      return json(route, { ok: true, record: { record_id: "record-1" } });
    }
    if (request.resourceType() === "document") {
      return route.continue();
    }
    return route.continue();
  });

  await page.goto(appUrl, { waitUntil: "networkidle" });
  const numberRow = page.locator(".invoice-fields-panel li").filter({
    hasText: "Numero fattura",
  });
  const numberInput = numberRow.locator("input");
  await numberInput.fill("NEW-2");
  await page.locator(".invoice-save-button").click();
  const continueButton = page.getByRole("button", { name: "Continua e salva" });
  if (await continueButton.isVisible()) {
    await continueButton.click();
  }
  let diagnosticTimer;
  const saveTimeout = new Promise((_, reject) => {
    diagnosticTimer = setTimeout(async () => {
        reject(
          new Error(
            `The save request did not arrive. Visible UI: ${await page.locator("body").innerText()}`,
          ),
        );
      }, 5000);
  });
  await Promise.race([saveRequest, saveTimeout]);
  clearTimeout(diagnosticTimer);

  assert.ok(savedInvoicePayload, "The frontend did not send /invoices/save");
  const submittedField = savedInvoicePayload.invoice.fields.find(
    (candidate) => candidate.kind === "numero_fattura",
  );
  assert.equal(submittedField.value, "NEW-2");
  assert.equal(submittedField.review_interaction, "manual_input");
  assert.equal(submittedField.review_value_origin, "manual_input");
  assert.equal(submittedField.review_source_field_kind, "numero_fattura");
  assert.match(submittedField.review_action_id, /.+/);
  const automaticallyEnrichedField = savedInvoicePayload.invoice.fields.find(
    (candidate) => candidate.kind === "nome_emittente",
  );
  assert.equal(automaticallyEnrichedField.value, "PRIMO SOLE SOCIETA COOPERATIVA");
  assert.equal(automaticallyEnrichedField.review_interaction, "automatic_replacement");
  assert.equal(automaticallyEnrichedField.review_value_origin, "automatic_registry");
  assert.match(automaticallyEnrichedField.review_action_id, /.+/);
  console.log("Frontend feedback provenance check passed.");
} finally {
  await browser.close();
}

function field(kind, label, value) {
  return { kind, label, value, score: 0.95 };
}

function registryRecord(
  registry_id,
  source_row,
  nominativo,
  codice_fiscale,
  partita_iva,
  tipologia,
) {
  return {
    registry_id,
    source_row,
    codice_anagrafica: null,
    nominativo,
    codice_fiscale,
    partita_iva,
    tipologia,
    warnings: [],
  };
}

function json(route, body) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}
