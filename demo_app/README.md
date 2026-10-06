# Bisola bordereau demo

Streamlit site for uploading bordereaux, cleaning them with the current workbook cleaner, and confirming unknown class labels. Runtime reads only `demo_app/data/store_snapshot/`. It does not call BigQuery and it does not write confirmations back to production.

Shared password: `bisola-demo` (override with env `DEMO_PASSWORD` or Streamlit secret `DEMO_PASSWORD` — the app reads the env var).

## Architecture

The cleaner is a copy of `reinsurance-ai-backend/src/domain/cre_cleaner` under `demo_app/_backend/`. The UI calls that copy. Partner names, insurance classes, and subclasses come from the JSON snapshot. **Keep** appends an alias to `classes.json` and a line to `audit.jsonl` on this machine only.

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
3. Upload one or more `.xls` / `.xlsx` files and press **Clean**.
4. If a label is unknown, the confirm step lists file, sheet, record count, and a suggestion when one exists. **Keep** stores the alias for that partner and re-runs. **Ignore** drops those rows for this run only. Bare `MARINE` has no suggestion; pick Marine Hull or Marine Cargo, or ignore.
5. Download the workbook or zip. Warnings such as `period_override_conflict` are shown above the download.

Year is required. A strong year mismatch ignores the file. Weak or undated files are cleaned under the selected year and quarter, with a warning. Year plus quarter returns one workbook. Year only returns a zip when more than one quarter is produced.

Compare still opens local gold files when they exist. A missing gold file does not block cleaning.

## Snapshot (shipped)

See `demo_app/data/store_snapshot/meta.json` for the export time and row counts.

## Limits versus production

- No GCS upload, no signed URLs, no BigQuery reads or writes.
- Confirms do not appear in Tyrone's app or in staging `insurance_class`.
- Container restart clears confirms made only on the hosted site.
- PDF conversion is off (no Llama Cloud key).
- Gold compare and the full AIICO regression suite are not part of this deploy.
