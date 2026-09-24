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

# TEMPLATE sheet names and header rows (1-indexed)
TEMPLATE_SHEETS = {
    "premium": ("PREMIUM BORDEREAU", 3),
    "claims": ("CLAIMS BORDEREAU", 4),
    "outstanding": ("OUTSTANDING LOSS BORDEREAU", 4),
}

# Premium template columns (row 3). Retention=cols 12-14, Surplus=15-17, Fac=18-20
PREMIUM_HEADERS = [
    "S/NO",
    "POLICY NO.",
    "NAME OF INSURED",
    "CHANNEL",
    "SUB CHANNEL",
    "UNDERWRITING YEAR",
    "FROM",
    "TO",
    "TOTAL SUM INSURED",
    "MPL %",
    "GROSS PREMIUM",
    "RETENTION PROPORTION %",
    "RETENTION SUM INSURED",
    "RETENTION PREMIUM",
    "SURPLUS PROPORTION %",
    "SURPLUS SUM INSURED",
    "SURPLUS PREMIUM",
    "FACULTATIVE PROPORTION %",
    "FACULTATIVE SUM INSURED",
    "FACULTATIVE PREMIUM",
]

# Physical column indices in TEMPLATE PREMIUM sheet (1-indexed)
PREMIUM_COL_MAP = {
    "S/NO": 1,
    "POLICY NO.": 2,
    "NAME OF INSURED": 3,
    "CHANNEL": 4,
    "SUB CHANNEL": 5,
    "UNDERWRITING YEAR": 6,
    "FROM": 7,
    "TO": 8,
    "TOTAL SUM INSURED": 9,
    "MPL %": 10,
    "GROSS PREMIUM": 11,
    "RETENTION PROPORTION %": 12,
    "RETENTION SUM INSURED": 13,
    "RETENTION PREMIUM": 14,
    "SURPLUS PROPORTION %": 15,
    "SURPLUS SUM INSURED": 16,
    "SURPLUS PREMIUM": 17,
    "FACULTATIVE PROPORTION %": 18,
    "FACULTATIVE SUM INSURED": 19,
    "FACULTATIVE PREMIUM": 20,
}

# Claims / Outstanding template columns start at col 2
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
    "AMOUNT RET",
    "PPN TREATY %",
    "AMOUNT TREATY",
    "PPN FAC %",
    "AMOUNT FAC",
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
    "AMOUNT RET": 13,
    "PPN TREATY %": 14,
    "AMOUNT TREATY": 15,
    "PPN FAC %": 16,
    "AMOUNT FAC": 17,
    "DETAILS OF LOSS": 18,
}

SKIP_TOKENS = {
    "NIL", "N/A", "NA", "NONE", "TOTAL", "SUBTOTAL", "GRAND TOTAL",
    "TOTALS", "BROUGHT FORWARD", "CARRIED FORWARD", "BALANCE",
}
