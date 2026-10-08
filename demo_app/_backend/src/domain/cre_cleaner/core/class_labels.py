"""Map source class / sheet hints onto Bisola-style class labels.

Bisola sheet naming: ``{ClassLabel} - PREMIUM|CLAIMS|OUTSTANDING``.
Canonical labels she uses: Fire, General Accident, Marine Cargo, Marine Hull,
Engineering, Bond. This module holds only the generic class vocabulary; a
cedant's own subclasses, typos and approved mappings are supplied by its
adapter (``BaseAdapter.class_label_map``) and passed in as ``class_map``.

Facultative is **not** a Continental treaty class — ignored by default
(``--include-fac`` to keep).
"""
from __future__ import annotations

import re

from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.domain.cre_cleaner.config import FAC_CLASS_LABEL
from src.domain.cre_cleaner.core.normalize import normalize_header

# Display order for class-split sheets (Bisola-like). Unknown labels append after.
BISOLA_CLASS_ORDER: List[str] = [
    "Fire",
    "General Accident",
    "Marine Cargo",
    "Marine Hull",
    "Engineering",
    "Bond",
    "Motor",
    "Terrorism & PVT",
    "Agriculture",
    "Aviation",
    "Oil & Gas",
    "Travel",
    FAC_CLASS_LABEL,  # opt-in only; default pipeline drops these sheets
]

# Class text too broad to place in one Bisola class: bare MARINE could be Hull
# or Cargo. Rows carrying only this go to the exceptions file, never a sheet.
UNRESOLVED_CLASS_KEYS = {"MARINE"}
_LAYER_NOISE = {"1ST", "2ND", "3RD", "SURP", "SURPLUS", "TREATY", "QUOTA", "SHARE", "QS"}

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
    "GEN ACCIDENT": "General Accident",
    "GEN ACC": "General Accident",
    "GENERAL ACC": "General Accident",
    "GENERAL ACCIDENTS": "General Accident",
    "FACULTATIVE": FAC_CLASS_LABEL,
    "FAC OBLIG": FAC_CLASS_LABEL,
    "FAC": FAC_CLASS_LABEL,
    # Own classes (Continental books these separately from General Accident)
    "MOTOR": "Motor",
    "TERRORISM": "Terrorism & PVT",
    "PVT": "Terrorism & PVT",
    "POLITICAL VIOLENCE": "Terrorism & PVT",
    "AGRIC": "Agriculture",
    "AGRICULTURE": "Agriculture",
    "AGRICULTURAL": "Agriculture",
    "AVIATION": "Aviation",
    "OIL & GAS": "Oil & Gas",
    "OIL AND GAS": "Oil & Gas",
    "OIL/GAS": "Oil & Gas",
    "TRAVEL": "Travel",
}


# Keys matched only as the WHOLE normalized value, never as a substring in the
# fallback of normalize_class_label (short keys that occur inside other words).
# Adapters add their own via ``ClassMap.exact_only``.
_EXACT_ONLY_KEYS: FrozenSet[str] = frozenset()


class ClassMap:
    """Generic class map plus one adapter's extra keys (never another's)."""

    def __init__(self, extra: Optional[Mapping[str, str]] = None,
                 exact_only: Iterable[str] = ()) -> None:
        self.map: Dict[str, str] = dict(_CLASS_MAP)
        self.map.update(extra or {})
        self.exact_only: FrozenSet[str] = frozenset(_EXACT_ONLY_KEYS) | frozenset(exact_only)
        self._by_len = sorted(self.map.items(), key=lambda kv: -len(kv[0]))


GENERIC_CLASS_MAP = ClassMap()


def _norm_key(raw: str) -> str:
    s = (raw or "").strip().upper()
    s = s.replace(".", " ").replace("_", " ").replace("-", " ")
    while "  " in s:
        s = s.replace("  ", " ")
    return s.strip()


