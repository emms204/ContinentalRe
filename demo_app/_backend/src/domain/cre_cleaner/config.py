"""Configuration constants for cre_cleaner."""
from __future__ import annotations

QUARTER_MONTHS = {
    1: [1, 2, 3],
    2: [4, 5, 6],
    3: [7, 8, 9],
    4: [10, 11, 12],
}

MONTH_NAMES = {
    1: "JANUARY",
    2: "FEBRUARY",
    3: "MARCH",
    4: "APRIL",
    5: "MAY",
    6: "JUNE",
    7: "JULY",
    8: "AUGUST",
    9: "SEPTEMBER",
    10: "OCTOBER",
    11: "NOVEMBER",
    12: "DECEMBER",
}

MONTH_ALIASES = {
    "JAN": 1, "JANUARY": 1,
    "FEB": 2, "FEBRUARY": 2,
    "MAR": 3, "MARCH": 3,
    "APR": 4, "APRIL": 4,
    "MAY": 5,
    "JUN": 6, "JUNE": 6,
    "JUL": 7, "JULY": 7,
    "AUG": 8, "AUGUST": 8,
    "SEP": 9, "SEPT": 9, "SEPTEMBER": 9,
    "OCT": 10, "OCTOBER": 10,
    "NOV": 11, "NOVEMBER": 11,
    "DEC": 12, "DECEMBER": 12,
}

# TEMPLATE sheet names and header rows (1-indexed) — used as layout/style seed only.
# Upload header text comes from UPLOAD_* below (Bisola/validator schema), not TEMPLATE.
TEMPLATE_SHEETS = {
    "premium": ("PREMIUM BORDEREAU", 3),
    "claims": ("CLAIMS BORDEREAU", 4),
    "outstanding": ("OUTSTANDING LOSS BORDEREAU", 4),
}

# ---------------------------------------------------------------------------
# Upload / Bisola-gold premium schema (no CHANNEL / SUB CHANNEL).
# Row 2 bands: RETENTION / TREATY / FACULTATIVE
# Row 3 headers; unique SI + premium names per Continental validator.
# ---------------------------------------------------------------------------
# Proportion-header naming for premium sheets (``--proportion-headers``).
#   gold     : the gold (Bisola) distinct naming, identical strings to
#              "distinct"; the cedant gold-survey evidence for this default is
#              kept with the cedant adapter that relies on it
#   distinct : RET PROPORTION % / TREATY PROPORTION % / FAC PROPORTION %
#   plain    : PROPORTION % x3 (duplicate names — only if Continental insists)
PROPORTION_HEADER_MODES = {
    "gold": ("RET PROPORTION %", "TREATY PROPORTION %", "FAC PROPORTION %"),
    "distinct": ("RET PROPORTION %", "TREATY PROPORTION %", "FAC PROPORTION %"),
    "plain": ("PROPORTION %", "PROPORTION %", "PROPORTION %"),
}
DEFAULT_PROPORTION_MODE = "gold"


def premium_headers(proportion_mode: str = DEFAULT_PROPORTION_MODE) -> list:
    """Row-3 premium headers (exactly 18, A..R) for the given proportion mode."""
    try:
        ret_p, tty_p, fac_p = PROPORTION_HEADER_MODES[proportion_mode]
    except KeyError:
        raise ValueError(
            f"Unknown proportion header mode {proportion_mode!r}; "
            f"choose from {sorted(PROPORTION_HEADER_MODES)}"
        )
    return [
        "S/NO",
        "POLICY NO.",
        "NAME OF INSURED",
        "UNDERWRITING YEAR",
        "FROM",
        "TO",
        "TOTAL SUM INSURED",
        "MPL %",
        "GROSS PREMIUM",
        ret_p,
        "RET SUM INSURED",
        "RET PREMIUM",
        tty_p,
        "TREATY SUM INSURED",
        "TREATY PREMIUM",
        fac_p,
        "FAC SUM INSURED",
        "FAC PREMIUM",
    ]


