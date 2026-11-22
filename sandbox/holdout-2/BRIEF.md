# Brief: generate a labeled invoice test set

You are making a test set for an invoice-reading system you will never see. The system reads one supplier invoice (a PDF) and returns seven fields an accounts-payable clerk would key, and flags the invoice for a person when something is wrong with it. Your set will be used to measure it once. Nobody will tune the system against your set.

You have no access to the system, its code, its earlier test sets, or its scores, and you should not go looking for them. Work in an empty folder. Build the set from what real small-business supplier invoices look like, not from guesses about what might trip a reader up.

## What to produce

In your working folder:

```
make_set.py          the generator. One run writes everything below.
requirements.txt     pinned versions of anything make_set.py imports
labels.json          the labels, in the format below
clean/               PDFs rendered directly
scans/               PDFs that look scanned
photos/              PDFs that look photographed with a phone
NOTES.md             what you chose and why, in a page or less
```

- **100 invoices, one page each.** Roughly half clean, a quarter scanned, a quarter photographed.
- **Deterministic.** `make_set.py` takes a fixed seed and writes the same labels every run.
- **Labels come from the generator.** The script decides each invoice's values, renders the page from them, and writes the label from the same values. Do not hand-edit PDFs or labels.
- **Fictional.** Supplier and customer names, addresses, phone numbers, and logos are invented. No real company names or trademarks.

## The seven fields

| Field | What the label holds |
|---|---|
| `supplier` | The supplier's name exactly as printed in the letterhead. |
| `invoice_number` | Exactly as printed, including prefixes, dashes, and letters. |
| `invoice_date` | ISO date, `YYYY-MM-DD`. |
| `due_date` | ISO date. See the rules below. |
| `subtotal` | Decimal dollars as a string with two places, `"1214.90"`. |
| `tax` | Same format. `"0.00"` when the page prints a zero tax line. |
| `total` | Same format. The total as printed, even if the arithmetic on the page is wrong. |

A field that the page does not print is `null` in the label.

**Due date rules.** A printed due date is the label, always. When no due date is printed but payment terms are:
- "Net N" is the invoice date plus N days.
- "Due on receipt" or "Upon receipt" is the invoice date.
- "2/10 Net 30" and similar discount terms are due at the net days (30 here), not the discount date.
- Terms printed where the due date would go ("Due Date: Upon receipt") count as terms.
- Anything else, such as "Net 30 EOM", "10th prox", or "payment due in 30 days", leaves `due_date` null.

**Total rule.** When the page prints no total but prints an amount due ("Amount Due", "Balance Due", "Total Due"), the `total` label is that amount.

## Exceptions

Each invoice is labeled with whether it should go to a person, and why. It should go to a person when any of these is true:

| Reason | When |
|---|---|
| `missing_<field>` | That field is null in the label, for example `missing_due_date`. |
| `due_before_invoice` | The due date is earlier than the invoice date. |
| `total_mismatch` | Subtotal plus tax differs from the total by more than one cent. |

`exception` is true when there is at least one reason. List every reason that applies. Make about a quarter of the set exceptions, and include each kind at least a few times. The rest should be invoices a clerk would key without a second look.

## Variety

Make the set look like a real small business's accounts-payable inbox over a couple of years: many different suppliers and trades, many layouts, and the range of styles real suppliers use for every field. Decide the mix yourself from what you know of real invoices and write it down in `NOTES.md`. Do not reuse one template with the values swapped. Each supplier keeps a consistent look across its own invoices, the way real suppliers do. Line items, addresses, PO numbers, remit-to blocks, notes, and other ordinary clutter belong on the page.

Scans and photos should look like the real thing: slight rotation, uneven lighting, blur, compression, a page not perfectly flat. The text a clerk needs must still be readable by a person.

## labels.json

```json
{
  "fields": ["supplier", "invoice_number", "invoice_date", "due_date", "subtotal", "tax", "total"],
  "frozen": null,
  "generator": "make_set.py",
  "seed": 0,
  "layouts": ["<one name per layout you used>"],
  "invoices": [
    {
      "id": "001-short-supplier-slug",
      "file": "holdout-2/scans/001-short-supplier-slug.pdf",
      "supplier": "...",
      "invoice_number": "...",
      "invoice_date": "2026-06-14",
      "due_date": "2026-07-14",
      "subtotal": "1214.90",
      "tax": "0.00",
      "total": "1214.90",
      "exception": false,
      "exception_reasons": [],
      "terms": "Net 30",
      "due_source": "terms",
      "layout": "<layout name>",
      "render": "scan",
      "sha256": "<sha256 of the PDF file>"
    }
  ]
}
```

- `file` starts with `holdout-2/`, then the render folder. Keep that prefix even though your folders sit at the top of your working folder.
- `terms` is the terms text as printed, or null. `due_source` is `"printed"`, `"terms"`, or null when there is no due date.
- `render` is `"clean"`, `"scan"`, or `"photo"`.
- Leave `frozen` null. It is set when the set is accepted.

## Checking your work

- Re-derive every label from its rules and confirm it matches what the generator wrote: dates from terms, the exception reasons, the arithmetic.
- Render a sample of pages from each folder to images and look at them yourself.
- Do not run any document-reading service or model over the PDFs to check them: no OCR engine, no Textract, Docling, Azure, Google, or vision model. The set has to be judged by the rules, not by whether some reader gets it right.

## Hand back

The folder, zipped or as files, and a short reply saying: how many invoices of each render and layout, how many exceptions of each reason, and anything you were unsure how to label.