def normalize_class_label(raw: Optional[str], class_map: Optional[ClassMap] = None) -> str:
    """Map a source CLASS / sheet hint to a Bisola class label.

    ``class_map`` is the adapter's map (generic + that cedant's extras);
    default: generic only.     Unknown non-empty values return ``\"\"`` so they cannot become a new
    sheet by title-casing. Empty input → ``\"Other\"``.

    A single-class run sets ``store_before_builtin`` on the map. Store entries
    kept for that class are consulted before the built-in map. Every other
    partner leaves the flag unset, so this function is unchanged for them.
    """
    cmap = class_map or GENERIC_CLASS_MAP
    if getattr(cmap, "store_before_builtin", False):
        early = getattr(cmap, "confirmed_label", None)
        if early is not None:
            hit = early(raw)
            if hit:
                return hit
    if raw is None:
        return "Other"
    key = _norm_key(str(raw))
    if not key:
        return "Other"
    if key in cmap.map:
        return cmap.map[key]
    # Word-start fallback (longest keys first): a key matches only where a
    # word of the label starts ("FIRE 1ST", "MCARGO 2ND SURP", "BONDS",
    # "GEN ACCD" for GEN ACC, "MOTORCYCLE"); digits may precede it. Never in
    # the middle of a word: MANUFACTURERS is not FAC, PASSENGER is not ENG,
    # DIGITAL is not GIT (IMPL-20261006-03 B4) — such labels stay unresolved
    # (422 with a suggestion), never auto-mapped.
    # Exact-only tokens never match inside a longer label.
    for token, label in cmap._by_len:
        if token in cmap.exact_only:
            continue
        if token and _contains_word(key, token):
            return label
    # IMPL-20261006-05: confirmed aliases (``class_aliases.AliasedClassMap``)
    # are consulted only here, after the built-in map found nothing, so the
    # built-in map always wins. A plain ClassMap has none: unchanged.
    confirmed = getattr(cmap, "confirmed_label", None)
    if confirmed is not None:
        return confirmed(raw)
    return ""


def _contains_word(key: str, token: str) -> bool:
    """``token`` occurs in ``key`` starting at a word start."""
    pattern = _WORD_PATTERNS.get(token)
    if pattern is None:
        pattern = re.compile(r"(?<![A-Z])" + re.escape(token))
        _WORD_PATTERNS[token] = pattern
    return pattern.search(key) is not None


_WORD_PATTERNS: Dict[str, "re.Pattern[str]"] = {}


def approved_class_labels(class_map: Optional[ClassMap] = None) -> set:
    """Canonical sheet classes. Adapter map values are included; ``Other`` is not."""
    labels = set(BISOLA_CLASS_ORDER)
    if class_map is not None:
        labels.update(class_map.map.values())
    labels.discard("Other")
    return labels


def unapproved_class_rows(rows, class_getter, class_map: Optional[ClassMap] = None):
    """Rows whose class does not resolve to an approved label.

    Each item is ``(original_label, filename, sheet, count)``.
    A single-class map places empty / unresolved labels on that class, so
    those rows are approved. A different approved class stays unapproved.
    """
    allowed = approved_class_labels(class_map)
    single = getattr(class_map, "single_class", None)
    buckets: Dict[Tuple[str, str, str], int] = {}
    for row in rows:
        raw = (class_getter(row) or "").strip()
        text = text_for_single_class(row, raw) if single else raw
        label = (
            resolve_class_label(text, class_map, single)
            if single else normalize_class_label(raw, class_map)
        )
        if label in allowed:
            continue
        audit = getattr(row, "audit", None)
        key = (
            raw or "(blank)",
            getattr(audit, "source_filename", "") if audit else "",
            getattr(audit, "source_sheet", "") if audit else "",
        )
        buckets[key] = buckets.get(key, 0) + 1
    return [(label, filename, sheet, count) for (label, filename, sheet), count in buckets.items()]


def format_unapproved_classes(items) -> str:
    parts = []
    for label, filename, sheet, count in items:
        where = filename or "workbook"
        if sheet:
            where = f"{where} / {sheet}"
        parts.append(f"{label!r} on {where} ({count} record(s))")
    return (
        "Unresolved class — no upload-ready workbook was published. "
        "Map these labels to an approved class or correct the source: "
        + "; ".join(parts)
    )


def _map_base(class_map: Optional[ClassMap]) -> Optional[ClassMap]:
    """The built-in map under an alias wrapper, or the map itself."""
    return getattr(class_map, "base", None) or class_map


