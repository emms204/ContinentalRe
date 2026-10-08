"""Partner class vocabulary from the EXISTING class store (IMPL-20261006-05).

The store is BigQuery ``insurance_class`` (one row per partner + reinsurer +
class), written by POST /load-partner-class and by POST /class-aliases. A
partner (``cedents.cedent_id`` = ``partnerId``) is one cedant + broker pair
(``CEDANT(BROKER)`` in ``cedents.name``), so the vocabulary is per cedant + broker.
This module is pure: it never reads a database. The service reads the
selected partner's rows ONCE per run and hands them to ``build_partner_aliases``.

Lookup order inside ``normalize_class_label`` (``class_labels``):

1. the built-in adapter / exact map and its word-start fallback (unchanged,
   always first: a store entry can never override it; a store entry that
   disagrees with it is dropped with WARN ``store_conflicts_builtin``);
2. the partner's ``aliases`` (exact match on the normalised label);
3. the partner's ``variations`` (exact match on the normalised label);
4. nothing: the label stays unresolved, the IMPL-01 suggester runs and the
   clean answers 422. A suggestion is never applied.

``keywords`` are NEVER used for mapping: they are broad terms ("marine",
"vessel", "general") shared by several classes.

Store class names (``marine_hull``) become template output classes
(``Marine Hull``) only through the explicit per-cedant map in
``adapters/store_classes.py`` (cedant data stays out of this shared
module). A store class with no entry there is skipped with WARN ``store_class_unmapped``; it is never guessed.

Labels are compared with the class-suggestion normalisation
(``normalize_for_suggestion``), so layer and context words fold: an alias
``marine`` also resolves ``MARINE 2ND SURPLUS``. Facultative is never a target.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from src.domain.cre_cleaner.config import FAC_CLASS_LABEL
from src.domain.cre_cleaner.core.class_labels import (
    GENERIC_CLASS_MAP,
    UNRESOLVED_CLASS_KEYS,
    ClassMap,
    approved_class_labels,
    normalize_class_label,
)
from src.domain.cre_cleaner.core.class_suggest import normalize_for_suggestion

# Exception / warning codes written by the pipeline and the service.
ALIAS_STORE_UNAVAILABLE = "alias_store_unavailable"
CLASS_ALIAS_APPLIED = "class_alias_applied"
CLASS_ALIASES_LOADED = "class_aliases_loaded"
STORE_CLASS_UNMAPPED = "store_class_unmapped"
STORE_CLASS_NOT_APPROVED = "store_class_not_approved"
STORE_CONFLICTS_BUILTIN = "store_conflicts_builtin"
STORE_OVERRIDES_BUILTIN = "store_overrides_builtin"
STORE_LABEL_AMBIGUOUS = "store_label_ambiguous"
STORE_VARIATION_TOO_BROAD = "store_variation_too_broad"

TIER_ALIAS = "alias"
TIER_VARIATION = "variation"

def store_class_key(name: Any) -> str:
    """``insurance_class.class_name`` as /load-partner-class writes it."""
    return str(name or "").lower().strip().replace(" ", "_")


def template_class_for(store_map: Optional[Mapping[str, str]], store_class: Any) -> Optional[str]:
    """Template output class for a store class under one cedant's explicit
    ``store_map`` (None = unmapped)."""
    return dict(store_map or {}).get(store_class_key(store_class))


def alias_key(raw: Any) -> str:
    """The normalised label store entries and source labels are compared on."""
    return normalize_for_suggestion(raw)


def _field(row: Any, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def _entries(row: Any, name: str) -> List[str]:
    return [str(v) for v in (_field(row, name) or []) if str(v or "").strip()]


@dataclass(frozen=True)
class ConfirmedAliases:
    """The partner vocabulary that applies to ONE run, resolved for precedence:
    ``mapping`` is normalised label -> template class (aliases beat variations;
    entries the built-in map contradicts, ambiguous entries and unmapped store
    classes are already left out).

    ``available`` False means the store could not be read: the run goes on
    with the built-in map only and the pipeline writes WARN
    ``alias_store_unavailable``. Nothing is ever mapped silently."""

    mapping: Mapping[str, str] = field(default_factory=dict)
    available: bool = True
    detail: str = ""
    # normalised label -> (tier "alias"/"variation", store class name)
    provenance: Mapping[str, Tuple[str, str]] = field(default_factory=dict)
    warnings: Tuple[Mapping[str, Any], ...] = ()
    # Store entries a single-class partner kept even though the built-in map
    # names a different class. INFO, not warnings: the built-in map does not win.
    overrides: Tuple[Mapping[str, Any], ...] = ()
    # True when the store was read and the partner has no live class rows.
    empty_store: bool = False
    partner_id: Optional[str] = None

    @classmethod
    def unavailable(cls, detail: str, partner_id: Optional[str] = None) -> "ConfirmedAliases":
        return cls(mapping={}, available=False, detail=detail, partner_id=partner_id)

    def __len__(self) -> int:
        return len(self.mapping)

    def counts(self) -> Dict[str, int]:
        out = {TIER_ALIAS: 0, TIER_VARIATION: 0}
        for tier, _store in self.provenance.values():
            out[tier] = out.get(tier, 0) + 1
        return out


def _builtin(label: str, class_map: Optional[ClassMap]) -> str:
    got = normalize_class_label(label, class_map)
    return "" if got in ("", "Other") else got


def build_partner_aliases(rows: Iterable[Any], store_map: Optional[Mapping[str, str]],
                          class_map: Optional[ClassMap] = None,
                          partner_id: Optional[str] = None,
                          single_class: Optional[str] = None) -> ConfirmedAliases:
    """Resolve one partner's ``insurance_class`` rows into a run vocabulary.

    ``rows``: mappings/objects with ``class_name``, ``aliases``, ``variations``
    (``keywords`` may be present and is ignored). ``store_map``: the cedant's
    explicit store-class -> template-class map (``adapters.store_classes``).
    ``class_map``: the run's adapter map (built-in), used to detect conflicts
    and approved classes."""
    cmap = class_map or GENERIC_CLASS_MAP
    approved = approved_class_labels(cmap) - {FAC_CLASS_LABEL, "Other"}
    warnings: List[Dict[str, Any]] = []
    overrides: List[Dict[str, Any]] = []
    warned: Set[Tuple[str, str]] = set()

    def warn(code: str, message: str, **extra: Any) -> None:
        sig = (code, str(sorted((k, v) for k, v in extra.items() if k != "tier")))
        if sig in warned:
            return
        warned.add(sig)
        warnings.append({"code": code, "message": message, **extra})

    # tier -> key -> {template: {store classes}}, and key -> one raw entry
    found: Dict[str, Dict[str, Dict[str, Set[str]]]] = {TIER_ALIAS: {}, TIER_VARIATION: {}}
    raw_of: Dict[Tuple[str, str], str] = {}
    for row in rows or ():
        store = store_class_key(_field(row, "class_name"))
        if not store:
            continue
        template = template_class_for(store_map, store)
        if template is None:
            warn(STORE_CLASS_UNMAPPED,
                 f"Store class {store!r} has no template class mapping for this cedant; "
                 "its aliases and variations are ignored (never guessed).",
                 store_class=store)
            continue
        if template == FAC_CLASS_LABEL:
            continue
        if template not in approved:
            warn(STORE_CLASS_NOT_APPROVED,
                 f"Store class {store!r} maps to {template!r}, which is not an approved "
                 "class for this partner; ignored.", store_class=store, class_name=template)
            continue
        for tier, column in ((TIER_ALIAS, "aliases"), (TIER_VARIATION, "variations")):
            for entry in _entries(row, column):
                key = alias_key(entry)
                if not key:
                    continue
                if tier == TIER_VARIATION and key in UNRESOLVED_CLASS_KEYS:
                    warn(STORE_VARIATION_TOO_BROAD,
                         f"Variation {entry!r} of {store!r} is the bare label {key!r}, "
                         "which only a confirmed alias may place; ignored.",
                         store_class=store, label=entry)
                    continue
                found[tier].setdefault(key, {}).setdefault(template, set()).add(store)
                raw_of.setdefault((tier, key), entry)

    mapping: Dict[str, str] = {}
    provenance: Dict[str, Tuple[str, str]] = {}
    for tier in (TIER_VARIATION, TIER_ALIAS):          # aliases last: they win
        for key, targets in sorted(found[tier].items()):
            if len(targets) > 1:
                warn(STORE_LABEL_AMBIGUOUS,
                     f"{key!r} is a store {tier} of several classes "
                     f"({', '.join(sorted(targets))}); not used.",
                     label=key, tier=tier)
                if tier == TIER_ALIAS:
                    mapping.pop(key, None)
                    provenance.pop(key, None)
                continue
            template, stores = next(iter(targets.items()))
            entry = raw_of[(tier, key)]
            builtin = _builtin(entry, cmap) or _builtin(key, cmap)
            if builtin and builtin != template:
                if single_class and template == single_class:
                    overrides.append({
                        "code": STORE_OVERRIDES_BUILTIN,
                        "message": (
                            f"Store {tier} {entry!r} of {sorted(stores)[0]!r} places the label "
                            f"in {template!r}; the built-in map says {builtin!r}. "
                            "The partner is single-class, so the store entry is used."
                        ),
                        "label": entry, "store_class": sorted(stores)[0], "tier": tier,
                        "class_name": template, "builtin_class": builtin,
                    })
                else:
                    warn(STORE_CONFLICTS_BUILTIN,
                         f"Store {tier} {entry!r} of {sorted(stores)[0]!r} says {template!r} but the "
                         f"built-in map says {builtin!r}; the built-in map wins.",
                         label=entry, store_class=sorted(stores)[0], tier=tier,
                         class_name=template, builtin_class=builtin)
                    if tier == TIER_ALIAS:
                        mapping.pop(key, None)
                        provenance.pop(key, None)
                    continue
            mapping[key] = template
            provenance[key] = (tier, sorted(stores)[0])
    return ConfirmedAliases(mapping=mapping, available=True, provenance=provenance,
                            warnings=tuple(warnings), overrides=tuple(overrides),
                            partner_id=partner_id)


class AliasedClassMap(ClassMap):
    """The run's class map plus the partner vocabulary.

    The built-in data (``map``, ``exact_only``) is the base map's, unchanged:
    suggestions and the approved-class set see exactly what they saw before.
    ``normalize_class_label`` consults ``confirmed_label`` only after the
    built-in map (exact and word-start) found nothing."""

    def __init__(self, base: Optional[ClassMap], confirmed: Mapping[str, str]) -> None:
        # Deliberately no ClassMap.__init__: share the base's built-in data.
        self.base: ClassMap = base or GENERIC_CLASS_MAP
        self.map = self.base.map
        self.exact_only = self.base.exact_only
        self._by_len = self.base._by_len
        self.confirmed: Dict[str, str] = {
            k: v for k, v in dict(confirmed or {}).items() if k and v and v != FAC_CLASS_LABEL
        }
        self._cache: Dict[str, str] = {}

    def confirmed_label(self, raw: Any) -> str:
        """Class the partner vocabulary gives ``raw`` (via its normalised label), or ``""``."""
        text = "" if raw is None else str(raw)
        hit = self._cache.get(text)
        if hit is None:
            key = alias_key(text)
            hit = self.confirmed.get(key, "") if key else ""
            self._cache[text] = hit
        return hit


def with_confirmed_aliases(class_map: Optional[ClassMap],
                           aliases: Optional[ConfirmedAliases]) -> Optional[ClassMap]:
    """``class_map`` with the run's partner vocabulary layered after it (or
    ``class_map`` itself when there is none)."""
    if aliases is None or not aliases.available or not aliases.mapping:
        return class_map
    return AliasedClassMap(class_map, aliases.mapping)


def mark_single_class(class_map: Optional[ClassMap], single_class: Optional[str]) -> Optional[ClassMap]:
    """Carry ``single_class`` on the run's map and consult kept store entries
    before the built-in map. No-op when the adapter has no single class.
    The wrapper is a new object, so a shared adapter map is not mutated."""
    if not single_class:
        return class_map
    if not isinstance(class_map, AliasedClassMap):
        class_map = AliasedClassMap(class_map, {})
    class_map.single_class = single_class
    class_map.store_before_builtin = True
    return class_map


def confirmed_label(raw: Any, class_map: Optional[ClassMap]) -> str:
    """Class the partner vocabulary gives ``raw`` under ``class_map`` (``""`` if none)."""
    fn = getattr(class_map, "confirmed_label", None)
    return fn(raw) if fn is not None else ""


def alias_applications(rows_and_getters, class_map: Optional[ClassMap]) -> List[Tuple[str, str, int]]:
    """``(label, class, rows)`` for labels resolved by the partner vocabulary,
    i.e. the built-in map gave nothing and the store did. For the audit trail."""
    if getattr(class_map, "confirmed_label", None) is None:
        return []
    base = getattr(class_map, "base", None)
    counts: Dict[Tuple[str, str], int] = {}
    for rows, getter in rows_and_getters:
        for row in rows or ():
            raw = (getter(row) or "").strip()
            if not raw or normalize_class_label(raw, base):
                continue
            cls = class_map.confirmed_label(raw)
            if cls:
                counts[(raw, cls)] = counts.get((raw, cls), 0) + 1
    return [(label, cls, n) for (label, cls), n in sorted(counts.items())]
