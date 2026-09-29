# cre_cleaner — Continental Re Bordereau Cleaner

Deterministic, rule-based Excel cleaning pipeline for Continental Re quarterly bordereaux.
Built for Emmanuel Osanebi / MasteryHiveAI. v1 focuses on **AIICO ARK**.

## Design notes (Cleaning Manual + Bisola / upload validator)

- Original detailed source is **evidence** — never invent amounts, never add balancing rows, never silently drop genuine transactions.
- **Do not merge** Paid Claims with Outstanding Claims.
- **Preserve negative values** (minus signs / credit notes) — critical for platform upload.
- Monthly premium sources are merged into the quarter in **calendar order**.
- **Default output = upload-ready workbook**:
  - Bisola-style class-split sheets: `{ClassLabel} - PREMIUM|CLAIMS|OUTSTANDING`
  - Premium band **TREATY** (not SURPLUS); unique `RET/TREATY/FAC SUM INSURED` + `RET/TREATY/FAC PREMIUM`
  - Claims/OST unique `RET AMOUNT` / `TREATY AMOUNT` / `FAC AMOUNT` (+ `SUM INSURED` column)
  - **No** `EXCEPTIONS` / `SOURCE AUDIT` sheets in the upload file (validator treats them as invalid classes)
  - **No** `Facultative - *` sheets (Continental: FAC is not a treaty class)
- Audit logs are written as **sidecar files** next to the cleaned xlsx:
  - `{stem}_exceptions.xlsx`
  - `{stem}_source_audit.xlsx`
- Optional `--include-audit-sheets` embeds audit sheets in the workbook (legacy/debug).
- Optional `--include-fac` keeps Facultative source sheets / output tabs.
- Optional `--collapsed` writes legacy single PREMIUM/CLAIMS/OUTSTANDING sheets.
- `--proportion-headers gold|distinct|plain` (default `gold`): premium proportion column names. `gold` = Bisola's most recent distinct naming (RET/TREATY/FAC PROPORTION %, her Q4 2025 Bond sheet) — same text as `distinct`; `plain` = `PROPORTION %` ×3 (literal Q2 2025 gold; duplicate names).
- `--out-name FILE.xlsx` overrides the output filename (sidecars follow the stem).
- CLAIMS/OUTSTANDING sheets keep **column A fully empty** with the title in B1 and headers in B–S (TEMPLATE.xlsx / Bisola gold layout) — default ON (`--claims-leading-blank`). `--no-claims-leading-blank` starts claims headers at column A instead.
- Upload sheets are built fresh (TEMPLATE = header style seed only): premium exactly 18 cols A–R, claims/outstanding 18 header cols B–S with column A empty (19 cols A–S), nothing hidden, no stray/whitespace cells, no Excel error literals, explicit number formats. Validate with `python tests/validate_upload.py OUTPUT.xlsx [GOLD.xlsx]`.
- TEMPLATE.xlsx is used only as a layout/style seed; **upload headers come from Bisola/validator schema** in `config.py`.

## Class label mapping (AIICO ARK → Bisola)