def resolve_class_label(raw: Optional[str], class_map: Optional[ClassMap] = None,
                        single_class: Optional[str] = None) -> str:
    """Sheet class for one hint.

    Without ``single_class`` this is ``normalize_class_label``. With it:

    * Facultative stays Facultative (still dropped later).
    * A store entry for the single class wins over the built-in map.
    * An empty hint, ``Other``, a bare-unresolved hint, or a label nothing
      resolves becomes the single class.
    * A different approved class returns ``\"\"`` so the run can answer
      ``class_outside_single_class`` instead of opening that class's sheet.
    """
    single = single_class or getattr(class_map, "single_class", None)
    if not single:
        return normalize_class_label(raw, class_map)
    base = _map_base(class_map)
    if is_fac_class(raw, base):
        return FAC_CLASS_LABEL
    early = getattr(class_map, "confirmed_label", None)
    store = early(raw) if early is not None else ""
    if store == single:
        return single
    text = "" if raw is None else str(raw).strip()
    if not text or is_unresolved_class(text):
        return single
    builtin = normalize_class_label(text, base)
    if builtin in ("", "Other", single):
        return single
    if builtin in approved_class_labels(base):
        return ""
    return single


def is_unresolved_class(raw: Optional[str]) -> bool:
    """True when no class was found, or the only class text is too broad
    (bare MARINE, incl. 'MARINE 2ND SURP') to pick a Bisola class."""
    key = _norm_key(str(raw or ""))
    if not key:
        return True
    toks = [t for t in key.replace("/", " ").split() if t not in _LAYER_NOISE]
    return " ".join(toks) in UNRESOLVED_CLASS_KEYS


# Filler words allowed around a class keyword in a section banner row
# ("FIRE PAID CLAIM", "ENGINEERING 2ND SURPLUS OUTSTANDING CLAIMS AS AT MARCH 2020").
_BANNER_MAX_LEN = 90


def banner_class_label(text: Optional[str]) -> str:
    """Class label from a lone section-banner cell, or ``""`` if none.

    Used only when the tab itself carries no class (combined ``2nd surplus`` /
    ``2ND SURPLUS TREATY`` tabs). Keyword based so typos and decorated banners
    work: ENGIN*/ENG (incl. ENGINERRING, ENGINEERGING) → Engineering; FIRE →
    Fire; MARINE HULL/HULL → Marine Hull; MARINE CARGO/CARGO → Marine Cargo;
    BOND → Bond; GEN ACC*/GENERAL ACCIDENT/MISC → General Accident; MOTOR,
    TERRORISM/PVT, AGRIC*, AVIATION, OIL & GAS, TRAVEL → their own classes.
    Bare MARINE (e.g. "MARINE PAID CLAIM") → ``"MARINE"``: the banner still
    starts a new section, but the rows are routed to exceptions as unresolved
    (see ``is_unresolved_class``). Ambiguous (two classes) or keyword-free
    banners (e.g. "2ND SURP PAID CLAIM") → ``""``.
    """
    if text is None:
        return ""
    raw = str(text).strip()
    if not raw or len(raw) > _BANNER_MAX_LEN:
        return ""
    key = _norm_key(raw)
    # Numbers / dates are never banners
    if not any(ch.isalpha() for ch in key):
        return ""
    toks = key.replace("/", " ").replace("&", " ").split()
    found = set()
    joined = " " + " ".join(toks) + " "
    if " MARINE HULL " in joined or "HULL" in toks or "MHULL" in toks:
        found.add("Marine Hull")
    if " MARINE CARGO " in joined or "CARGO" in toks or "MCARGO" in toks:
        found.add("Marine Cargo")
    if "MARINE" in toks and not ({"Marine Hull", "Marine Cargo"} & found):
        found.add("MARINE")
    if "MOTOR" in toks:
        found.add("Motor")
    if "TERRORISM" in toks or "PVT" in toks or " POLITICAL VIOLENCE " in joined:
        found.add("Terrorism & PVT")
    if any(t.startswith("AGRIC") for t in toks):
        found.add("Agriculture")
    if "AVIATION" in toks:
        found.add("Aviation")
    if " OIL GAS " in joined or " OIL AND GAS " in joined:
        found.add("Oil & Gas")
    if "TRAVEL" in toks:
        found.add("Travel")
    if any(t.startswith("ENGIN") for t in toks) or "ENG" in toks:
        found.add("Engineering")
    if "FIRE" in toks:
        found.add("Fire")
    if "BOND" in toks or "BONDS" in toks:
        found.add("Bond")
    if (
        " GENERAL ACCIDENT " in joined
        or " GEN ACC" in joined
        or " MISC " in joined
        or " MISCELLANEOUS " in joined
        or "GA" == key
    ):
        found.add("General Accident")
    if len(found) == 1:
        return next(iter(found))
    return ""


