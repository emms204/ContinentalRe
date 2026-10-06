# Reference TEMPLATE.xlsx

- Source: `/home/emms/Downloads/ZindiComp/MasteryHIve/Template.xlsx` (Emmanuel's HP "Rex"),
  copied 2026-10-06 by IMPL-20261006-04 (template swap). It was copied (`cp`), never opened in place.
- Origin mtime: 2026-10-06 14:20:30 +0100 (WAT); 248178 bytes.
- sha256: `c60d29c5e2791c68f1ac9e9d1d71a4490589890683bfcc5c06eb052b6fc31f68`.
- Layout: PREMIUM BORDEREAU headers A3:T3 with bands INSURANCE PERIOD (G2:H2), R E T E N T I O N
  (L2:N2), TREATY (O2:Q2), F A C U L T A T I V E (R2:T2) and unique RET / TREATY / FAC names;
  CLAIMS and OUTSTANDING LOSS BORDEREAU headers B4:R4 with RET / TREATY / FAC AMOUNT and the
  INSURANCE PERIOD band at I3:J3.
- Replaces the earlier ContinentalRe `templates/TEMPLATE.xlsx` copy (IMPL-20261006-03 B5,
  sha256 `4dae20fff7cf9c3f69fb3e3ab20558507167bc57f7250df76629dff79c632c3f`), which still had the
  SURPLUS band and duplicate PROPORTION % / SUM INSURED / PREMIUM / AMOUNT names.
- Used by `io/upload_validator` (`template_header_deviations`, `template_alignment_problems`,
  `validate_against_template`) and their unit tests only.
- It is deliberately NOT at `templates/TEMPLATE.xlsx` (`paths.TEMPLATE_PATH`): placing it there turns
  on the writer's style seed and changes output styling. Whether to do that is a decision for Emmanuel.
- Differences from the upload contract after alignment are listed in
  `upload_validator.TEMPLATE_DEVIATIONS_ALLOWED`: CHANNEL / SUB CHANNEL (excluded from the cleaned
  Premium output, Cleaning Manual Part Four Step 12 and Part Seven) and whitespace-only items.
  Output headers and column positions are unchanged by the swap.
