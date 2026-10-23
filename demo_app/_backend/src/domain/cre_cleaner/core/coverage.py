"""Evidence-based row availability, separate from financial validation."""
from __future__ import annotations

_KINDS = (("premium", "Premium", "premium_rows"),
          ("claims", "Claims (paid)", "claims_rows"),
          ("outstanding", "Outstanding", "outstanding_rows"))
_TYPE = {"premium": "premium", "paid": "claims", "claims": "claims", "outstanding": "outstanding"}
_PROBLEMS = {"header_not_found", "premium_file_unreadable", "claims_parse_failed",
             "lasaco_outstanding_source_conflict",
             "lasaco_outstanding_layout_unresolved",
             "heirs_claims_correction_review", "heirs_claims_layout_unresolved",
             "source_layout_ambiguous", "required_field_unmapped", "secondary_source_unreadable"}


def cleaning_coverage(result, *, mode="all", errors=()):
    """Three availability records. Counts come only from published outputs.

    A detected source table is not proof that it contains extractable records;
    absence of detection is likewise not proof that an unknown table is empty.
    """
    audits = list(getattr(result, "source_audit", None) or [])
    exceptions = list(getattr(result, "exceptions", None) or [])
    summary = getattr(result, "summary", None) or {}
    outputs = list(getattr(result, "outputs", None) or [])
    error = summary.get("error")
    unknown = any(str(getattr(a, "sheet_type", "")).lower() == "unknown" for a in audits)
    unknown = unknown or any(getattr(e, "reason", "") == "table_type_unknown" for e in exceptions)
    unknown_details = []
    for e in exceptions:
        if getattr(e, "reason", "") == "table_type_unknown":
            record = dict(code=e.reason, file=e.source_filename, sheet=e.source_sheet, detail=e.detail)
            if record not in unknown_details:
                unknown_details.append(record)
    requested = {"premium", "claims", "outstanding"}
    if mode in {"premium", "claims", "outstanding"}:
        requested = {mode}
    rows = []
    for kind, label, field in _KINDS:
        by_source = {}
        for audit in audits:
            if _TYPE.get(str(getattr(audit, "sheet_type", "")).lower()) != kind:
                continue
            key = (audit.source_filename, audit.source_sheet)
            previous = by_source.get(key)
            if previous is None or (audit.header_row, audit.rows_kept) > (previous.header_row, previous.rows_kept):
                by_source[key] = audit
        sources = list(by_source.values())
        locations = {(a.source_filename, a.source_sheet) for a in sources}
        details = []
        for exc in exceptions:
            code = getattr(exc, "reason", "")
            if code in {"lasaco_outstanding_source_conflict", "lasaco_outstanding_layout_unresolved"} and kind != "outstanding":
                continue
            if code in {"heirs_claims_correction_review", "heirs_claims_layout_unresolved"} and kind != "claims":
                continue
            location = (getattr(exc, "source_filename", ""), getattr(exc, "source_sheet", ""))
            named_kind = (kind == "premium" and "premium" in code or
                          kind in {"claims", "outstanding"} and "claims" in code)
            if code not in _PROBLEMS or not (location in locations or named_kind or code == "source_layout_ambiguous"):
                continue
            record = dict(code=code, file=location[0], sheet=location[1], detail=getattr(exc, "detail", ""))
            if record not in details:
                details.append(record)
        count = sum(int(o.get(field) or 0) for o in outputs)
        if kind not in requested:
            status, reason = "not_requested", "Excluded by the selected bordereau type."
        elif count:
            status = "partial" if details or unknown or summary.get("ignored") else "cleaned"
            reason = "Rows included in the cleaned workbooks."
            if details:
                if any(d['code'] == 'lasaco_outstanding_source_conflict' for d in details):
                    reason += " Conflicting outstanding source versions are included; choose the controlling file and re-run."
                else:
                    reason += " Some source tables have extraction or field-mapping issues; review details."
            if unknown:
                reason += " Unclassified tables were not loaded; their contents need review."
            if summary.get("ignored"):
                reason += " User-requested class exclusions apply to this run."
        elif error and "class" in str(error):
            status, reason = "blocked", "Class mapping required; no workbook published for this group."
        elif errors or (error and error != "no_data_rows"):
            status, reason = "failed", str("; ".join(errors) or summary.get("detail") or error)
        elif details:
            status, reason = "failed", "Detected source could not be fully extracted. " + details[0]["detail"]
        elif not sources:
            status, reason = "not_detected", "No source table of this type was identified in the processed files."
            if unknown:
                reason += " Unclassified tables were not loaded; absence cannot be confirmed."
        elif summary.get("ignored"):
            status, reason = "no_rows", "No rows published. User-requested class exclusions apply; review ignored rows."
        elif all(a.rows_kept == 0 for a in sources) and any("explicit_nil_return" in a.notes for a in sources):
            status, reason = "empty", "Source explicitly reports NIL/no transactions; no data rows to publish."
        elif all(a.header_row > 0 and a.rows_read == 0 for a in sources):
            status, reason = "empty", "Source headers found, but no data rows were read."
        else:
            status, reason = "no_rows", "Source detected, but no rows were published; review source and exclusion details."
            if sources and all(a.rows_kept == 0 for a in sources):
                reason = "Source table read, but no data rows were retained by parsing; review skipped rows and source details."
        rows.append(dict(type=kind, label=label, rows=count, status=status, reason=reason,
                         source_tables=len(locations), details=details + unknown_details,
                         sources=[dict(file=a.source_filename, sheet=a.source_sheet,
                                       rows_read=a.rows_read, rows_parsed=a.rows_kept, notes=a.notes)
                                  for a in sorted(sources, key=lambda a:(a.source_filename,a.source_sheet))]))
    return rows
