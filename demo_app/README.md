# Bisola bordereau demo

Streamlit site for uploading bordereaux, cleaning them with the current workbook cleaner, and confirming unknown class labels. Runtime uses the local cleaner registry and `demo_app/data/store_snapshot/`. It does not call BigQuery and it does not write confirmations back to production.

Shared password: `bisola-demo` (override with env `DEMO_PASSWORD` or Streamlit secret `DEMO_PASSWORD` — the app reads the env var).

## Architecture

The cleaner is a copy of `reinsurance-ai-backend/src/domain/cre_cleaner` under `demo_app/_backend/`. The UI calls that copy. Existing partner IDs and alias-store backing come from the JSON snapshot. Selectable classes come from each adapter’s approved cleaner vocabulary, so missing snapshot class rows do not leave an empty dropdown. Single-class partners retain their permitted class scope. The partner picker also includes every registered adapter missing from that snapshot, marked **demo adapter**, with stable local-only identifiers. These entries are computed in memory and do not create shared partners or modify the snapshot. For these entries, unresolved records use run-specific class decisions; no alias is saved. For ordinary labels with a real snapshot class row, confirming a class saves a local alias and audit entry. Choices without that backing and contradictory source classes use explicit source-record IDs for the current run and save no alias. Genuine source contradictions show both labels and require confirmation or exclusion.

The vendored cleaner was synced on 2026-10-21, including current backend changes
and the approved Royal Exchange/DIRECT General Accident subclasses. All source
package files and templates are recorded by SHA-256 in
`_backend/cleaner-sync.json`. The legacy top-level `cre_cleaner/` package is not
used by this Streamlit app. Native source layouts require `fastexcel` and
`pyarrow`, included in the demo requirements. Syncing the registry automatically
adds missing adapter pairs to the Partner dropdown for local testing. Alias
spellings already represented by snapshot partners do not create extra demo entries.
Snapshot refresh is still required to obtain real shared partner IDs and their class-store data.

Sync verification: `tests/test_demo_backend_sync.py` checks the complete vendored
file manifest, byte equality with the backend checkout when present, isolated
demo imports, Royal Exchange General Accident premium/paid/outstanding exports,
and startup without an optional secrets file. A Streamlit AppTest additionally
cleaned the real Royal Exchange 2021 Q1 General Accident and non-accident sources
together, then opened Run and Review without exceptions. The Review reader
and the batch panel import the same vendored package as Run.

Local startup quietly falls back to the documented demo password when no
`secrets.toml` exists; configured secret passwords still take precedence.

## Refresh the snapshot (needs gcloud)

On Emmanuel's Mac, with `bq` already logged in:

```bash
cd ContinentalRe
python demo_app/scripts/refresh_store_snapshot.py
```

That overwrites `partners.json`, `classes.json`, `subclasses.json`, and `meta.json` from `infrastructure-staging-418710.staging_mh_reinsurance_ai`. It does not upload anything. Keywords are stored but never used for mapping.

## Run locally

```bash
cd ContinentalRe
python -m pip install -r demo_app/requirements.txt
streamlit run demo_app/app.py
```

Open the URL Streamlit prints. Password `bisola-demo`.

## Deploy (Streamlit Community Cloud)

This repo is not already connected from this session. Emmanuel does the clicks:

