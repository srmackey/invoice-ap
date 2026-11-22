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
| subtotal | Decimal amount in the invoice's currency, two places. |
| tax | Decimal amount in the invoice's currency, two places. |
| total | The printed total, not a corrected one. When no total is printed, the printed amount due. |

A field that is not on the page is null. A date or amount that is printed but does not parse reads as `unparsed`, and the result's `printed` holds the text that was on the page for each such field, so the clerk sees what to read and a scoring run shows which forms failed. `printed` is empty when everything parsed.

**Payment terms.** When the page prints no due date but prints payment terms, the due date is the invoice date plus the terms' days. One payment period in days, 1 to 180, stated as the time to pay, in English, French, German, Dutch, Italian, or Spanish, sets it: "Net 30", "30 days net", "Payment due within 14 days", "30 Tage netto", "zahlbar innerhalb 14 Tagen", "à 45 jours", "netto 30 dagen", "a 30 giorni", "neto 60 días". Payment on receipt in those languages ("Due on receipt", "sofort zahlbar", "à réception", "bij ontvangst", "al ricevimento", "a la recepción") is the invoice date. Discount terms are due at the net period ("2/10 Net 30", "2 % Skonto innerhalb 10 Tagen, 30 Tage netto" are 30 days); a discount period alone is not a due date. The due date stays null, for a person, when the terms are end-of-month or following-month in any of those languages ("Net 30 EOM", "10th prox", "30 jours fin de mois"), when the period counts from something other than the invoice date (delivery, goods, a statement), when two periods are offered with nothing marking the net one ("30 or 60 days"), when the period is over 180 days, or when the terms are in another language or are not a payment period at all. A printed due date always wins, even one the reader cannot parse. Terms printed where the due date goes ("Due Date: Upon receipt") count as terms. Labeled sets follow this rule; holdout-2 was labeled under the narrower rule before it and is relabeled to match.

