# Phase 1 audit — canonical flight identity and 2024 source reconciliation

**Status: canonical output blocked pending source correction.** The audit found one eligible BTS row with a blank reporting flight number. I did not assign a fallback ID, and the script withheld the canonical table and crosswalk.

## Scope and repository state

- Audit branch: codex/canonical-flight-audit, based on feature/flight-weather-ml at 07a7a71.
- PR #1 remains open and draft; no branch switch, merge, or PR change was made. main was not touched.
- Audit source: the 12 monthly 2024 BTS ZIPs in the existing checkout. All ZIP CRC checks passed.
- Runtime: system Python 3.13.2, standard library only. The project .venv and saved Parquet/model artifacts were not opened.
- No model training, scoring, feature generation, or feature-definition changes were performed.

## 2024 BTS archive provenance

| Month | Source ZIP | Bytes | SHA-256 |
|---:|---|---:|---|
| 01 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_1.zip | 27,573,265 | fe089b45523f9d4ac0ccd0e176a543d274e914ee5ae5bd384dbaafc7dc06ebfd |
| 02 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_2.zip | 25,086,854 | 8463683d100504a97847497d2f2281008fd9add9639bf7436a9747032df15f9a |
| 03 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_3.zip | 29,313,663 | e2c5021406f480e813a52a2ed795c251eb1169613da6a416daf88236db8c9013 |
| 04 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_4.zip | 28,540,589 | d8b1e88626a7375d351962917fe5ec132660c1636dc8b91f778627c769e0e15a |
| 05 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_5.zip | 31,139,902 | 03e4ae26c2d1247de02d0b9a4d87160f8021718abf475a1421de9c45b7d68b04 |
| 06 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_6.zip | 31,226,761 | 4f329439fd6892aaf89b32908191b2a007e70f775e2551181a22e704ef28a0ab |
| 07 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_7.zip | 34,427,174 | 4d992fee134137b3fa43094d7e8cf4a8e6feaa6a422cffb4063f1ed6c7f3f731 |
| 08 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_8.zip | 30,854,511 | 58d651379af90cc44635657e95fe098567ff954046aec21d6d2cd18424a317f9 |
| 09 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_9.zip | 28,433,978 | c0df7503b0050ca46099dab1edfffa33464a0a1031ad712c542af6a4ecece24c |
| 10 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_10.zip | 29,418,453 | a417136d6c1663736437dfeeacefa5b93b82e9db322c863c2f57b19bdd3f04fb |
| 11 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_11.zip | 28,149,354 | 26b6e89e7c1f66163a412380c743c648eb470a9017074de4efbd209f1e09860a |
| 12 | On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2024_12.zip | 30,150,370 | a324577aa76d1f0aa36239f8010b6b5270fea0ff5ac846177753b76a42c5d01c |

All 12 archives contained one BTS CSV member, passed CRC validation, and had matching headers. Total source rows: 7,079,061.

## Processing stages and population reconciliation

The Flight/Weather preprocessing code in src/bts_preprocessing.py uses the rule: valid in-month date, Cancelled=0, and binary DepDel15. Replaying this rule against raw BTS matched all 12 saved monthly_audit.csv eligible counts exactly, totaling 6,982,746 rows.

The Airport/Network notebook’s complete-input mask requires date, carrier, route, scheduled departure time, and DEP_DELAY; it derives delay_15 from DEP_DELAY >= 15. The notebook cleaning cell does not itself filter cancellation, while the later scoring eligibility does.

| Source-derived stage | Rows |
|---|---:|
| All 2024 BTS source rows | 7,079,061 |
| Cancelled rows | 96,315 |
| Missing DepDel15 | 92,970 |
| Missing DepDelay | 92,970 |
| Airport/Network complete-input candidates before cancellation filtering | 6,986,091 |
| Of those candidates, cancelled | 3,345 |
| Airport/Network non-cancelled candidates | 6,982,746 |
| Flight/Weather eligible rows | 6,982,746 |
| Airport/Network retrospective T−2h eligible rows | 6,982,746 |
| Airport/Network retrospective T−30m eligible rows | 6,982,312 |
| Rows already departed by T−30m under final BTS DepDelay | 434 |

