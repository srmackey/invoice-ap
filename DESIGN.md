# invoice-ap design

invoice-ap reads one supplier invoice and returns the fields an accounts-payable clerk would key, plus an exception when something on the page needs a person. The invoices it has been measured on are synthetic. Install and operation are in `README.md`.

## What it returns

Seven fields:

| Field | Meaning |
|---|---|
| supplier | The letterhead name, as printed. |
| invoice_number | The invoice number, as printed. |
| invoice_date | A calendar date. |
| due_date | A calendar date. See payment terms below. |
| subtotal | Decimal dollars, two places. |
| tax | Decimal dollars, two places. |
| total | The printed total, not a corrected one. When no total is printed, the printed amount due. |

A field that is not on the page is null. A date or amount that is printed but does not parse reads as `unparsed`, and the result's `printed` holds the text that was on the page for each such field, so the clerk sees what to read and a scoring run shows which forms failed. `printed` is empty when everything parsed.

**Payment terms.** When the page prints no due date but prints payment terms, the due date is the invoice date plus the terms' days. "Net N" is due in N days. "Due on receipt" (or "upon receipt") is the invoice date. "2/10 Net 30" is due in 30 days; the discount date is not the due date. Terms the rule cannot compute, such as "Net 30 EOM" or "10th prox", leave the due date null. Any other wording, such as "payment due in 30 days", is not terms the rule knows and also leaves it null. A printed due date always wins, even one the reader cannot parse. Terms printed where the due date goes ("Due Date: Upon receipt") count as terms.

**Amount due.** When the page prints an amount due ("Amount Due", "Balance Due", "Total Due") and no total, the total is the amount due. This holds for every supplier. When a deposit or credit has been taken off, the amount due will not equal subtotal plus tax, and the exception rule sends the invoice to a person. Which number a particular supplier's total should be is per-supplier memory, not this rule.

**Where the reader finds them.** Textract types them itself: `PAYMENT_TERMS` and `AMOUNT_DUE`. When it returns no `PAYMENT_TERMS`, an untyped field under a label with "terms" in it counts, then a short line of page text that is terms on its own, since Textract does not type an unlabeled "Due on receipt". Docling's mapper takes terms after a "Terms" or "Payment Terms" label, in the same cell, the next cell, or the next line, or a short line that is terms on its own, and the amount due after one of the three labels above.

**The exception.** The reader raises an exception, with reasons, when any of the seven is missing (`missing_<field>`), when a date or amount is printed but does not parse (`unparsed_<field>`), when the due date is before the invoice date (`due_before_invoice`), or when subtotal plus tax differs from the total by more than one cent (`total_mismatch`). Missing and unparsed are kept apart because a clerk handles them differently: a missing field is not on the page, while an unparsed one is there and needs reading. An unparsed invoice date also leaves terms unable to set a due date, so that invoice carries both `unparsed_invoice_date` and `missing_due_date`. An exception is an answer, not a failure: the invoice goes to a person instead of straight through.

## How it is built

One reader, `read_invoice.py`, holds the runners, the mapping from each runner's output to the seven fields, and the exception rule. Every door calls it:

- the command, which reads one PDF and appends a record to `reads/reads.jsonl`
- the local page (`page.py`), which lists those records and reads an uploaded PDF
- the sandbox scorer (`sandbox/score.py`)
- the hosted function (`host/handler.py`)

So the system that was scored is the system that runs.

**Runners.** Amazon Textract AnalyzeExpense is the default. A local Docling container is the backup, chosen explicitly, for reading without AWS. Both go through the same exception rule.

**Host.** One AWS Lambda function behind a function URL that requires IAM credentials. It takes one PDF as the request body and returns the seven fields, the exception and its reasons, the printed text of any field that did not parse, the page count, and the seconds taken. A non-PDF body is a 400 and a Textract failure is a 502 with the reason. The function's role may call AnalyzeExpense and write its own logs, nothing else. Each read logs one line with timing, pages, and reasons, never field values. Nothing runs between calls. The stacks are CloudFormation in `infra/`.

**Caller.** `host/call.py` signs a request for the function URL and needs only `host/requirements.txt`, so a caller does not need this checkout.

## Limits

- One page per invoice. Textract reads a one-page PDF in a single call; a longer PDF is refused with the reason.
- About 4.4 MB per file through the function URL, since the request is buffered at 6 MB and a binary body arrives base64-encoded.
- AnalyzeExpense allows 5 calls per second per account in us-east-1.

## Evaluation

The sandbox (`sandbox/`) runs the reader against labeled invoice sets and writes a run file per scoring run. How a score is measured is in `AGENTS.md`; the current scores are in `README.md`.

| Set | What it is |
|---|---|
| `sandbox/fixtures/` | Ten generated invoices in one layout. |
| `sandbox/hard/` | Those ten as scans and phone photos, and four in a second layout. |
| `sandbox/holdout/` | A frozen holdout: 100 one-page invoices in twelve further layouts, clean, scanned, and photographed, 25 of them exceptions and ten with due dates set by terms. |
| `sandbox/holdout-2/` | A second frozen holdout, built by an outside agent that never saw this project: 100 one-page invoices from 33 suppliers in 13 layouts, clean, scanned, and photographed, 26 of them exceptions and 46 with due dates set by terms. |
| `sandbox/holdout-3/` | A frozen cross-border challenge set, built blind by outside agents in two parts: 120 one-page invoices from 54 suppliers in 20 layouts, from the US, Canada, the UK, Ireland, Germany, France, the Netherlands, Italy, and Spain, in USD, CAD, GBP, and EUR, clean, scanned, and photographed in every country. Labels hold the date and amount the supplier meant, plus currency, country, date convention, and number style. It is labeled to the rules this project is moving to (`foreign_currency` and the wider terms rule), which the reader does not follow yet. |
| `sandbox/practice/` | Ten generated invoices for developing the terms rule and the amount-due fallback. Practice, not a benchmark: not claimed, and it may be regenerated. |
| `sandbox/inv-cdip.json` | A frozen real-document set: 276 one-page invoices from INV-CDIP (Salesforce Research, CC BY-NC 4.0), mostly scans. Labels cover invoice number, invoice date, due date, tax, and total; the documents are fetched by `get_inv_cdip.py` and not committed. |

Each set is written by its own script in `sandbox/` and labeled by that script. Labels use ISO dates and decimal dollars. A set that labels fewer than the seven fields lists them, and each invoice is judged only on the fields its label row lists. On INV-CDIP the total is the annotated total amount, or the amount due where no total is annotated.

**Matching.** Dates match as calendar dates. Money matches to the cent. Supplier and invoice number match the printed text.