**Dates.** A printed date reads through dateutil, strictly first and then, if that fails, as a date inside a sentence. Month words in French, German, Dutch, Italian, and Spanish (CLDR's wide and abbreviated names) read like English ones. A date counts only when the text gives a day, a month, and a year, and the year falls from 1900 through the year after the read; anything else printed is `unparsed`, never a date filled in from today. A numeric date that only one order can read is read that way (14/03/2026 is day-first, 2026-03-04 year-first). A dotted numeric date is read day-first. An ambiguous one (03/04/2026) is read month-first, unless the other date on the page can only be day-first, since a supplier uses one convention on a page. A known supplier's convention is per-supplier memory, not this rule.

**Currency and amounts.** The result carries `currency`, the ISO code the invoice names (USD, CAD, GBP, or EUR), or null when it names none, which reads as US dollars. It is Textract's currency code on the amounts when Textract gives one, else an untyped "Currency" field, else a sign or code printed on an amount (€, £, EUR, GBP, CAD, C$, USD); a bare "$" names no currency. A currency other than the US dollar sends the invoice to a person as `foreign_currency`. Nothing is converted: which rate, on which date, is an accounting choice. An amount reads its decimal mark from itself when it can: with both a dot and a comma, the later one is the decimal ("1.234,56", "1,234.56"); a lone mark followed by one or two digits is the decimal ("3872,40"); followed by three it is grouping ("1,234"). Only a lone dot before three digits ("1.234") cannot say, and on a EUR invoice it reads as grouping. A non-US currency is also evidence for dates: an ambiguous numeric date on such an invoice reads day-first, unless a date on the page can only be month-first. A supplier billing in US dollars gives no such evidence, so its ambiguous dates read month-first.

**Amount due.** When the page prints an amount due ("Amount Due", "Balance Due", "Total Due") and no total, the total is the amount due. This holds for every supplier. When a deposit or credit has been taken off, the amount due will not equal subtotal plus tax, and the exception rule sends the invoice to a person. Which number a particular supplier's total should be is per-supplier memory, not this rule.

**Where the reader finds them.** Textract types them itself: `PAYMENT_TERMS` and `AMOUNT_DUE`. When it returns no `PAYMENT_TERMS`, an untyped field under a terms label counts (one holding "terms", "zahlung", "paiement", "betaling", "pagamento", or "pago"), then a short line of page text that is terms on its own, since Textract does not type an unlabeled "Due on receipt". Docling's mapper takes terms after a terms label in any of the six languages ("Payment Terms", "Zahlungsbedingungen", "Conditions de paiement", "Betalingsvoorwaarden", "Termini di pagamento", "Condiciones de pago", and the like), in the same cell, the next cell, or the next line, or a short line that is terms on its own, and the amount due after one of the three labels above. A short line with no label counts only when it carries a payment word, so "Warranty: 90 days" is never terms.

**Labels in other languages.** Textract types English field labels well and foreign ones badly: it leaves "Rechnungsdatum" or "Factuurnummer" untyped, and has typed "Fällig am" and "Échéance" as the invoice date. So a known due-date label in any of the six languages ("Fällig am", "Échéance", "Vervaldatum", "Scadenza", "Vencimiento", and the like) makes that field the due date whatever Textract typed it. An untyped field under a known invoice-number or invoice-date label takes that field; a typed one keeps its type. A field still missing after that takes the value printed with its label on the page, after it on the same line or in the nearest line to its right on the same row. Labels are matched without case or accents, and the "º" in "N.º" matches however the OCR reads it. Docling's mapper takes the foreign due-date labels the same way it takes "Due Date".

**The exception.** The reader raises an exception, with reasons, when the invoice is in a currency other than the US dollar (`foreign_currency`), when any of the seven is missing (`missing_<field>`), when a date or amount is printed but does not parse (`unparsed_<field>`), when the due date is before the invoice date (`due_before_invoice`), or when subtotal plus tax differs from the total by more than one cent (`total_mismatch`). Missing and unparsed are kept apart because a clerk handles them differently: a missing field is not on the page, while an unparsed one is there and needs reading. An unparsed invoice date also leaves terms unable to set a due date, so that invoice carries both `unparsed_invoice_date` and `missing_due_date`. An exception is an answer, not a failure: the invoice goes to a person instead of straight through.

## How it is built

One reader, `read_invoice.py`, holds the runners, the mapping from each runner's output to the seven fields, and the exception rule. Every door calls it:

- the command, which reads one PDF and appends a record to `reads/reads.jsonl`
- the local page (`page.py`), which lists those records and reads an uploaded PDF
- the sandbox scorer (`sandbox/score.py`)
- the hosted function (`host/handler.py`)

So the system that was scored is the system that runs.

**Runners.** Amazon Textract AnalyzeExpense is the default. A local Docling container is the backup, chosen explicitly, for reading without AWS. Both go through the same exception rule.

**Host.** One AWS Lambda function behind a function URL that requires IAM credentials. It takes one PDF as the request body and returns the seven fields, the exception and its reasons, the currency, the printed text of any field that did not parse, the page count, and the seconds taken. A non-PDF body is a 400 and a Textract failure is a 502 with the reason. The function's role may call AnalyzeExpense and write its own logs, nothing else. Each read logs one line with timing, pages, and reasons, never field values. Nothing runs between calls. The stacks are CloudFormation in `infra/`.

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
| `sandbox/practice/` | Forty generated invoices for developing the reader-misses work: suppliers in eight locales, each printing dates and numbers in its own CLDR format, USD, CAD, GBP, and EUR, payment terms in six languages including the forms left for a person, dates that do not parse, and the arithmetic exceptions. Practice, not a benchmark: not claimed, and it may be regenerated. |
| `sandbox/inv-cdip-dev.json` | Development data: INV-CDIP's 74 multi-page invoices, page one only, labeled by the same rules as the frozen INV-CDIP set. Not frozen. Because it shares that archive, a later INV-CDIP score is no longer a clean holdout. |
| `sandbox/dev-crossborder/` | Development data: the rejected first delivery of holdout-3, 60 cross-border invoices with low-resolution scans and photos. Eight labels that broke the rules are dropped. Not frozen. |
| `sandbox/inv-cdip.json` | A frozen real-document set: 276 one-page invoices from INV-CDIP (Salesforce Research, CC BY-NC 4.0), mostly scans. Labels cover invoice number, invoice date, due date, tax, and total; the documents are fetched by `get_inv_cdip.py` and not committed. |

Each set is written by its own script in `sandbox/` and labeled by that script. Labels use ISO dates and decimal dollars. A set that labels fewer than the seven fields lists them, and each invoice is judged only on the fields its label row lists. On INV-CDIP the total is the annotated total amount, or the amount due where no total is annotated.

**Matching.** Dates match as calendar dates. Money matches to the cent. Supplier and invoice number match the printed text.