All rows with both DepDel15 and numeric DepDelay agree on the label DepDel15 = (DepDelay >= 15): zero mismatches. The single identity-incomplete row also has DepDel15=0 and DepDelay=-6, so its target agrees. The 3,345 row-level differences between the broad A/N input candidate and the F/W population are all cancelled rows; they disappear when applying A/N’s later non-cancelled eligibility.

This does **not** reconcile the handoff’s Airport/Network file counts:

| Reported or reconstructed population | Rows |
|---|---:|
| Handoff-reported A/N input before duplicate removal | 6,982,766 |
| Handoff-reported exact duplicates removed | 110 |
| Handoff-reported A/N rows after duplicate removal | 6,982,656 |
| Flight/Weather eligible rows | 6,982,746 |
| Reported post-dedup A/N minus F/W | −90 |
| Reconstructed raw-BTS A/N complete-input candidates | 6,986,091 |
| Reconstructed raw-BTS A/N non-cancelled candidates | 6,982,746 |
| Exact duplicate BTS source rows found | 0 |

The local BTS reconstruction therefore differs from the handoff’s A/N pre-dedup input by 3,325 rows. Its exact source-row duplicate count is zero, so the handoff’s 110 duplicate removals are not reproduced by these archives. The 90-row post-dedup count difference cannot be attributed to deduplication based on available evidence. The original cleaned file and build manifest are necessary for exact record-level reconciliation.

The notebook’s required OP_UNIQUE_CARRIER field is not present in the 2024 BTS raw header; this audit uses Reporting_Airline as a clearly labeled proxy. The original preparation script is unavailable, so that mapping is not independently confirmed.

The per-month row counts, ZIP hashes, duplicate checks, and source-derived row-level differences are in the ignored output directory described below. The difference CSV has 3,345 rows, all cancelled A/N candidates; it is a comparison reconstructed from BTS rules, not a comparison against the missing Colab CSV.

## Per-month processing counts

These source-level counts include the single row whose identity is incomplete. The SQL identity-set summaries exclude that row and therefore show 6,982,745 assignable non-cancelled IDs rather than the 6,982,746 source rows. Every F/W count below matches the saved monthly audit.

| Month | BTS source rows | F/W eligible | A/N complete candidates | A/N non-cancelled | A/N T−2h retrospective | A/N T−30m retrospective | Exact duplicate copies |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 01 | 547,271 | 526,882 | 527,413 | 526,882 | 526,882 | 526,833 | 0 |
| 02 | 519,221 | 516,219 | 516,345 | 516,219 | 516,219 | 516,158 | 0 |
| 03 | 591,767 | 586,647 | 586,822 | 586,647 | 586,647 | 586,610 | 0 |
| 04 | 582,185 | 578,150 | 578,337 | 578,150 | 578,150 | 578,098 | 0 |
| 05 | 609,743 | 601,499 | 601,767 | 601,499 | 601,499 | 601,472 | 0 |
| 06 | 611,132 | 603,256 | 603,676 | 603,256 | 603,256 | 603,236 | 0 |
| 07 | 634,613 | 616,241 | 616,756 | 616,241 | 616,241 | 616,228 | 0 |
| 08 | 619,025 | 606,311 | 606,942 | 606,311 | 606,311 | 606,291 | 0 |
| 09 | 582,622 | 579,123 | 579,201 | 579,123 | 579,123 | 579,086 | 0 |
| 10 | 615,497 | 609,213 | 609,295 | 609,213 | 609,213 | 609,174 | 0 |
| 11 | 575,404 | 572,775 | 572,872 | 572,775 | 572,775 | 572,732 | 0 |
| 12 | 590,581 | 586,430 | 586,665 | 586,430 | 586,430 | 586,394 | 0 |
| **Total** | **7,079,061** | **6,982,746** | **6,986,091** | **6,982,746** | **6,982,746** | **6,982,312** | **0** |

The source candidate columns follow the notebook’s nonmissing-input requirements as reconstructed from BTS. A/N complete candidates include the 3,345 cancelled records with a nonmissing delay; the later non-cancelled rule removes them.


## Identity audit and stop condition

The proposed durable ID is:

bts-flight-v1:<SHA-256 of normalized compact JSON>

