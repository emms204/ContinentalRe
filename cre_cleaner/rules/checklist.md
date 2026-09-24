# CRE Cleaner Checklist (mapped to modules)

1. **Never invent values** — `quarterly.py` only copies source cells via `map_columns` / `normalize.parse_number`.
2. **Never add balancing rows** — writers only emit parsed transaction rows (`io_excel.write_*`).
3. **Never silently drop genuine transactions** — uncertain rows → `EXCEPTIONS` (`filters.py`, `quarterly.py`).
4. **Do not merge Paid with Outstanding** — `parse_claims_file` returns separate lists; pipeline writes separate sheets.
5. **Preserve negatives** — `normalize.parse_number` keeps sign; accounting `( )` supported.
6. **Monthly → quarter calendar order** — `adapters/aiico_ark.py` + `quarterly.merge_monthly_premiums`.
7. **Audit trail** — `SOURCE AUDIT` + `EXCEPTIONS` sheets (`io_excel.py`); not forced onto fixed TEMPLATE headers.
8. **TEMPLATE headers win** — `config.PREMIUM_COL_MAP` / `CLAIMS_COL_MAP`; CHANNEL/SUB CHANNEL left blank if absent.
9. **SURPLUS block label** — retention / surplus / facultative triples in `map_columns.detect_premium_allocation_blocks`.
10. **IDs as text, dates as dates** — `normalize.as_text_id`, `parse_date`; Excel `@` / `DD/MM/YYYY` in `io_excel`.
11. **Skip title/subtotal/blank/NIL** — `filters.py`; uncertain → Exception Log.
12. **Duplicates flagged not deleted** — `reconcile.flag_duplicate_claims`.
13. **Class v1** — class from sheet name / SUB CLASS column into CLAIMS `CLASS`; premium kept on one sheet with class_hint in SOURCE AUDIT.
