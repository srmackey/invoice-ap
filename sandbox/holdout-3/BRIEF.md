# Brief: generate a labeled test set of cross-border invoices

You are making a test set for an invoice-reading system you will never see. The system reads one supplier invoice (a PDF) and returns seven fields an accounts-payable clerk would key, and flags the invoice for a person when something needs one. Your set will be used to measure it once. Nobody will tune the system against your set.

You have no access to the system, its code, its earlier test sets, or its scores, and you should not go looking for them. Work in an empty folder.

This set is not a general sample. It is the part of a small US business's accounts-payable inbox that comes from outside the US, plus some ordinary US invoices for comparison. Build it from what real suppliers in those countries actually send, not from guesses about what might trip a reader up.

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

- **60 invoices, one page each.** Roughly half clean, a quarter scanned, and a quarter photographed. Assign the render independently of country and supplier, so every country appears clean, scanned, and photographed in roughly those shares. A score by country must not turn into a score by image quality.
- **Deterministic.** `make_set.py` takes a fixed seed and writes the same labels every run.
- **Labels come from the generator.** The script decides each invoice's values, renders the page from them, and writes the label from the same values. Do not hand-edit PDFs or labels.
- **Fictional.** Supplier and customer names, addresses, phone numbers, tax registration numbers, and logos are invented. No real company names or trademarks.

## Who sends these invoices

The buyer is one small US business. Its suppliers:

- **About a fifth are US suppliers** billing in US dollars, the way US suppliers normally write invoices.
- **The rest are from Canada, the United Kingdom, Ireland, and continental Europe** (for example Germany, France, the Netherlands, Italy, Spain). Each writes its invoice the way a supplier in that country normally does: its own conventions for dates, for numbers and money, for tax lines (VAT, GST/HST, and so on), and for layout. Some foreign suppliers bill in their home currency. Some bill a US customer in US dollars. Decide the mix from what you know of real cross-border trade and write it down in `NOTES.md`.

Only four currencies: USD, CAD, GBP, and EUR.

**Every invoice's currency must be determinable by a careful person reading the page.** That can be a currency code, a distinctive symbol, or a line of text. A page where nobody could tell is not allowed. How prominently it shows is up to you and should be realistic.

## The seven fields

| Field | What the label holds |
|---|---|
| `supplier` | The supplier's name exactly as printed in the letterhead. |
| `invoice_number` | Exactly as printed, including prefixes, dashes, and letters. |
| `invoice_date` | ISO date, `YYYY-MM-DD`. |
| `due_date` | ISO date. See the rules below. |
| `subtotal` | The amount in the invoice's own currency, as a string with a dot and two places, `"1214.90"`. No symbol, no grouping. |
| `tax` | Same format. `"0.00"` when the page prints a zero tax line. |
| `total` | Same format. The total as printed, even if the arithmetic on the page is wrong. |

A field that the page does not print is `null` in the label.

**Label the date the supplier meant.** Many of these suppliers write dates in an order a US reader would misread. The label is always the real calendar date the supplier intended, however the page writes it. Do not avoid dates that could be read two ways. They are part of what real invoices look like.

**Label the amount the supplier meant.** Write it in the label format above, whatever grouping and decimal marks the page uses.

**Due date rules.** A printed due date is the label, always. When no due date is printed but payment terms are:
- **One payment period in days** sets the due date at the invoice date plus that many days. The terms state a single number of days, between 1 and 180, as the time to pay. In English: "Net 30", "30 days net", "30 days", "Payment terms: 30 days", "Payment due within 30 days", "Payable within 30 days". The same meaning in French, German, Dutch, Italian, or Spanish counts too: "30 jours net", "à 30 jours", "30 Tage netto", "zahlbar innerhalb 30 Tagen", "netto 30 dagen", "binnen 30 dagen", "a 30 giorni", "30 giorni netto", "a 30 días", "neto 30 días".
- **Payment on receipt** sets the due date at the invoice date: "Due on receipt", "Upon receipt", "Due immediately", and the same meaning in those five languages ("à réception", "sofort zahlbar", "bij ontvangst", "al ricevimento", "a la recepción").
- **Discount terms** are due at the net period, never the discount period. "2/10 Net 30", "2% 10 days, net 30", "2 % Skonto innerhalb 14 Tagen, 30 Tage netto", and "escompte 2 % à 10 jours, net à 30 jours" are all due at 30 days. A discount period with no net period beside it leaves `due_date` null.
- **Terms printed where the due date would go** ("Due Date: Upon receipt", "Due: 30 days") count as terms.
- **Anything else leaves `due_date` null**, even when a person could work the date out:
  - end-of-month or following-month terms in any language: "Net 30 EOM", "10th prox", "30 days end of month", "30 jours fin de mois", "30 Tage zum Monatsende", "fine mese", "einde maand", "fin de mes"
  - periods counted from something other than the invoice date: delivery, receipt of goods, a statement, or a month
  - two or more periods with nothing marking which one is the net period
  - a period over 180 days
  - terms in any language other than those six
  - wording that is not a payment period, such as "payment by bank transfer"

