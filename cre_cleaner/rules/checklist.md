# CRE Cleaner Checklist (mapped to modules)

1. **Never invent values** — `quarterly.py` only copies source cells via `map_columns` / `normalize.parse_number`.
2. **Never add balancing rows** — writers only emit parsed transaction rows (`io_excel.write_*`).
3. **Never silently drop genuine transactions** — uncertain rows → exceptions sidecar (`filters.py`, `quarterly.py`).
4. **Do not merge Paid with Outstanding** — `parse_claims_file` returns separate lists; pipeline writes separate sheets.
5. **Preserve negatives** — `normalize.parse_number` keeps sign; accounting `( )` supported.
6. **Monthly → quarter calendar order** — `adapters/aiico_ark.py` + `quarterly.merge_monthly_premiums`.
7. **Audit trail** — default: sidecar `_exceptions.xlsx` / `_source_audit.xlsx` (not in upload workbook). Opt-in embed via `--include-audit-sheets`.
8. **Upload headers win** — `config.PREMIUM_COL_MAP` / `CLAIMS_COL_MAP` (Bisola/validator schema). TEMPLATE is layout seed only.
9. **TREATY band label** — retention / treaty / facultative triples; unique `RET/TREATY/FAC` SI, premium, and amount names.
10. **IDs as text, dates as dates** — `normalize.as_text_id`, `parse_date`; Excel `@` / `DD/MM/YYYY` in `io_excel`.
11. **Skip title/subtotal/blank/NIL** — `filters.py`; uncertain → Exception Log.
12. **Duplicates flagged not deleted** — `reconcile.flag_duplicate_claims`.
13. **Class-split** — `{Class} - PREMIUM|CLAIMS|OUTSTANDING`; Facultative ignored unless `--include-fac`. Class order: tab name > row CLASS > section banner (e.g. `FIRE PAID CLAIM`, `ENGINERRING`) > mapping > exception. No `Other` sheet: unresolved rows go to the exceptions sidecar (`class_unresolved`). `GEN ACCIENT`-style typos → General Accident; `CASUALTY` not mapped (pending Bisola). CLAIMS/OUTSTANDING: column A fully empty, headers B–S, title B1.
14. **Upload hygiene** — `io_excel.enforce_upload_hygiene`: real header columns only (premium A–R, claims/OST A–R), nothing right of the last header, no whitespace-only cells, no hidden rows/cols, never write `#VALUE!`/`#REF!`/… (blanked + logged in exceptions sidecar), explicit number formats (no inherited TEMPLATE date formats). Check: `tests/validate_upload.py`.