PREMIUM_HEADERS = premium_headers(DEFAULT_PROPORTION_MODE)

# Physical column indices for upload premium sheets (1-indexed).
# Keys are logical (PremiumRow.to_template_values); display text comes from
# premium_headers(mode) at the same position.
PREMIUM_COL_MAP = {
    "S/NO": 1,
    "POLICY NO.": 2,
    "NAME OF INSURED": 3,
    "UNDERWRITING YEAR": 4,
    "FROM": 5,
    "TO": 6,
    "TOTAL SUM INSURED": 7,
    "MPL %": 8,
    "GROSS PREMIUM": 9,
    "RETENTION PROPORTION %": 10,
    "RET SUM INSURED": 11,
    "RET PREMIUM": 12,
    "TREATY PROPORTION %": 13,
    "TREATY SUM INSURED": 14,
    "TREATY PREMIUM": 15,
    "FACULTATIVE PROPORTION %": 16,
    "FAC SUM INSURED": 17,
    "FAC PREMIUM": 18,
}

# Band labels on premium header row 2 (1-indexed col → text)
PREMIUM_BAND_LABELS = {
    5: "INSURANCE PERIOD",
    10: "R E T E N T I O N",
    13: "TREATY",
    16: "F A C U L T A T I V E",
}

# ---------------------------------------------------------------------------
# Upload claims & outstanding schema — matches templates/TEMPLATE.xlsx.
# TEMPLATE / gold have a leading blank col A (S/NO. at col 2). The upload
# writer reproduces this by default (claims_leading_blank=True: col A fully
# empty, headers B..R); CLAIMS_COL_MAP holds those positions and io_excel
# shifts them to start at col A only when claims_leading_blank=False.
# Unique amount designations (RET/TREATY/FAC AMOUNT); no SUM INSURED column
# (TEMPLATE goes TO → TOTAL CLAIMS). Later Bisola golds that add SI are not
# the upload contract.
# ---------------------------------------------------------------------------
CLAIMS_HEADERS = [
    "S/NO.",
    "INSURED",
    "CLASS",
    "POLICY NO.",
    "CLAIM NO",
    "DATE OF LOSS (day-mth-year)",
    "UW YR",
    "FROM",
    "TO",
    "TOTAL CLAIMS",
    "PPN RET %",
    "RET AMOUNT",
    "PPN TREATY %",
    "TREATY AMOUNT",
    "PPN FAC %",
    "FAC AMOUNT",
    "DETAILS OF LOSS",
]

CLAIMS_COL_MAP = {
    "S/NO.": 2,
    "INSURED": 3,
    "CLASS": 4,
    "POLICY NO.": 5,
    "CLAIM NO": 6,
    "DATE OF LOSS (day-mth-year)": 7,
    "UW YR": 8,
    "FROM": 9,
    "TO": 10,
    "TOTAL CLAIMS": 11,
    "PPN RET %": 12,
    "RET AMOUNT": 13,
    "PPN TREATY %": 14,
    "TREATY AMOUNT": 15,
    "PPN FAC %": 16,
    "FAC AMOUNT": 17,
    "DETAILS OF LOSS": 18,
}

# Period banner on claims header row (header_row - 1), col of FROM
CLAIMS_PERIOD_BANNER_COL = 9

SKIP_TOKENS = {
    "NIL", "N/A", "NA", "NONE", "TOTAL", "SUBTOTAL", "GRAND TOTAL",
    "TOTALS", "BROUGHT FORWARD", "CARRIED FORWARD", "BALANCE",
}

# Excel error literals that must never be written to an upload workbook.
EXCEL_ERROR_STRINGS = frozenset({
    "#VALUE!", "#REF!", "#DIV/0!", "#N/A", "#NAME?", "#NUM!", "#NULL!",
    "#GETTING_DATA", "#SPILL!", "#CALC!", "#FIELD!", "#BLOCKED!", "#UNKNOWN!",
})

# Class label that Continental told Bisola to ignore (not a treaty class)
FAC_CLASS_LABEL = "Facultative"
