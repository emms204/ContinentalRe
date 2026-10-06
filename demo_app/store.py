"""Local class-store snapshot. Never calls BigQuery.

Reads ``demo_app/data/store_snapshot/``. Confirm/keep appends an alias onto
the matching ``insurance_class`` row for that partner and appends an audit
line. Nothing here syncs to production.
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()

SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "store_snapshot"
PARTNERS_PATH = SNAPSHOT_DIR / "partners.json"
CLASSES_PATH = SNAPSHOT_DIR / "classes.json"
SUBCLASSES_PATH = SNAPSHOT_DIR / "subclasses.json"
CONFIRMATIONS_PATH = SNAPSHOT_DIR / "confirmations.json"
AUDIT_PATH = SNAPSHOT_DIR / "audit.jsonl"

_PARTNER_NAME_RE = re.compile(r"^(?P<cedant>.+?)\((?P<broker>.+)\)$")


def partner_name_to_cedant_broker(name: str) -> tuple[str, str]:
    """Same parse as the backend ``partner_name_to_cedant_broker``."""
    raw = (name or "").strip()
    if not raw:
        raise ValueError("Partner name is empty; cannot resolve cedant/broker")
    match = _PARTNER_NAME_RE.fullmatch(raw)
    if match:
        cedant = match.group("cedant").strip().upper()
        broker = " ".join(match.group("broker").strip().upper().replace("-", " ").split())
        return cedant, broker
    parts = raw.split()
    if len(parts) == 1:
        return parts[0].upper(), "DIRECT"
    return " ".join(parts[:-1]).upper(), parts[-1].upper()


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def partners() -> List[Dict[str, Any]]:
    rows = _read_json(PARTNERS_PATH, [])
    if not isinstance(rows, list) or not rows:
        raise FileNotFoundError(
            f"Partner snapshot is missing or empty ({PARTNERS_PATH}). "
            "Run demo_app/scripts/refresh_store_snapshot.py on a machine with gcloud, "
            "then redeploy. This site does not call BigQuery."
        )
    return rows


def partner_by_id(partner_id: str) -> Optional[Dict[str, Any]]:
    pid = (partner_id or "").strip().lower()
    for row in partners():
        if str(row.get("partner_id") or "").strip().lower() == pid:
            return row
    return None


def classes_for(partner_id: str) -> List[Dict[str, Any]]:
    pid = (partner_id or "").strip().lower()
    rows = _read_json(CLASSES_PATH, [])
    return [
        row for row in rows
        if str(row.get("cedent_id") or "").strip().lower() == pid
    ]


def subclasses_for(partner_id: str) -> List[Dict[str, Any]]:
    pid = (partner_id or "").strip().lower()
    rows = _read_json(SUBCLASSES_PATH, [])
    return [
        row for row in rows
        if str(row.get("cedent_id") or "").strip().lower() == pid
    ]


def append_alias(partner_id: str, store_class: str, raw_label: str, *, confirmed_by: str,
                 template_class: str) -> bool:
    """Append ``raw_label`` to aliases of ``store_class`` for this partner.

    Returns True when the snapshot changed. Keywords are never written.
    """
    pid = (partner_id or "").strip().lower()
    store = str(store_class or "").lower().strip().replace(" ", "_")
    label = str(raw_label or "").strip()
    if not pid or not store or not label:
        raise ValueError("partner, store class, and label are required")
    with _LOCK:
        rows = _read_json(CLASSES_PATH, [])
        changed = False
        found = False
        for row in rows:
            if str(row.get("cedent_id") or "").strip().lower() != pid:
                continue
            name = str(row.get("class_name") or "").lower().strip().replace(" ", "_")
            if name != store:
                continue
            found = True
            aliases = [str(v) for v in (row.get("aliases") or [])]
            keys = {a.strip().upper() for a in aliases}
            if label.upper() not in keys:
                aliases.append(label)
                row["aliases"] = aliases
                changed = True
        if not found:
            raise ValueError(
                f"Partner {pid} has no insurance_class row for {store!r} in the local snapshot."
            )
        if changed:
            _write_json(CLASSES_PATH, rows)
        _audit({
            "at": datetime.now(timezone.utc).isoformat(),
            "action": "keep",
            "partner_id": pid,
            "store_class": store,
            "template_class": template_class,
            "raw_label": label,
            "confirmed_by": confirmed_by,
            "changed": changed,
        })
        conf = _read_json(CONFIRMATIONS_PATH, {"aliases_by_partner": {}, "updated_at": None})
        bucket = conf.setdefault("aliases_by_partner", {}).setdefault(pid, [])
        bucket.append({
            "raw_label": label,
            "store_class": store,
            "template_class": template_class,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        conf["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(CONFIRMATIONS_PATH, conf)
        return changed


def _audit(event: Dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
