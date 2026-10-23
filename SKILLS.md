# Project working guidance

- The Streamlit entry point is `demo_app/app.py`. It runs the vendored backend cleaner under `demo_app/_backend/src/domain/cre_cleaner`, through `demo_app/engine.py`.
- Treat `../reinsurance-ai-backend/src/domain/cre_cleaner` as the source for that vendored engine. Preserve its `src.domain.cre_cleaner` package namespace and include package templates and new modules when syncing. Do not copy bytecode or runtime logs.
- The top-level `cre_cleaner` package is a separate legacy CLI/API package. Inspect its callers before changing its import namespace or paths.
- Keep paid claims, outstanding claims and premiums distinct. Preserve source workbooks and existing local changes.
- Demo partner/class data and local confirmations live under `demo_app/data/store_snapshot`. Do not overwrite them while syncing the engine.
- Verify the vendored package in an isolated Python import path and run a Streamlit startup/integration smoke check after a sync. Keep the demo dependency file consistent with the modules it actually imports.
- Do not commit environment files, credentials, private source workbooks, runtime logs or generated cleaning outputs.
