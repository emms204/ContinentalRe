# cre_cleaner — Continental Re Bordereau Cleaner

Deterministic, rule-based Excel cleaning pipeline for Continental Re quarterly bordereaux.
Built for Emmanuel Osanebi / MasteryHiveAI. v1 focuses on **AIICO ARK Q1 2025**.

## Design notes (Cleaning Manual + Bisola/Franklin walkthrough)

- Original detailed source is **evidence** — never invent amounts, never add balancing rows, never silently drop genuine transactions.
- **Do not merge** Paid Claims with Outstanding Claims.
- **Preserve negative values** (minus signs / credit notes) — critical for platform upload.
- Monthly premium sources are merged into the quarter in **calendar order** (Jan→Feb→Mar for Q1).
- Audit columns live on **SOURCE AUDIT** / **EXCEPTIONS** (DATA QUALITY LOG), not jammed into fixed TEMPLATE headers.
- Column headers/structure still come from local **TEMPLATE.xlsx** (CHANNEL / SUB CHANNEL left blank if source lacks them).
- **Default output = Bisola-style class-split sheets**: `{ClassLabel} - PREMIUM|CLAIMS|OUTSTANDING`. Empty class×type combos are omitted. S/NO restarts at 1 on each sheet.
- Optional `--collapsed` writes the legacy single `PREMIUM BORDEREAU` / `CLAIMS BORDEREAU` / `OUTSTANDING LOSS BORDEREAU` sheets.
- Premium middle allocation block is **SURPLUS** (not TREATY) on TEMPLATE.
- Identifiers as text; dates as real dates `dd/mm/yyyy`.
- Skip title / subtotal / blank / NIL rows; keep uncertain rows in EXCEPTIONS.

## Class label mapping (AIICO ARK → Bisola)

| Source CLASS / sheet hint | Bisola sheet label |
|---------------------------|--------------------|
| FIRE / Fire | Fire |
| ENGINEERING / ENG | Engineering |
| BOND | Bond |
| MARINE HULL / MHULL / HULL | Marine Hull |
| MARINE CARGO / MCARGO | Marine Cargo |
| MISCELLANEOUS ACCIDENT / MISC ACDNT / MISC | General Accident |
| GOODS IN TRANSIT, ALL RISKS, BURGLARY, MONEY, FIDELITY GUARANTEE, PUBLIC LIABILITY, PRODUCT LIABILITY, PROFESSIONAL INDEMNITY, D&O, MOTOR, … | General Accident |
| FACULTATIVE / FAC OBLIG | Facultative *(kept so rows are not dropped; Bisola gold omits a Facultative sheet)* |

See `cre_cleaner/class_labels.py` for the full map.

## Setup

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI/ContinentalRe
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run (AIICO ARK Q1 2025 smoke test)

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI/ContinentalRe
source .venv/bin/activate
python -m cre_cleaner run --cedant AIICO --broker ARK --year 2025 --quarter 1 \
  --raw-dir AIICO/ARK/2025 --template TEMPLATE.xlsx --out-dir output
```

Output: `output/AIICO_ARK_2025_Q1_cleaned.xlsx` (class-split by default).

Legacy collapsed workbook:

```bash
python -m cre_cleaner run --cedant AIICO --broker ARK --year 2025 --quarter 1 \
  --raw-dir AIICO/ARK/2025 --template TEMPLATE.xlsx --out-dir output --collapsed
```

## Inspect a raw file

```bash
python -m cre_cleaner inspect --file "AIICO/ARK/2025/JANUARY PREM 2025 LOCAL (1).xlsx"
```

## Output workbook sheets (default)

| Sheet | Purpose |
|-------|---------|
| SUMMARY | Counts, sums, per-class row counts, run metadata |
| `{Class} - PREMIUM` | Premium rows for that class (TEMPLATE premium columns) |
| `{Class} - CLAIMS` | Paid claims for that class |
| `{Class} - OUTSTANDING` | Outstanding for that class |
| SOURCE AUDIT | Per source sheet: header row, kept/skipped |
| EXCEPTIONS | DATA QUALITY LOG (missing fields, duplicates, uncertain rows) |

Class labels typically seen for AIICO ARK: Fire, General Accident, Marine Cargo, Marine Hull, Engineering, Bond, Facultative.

## Known gaps (v1)

- No multi-treaty column expansion beyond Retention / Surplus / Facultative triples.
- Bisola further splits some Fire/Engineering outstanding into `2ND SURPLUS … - OS`; cre_cleaner keeps them on `{Class} - OUTSTANDING`.
- No Bisola golden-file compare yet.
- Facultative premium rarely present in AIICO ARK local monthly files — FAC columns left blank.
- Claims PPN % columns usually absent in source — amounts mapped, % left blank.
- Very large outstanding sheets may include many historical reserves; all parseable rows are kept (no date filter in v1).

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -q
# or:
python tests/test_smoke_units.py
```
