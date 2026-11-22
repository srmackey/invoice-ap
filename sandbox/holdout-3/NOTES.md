# Notes on the cross-border invoice test set

This set contains 60 one-page fictional invoices from a small US buyer's AP inbox.

## Country and currency mix
- US: 12 invoices (20%), all USD, month-first dates (MM/DD/YYYY), number style 1,234.56. Sales tax sometimes applied, sometimes zero.
- Foreign (48 invoices): 6 each from CA, GB, IE, DE, FR, NL, IT, ES.
  - Approximately 55% of foreign invoices billed in home currency (CAD, GBP or EUR), 45% in USD. This matches common cross-border practice for US customers: many suppliers invoice in USD for simplicity; others use local currency.
- Only USD, CAD, GBP, EUR appear. Every page prints an explicit "Currency: XXX (symbol)" line in the header plus the symbol on the total amount, so currency is always determinable by a careful reader.
- Date conventions: US = month-first; all others = day-first (DD/MM/YYYY). Ambiguous dates (e.g. 05/06/2025) are included; labels hold the calendar date the supplier intended.
- Number styles: US/CA/GB/IE = 1,234.56; DE/NL/IT/ES = 1.234,56; FR = 1 234,56.

## Layouts
10 distinct layouts used (see labels.json "layouts" list). Each supplier is assigned one layout and keeps it consistent across its invoices. Differences include header styling, accent bars or borders, bilingual headers for Canadian suppliers, and total-block placement. Ordinary invoice clutter (fictional tax registration numbers, IBANs/sort codes, line-item descriptions, notes) is present. Labels a clerk needs remain findable in English.

## Renders
- 30 clean (vector PDF rendered directly)
- 11 scans (~200 dpi, slight skew ±1.8°, light noise and blur)
- 19 photos (~220 dpi, larger skew ±3.5°, uneven lighting, more blur)
Renders were assigned independently of country so that every country appears in all three render types. Scans and photos start from realistic resolution then degrade; text required by a clerk stays human-readable.

## Due dates, terms and exceptions
Due-date labeling follows the rules exactly:
- Printed due date is used when present.
- Otherwise, single net-days terms (including the listed English and foreign-language forms) or "on receipt" produce a due date; EOM / fin de mois / Monatsende / fine mese / etc., discount-only periods, and non-matching wording leave due_date null.
A few of each exception type (missing_due_date, missing_invoice_number, due_before_invoice, total_mismatch) appear on both US and foreign invoices. All non-USD invoices carry foreign_currency. The rest are ordinary invoices a clerk would key without flagging.

All names, addresses, tax IDs, bank details and other identifiers are invented. The generator is deterministic (seed 0); labels are written from the identical values used to render each page. No real company names or trademarks are used.
