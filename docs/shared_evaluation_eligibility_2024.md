# 2024 Shared Evaluation Eligibility

**Status:** eligibility specification prepared from saved Phase 1 outputs; no features or scores were generated.

## Purpose and interpretation

This note defines a proposed matched-flight research cohort for the Flight/Weather and Airport/Network workstreams. A cohort must be constructed independently for T−2h and T−30m, then intersected by canonical `flight_id`. Existing aggregate counts are not themselves matched-cohort counts.

The eligibility rules below use final BTS outcomes to reconstruct whether a flight had departed by a historical cutoff. They define a **retrospective research cohort**, not eligibility that a live system can verify at prediction time.

## Shared target and source filters

- Target: BTS `DepDel15`, where `1` means departure delay of at least 15 minutes and `0` means less than 15 minutes. The reconciliation compared it with `DEP_DELAY >= 15`; it found zero mismatches in the supplied cleaned rows.
- Primary label cohort: `Cancelled == 0`, a valid binary `DepDel15`, and a valid `FlightDate` in the source month. Cancelled flights and rows with missing or non-binary labels are excluded.
- `DepDel15`, `DEP_DELAY`, actual departure time, cancellation outcome, and other post-flight fields are labels or retrospective eligibility evidence only. They must not be predictor inputs.
- A valid cross-pipeline row requires an unambiguous source-to-cleaned match and a recovered versioned canonical ID. Do not infer an ID from route and time alone or merge codeshares.

## Time zone, cutoff, and retrospective eligibility

`FlightDate` and BTS `CRSDepTime` describe the scheduled departure at the origin airport. The Flight/Weather pipeline forms the scheduled departure in the origin airport's local time zone, subtracts the horizon, and converts the cutoff to UTC for weather matching. Any shared implementation should resolve the origin's IANA time zone, use one consistent daylight-saving-time rule, and retain both the local schedule and UTC cutoff for audit.

The saved Airport/Network retrospective audit expresses eligibility using final `DEP_DELAY` rather than an operational status snapshot. Since actual departure time is scheduled departure plus final departure delay:

| Horizon | Retrospective rule | Meaning at the cutoff |
| --- | --- | --- |
| T−2h | `Cancelled == 0` and `DEP_DELAY > -120` | Actual departure was strictly after scheduled departure minus 120 minutes. |
| T−30m | `Cancelled == 0` and `DEP_DELAY > -30` | Actual departure was strictly after scheduled departure minus 30 minutes. |

The inequality is strict: a flight whose actual departure equals the cutoff is excluded. Cancellation and `DEP_DELAY` are final BTS outcomes, so neither rule can establish live eligibility. A live scorer would need a timestamped operational status showing the flight had not departed by that cutoff; no such source was validated here.

### Reconciling the reported counts

The counts below refer to different units and filters. In particular, equal totals do not prove equal row membership.

| Count | Rows | What it counts |
| --- | ---: | --- |
| 2024 raw BTS source | 7,079,061 | All monthly source records before target filtering and identity quarantine. |
| Flight/Weather target-eligible source rows | 6,982,746 | Non-cancelled, valid binary `DepDel15`, in-month records; not a horizon-specific or canonical-ID count. |
| Earlier raw-source T−2h retrospective cohort | 6,982,746 | Records meeting the retrospective cutoff rule; this total includes the identity-incomplete F9 record. Its equality with the Flight/Weather total is numeric only. |
| Earlier raw-source T−30m retrospective cohort | 6,982,312 | Records meeting the stricter retrospective cutoff rule; includes the same identity-incomplete F9 record. |
| Canonical table | 7,079,060 | Raw BTS records assigned `bts-flight-v1` IDs. One record was quarantined because its reporting flight number is blank. |
| Canonical-ID T−2h retrospective cohort | 6,982,745 | The saved identity audit's horizon count over rows with valid IDs. |
| Canonical-ID T−30m retrospective cohort | 6,982,311 | The saved identity audit's horizon count over rows with valid IDs. |
| Supplied cleaned Airport/Network input before deduplication | 6,982,766 | Original cleaned CSV rows, including exact duplicate copies. |
| Supplied cleaned input after exact-row deduplication | 6,982,656 | Cleaned distinct rows after removing 110 duplicate copies from 110 groups. |

