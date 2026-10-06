"""Deterministic class suggestions for labels the approved class map leaves unresolved.

Advisory only. The approved map (``normalize_class_label``) always runs first
and is never changed here; a suggestion is never applied, so an unresolved
label still blocks the clean (HTTP 422). The candidate set is the approved
class names plus every alias in the class map in use, which includes the
aliases an adapter adds.

Rules, in order (the first that decides wins):

1. Normalise (suggestions only): upper case, drop punctuation, collapse spaces,
   fold light plurals/suffixes (CLAIMS = CLAIM, AGRICULTURAL = AGRICULTURE),
   then strip trailing context words (PAID/OS/OUTSTANDING CLAIMS, CLAIMS,
   PREMIUM, 1ST/2ND/3RD SURPLUS, SURPLUS, TREATY, a lone 1ST/2ND/3RD).
2. Marine guard: bare MARINE (or a one-letter typo of it) never gets Hull or
   Cargo: ``marine_hull_vs_cargo_ambiguous``.
3. ``normalized_exact``: the normalised label equals a normalised alias.
4. ``prefix``: a label of 3+ characters that starts a word of aliases or class
   names of exactly ONE class (classes are counted, not aliases). Two or more
   classes: no suggestion, ``ambiguous_prefix``.
   Treaty-layer words (1ST/2ND/3RD, SURP, SURPLUS, TREATY, QS) are dropped
   wherever they stand ("2nd surp cago" -> CAGO).
5. ``typo``: edit distance (adjacent swap = 1) <= 1 for 4-7 characters, <= 2 for 8+, against full
   aliases and class names of 4+ characters (adapter exact-only tokens are
   excluded: they are too short to match safely). Only a unique best class is
   suggested; a tie is ``ambiguous_typo``. Labels under 4 characters never get
   a typo match.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.domain.cre_cleaner.core.class_labels import (
    GENERIC_CLASS_MAP,
    ClassMap,
    approved_class_labels,
)

RULE_EXACT = "normalized_exact"
RULE_PREFIX = "prefix"
RULE_TYPO = "typo"

REASON_MARINE = "marine_hull_vs_cargo_ambiguous"
REASON_AMBIGUOUS_EXACT = "ambiguous_normalized_match"
REASON_AMBIGUOUS_PREFIX = "ambiguous_prefix"
REASON_AMBIGUOUS_TYPO = "ambiguous_typo"
REASON_NO_CLASS_TEXT = "no_class_text"
REASON_TOO_SHORT = "too_short"
REASON_NO_MATCH = "no_match"

_MIN_PREFIX = 3
_MIN_TYPO = 4
_MARINE = "MARINE"

# Folded form (plural already removed), longest first.
_CONTEXT_SUFFIXES: Tuple[Tuple[str, ...], ...] = (
    ("OUTSTANDING", "CLAIM"),
    ("PAID", "CLAIM"),
    ("OS", "CLAIM"),
    ("1ST", "SURPLUS"),
    ("2ND", "SURPLUS"),
    ("3RD", "SURPLUS"),
    ("CLAIM",),
    ("PREMIUM",),
    ("SURPLUS",),
    ("TREATY",),
    # A lone layer ordinal, as in tab names like ``FIRE 1ST`` (same layer
    # noise class_labels ignores for bare MARINE).
    ("1ST",),
    ("2ND",),
    ("3RD",),
)


def _fold_word(word: str) -> str:
    """Light plural / suffix fold (no stemmer dependency)."""
    if len(word) > 5 and word.endswith("TURAL"):        # AGRICULTURAL -> AGRICULTURE
        return word[:-2] + "E"
    if len(word) > 3 and word.endswith("S") and not word.endswith(("SS", "US", "IS")):
        return word[:-1]                                  # CLAIMS -> CLAIM
    return word


def _words(raw: Any) -> List[str]:
    text = str(raw or "").upper().replace("'", "").replace("\u2019", "")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return [_fold_word(w) for w in text.split()]


# Treaty-layer words that never carry a class, wherever they stand in a tab
# name ("2nd surp cago", "CARCO 2ND SURP"); the same layer noise class_labels
# ignores for bare MARINE (IMPL-20261006-03 B17). Folded forms.
_LAYER_WORDS = frozenset({"1ST", "2ND", "3RD", "SURP", "SURPLUS", "TREATY", "QS"})


def normalize_for_suggestion(raw: Any) -> str:
    """Normalised label used only for suggestions (never for mapping)."""
    words = _words(raw)
    stripped = True
    while words and stripped:
        stripped = False
        for suffix in _CONTEXT_SUFFIXES:
            n = len(suffix)
            if len(words) >= n and tuple(words[-n:]) == suffix:
                words = words[:-n]
                stripped = True
                break
    words = [w for w in words if w not in _LAYER_WORDS]
    return " ".join(words)


def edit_distance(a: str, b: str) -> int:
    """Optimal-string-alignment distance: insert / delete / substitute and a
    swap of two adjacent letters each count 1 (MICS = MISC + 1 swap)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev2: List[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            best = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                best = min(best, prev2[j - 2] + 1)
            cur.append(best)
        prev2, prev = prev, cur
    return prev[-1]


def _max_typo_distance(length: int) -> int:
    if length < _MIN_TYPO:
        return -1
    return 1 if length <= 7 else 2


class _Candidate:
    __slots__ = ("source", "norm", "words", "label", "typo_ok")

    def __init__(self, source: str, label: str, exact_only: bool) -> None:
        self.source = source
        self.label = label
        self.norm = " ".join(_words(source))
        self.words = tuple(self.norm.split())
        self.typo_ok = not exact_only and len(self.norm) >= _MIN_TYPO


def suggestion_candidates(class_map: Optional[ClassMap] = None) -> List[_Candidate]:
    """Approved class names + every alias of the map in use (adapter extras included)."""
    cmap = class_map or GENERIC_CLASS_MAP
    approved = approved_class_labels(class_map)
    out: Dict[Tuple[str, str], _Candidate] = {}
    for key, label in sorted(cmap.map.items()):
        if label in approved:
            out[(key, label)] = _Candidate(key, label, key in cmap.exact_only)
    for label in sorted(approved):
        out.setdefault((label, label), _Candidate(label, label, False))
    return [c for c in out.values() if c.norm]


def _pick(cands: Iterable[_Candidate]) -> _Candidate:
    """Deterministic representative: shortest source, then alphabetical."""
    return min(cands, key=lambda c: (len(c.source), c.source))


def _by_class(hits: Iterable[_Candidate]) -> Dict[str, List[_Candidate]]:
    grouped: Dict[str, List[_Candidate]] = {}
    for c in hits:
        grouped.setdefault(c.label, []).append(c)
    return grouped


def _suggest(label: str, matches: List[_Candidate], rule: str) -> Dict[str, Any]:
    best = _pick(matches)
    return {
        "suggestion": {"class": best.label, "rule": rule, "matched_against": best.source},
        "suggestion_reason": None,
        "suggestion_candidates": [],
    }


def _abstain(reason: str, classes: Sequence[str] = ()) -> Dict[str, Any]:
    return {
        "suggestion": None,
        "suggestion_reason": reason,
        "suggestion_candidates": sorted(classes),
    }


def suggest_class(raw: Any, class_map: Optional[ClassMap] = None) -> Dict[str, Any]:
    """``{"suggestion": {class, rule, matched_against} | None, "suggestion_reason": str | None,
    "suggestion_candidates": [classes tied, when ambiguous]}``. Pure and deterministic."""
    label = normalize_for_suggestion(raw)
    if not label:
        return _abstain(REASON_NO_CLASS_TEXT)
    compact = label.replace(" ", "")
    if label == _MARINE or (
        len(compact) >= _MIN_TYPO and edit_distance(compact, _MARINE) <= 1
    ):
        return _abstain(REASON_MARINE)
    cands = suggestion_candidates(class_map)

    exact = _by_class(c for c in cands if c.norm == label)
    if len(exact) == 1:
        return _suggest(label, next(iter(exact.values())), RULE_EXACT)
    if len(exact) > 1:
        return _abstain(REASON_AMBIGUOUS_EXACT, exact)

    if len(label) < _MIN_PREFIX:
        return _abstain(REASON_TOO_SHORT)
    prefix = _by_class(
        c for c in cands
        if c.norm.startswith(label) or any(w.startswith(label) for w in c.words)
    )
    if len(prefix) == 1:
        return _suggest(label, next(iter(prefix.values())), RULE_PREFIX)
    if len(prefix) > 1:
        return _abstain(REASON_AMBIGUOUS_PREFIX, prefix)

    limit = _max_typo_distance(len(label))
    if limit < 0:
        return _abstain(REASON_TOO_SHORT)
    scored: Dict[str, Tuple[int, List[_Candidate]]] = {}
    for c in cands:
        if not c.typo_ok:
            continue
        d = edit_distance(label, c.norm)
        if d > limit:
            continue
        best = scored.get(c.label)
        if best is None or d < best[0]:
            scored[c.label] = (d, [c])
        elif d == best[0]:
            best[1].append(c)
    if not scored:
        return _abstain(REASON_NO_MATCH)
    top = min(d for d, _ in scored.values())
    winners = {lab: cs for lab, (d, cs) in scored.items() if d == top}
    if len(winners) == 1:
        return _suggest(label, next(iter(winners.values())), RULE_TYPO)
    return _abstain(REASON_AMBIGUOUS_TYPO, winners)


def unresolved_class_entries(items, class_map: Optional[ClassMap] = None, *,
                             cedant: Optional[str] = None,
                             broker: Optional[str] = None) -> List[Dict[str, Any]]:
    """API entries for ``unapproved_class_rows`` items ``(label, file, sheet, count)``.

    Only labels the approved map (and any confirmed alias) left unresolved
    reach here; nothing is applied. With ``cedant`` given (IMPL-20261006-05)
    each entry also carries ``type: "class"``, ``cedant`` and ``broker`` so the
    review screen can confirm an alias for exactly that pair.
    """
    cache: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for label, filename, sheet, count in items:
        raw = "" if label == "(blank)" else label
        if raw not in cache:
            cache[raw] = suggest_class(raw, class_map)
        hint = cache[raw]
        out.append({
            "label": label,
            "file": filename or "",
            "sheet": sheet or "",
            "records": int(count),
            "suggestion": dict(hint["suggestion"]) if hint["suggestion"] else None,
            "suggestion_reason": hint["suggestion_reason"],
            "suggestion_candidates": list(hint["suggestion_candidates"]),
        })
        if cedant is not None:
            out[-1].update({"type": "class", "cedant": cedant, "broker": broker})
        # IMPL-20261006-06: frontend offers keep (POST /class-aliases) or ignore
        # (re-run with ignored_labels). Always both; never pre-chosen.
        out[-1]["actions"] = ["keep", "ignore"]
    return out
