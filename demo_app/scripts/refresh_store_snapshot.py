#!/usr/bin/env python3
"""Re-pull the demo class store from staging BigQuery.

Run this only on a machine where `gcloud` / `bq` is already logged in
(Emmanuel's Mac). The Streamlit site never runs this and never calls BigQuery.

    cd ContinentalRe
    python demo_app/scripts/refresh_store_snapshot.py

Overwrite demo_app/data/store_snapshot/{partners,classes,subclasses,meta}.json.
Does not write confirmations or the audit log. Does not write back to BigQuery.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from demo_app.store import SNAPSHOT_DIR, partner_name_to_cedant_broker  # noqa: E402

DATASET = "infrastructure-staging-418710.staging_mh_reinsurance_ai"


def _bq(sql: str) -> list:
    proc = subprocess.run(
        ["bq", "query", "--use_legacy_sql=false", "--format=json", "--max_rows=100000", sql],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr or proc.stdout)
        raise SystemExit(
            "bq query failed. Log in with `gcloud auth login` and `gcloud auth application-default login`, "
            "then retry. Do not paste credentials into this script."
        )
    text = proc.stdout.strip() or "[]"
    data = json.loads(text)
    return data if isinstance(data, list) else []


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def main() -> None:
    cedents = _bq(
        f"""
        SELECT cedent_id, name, reinsurer_id
        FROM `{DATASET}.cedents`
        WHERE deleted IS NULL OR deleted = FALSE
        ORDER BY name
        """
    )
    classes = _bq(
        f"""
        SELECT class_id, class_name, reinsurer_id, cedent_id, retention_limit,
               variations, aliases, keywords
        FROM `{DATASET}.insurance_class`
        WHERE deleted IS NULL OR deleted = FALSE
        """
    )
    subclasses = _bq(
        f"""
        SELECT subclass_id, class_id, subclass_name, reinsurer_id, cedent_id,
               retention_limit, variations, aliases
        FROM `{DATASET}.insurance_subclass`
        WHERE deleted IS NULL OR deleted = FALSE
        """
    )
    partners = []
    for row in cedents:
        name = str(row.get("name") or "").strip()
        try:
            cedant, broker = partner_name_to_cedant_broker(name)
        except ValueError:
            cedant, broker = name.upper(), ""
        partners.append({
            "partner_id": str(row.get("cedent_id") or "").strip().lower(),
            "name": name,
            "cedant": cedant,
            "broker": broker,
            "reinsurer_id": row.get("reinsurer_id"),
        })
    for row in classes:
        row["variations"] = _as_list(row.get("variations"))
        row["aliases"] = _as_list(row.get("aliases"))
        row["keywords"] = _as_list(row.get("keywords"))
        row["cedent_id"] = str(row.get("cedent_id") or "").strip().lower()
    for row in subclasses:
        row["variations"] = _as_list(row.get("variations"))
        row["aliases"] = _as_list(row.get("aliases"))
        row["cedent_id"] = str(row.get("cedent_id") or "").strip().lower()

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("partners.json", partners),
        ("classes.json", classes),
        ("subclasses.json", subclasses),
    ):
        path = SNAPSHOT_DIR / name
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    meta = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_project": "infrastructure-staging-418710",
        "source_dataset": "staging_mh_reinsurance_ai",
        "partner_count": len(partners),
        "class_count": len(classes),
        "subclass_count": len(subclasses),
        "note": "Demo snapshot only. Confirmations update local files and are never synced to production.",
    }
    (SNAPSHOT_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(
        f"Wrote {len(partners)} partners, {len(classes)} classes, "
        f"{len(subclasses)} subclasses to {SNAPSHOT_DIR}"
    )


if __name__ == "__main__":
    main()