The one-row difference between each raw-source horizon total and its canonical-ID horizon total is the quarantined F9 MIA→ATL record on 2024-08-25, scheduled 06:00. Its `Cancelled=0`, `DepDel15=0`, and `DEP_DELAY=-6`, so it satisfies both retrospective cutoff rules, but its reporting flight number is blank and no canonical ID was assigned. It remains unresolved; no replacement value is inferred.

The cleaned-input match breakdown is `6,982,525` unique rows with recovered IDs, `1` unique exact match with incomplete identity, `110` ambiguous deduplicated cleaned rows, and `20` cleaned rows with no exact raw match. These sum to `6,982,656`. In the original, pre-dedup crosswalk, each of the 110 ambiguous groups has two original rows, so statuses are `6,982,525 + 1 + 220 + 20 = 6,982,766`. The 220 are candidate source records in ambiguous groups, not 220 additional cleaned groups.

The canonical-ID audit reports 434 fewer rows at T−30m than at T−2h. Under its strict rules, these are flights whose final actual departure was after T−2h but at or before T−30m. This is a retrospective outcome-derived difference.

### Proposed common cohorts

Define each horizon cohort separately:

```text
C_2h = IDs with one recovered canonical identity and one unambiguous cleaned match,
       valid/matching DepDel15 labels, Cancelled == 0, and final DEP_DELAY > -120;
C_30m = the same conditions, with final DEP_DELAY > -30.
```

Then define the matched scoring cohort for horizon `h` as `C_h` intersected with the set of flight IDs for which both saved models produce a valid score from their required features for that same horizon. Report the denominator and each model's coverage separately. Missing network features or scores must be marked unavailable and excluded from a paired metric; never replace them with zero. Rows without a recovered ID, including ambiguous, unmatched, and identity-incomplete rows, cannot enter an ID-based comparison.

At the time this initial note was written, the saved artifacts did not report horizon-by-horizon ID intersections. The 2026-10-16 update below adds the row-level intersections and defensible membership counts. The paired model-scored cohort remains unknown because horizon-specific scores from both models are still unavailable; do not claim every recovered ID was scored.

## Weather and feature availability

### Flight/Weather weather rule

For each horizon, the existing weather builder derives the cutoff from scheduled departure in the origin airport's local time zone and converts it to UTC. For NOAA GHCNh FM15 observations, it uses the latest report satisfying:

```text
report_available_at = observation_timestamp_utc + 15 minutes
selected report_available_at <= prediction_cutoff_utc
report older than 180 minutes -> weather values missing
```

The 15-minute addition is a conservative availability proxy, not an archived receipt timestamp. GHCNh is a quality-controlled historical archive and does not prove when an airline first received a report. Consequently, the historical weather join is a point-in-time assumption, not a perfect replay of operational availability. A live feed should use its actual returned/receipt timestamps. Origin and destination observations use the same origin-departure cutoff; destination observations are not arrival forecasts.

Flight/Weather's prior-delay aggregates use earlier BTS outcomes. Its documented daily windows use completed days and exclude the target flight's entire calendar date; three-month features use completed calendar months. BTS publication/availability timestamps are not supplied as part of this audit, so these historical outcome features also lack proof of availability at each prediction cutoff.

### Airport/Network feature groups

The previously audited Airport/Network builder defines `scheduled_origin_departures_prior2h` as scheduled origin departures in `[scheduled departure − 2 hours, scheduled departure)`. This window is anchored to the target's **scheduled departure**, and the same definition is used for T−2h and T−30m. It is not a count of only departures before the prediction cutoff: it may include flights scheduled after that cutoff. It can be interpreted as schedule volume only if the relevant schedule vintage was available by the cutoff. Schedule-vintage timestamps were not verified, so this feature is not certified for live use and must not be described as observed prior airport activity.

The Airport/Network prior-seven-day aggregates and recent airport-event features are BTS-derived. Their row construction may be retrospective, and no source-availability/event timestamps were established to prove that each contributing record was known by T−2h or T−30m. They remain offline research features pending timestamp lineage and a point-in-time reconstruction. Any other operational, aircraft-rotation, or propagation input likewise remains unavailable for live claims until the source record, event time, ingestion/availability time, and cutoff filter are documented.

## Handoff index and audit status

These generated tables are ignored local artifacts in the `codex/canonical-flight-audit` worktree. They have not been uploaded or committed; Omruthik cannot access them from GitHub yet.

