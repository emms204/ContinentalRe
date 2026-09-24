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
    sur_ppn: Any = None
    sur_si: Any = None
    sur_prem: Any = None
    fac_ppn: Any = None
    fac_si: Any = None
    fac_prem: Any = None
    audit: AuditMeta = field(default_factory=AuditMeta)

    def to_template_values(self) -> dict:
        return {
            "POLICY NO.": self.policy_no,
            "NAME OF INSURED": self.name_of_insured,
            "CHANNEL": self.channel or None,
            "SUB CHANNEL": self.sub_channel or None,
            "UNDERWRITING YEAR": self.underwriting_year,
            "FROM": self.period_from,
            "TO": self.period_to,
            "TOTAL SUM INSURED": self.total_sum_insured,
            "MPL %": self.mpl_pct,
            "GROSS PREMIUM": self.gross_premium,
            "RETENTION PROPORTION %": self.ret_ppn,
            "RETENTION SUM INSURED": self.ret_si,
            "RETENTION PREMIUM": self.ret_prem,
            "SURPLUS PROPORTION %": self.sur_ppn,
            "SURPLUS SUM INSURED": self.sur_si,
            "SURPLUS PREMIUM": self.sur_prem,
            "FACULTATIVE PROPORTION %": self.fac_ppn,
            "FACULTATIVE SUM INSURED": self.fac_si,
            "FACULTATIVE PREMIUM": self.fac_prem,
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
    total_claims: Any = None
    ppn_ret: Any = None
    amount_ret: Any = None
    ppn_treaty: Any = None
    amount_treaty: Any = None
    ppn_fac: Any = None
    amount_fac: Any = None
    details: str = ""
    audit: AuditMeta = field(default_factory=AuditMeta)

    def to_template_values(self) -> dict:
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
            "AMOUNT RET": self.amount_ret,
            "PPN TREATY %": self.ppn_treaty,
            "AMOUNT TREATY": self.amount_treaty,
            "PPN FAC %": self.ppn_fac,
            "AMOUNT FAC": self.amount_fac,
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

    def as_row(self) -> list:
        return [
            self.source_filename,
            self.source_sheet,
            self.sheet_type,
            self.header_row,
            self.rows_read,
            self.rows_kept,
            self.rows_skipped,
            self.source_month,
            self.notes,
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
