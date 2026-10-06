"""Clean uploads with the vendored workbook cleaner. Snapshot only — no GCP."""
from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

_BACKEND = Path(__file__).resolve().parent / "_backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from src.domain.cre_cleaner.adapters import get_adapter  # noqa: E402
from src.domain.cre_cleaner.adapters.base import UnsupportedCedantError  # noqa: E402
from src.domain.cre_cleaner.adapters.store_classes import store_class_map  # noqa: E402
from src.domain.cre_cleaner.core.batch import plan_batch, run_batch  # noqa: E402
from src.domain.cre_cleaner.core.class_aliases import build_partner_aliases  # noqa: E402
from src.domain.cre_cleaner.core.class_labels import (  # noqa: E402
    approved_class_labels,
    ordered_class_labels,
)
from src.domain.cre_cleaner.core.class_suggest import normalize_for_suggestion  # noqa: E402
from src.domain.cre_cleaner.core.period_infer import folder_year, infer_period  # noqa: E402
from src.domain.cre_cleaner.io.delivery import deliver, upload_files  # noqa: E402
from src.domain.cre_cleaner.paths import TEMPLATE_PATH  # noqa: E402
from src.domain.cre_cleaner.pipeline import (  # noqa: E402
    normalize_bordereau_type,
    run_pipeline,
)

from demo_app import store  # noqa: E402

MARINE_REASON = "marine_hull_vs_cargo_ambiguous"


class NeedsReview(Exception):
    def __init__(self, message: str, unresolved: list, warnings: Optional[list] = None,
                 ignored: Optional[list] = None) -> None:
        super().__init__(message)
        self.unresolved = list(unresolved or [])
        self.warnings = list(warnings or [])
        self.ignored = list(ignored or [])


class CleanError(Exception):
    """A clean that produced nothing the user can download."""


@dataclass
class CleanedFile:
    name: str
    data: bytes


@dataclass
class CleanOutcome:
    files: List[CleanedFile] = field(default_factory=list)
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    ignored: List[Dict[str, Any]] = field(default_factory=list)
    ignored_files: List[Dict[str, Any]] = field(default_factory=list)
    year: Optional[int] = None
    quarter: Optional[int] = None
    saved_paths: List[Path] = field(default_factory=list)


def approved_class_options(partner_id: str) -> List[Dict[str, str]]:
    """Dropdown values: template class + store class, from the snapshot map."""
    partner = store.partner_by_id(partner_id)
    if partner is None:
        return []
    cedant, broker = store.partner_name_to_cedant_broker(partner["name"])
    try:
        cmap = get_adapter(cedant, broker).class_map()
    except UnsupportedCedantError:
        return []
    allowed = approved_class_labels(cmap) - {"Facultative", "Other"}
    smap = store_class_map(cedant)
    out: Dict[str, str] = {}
    for row in store.classes_for(partner_id):
        key = str(row.get("class_name") or "").lower().strip().replace(" ", "_")
        template = smap.get(key)
        if template and template in allowed:
            out.setdefault(template, key)
    return [{"class_name": t, "store_class": out[t]} for t in ordered_class_labels(set(out))]


def keep_alias(partner_id: str, raw_label: str, template_class: str, *, confirmed_by: str) -> None:
    options = approved_class_options(partner_id)
    match = next((o for o in options if o["class_name"].upper() == template_class.strip().upper()), None)
    if match is None:
        names = ", ".join(o["class_name"] for o in options) or "(none)"
        raise ValueError(f"{template_class!r} is not an approved class for this partner. Choices: {names}")
    store.append_alias(
        partner_id, match["store_class"], raw_label,
        confirmed_by=confirmed_by, template_class=match["class_name"],
    )