1. Push the `ContinentalRe` repo (including `demo_app/data/store_snapshot/`) to GitHub. Do not commit `.env`, service-account JSON, or `demo_app/.streamlit/secrets.toml`.
2. Sign in at https://share.streamlit.io with the GitHub account that can see that repo.
3. **Create app** → select the repo and branch.
4. Main file path: `demo_app/app.py`
5. Click **Advanced settings** and set **Python version to 3.11 or 3.12** (not 3.13/3.14). The pins in `requirements.txt` need binary wheels; on 3.14 Streamlit Cloud tries to build Pillow/pandas from source and fails on zlib. Python cannot be changed after deploy — delete and redeploy if the wrong version was chosen.
6. Requirements file: `demo_app/requirements.txt` if the form asks (otherwise the app directory's `requirements.txt` is used when the main file lives in `demo_app/`).
7. Optional secret: `DEMO_PASSWORD` = the password you want Bisola to use. If omitted, the password is `bisola-demo`.
8. Deploy. Copy the `*.streamlit.app` URL and send it to Bisola.

Confirmations written on that URL live on the Streamlit container disk. They survive a second clean in the same running app. A reboot or redeploy of the app restores the snapshot from Git and drops confirms made only on the site. That is intentional: nothing is synced to production.

## What Bisola does

1. Enter the password.
2. Pick a partner (for example `AIICO(ARK)`), a year, and optionally a quarter.
3. Upload one or more `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, or `.ods` workbooks and press **Clean**. Excel binary (`.xlsb`) and OpenDocument (`.ods`) workbooks use the included `fastexcel` reader.
4. If a label is unknown, the confirm step lists file, sheet, record count, and a suggestion when one exists. **Keep** stores the alias for that partner and re-runs. **Ignore** drops those rows for this run only. Bare `MARINE` has no suggestion; pick Marine Hull or Marine Cargo, or ignore.
5. Download the workbook or zip. Exclusions are flagged beside the downloads. Expand **Excluded files** for reasons, or **Cleaning notices** for category counts and full source details. Identical notices at the same file/sheet/row are grouped with occurrence counts. **Download full diagnostic report** exports every original notice as CSV. Newly cleaned runs include sheet and row locations when supplied by the cleaner.

Year is required. A strong year mismatch ignores the file. Weak or undated files are cleaned under the selected year and quarter, with a warning. Year plus quarter returns one workbook. Year only returns a zip when more than one quarter is produced.

During cleaning, a status panel shows period checks, the current quarter group, and download preparation. Its progress bar marks stages rather than predicting time remaining. Planning uses a request-local workbook cache so partner, broker and period checks reuse each parsed workbook. That cache is released before cleaning starts. If the native reader cannot initialize its logger, the shared cleaner falls back to the standard reader for XLS/XLSX/XLSM and records a `native_reader_fallback` notice. These files can continue without restarting the server. Other native panics stop the run with a visible error.

The demo navigation contains **Run** and **Review**. Review opens individual
cleaned workbooks and ZIP deliveries: select **Workbook inside ZIP**, then a
sheet to preview its first 20 data rows. Switching the workbook reads only that
archive member and does not modify the original ZIP. Archives without Excel
files or unreadable workbooks show a message instead of a traceback.

Before downloads, **Cleaning results** shows published Premium, Claims (paid)
and Outstanding row counts, with a status and reason for each period/type.
“Not detected” is different from an extraction failure or an explicit NIL return.
Failed or mapping-blocked groups remain in the report even if other quarters
produced workbooks. Expand **Source and extraction details** for filenames,
sheets, parsed source counts and diagnostics. Published counts sum every currency
workbook; they do not certify financial accuracy or prove unclassified tables
contain no missing transactions. The shared backend also records this report in
`PipelineResult.summary["cleaning_coverage"]`.

## Snapshot (shipped)

See `demo_app/data/store_snapshot/meta.json` for the export time and row counts.

Royal Exchange DIRECT and AGRIC include locally seeded class rows so the manual
mapping dropdown works in the demo. These rows carry `demo_seed` and a source
description; `meta.json` records their provenance separately from the staging
export timestamp. DIRECT uses the nine mapped template classes and AGRIC uses
Agriculture. They are demo configuration, not evidence of staging class records.
A full staging snapshot refresh replaces this local seed data.

## Limits versus production

- No GCS upload, no signed URLs, no BigQuery reads or writes.
- Confirms do not appear in Tyrone's app or in staging `insurance_class`.
- Container restart clears confirms made only on the hosted site.
- PDF conversion is off (no Llama Cloud key).
- Gold compare and the full AIICO regression suite are not part of this deploy.


AXA DIRECT outstanding overlap handling: when a separate Energy return repeats an entire NGN Energy block already present in the broader outstanding return, the cleaner includes that block once after comparing raw and parsed values and duplicate counts. The diagnostic notice names both sources. USD remains separate. Conflicting source blocks require review and block that period instead of choosing a source automatically. Reclean earlier affected downloads to apply this correction.


CHI SCIB Excel handling now recognizes native compact claim labels, cover dates, loss descriptions and retention/treaty allocations. CHI quota-share premium bands keep their own SI, percentage and premium columns. Fractional percentages are normalized to percentage points only with source-amount evidence. Explicit UW years/banners take precedence over an audited policy-start-year fallback. PDF ingestion remains separate and unchanged; Excel-only CHI batches omit PDF-only premiums.


Custodian SCIB headerless claims can recover mixed Policy/Claim/Insured and Insured/Policy/Claim orders when identifiers, dates and treaty arithmetic verify each payment. Diagnostics record source rows and original column roles; unsupported candidates remain unresolved. Repeated claim numbers remain separate payments. The recovery is shared with the backend and does not affect UAIB or explicit-header sheets.


Pending class review is displayed separately from completed results, with provisional quarter checks and no published-result metric cards. Marine review shows each source record, loss description and an advisory Hull/Cargo suggestion; Apply confirms only the listed content-bound records for that run. Uncertain records remain unresolved, and no general MARINE alias is saved. A modified workbook requires fresh validation. HEIRS(HIB) has local demo template-vocabulary choices; these seeds do not create staging classes. Bisola-approved General Accident subclasses now use shared cleaner mappings; MD/BI remains a manual decision.

Source class conflicts are reviewed per source record. The card displays the original section heading, row class, row number and policy. Candidate classes appear first, with no default choice; each confirmed choice becomes a content-bound row mapping, never a reusable CLASS CONFLICT alias. Record-only choices use the cleaner template vocabulary even when the partner has no local alias-class snapshot rows. Ordinary alias confirmations still require snapshot class rows.

HILAL class handling (2026-10-22): CESSION/NUMBER preserves per-row covers and mixed premium sections recognize class headings beside allocation labels. Exact ICC A variants resolve Marine Cargo. Date-valued CLASS cells remain visible in source audit but require a reliable class. Annual batches can resolve these using explicit matching policy/insured/currency and compatible cover-period evidence from other accepted uploaded quarters. Bare MARINE remains for review when evidence cannot distinguish Hull/Cargo. Re-run Clean to refresh prior results.

HILAL 2025 claims: unknown subclasses now use an explicit parent heading in their own claims block. GROUP FAMILY and GL under LIFE resolve Life without global aliases; the audit retains both the subclass and heading row. GOOD IN TRANSIT is an approved exact General Accident spelling. Totals/new blocks bound heading scope. Re-run Clean to refresh existing review results.

JORDANS source review (2026-10-22): agreeing source subclasses and record covers resolve automatically, while incompatible headings and record covers show both candidates and an advisory product suggestion for per-record confirmation. Choices work without saved class-store entries and are not saved as aliases. Verified headerless Q4 layouts recover source premium records with their physical row numbers and signed values preserved. Re-run Clean on the original annual upload to replace cached review state.

SCIB premium cover column (2026-10-22): the validated debit/credit layout recognizes unlabelled source cover descriptions in column B. Q3 2022 Prem Bordreaux 2 now resolves its explicit Fire covers, and mixed sheets use row covers rather than a blanket Fire heading. Unknown covers such as BETTER HOME GUARD still require review. Source financial fields remain unchanged; re-run Clean to refresh results.
