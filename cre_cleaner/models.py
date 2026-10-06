"""Data models for cleaned bordereau rows and audit/exception logs."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class AuditMeta:
    source_month: str = ""
    source_filename: str = ""
    source_sheet: str = ""
    source_row: int = 0
    class_hint: str = ""
    # How the class was resolved: "sheet" (tab name), "banner" (section banner
    # row above the rows, tab gave no class), "column" (row-level CLASS), or "".
    class_source: str = ""
    # Original class text as it appeared in the source (tab name / banner cell).
    class_label_raw: str = ""
    # ISO-ish code (NGN, USD, EUR, GBP) or FCY when the source only says
    # "foreign". Rows of different currencies are never summed together.
    currency: str = "NGN"


@dataclass
class PremiumRow:
    policy_no: str = ""
    name_of_insured: str = ""
    channel: str = ""
    sub_channel: str = ""
    underwriting_year: Any = None
    period_from: Any = None
    period_to: Any = None
    total_sum_insured: Any = None
    mpl_pct: Any = None
    gross_premium: Any = None
    ret_ppn: Any = None
    ret_si: Any = None
    ret_prem: Any = None
    # sur_* = treaty/surplus allocation block (output headers say TREATY)
    sur_ppn: Any = None
    sur_si: Any = None
    sur_prem: Any = None
    fac_ppn: Any = None
    fac_si: Any = None
    fac_prem: Any = None
    # Treaty layers beyond the one written to the TREATY band (the 18-column
    # upload schema has a single band): [{"layer", "ppn", "si", "prem"}].
    extra_layers: list = field(default_factory=list)
    audit: AuditMeta = field(default_factory=AuditMeta)

    def to_template_values(self) -> dict:
        """Keys match PREMIUM_COL_MAP (upload / Bisola schema)."""
        return {
            "POLICY NO.": self.policy_no,
            "NAME OF INSURED": self.name_of_insured,
            "UNDERWRITING YEAR": self.underwriting_year,
            "FROM": self.period_from,
            "TO": self.period_to,
            "TOTAL SUM INSURED": self.total_sum_insured,
            "MPL %": self.mpl_pct,
            "GROSS PREMIUM": self.gross_premium,
            "RETENTION PROPORTION %": self.ret_ppn,
            "RET SUM INSURED": self.ret_si,
            "RET PREMIUM": self.ret_prem,
            "TREATY PROPORTION %": self.sur_ppn,
            "TREATY SUM INSURED": self.sur_si,
            "TREATY PREMIUM": self.sur_prem,
            "FACULTATIVE PROPORTION %": self.fac_ppn,
            "FAC SUM INSURED": self.fac_si,
            "FAC PREMIUM": self.fac_prem,
        }


@dataclass
class ClaimsRow:
    insured: str = ""
    class_name: str = ""
    policy_no: str = ""
    claim_no: str = ""
    date_of_loss: Any = None
    uw_yr: Any = None
    period_from: Any = None
    period_to: Any = None
    sum_insured: Any = None  # source-only; not in TEMPLATE / CLAIMS_COL_MAP
    total_claims: Any = None
    ppn_ret: Any = None
    amount_ret: Any = None
    ppn_treaty: Any = None
    amount_treaty: Any = None
    ppn_fac: Any = None
    amount_fac: Any = None
    details: str = ""
    # Not in the upload schema; kept for date checks and reconciliation.
    paid_date: Any = None
    extra_layers: list = field(default_factory=list)  # [{"layer", "amount"}]
    audit: AuditMeta = field(default_factory=AuditMeta)

    def to_template_values(self) -> dict:
        """Keys match CLAIMS_COL_MAP (TEMPLATE.xlsx upload schema)."""
        return {
            "INSURED": self.insured,
            "CLASS": self.class_name,
            "POLICY NO.": self.policy_no,
            "CLAIM NO": self.claim_no,
            "DATE OF LOSS (day-mth-year)": self.date_of_loss,
            "UW YR": self.uw_yr,
            "FROM": self.period_from,
            "TO": self.period_to,
            "TOTAL CLAIMS": self.total_claims,
            "PPN RET %": self.ppn_ret,
            "RET AMOUNT": self.amount_ret,
            "PPN TREATY %": self.ppn_treaty,
            "TREATY AMOUNT": self.amount_treaty,
            "PPN FAC %": self.ppn_fac,
            "FAC AMOUNT": self.amount_fac,
            "DETAILS OF LOSS": self.details,
        }


@dataclass
class ExceptionRecord:
    severity: str
    reason: str
    source_filename: str = ""
    source_sheet: str = ""
    source_row: int = 0
    detail: str = ""

    def as_row(self) -> list:
        return [
            self.severity,
            self.reason,
            self.source_filename,
            self.source_sheet,
            self.source_row,
            self.detail,
        ]


@dataclass
class SourceAuditRecord:
    source_filename: str
    source_sheet: str
    sheet_type: str
    header_row: int
    rows_read: int
    rows_kept: int
    rows_skipped: int
    source_month: str = ""
    notes: str = ""
    # Sheet-level currency, or "MIXED" when a CURRENCY column varies by row.
    currency: str = "NGN"
    hidden_rows: int = 0
    # Row sums of kept rows: {currency: {metric: amount}} and {currency: rows}.
    parsed_totals: dict = field(default_factory=dict)
    parsed_rows: dict = field(default_factory=dict)
    # Cedant's own total/footer rows: {metric: amount}. Empty = no total row.
    footer_totals: dict = field(default_factory=dict)
    # Content-based table type (core.table_type): PREMIUM / PAID / OUTSTANDING /
    # UNKNOWN / OTHER, its confidence and per-table evidence + conflicts.
    detected_type: str = ""
    type_confidence: Optional[float] = None
    type_evidence: str = ""

    AUDIT_HEADERS = [
        "Source Filename", "Source Sheet", "Sheet Type", "Header Row",
        "Rows Read", "Rows Kept", "Rows Skipped", "Source Month", "Currency",
        "Hidden Rows Skipped", "Row Sum (main amount)", "Source Total Row (main amount)",
        "Notes", "Detected Type", "Type Confidence", "Type Evidence / Conflicts (per table)",
    ]

    def main_metric(self) -> str:
        return "gross" if self.sheet_type == "premium" else "total"

    def as_row(self) -> list:
        metric = self.main_metric()
        row_sum = sum(
            (t.get(metric) or 0.0) for t in self.parsed_totals.values()
        ) if self.parsed_totals else None
        return [
            self.source_filename,
            self.source_sheet,
            self.sheet_type,
            self.header_row,
            self.rows_read,
            self.rows_kept,
            self.rows_skipped,
            self.source_month,
            self.currency,
            self.hidden_rows,
            row_sum,
            self.footer_totals.get(metric),
            self.notes,
            self.detected_type,
            self.type_confidence,
            self.type_evidence,
        ]


@dataclass
class PipelineResult:
    premium_rows: list = field(default_factory=list)
    claims_rows: list = field(default_factory=list)
    outstanding_rows: list = field(default_factory=list)
    exceptions: list = field(default_factory=list)
    source_audit: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    output_path: Optional[str] = None
    exceptions_path: Optional[str] = None
    source_audit_path: Optional[str] = None
    # One entry per currency workbook: {"currency", "output_path",
    # "exceptions_path", "source_audit_path", "summary"}. output_path above is
    # the primary (NGN when present) workbook.
    outputs: list = field(default_factory=list)