def is_fac_class(raw: Optional[str], class_map: Optional[ClassMap] = None) -> bool:
    return normalize_class_label(raw, class_map) == FAC_CLASS_LABEL


def is_fac_sheet_name(sheet_name: str) -> bool:
    """True for FAC OBLIG / Facultative source sheets Continental said to ignore."""
    n = normalize_header(sheet_name)
    if "FACULTATIVE" in n:
        return True
    if "FAC" in n and "OBLIG" in n:
        return True
    # Bare FAC sheets that aren't "FAC AMOUNT" style column noise in other names
    tokens = n.split()
    if tokens and tokens[0] == "FAC":
        return True
    return False


_SHEET_TITLE_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


def class_sheet_title(class_label: str, bordereau_type: str) -> str:
    """e.g. ``Fire - PREMIUM``. Characters Excel forbids in a sheet title
    (``[ ] : * ? / \\``) are replaced by ``-`` so an unmapped source class such
    as ``PUBLIC/PRODUCT LIABILITY`` cannot crash the writer."""
    return f"{_SHEET_TITLE_FORBIDDEN.sub('-', class_label)} - {bordereau_type}"


def ordered_class_labels(labels: Iterable[str]) -> List[str]:
    seen = []
    for lab in BISOLA_CLASS_ORDER:
        if lab in labels and lab not in seen:
            seen.append(lab)
    for lab in sorted(set(labels)):
        if lab not in seen:
            seen.append(lab)
    return seen


def text_for_single_class(row, hint: str) -> str:
    """Label the single-class resolver should see.

    A section banner collapses ``PLANTATION FIRE`` to ``Fire`` and stores
    that on ``class_hint``. The banner text stays on ``class_label_raw``.
    The store alias is on that raw text, so a raw label that differs from
    the collapsed hint is what gets resolved.
    """
    audit = getattr(row, "audit", None)
    raw = (getattr(audit, "class_label_raw", "") or "").strip()
    hint = (hint or "").strip()
    if raw and raw.casefold() != hint.casefold():
        return raw
    return hint


def group_rows_by_class(rows: Sequence, class_getter,
                        class_map: Optional[ClassMap] = None) -> Dict[str, list]:
    """Group row objects by normalized Bisola class label.

    A single-class map places empty and unresolved hints on that class.
    A hint that resolves to a different approved class is left out of every
    sheet (the pipeline blocks the run before this write).
    """
    out: Dict[str, list] = {}
    single = getattr(class_map, "single_class", None)
    for r in rows:
        raw = class_getter(r)
        text = text_for_single_class(r, raw) if single else raw
        lab = resolve_class_label(text, class_map, single) if single else normalize_class_label(raw, class_map)
        if not lab:
            continue
        out.setdefault(lab, []).append(r)
    return out


def premium_class_hint(row) -> str:
    return getattr(getattr(row, "audit", None), "class_hint", "") or ""


def claims_class_hint(row) -> str:
    name = getattr(row, "class_name", "") or ""
    sheet = getattr(getattr(row, "audit", None), "class_hint", "") or ""
    # Prefer a resolved sheet/tab class over a bare MARINE row/banner value.
    if name and not is_unresolved_class(name):
        return name
    if sheet and not is_unresolved_class(sheet):
        return sheet
    return name or sheet


def mapping_documentation(class_map: Optional[ClassMap] = None) -> List[Tuple[str, str]]:
    """Stable (source, Bisola label) pairs for README / SUMMARY."""
    items = sorted((class_map or GENERIC_CLASS_MAP).map.items(), key=lambda kv: (kv[1], kv[0]))
    return items
