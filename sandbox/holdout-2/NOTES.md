# holdout-2: notes

**The inbox.** This is the accounts-payable inbox of one fictional small business: Owlsedge Brewing Co. in Fenmore, CO. It has a taproom and a production unit. The set covers 100 invoices from 33 invented suppliers, dated Dec 2024 to Nov 2026. A brewery was chosen because it buys from many trades: malt, hops, cans, CO2, linen, waste, propane, alarm monitoring, pest control, IT, plumbing, electrical, refrigeration, pump repair, lumber, auto parts, signs, printing, glassware, freight, food, janitorial, lab testing, equipment rental, snow removal, window cleaning, a handyman, a CPA, a lawyer and a designer. Recurring vendors send several invoices (monthly waste, IT and pest; weekly linen; quarterly alarm), and one-off vendors send one or two. Phone numbers use 555-01xx. Logos are simple geometric marks drawn in code. I can't rule out that some invented name matches a real business somewhere, but none is meant to.

**Layouts (13).** classic letterhead, banded header, centered serif letterhead, colored sidebar, field-service ticket, dot-matrix on a preprinted form (some on green-bar tractor paper), minimal modern, narrow counter ticket, QuickBooks-style boxed template, carbon-form with hand-filled blue ink, professional-services letter, word-processor template, and a utility bill with a tear-off remittance stub. Each supplier always uses the same layout, color, fonts, logo, field labels, date format, money format, number scheme and terms. Several suppliers share a layout but look different, the way many shops share accounting-software templates.

**Field styles.** Invoice numbers are sequential per supplier over time. They vary in style: plain, zero-padded, prefixed (`INV`-style letters), year-based resets, and dashed. Field labels include Invoice # / No. / Number / Ticket/Invoice / Freight Bill / Invoice No. Dates come in ten formats, all unambiguous to a US reader (`03/04/2026`, `3/4/26`, `March 4, 2026`, `04-Mar-2026`, `4 March 2026`, ISO…). Money is printed as `$1,214.90`, `1,214.90` or `1214.90`.

The total line is printed as Total, Invoice Total, Total Charges, Total Due, Amount Due, Balance Due, or Total followed by Payments/Credits and Balance Due. Pages also carry ordinary clutter: PO, account and work-order numbers, ship/order dates, service periods, BOL numbers, remit-to blocks that sometimes use a longer legal name, account summaries with a previous balance, and notes.

**Exceptions (26 of 100).** Some come from how a supplier always bills:
- The CPA and the lawyer print no subtotal or tax line.
- The lab prints no subtotal.
- The handyman prints no invoice number and no tax.
- The lumber yard bills "10th Prox".
- One fitting supplier uses "Net 30 EOM".
- The screen printer says "Payment due in 30 days".
- The fabricator prints no terms at all.
- The glassware shop adds shipping below the subtotal.

Others are planted on single invoices, each a mistake real invoices contain:
- a total keyed without the tax
- transposed digits in the total
- a subtotal not updated after a line was added
- a due-date year typed as last year
- a due-date month typo
- a due date filled from the order date
- a blank date, a blank invoice number, or a blank total box on a form

Exceptions are spread across renders in proportion to the set: 14 clean, 6 scanned, 6 photographed.

**Deliberate non-exceptions** that test the rules:
- Two invoices print a due date that disagrees with their printed terms. The printed date wins.
- Two invoices show a deposit, so Balance Due is less than Total. The label is the Total.
- Several invoices print terms in the due-date slot ("Due Date: Upon receipt", "Due Date: Net 15").
- "2/10 Net 30" terms appear.
- Remit-to names differ from the letterhead names.

**Renders.** Clean pages are vector PDFs from reportlab. For scans and photos, the clean page is rasterized with PyMuPDF and then degraded with OpenCV, and the result is wrapped as a JPEG in a PDF.

Scans:
- 150–200 dpi, mostly grayscale
- ±1.8° skew
- lid shadow on one edge and uneven lamp light
- specks, an occasional fold line, mild blur and noise
- JPEG quality 42–72
- small tickets laid on the scanner glass

Photos:
- page bow and curl
- perspective tilt and ±5° rotation
- desk, counter or steel-bench backgrounds, with a drop shadow
- warm color cast, light gradient and vignette
- sometimes a soft hand or phone shadow, or slight hand shake
- blur and noise, JPEG quality 58–82

The mix of renders follows each supplier's habits: route tickets and forms skew to scan or photo, and emailed SaaS invoices stay clean. Render counts are fixed at exactly 50/25/25.

**Checks.** `make_set.py` asserts that every non-null label value was drawn on the page in that supplier's printed format. A separate checker (not part of the set) re-derived every label from the page facts using its own rule code. It found 0 differences across due dates from terms, exception reasons, arithmetic, file paths and sha256. Two runs of `make_set.py` produce byte-identical PDFs and labels on the pinned versions. Sample pages from every layout and render were viewed by eye. No OCR or reading model was used.

**Judgment calls.**
- Freight shown below the subtotal (Fenwick) is labeled `total_mismatch`, because the rule is subtotal + tax vs. total.
- Prices bump about 3% each March for most suppliers, so line prices have odd cents.
- Statements are avoided: the previous balance on utility bills is always shown as paid in full, so the amount due equals the invoice total.