The key fields, in order, are FlightDate, DOT_ID_Reporting_Airline, Reporting_Airline, Flight_Number_Reporting_Airline, OriginAirportID, Origin, DestAirportID, Dest, and CRSDepTime. Text codes are trimmed and uppercased, integral numeric identifiers are normalized, dates use ISO format, and scheduled time uses zero-padded HHMM while preserving 2400. The reporting carrier and flight number are part of the key, so codeshare rows are not collapsed by route and time.

Among the 7,079,060 records with complete identity fields, the audit found zero exact duplicate full source rows, zero conflicting identity keys, and zero flight-ID hash collisions. The ID function’s self-check confirms determinism and that changing a carrier or scheduled time changes the key.

One remaining raw row cannot receive this ID:

- Archive/member: August 2024, row ordinal 234,964.
- FlightDate 2024-08-25; reporting carrier F9; MIA to ATL; scheduled departure 0600.
- Flight_Number_Reporting_Airline is blank.
- Cancelled=0, DepDel15=0, DepDelay=-6.
- It is eligible for the Flight/Weather population. Assigning an ID by route and time, or inventing a flight number, could merge distinct flights, so canonicalization stopped.

Because a complete canonical table must include eligible rows and preserve identities across both workstreams, the script removed its provisional outputs and did not publish a canonical table or ID crosswalk. The other 7,079,060 valid identities are not published as a partial crosswalk.

## Eligibility caveat

The Airport/Network notebook forms actual departure time as scheduled time plus final BTS DEP_DELAY. It excludes a target when that reconstructed actual time is at or before the T−2h or T−30m cutoff. The 434 T−30m exclusions therefore depend on a final outcome field unavailable at prediction time. BTS archives do not provide event-availability timestamps proving that recent airport-state records were visible by those cutoffs. These are retrospective eligibility counts, not operational eligibility evidence.

A live scorer must establish “not departed” from a timestamped operational status snapshot available by the cutoff. No such source or timestamp lineage was validated in this phase.

## Code, outputs, and remaining blockers

The reproducible audit utility is src/audit_canonical_flights_2024.py. It uses system Python and the standard library, checks all 12 archive hashes/CRCs, replays both row-selection rules, measures exact duplicates and identity conflicts, writes row-level differences, and refuses to publish a canonical table if identity rows are incomplete or ambiguous.

Generated audit evidence is under data/processed/canonical_audit_2024_20261012, which is ignored by Git. It includes audit_report.json, source_archives.csv, monthly_stage_counts.csv, row_level_population_differences.csv, identity_conflicts.csv, identity_invalid_rows.csv, and collision audit output. No large canonical dataset or crosswalk was emitted.

The Flight/Weather preprocessing projection drops flight number, DOT carrier ID, origin/destination airport IDs, and DepDelay, so the existing prepared tables cannot be the source for canonical IDs; IDs must be assigned from BTS source records before either pipeline filters. The PR’s airport_network_integration.add_canonical_flight_id helper currently falls back to range(len(table)), which is row-order dependent and is not a durable cross-pipeline identifier.

Before a complete canonical table can be built:

1. Obtain the exact original flights_2024_cleaned_verified.csv.gz and its SHA-256, plus its source archive manifest and cleaning/concatenation script. Reconcile its 110 duplicate rows and 6,982,766 pre-dedup rows against BTS at record level.
2. Resolve the blank reporting flight number in the August BTS row from an authoritative source or a source correction. Do not use route/time-only identity.
3. Confirm how BTS Reporting_Airline became A/N OP_UNIQUE_CARRIER.
4. Once identity and source parity are accepted, run the canonicalizer on all years required by the agreed evaluation split, assigning IDs once before either pipeline branches. The present audit covered the disputed 2024 slice only.
5. Keep retrospective Airport/Network eligibility distinct from live readiness until a timestamped operational source proves feature and status availability by cutoff.

Previously inspected 2024 and 2025 model results remain exploratory. This audit makes no comparative performance claim.

## Reproduction

Run from the audit worktree root with the system Python 3.13 executable, not the project .venv. Choose a new output directory on each run because the script refuses to overwrite audit artifacts.

~~~powershell
& 'python' src\audit_canonical_flights_2024.py --raw-dir '<project-root>\data\raw' --fw-monthly-audit '<project-root>\data\processed\depdel15\monthly_audit.csv' --out-dir 'data\processed\canonical_audit_2024_rerun'
~~~