def clean(
    uploads: Sequence[tuple[str, bytes]],
    *,
    partner_id: str,
    year: int,
    quarter: Optional[int],
    bordereau_type: str,
    delivery: str,
    ignored_labels: Optional[list] = None,
) -> CleanOutcome:
    if not uploads:
        raise CleanError("Upload at least one .xls or .xlsx file.")
    partner = store.partner_by_id(partner_id)
    if partner is None:
        raise CleanError(
            f"Partner {partner_id!r} is not in the local snapshot. "
            "This site does not look up partners in BigQuery."
        )
    try:
        cedant, broker = store.partner_name_to_cedant_broker(partner["name"])
    except ValueError as exc:
        raise CleanError(str(exc)) from exc
    try:
        adapter_map = get_adapter(cedant, broker).class_map()
    except UnsupportedCedantError as exc:
        raise CleanError(str(exc)) from exc

    mode = normalize_bordereau_type(bordereau_type)
    choice = (delivery or "separate").strip().lower()
    if choice not in ("separate", "zip", "merged"):
        raise CleanError("delivery must be separate, zip, or merged")

    aliases = build_partner_aliases(
        store.classes_for(partner_id),
        store_class_map(cedant),
        adapter_map,
        partner_id=partner_id,
    )
    warnings = [_warn_dict(w) for w in aliases.warnings]

    with tempfile.TemporaryDirectory(prefix="bisola_demo_") as tmp:
        root = Path(tmp)
        raw = root / "raw"
        out = root / "out"
        raw.mkdir()
        out.mkdir()
        paths = []
        for name, data in uploads:
            safe = Path(name).name
            if Path(safe).suffix.lower() not in {".xls", ".xlsx", ".xlsm"}:
                raise CleanError(f"{safe}: only .xls and .xlsx files are accepted")
            dest = raw / safe
            dest.write_bytes(data)
            paths.append(dest)

        ignored_files: list = []
        ignored_classes: list = []
        if len(paths) == 1:
            _reject_strong_period(paths[0], year, quarter)
            result = run_pipeline(
                cedant=cedant, broker=broker, year=int(year), quarter=quarter,
                raw_dir=raw, template=TEMPLATE_PATH, out_dir=out,
                convert_pdfs=False, bordereau_type=mode, single_file=True,
                class_aliases=aliases,
                **({"ignored_labels": list(ignored_labels)} if ignored_labels else {}),
            )
            unresolved = _unresolved(result)
            ignored_classes = list((result.summary or {}).get("ignored") or [])
            warnings.extend(_result_warns(result))
            if unresolved:
                raise NeedsReview(
                    "Unresolved class", unresolved, warnings=warnings, ignored=ignored_classes,
                )
            err = _pipeline_error(result)
            if err:
                raise CleanError(err)
            produced = list(getattr(result, "outputs", None) or [])
        else:
            plan = plan_batch(
                paths, cedant=cedant, broker=broker,
                selected_year=int(year), selected_quarter=quarter,
            )
            ignore_statuses = {"year_mismatch", "quarter_mismatch", "period_uninferable"}
            hard = []
            for item in getattr(plan, "pending", None) or []:
                if item.status in ignore_statuses:
                    ignored_files.append({
                        "file": item.name, "reason": item.status, "detail": item.detail,
                    })
                else:
                    hard.append(item)
            if hard:
                detail = "; ".join(f"{item.name}: {item.detail}" for item in hard)
                raise CleanError(f"Could not clean every file. {detail}")
            if not getattr(plan, "groups", None):
                raise CleanError(
                    "No files matched the selected year"
                    + (f"/Q{quarter}" if quarter is not None else "")
                    + ". "
                    + "; ".join(f"{i['file']} ({i['reason']})" for i in ignored_files[:5])
                )
            batch = run_batch(
                plan, template=TEMPLATE_PATH, out_dir=out, work_dir=root / "groups",
                convert_pdfs=False, bordereau_type=mode, class_aliases=aliases,
                **({"ignored_labels": list(ignored_labels)} if ignored_labels else {}),
            )
            unresolved_msgs = [
                err for gr in batch.groups for err in gr.errors if "class_unresolved" in err
            ]
            entries = []
            for gr in batch.groups:
                result = getattr(gr, "result", None)
                entries.extend(_unresolved(result) if result is not None else [])
                ignored_classes.extend(list((getattr(result, "summary", None) or {}).get("ignored") or []))
                warnings.extend(_result_warns(result))
            if unresolved_msgs:
                raise NeedsReview(
                    " ".join(unresolved_msgs), entries, warnings=warnings, ignored=ignored_classes,
                )
            produced = batch.deliverable_outputs()

        if not produced:
            raise CleanError("Clean produced no files.")
        if year is not None and quarter is None and choice == "separate":
            qset = {item.get("quarter") for item in produced if item.get("quarter") is not None}
            if len(qset) > 1:
                choice = "zip"
        if len(paths) <= 1 or choice == "separate":
            handed = upload_files(produced)
        else:
            handed = deliver(
                produced, choice, root / "delivery",
                stem=f"{cedant}_{broker}".replace(" ", "_"),
            )
        files = [CleanedFile(name=p.name, data=p.read_bytes()) for p in handed if p.is_file()]
        if not files:
            raise CleanError("Clean produced no downloadable file.")
        return CleanOutcome(
            files=files,
            warnings=warnings,
            ignored=ignored_classes,
            ignored_files=ignored_files,
            year=int(year),
            quarter=int(quarter) if quarter is not None else None,
            saved_paths=_persist(files),
        )