Label by these rules, not by what the supplier meant.

Write terms the way each supplier really would. Do not steer them toward the wordings above.

**Total rule.** When the page prints no total but prints an amount due ("Amount Due", "Balance Due", "Total Due", or the equivalent), the `total` label is that amount.

## Exceptions

Each invoice is labeled with whether it should go to a person, and why. It should go to a person when any of these is true:

| Reason | When |
|---|---|
| `foreign_currency` | The invoice's currency is not USD. |
| `missing_<field>` | That field is null in the label, for example `missing_due_date`. |
| `due_before_invoice` | The due date is earlier than the invoice date. |
| `total_mismatch` | Subtotal plus tax differs from the total by more than one cent. |

`exception` is true when there is at least one reason. List every reason that applies. Apart from `foreign_currency`, include each of the other reasons a few times across the set, on both US and foreign invoices. Also include, where a real supplier in that country would use them, terms that fall under the null cases of the due date rules (end-of-month terms are common in France and elsewhere). The rest should be invoices a clerk would key without a second look.

## Variety

Many suppliers, many trades, and many layouts. Each supplier keeps a consistent look across its own invoices, the way real suppliers do. Do not reuse one template with the values swapped. Line items, addresses, VAT or GST numbers, bank details (IBAN, sort code, transit number, all invented), PO numbers, notes, and other ordinary clutter belong on the page. Some invoices may carry a second language beside English, the way bilingual Canadian or European invoices do. The field labels a clerk needs should still be findable by someone who reads English.

Scans and photos should look like the real thing: slight rotation, uneven lighting, blur, compression, a page not perfectly flat. Start from realistic resolution, then degrade: scans at about 200 dpi (around 1700 pixels across a letter or A4 page) and photos at least 1500 pixels across. The text a clerk needs must still be readable by a person.

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
      "file": "holdout-3/scans/001-short-supplier-slug.pdf",
      "supplier": "...",
      "invoice_number": "...",
      "invoice_date": "2026-06-14",
      "due_date": "2026-07-14",
      "subtotal": "1214.90",
      "tax": "243.00",
      "total": "1457.90",
      "currency": "EUR",
      "exception": true,
      "exception_reasons": ["foreign_currency"],
      "terms": "Net 30",
      "due_source": "terms",
      "country": "DE",
      "date_convention": "day-first",
      "number_style": "1.234,56",
      "layout": "<layout name>",
      "render": "scan",
      "sha256": "<sha256 of the PDF file>"
    }
  ]
}
```

- `file` starts with `holdout-3/`, then the render folder. Keep that prefix even though your folders sit at the top of your working folder.
- `currency` is `"USD"`, `"CAD"`, `"GBP"`, or `"EUR"`.
- `terms` is the terms text as printed, or null. `due_source` is `"printed"`, `"terms"`, or null when there is no due date.
- `country` is the supplier's country as a two-letter code.
- `date_convention` is how this supplier writes numeric dates: `"month-first"`, `"day-first"`, or `"year-first"`. Use `"written"` if the supplier only writes dates with the month as a word.
- `number_style` shows how this supplier writes one thousand two hundred thirty-four and 56 cents, for example `"1,234.56"`, `"1.234,56"`, or `"1 234,56"`.
- `render` is `"clean"`, `"scan"`, or `"photo"`.
- Leave `frozen` null. It is set when the set is accepted.

## Checking your work

- Re-derive every label from its rules and confirm it matches what the generator wrote: the dates the supplier meant, dates from terms, the amounts, the exception reasons, the arithmetic.
- Check the labels agree with each other: `due_source` is null exactly when `due_date` is null; a page that prints computable terms and no due date has a due date from terms; `missing_<field>` appears exactly when that field is null.
- Count renders by country and confirm each country spans all three.
- Render a sample of pages from each folder to images and look at them yourself. For each, confirm a careful person could find the currency.
- Do not run any document-reading service or model over the PDFs to check them: no OCR engine, no Textract, Docling, Azure, Google, or vision model. The set has to be judged by the rules, not by whether some reader gets it right.

## Hand back

The folder, zipped or as files, and a short reply saying: how many invoices of each render and layout, how many by country and by currency, how many exceptions of each reason, and anything you were unsure how to label.