Class resolution order (Bisola's Cleaning Manual): clear source grouping (tab name) > row-level CLASS column > section/sheet heading > mapping guide > exception.
When a tab carries no class (combined `2nd surplus` premium tabs, `2ND SURPLUS TREATY` paid tab) the class comes from the lone **section-banner row** above the rows (`FIRE`, `ENGINERRING`, `FIRE PAID CLAIM`, `ENGINEERING PAID CLAIM`, `MARINE PAID CLAIM` → Marine Cargo, …). Banner rows are never output; the banner text is recorded in the source-audit notes. Rows still without a class go to the exceptions sidecar (`class_unresolved`) — there is no `Other` sheet. These rows land on the plain class sheets (no `2ND SURPLUS …` sheets). `CASUALTY` / `CAS` are **not** mapped yet (pending Bisola) and keep a title-cased sheet (`Casualty - PREMIUM`, `Cas - PREMIUM`).

| Source CLASS / sheet hint | Bisola sheet label |
|---------------------------|--------------------|
| FIRE / Fire | Fire |
| ENGINEERING / ENG | Engineering |
| BOND | Bond |
| MARINE HULL / MHULL / HULL | Marine Hull |
| MARINE CARGO / MCARGO | Marine Cargo |
| MISCELLANEOUS ACCIDENT / MISC ACDNT / MISC | General Accident |
| GEN ACCIENT / GEN ACC / GEN. ACCIDENT (typos) | General Accident |
| GOODS IN TRANSIT, ALL RISKS, BURGLARY, MONEY, … | General Accident |
| FACULTATIVE / FAC OBLIG | Facultative *(ignored by default; `--include-fac` to keep)* |

See `cre_cleaner/core/class_labels.py` for the full map.

## Layout

```
ContinentalRe/
  cre_cleaner/          # cleaning package + API
    adapters/           # cedant/broker discovery
    core/               # parse, map, merge, reconcile
    io/                 # Excel + PDF helpers
    api.py  cli.py  pipeline.py  config.py  models.py  paths.py
  demo_app/             # Streamlit UI
  tests/
  templates/            # TEMPLATE.xlsx (style seed)
  docs/                 # REPORT.md and notes
  data/
    raw/newdata/        # source bordereaux
    raw/next5/          # Next 5 Cedant pack
    gold/               # Bisola cleaned reference
  output/               # run artifacts (gitignored)
```

## Setup

Prefer the **MasteryHiveAI repo-root** uv environment (Python 3.12). ContinentalRe does not need its own `.venv`.

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI
source .venv/bin/activate
# if anything from ContinentalRe/requirements.txt is missing:
uv pip install -r ContinentalRe/requirements.txt
```

## Run (AIICO ARK Q2 2025)

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI/ContinentalRe
source ../.venv/bin/activate
python -m cre_cleaner run --cedant AIICO --broker ARK --year 2025 --quarter 2 \
  --raw-dir data/raw/newdata/AIICO/ARK/2025 \
  --template templates/TEMPLATE.xlsx --out-dir output
```

Outputs:
- `output/AIICO_ARK_2025_Q2_cleaned.xlsx` (upload workbook)
- `output/AIICO_ARK_2025_Q2_cleaned_exceptions.xlsx`
- `output/AIICO_ARK_2025_Q2_cleaned_source_audit.xlsx`

## Inspect a raw file

```bash
python -m cre_cleaner inspect --file "data/raw/newdata/AIICO/ARK/2025/JANUARY PREM 2025 LOCAL (1).xlsx"
```

## Output workbook sheets (default upload)

| Sheet | Purpose |
|-------|---------|
| SUMMARY | Counts, sums, per-class row counts, run metadata |
| `{Class} - PREMIUM` | Premium rows (TREATY band + unique premium/SI names) |
| `{Class} - CLAIMS` | Paid claims (unique RET/TREATY/FAC AMOUNT) |
| `{Class} - OUTSTANDING` | Outstanding claims |

Class labels typically seen for AIICO ARK: Fire, General Accident, Marine Cargo, Marine Hull, Engineering, Bond, Motor. Additional own classes when present in source: Terrorism & PVT, Agriculture, Aviation, Oil & Gas, Travel. Bare **MARINE** (Hull vs Cargo unclear) goes to the exceptions sidecar, not a sheet. CASUALTY stays unmapped pending Bisola.

## Known gaps / behaviour notes

- Upload schema stays 18 columns (one TREATY band). Extra treaty layers (2nd/3rd surplus, quota) are parsed, logged as `multiple_treaty_layers`, and shown in SUMMARY reconciliation — not as extra upload columns.
- One cleaned workbook **per currency** (`…_USD_cleaned.xlsx` etc.); amounts are never summed across currencies.
- Hidden source rows/sheets are skipped and logged; visibility that cannot be read is itself logged.
- Adapters: AIICO/ARK verified against Bisola gold. CHI/SCIB, CUSTODIAN/SCIB, MUTUAL BENEFITS/ARK, NEM/SCIB, ROYAL EXCHANGE/DIRECT are first-pass and labelled unverified. Unknown cedant/broker pairs raise — no silent AIICO fallback.
- Bisola further splits some Fire/Engineering outstanding into `2ND SURPLUS … - OS`; cre_cleaner keeps them on `{Class} - OUTSTANDING`.
- Facultative premium rarely present in AIICO ARK local monthly files — FAC columns left blank.
- Claims PPN % columns usually absent in source — filled as amount/total_claims (Bisola-style fractions).
- Very large outstanding sheets may include many historical reserves; date checks flag (do not drop) loss dates after the quarter.

## Demo UI (Streamlit)

Thin local app for stakeholder demos: **Run → Compare → Review**.

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI/ContinentalRe
source ../.venv/bin/activate
streamlit run demo_app/app.py
```

Phase 1: upload **one** Excel file; year/quarter come from date columns; mode is
Premium / Claims / Outstanding / All. Sample data: `data/raw/newdata/AIICO/ARK/2025`.

## Provisional API (FastAPI)

Grouping `cedant/broker/year/quarter/currency` is provisional until agreed with Tyrone.

Requires `CRE_CLEANER_API_KEY` (send as `X-API-Key`). Uploads are capped by
`CRE_CLEANER_MAX_UPLOAD_MB` (default 50) and never written under `output/api_runs`.
PDFs need `LLAMA_CLOUD_API_KEY`. Year/quarter form fields are optional
(default: read from date columns in the sheets).

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI/ContinentalRe
source ../.venv/bin/activate
export CRE_CLEANER_API_KEY=dev-secret
export LLAMA_CLOUD_API_KEY=…   # when sending PDFs
uvicorn cre_cleaner.api:app --reload --port 8090
# POST /clean       multipart: cedant, broker, files=… (year/quarter optional)
# POST /clean/json  same; metrics only
# GET  /adapters
# GET  /health
```

## Tests

```bash
cd /Users/osabobo/Downloads/MasteryHiveAI/ContinentalRe
source ../.venv/bin/activate
uv run python tests/test_smoke_units.py
```
