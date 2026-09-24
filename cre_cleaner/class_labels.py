"""Map AIICO ARK source class / sheet hints onto Bisola-style class labels.

Bisola sheet naming: ``{ClassLabel} - PREMIUM|CLAIMS|OUTSTANDING``.
Canonical labels she uses: Fire, General Accident, Marine Cargo, Marine Hull,
Engineering, Bond. Source files often carry finer subclasses (Goods in Transit,
All Risks, Burglary, …) that belong under General Accident.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Display order for class-split sheets (Bisola-like). Unknown labels append after.
BISOLA_CLASS_ORDER: List[str] = [
    "Fire",
    "General Accident",
    "Marine Cargo",
    "Marine Hull",
    "Engineering",
    "Bond",
    "Facultative",  # present in AIICO ARK FAC OBLIG sheets; Bisola gold omits these
]

# Bordereau type suffixes on sheet titles (exact Bisola wording for main types).
TYPE_PREMIUM = "PREMIUM"
TYPE_CLAIMS = "CLAIMS"
TYPE_OUTSTANDING = "OUTSTANDING"

# Exact / normalized (upper) source tokens → Bisola label.
# Subclasses under Misc/GA map to General Accident.
_CLASS_MAP: Dict[str, str] = {
    # Direct class / sheet hints
    "FIRE": "Fire",
    "ENGINEERING": "Engineering",
    "ENG": "Engineering",
    "BOND": "Bond",
    "MARINE HULL": "Marine Hull",
    "MHULL": "Marine Hull",
    "HULL": "Marine Hull",
    "MARINE CARGO": "Marine Cargo",
    "MCARGO": "Marine Cargo",
    "CARGO": "Marine Cargo",
    "MISCELLANEOUS ACCIDENT": "General Accident",
    "MISC ACCIDENT": "General Accident",
    "MISC ACDNT": "General Accident",
    "MISC ACC": "General Accident",
    "MISC": "General Accident",
    "GENERAL ACCIDENT": "General Accident",
    "ACCIDENT": "General Accident",
    "FACULTATIVE": "Facultative",
    "FAC OBLIG": "Facultative",
    "FAC": "Facultative",
    # AIICO ARK claims CLASS column subclasses → General Accident
    "GOODS IN TRANSIT": "General Accident",
    "GIT": "General Accident",
    "ALL RISKS": "General Accident",
    "ALL RISK": "General Accident",
    "BURGLARY": "General Accident",
    "MONEY": "General Accident",
    "FIDELITY GUARANTEE": "General Accident",
    "FIDELITY": "General Accident",
    "PUBLIC LIABILITY": "General Accident",
    "PUBLIC / PRODUCT LIABILITY": "General Accident",
    "PUBLIC/PRODUCT LIABILITY": "General Accident",
    "PRODUCT LIABILITY": "General Accident",
    "PROFESSIONAL INDEMNITY": "General Accident",
    "DIRECTORS AND OFFICERS LIABILITY": "General Accident",
    "D&O": "General Accident",
    "MOTOR": "General Accident",
    "PERSONAL ACCIDENT": "General Accident",
    "WORKMEN COMPENSATION": "General Accident",
    "WORKMEN'S COMPENSATION": "General Accident",
}


def _norm_key(raw: str) -> str:
    s = (raw or "").strip().upper()
    s = s.replace(".", " ").replace("_", " ").replace("-", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def normalize_class_label(raw: Optional[str]) -> str:
    """Map a source CLASS / sheet hint to a Bisola class label.

    Unknown non-empty values are returned in title case so they still get a
    sheet (never silently dropped). Empty → ``\"Other\"``.
    """
    if raw is None:
        return "Other"
    key = _norm_key(str(raw))
    if not key:
        return "Other"
    if key in _CLASS_MAP:
        return _CLASS_MAP[key]
    # Substring / prefix fallbacks (longest keys first)
    for token, label in sorted(_CLASS_MAP.items(), key=lambda kv: -len(kv[0])):
        if token and token in key:
            return label
    # Title-case leftover for a visible sheet name
    return str(raw).strip().title() or "Other"


def class_sheet_title(class_label: str, bordereau_type: str) -> str:
    """e.g. ``Fire - PREMIUM``."""
    return f"{class_label} - {bordereau_type}"


def ordered_class_labels(labels: Iterable[str]) -> List[str]:
    seen = []
    for lab in BISOLA_CLASS_ORDER:
        if lab in labels and lab not in seen:
            seen.append(lab)
    for lab in sorted(set(labels)):
        if lab not in seen:
            seen.append(lab)
    return seen


def group_rows_by_class(rows: Sequence, class_getter) -> Dict[str, list]:
    """Group row objects by normalized Bisola class label."""
    out: Dict[str, list] = {}
    for r in rows:
        lab = normalize_class_label(class_getter(r))
        out.setdefault(lab, []).append(r)
    return out


def premium_class_hint(row) -> str:
    return getattr(getattr(row, "audit", None), "class_hint", "") or ""


def claims_class_hint(row) -> str:
    name = getattr(row, "class_name", "") or ""
    if name:
        return name
    return getattr(getattr(row, "audit", None), "class_hint", "") or ""


def mapping_documentation() -> List[Tuple[str, str]]:
    """Stable (source, Bisola label) pairs for README / SUMMARY."""
    # Deduplicate by keeping first occurrence of each source key in sorted order
    items = sorted(_CLASS_MAP.items(), key=lambda kv: (kv[1], kv[0]))
    return items
