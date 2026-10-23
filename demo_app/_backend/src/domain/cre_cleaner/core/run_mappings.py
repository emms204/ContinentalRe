"""Per-run class mappings. Nothing here reads or writes the class store.

A mapping is matched on the same key as a stored alias and an ignored label:
``alias_key(label) or label.strip().upper()``. That key folds context suffixes
and layer words, so "Marine paid claims" and "marine os claims" are both
MARINE. An unscoped mapping of either one covers every MARINE-key label in
the run. File and sheet scope is how those two labels are split.

Precedence for one row is applied by tagging ``audit.run_mapped_class``
before any class lookup: per-run mapping (scoped beats unscoped), then the
stored alias, then the built-in map, then the single-class default. The
tag is not a ClassMap layer, because a ClassMap lookup cannot see file
or sheet.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Tuple

from src.domain.cre_cleaner.core.class_aliases import alias_key, confirmed_label, store_class_key
from src.domain.cre_cleaner.core.class_labels import (
    claims_class_hint,
    normalize_class_label,
    premium_class_hint,
    run_mapped_class,
)


class RunMappingError(Exception):
    """Coded mapping rejection. Not a ValueError: the routes map those to 400."""

    def __init__(self, errors: Sequence[Mapping[str, Any]]):
        self.errors = [dict(item) for item in errors]
        self.code = str(self.errors[0]["code"]) if self.errors else "mapping_invalid"
        super().__init__(self.code)


@dataclass(frozen=True)
class RunMapping:
    index: int
    alias: str
    alias_key: str
    class_id: str
    name: str = ""
    file: Optional[str] = None
    sheet: Optional[str] = None
    template: str = ""
    store_class: str = ""
    row_ids: Tuple[str, ...] = ()

    @property
    def specificity(self) -> int:
        """file+sheet is 3, file is 2, sheet is 1, unscoped is 0."""
        return 100 if self.row_ids else (2 if self.file else 0) + (1 if self.sheet else 0)


def match_key(label: Any) -> str:
    """Same fold as ignored_labels. ``(blank)`` is the empty-class label."""
    text = "" if label is None else str(label)
    return alias_key(text) or text.strip().upper()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _scope(value: Any) -> Optional[str]:
    # File and sheet names are source identities, including significant spaces.
    text = "" if value is None else str(value)
    return text if text.strip() else None


def _fields(item: Any) -> Tuple[str, str, str, Optional[str], Optional[str]]:
    if isinstance(item, Mapping):
        alias, cid, name = item.get("alias"), item.get("id"), item.get("name")
        file, sheet = item.get("file"), item.get("sheet")
    else:
        alias = getattr(item, "alias", None)
        cid = getattr(item, "id", None)
        name = getattr(item, "name", None)
        file = getattr(item, "file", None)
        sheet = getattr(item, "sheet", None)
    return _text(alias), _text(cid), _text(name), _scope(file), _scope(sheet)


def _ignore_fields(item: Any) -> Tuple[str, Optional[str], Optional[str]]:
    if isinstance(item, Mapping):
        label, file, sheet = item.get("label"), item.get("file"), item.get("sheet")
    else:
        label = getattr(item, "label", None)
        file = getattr(item, "file", None)
        sheet = getattr(item, "sheet", None)
    return _text(label), _scope(file), _scope(sheet)


def _error(code: str, indexes: Sequence[int], aliases: Sequence[str], ids: Sequence[str],
           key: str, file: Optional[str], sheet: Optional[str],
           ignored_index: Optional[int], detail: str) -> dict:
    return {
        "code": code,
        "index": list(indexes),
        "alias": list(aliases),
        "alias_key": key,
        "id": list(ids),
        "file": file,
        "sheet": sheet,
        "ignored_index": ignored_index,
        "detail": detail,
    }


def _scopes_overlap(left_file, left_sheet, right_file, right_sheet) -> bool:
    """An unset file or sheet overlaps any value on that side."""
    if left_file and right_file and left_file != right_file:
        return False
    if left_sheet and right_sheet and left_sheet != right_sheet:
        return False
    return True


def prepare_run_mappings(items: Optional[Iterable[Any]],
                         ignored_labels: Optional[Iterable[Any]] = None,
                         row_mappings: Optional[Iterable[Any]] = None) -> List[RunMapping]:
    """Payload checks that do not need the class store.

    Raises ``RunMappingError`` with every problem found at this stage.
    A blank alias or id is ``mapping_invalid`` and is reported on its own:
    the other checks need a key and an id. The same key and the same id in
    one scope is kept. Two ids in one scope is ``mapping_alias_conflict``.
    """
    raw_items = list(items or [])
    invalid = []
    prepared: List[RunMapping] = []
    for index, item in enumerate(raw_items):
        alias, cid, name, file, sheet = _fields(item)
        if not alias or not cid:
            which = "alias" if not alias else "id"
            if not alias and not cid:
                which = "alias and id"
            invalid.append(_error(
                "mapping_invalid", [index], [alias], [cid], match_key(alias),
                file, sheet, None,
                f"mappings[{index}].{which} is empty",
            ))
            continue
        if match_key(alias) == match_key("(blank)"):
            raise RunMappingError([{"code": "missing_class_requires_row_mapping", "index": [index],
                "detail": "Missing classes may belong to different classes. Use row_mappings with record IDs returned by validation instead of mapping the (blank) label."}])
        prepared.append(RunMapping(
            index=index, alias=alias, alias_key=match_key(alias), class_id=cid,
            name=name, file=file, sheet=sheet,
        ))
    if invalid:
        raise RunMappingError(invalid)

    grouped: dict = {}
    for mapping in prepared:
        grouped.setdefault((mapping.alias_key, mapping.file, mapping.sheet), []).append(mapping)
    conflicts = []
    for (key, file, sheet), group in grouped.items():
        ids = {item.class_id.casefold() for item in group}
        if len(ids) < 2:
            continue
        conflicts.append(_error(
            "mapping_alias_conflict",
            [item.index for item in group],
            [item.alias for item in group],
            [item.class_id for item in group],
            key, file, sheet, None,
            f"{key!r} is mapped to more than one class in the same scope",
        ))

    ignores = list(ignored_labels or [])
    overlaps = []
    for mapping in prepared:
        for ignored_index, ignored in enumerate(ignores):
            label, file, sheet = _ignore_fields(ignored)
            if not label or match_key(label) != mapping.alias_key:
                continue
            if not _scopes_overlap(mapping.file, mapping.sheet, file, sheet):
                continue
            overlaps.append(_error(
                "mapping_and_ignore_conflict",
                [mapping.index], [mapping.alias], [mapping.class_id],
                mapping.alias_key, mapping.file, mapping.sheet, ignored_index,
                (f"mappings[{mapping.index}] and ignored_labels[{ignored_index}] "
                 f"both cover {mapping.alias_key!r}"),
            ))
    problems = conflicts + overlaps
    if problems:
        raise RunMappingError(problems)
    targets = {}
    for offset, item in enumerate(row_mappings or []):
        data = item if isinstance(item, Mapping) else vars(item)
        cid = _text(data.get("id"))
        row_ids = data.get("row_ids")
        index = len(raw_items) + offset
        if not cid or not isinstance(row_ids, (list, tuple)) or not row_ids or any(
            not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in row_ids
        ):
            raise RunMappingError([{"code": "row_mapping_invalid", "index": [index],
                "detail": "row_mappings requires an approved class id and content-bound row_ids from validation"}])
        unique = tuple(dict.fromkeys(row_ids))
        for row_id in unique:
            if row_id in targets and targets[row_id].casefold() != cid.casefold():
                raise RunMappingError([{"code": "row_mapping_conflict", "row_ids": [row_id],
                    "detail": "The same record is assigned to different classes"}])
            targets[row_id] = cid
        prepared.append(RunMapping(index=index, alias="(row)", alias_key="", class_id=cid,
                                   name=_text(data.get("name")), row_ids=unique))
    return prepared


def validate_row_targets(mappings, results):
    """Validate against all quarter groups before a request can publish files."""
    from collections import Counter
    seen = Counter()
    for result in results:
        errors = (getattr(result, "summary", None) or {}).get("mapping_errors")
        if errors:
            raise RunMappingError(errors)
        seen.update(getattr(result, "source_row_ids", None) or [])
    for mapping in mappings:
        invalid = [row_id for row_id in mapping.row_ids if seen[row_id] != 1]
        if invalid:
            raise RunMappingError([{"code": "row_mapping_stale_or_unknown", "row_ids": invalid,
                "detail": "Record identities no longer match exactly one retained source record. Revalidate the supplied files and selected partner/period."}])


def resolve_class_ids(mappings: Sequence[RunMapping],
                      class_ids: Optional[Mapping[str, Tuple[str, str]]]) -> List[RunMapping]:
    """Attach the template class for each id. Unknown ids are one error each."""
    known = {str(key).strip().lower(): value for key, value in dict(class_ids or {}).items()}
    errors = []
    resolved = []
    for mapping in mappings:
        found = known.get(mapping.class_id.casefold())
        if not found:
            errors.append(_error(
                "mapping_unknown_class",
                [mapping.index], [mapping.alias], [mapping.class_id],
                mapping.alias_key, mapping.file, mapping.sheet, None,
                (f"class id {mapping.class_id!r} is not an approved class "
                 "for this partner and tenant"),
            ))
            continue
        template, store = found
        resolved.append(replace(mapping, template=template, store_class=store))
    if errors:
        raise RunMappingError(errors)
    return resolved


def _row_parts(row, getter) -> Tuple[str, str, str]:
    label = (getter(row) or "").strip() or "(blank)"
    audit = getattr(row, "audit", None)
    file = getattr(audit, "source_filename", "") if audit else ""
    sheet = getattr(audit, "source_sheet", "") if audit else ""
    return label, str(file or ""), str(sheet or "")


def _covers(mapping: RunMapping, key: str, file: str, sheet: str) -> bool:
    if mapping.row_ids or mapping.alias_key != key:
        return False
    if mapping.file and mapping.file != file:
        return False
    if mapping.sheet and mapping.sheet != sheet:
        return False
    return True


def choose_mapping(mappings: Sequence[RunMapping], label: str,
                   file: str, sheet: str) -> Optional[RunMapping]:
    """The most specific mapping that covers this row.

    Equal specificity means the same scope, so §5 already required the same
    id. The row is counted on the mapping whose alias equals the label,
    otherwise on the first one sent.
    """
    key = match_key(label)
    hits = [item for item in mappings if _covers(item, key, file, sheet)]
    if not hits:
        return None
    best = max(item.specificity for item in hits)
    tied = [item for item in hits if item.specificity == best]
    folded = label.casefold()
    for item in tied:
        if item.alias.casefold() == folded:
            return item
    return tied[0]


def _set_mapped(row, template: str) -> None:
    audit = getattr(row, "audit", None)
    if audit is None:
        return
    audit.run_mapped_class = template


def _override(row, getter, mapping: RunMapping, class_map) -> bool:
    """True when this mapping places a row the built-in map or the store already placed elsewhere."""
    raw = (getter(row) or "").strip()
    base = getattr(class_map, "base", None) or class_map
    builtin = normalize_class_label(raw, base) if raw else ""
    if builtin in ("", "Other"):
        builtin = ""
    store = confirmed_label(raw, class_map) if raw else ""
    inferred = getattr(getattr(row, "audit", None), "inferred_class", "")
    if inferred and inferred != mapping.template:
        return True
    if builtin and builtin != mapping.template:
        return True
    if store and store != mapping.template:
        return True
    return False


def tag_rows(rows, getter, mappings: Sequence[RunMapping], class_map=None, ignored_labels=None) -> List[dict]:
    """Set ``run_mapped_class`` and return one tally per mapping, in order."""
    tallies = [{"index": item.index, "rows": 0, "overrides": 0} for item in mappings]
    by_index = {item.index: tally for item, tally in zip(mappings, tallies)}
    for row in rows or []:
        if run_mapped_class(row):
            continue
        label, file, sheet = _row_parts(row, getter)
        row_id = getattr(getattr(row, "audit", None), "row_id", "")
        chosen = next((m for m in mappings if row_id and row_id in m.row_ids), None)
        if chosen:
            for ignored in ignored_labels or []:
                ignored_label, ignored_file, ignored_sheet = _ignore_fields(ignored)
                if match_key(ignored_label) == match_key(label) and \
                        (not ignored_file or ignored_file == file) and \
                        (not ignored_sheet or ignored_sheet == sheet):
                    raise RunMappingError([{"code": "mapping_and_ignore_conflict", "row_ids": [row_id],
                        "detail": "A record cannot be both assigned a class and ignored"}])
        else:
            if getattr(getattr(row, "audit", None), "class_source", "") == "conflict":
                continue  # Synthetic conflict labels are not reusable class aliases.
            chosen = choose_mapping(mappings, label, file, sheet)
            # Adapters may canonicalize a newly approved subclass before this
            # stage. Preserve an explicit override addressed to its original
            # source label only when both labels resolve to the same class.
            raw = getattr(getattr(row, "audit", None), "class_label_raw", "")
            canonical = normalize_class_label(label, class_map)
            if chosen is None and raw and canonical not in {"", "Other"} and normalize_class_label(raw, class_map) == canonical:
                chosen = choose_mapping(mappings, raw, file, sheet)
        if chosen is None or not chosen.template:
            continue
        _set_mapped(row, chosen.template)
        if chosen.row_ids:
            row.audit.class_resolution['record_mapping'] = True
        tally = by_index[chosen.index]
        tally["rows"] += 1
        if _override(row, getter, chosen, class_map):
            tally["overrides"] += 1
    return tallies


def tag_result_rows(result, mappings: Sequence[RunMapping], class_map=None, ignored_labels=None) -> None:
    """Tag every row kind on a pipeline result and store this group's tallies."""
    merged = {item.index: {"rows": 0, "overrides": 0} for item in mappings}
    for rows, getter in (
        (getattr(result, "premium_rows", None), premium_class_hint),
        (getattr(result, "claims_rows", None), claims_class_hint),
        (getattr(result, "outstanding_rows", None), claims_class_hint),
    ):
        for tally in tag_rows(rows, getter, mappings, class_map, ignored_labels):
            slot = merged.get(tally["index"])
            if slot is None:
                continue
            slot["rows"] += tally["rows"]
            slot["overrides"] += tally["overrides"]
    result.applied_mappings = [
        _public(mapping, merged[mapping.index]["rows"], merged[mapping.index]["overrides"])
        for mapping in mappings
    ]


