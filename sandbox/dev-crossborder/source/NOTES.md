# Notes on the cross-border invoice test set

## Composition
- 60 one-page invoices.
- Renders: 30 clean, 15 scanned, 15 photographed.
- Countries: US 12 (~20%), CA 8, GB 8, DE 8, FR 6, NL 5, IT 5, IE 4, ES 4.
- Currencies: USD 42, EUR 11, CAD 4, GBP 3. Foreign suppliers bill in home currency ~70% of the time (realistic for many SMEs; larger ones more often USD). US suppliers always USD.
- Layouts: 22 distinct layouts (us_standard/modern/classic, ca_*, uk_*, de_*, fr_*, nl_*, it_*, es_*, eu_*). Each supplier keeps a consistent layout across its invoices.

## Design choices
- Built from typical real-world conventions: German invoices use day-first dates, 1.234,56 numbering, USt-IdNr, IBAN, "Rechnung" headers, 19% VAT or 0% for export. UK uses day-first or written, sort code + account, 20% VAT. Canada uses GST/HST numbers (15-char), bilingual options, month-first or day-first. US uses month-first, $ , EIN.
- Terms written in the supplier's language where appropriate (e.g. "30 Tage netto", "à 30 jours", "binnen 30 dagen"). Discount terms use the net period for due date.
- Currency always explicit (code + symbol near totals and header).
- Line items, PO numbers, bank details, tax IDs are invented but realistic.
- Scans/photos: slight rotation, blur, noise/lighting variation via PIL after pdf2image; text remains human-readable.
- Exceptions: foreign_currency on all non-USD; a few missing_due_date (terms that do not imply a calculable period, e.g. "Payment by bank transfer"), total_mismatch (>1¢ arithmetic error), due_before_invoice, and a couple missing_tax. Most invoices are clean for a clerk to key.

## Labeling
- Dates labeled as the supplier intended (ISO), regardless of printed order (day-first common outside US).
- Amounts in label always "1234.56" style; page uses local grouping/decimal.
- Due date from printed date, or calculated from single-period terms / on-receipt; discount uses net days; EOM/other languages/other bases leave null.
- Total is the printed total even if arithmetic mismatches.

## Uncertainties
- Exact frequency of home-currency billing for small EU/UK/CA suppliers to US buyers is approximate (used ~70% home / 30% USD).
- Some European invoices might more often include reverse-charge notes for B2B exports; I used 0% tax lines in those cases.
- Photo/scan realism is approximate (no real paper texture or extreme distortion) but sufficient for human readability tests.
