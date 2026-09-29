# CRE cleaner: test of the current implementation (28 Sep 2026, ~21:00–22:30 WAT)

**Scope:** read-only test of the code on the Mac at `e2a7039 "Newer Cedants"` (28 Sep 12:29 WAT) plus the uncommitted changes (10 files, +386/−135, including `adapters/new_cedants.py`, `period_infer.py` and `pdf_extract.py`). Code and data were copied to `/workspace/cre_test/`, and everything ran there with `convert_pdfs=False`, so no data went to LlamaParse. Nothing was fixed. Nothing was written into the repo.

## Verdict: PARTLY ACCURATE, and not safe to upload without review. For most non-AIICO cedants it is BROKEN at the cell level.

What works:
* **Rows are captured.** The cleaner finds the right rows in most quarters. Premium row recall is 96.9% for AIICO and 74.4% for non-AIICO cedants.
* **Gross premium totals are often right.** They match gold in many quarters.
* **It rarely crashes.** There were no crashes in 138 real outputs or in 40 synthetic cases. `validate_upload.py` passed 138 out of 138.

What does not work:
* **The money split is almost never right.** The retention / treaty / fac split on premium, and the retention / treaty / total amounts on claims, are mostly blank or wrong. It happens silently.
* **No non-AIICO row matches gold exactly.** Across 10 cedant/broker pairs and 84 premium quarters, the exact-row match rate is **0.0%**.
* **Some cells hold the wrong kind of value.** In several cedants, percentages land in amount columns: LASACO OS has `RET AMOUNT = 50`, and HEIRS/REX have `TREATY AMOUNT = 92.59` / `60.27`.
* **Two new discovery/classification bugs move money between quarters and between paid and outstanding** (see risks 1 and 2).
* **The validator can't catch any of this.** It only checks structure, so every one of these outputs passes it.

| Group (equal weight) | Premium row recall | Premium cell accuracy | Paid cell accuracy | OS cell accuracy | Row-exact vs gold (prem / paid / OS) | Quarters where count and money = gold |
|---|---|---|---|---|---|---|
| AIICO (ARK + SCIB, 2020–25) | 96.9% | 69.6% | 99.2% (but see SCIB paid/OS mix-up) | 95.9% | 11.2% / 89.2% / 31.8% | prem 27/45, paid 21/47, OS 11/47 |
| **Non-AIICO (8 pairs)** | **74.4%** | **67.0%** | **50.5%** | **46.6%** | **0.0% / 0.0% / 0.0%** | prem 35/84, paid 30/76, OS 3/27 |

How to read the columns:
* *Cell accuracy* is measured on matched rows, over the fields that both files have: policy, insured, from/to, TSI, gross, the ret/treaty/fac PPN, SI and premium, and the claim total and amounts.
* *Count and money = gold* means the row count is equal AND the net amount variance is under ₦1.

Absolute money variance (premium) on the amount column:
* AIICO: ₦1.70bn out of ₦62.5bn
* Non-AIICO: ₦65.7bn out of ₦205.4bn

For claims, the variance is inflated wherever our TOTAL is blank (AXA, HEIRS, NEM).

### Per cedant/broker verdict (one line each, equal weight)
| Cedant/broker (quarters tested) | Verdict | Why |
|---|---|---|
| AIICO/ARK (24) | Partly accurate | Rows are 95–99.9% right. Paid cells are 99.2% right. Premium ret/treaty bands are blank in 12/23 quarters because headers like `RTNTN PPN`, `TREATY PPN1/2`, `QUOTA SHARE PPN`, `COVER START/END` are not recognised. In 2020Q2, 2021Q2 and 2025Q2, claims from other quarters' files are added (risk 1). |
| AIICO/SCIB (23) | Broken on claims | Premium rows are 98.7% right, but the treaty band is blank on about 85% of rows. 12,481 of 13,110 OS rows (95%) are written as PAID because the tabs are named `… os claims` (risk 2). That puts ₦56.0bn in the paid sheets against ₦16.6bn in gold. |
| AXA/DIRECT (2024–25, 8) | Broken | 0 premium rows in all 8 quarters, so all 1,046 gold rows (₦25.8bn) are missing. Claims TOTAL is blank and the PPN is written into TREATY AMOUNT. |
| CUSTODIAN/SCIB (24) | Partly accurate (best non-AIICO) | Premium recall is 95.5% and 23/24 quarters match gold on count and gross. RET PREMIUM is blank on 100% of rows. FAC SI/premium are blank. The claims RET AMOUNT is blank and the RET PPN is 0. |
| NEM/SCIB (24) | Broken | 2,194 premium rows are missing, likely USD/foreign tabs. GROSS PREMIUM and all bands are blank on about 100% of matched rows. Claims FROM/TO/TOTAL/amounts are blank. Cell accuracy is 34–36% on claims. |
| ROYAL EXCHANGE/DIRECT (2024–25, 8) | Broken | Premium recall is only 64.6%, with 11,354 rows missing and 7,838 extra. 2024Q2 took the `3RD QTR 2024` file (risk 1). 2024Q4 has +2,470 rows (+₦18.1bn). Bands are blank. PPN is written into TREATY AMOUNT. |
| HEIRS/DIRECT (2025, 4) | Broken | 2025Q4 has 0 premium rows (984 missing, WARN `class_unresolved`). In 2025Q3 the sheet copy `Q3 Claims Recovery (2)` is also read, so paid rows are doubled (212 vs 104), with no flag. Bands and dates are blank. Claims TOTAL is blank and the PPN is written into TREATY AMOUNT. |
| HEIRS/JOMOLA (2021, 4) | Partly accurate on rows, broken on the split | Premium recall is 99.8%. In 2021Q2 a production-listing tab (`Prod. - Jan. - Aug 6`) is read as premium: +111 rows, +₦227m, flagged only as `premium_quarter_ambiguous`. All bands are blank. 2021Q1 paid is missing: the claims live inside the premium workbook and the run stopped with `ERROR no_claims_files`. |
| LASACO/FEYBIL (2023, 4) | Rows and gross right, split broken | Premium recall is 99.8% and the gross variance is only ₦0.3m. FROM/TO and all bands are blank. On OS, RET AMOUNT and TREATY AMOUNT each hold `50` (a percentage) and TOTAL is blank. |
| UNITRUST/AGRIC (2023+2025, 8) | Broken | Every 2025 quarter has 0 premium rows (43 missing, WARN `class_unresolved`), even though the adapter is the AGRIC one. The 2023 rows are present but the bands are blank. There are 0 of 6 OS rows. |

### Cedant/broker inventory
* **Gold AND raw, tested here (10 pairs):** AIICO/ARK, AIICO/SCIB, AXA/DIRECT (2024–25 only; 2020–23 raw are mostly PDFs), CUSTODIAN/SCIB, NEM/SCIB, ROYAL EXCHANGE/DIRECT (2024–25), HEIRS/DIRECT (2025), HEIRS/JOMOLA (2021), LASACO/FEYBIL (2023), UNITRUST/AGRIC (2023, 2025).
* **Gold AND raw, NOT tested (raw not copied yet, or blocked):**
  * CUSTODIAN/UAIB, NEM/AON, ROYAL EXCHANGE/AGRIC and REX 2021–23: raw sits inside `~/Downloads/Next 5 Cedant.zip`, which is 167 MB and over the copy limit.
  * LASACO/JOMOLA (124 MB), LASACO/JORDANS (71 MB) and UNITRUST/ARK (79 files): not copied, for time.
  * The other years of HEIRS/JOMOLA, LASACO/FEYBIL and UNITRUST/AGRIC 2024: the Mac shell got **"Permission denied"** on about 35 NewData files (macOS privacy protection / quarantine). CopyToBox may still work for these.
* **Gold but no adapter:** none. Every gold pair has a registered adapter, but all 15 new adapters are `verified=False`, and most are empty `QuarterlyWorkbookAdapter` subclasses.
* **Raw but no gold:** CHI/SCIB (176 of its raw files are PDFs), MUTUAL BENEFITS/ARK, MUTUAL BENEFITS/JOMOLA AGRIC, AIICO/AGRIC DIRECT, HEIRS/HIB.
* **Google Drive:** not searched separately. The Mac `gold/_incoming_zips` are the Drive downloads of the cleaned folders (drive-download …161921 / …162025), and those are what I used.

## Top silent-error risks (with reproduction)
All commands run on the box: `cd /workspace/cre_test && PY=/workspace/.venv_cre/bin/python`.

1. **Claims and premium files from other quarters get pulled into Q2 (money-wrong-silently).** The `quarters_in_text` regex `QTR\s*([1-4])` in `adapters/base.py:52` matches the "2" of the year in "Qtr 2021". So `4TH Qtr 2021 Claims Bord.xls` → {2, 4}, `1ST QTR 2020 …` → {1, 2}, and `3RD QTR 2024 …` → {2, 3}. This was introduced by the QTR1/1QTR discovery change made since 12:10.
   * Real impact:
     * AIICO/ARK 2020Q2: OS 1,143 vs 558 and paid 200 vs 90
     * AIICO/ARK 2021Q2: OS +₦1.70bn and paid +₦213m (the Q4 file was read)
     * AIICO/ARK 2025Q2: OS 3,389 vs 923 (+₦5.39bn) and paid +₦1.12bn
     * REX 2024Q2: took the Q3 premium file
   * Only `apparent_duplicate` WARNs appear.
   * Reproduce: `$PY -c "import sys;sys.path.insert(0,'code');from cre_cleaner.adapters.base import quarters_in_text as q;print(q('4TH Qtr 2021 Claims Bord.xls'),q('1ST QTR 2020 CLAIMS.xls'))"` → `{2, 4} {1, 2}`. Edge case `cross_quarter_filename` gives paid=6 / OS=6 instead of 2 / 2.
2. **Outstanding tabs named "OS" / "O/S" are treated as PAID (money-wrong-silently).** In `detect.detect_sheet_type`, "OS" is not an outstanding token. `class_from_sheet_name('marine os claims')` then returns the raw tab name as the class, so you get sheets like `Marine Os Claims - CLAIMS`.
   * Real impact: AIICO/SCIB, every quarter. 95% of OS rows land in paid.
   * Reproduce: `$PY -c "import sys;sys.path.insert(0,'code');from cre_cleaner.core.detect import detect_sheet_type as d;print(d('marine os claims',[]),d('Fire O/S Claims',[]))"` → `paid paid`. Edge case `os_sheet_name` confirms it.
3. **Band split is blank or mis-mapped for non-AIICO layouts and for AIICO 2022–24 headers (money-wrong-silently).**
   * Premium RET/TREATY PPN/SI/premium are blank on about 100% of rows for CUSTODIAN (RET only), NEM, REX, HEIRS, LASACO and UNITRUST, and in 12/23 AIICO/ARK quarters.
   * On claims, PPN percentages are written into amount columns: LASACO OS `RET AMOUNT=50, TREATY AMOUNT=50`; HEIRS paid `TREATY AMOUNT=92.59` with TOTAL blank.
   * No exception is raised. Only `period_*_missing` fires when the dates are also blank.
   * Reproduce: open `runs/LASACO_FEYBIL/2023Q3/LASACO_FEYBIL_2023_Q3_cleaned.xlsx`, sheet `Bond - OUTSTANDING`, row 5. Or open `runs/HEIRS_DIRECT/2025Q1/*_cleaned.xlsx`, sheets `Fire - PREMIUM` and `Fire - CLAIMS`. To re-run: `$PY report/scripts/run_cedant.py LASACO FEYBIL $PWD/raw/newdata/LASACO/FEYBIL $PWD/runs/LASACO_FEYBIL 2023`.
4. **Whole premium bordereaux missing, flagged only as WARN or not at all (data-lost).**
   * AXA premium: 0 rows, 8/8 quarters.
   * HEIRS/DIRECT 2025Q4: 0 of 984 rows (WARN `class_unresolved` ×984).
   * UNITRUST/AGRIC 2025: 0 rows in every quarter.
   * NEM: 2,194 rows missing.
   * The API still returns HTTP 200 in these cases.
   * Reproduce: `runs/*/run.json` and `runs/*.log` show `rows: [0, …]`.
5. **Duplicate sheets and extra listing tabs are ingested (money-wrong-silently).**
   * HEIRS/DIRECT 2025Q3 reads both `Q3 Claims Recovery` and `Q3 Claims Recovery (2)`, doubling paid rows.
   * HEIRS/JOMOLA 2021Q2 reads the tab `Prod. - Jan. - Aug 6` as premium.
   * Evidence: the `*_source_audit.xlsx` files in those run folders.