def _persist(files: List[CleanedFile]) -> List[Path]:
    dest_dir = Path(__file__).resolve().parent / "data" / "demo_output"
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)
    out = []
    for item in files:
        path = dest_dir / item.name
        path.write_bytes(item.data)
        out.append(path)
    return out


def _reject_strong_period(path: Path, year: int, quarter: Optional[int]) -> None:
    inf = infer_period(path, folder_year=folder_year(path))
    strong = (inf.confidence or "").lower() in ("high", "medium")
    if inf.year is not None and int(inf.year) != int(year) and strong:
        raise CleanError(
            f"{path.name}: inferred year {inf.year} (confidence={inf.confidence}) "
            f"≠ selected year {year} — file ignored, not cleaned."
        )
    if (
        quarter is not None
        and inf.year is not None
        and int(inf.year) == int(year)
        and inf.quarter in (1, 2, 3, 4)
        and int(inf.quarter) != int(quarter)
        and strong
    ):
        raise CleanError(
            f"{path.name}: inferred Q{inf.quarter} (confidence={inf.confidence}) "
            f"≠ selected Q{quarter} for {year} — file ignored."
        )


def _unresolved(result: Any) -> list:
    summary = getattr(result, "summary", None) or {}
    if summary.get("error") != "class_unresolved":
        return []
    return [dict(item) for item in summary.get("unresolved") or []]


def _pipeline_error(result: Any) -> Optional[str]:
    summary = getattr(result, "summary", None) or {}
    if summary.get("error") and summary.get("error") != "class_unresolved":
        return str(summary.get("detail") or summary.get("error"))
    for rec in getattr(result, "exceptions", None) or []:
        if getattr(rec, "severity", "") == "ERROR":
            return str(getattr(rec, "detail", "") or getattr(rec, "reason", ""))
    return None


def _result_warns(result: Any) -> List[Dict[str, Any]]:
    out = []
    for rec in getattr(result, "exceptions", None) or []:
        if getattr(rec, "severity", "") != "WARN":
            continue
        out.append({
            "code": getattr(rec, "reason", "") or "WARN",
            "detail": getattr(rec, "detail", "") or "",
            "file": getattr(rec, "source_filename", "") or "",
        })
    return out


def _warn_dict(w: Any) -> Dict[str, Any]:
    if isinstance(w, dict):
        return {"code": w.get("code") or "store", "detail": w.get("message") or "", "file": ""}
    return {"code": "store", "detail": str(w), "file": ""}


def is_bare_marine(entry: Dict[str, Any]) -> bool:
    if entry.get("suggestion_reason") == MARINE_REASON:
        return True
    return normalize_for_suggestion(entry.get("label")) == "MARINE"