| Artifact | Path relative to this worktree | Verified count/status |
| --- | --- | ---: |
| Canonical table | `data/processed/canonical_identity_2024_20261015_v1/canonical_flights.csv.gz` | 7,079,060 rows; unique IDs; zero duplicate-ID groups and zero hash-collision groups. |
| Cleaned-to-canonical crosswalk | `data/processed/canonical_identity_2024_20261015_v1/cleaned_to_canonical_crosswalk.csv.gz` | 6,982,766 original cleaned rows; statuses reconcile as above. |
| Identity quarantine | `data/processed/canonical_identity_2024_20261015_v1/identity_quarantine.csv` | 1 row: unresolved F9 record with blank reporting flight number. |
| Ambiguous cleaned matches | `data/processed/canonical_identity_2024_20261015_v1/ambiguous_cleaned_matches.csv` | 110 deduplicated cleaned groups; 220 original rows in crosswalk status counts. |
| Unmatched cleaned rows | `data/processed/canonical_identity_2024_20261015_v1/unmatched_cleaned_rows.csv` | 20 deduplicated cleaned rows. |
| Identity conflicts | `data/processed/canonical_identity_2024_20261015_v1/identity_conflicts.csv` | 0 conflict rows. |
| Run report and checksum | `data/processed/canonical_identity_2024_20261015_v1/canonical_identity_run_report.json` and `.sha256` | PASS; report SHA-256 `419aebfb91575420755c21b5cc559f01426f75229391b42f67e9c65c2d580117`. |
| Reconciliation report | `data/processed/airport_network_reconciliation_2024_20261012_retry1/audit_report.json` | Raw/cleaned row matching and target parity evidence; no horizon-by-clean-crosswalk intersection. |
| Horizon audit report | `data/processed/canonical_audit_2024_20261012/audit_report.json` | ID-valid retrospective counts: 6,982,745 at T−2h; 6,982,311 at T−30m. |
| Canonical builder and finalizer | `src/build_canonical_flight_table_2024.py`; `src/finalize_canonical_flight_table_2024.py` | Existing output build/finalization code; no source scan rerun for this note. |
| Focused identity tests | `tests/test_canonical_flight_identity.py` | 6 tests passed in the saved run report and on 2026-10-16 rerun. |

Current audit worktree base is commit `07a7a71f37ca302fdd11c64c81aa06b4c54f3b9e`, branch `codex/canonical-flight-audit`. The canonical build has no separate commit; its builder, finalizer, audit script, tests, and audit documents are untracked worktree files. This eligibility document is also a local worktree change until reviewed and committed. The original `feature/flight-weather-ml` checkout and `main` were not changed.

## Remaining blockers before matched-flight scoring

1. Obtain horizon-tagged row-level model scores or feature-ready outputs from both workstreams keyed to the defensible canonical IDs. The paired score cohort and comparative metrics remain unknown.
2. Establish source-availability evidence for Airport/Network features, including schedule vintages, BTS publication timing, and recent-event ingestion timestamps. Until then these features are retrospective and unavailable for live-use claims.
3. Keep previously inspected 2024 and 2025 results exploratory; 2025 is not a blind final test.
4. Transfer the ignored local membership and input artifacts through an approved channel before another contributor can review or reproduce them.

## Validation performed for this document

- Counts and status totals were checked against the saved reconciliation report, canonical identity run report, horizon audit, the new row-level cohort report, and existing feature-builder/weather source.
- The saved SQLite database was read-only. No monthly BTS archive was reopened, no index was built, and no canonical table was rebuilt.
- Focused tests passed: 5 shared-cohort tests and 6 canonical-identity tests.
- Both membership gzip files were fully decompressed; output row counts, SHA-256 hashes, report sidecar, checksum manifest, and ID-set arithmetic all verified.
- No feature generation, inference, model scoring, training, or full project suite was run.
## Validation performed for this document

- Counts and status totals were checked against the saved reconciliation report, canonical identity run report, prior Phase 1 eligibility note, and existing code. No BTS ZIP scan, index rebuild, canonical rebuild, feature generation, inference, model scoring, or training was performed.
- The canonical identity test module was rerun; see the final task report for its exact result.
- This document specifies a proposed research cohort. It does not certify live readiness or establish comparative model performance.


## Verified cohort reconciliation update — 2026-10-16

