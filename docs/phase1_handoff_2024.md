# Phase 1 Cohort Reconciliation Handoff — 2024

**Status:** retrospective identity and cohort membership are complete. Model-score matching remains blocked.

## Worktree and repository state

- Audit worktree: <project-root>
- Branch: codex/canonical-flight-audit
- Base commit: 07a7a71f37ca302fdd11c64c81aa06b4c54f3b9e
- Reconciliation script, focused tests, and audit documents are local worktree files; nothing was committed or pushed.
- The original feature/flight-weather-ml checkout and main were not changed.
- PR #1 remains open and draft, targeting feature/flight-weather-ml. The latest observed head is 55481a8607d472b310604ea9f133846c288e0061 on codex/airport-network-workstream. No merge or PR edit was made.

## Results

The retained 2024 reconciliation SQLite database was opened read-only. The script read 6,982,746 eligible raw-source rows and used the existing rk_key_idx and ck_key_idx. It opened no monthly BTS archives, created no indexes, and generated no features or scores.

| Horizon | Raw eligible records (includes F9) | Valid canonical IDs | Unique cleaned-to-raw ID mappings | Ambiguous raw candidates / groups | Incomplete identity | Raw eligible unmatched | Membership rows |
|---|---:|---:|---:|---:|---:|---:|---:|
| T−2h | 6,982,746 | 6,982,745 | 6,982,525 | 220 / 110 | 1 | 0 | 6,982,525 |
| T−30m | 6,982,312 | 6,982,311 | 6,982,091 | 220 / 110 | 1 | 0 | 6,982,091 |

The 220 ambiguous candidates are two raw BTS records for each of 110 cleaned rows. Neither candidate was selected. The single incomplete identity is the quarantined F9 MIA-to-ATL flight on 2024-08-25, scheduled 06:00. It is eligible at both retrospective cutoffs but its reporting flight number is blank, so no ID was assigned. Twenty cleaned-input rows have no raw match and remain outside horizon cohorts.

| ID set | T−2h | T−30m | Intersection | T−2h only | T−30m only |
|---|---:|---:|---:|---:|---:|
| All valid IDs in raw retrospective cohort | 6,982,745 | 6,982,311 | 6,982,311 | 434 | 0 |
| Unambiguous cleaned-to-raw IDs | 6,982,525 | 6,982,091 | 6,982,091 | 434 | 0 |

Intersections were counted per source row while applying both strict final-DEP_DELAY rules. The 434 T−2h-only flights have final DEP_DELAY in (-120, -30]. The saved identity audit reports zero duplicate ID groups and zero hash collisions.

These are retrospective pre-score candidate counts. Neither model’s horizon-specific row-level score table was available, so the final paired scored cohort is unknown. No comparative performance claim is made.

## Outputs and provenance

The generated files below are ignored, local artifacts. They are not uploaded or committed, and Omruthik cannot access them from GitHub.

| Artifact | Worktree-relative path | Rows / bytes | SHA-256 |
|---|---|---:|---|
| T−2h membership | data/processed/shared_cohort_2024_20261016_v1/t_minus_2h_membership.csv.gz | 6,982,525 / 426,427,472 | ddc2d679bb6c93fdc1ce6758ccee570b8110dbf726d05febc7e75d714d2ae1be |
| T−30m membership | data/processed/shared_cohort_2024_20261016_v1/t_minus_30m_membership.csv.gz | 6,982,091 / 426,384,565 | 988639c03df4213b448fa509f0999d35c74c372be5a907e460d0ac149748add3 |
| Exceptions | data/processed/shared_cohort_2024_20261016_v1/cohort_exceptions.csv | 462 / 188,676 | 15959c16bc950eb4dcfc94fa77085f665c47e31ca165e31cbf5d68d13558c7b2 |
| Machine-readable report | data/processed/shared_cohort_2024_20261016_v1/cohort_reconciliation_report.json | 7,291 bytes | f0fee0cca4617a850c05f2508a3e0a824ba99f582f7fd1c109bdb0ac5e24379c |
| Report sidecar and manifest | data/processed/shared_cohort_2024_20261016_v1/cohort_reconciliation_report.sha256 and checksums.sha256 | compact | report hash agrees with sidecar and manifest |

Existing input artifacts:

- Canonical table: data/processed/canonical_identity_2024_20261015_v1/canonical_flights.csv.gz — 7,079,060 rows; SHA-256 62c60f3aa01ac9ff8129f51f49dce7809cfd634429260705312cb0c52b40df3c.
- Cleaned-to-canonical crosswalk: data/processed/canonical_identity_2024_20261015_v1/cleaned_to_canonical_crosswalk.csv.gz — 6,982,766 original rows; SHA-256 3856b381d375bbc1cf04fc1fcbf2df63132ed12ced4e62df0def86b9f195da84.
- Canonical identity report: data/processed/canonical_identity_2024_20261015_v1/canonical_identity_run_report.json — sidecar verified; SHA-256 419aebfb91575420755c21b5cc559f01426f75229391b42f67e9c65c2d580117.
- Source reconciliation report: data/processed/airport_network_reconciliation_2024_20261012_retry1/audit_report.json — 7,079,061 raw rows, 6,982,766 cleaned before dedup, 6,982,656 after dedup, zero label mismatches.
- Horizon audit report: data/processed/canonical_audit_2024_20261012/audit_report.json — valid-ID totals 6,982,745 at T−2h and 6,982,311 at T−30m.
- Retained SQLite scratch database: data/processed/airport_network_reconciliation_2024_20261012_retry1/reconciliation.sqlite — 20,334,665,728 bytes. It remains intact and was opened read-only; no checksum recalculation was attempted for this 20 GB file.
- The previous identity audit document lists all 12 source BTS archive checksums: docs/phase1_canonical_flight_identity_audit_2024.md. This task did not reopen those archives.

## Reproduction and checks

The completed v1 run used the default output directory. The script refuses to overwrite it. To reproduce into a new ignored output directory, run from the audit worktree:

    & 'python' -B src\reconcile_shared_cohort_2024.py --output-dir data\processed\shared_cohort_2024_20261016_v2

The script validates saved reports and identity counts, checks the existing indexes, reads the retained database read-only, and writes the versioned membership, exception, report, and checksum files.

- tests/test_shared_cohort_reconciliation.py: 5 passed.
- tests/test_canonical_flight_identity.py: 6 passed.
- Independent verification fully decompressed both gzip CSVs, confirmed all three output row counts, recomputed output SHA-256 values, checked the report sidecar and manifest, and validated ID-set arithmetic: PASS.
- git diff --check: clean. Explicit trailing-whitespace scan of the untracked audit script, tests, and documents: clean.
- The full project suite was not run.

### Reproducibility

A same-machine rerun is possible while the ignored inputs and database remain available. Another person cannot reproduce this result from GitHub alone. The canonical table, crosswalk, cleaned input, BTS archives, 20 GB SQLite database, and membership outputs are local and ignored. They must be shared through an approved transfer method before Omruthik can inspect them.

## Feature availability and evaluation status

Read-only PR source at head 55481a8607d472b310604ea9f133846c288e0061 is airport-network/airport_network_feature_builder.py. It defines four prior-seven-day BTS outcome aggregates, the schedule count in [scheduled departure minus 2 hours, scheduled departure) at both horizons, and recent departure/delay features reconstructed from final BTS DEP_DELAY. The schedule feature is not cutoff-anchored, and schedule vintage is unverified. Prior BTS publication and recent-event availability timestamps are also unverified. None of these feature groups is certified for live use.

Historical weather uses the origin-local scheduled departure cutoff converted to UTC. The latest FM15 observation whose timestamp plus 15 minutes is no later than cutoff is selected; weather older than 180 minutes is missing. The buffer is an availability proxy, not a historical receipt time.

The full cohort specification is docs/shared_evaluation_eligibility_2024.md.

The draft Phase 2 mapping and score-export contract is docs/phase2_score_export_contract.md. It is a specification only; no mapping artifact or model scores have been generated or transferred. Previously inspected 2024 and 2025 results remain exploratory; 2025 is not a blind final test.

## Review decisions and remaining blockers

**Yash:**
1. Decide whether this strict retrospective 2024 ID cohort is approved for an exploratory score-alignment phase.
2. Specify an approved secure transfer method if Omruthik needs the ignored membership or exception files. No data have been uploaded or committed.

**Omruthik:**
1. Confirm the exact Airport/Network model bundle, feature-builder version/hash, and horizon-specific inference contract to evaluate.
2. Provide per-row, horizon-tagged Airport/Network features or scores keyed by these canonical IDs using the unique cleaned-to-raw mapping. Include explicit unavailable/missing status; do not fill missing scores with zero.
3. Provide provenance and event/source-availability timestamps for schedule vintage, prior-seven-day records, and recent-event inputs. Without these, results remain retrospective exploratory and unavailable for live-use claims.
4. Confirm target and retrospective eligibility semantics; report the training cohort and any prior inspection of 2024/2025 results.

Do not compute comparative performance or claim live readiness until these decisions and inputs are resolved.