6. **Number parsing (money-wrong-silently).** Text amounts `1,234-`, `₦1,234,567.89` and `NGN 1,000.00` become **blank gross premium** with no exception. `(1,234.50)` works. Edge cases: `trailing_minus`, `currency_symbol`, `currency_code`.
7. **Second surplus / quota share band money dropped (money-wrong-silently).** With OWN RETENTION / 1ST SURPLUS / 2ND SURPLUS, the output premium columns sum to ₦67.5k against a gross of ₦90k. The 2nd-surplus premium is not placed anywhere in the upload sheet, and no flag is raised. Edge case: `second_surplus_qs`.
8. **Unknown classes accepted silently.** A tab `XYZ UNKNOWN` becomes its own class sheet. `Casualty` also becomes its own class, while gold uses General Accident. A bare `MARINE` tab is kept out, flagged `class_unresolved`. A tab with a CLASS column is not split by that column: all its rows are kept out and flagged.
9. **An empty premium workbook for a month raises no flag.** A file that exists but has no data rows is not reported as a missing month. Edge case: `empty_workbook`.
10. **Exact duplicate premium rows are kept with no `apparent_duplicate` flag.** Edge case: `dup_exact`. On real data the flag does fire in some cases.

**Crashes:** none. No crash in 138 real-data quarter runs across 10 pairs, in the 40 synthetic cases, or in the API tests.

Related notes:
* Source file `NewData/AIICO/ARK/2024/JAN Premium Loc.xlsx` is genuinely truncated: the Mac copy is the same 882,996 bytes and isn't a valid zip. It was correctly flagged `ERROR premium_file_unreadable`.
* A relative `raw_dir` is resolved against `base_dir`, not the CWD. It gives `ERROR no_premium_files` / `no_claims_files` and empty workbooks rather than a crash.

## Gold inconsistencies vs genuine cleaner errors
I checked the gold files for internal consistency (`report/gold_consistency.json`, `scripts/gold_consistency.py`):
* **Split mismatches.** Gold premium ret+treaty+fac ≠ gross in 33% of AIICO/ARK and 25% of AIICO/SCIB gold rows, and in 45% of REX. Much of this is 2nd-surplus / quota-share bands that sit outside the three columns, so gold "split" diffs on those rows are partly comparator scope, not cleaner error.
* **Claims PPN.** Gold claims PPN is 0 while amounts are non-zero in 134 AIICO/ARK and 80 AIICO/SCIB rows. Our PPN is arguably better there, so those diffs are gold-side.
* **Blank FROM in gold.** 86 AIICO/ARK rows and 3,588 AXA rows. Where ours has a date, that is gold-side.
* **Exact duplicate rows inside gold.** AIICO/ARK 832, AIICO/SCIB 887, REX 3,899. These inflate our "missing" and "extra" counts slightly.
* **Formats differ between gold cleaners.** Premium PPN is in % while claims PPN is a fraction, NEM has no band SI, and Custodian uses `RETENTION %`. The comparator normalises % to a fraction and only compares fields present in both files.
* **Duplicate gold file.** AIICO/SCIB 2023Q4 had `… (1).xlsx`; it was dropped.

**Cleaner errors confirmed by reading our own output cells**, not relying on the comparator: blank bands (AIICO 2022Q2–2024Q3, NEM, Custodian, REX, HEIRS, LASACO, UNITRUST); percentages in amount columns (LASACO, HEIRS, REX, AXA); cross-quarter reads and paid/OS confusion (source_audit shows the Q4 file inside the Q2 run, and `os claims` tabs typed `paid`); duplicate sheet reads (HEIRS 2025Q3). These dominate the diff counts by orders of magnitude, so the verdict does not depend on the gold quirks.

## validate_upload.py
All **138/138** cleaned workbooks pass: exit 0, 0 per-sheet failures (117 in `report/validate_all.json` plus 21 for HEIRS/LASACO/UNITRUST). It checks structure only. It passes workbooks with 0 premium rows, blank bands and PPN-in-amount cells, so it is **not** a correctness gate.

## API (`/clean`, `/clean/json`; TestClient; `CRE_CLEANER_API_KEY` set only in the test process; LLAMA key unset)
20 of 23 tests pass. The auth, labels, traversal, zip-slip, size-limit, empty and corrupt-zip handling all behave correctly. Temp dirs are cleaned up, and nothing is written to the repo, so `/clean/json` persists nothing.

The 3 failures:
* **An ERROR-level result still returns HTTP 200.** Missing claims, wrong quarter, and PDF without a LlamaParse key all return 200. The errors show only as `exception_count` and zero row counts.
* **Two uploads with the same basename silently overwrite each other.**

Also note: the API calls `run_pipeline` without `convert_pdfs=False`. If `LLAMA_CLOUD_API_KEY` is set on the server, any uploaded PDF, including PDFs inside a zip, is **sent to LlamaParse (an external service)**. That is a privacy/confidentiality risk for cedant data.

## Unit tests
`tests/test_smoke_units.py` prints ALL UNIT TESTS PASSED; `pytest` gives 21 passed. They do not cover the quarter-regex, "OS"-tab, number-format or band-mapping failures above.

## Changes since the 12:10 check (context)
* 15 new unverified adapters and folder aliases.
* Fixed since 12:10:
  * Unlabelled single/two-block band mapping (the swap fix). The edge cases `no_group_row` and `treaty_only` now PASS.
  * `parse_period(datetime)`.
  * `period_from/to_missing` WARN.
  * The duplicate key now includes FROM/TO (`dup_diff_period` PASS).
  * AIICO discovery now accepts QTR1/1QTR, BORD JAN and monthly claims (all PASS).
* **Regression:** the new quarter token logic creates risk 1.
* API changes: `X-API-Key` auth, sanitising, a 50 MB limit, PDF support.
* Pipeline changes: LlamaParse PDF conversion is on by default, and year/quarter are inferred.

## Side effects and notes
* **Mac:** read-only throughout. The only writes on the Mac are the tool harness's own output logs under `/Users/osabobo/agent-tools/`:
  * `13ebb63b-….txt`: base64 of the code tar
  * `c9104f96-997f-4d39-b4cd-1afc36df6faa.txt`: about 23 MB of base64 of the HEIRS/LASACO/UNITRUST raw files
  
  You may want to delete these. Nothing was written in the repo.
* The untracked `.env` was not copied or read.
* No messages were sent and nothing was pushed.

## Appendix A: accuracy per cedant/broker and bordereau

| Cedant/broker | Qtrs | Bordereau | Gold rows | Our rows | Matched (recall) | Missing | Extra | Row-exact vs gold | Cell accuracy (matched rows) | Qtrs with totals+count = gold | Abs money variance / gold total | Top field diffs |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AIICO_ARK | 24 | premium | 8999 | 8780 | 8531 (94.8%) | 468 | 249 | 12.1% | 70.4% | 16/23 | ₦1,357,904,575 / ₦24,701,295,315 (5.5%) | tr_prem 7435, tr_si 7435, tr_ppn 7422, ret_prem 3657 |
| AIICO_ARK | 24 | paid | 3103 | 3650 | 3098 (99.8%) | 5 | 552 | 95.5% | 99.2% | 21/24 | ₦1,474,398,265 / ₦9,316,518,847 (15.8%) | tr_ppn 127, ret_ppn 126, tr_amt 14, fac_amt 5 |
| AIICO_ARK | 24 | outstanding | 20287 | 24181 | 20273 (99.9%) | 14 | 3908 | 52.3% | 95.8% | 11/24 | ₦7,701,698,834 / ₦41,999,984,937 (18.3%) | si 9471, ret_ppn 128, tr_ppn 128, total 127 |
| AIICO_SCIB | 23 | premium | 10878 | 10916 | 10738 (98.7%) | 140 | 178 | 10.4% | 69.0% | 13/22 | ₦342,422,424 / ₦37,806,477,998 (0.9%) | tr_si 9598, tr_ppn 9202, tr_prem 9202, ret_si 5495 |
| AIICO_SCIB | 23 | paid | 2558 | 13658 | 2307 (90.2%) | 251 | 11351 | 81.6% | 99.2% | 0/23 | ₦56,008,905,182 / ₦16,621,323,000 (337.0%) | tr_ppn 82, ret_ppn 77, from 13, to 12 |
| AIICO_SCIB | 23 | outstanding | 13110 | 629 | 629 (4.8%) | 12481 | 0 | 0.0% | 100.0% | 0/23 | ₦61,131,772,492 / ₦65,436,366,786 (93.4%) |  |
| AXA_DIRECT | 8 | premium | 1046 | 0 | 0 (0.0%) | 1046 | 0 | 0.0% | n/a | 0/8 | ₦25,788,923,408 / ₦25,788,923,408 (100.0%) |  |
| AXA_DIRECT | 8 | paid | 484 | 1387 | 480 (99.2%) | 4 | 907 | 0.4% | 67.0% | 1/7 | ₦3,292,443,750 / ₦3,292,443,750 (100.0%) | ret_ppn 473, total 441, ret_amt 409, tr_ppn 279 |
| AXA_DIRECT | 8 | outstanding | 3104 | 2288 | 2151 (69.3%) | 953 | 137 | 0.0% | 66.3% | 1/7 | ₦39,293,560,472 / ₦39,293,560,472 (100.0%) | total 2151, ret_ppn 2140, ret_amt 1224, tr_ppn 1151 |
| CUSTODIAN_SCIB | 24 | premium | 1232 | 1177 | 1177 (95.5%) | 55 | 0 | 0.0% | 89.9% | 23/24 | ₦407,437,614 / ₦4,262,044,553 (9.6%) | ret_prem 1177, fac_prem 298, fac_si 125, ret_ppn 4 |
| CUSTODIAN_SCIB | 24 | paid | 132 | 118 | 118 (89.4%) | 14 | 0 | 0.0% | 81.4% | 21/23 | ₦347,522,581 / ₦900,916,275 (38.6%) | ret_amt 118, ret_ppn 118, tr_ppn 5 |
| NEM_SCIB | 24 | premium | 20626 | 18443 | 18432 (89.4%) | 2194 | 11 | 0.0% | 61.2% | 1/24 | ₦11,872,482,841 / ₦39,480,317,975 (30.1%) | ret_ppn 18431, ret_prem 18425, tr_ppn 18273, tr_prem 18268 |
| NEM_SCIB | 24 | paid | 4253 | 3308 | 3227 (75.9%) | 1026 | 81 | 0.0% | 35.7% | 4/24 | ₦13,424,297,572 / ₦20,176,283,945 (66.5%) | from 3163, to 3162, tr_amt 3115, tr_ppn 3115 |
| NEM_SCIB | 24 | outstanding | 2995 | 2855 | 2855 (95.3%) | 140 | 0 | 0.0% | 33.5% | 10/15 | ₦5,812,633,337 / ₦6,108,116,588 (95.2%) | ret_ppn 2840, total 2838, tr_amt 2834, tr_ppn 2833 |
| ROYAL_EXCHANGE_DIRECT | 8 | premium | 32089 | 28573 | 20735 (64.6%) | 11354 | 7838 | 0.0% | 75.4% | 2/8 | ₦25,345,513,966 / ₦124,950,250,782 (20.3%) | ret_ppn 20735, ret_prem 20688, ret_si 20550, tr_ppn 2602 |
| ROYAL_EXCHANGE_DIRECT | 8 | paid | 2503 | 2207 | 1905 (76.1%) | 598 | 302 | 0.0% | 68.2% | 1/8 | ₦525,021,985 / ₦2,839,359,320 (18.5%) | from 1864, to 1864, ret_amt 863, ret_ppn 862 |
| HEIRS_DIRECT | 4 | premium | 3921 | 2936 | 2936 (74.9%) | 985 | 0 | 0.0% | 42.9% | 2/4 | ₦1,877,307,208 / ₦9,192,170,389 (20.4%) | ret_si 2936, to 2936, tr_ppn 2936, tr_prem 2936 |
| HEIRS_DIRECT | 4 | paid | 374 | 480 | 372 (99.5%) | 2 | 108 | 0.0% | 50.7% | 0/4 | ₦1,995,481,328 / ₦1,995,481,328 (100.0%) | ret_amt 372, ret_ppn 372, to 372, total 372 |
| HEIRS_JOMOLA | 4 | premium | 834 | 945 | 832 (99.8%) | 2 | 113 | 0.0% | 52.2% | 2/4 | ₦227,741,652 / ₦883,334,297 (25.8%) | ret_ppn 832, ret_prem 832, ret_si 832, tr_ppn 832 |
| HEIRS_JOMOLA | 4 | paid | 149 | 146 | 98 (65.8%) | 51 | 48 | 0.0% | 78.8% | 3/4 | ₦9,521,885 / ₦140,951,490 (6.8%) | ret_amt 98, ret_ppn 98, tr_amt 16, tr_ppn 16 |
| LASACO_FEYBIL | 4 | premium | 1494 | 1491 | 1491 (99.8%) | 3 | 0 | 0.0% | 41.5% | 1/4 | ₦316,050 / ₦596,512,765 (0.1%) | from 1491, ret_ppn 1491, ret_prem 1491, tr_ppn 1491 |
| LASACO_FEYBIL | 4 | outstanding | 12 | 15 | 12 (100.0%) | 0 | 3 | 0.0% | 41.7% | 2/4 | ₦2,600,000 / ₦0 (n/a) | ret_amt 12, ret_ppn 12, tr_amt 12, tr_ppn 12 |
| UNITRUST_AGRIC | 8 | premium | 58 | 15 | 15 (25.9%) | 43 | 0 | 0.0% | 57.1% | 4/8 | ₦154,218,549 / ₦241,758,510 (63.8%) | ret_ppn 15, ret_prem 15, ret_si 15, tr_ppn 15 |
| UNITRUST_AGRIC | 8 | paid | 9 | 5 | 5 (55.6%) | 4 | 0 | 0.0% | 38.8% | 4/6 | ₦7,276,962 / ₦23,017,217 (31.6%) | ret_amt 5, ret_ppn 5, tr_amt 5, tr_ppn 5 |
| UNITRUST_AGRIC | 8 | outstanding | 6 | 0 | 0 (0.0%) | 6 | 0 | 0.0% | n/a | 7/1 | ₦0 / ₦0 (n/a) |  |


