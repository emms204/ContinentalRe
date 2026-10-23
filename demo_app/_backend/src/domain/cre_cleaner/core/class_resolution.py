"""Batch-local class evidence and content-bound record identities.

Only explicit source classifications seed the index. Inferences never seed
other inferences, so file order and workbook packaging cannot change a result.
No class store writes, fuzzy identifiers, or partner-specific code guesses.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from src.domain.cre_cleaner.core.class_labels import (
    approved_class_labels, claims_class_hint, is_unresolved_class,
    premium_class_hint, row_class_label,
)
from src.domain.cre_cleaner.core.normalize import clean_text
from src.domain.cre_cleaner.models import ExceptionRecord

IDENTITY_VERSION = "class-record-v1"


@lru_cache(maxsize=256)
def _digest(path, size, mtime_ns):
    with open(path, "rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def source_digest(path):
    path = Path(path)
    stat = path.stat()
    return _digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


def records(result):
    for attr, kind, getter in (
        ("premium_rows", "premium", premium_class_hint),
        ("claims_rows", "paid", claims_class_hint),
        ("outstanding_rows", "outstanding", claims_class_hint),
    ):
        for row in getattr(result, attr):
            yield row, kind, getter


def identify_rows(result, context):
    for row, kind, _ in records(result):
        a = row.audit
        identity = [IDENTITY_VERSION, context, a.source_digest,
                    a.source_filename, a.source_sheet, a.source_row, kind,
                    row.policy_no, getattr(row, "claim_no", ""),
                    getattr(row, "insured", getattr(row, "name_of_insured", "")),
                    a.currency, getattr(row, "total_claims", getattr(row, "gross_premium", None)),
                    row.period_from, row.period_to]
        a.row_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False,
                                           sort_keys=True, default=str).encode()).hexdigest()
    result.source_row_ids = [row.audit.row_id for row, _, _ in records(result)]


def _key(row):
    policy = clean_text(row.policy_no).upper()
    insured = clean_text(getattr(row, "insured", getattr(row, "name_of_insured", ""))).upper()
    currency = clean_text(row.audit.currency).upper()
    placeholders = {"", "N/A", "NA", "NIL", "NONE", "NULL", "UNKNOWN", "NAN", "-", "0", "0.0"}
    return (policy, insured, currency) if policy not in placeholders and insured not in placeholders and currency else None


def _compatible(left, right):
    lf, lt = left.period_from, left.period_to
    rf, rt = right.period_from, right.period_to
    if (lf and lt and lf > lt) or (rf and rt and rf > rt):
        return False
    return not ((lt and rf and lt < rf) or (rt and lf and rt < lf))


def _reference(row, kind, label):
    a = row.audit
    return dict(row_id=a.row_id, file=a.source_filename, sheet=a.source_sheet,
                source_row=a.source_row, bordereau_type=kind, class_name=label)


def resolve_from_policy_evidence(result, class_map=None, additional_records=()):
    allowed = approved_class_labels(class_map)
    index = {}
    snapshot = list(records(result))
    for row, kind, getter in snapshot + list(additional_records):
        if row.audit.class_source == "conflict":
            continue
        # Never let a previous inferred or user-assigned value become source evidence.
        raw = (getter(row) or "").strip()
        if not raw or is_unresolved_class(raw):
            continue
        label = row_class_label(row, raw, class_map)
        key = _key(row)
        if key and label in allowed and not row.audit.run_mapped_class and not row.audit.inferred_class:
            index.setdefault(key, []).append((row, kind, label))
    for row, kind, getter in snapshot:
        if row.audit.class_source == "conflict":
            continue  # Preserve both source values; policy matches cannot settle a contradiction.
        raw = (getter(row) or "").strip()
        candidates = [(other, other_kind, label)
                      for other, other_kind, label in index.get(_key(row), [])
                      if other is not row and _compatible(row, other)]
        if not candidates:
            continue
        labels = {label for _, _, label in candidates}
        explicit = row_class_label(row, raw, class_map)
        missing = not raw or is_unresolved_class(raw)
        conflict = (len(labels) != 1
                    or (not missing and explicit in allowed and labels != {explicit})
                    or (bool(raw) and is_unresolved_class(raw) and not labels <= {"Marine Hull", "Marine Cargo"}))
        if not missing and not conflict:
            continue
        evidence = sorted((_reference(r, k, c) for r, k, c in candidates),
                          key=lambda item: (item["row_id"], item["class_name"]))
        reason = "conflicting_policy_classes" if conflict else "unique_policy_insured_match"
        row.audit.class_resolution = dict(rule=reason, candidates=sorted(labels),
                                          evidence=evidence[:20], evidence_count=len(evidence))
        # Bare MARINE may only be refined to a marine class.
        if not conflict and missing and (not raw or labels <= {"Marine Hull", "Marine Cargo"}):
            label = next(iter(labels))
            row.audit.inferred_class = label
            result.exceptions.append(ExceptionRecord(
                "INFO", "class_inferred_from_policy", row.audit.source_filename,
                row.audit.source_sheet, row.audit.source_row,
                f"{label} from matching policy, insured, currency and compatible period; "
                f"evidence={json.dumps(evidence[:20], ensure_ascii=False)}; matches={len(evidence)}",
            ))
        elif conflict:
            result.exceptions.append(ExceptionRecord(
                "WARN", "class_evidence_conflict", row.audit.source_filename,
                row.audit.source_sheet, row.audit.source_row,
                f"Conflicting class evidence for policy {row.policy_no!r}: {sorted(labels)}; "
                "explicit source classification preserved; missing classes require review",
            ))


def marine_record_suggestion(description):
    """Advisory only: explicit loss subject, never a global MARINE alias."""
    text = clean_text(description).upper()
    cargo = bool(re.search(r"\b(?:CARGO|CONSIGNMENT|PAPER REELS|INSURED.S GOODS|PAPER ON TRUCK)\b", text)
                 or 'ITEMS ON MARINE CERT' in text)
    hull = bool(re.search(r"\b(?:VESSEL|BARGE|ENGINE DAMAGE|FLOATING MODULAR DOCK)\b", text))
    if cargo == hull:
        return dict(reason='marine_subject_ambiguous')
    return {'class': 'Marine Cargo' if cargo else 'Marine Hull',
            'rule': 'source_loss_subject', 'reason': 'Review the loss subject against the policy cover; advisory only'}


def affected_rows(entries, snapshot, class_map=None):
    """Attach original records to existing unresolved label/file/sheet groups."""
    allowed = approved_class_labels(class_map)
    buckets = {}
    for row, kind, getter in snapshot:
        raw = (getter(row) or "").strip()
        if row_class_label(row, raw, class_map) in allowed:
            continue
        a = row.audit
        item = dict(row_id=a.row_id, source_row=a.source_row, bordereau_type=kind,
                    policy_no=row.policy_no, claim_no=getattr(row, "claim_no", ""),
                    insured=getattr(row, "insured", getattr(row, "name_of_insured", "")),
                    currency=a.currency, raw_class=raw, source_file_hash=a.source_digest,
                    resolution=a.class_resolution, description=getattr(row, "details", ""))
        if a.class_source == "conflict":
            item['raw_class'] = a.class_label_raw
        if is_unresolved_class(raw):
            item['description'] = getattr(row, 'details', '')
            item['suggestion'] = marine_record_suggestion(item['description'])
        buckets.setdefault((raw or "(blank)", a.source_filename, a.source_sheet), []).append(item)
    for entry in entries:
        entry["rows"] = buckets.get((entry["label"], entry["file"], entry["sheet"]), [])
        entry["requires_row_resolution"] = (entry["label"] == "(blank)"
            or is_unresolved_class(entry["label"])
            or clean_text(entry["label"]).upper() == "PACKAGE POLICY")
        base = getattr(class_map, 'base', None) or class_map
        if getattr(class_map, 'single_class', None):
            entry['requires_row_resolution'] = True
            entry['expected_class'] = getattr(class_map, 'single_class', None)
            entry['allows_record_class_override'] = bool(getattr(base, 'allow_record_class_override', False))
        conflicts = [r for r in entry['rows'] if r['resolution'].get('rule') == 'conflicting_source_classes']
        if conflicts:
            entry['requires_row_resolution'] = True
            entry['suggestion'] = None
            entry['suggestion_reason'] = 'conflicting_source_classes'
            entry['suggestion_candidates'] = sorted({c for r in conflicts for c in r['resolution'].get('candidates', [])})
    return entries