This update supersedes the earlier statements in this document that the horizon ID intersection had not been generated and that the PR source could not be accessed. The retained SQLite reconciliation database was read in read-only mode; no BTS archives were reopened and no indexes were built.

### Horizon results

| Measure | T−2h | T−30m |
|---|---:|---:|
| Raw eligible source records, including identity-incomplete F9 | 6,982,746 | 6,982,312 |
| Records with valid canonical IDs | 6,982,745 | 6,982,311 |
| Unambiguous cleaned-to-raw matches with IDs | 6,982,525 | 6,982,091 |
| Ambiguous raw candidates / cleaned groups | 220 / 110 | 220 / 110 |
| Identity-incomplete F9 record | 1 | 1 |
| Eligible raw source rows with no cleaned match | 0 | 0 |
| Defensible membership rows written | 6,982,525 | 6,982,091 |

The 20 cleaned rows without a raw match are a separate cleaned-input exception; they cannot be assigned to either horizon. The previous horizon totals included the quarantined F9 row. The corrected valid-ID totals are therefore 6,982,745 and 6,982,311.

The ID intersection was counted row by row against both strict final-DEP_DELAY rules. All valid raw-cohort IDs: T−2h 6,982,745; T−30m 6,982,311; intersection 6,982,311; T−2h-only 434; T−30m-only 0. The unambiguous cleaned-to-raw ID sets: T−2h 6,982,525; T−30m 6,982,091; intersection 6,982,091; T−2h-only 434; T−30m-only 0. The 434 T−2h-only flights have final DEP_DELAY in (-120, -30]. The global ID audit found no duplicate ID groups or SHA-256 collisions.

The exception file has 462 rows: 220 ambiguous records at each horizon, one F9 identity-incomplete record at each horizon, and 20 cleaned-only unmatched rows. The membership files contain no ambiguous or unmatched IDs. The final paired model-scored cohort remains unknown because no row-level scores from both workstreams were available; no model scoring was run.

### Local outputs

All outputs are ignored and local to the audit worktree; they have not been uploaded or committed.

| File | Rows | SHA-256 |
|---|---:|---|
| data/processed/shared_cohort_2024_20261016_v1/t_minus_2h_membership.csv.gz | 6,982,525 | ddc2d679bb6c93fdc1ce6758ccee570b8110dbf726d05febc7e75d714d2ae1be |
| data/processed/shared_cohort_2024_20261016_v1/t_minus_30m_membership.csv.gz | 6,982,091 | 988639c03df4213b448fa509f0999d35c74c372be5a907e460d0ac149748add3 |
| data/processed/shared_cohort_2024_20261016_v1/cohort_exceptions.csv | 462 | 15959c16bc950eb4dcfc94fa77085f665c47e31ca165e31cbf5d68d13558c7b2 |
| data/processed/shared_cohort_2024_20261016_v1/cohort_reconciliation_report.json | — | f0fee0cca4617a850c05f2508a3e0a824ba99f582f7fd1c109bdb0ac5e24379c |

Independent verification decompressed both membership files and counted CSV rows, checked all listed SHA-256 values against the report and checksums manifest, verified the report sidecar, and checked ID-set arithmetic. All passed.

### Airport/Network source check

The current source was fetched read-only from PR #1 at head 55481a8607d472b310604ea9f133846c288e0061, file airport-network/airport_network_feature_builder.py. PR #1 remains open and draft. The source defines:

- Prior-seven-day BTS aggregates: airport_prior7_flights, airport_prior7_delay_rate, airport_prior7_mean_delay_min, airport_prior7_avg_destinations_per_active_day. Daily aggregates use shift(1).rolling(7), excluding the target date, but BTS publication/availability times are unverified.
- scheduled_origin_departures_prior2h: schedule count in [scheduled departure − 2 hours, scheduled departure), for both horizons. It is anchored to scheduled departure and may include schedules after the prediction cutoff. Schedule vintage is unverified.
- Horizon-specific recent-departure, delay-rate, and mean-delay features reconstructed using final BTS DEP_DELAY. Their event/source-availability times are absent; the builder marks them not live-feed safe.

These are retrospective features. Neither successful ID alignment nor saved-model deserialization proves point-in-time availability. The exact weather as-of rule and 15-minute observation buffer remain as documented above and in docs/weather_data_plan.md.

See docs/phase1_handoff_2024.md for input/output paths, checksums, tests, current worktree status, reproducibility limits, and review decisions.