## Appendix B: edge-case suite (sorted by severity: crash → money-wrong-silently → data-lost-silently → flagged → handled)

40 cases: 28 PASS, 9 FAIL (8 money-wrong-silently, 1 data-lost-silently), 3 FLAGGED (value not recovered, but flagged), 0 crash. Most cases use the AIICO/ARK adapter; combined_workbook uses HEIRS/DIRECT.

| # | Case | Expected | Actual | Result | Severity |
|---|---|---|---|---|---|
| 1 | **bare_class_tabs**: Tabs MARINE, MOTOR, PVT, Casualty, XYZ-UNKNOWN | each mapped to a gold class or flagged (never silently a new/unknown class sheet) | rows=12 classes={'MOTOR': 3, 'TERRORISM': 3, 'CASUALTY': 3, 'XYZ UNKNOWN': 3} sheets=['SUMMARY', 'Fire - CLAIMS', 'Fire - OUTSTANDING', 'Motor - PREMIUM', 'Terrorism & PVT - PREMIUM', 'Casualty - PREMIUM', 'Xyz Unknown - PREMIUM'] exc={'INFO:adapter_status': 1 | FAIL | money-wrong-silently |
| 2 | **cross_quarter_filename**: Q2 run with Q1+Q4 claims files named '1ST QTR 2023 ...' and '4TH Qtr 2023 ...' plus correct '2ND QTR 2023 ...' | only the 2ND QTR claims file used (2 paid, 2 OS) | paid=6 os=6 claims=['CL/Q1/000', 'CL/Q1/001', 'CL/Q2/000', 'CL/Q2/001', 'CL/Q4/000', 'CL/Q4/001'] exc={'INFO:adapter_status': 1} | FAIL | money-wrong-silently |
| 3 | **currency_code**: Premium text 'NGN 1,000.00' | gross parsed as 1000.0 | gross values=[None, None, None] exc={'INFO:adapter_status': 1} | FAIL | money-wrong-silently |
| 4 | **currency_symbol**: Premium text '₦1,234,567.89' | gross parsed as 1234567.89 | gross values=[None, None, None] exc={'INFO:adapter_status': 1} | FAIL | money-wrong-silently |
| 5 | **dup_exact**: Exact duplicate row | kept (both) and flagged apparent_duplicate | rows=9 exc={'INFO:adapter_status': 1} | FAIL | money-wrong-silently |
| 6 | **os_sheet_name**: Outstanding tab named 'fire os claims' (as in AIICO/SCIB) | rows go to OUTSTANDING, class FIRE | paid=4 os=0 classes={'FIRE'} | FAIL | money-wrong-silently |
| 7 | **second_surplus_qs**: Three bands: OWN RETENTION / 1ST SURPLUS / 2ND SURPLUS (quota-share-like) | no band money dropped: ret+treaty(+extra)=gross, or flagged | rows=6 gross=90,000 sum(all band premium cols)=67,500 premium headers=['GROSS PREMIUM', 'RET PREMIUM', 'TREATY PREMIUM', 'FAC PREMIUM'] | FAIL | money-wrong-silently |
| 8 | **trailing_minus**: Premium text '1,234-' (trailing minus) | gross parsed as -1234.0 | gross values=[None, None, None] exc={'INFO:adapter_status': 1} | FAIL | money-wrong-silently |
| 9 | **empty_workbook**: Valid xlsx premium file with an empty sheet only | flagged (no rows) not silent | rows=4 exc={'INFO:adapter_status': 1} | FAIL | data-lost-silently |
| 10 | **excel_serial_dates**: COVER FROM/TO given as Excel serial numbers (44927/45291) in claims; period cell numeric serial in premium | dates converted to 2023-01-01 / 2023-12-31 | claim from/to=[('2023-01-01', '2023-12-31')] prem from=['None', 'None', 'None'] / claims serials OK; premium PERIOD serial -> WARN date_unparseable + period_from/to_missing (left blank) | FLAGGED | flagged-correctly (value not recovered) |
| 11 | **mixed_classes_tab**: One tab 'PREMIUM' with a CLASS column (FIRE / ENGINEERING) | rows split to Fire and Engineering sheets | classes={} exc={'INFO:adapter_status': 1, 'WARN:class_unresolved': 6} | FLAGGED | flagged-correctly (rows kept out of upload; CLASS column not supported) |
| 12 | **relative_raw_dir**: raw_dir passed as a relative path | resolved against CWD or clear error | premium rows=0 exc={'INFO:adapter_status': 1, 'ERROR:no_premium_files': 1, 'ERROR:no_claims_files': 1} (base_dir wins over CWD) | FLAGGED | flagged-correctly (value not recovered) |
| 13 | **baseline**: Clean 3-month premium + quarterly claims | 6 premium rows, ret/treaty split exact, 2 paid, 2 OS | prem=6 paid=2 os=2 sample={'gross': 10000.0, 'ret_prem': 2500.0, 'tr_prem': 7500.0, 'from': datetime.date(2023, 1, 1), 'to': datetime.date(2023, 12, 31)} | PASS | handled |
| 14 | **combined_workbook**: Single workbook 'Q1 2023 Premium and Claims Bordereaux.xlsx' with premium + claims tabs (HEIRS/DIRECT generic adapter) | 6 premium (FIRE premium tab), 2 paid, 2 OS | prem=6 paid=2 os=2 exc={'INFO:adapter_status': 1, 'INFO:premium_input_quarterly': 1} | PASS | handled |
| 15 | **corrupt_file**: Garbage bytes in 'FEB PREMIUM 2023 LOCAL.xlsx' and a truncated real xlsx for claims | flagged, no crash | rows=4 exc={'INFO:adapter_status': 1, 'ERROR:premium_file_unreadable': 1, 'ERROR:claims_parse_failed': 1} | PASS | handled (flagged-correctly) |
| 16 | **dup_diff_period**: Two rows identical except FROM/TO (renewal) | both kept, no duplicate warning | rows=9 exc={'INFO:adapter_status': 1, 'WARN:premium_date_outside_quarter': 3} | PASS | handled (flagged-correctly) |
| 17 | **empty_file**: 0-byte 'FEB PREMIUM 2023 LOCAL.xlsx' | flagged unreadable, others processed | rows=4 exc={'INFO:adapter_status': 1, 'ERROR:premium_file_unreadable': 1} | PASS | handled (flagged-correctly) |
| 18 | **fname_1ST_QTR**: Claims filename '1ST QTR 2023 CLAIMS BORD.xlsx' | claims file '1ST QTR 2023 CLAIMS BORD.xlsx' discovered for Q1 | paid=2 os=2 exc={'INFO:adapter_status': 1} | PASS | handled |
| 19 | **fname_BORD_JAN**: Premium files named 'BORD JAN 2023.xlsx' etc. | 3 monthly premium files discovered | prem rows=6 exc={'INFO:adapter_status': 1} | PASS | handled |
| 20 | **fname_Q1**: Claims filename 'Q1 2023 Loss Bordereaux.xlsx' | claims file 'Q1 2023 Loss Bordereaux.xlsx' discovered for Q1 | paid=2 os=2 exc={'INFO:adapter_status': 1} | PASS | handled |
| 21 | **fname_QTR1**: Claims filename 'QTR1 2023 CLAIMS.xlsx' | claims file 'QTR1 2023 CLAIMS.xlsx' discovered for Q1 | paid=2 os=2 exc={'INFO:adapter_status': 1} | PASS | handled |
| 22 | **header_row_6**: Five title rows above the header | header detected, 6 rows | rows=6 | PASS | handled |
| 23 | **hidden_column**: Hidden column (gross PREMIUM column hidden) | values still read (hidden columns are data) | rows=6 gross=[10000.0, 20000.0, 10000.0] exc={'INFO:adapter_status': 1, 'INFO:hidden_columns_present': 3} | PASS | handled |
| 24 | **hidden_rows**: One hidden data row in premium sheet | hidden row either kept or skipped WITH a flag (never silent) | rows=3 exc={'INFO:adapter_status': 1, 'WARN:hidden_row_skipped': 3} | PASS | handled (flagged-correctly) |
| 25 | **hidden_sheet**: Hidden premium sheet ENGINEERING with real rows | skipped and flagged (or kept) | rows=6 exc={'INFO:adapter_status': 1, 'WARN:hidden_sheet_skipped': 3} | PASS | handled (flagged-correctly) |
| 26 | **large_file**: 3 monthly files x 20,000 premium rows (60k rows) | completes < 120 s, 60,000 rows | rows=60000 secs=31.3 | PASS | handled |
| 27 | **leading_zeros**: Policy '000123' and claim '000456' stored as text | leading zeros preserved | id cells found=["'000123'", "'000223'", "'000323'", "'000456'"] | PASS | handled |
| 28 | **merged_group_header**: Group row labels merged across band columns (G1:I1, J1:L1) | bands mapped correctly | sample={'gross': 10000.0, 'ret_prem': 2500.0, 'tr_prem': 7500.0} | PASS | handled |
| 29 | **missing_claims**: No claims file | ERROR no_claims_files | exc={'INFO:adapter_status': 1, 'ERROR:no_claims_files': 1} | PASS | handled (flagged-correctly) |
| 30 | **missing_month**: FEB premium file absent | WARN/ERROR missing month | rows=4 exc={'INFO:adapter_status': 1, 'ERROR:premium_month_missing': 1} | PASS | handled (flagged-correctly) |
| 31 | **monthly_claims**: Three monthly claims files 'JAN 2023 CLAIMS.xlsx'.. (paid only) instead of quarterly | 6 paid rows | paid=6 exc={'INFO:adapter_status': 1} | PASS | handled |
| 32 | **no_group_row**: Two unlabelled PPN/SI/Premium blocks (no OWN RETENTION / TREATY group row) | first block->retention, second->treaty | rows=6 sample={'gross': 10000.0, 'ret_prem': 2500.0, 'tr_prem': 7500.0} | PASS | handled |
| 33 | **paren_negative**: Premium text '(1,234.50)' | gross parsed as -1234.5 | gross values=[-1234.5, -1234.5, -1234.5] exc={'INFO:adapter_status': 1} | PASS | handled |
| 34 | **period_blank**: Blank period cell | row kept, period_from/to_missing flagged | rows=6 exc={'INFO:adapter_status': 1, 'WARN:period_from_missing': 3, 'WARN:period_to_missing': 3} | PASS | handled (flagged-correctly) |
| 35 | **period_single_date**: Period cell holds a single date '01/02/2023' | FROM=2023-02-01, TO blank+flagged or derived | from/to=[('2023-01-01', 'None'), ('2023-02-01', 'None'), ('2023-03-01', 'None')] exc={'INFO:adapter_status': 1, 'WARN:period_to_missing': 3} | PASS | handled (flagged-correctly) |
| 36 | **period_unparseable**: Period 'TBC / N/A' | row kept, date_unparseable flagged | rows=6 exc={'INFO:adapter_status': 1, 'WARN:date_unparseable': 3, 'WARN:period_from_missing': 3, 'WARN:period_to_missing': 3} | PASS | handled (flagged-correctly) |
| 37 | **subtotal_rows**: SUB TOTAL / GRAND TOTAL / NIL / TOTAL rows in data | total rows excluded, gross = sum of detail rows | rows=6 gross=90,000 (expected 90,000) insureds=[] | PASS | handled (flagged-correctly) |
| 38 | **treaty_only**: Premium sheet with only a TREATY band (no retention columns) | treaty premium = source; retention blank | rows=6 sample={'gross': 10000.0, 'ret_ppn': None, 'ret_prem': None, 'tr_ppn': 75.0, 'tr_prem': 7500.0} | PASS | handled |
| 39 | **unicode_whitespace**: Insured 'Ọlá  Ẹniọlá\u00a0Ltd ' and policy ' P/01/000 ' with NBSP/spaces | insured unicode preserved & trimmed; policy trimmed | insured cells=['Ọlá Ẹniọlá Ltd', 'Ọlá Ẹniọlá Ltd', 'Ọlá Ẹniọlá Ltd'] | PASS | handled |
| 40 | **usd_ngn_mixed**: Same file: FIRE (NGN) and 'FIRE USD' tab with USD amounts | USD kept separate / labelled or flagged; not summed into NGN | outputs=['AIICO_ARK_2023_Q1_NGN_cleaned.xlsx', 'AIICO_ARK_2023_Q1_USD_cleaned.xlsx'] premium rows in NGN book=6 exc={'INFO:adapter_status': 1, 'INFO:premium_date_outside_month': 2, 'INFO:currency_split_workbooks': 1} | PASS | handled |

## Appendix C: API tests

| Test | Expected | Status | Result | Body (trimmed) |
|---|---|---|---|---|
| json_happy_multi_xlsx | 200, 6/2/2 rows | 200 | PASS | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":1,"period_inferred":false,"outputs":[{"currency" |
| clean_happy_zip | 200 zip with cleaned + manifest | 200 | PASS | <application/zip 20568 bytes> |
| json_period_inferred | 200 with year=2023 quarter=1 inferred | 200 | PASS | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":1,"period_inferred":true,"outputs":[{"currency": |
| no_key | 401 | 401 | PASS | {"detail":"Invalid or missing X-API-Key"} |
| wrong_key | 401 | 401 | PASS | {"detail":"Invalid or missing X-API-Key"} |
| key_unset | 503 | 503 | PASS | {"detail":"API key not configured; set CRE_CLEANER_API_KEY before accepting uploads"} |
| traversal_filename | 400 or basename only (no write outside tmp) | 200 | PASS | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":1,"period_inferred":false,"outputs":[{"currency" |
| bad_cedant_label | 400 | 400 | PASS | {"detail":"Invalid cedant: use up to 64 letters, digits, spaces, _ . - (got '../AIICO')"} |
| unknown_cedant | 400 unsupported | 400 | PASS | {"detail":"No adapter for cedant='FOO' broker='ARK'. Registered: AIICO/ARK, AIICO/SCIB, AIICO/AGRIC DIRECT, AXA/DIRECT, CHI/SCIB, CUSTODIAN/ |
| quarter_5 | 422 | 422 | PASS | {"detail":[{"type":"less_than_equal","loc":["body","quarter"],"msg":"Input should be less than or equal to 4","input":"5","ctx":{"le":4}}]} |
| unsupported_type | 400 | 400 | PASS | {"detail":"Unsupported upload type '.exe'; use .xlsx, .xls, .pdf or .zip"} |
| empty_file | 400 Empty upload | 400 | PASS | {"detail":"Empty upload"} |
| corrupt_zip | 400 | 400 | PASS | {"detail":"Upload is not a valid zip"} |
| zip_slip | 400 Unsafe zip entry | 400 | PASS | {"detail":"Unsafe zip entry: ../../evil.txt"} |
| zip_abs_path | 400 | 400 | PASS | {"detail":"Unsafe zip entry: /abs/evil.txt"} |
| oversize | 413 | 413 | PASS | {"detail":"Upload exceeds size limit of 1048576 bytes (set CRE_CLEANER_MAX_UPLOAD_MB to raise)"} |
| pdf_without_llama_key | clear 4xx/flag, no crash, nothing sent | 200 | PASS | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":1,"period_inferred":false,"outputs":[{"currency" |
| multi_with_zip | 400 | 400 | PASS | {"detail":"When uploading multiple files, send Excel/PDF only (not zip)"} |
| duplicate_basename_multi | 400 or both processed; not silent overwrite | 200 | FAIL | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":1,"period_inferred":false,"outputs":[{"currency" |
| missing_claims_status | non-200 or error surfaced in body (exception_count only?) | 200 | FAIL | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":1,"period_inferred":false,"outputs":[{"currency" |
| wrong_quarter_files | non-200 or errors surfaced | 200 | FAIL | {"provisional":true,"persisted":false,"cedant":"AIICO","broker":"ARK","year":2023,"quarter":3,"period_inferred":false,"outputs":[{"currency" |
| temp_cleanup | no cre_api* dirs left in /tmp | None | PASS | [] |
| no_repo_writes | no files written into code tree | None | PASS | [] |

## Appendix D: per-quarter accuracy

| Cedant | Qtr | Bordereau | Gold | Ours | Recall | Row-exact | Cell acc | Net variance | Gold total |
|---|---|---|---|---|---|---|---|---|---|
| AIICO_ARK | 2020Q1 | outstanding | 585 | 585 | 100.0% | 100.0% | 100.0% | ₦0 | ₦434,090,052 |
| AIICO_ARK | 2020Q1 | paid | 110 | 110 | 100.0% | 100.0% | 100.0% | ₦0 | ₦143,730,375 |
| AIICO_ARK | 2020Q2 | outstanding | 558 | 1143 | 100.0% | 99.8% | 100.0% | ₦434,090,052 | ₦374,156,905 |
| AIICO_ARK | 2020Q2 | paid | 90 | 200 | 100.0% | 100.0% | 100.0% | ₦143,730,375 | ₦104,622,309 |
| AIICO_ARK | 2020Q2 | premium | 510 | 510 | 100.0% | 0.2% | 78.6% | ₦0 | ₦594,788,818 |
| AIICO_ARK | 2020Q3 | outstanding | 670 | 670 | 100.0% | 100.0% | 100.0% | ₦0 | ₦700,564,258 |
| AIICO_ARK | 2020Q3 | paid | 177 | 177 | 100.0% | 97.7% | 99.6% | ₦0 | ₦203,445,142 |
| AIICO_ARK | 2020Q3 | premium | 512 | 512 | 100.0% | 3.7% | 77.1% | ₦0 | ₦886,825,490 |
| AIICO_ARK | 2020Q4 | outstanding | 737 | 737 | 100.0% | 82.8% | 95.3% | ₦-119,935,807 | ₦637,646,617 |
| AIICO_ARK | 2020Q4 | paid | 175 | 175 | 100.0% | 100.0% | 100.0% | ₦0 | ₦288,681,602 |
| AIICO_ARK | 2020Q4 | premium | 401 | 401 | 100.0% | 3.0% | 79.0% | ₦0 | ₦654,343,081 |
| AIICO_ARK | 2021Q1 | outstanding | 820 | 820 | 100.0% | 100.0% | 100.0% | ₦0 | ₦1,078,331,159 |
| AIICO_ARK | 2021Q1 | paid | 170 | 170 | 100.0% | 100.0% | 100.0% | ₦0 | ₦407,769,200 |
| AIICO_ARK | 2021Q1 | premium | 621 | 619 | 99.7% | 0.8% | 78.8% | ₦-32,824,681 | ₦1,242,085,274 |
| AIICO_ARK | 2021Q2 | outstanding | 906 | 1763 | 100.0% | 100.0% | 100.0% | ₦1,701,258,932 | ₦2,684,686,751 |
| AIICO_ARK | 2021Q2 | paid | 137 | 285 | 96.4% | 89.1% | 98.6% | ₦212,976,958 | ₦1,002,580,385 |
| AIICO_ARK | 2021Q2 | premium | 434 | 434 | 100.0% | 1.6% | 78.9% | ₦0 | ₦844,329,227 |
| AIICO_ARK | 2021Q3 | outstanding | 774 | 774 | 100.0% | 100.0% | 100.0% | ₦0 | ₦2,346,897,361 |
| AIICO_ARK | 2021Q3 | paid | 183 | 183 | 100.0% | 92.9% | 98.7% | ₦0 | ₦316,692,625 |
| AIICO_ARK | 2021Q3 | premium | 402 | 402 | 100.0% | 2.0% | 72.7% | ₦0 | ₦974,950,271 |
| AIICO_ARK | 2021Q4 | outstanding | 858 | 857 | 99.9% | 0.0% | 91.7% | ₦-2,000,000 | ₦1,703,258,932 |
| AIICO_ARK | 2021Q4 | paid | 153 | 153 | 100.0% | 96.1% | 99.3% | ₦0 | ₦217,155,297 |
| AIICO_ARK | 2021Q4 | premium | 389 | 388 | 99.7% | 3.6% | 79.3% | ₦-13,990,259 | ₦493,177,963 |
| AIICO_ARK | 2022Q1 | outstanding | 925 | 922 | 99.7% | 0.0% | 91.7% | ₦-8,000,000 | ₦1,723,170,847 |
| AIICO_ARK | 2022Q1 | paid | 113 | 113 | 100.0% | 90.3% | 98.3% | ₦0 | ₦168,120,538 |
| AIICO_ARK | 2022Q1 | premium | 582 | 582 | 100.0% | 0.9% | 78.8% | ₦0 | ₦1,443,081,820 |
| AIICO_ARK | 2022Q2 | outstanding | 984 | 982 | 99.8% | 0.0% | 91.7% | ₦-6,000,000 | ₦2,447,941,127 |
| AIICO_ARK | 2022Q2 | paid | 150 | 150 | 100.0% | 96.0% | 99.3% | ₦0 | ₦300,765,661 |
| AIICO_ARK | 2022Q2 | premium | 450 | 450 | 100.0% | 0.0% | 56.1% | ₦0 | ₦626,417,181 |
| AIICO_ARK | 2022Q3 | outstanding | 1001 | 1000 | 99.9% | 0.0% | 91.7% | ₦-2,000,000 | ₦2,247,576,285 |
| AIICO_ARK | 2022Q3 | paid | 188 | 188 | 100.0% | 96.8% | 99.5% | ₦0 | ₦278,208,842 |
| AIICO_ARK | 2022Q3 | premium | 368 | 368 | 100.0% | 0.0% | 54.9% | ₦0 | ₦1,354,647,096 |
| AIICO_ARK | 2022Q4 | outstanding | 1002 | 1001 | 99.9% | 0.0% | 91.7% | ₦-150,000 | ₦1,627,276,031 |
| AIICO_ARK | 2022Q4 | paid | 188 | 188 | 100.0% | 94.7% | 99.1% | ₦0 | ₦181,683,325 |
| AIICO_ARK | 2022Q4 | premium | 287 | 253 | 82.9% | 0.0% | 57.1% | ₦-45,751,071 | ₦535,563,465 |
| AIICO_ARK | 2023Q1 | outstanding | 952 | 951 | 99.9% | 0.0% | 91.7% | ₦-150,000 | ₦1,201,402,076 |
| AIICO_ARK | 2023Q1 | paid | 125 | 125 | 100.0% | 87.2% | 97.9% | ₦0 | ₦1,118,764,061 |
| AIICO_ARK | 2023Q1 | premium | 408 | 408 | 100.0% | 0.0% | 42.9% | ₦0 | ₦1,197,544,222 |
| AIICO_ARK | 2023Q2 | outstanding | 885 | 883 | 99.8% | 0.0% | 91.7% | ₦-13,020,000 | ₦1,156,598,682 |
| AIICO_ARK | 2023Q2 | paid | 129 | 129 | 100.0% | 95.3% | 99.2% | ₦0 | ₦211,632,046 |
| AIICO_ARK | 2023Q2 | premium | 214 | 213 | 99.5% | 0.0% | 42.7% | ₦-15,958,683 | ₦1,184,557,070 |
| AIICO_ARK | 2023Q3 | outstanding | 853 | 851 | 99.8% | 0.0% | 91.7% | ₦-13,020,000 | ₦3,235,734,512 |
| AIICO_ARK | 2023Q3 | paid | 123 | 123 | 100.0% | 95.9% | 99.3% | ₦0 | ₦201,951,766 |
| AIICO_ARK | 2023Q3 | premium | 261 | 261 | 100.0% | 0.0% | 55.5% | ₦0 | ₦646,781,844 |
| AIICO_ARK | 2023Q4 | outstanding | 842 | 841 | 99.9% | 0.0% | 91.7% | ₦-12,870,000 | ₦2,283,841,649 |
| AIICO_ARK | 2023Q4 | paid | 96 | 96 | 100.0% | 96.9% | 99.5% | ₦0 | ₦217,623,733 |
| AIICO_ARK | 2023Q4 | premium | 297 | 297 | 100.0% | 0.0% | 55.5% | ₦0 | ₦744,300,334 |
| AIICO_ARK | 2024Q1 | outstanding | 881 | 881 | 100.0% | 0.0% | 90.3% | ₦0 | ₦2,158,630,404 |
| AIICO_ARK | 2024Q1 | paid | 75 | 75 | 100.0% | 96.0% | 99.3% | ₦0 | ₦343,585,047 |
| AIICO_ARK | 2024Q1 | premium | 368 | 190 | 51.6% | 0.0% | 55.1% | ₦-692,905,866 | ₦1,447,988,530 |
| AIICO_ARK | 2024Q2 | outstanding | 879 | 879 | 100.0% | 99.8% | 100.0% | ₦0 | ₦1,887,494,015 |
| AIICO_ARK | 2024Q2 | paid | 82 | 82 | 100.0% | 85.4% | 97.6% | ₦0 | ₦1,424,028,773 |
| AIICO_ARK | 2024Q2 | premium | 337 | 337 | 100.0% | 0.0% | 55.3% | ₦0 | ₦823,122,017 |
| AIICO_ARK | 2024Q3 | outstanding | 881 | 881 | 100.0% | 100.0% | 100.0% | ₦0 | ₦2,899,748,587 |
| AIICO_ARK | 2024Q3 | paid | 110 | 110 | 100.0% | 91.8% | 98.6% | ₦0 | ₦286,457,660 |
| AIICO_ARK | 2024Q3 | premium | 374 | 374 | 38.8% | 0.0% | 58.4% | ₦0 | ₦2,033,318,870 |
| AIICO_ARK | 2024Q4 | outstanding | 905 | 905 | 100.0% | 91.6% | 99.3% | ₦0 | ₦2,497,955,416 |
| AIICO_ARK | 2024Q4 | paid | 131 | 131 | 100.0% | 95.4% | 98.6% | ₦0 | ₦270,136,111 |
| AIICO_ARK | 2024Q4 | premium | 369 | 366 | 98.4% | 60.7% | 90.5% | ₦42,782,017 | ₦1,572,738,663 |
| AIICO_ARK | 2025Q1 | outstanding | 914 | 914 | 100.0% | 91.6% | 99.3% | ₦0 | ₦1,747,486,280 |
| AIICO_ARK | 2025Q1 | paid | 107 | 107 | 100.0% | 100.0% | 100.0% | ₦0 | ₦397,146,034 |
| AIICO_ARK | 2025Q1 | premium | 440 | 439 | 99.5% | 99.1% | 100.0% | ₦-2,686,389 | ₦1,926,414,768 |
| AIICO_ARK | 2025Q2 | outstanding | 923 | 3389 | 100.0% | 91.2% | 99.3% | ₦5,389,204,043 | ₦1,283,779,228 |
| AIICO_ARK | 2025Q2 | paid | 109 | 398 | 100.0% | 91.7% | 99.2% | ₦1,117,690,931 | ₦511,193,418 |
| AIICO_ARK | 2025Q2 | premium | 354 | 354 | 100.0% | 100.0% | 100.0% | ₦0 | ₦1,288,277,236 |
| AIICO_ARK | 2025Q3 | outstanding | 940 | 940 | 100.0% | 92.0% | 99.3% | ₦0 | ₦1,539,800,349 |
| AIICO_ARK | 2025Q3 | paid | 65 | 65 | 100.0% | 100.0% | 100.0% | ₦0 | ₦97,581,190 |
| AIICO_ARK | 2025Q3 | premium | 300 | 300 | 100.0% | 0.0% | 55.2% | ₦-0 | ₦1,172,160,860 |
| AIICO_ARK | 2025Q4 | outstanding | 612 | 612 | 100.0% | 90.4% | 99.2% | ₦0 | ₦2,101,917,414 |
| AIICO_ARK | 2025Q4 | paid | 117 | 117 | 100.0% | 100.0% | 100.0% | ₦0 | ₦622,963,708 |
| AIICO_ARK | 2025Q4 | premium | 321 | 322 | 100.0% | 0.0% | 54.8% | ₦511,005,609 | ₦1,013,881,214 |
| AIICO_SCIB | 2020Q1 | outstanding | 410 | 16 | 3.9% | 0.0% | 100.0% | ₦-2,469,476,547 | ₦2,652,189,506 |
| AIICO_SCIB | 2020Q1 | paid | 124 | 517 | 99.2% | 30.6% | 88.7% | ₦2,468,521,712 | ₦777,756,454 |
| AIICO_SCIB | 2020Q2 | outstanding | 439 | 18 | 4.1% | 0.0% | 100.0% | ₦-2,939,666,533 | ₦3,147,303,293 |
| AIICO_SCIB | 2020Q2 | paid | 70 | 491 | 100.0% | 95.7% | 100.0% | ₦2,939,666,533 | ₦200,380,351 |
| AIICO_SCIB | 2020Q2 | premium | 524 | 524 | 100.0% | 0.0% | 77.0% | ₦0 | ₦837,214,655 |
| AIICO_SCIB | 2020Q3 | outstanding | 469 | 29 | 6.2% | 0.0% | 100.0% | ₦-4,112,473,502 | ₦4,918,461,519 |
| AIICO_SCIB | 2020Q3 | paid | 160 | 599 | 99.4% | 96.2% | 100.0% | ₦4,112,359,294 | ₦443,406,599 |
| AIICO_SCIB | 2020Q3 | premium | 602 | 601 | 99.8% | 0.0% | 77.5% | ₦-277,200 | ₦1,188,967,715 |
| AIICO_SCIB | 2020Q4 | outstanding | 499 | 35 | 7.0% | 0.0% | 100.0% | ₦-2,058,178,551 | ₦2,308,400,532 |
| AIICO_SCIB | 2020Q4 | paid | 137 | 703 | 100.0% | 94.9% | 99.2% | ₦5,468,032,589 | ₦3,535,045,283 |
| AIICO_SCIB | 2020Q4 | premium | 294 | 405 | 99.3% | 0.0% | 78.6% | ₦248,752,057 | ₦445,746,085 |
| AIICO_SCIB | 2021Q1 | outstanding | 567 | 37 | 6.5% | 0.0% | 100.0% | ₦-2,255,283,783 | ₦2,498,339,311 |
| AIICO_SCIB | 2021Q1 | paid | 156 | 686 | 100.0% | 96.2% | 100.0% | ₦2,255,283,783 | ₦530,416,487 |
| AIICO_SCIB | 2021Q1 | premium | 676 | 674 | 99.7% | 0.0% | 87.0% | ₦-1,334,400 | ₦1,416,771,055 |
| AIICO_SCIB | 2021Q2 | outstanding | 553 | 30 | 5.4% | 0.0% | 100.0% | ₦-3,490,927,970 | ₦3,596,508,752 |
| AIICO_SCIB | 2021Q2 | paid | 125 | 648 | 100.0% | 89.6% | 100.0% | ₦3,490,927,970 | ₦555,077,322 |
| AIICO_SCIB | 2021Q2 | premium | 554 | 553 | 99.8% | 0.0% | 78.6% | ₦-2,000,000 | ₦1,180,401,900 |
| AIICO_SCIB | 2021Q3 | outstanding | 549 | 27 | 4.9% | 0.0% | 100.0% | ₦-2,529,511,160 | ₦2,598,380,478 |
| AIICO_SCIB | 2021Q3 | paid | 117 | 639 | 100.0% | 96.6% | 100.0% | ₦2,529,511,160 | ₦1,318,609,047 |
| AIICO_SCIB | 2021Q3 | premium | 565 | 564 | 99.8% | 0.0% | 70.9% | ₦-1,000,000 | ₦1,499,691,156 |
| AIICO_SCIB | 2021Q4 | outstanding | 565 | 24 | 4.2% | 0.0% | 100.0% | ₦-2,150,888,073 | ₦2,210,486,114 |
| AIICO_SCIB | 2021Q4 | paid | 120 | 661 | 100.0% | 97.5% | 100.0% | ₦2,150,888,073 | ₦288,954,240 |
| AIICO_SCIB | 2021Q4 | premium | 450 | 449 | 99.8% | 0.0% | 78.6% | ₦-975,662 | ₦721,463,899 |
| AIICO_SCIB | 2022Q1 | outstanding | 324 | 25 | 7.7% | 0.0% | 100.0% | ₦-904,875,314 | ₦1,295,628,683 |
| AIICO_SCIB | 2022Q1 | paid | 104 | 402 | 100.0% | 94.2% | 100.0% | ₦904,755,747 | ₦248,228,836 |
| AIICO_SCIB | 2022Q1 | premium | 613 | 613 | 100.0% | 0.0% | 78.6% | ₦0 | ₦1,518,053,175 |
| AIICO_SCIB | 2022Q2 | outstanding | 602 | 29 | 4.8% | 0.0% | 100.0% | ₦-1,714,810,085 | ₦2,122,894,630 |
| AIICO_SCIB | 2022Q2 | paid | 128 | 701 | 100.0% | 94.5% | 100.0% | ₦1,714,810,085 | ₦783,624,941 |
| AIICO_SCIB | 2022Q2 | premium | 567 | 567 | 100.0% | 0.0% | 53.5% | ₦0 | ₦1,433,227,080 |
| AIICO_SCIB | 2022Q3 | outstanding | 621 | 28 | 4.5% | 0.0% | 100.0% | ₦-1,899,523,951 | ₦2,296,915,462 |
| AIICO_SCIB | 2022Q3 | paid | 125 | 718 | 100.0% | 96.8% | 100.0% | ₦1,899,523,951 | ₦234,759,201 |
| AIICO_SCIB | 2022Q3 | premium | 402 | 402 | 100.0% | 0.0% | 54.7% | ₦0 | ₦1,642,075,224 |
| AIICO_SCIB | 2022Q4 | outstanding | 611 | 32 | 5.2% | 0.0% | 100.0% | ₦-1,498,675,217 | ₦1,577,506,832 |
| AIICO_SCIB | 2022Q4 | paid | 110 | 689 | 100.0% | 94.5% | 100.0% | ₦1,498,675,217 | ₦679,850,870 |
| AIICO_SCIB | 2022Q4 | premium | 314 | 314 | 100.0% | 0.0% | 57.1% | ₦0 | ₦612,084,446 |
| AIICO_SCIB | 2023Q1 | outstanding | 618 | 33 | 5.3% | 0.0% | 100.0% | ₦-2,682,210,525 | ₦2,769,077,384 |
| AIICO_SCIB | 2023Q1 | paid | 76 | 660 | 100.0% | 92.1% | 99.9% | ₦2,682,090,958 | ₦491,164,337 |
| AIICO_SCIB | 2023Q1 | premium | 501 | 500 | 99.6% | 0.0% | 57.1% | ₦20,642,775 | ₦2,222,695,399 |
| AIICO_SCIB | 2023Q2 | outstanding | 612 | 28 | 4.6% | 0.0% | 100.0% | ₦-2,598,035,608 | ₦2,667,180,818 |
| AIICO_SCIB | 2023Q2 | paid | 103 | 675 | 89.3% | 80.6% | 99.7% | ₦2,347,786,344 | ₦486,058,640 |
| AIICO_SCIB | 2023Q2 | premium | 375 | 311 | 82.9% | 0.0% | 42.9% | ₦-62,813,374 | ₦1,281,816,138 |
| AIICO_SCIB | 2023Q3 | outstanding | 620 | 0 | 0.0% | 0.0% | n/a | ₦-4,512,382,501 | ₦4,512,382,501 |
| AIICO_SCIB | 2023Q3 | paid | 98 | 0 | 0.0% | 0.0% | n/a | ₦-445,757,294 | ₦445,757,294 |
| AIICO_SCIB | 2023Q3 | premium | 465 | 465 | 100.0% | 0.0% | 52.9% | ₦0 | ₦1,540,142,737 |
| AIICO_SCIB | 2023Q4 | outstanding | 606 | 0 | 0.0% | 0.0% | n/a | ₦-3,134,026,235 | ₦3,134,026,235 |
| AIICO_SCIB | 2023Q4 | paid | 128 | 0 | 0.0% | 0.0% | n/a | ₦-313,304,129 | ₦313,304,129 |
| AIICO_SCIB | 2023Q4 | premium | 396 | 396 | 100.0% | 0.0% | 56.3% | ₦0 | ₦1,009,074,178 |
| AIICO_SCIB | 2024Q1 | outstanding | 614 | 32 | 5.2% | 0.0% | 100.0% | ₦-3,099,606,583 | ₦3,178,471,402 |
| AIICO_SCIB | 2024Q1 | paid | 80 | 661 | 100.0% | 71.2% | 97.2% | ₦3,099,487,016 | ₦678,062,684 |
| AIICO_SCIB | 2024Q1 | premium | 472 | 471 | 99.8% | 0.0% | 56.5% | ₦-2,313,478 | ₦2,255,138,477 |
| AIICO_SCIB | 2024Q2 | outstanding | 664 | 35 | 5.3% | 0.0% | 100.0% | ₦-2,408,918,084 | ₦2,497,619,798 |
| AIICO_SCIB | 2024Q2 | paid | 82 | 697 | 85.4% | 82.9% | 99.6% | ₦1,015,221,057 | ₦1,554,839,355 |
| AIICO_SCIB | 2024Q2 | premium | 516 | 516 | 100.0% | 0.0% | 53.0% | ₦0 | ₦1,364,377,673 |
| AIICO_SCIB | 2024Q3 | outstanding | 665 | 34 | 5.1% | 0.0% | 100.0% | ₦-3,528,134,007 | ₦3,903,942,114 |
| AIICO_SCIB | 2024Q3 | paid | 129 | 760 | 100.0% | 92.2% | 100.0% | ₦3,528,134,007 | ₦357,785,543 |
| AIICO_SCIB | 2024Q3 | premium | 461 | 461 | 86.1% | 0.0% | 66.0% | ₦0 | ₦2,713,474,439 |
| AIICO_SCIB | 2025Q1 | outstanding | 660 | 36 | 5.5% | 0.0% | 100.0% | ₦-3,178,721,985 | ₦3,296,794,614 |
| AIICO_SCIB | 2025Q1 | paid | 96 | 720 | 100.0% | 95.8% | 100.0% | ₦3,178,721,985 | ₦592,371,111 |
| AIICO_SCIB | 2025Q1 | premium | 601 | 600 | 99.8% | 99.7% | 100.0% | ₦-2,313,478 | ₦4,236,859,106 |
| AIICO_SCIB | 2025Q2 | outstanding | 669 | 36 | 5.4% | 0.0% | 100.0% | ₦-2,849,815,373 | ₦2,993,104,174 |
| AIICO_SCIB | 2025Q2 | paid | 104 | 737 | 100.0% | 93.3% | 100.0% | ₦2,849,815,373 | ₦884,957,046 |
| AIICO_SCIB | 2025Q2 | premium | 536 | 536 | 100.0% | 100.0% | 100.0% | ₦0 | ₦2,204,399,374 |
| AIICO_SCIB | 2025Q3 | outstanding | 662 | 34 | 5.1% | 0.0% | 100.0% | ₦-1,990,073,287 | ₦2,061,545,168 |
| AIICO_SCIB | 2025Q3 | paid | 66 | 694 | 100.0% | 92.4% | 100.0% | ₦1,990,073,287 | ₦791,764,527 |
| AIICO_SCIB | 2025Q3 | premium | 473 | 473 | 100.0% | 0.0% | 53.0% | ₦-0 | ₦3,703,898,878 |
| AIICO_SCIB | 2025Q4 | outstanding | 511 | 31 | 6.1% | 0.0% | 100.0% | ₦-3,125,557,618 | ₦3,199,207,466 |
| AIICO_SCIB | 2025Q4 | paid | 120 | 600 | 100.0% | 96.7% | 100.0% | ₦3,125,557,618 | ₦429,148,703 |
| AIICO_SCIB | 2025Q4 | premium | 521 | 521 | 100.0% | 0.0% | 52.8% | ₦0 | ₦2,778,905,210 |
| AXA_DIRECT | 2024Q1 | outstanding | 471 | 0 | 0.0% | 0.0% | n/a | ₦-2,133,718,069 | ₦2,133,718,069 |
| AXA_DIRECT | 2024Q1 | paid | 37 | 493 | 100.0% | 0.0% | 71.7% | ₦0 | ₦0 |
| AXA_DIRECT | 2024Q1 | premium | 173 | 0 | 0.0% | 0.0% | n/a | ₦-3,718,475,785 | ₦3,718,475,785 |
| AXA_DIRECT | 2024Q2 | outstanding | 461 | 0 | 0.0% | 0.0% | n/a | ₦-2,362,017,714 | ₦2,362,017,714 |
| AXA_DIRECT | 2024Q2 | paid | 32 | 481 | 93.8% | 0.0% | 57.0% | ₦-594,437,210 | ₦594,437,210 |
| AXA_DIRECT | 2024Q2 | premium | 96 | 0 | 0.0% | 0.0% | n/a | ₦-1,092,337,062 | ₦1,092,337,062 |
| AXA_DIRECT | 2024Q3 | paid | 151 | 150 | 99.3% | 1.3% | 68.6% | ₦-770,295,700 | ₦770,295,700 |
| AXA_DIRECT | 2024Q3 | premium | 134 | 0 | 0.0% | 0.0% | n/a | ₦-3,144,135,022 | ₦3,144,135,022 |
| AXA_DIRECT | 2024Q4 | outstanding | 229 | 226 | 98.7% | 0.0% | 63.2% | ₦-3,406,551,798 | ₦3,406,551,798 |
| AXA_DIRECT | 2024Q4 | premium | 124 | 0 | 0.0% | 0.0% | n/a | ₦-2,786,501,552 | ₦2,786,501,552 |
| AXA_DIRECT | 2025Q1 | outstanding | 341 | 339 | 99.4% | 0.0% | 64.3% | ₦-5,944,032,071 | ₦5,944,032,071 |
| AXA_DIRECT | 2025Q1 | paid | 130 | 129 | 99.2% | 0.0% | 70.5% | ₦-806,247,011 | ₦806,247,011 |
| AXA_DIRECT | 2025Q1 | premium | 143 | 0 | 0.0% | 0.0% | n/a | ₦-6,325,136,788 | ₦6,325,136,788 |
| AXA_DIRECT | 2025Q2 | outstanding | 341 | 336 | 98.5% | 0.0% | 64.8% | ₦-8,335,407,442 | ₦8,335,407,442 |
| AXA_DIRECT | 2025Q2 | paid | 19 | 19 | 100.0% | 0.0% | 60.8% | ₦-65,456,568 | ₦65,456,568 |
| AXA_DIRECT | 2025Q2 | premium | 144 | 0 | 0.0% | 0.0% | n/a | ₦-2,930,297,110 | ₦2,930,297,110 |
| AXA_DIRECT | 2025Q3 | outstanding | 335 | 332 | 99.1% | 0.0% | 65.1% | ₦-7,318,403,514 | ₦7,318,403,514 |
| AXA_DIRECT | 2025Q3 | paid | 47 | 47 | 100.0% | 0.0% | 62.5% | ₦-638,889,839 | ₦638,889,839 |
| AXA_DIRECT | 2025Q3 | premium | 139 | 0 | 0.0% | 0.0% | n/a | ₦-3,665,083,426 | ₦3,665,083,426 |
| AXA_DIRECT | 2025Q4 | outstanding | 926 | 1055 | 99.1% | 0.0% | 69.8% | ₦-9,793,429,864 | ₦9,793,429,864 |
| AXA_DIRECT | 2025Q4 | paid | 68 | 68 | 100.0% | 0.0% | 62.7% | ₦-417,117,422 | ₦417,117,422 |
| AXA_DIRECT | 2025Q4 | premium | 93 | 0 | 0.0% | 0.0% | n/a | ₦-2,126,956,664 | ₦2,126,956,664 |
| CUSTODIAN_SCIB | 2020Q1 | paid | 6 | 6 | 100.0% | 0.0% | 77.3% | ₦0 | ₦55,674,531 |
| CUSTODIAN_SCIB | 2020Q1 | premium | 56 | 56 | 100.0% | 0.0% | 87.2% | ₦0 | ₦71,409,468 |
| CUSTODIAN_SCIB | 2020Q2 | paid | 3 | 3 | 100.0% | 0.0% | 81.8% | ₦0 | ₦11,092,362 |
| CUSTODIAN_SCIB | 2020Q2 | premium | 19 | 19 | 100.0% | 0.0% | 91.0% | ₦0 | ₦37,649,259 |
| CUSTODIAN_SCIB | 2020Q3 | paid | 7 | 7 | 100.0% | 0.0% | 81.8% | ₦0 | ₦15,965,815 |
| CUSTODIAN_SCIB | 2020Q3 | premium | 21 | 21 | 100.0% | 0.0% | 88.1% | ₦0 | ₦21,602,122 |
| CUSTODIAN_SCIB | 2020Q4 | premium | 47 | 47 | 100.0% | 0.0% | 90.3% | ₦0 | ₦62,191,192 |
| CUSTODIAN_SCIB | 2021Q1 | paid | 3 | 3 | 100.0% | 0.0% | 81.8% | ₦0 | ₦38,451,607 |
| CUSTODIAN_SCIB | 2021Q1 | premium | 55 | 55 | 100.0% | 0.0% | 88.6% | ₦0 | ₦79,962,451 |
| CUSTODIAN_SCIB | 2021Q2 | paid | 11 | 11 | 100.0% | 0.0% | 81.8% | ₦0 | ₦53,886,126 |
| CUSTODIAN_SCIB | 2021Q2 | premium | 55 | 55 | 100.0% | 0.0% | 90.6% | ₦0 | ₦67,557,879 |
| CUSTODIAN_SCIB | 2021Q3 | paid | 3 | 3 | 100.0% | 0.0% | 81.8% | ₦0 | ₦4,046,395 |
| CUSTODIAN_SCIB | 2021Q3 | premium | 72 | 72 | 100.0% | 0.0% | 91.6% | ₦0 | ₦94,603,634 |
| CUSTODIAN_SCIB | 2021Q4 | paid | 3 | 3 | 100.0% | 0.0% | 81.8% | ₦0 | ₦1,728,410 |
| CUSTODIAN_SCIB | 2021Q4 | premium | 51 | 51 | 100.0% | 0.0% | 90.1% | ₦0 | ₦85,377,858 |
| CUSTODIAN_SCIB | 2022Q1 | paid | 2 | 0 | 0.0% | 0.0% | n/a | ₦-2,785,251 | ₦2,785,251 |
| CUSTODIAN_SCIB | 2022Q1 | premium | 43 | 43 | 100.0% | 0.0% | 89.5% | ₦0 | ₦78,747,873 |
| CUSTODIAN_SCIB | 2022Q2 | paid | 7 | 0 | 0.0% | 0.0% | n/a | ₦-15,508,284 | ₦15,508,284 |
| CUSTODIAN_SCIB | 2022Q2 | premium | 53 | 53 | 100.0% | 0.0% | 88.3% | ₦0 | ₦92,102,213 |
| CUSTODIAN_SCIB | 2022Q3 | paid | 4 | 4 | 100.0% | 0.0% | 81.8% | ₦0 | ₦4,182,781 |
| CUSTODIAN_SCIB | 2022Q3 | premium | 43 | 43 | 100.0% | 0.0% | 90.5% | ₦0 | ₦64,211,330 |
| CUSTODIAN_SCIB | 2022Q4 | paid | 7 | 7 | 100.0% | 0.0% | 81.8% | ₦0 | ₦15,601,128 |
| CUSTODIAN_SCIB | 2022Q4 | premium | 37 | 37 | 100.0% | 0.0% | 90.0% | ₦0 | ₦66,509,953 |
| CUSTODIAN_SCIB | 2023Q1 | paid | 5 | 5 | 100.0% | 0.0% | 81.8% | ₦0 | ₦3,796,449 |
| CUSTODIAN_SCIB | 2023Q1 | premium | 53 | 53 | 100.0% | 0.0% | 90.7% | ₦0 | ₦111,387,875 |
| CUSTODIAN_SCIB | 2023Q2 | paid | 10 | 10 | 100.0% | 0.0% | 81.8% | ₦0 | ₦9,145,709 |
| CUSTODIAN_SCIB | 2023Q2 | premium | 42 | 42 | 100.0% | 0.0% | 89.5% | ₦0 | ₦110,803,985 |
| CUSTODIAN_SCIB | 2023Q3 | paid | 12 | 12 | 100.0% | 0.0% | 81.8% | ₦0 | ₦27,722,650 |
| CUSTODIAN_SCIB | 2023Q3 | premium | 35 | 35 | 100.0% | 0.0% | 90.4% | ₦0 | ₦96,210,759 |
| CUSTODIAN_SCIB | 2023Q4 | paid | 8 | 8 | 100.0% | 0.0% | 81.8% | ₦0 | ₦5,020,746 |
| CUSTODIAN_SCIB | 2023Q4 | premium | 30 | 30 | 100.0% | 0.0% | 88.6% | ₦0 | ₦70,901,108 |
| CUSTODIAN_SCIB | 2024Q1 | paid | 9 | 9 | 100.0% | 0.0% | 81.8% | ₦0 | ₦110,803,394 |
| CUSTODIAN_SCIB | 2024Q1 | premium | 54 | 54 | 100.0% | 0.0% | 90.3% | ₦0 | ₦249,095,753 |
| CUSTODIAN_SCIB | 2024Q2 | paid | 4 | 4 | 100.0% | 0.0% | 81.8% | ₦0 | ₦17,964,413 |
| CUSTODIAN_SCIB | 2024Q2 | premium | 29 | 29 | 100.0% | 0.0% | 89.1% | ₦0 | ₦161,218,273 |
| CUSTODIAN_SCIB | 2024Q3 | paid | 1 | 1 | 100.0% | 0.0% | 81.8% | ₦0 | ₦3,999,701 |
| CUSTODIAN_SCIB | 2024Q3 | premium | 64 | 64 | 100.0% | 0.0% | 89.9% | ₦0 | ₦173,787,167 |
| CUSTODIAN_SCIB | 2024Q4 | paid | 6 | 6 | 100.0% | 0.0% | 81.8% | ₦0 | ₦96,317,156 |
| CUSTODIAN_SCIB | 2024Q4 | premium | 63 | 63 | 100.0% | 0.0% | 90.8% | ₦0 | ₦136,800,332 |
| CUSTODIAN_SCIB | 2025Q1 | paid | 9 | 9 | 100.0% | 0.0% | 81.8% | ₦0 | ₦-69,764,269 |
| CUSTODIAN_SCIB | 2025Q1 | premium | 130 | 130 | 100.0% | 0.0% | 90.4% | ₦0 | ₦572,202,158 |
| CUSTODIAN_SCIB | 2025Q2 | paid | 4 | 4 | 100.0% | 0.0% | 81.8% | ₦0 | ₦8,072,211 |
| CUSTODIAN_SCIB | 2025Q2 | premium | 59 | 59 | 100.0% | 0.0% | 90.2% | ₦0 | ₦946,940,180 |
| CUSTODIAN_SCIB | 2025Q3 | paid | 3 | 3 | 100.0% | 0.0% | 75.8% | ₦0 | ₦157,841 |
| CUSTODIAN_SCIB | 2025Q3 | premium | 59 | 59 | 100.0% | 0.0% | 90.6% | ₦0 | ₦341,591,885 |
| CUSTODIAN_SCIB | 2025Q4 | paid | 5 | 0 | 0.0% | 0.0% | n/a | ₦-329,229,046 | ₦329,229,046 |
| CUSTODIAN_SCIB | 2025Q4 | premium | 62 | 7 | 11.3% | 0.0% | 91.2% | ₦-407,437,614 | ₦469,179,845 |
| HEIRS_DIRECT | 2025Q1 | paid | 78 | 77 | 98.7% | 0.0% | 45.5% | ₦-391,188,366 | ₦391,188,366 |
| HEIRS_DIRECT | 2025Q1 | premium | 886 | 886 | 100.0% | 0.0% | 42.8% | ₦0 | ₦3,130,842,751 |
| HEIRS_DIRECT | 2025Q2 | paid | 80 | 80 | 100.0% | 0.0% | 45.5% | ₦-222,617,535 | ₦222,617,535 |
| HEIRS_DIRECT | 2025Q2 | premium | 948 | 947 | 99.9% | 0.0% | 42.9% | ₦0 | ₦1,968,587,160 |
| HEIRS_DIRECT | 2025Q3 | paid | 104 | 212 | 100.0% | 0.0% | 54.5% | ₦-425,170,260 | ₦425,170,260 |
| HEIRS_DIRECT | 2025Q3 | premium | 1103 | 1103 | 100.0% | 0.0% | 42.9% | ₦0 | ₦2,215,433,270 |
| HEIRS_DIRECT | 2025Q4 | paid | 112 | 111 | 99.1% | 0.0% | 54.5% | ₦-956,505,166 | ₦956,505,166 |
| HEIRS_DIRECT | 2025Q4 | premium | 984 | 0 | 0.0% | 0.0% | n/a | ₦-1,877,307,208 | ₦1,877,307,208 |
| HEIRS_JOMOLA | 2021Q1 | paid | 3 | 0 | 0.0% | 0.0% | n/a | ₦-9,521,885 | ₦9,521,885 |
| HEIRS_JOMOLA | 2021Q1 | premium | 84 | 84 | 100.0% | 0.0% | 57.1% | ₦0 | ₦252,885,617 |
| HEIRS_JOMOLA | 2021Q2 | paid | 1 | 1 | 100.0% | 0.0% | 81.8% | ₦0 | ₦1,019,014 |
| HEIRS_JOMOLA | 2021Q2 | premium | 179 | 290 | 99.4% | 0.0% | 57.1% | ₦226,831,054 | ₦206,129,369 |
| HEIRS_JOMOLA | 2021Q3 | paid | 30 | 30 | 100.0% | 0.0% | 81.8% | ₦0 | ₦25,302,493 |
| HEIRS_JOMOLA | 2021Q3 | premium | 351 | 351 | 99.7% | 0.0% | 50.0% | ₦910,598 | ₦249,779,850 |
| HEIRS_JOMOLA | 2021Q4 | paid | 115 | 115 | 58.3% | 0.0% | 77.5% | ₦0 | ₦105,108,098 |
| HEIRS_JOMOLA | 2021Q4 | premium | 220 | 220 | 100.0% | 0.0% | 49.9% | ₦0 | ₦174,539,461 |
| LASACO_FEYBIL | 2023Q1 | outstanding | 3 | 6 | 100.0% | 0.0% | 44.4% | ₦0 | ₦0 |
| LASACO_FEYBIL | 2023Q1 | premium | 437 | 436 | 99.8% | 0.0% | 54.3% | ₦-103,200 | ₦153,711,756 |
| LASACO_FEYBIL | 2023Q2 | outstanding | 3 | 3 | 100.0% | 0.0% | 44.4% | ₦0 | ₦0 |
| LASACO_FEYBIL | 2023Q2 | premium | 240 | 239 | 99.6% | 0.0% | 36.3% | ₦-103,200 | ₦85,012,668 |
| LASACO_FEYBIL | 2023Q3 | outstanding | 3 | 3 | 100.0% | 0.0% | 44.4% | ₦0 | ₦0 |
| LASACO_FEYBIL | 2023Q3 | premium | 313 | 313 | 100.0% | 0.0% | 36.1% | ₦0 | ₦142,518,070 |
| LASACO_FEYBIL | 2023Q4 | outstanding | 3 | 3 | 100.0% | 0.0% | 33.3% | ₦2,600,000 | ₦0 |
| LASACO_FEYBIL | 2023Q4 | premium | 504 | 503 | 99.8% | 0.0% | 36.2% | ₦-109,650 | ₦215,270,271 |
| NEM_SCIB | 2020Q1 | outstanding | 68 | 68 | 100.0% | 0.0% | 31.0% | ₦-229,522,070 | ₦229,522,070 |
| NEM_SCIB | 2020Q1 | paid | 115 | 115 | 73.0% | 0.0% | 38.9% | ₦2,656,448 | ₦456,541,811 |
| NEM_SCIB | 2020Q1 | premium | 944 | 940 | 99.6% | 0.0% | 62.1% | ₦-1,675,931 | ₦1,217,312,713 |
| NEM_SCIB | 2020Q2 | outstanding | 63 | 63 | 100.0% | 0.0% | 31.0% | ₦-60,740,095 | ₦60,740,095 |
| NEM_SCIB | 2020Q2 | paid | 64 | 64 | 100.0% | 0.0% | 41.3% | ₦0 | ₦334,679,561 |
| NEM_SCIB | 2020Q2 | premium | 466 | 465 | 99.8% | 0.0% | 62.2% | ₦-11,081,352 | ₦564,406,207 |
| NEM_SCIB | 2020Q3 | outstanding | 64 | 64 | 100.0% | 0.0% | 31.1% | ₦-68,820,984 | ₦68,820,984 |
| NEM_SCIB | 2020Q3 | paid | 153 | 152 | 91.5% | 0.0% | 44.2% | ₦-22,395,615 | ₦273,130,786 |
| NEM_SCIB | 2020Q3 | premium | 617 | 616 | 99.8% | 0.0% | 62.4% | ₦-798,898 | ₦420,952,403 |
| NEM_SCIB | 2020Q4 | outstanding | 58 | 58 | 100.0% | 0.0% | 30.1% | ₦-46,720,623 | ₦46,720,623 |
| NEM_SCIB | 2020Q4 | paid | 95 | 95 | 88.4% | 0.0% | 38.0% | ₦-622,628 | ₦455,683,918 |
| NEM_SCIB | 2020Q4 | premium | 755 | 755 | 100.0% | 0.0% | 63.1% | ₦0 | ₦247,768,598 |
| NEM_SCIB | 2021Q1 | paid | 147 | 147 | 81.6% | 0.0% | 34.8% | ₦-5,055,563 | ₦625,372,223 |
| NEM_SCIB | 2021Q1 | premium | 739 | 737 | 99.7% | 0.0% | 61.6% | ₦-8,883,693 | ₦946,255,708 |
| NEM_SCIB | 2021Q2 | outstanding | 17 | 17 | 100.0% | 0.0% | 35.8% | ₦0 | ₦295,483,250 |
| NEM_SCIB | 2021Q2 | paid | 151 | 0 | 0.0% | 0.0% | n/a | ₦-335,595,018 | ₦335,595,018 |
| NEM_SCIB | 2021Q2 | premium | 671 | 670 | 99.9% | 0.0% | 62.9% | ₦-11,081,352 | ₦454,946,160 |
| NEM_SCIB | 2021Q3 | outstanding | 17 | 0 | 0.0% | 0.0% | n/a | ₦-295,483,250 | ₦295,483,250 |
| NEM_SCIB | 2021Q3 | paid | 171 | 171 | 100.0% | 0.0% | 44.8% | ₦0 | ₦436,499,664 |
| NEM_SCIB | 2021Q3 | premium | 775 | 773 | 99.7% | 0.0% | 62.2% | ₦-1,125,000 | ₦661,600,007 |
| NEM_SCIB | 2021Q4 | outstanding | 18 | 0 | 0.0% | 0.0% | n/a | ₦-302,883,250 | ₦302,883,250 |
| NEM_SCIB | 2021Q4 | paid | 206 | 0 | 0.0% | 0.0% | n/a | ₦-416,155,792 | ₦416,155,792 |
| NEM_SCIB | 2021Q4 | premium | 920 | 919 | 99.9% | 0.0% | 63.1% | ₦-22,948 | ₦353,049,703 |
| NEM_SCIB | 2022Q1 | outstanding | 18 | 0 | 0.0% | 0.0% | n/a | ₦-302,883,250 | ₦302,883,250 |
| NEM_SCIB | 2022Q1 | paid | 118 | 118 | 100.0% | 0.0% | 41.6% | ₦0 | ₦728,634,529 |
| NEM_SCIB | 2022Q1 | premium | 787 | 784 | 99.6% | 0.0% | 62.8% | ₦-491,014 | ₦1,321,819,645 |
| NEM_SCIB | 2022Q2 | paid | 149 | 149 | 100.0% | 0.0% | 45.1% | ₦0 | ₦605,841,939 |
| NEM_SCIB | 2022Q2 | premium | 811 | 805 | 99.3% | 0.0% | 63.0% | ₦-9,890,404 | ₦623,669,908 |
| NEM_SCIB | 2022Q3 | outstanding | 102 | 82 | 80.4% | 0.0% | 33.8% | ₦-80,094,397 | ₦80,094,397 |
| NEM_SCIB | 2022Q3 | paid | 143 | 135 | 94.4% | 0.0% | 44.1% | ₦-84,280,837 | ₦675,243,202 |
| NEM_SCIB | 2022Q3 | premium | 870 | 868 | 99.8% | 0.0% | 63.3% | ₦-1,120,512 | ₦845,871,276 |
| NEM_SCIB | 2022Q4 | paid | 126 | 119 | 94.4% | 0.0% | 45.5% | ₦-42,507,473 | ₦349,211,343 |
| NEM_SCIB | 2022Q4 | premium | 710 | 708 | 99.7% | 0.0% | 55.7% | ₦-374,395,566 | ₦505,117,903 |
| NEM_SCIB | 2023Q1 | outstanding | 511 | 511 | 100.0% | 0.0% | 36.8% | ₦-238,958,316 | ₦238,958,316 |
| NEM_SCIB | 2023Q1 | paid | 250 | 250 | 100.0% | 0.0% | 52.6% | ₦3,750,000 | ₦1,094,581,431 |
| NEM_SCIB | 2023Q1 | premium | 1036 | 687 | 66.3% | 0.0% | 51.3% | ₦-1,968,166,500 | ₦1,968,166,500 |
| NEM_SCIB | 2023Q2 | outstanding | 50 | 27 | 54.0% | 0.0% | 35.4% | ₦-8,828,063 | ₦8,828,063 |
| NEM_SCIB | 2023Q2 | paid | 91 | 83 | 91.2% | 0.0% | 38.9% | ₦-161,432,451 | ₦601,966,241 |
| NEM_SCIB | 2023Q2 | premium | 1002 | 1000 | 99.8% | 0.0% | 60.5% | ₦-265,585,186 | ₦1,304,424,801 |
| NEM_SCIB | 2023Q3 | paid | 169 | 165 | 97.6% | 0.0% | 24.8% | ₦-1,140,267,801 | ₦1,140,267,801 |
| NEM_SCIB | 2023Q3 | premium | 1201 | 1201 | 100.0% | 0.0% | 52.5% | ₦-1,551,379,039 | ₦1,551,379,039 |
| NEM_SCIB | 2023Q4 | paid | 163 | 135 | 82.8% | 0.0% | 45.2% | ₦-302,811,183 | ₦740,111,922 |
| NEM_SCIB | 2023Q4 | premium | 712 | 0 | 0.0% | 0.0% | n/a | ₦-772,518,483 | ₦772,518,483 |
| NEM_SCIB | 2024Q1 | paid | 278 | 247 | 88.8% | 0.0% | 29.4% | ₦-1,109,067,954 | ₦1,109,067,954 |
| NEM_SCIB | 2024Q1 | premium | 709 | 704 | 99.3% | 0.0% | 62.6% | ₦-15,557,847 | ₦2,143,630,885 |
| NEM_SCIB | 2024Q2 | outstanding | 186 | 148 | 79.6% | 0.0% | 35.9% | ₦-72,043,479 | ₦72,043,479 |
| NEM_SCIB | 2024Q2 | paid | 222 | 0 | 0.0% | 0.0% | n/a | ₦-947,025,401 | ₦947,025,401 |
| NEM_SCIB | 2024Q2 | premium | 935 | 934 | 99.9% | 0.0% | 63.0% | ₦-117,764 | ₦1,249,379,819 |
| NEM_SCIB | 2024Q3 | outstanding | 1134 | 1128 | 99.5% | 0.0% | 36.1% | ₦-3,433,916,766 | ₦3,433,916,766 |
| NEM_SCIB | 2024Q3 | paid | 195 | 195 | 100.0% | 0.0% | 27.3% | ₦-969,496,708 | ₦969,496,708 |
| NEM_SCIB | 2024Q3 | premium | 1040 | 0 | 0.0% | 0.0% | n/a | ₦-2,237,287,798 | ₦2,237,287,798 |
| NEM_SCIB | 2024Q4 | paid | 163 | 163 | 100.0% | 0.0% | 33.2% | ₦-1,037,479,024 | ₦1,037,479,024 |
| NEM_SCIB | 2024Q4 | premium | 867 | 864 | 99.7% | 0.0% | 61.9% | ₦-670,112,156 | ₦1,625,335,090 |
| NEM_SCIB | 2025Q1 | outstanding | 465 | 465 | 100.0% | 0.0% | 27.0% | ₦-207,975,611 | ₦207,975,611 |
| NEM_SCIB | 2025Q1 | paid | 275 | 260 | 94.5% | 0.0% | 24.4% | ₦-1,919,590,152 | ₦1,919,590,152 |
| NEM_SCIB | 2025Q1 | premium | 1099 | 1095 | 99.6% | 0.0% | 62.7% | ₦-42,874,049 | ₦6,466,290,208 |
| NEM_SCIB | 2025Q2 | outstanding | 224 | 224 | 100.0% | 0.0% | 27.0% | ₦-463,763,183 | ₦463,763,183 |
| NEM_SCIB | 2025Q2 | paid | 286 | 274 | 95.8% | 0.0% | 27.8% | ₦-1,593,298,036 | ₦1,593,298,036 |
| NEM_SCIB | 2025Q2 | premium | 884 | 880 | 99.5% | 0.0% | 62.8% | ₦-2,642,532,805 | ₦5,285,065,609 |
| NEM_SCIB | 2025Q3 | paid | 248 | 0 | 0.0% | 0.0% | n/a | ₦-1,200,459,958 | ₦1,200,459,958 |
| NEM_SCIB | 2025Q3 | premium | 968 | 934 | 95.4% | 0.0% | 62.5% | ₦333,739,366 | ₦3,050,669,961 |
| NEM_SCIB | 2025Q4 | paid | 275 | 271 | 98.5% | 0.0% | 23.6% | ₦-2,130,349,531 | ₦2,130,349,531 |
| NEM_SCIB | 2025Q4 | premium | 1108 | 1104 | 99.6% | 0.0% | 62.7% | ₦-952,045,176 | ₦3,663,399,550 |
| ROYAL_EXCHANGE_DIRECT | 2024Q1 | paid | 302 | 137 | 45.4% | 0.0% | 61.0% | ₦-148,093,091 | ₦197,021,658 |
| ROYAL_EXCHANGE_DIRECT | 2024Q1 | premium | 3545 | 1151 | 0.0% | 0.0% | n/a | ₦-1,450,101,468 | ₦1,762,188,412 |
| ROYAL_EXCHANGE_DIRECT | 2024Q2 | paid | 293 | 146 | 4.1% | 0.0% | 50.8% | ₦-69,484,898 | ₦147,678,064 |
| ROYAL_EXCHANGE_DIRECT | 2024Q2 | premium | 3586 | 1126 | 6.3% | 0.0% | 59.7% | ₦304,487,676 | ₦3,434,796,692 |
| ROYAL_EXCHANGE_DIRECT | 2024Q3 | paid | 296 | 150 | 50.7% | 0.0% | 55.0% | ₦-78,193,165 | ₦186,970,895 |
| ROYAL_EXCHANGE_DIRECT | 2024Q3 | premium | 4445 | 3319 | 0.0% | 0.0% | 57.1% | ₦-4,874,778,018 | ₦6,487,877,738 |
| ROYAL_EXCHANGE_DIRECT | 2024Q4 | paid | 291 | 459 | 100.0% | 0.0% | 60.6% | ₦222,427,921 | ₦318,409,054 |
| ROYAL_EXCHANGE_DIRECT | 2024Q4 | premium | 3496 | 5966 | 100.0% | 0.0% | 75.7% | ₦18,128,923,532 | ₦18,799,610,800 |
| ROYAL_EXCHANGE_DIRECT | 2025Q1 | paid | 315 | 312 | 99.0% | 0.0% | 79.9% | ₦-6,103,452 | ₦461,690,268 |
| ROYAL_EXCHANGE_DIRECT | 2025Q1 | premium | 4780 | 4776 | 99.9% | 0.0% | 75.9% | ₦-586,905,273 | ₦24,919,104,449 |
| ROYAL_EXCHANGE_DIRECT | 2025Q2 | paid | 289 | 289 | 100.0% | 0.0% | 72.1% | ₦0 | ₦309,335,542 |
| ROYAL_EXCHANGE_DIRECT | 2025Q2 | premium | 3247 | 3245 | 99.9% | 0.0% | 74.8% | ₦-318,000 | ₦14,147,143,705 |
| ROYAL_EXCHANGE_DIRECT | 2025Q3 | paid | 364 | 362 | 99.5% | 0.0% | 71.1% | ₦-30,488 | ₦497,594,447 |
| ROYAL_EXCHANGE_DIRECT | 2025Q3 | premium | 4444 | 4444 | 100.0% | 0.0% | 75.3% | ₦0 | ₦24,833,229,082 |
| ROYAL_EXCHANGE_DIRECT | 2025Q4 | paid | 353 | 352 | 99.7% | 0.0% | 66.8% | ₦-688,970 | ₦720,659,391 |
| ROYAL_EXCHANGE_DIRECT | 2025Q4 | premium | 4546 | 4546 | 100.0% | 0.0% | 75.8% | ₦0 | ₦30,566,299,905 |
| UNITRUST_AGRIC | 2023Q1 | premium | 5 | 5 | 100.0% | 0.0% | 57.1% | ₦0 | ₦8,260,604 |
| UNITRUST_AGRIC | 2023Q2 | paid | 1 | 1 | 100.0% | 0.0% | 50.0% | ₦87,021 | ₦0 |
| UNITRUST_AGRIC | 2023Q2 | premium | 7 | 7 | 100.0% | 0.0% | 57.1% | ₦0 | ₦72,927,211 |
| UNITRUST_AGRIC | 2023Q3 | paid | 1 | 1 | 100.0% | 0.0% | 37.5% | ₦160,650 | ₦0 |
| UNITRUST_AGRIC | 2023Q3 | premium | 2 | 2 | 100.0% | 0.0% | 57.1% | ₦0 | ₦2,543,351 |
| UNITRUST_AGRIC | 2023Q4 | premium | 1 | 1 | 100.0% | 0.0% | 57.1% | ₦0 | ₦3,808,795 |
| UNITRUST_AGRIC | 2025Q1 | outstanding | 6 | 0 | 0.0% | 0.0% | n/a | ₦0 | ₦0 |
| UNITRUST_AGRIC | 2025Q1 | paid | 2 | 0 | 0.0% | 0.0% | n/a | ₦0 | ₦0 |
| UNITRUST_AGRIC | 2025Q1 | premium | 18 | 0 | 0.0% | 0.0% | n/a | ₦-20,106,704 | ₦20,106,704 |
| UNITRUST_AGRIC | 2025Q2 | paid | 2 | 0 | 0.0% | 0.0% | n/a | ₦-7,029,291 | ₦7,029,291 |
| UNITRUST_AGRIC | 2025Q2 | premium | 6 | 0 | 0.0% | 0.0% | n/a | ₦-10,220,900 | ₦10,220,900 |
| UNITRUST_AGRIC | 2025Q3 | paid | 1 | 1 | 100.0% | 0.0% | 36.4% | ₦0 | ₦3,041,016 |
| UNITRUST_AGRIC | 2025Q3 | premium | 9 | 0 | 0.0% | 0.0% | n/a | ₦-22,891,415 | ₦22,891,415 |
| UNITRUST_AGRIC | 2025Q4 | paid | 2 | 2 | 100.0% | 0.0% | 36.4% | ₦0 | ₦12,946,909 |
| UNITRUST_AGRIC | 2025Q4 | premium | 10 | 0 | 0.0% | 0.0% | n/a | ₦-100,999,531 | ₦100,999,531 |

## Files
- Scripts: `report/scripts/` (run_cedant.py, build_spec.py, compare_gold.py, summarize.py, validate_all.py, gold_consistency.py, edge_cases.py, api_tests.py, make_tables.py)
- Data: `report/compare_all.json` (per-quarter/per-field diffs with samples), `edge_cases.json`, `api_tests.json`, `validate_all.json`, `gold_consistency.json`, `spec_all.json`; run outputs in `/workspace/cre_test/runs/<PAIR>/<yyyyQn>/`