def _public(mapping: RunMapping, rows: int, overrides: int) -> dict:
    return {
        "index": mapping.index,
        "alias": mapping.alias,
        "alias_key": mapping.alias_key,
        "class_id": mapping.class_id,
        "class_name": mapping.template,
        "store_class": mapping.store_class,
        "file": mapping.file,
        "sheet": mapping.sheet,
        "rows": rows,
        "overrides": overrides,
        **({"row_ids": list(mapping.row_ids), "scope": "row"} if mapping.row_ids else {}),
    }


def applied_from_results(mappings: Sequence[RunMapping], results) -> Tuple[list, list]:
    """Sum group tallies. A mapping used in any group is not unused."""
    rows = {item.index: 0 for item in mappings}
    overrides = {item.index: 0 for item in mappings}
    for result in results or []:
        for item in getattr(result, "applied_mappings", None) or []:
            index = item.get("index")
            if index not in rows:
                continue
            rows[index] += int(item.get("rows") or 0)
            overrides[index] += int(item.get("overrides") or 0)
    applied = []
    warnings = []
    for mapping in mappings:
        count = rows[mapping.index]
        beaten = overrides[mapping.index]
        applied.append({
            "alias": mapping.alias,
            "alias_key": mapping.alias_key,
            "class_id": mapping.class_id,
            "class_name": mapping.template,
            "store_class": mapping.store_class,
            "file": mapping.file,
            "sheet": mapping.sheet,
            "rows": count,
            **({"row_ids": list(mapping.row_ids), "scope": "row"} if mapping.row_ids else {}),
        })
        if count == 0:
            warnings.append({
                "code": "mapping_unused",
                "message": (
                    f"mappings[{mapping.index}] {mapping.alias!r} matched no row"
                ),
            })
        if beaten:
            warnings.append({
                "code": "mapping_overrides_class",
                "message": (
                    f"mappings[{mapping.index}] {mapping.alias!r} placed {mapping.template!r} "
                    f"over the built-in map or a stored alias on {beaten} row(s)"
                ),
            })
    return applied, warnings


def name_mismatch_warnings(mappings: Sequence[RunMapping]) -> list:
    """``name`` is display-only. A mismatch warns; it does not reject the run."""
    warnings = []
    for mapping in mappings:
        if not mapping.name:
            continue
        named = store_class_key(mapping.name)
        if named == store_class_key(mapping.template) or named == store_class_key(mapping.store_class):
            continue
        warnings.append({
            "code": "mapping_name_mismatch",
            "message": (
                f"mappings[{mapping.index}].name {mapping.name!r} is not "
                f"{mapping.template!r} or {mapping.store_class!r}"
            ),
        })
    return warnings
