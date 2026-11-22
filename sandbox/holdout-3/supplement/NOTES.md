# NOTES

Generated a set of 60 fictional cross-border invoices for a US small business (Horizon Analytics LLC).

Country distribution (approximate):
- US: 12
- CA: 8
- GB: 8
- IE: 6
- DE: 6
- FR: 6
- NL: 5
- IT: 3
- ES: 3

Currency mix based on typical cross-border practices:
- US suppliers: always USD
- Canadian: mostly CAD, some USD
- UK: mix of GBP and USD
- Ireland and continental Europe: predominantly EUR, some USD for US customers (common for convenience in trade)

Date conventions follow local norms (day-first in Europe/UK, month-first in US, year-first in CA).
Number styles match local (comma decimal in DE/NL/IT/ES/FR, period in US/CA/GB/IE).

Layouts vary by supplier and country (classic US, EU table, UK VAT style, Canadian bilingual, German formal, French TTC, Italian, Spanish, minimal, boxed). Each supplier uses consistent styling.

Renders assigned independently of country: ~30 clean, 15 scan, 15 photo.
Scans ~200 dpi with slight rotation and noise; photos with blur, lighting gradient, rotation.

Terms include local language variants and some EOM/fin de mois which leave due_date null per rules.
Exceptions include foreign_currency (non-USD), missing_due_date (EOM etc.), total_mismatch (occasional arithmetic error), and rare due_before_invoice.

All names, addresses, tax IDs, and bank details are fictional. Currency is always identifiable via code or symbol.
