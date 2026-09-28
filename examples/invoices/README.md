# Sample workspace: supplier invoices

The Wynd dogfood process (SPEC §6.2). `process_supplier_invoice` turns a supplier invoice PDF into a validated
JSON record:

```
read ─► extract ─► validate ─┬─ valid and check: a final invoice ─► save ─────► $exit.done
          │                  ├─ fixable, fewer than 3 fixes ──────► fix ─► validate
          │                  └─ otherwise (incl. check says no) ──► escalate ─► $exit.needs_review
          └─ not an invoice ─► $exit.not_an_invoice
```

`read_pdf`, `validate_fields`, `save_record` and `escalate_to_human` are deterministic;
`extract_invoice_fields` and `fix_fields` are agentic (default provider `claude-code`). The `validate.done` edge
is agentic: its `save` branch also asks a model to check that the document is a final invoice requesting payment.

## Layout

```
wynd.yaml                         workspace marker (process root: processes/)
.env.example                      env vars for local runs; copy to .env (git-ignored)
tools/make_sample_pdfs.py         generates the sample PDFs from SAMPLES (stdlib only)
tools/test_make_sample_pdfs.py    fails if a PDF or an example text drifts from SAMPLES
processes/process_supplier_invoice/
  process.yaml                    the graph and its examples (run by `wynd test`)
  proto/*.yaml                    one proto-step per step: instruction, types, examples
  steps/<name>/                   compiled step packages (module, tests, step.lock.yaml)
  edges.lock.yaml                 knobs of the agentic save branch (written by `wynd compile`)
  cassettes/                      recorded model calls of the process examples, incl. edge checks
  examples/*.pdf                  sample documents
```

| Example | Document | Expected exit |
|---|---|---|
| 1 | `acme_inv_1042.pdf` | `done`, record saved to `RECORDS_DIR` |
| 2 | `globex_inv_77810.pdf` | `done`, total over 10,000 so saved to `REVIEW_DIR` |
| 3 | `initech_inv_5521.pdf` | `done` after `fix` resolves `$` to `CAD` |
| 4 | `shipping_notice.pdf` | `not_an_invoice` |
| 5 | `hooli_credit_note_311.pdf` | `needs_review` (negative total is not fixable) |
| 6 | `umbrella_proforma_88.pdf` | `needs_review` (fields are valid, but the agentic check on `validate.done[save]` says a pro forma is not a request for payment) |

## Quickstart

From this directory, after `uv sync --all-packages` at the repository root:

```sh
cp .env.example .env
uv run wynd validate process_supplier_invoice
uv run wynd env check process_supplier_invoice
uv run wynd test process_supplier_invoice          # offline: replays recorded model calls
uv run wynd run process_supplier_invoice --local \
  --input pdf_path=processes/process_supplier_invoice/examples/acme_inv_1042.pdf   # live: uses your Claude login
uv run wynd trace <run_id>
uv run wynd optimise process_supplier_invoice      # tier report from recorded live calls (no model call)
```

Re-record the model calls with `uv run wynd test process_supplier_invoice --live`. It runs as a job and commits
the new cassettes on a `wynd/test-live/…` branch, which the CLI fast-forwards into your branch.

Image mode needs Docker:

```sh
uv run wynd build process_supplier_invoice
uv run wynd serve wynd/process_supplier_invoice:<commit12> -d --env-file .env
uv run wynd run process_supplier_invoice --image \
  --input pdf_path=processes/process_supplier_invoice/examples/acme_inv_1042.pdf
```

A container cannot use your local Claude login: set `CLAUDE_CODE_OAUTH_TOKEN` in `.env` (from `claude setup-token`).

## Sample PDFs

`uv run python tools/make_sample_pdfs.py` regenerates `processes/process_supplier_invoice/examples/*.pdf`
byte for byte. Lines must be non-blank and cp1252. After changing `SAMPLES`, update the example texts in
`proto/*.yaml` and run `uv run pytest examples/invoices/tools` from the repository root.

## Licence

GNU AGPL v3.0 or later; see `LICENSE`.
