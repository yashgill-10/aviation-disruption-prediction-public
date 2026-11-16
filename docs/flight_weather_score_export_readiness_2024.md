# Flight/Weather score-export readiness audit (2024)

**Current status (2026-10-21): PASS — new identity-preserving H2 feature tables were joined to the canonical cohort memberships and scored with the existing saved models.** The earlier blocked findings below describe the original saved tables and are retained as audit history; they are superseded for the newly rebuilt July–December 2024 outputs documented at the end. This is not a combined Flight/Weather + Airport/Network evaluation and does not establish live prediction readiness.

## Scope and file locations

This audit used the codex/canonical-flight-audit worktree at base commit 07a7a71 and inspected Flight/Weather artifacts read-only in the original checkout at <project-root>. The audit worktree itself has no data/processed/depdel15 directory; its canonical identity, Phase 2 mapping, and 2024 membership artifacts are separate ignored local data.

The provided mapping is data/processed/phase2_handoff_2024/cleaned_row_to_canonical_mapping.csv.gz, with the previously verified SHA-256 a0b9d6657d3be7148bbf8dc6bed2ff47a6d4301c9bd776c071cee627a0292168. This audit did not regenerate or rehash that mapping.

## Saved Flight/Weather models and protocol

Both saved horizon artifacts loaded in the existing original-checkout environment (NumPy 2.5.3, pandas 3.0.6, scikit-learn 1.9.1, joblib 1.6.0) without deserialization warnings. Each stores an SGDClassifier and a Platt LogisticRegression calibrator, target DepDel15, training years 2022–2023, validation year 2024, and a threshold selected on January–June 2024. The 2025 directory was not read by these model runs according to their saved metrics.

| Horizon | Saved model | Model SHA-256 | Fit rows | 2024 validation rows | Jan–Jun calibration rows | Jul–Dec evaluation rows | Saved threshold | Saved Jul–Dec AP / ROC-AUC / Brier / F1 |
|---|---|---|---:|---:|---:|---:|---:|---|
| T−2h | data/processed/depdel15/weather_models/t_minus_2h/weather_logistic_sgd.joblib | f5b62361189d5d4f388b6e257e2e8ca2a24be3a983075a31e1734292592957f3 | 13,307,795 | 6,982,746 | 3,412,653 | 3,570,093 | 0.2174423 | 0.3608 / 0.6882 / 0.1457 / 0.4065 |
| T−30m | data/processed/depdel15/weather_models/t_minus_30m/weather_logistic_sgd.joblib | a9851df4cd41ba77eec9bb9ed2e0dff8142612c97ea5a6c7ff30b334f76a723a | 13,307,795 | 6,982,746 | 3,412,653 | 3,570,093 | 0.2182222 | 0.3648 / 0.6898 / 0.1454 / 0.4066 |

These values are copied from the saved horizon-specific metrics.json files; no evaluation or model fitting was rerun. The established development protocol is: fit on 2022–2023; fit the Platt calibrator and select the F1 threshold on January–June 2024; evaluate July–December 2024. The July–December 2024 results have already been inspected and are exploratory for a future matched comparison. The project’s 2025 period was previously opened for separate frozen baseline exploration, so it must not be called a blind project-wide final test.

The saved model metadata lists 3 categorical, 17 base numeric, 31 historical, and 26 weather source columns (52 weather-derived numeric inputs). The weather-feature tables are under data/processed/depdel15/weather/{horizon}/flights/{train,validation}/YYYY-MM.parquet; each horizon contains 24 training months and 12 validation months. The saved 2024 validation totals are 6,982,746 rows per horizon.

## Why IDs could not be attached to the original saved tables

The canonical identity is based on nine normalized BTS fields, as defined by ID_COLS in src/audit_canonical_flights_2024.py:

FlightDate, DOT_ID_Reporting_Airline, Reporting_Airline, Flight_Number_Reporting_Airline, OriginAirportID, Origin, DestAirportID, Dest, and CRSDepTime.

The Flight/Weather preprocessing output in src/bts_preprocessing.py retains only these source schedule fields: FlightDate, Reporting_Airline, Origin, Dest, CRSDepTime, CRSArrTime, Distance, CRSElapsedTime, and DepDel15, plus year/month/split metadata. The saved weather-feature table schema likewise contains FlightDate, Reporting_Airline, Origin, Dest, and CRSDepTime, but no flight_id, source archive/member/row ordinal, reporting flight number, DOT carrier ID, or origin/destination airport IDs. DepDel15 is extracted as the target by the existing encode_frame path; it is not a model predictor.

The Phase 2 mapping’s cleaned_input_row_ordinal addresses the original Airport/Network cleaned CSV. No field or saved crosswalk links that ordinal to a Flight/Weather Parquet row. The Flight/Weather preprocessing filters rows and emits a reduced column set; the historical and weather feature builders then perform DuckDB joins/COPY operations without persisting a source ordinal. Parquet row position is not a durable join key. A join on date, carrier code, route, and scheduled time would omit four canonical identity components and has not been shown to be one-to-one; it could conflate codeshares or repeated schedule records. Therefore no canonical flight_id can be assigned to these feature rows from the saved artifacts without inventing provenance.

## Prediction-time feature limits

The horizon-specific weather builder uses the scheduled origin-local departure cutoff converted to UTC, selects FM15 observations whose observation time plus a 15-minute buffer is at or before the cutoff, and treats observations older than 180 minutes as missing. This is an archived-observation availability proxy, not a recorded airline receipt time.

The model also uses BTS-derived prior-three-month and prior-7/30-day delay aggregates. Their builders exclude the target date or use completed months, but BTS publication/availability timestamps are not carried into the feature tables. BTS schedule-vintage timestamps are also absent. Thus the model inputs are suitable for retrospective model scoring under the saved research pipeline, but current artifacts do not prove every schedule or historical feature value was available at its stated live cutoff. Any eventual score export must label this limitation explicitly; it cannot claim live operational validity.

## Canonical cohort context before the H2 rebuild

The Phase 1 membership artifacts define retrospective BTS cohorts, not operationally verified eligibility. Their defensible unambiguous cleaned-to-canonical membership counts are 6,982,525 at T−2h and 6,982,091 at T−30m. These counts cannot be treated as Flight/Weather scored-ID coverage because no Flight/Weather feature row has yet been linked to those IDs. Final DEP_DELAY and cancellation outcomes used by the retrospective eligibility audit are labels/eligibility evidence only and must never enter predictive features.

## Minimum remediation identified at the first audit gate

1. Rebuild or reproduce the Flight/Weather feature rows with durable source provenance attached before filtering: preferably the canonical flight_id, or at minimum the BTS archive checksum, CSV member, and one-based source row ordinal that can be checked against the canonical crosswalk. Carry that key through the historical-feature and weather joins; do not rely on row order.
2. Validate one-to-one row-to-ID mapping, unique IDs, and membership parity independently for each horizon. Keep unresolved identities and unavailable features as explicit statuses; do not coerce missing scores to zero.
3. For a retrospective comparison, use the existing saved horizon models, encoders, calibrators, and thresholds without retraining. Keep January–June 2024 marked as the calibration/threshold-selection period and reserve July–December 2024 for the already-inspected exploratory evaluation. Include source-availability limitations in every export.
4. Before claiming live readiness, obtain schedule-vintage and BTS history publication/availability timestamps, and use actual weather receipt timestamps or retain the archived-weather proxy limitation.

At this point in the audit, no scores, model changes, training, dashboard integration, or comparative performance claims had been produced. The later H2 run below adds inference-only Flight/Weather scores; it does not change those model or comparison boundaries.

## Checks run

- Loaded both saved T−2h and T−30m artifacts read-only; both deserialized with no warnings in the existing environment stated above.
- Inspected model metadata, saved metrics, preprocessing and feature-builder schemas, canonical ID definition, and Phase 1/Phase 2 eligibility documentation.
- Ran 23 existing tests across test_bts_pipeline.py, test_canonical_flight_identity.py, and test_shared_cohort_reconciliation.py; all passed. No new tests were added because the blocker is missing provenance in saved feature rows, and no score-join implementation exists to test safely.

## Identity-preserving pipeline update — 2026-10-21

**Status at the time of this note: code path implemented and focused tests passed; production features and scores had not yet been rebuilt.** The original saved tables in `<project-root>` remain unchanged. The later H2-only rebuild and inference results are recorded below.

### Provenance point and change

The raw 2024 BTS table contains all nine fields used by the canonical `bts-flight-v1` hash. The previous loss happened in `src/bts_preprocessing.py`: it selected only the reduced model columns, filtered rows, and wrote no flight ID or source-row ordinal. The historical builder then projected an explicit set of fields and would have dropped added provenance. The weather builder starts from `f.*` and uses left ASOF weather joins; a mocked test confirms it retains ID/provenance values and the two horizon outputs keep row cardinality for a unique airport map.

The audit branch now has a shared normalizer in `src/canonical_flight_identity.py`, imported by the canonical BTS audit and the Flight/Weather preprocessor. On raw ingestion, preprocessing reads the same nine identity fields and assigns `flight_id` before filtering, retains a one-based `source_row_ordinal` before filtering, and records `source_archive`, its SHA-256, and the exact CSV member. Rows with incomplete or invalid identity remain in the target-eligible dataset with null `flight_id` and `identity_status=unresolved_incomplete_or_invalid`; no identity is guessed. `src/build_historical_features.py` carries the provenance columns in its output without adding them to `feature_columns`. `train_baseline.encode_frame` continues to use its explicit schedule/history/weather lists; a test confirms adding provenance does not change its feature matrix or labels. The saved models and definitions are unchanged.

This makes *newly built* rows traceable and gives both workstreams the same deterministic ID definition. It does not retroactively repair old Flight/Weather Parquet rows. The old saved feature tables still cannot be joined safely by ordinal or the five schedule fields. The H2 run below rebuilt scoped target, history, and weather rows with IDs and verified their provenance and membership parity before scoring.

### Cohort, target, and cutoff gate

The saved model protocol is confirmed by the model metadata and saved metrics: fit 2022–2023, calibrate/select threshold on Jan–Jun 2024, evaluate Jul–Dec 2024. The H2 evaluation has 3,570,093 rows per horizon. The common target remains non-cancelled BTS `DepDel15` (delay of at least 15 minutes). The Phase 1 memberships use final BTS cancellation and actual-departure outcomes to define retrospective eligibility; they do not establish live eligibility.

Retrospective scoring can use the saved model under its original feature semantics only if those source-time limitations are carried in the export. The weather join uses the model's existing scheduled-origin-local cutoff, NOAA FM15 `observation_time + 15 minutes <= cutoff` rule, and 180-minute freshness limit. Actual report receipt time is unavailable. BTS prior-period delay aggregates exclude the target date/month as coded, but BTS publication/availability timestamps are absent. BTS schedule-vintage availability is also not recorded. These gaps prevent a claim that inputs were demonstrably available in a live system at T−2h or T−30m; they do not change the retrospective research interpretation of the saved 2024 evaluation.

### Rebuild cost before scoring

No large run was started. The local 2024 source inventory is 12 raw ZIPs / 354,314,874 compressed bytes; the Jul–Dec subset is six ZIPs / 181,433,840 bytes. The saved original 2024 tables occupy approximately 26.1 MB (base flights), 258.8 MB (expanded history), 409.8 MB (T−2h weather), and 409.6 MB (T−30m weather). These are reference sizes, not estimates of the identity-enriched outputs.

The saved H2 evaluation population is 3,570,093 flights. A canonical ID is 78 characters. If IDs are persisted in the base, historical, T−2h, and T−30m row tables, the ID strings alone represent about 1.114 GB uncompressed (`3,570,093 × 78 × 4`); the source ordinals add about 114 MB uncompressed across those four copies, before source metadata. Actual Parquet/Zstandard size has not been benchmarked. The audit branch now provides a scoped path for this work. `src/prepare_bts_scoring_targets.py` prepares only the selected H2 target months into a new, no-overwrite output directory. `src/build_historical_features.py --target-input-dir ... --target-months 2024-07 ... 2024-12` still reads the existing 20,290,541-row 2022–2024 base history to compute as-of aggregates, but writes only the 3,570,093-row H2 target period. `src/build_ghcnh_weather_features.py --months ...` now validates and writes only those H2 target months for both horizons. This avoids rewriting train and H1 feature outputs; it does not reduce the history aggregation scan itself. Runtime was not benchmarked. The 2024 H2 preprocessing pass hashes each selected ZIP once in addition to streaming its CSV member.

For H2-only output, the ID string payload across base, historical, T−2h, and T−30m tables remains approximately 1.114 GB uncompressed (plus about 114 MB of source ordinals and other metadata); actual Parquet/Zstandard size has not been measured. Existing H2 reference table storage, before added provenance, is approximately half of the 2024 full-year sizes reported above. The scoped path was tested on synthetic inputs and later run against the selected H2 BTS archives. Real-output identity uniqueness, membership-set parity, target parity, and row counts passed before model inference. The new score directory is ignored by Git.

### Tests and workspace state

Focused commands actually run with the existing `<project-root>\.venv` interpreter and `-B`:

- `python -B -m unittest discover -s tests -p test_bts_pipeline.py -v`: 14 passed.
- `python -B -m unittest discover -s tests -p test_canonical_flight_identity.py -v`: 6 passed.

A full `python -B -m unittest discover -s tests -v` run attempted 66 tests but did **not** pass: 3 failures and 13 errors. Most dashboard/provider tests expect ignored local `data/processed/depdel15` assets (station map and saved model artifacts) that are not present in the separate audit worktree. One evaluation-summary test also expected a `period` key not returned by the current code path. No Flight/Weather data directory was created in the audit worktree by the tests. No API request, model training, inference, or score generation was performed.

## H2 Flight/Weather score exports — 2026-10-21

### Scope and provenance

The new ignored output directory is `data/processed/flight_weather_score_exports_2024_h2_v1/`. It contains one gzip CSV per horizon and `run_manifest.json`. The export uses only existing model artifacts, encoders, Platt calibrators, and the thresholds saved with those artifacts. No fitting, refitting, recalibration, threshold selection, API calls, or 2025 data reads occurred.

The source rows were rebuilt under the shared `bts-flight-v1` identity before filtering. The target table preserved the original BTS archive checksum, CSV member, and one-based source-row ordinal; history and weather builders carried those values forward. The H2 cohorts were joined by canonical `flight_id`, then checked for target and source-provenance parity. The unresolved August F9 record has a null ID and is excluded from canonical membership; no guessed ID was used.

### Independently verified outputs

| Horizon | H2 cohort IDs / output rows | Scored | Unavailable | Extra / missing IDs | Threshold | Positive BTS targets | Positive threshold labels | Out-of-support inputs | Output compressed bytes | Compressed SHA-256 | Decompressed bytes | Decompressed SHA-256 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---|
| T−2h | 3,569,958 | 3,569,958 | 0 | 0 / 0 | 0.2174423336982727 | 700,887 | 1,124,188 | 2,540,282 | 279,894,753 | `4a44b8405b41380f46c310f129e819651a496c6e86cbda81d700494f7fcc4d93` | 2,491,928,575 | `973363dcb1789ab107a70efeba6b02b16c7a8dbea45209411bd4ef8c4119194f` |
| T−30m | 3,569,770 | 3,569,770 | 0 | 0 / 0 | 0.2182222455739975 | 700,887 | 1,086,548 | 2,624,136 | 280,836,599 | `5f9950a3a3c98e8032755c3a043886838b6dd08d08033fbfe64a376bed047753` | 2,495,138,809 | `489f15596f71b268baaa3f01afb029915e6fdde8ff10a53864e4189558af4d82` |

For both exports, an independent streaming gzip/hash check and DuckDB validation confirmed readable CSV data, unique non-null IDs, exact H2 membership ID sets, no missing or extra IDs, zero target/horizon mismatches, finite probabilities in `[0,1]`, one consistent saved threshold, and labels equal to `probability >= threshold`. Every row is explicitly marked `retrospective_source_availability_unverified`; the final `DEP_DELAY` field is retained only as eligibility-audit provenance and is not sent to the model encoder.

The model-input support audit marked 2,540,282 T−2h rows and 2,624,136 T−30m rows out of observed training support due to unknown categorical airport inputs. The reported fields were `Dest` (2,539,908 T−2h; 2,623,509 T−30m) and `Origin` (40,125; 45,931), with overlap between these categories. No `Distance` values were missing in these BTS-derived H2 cohorts. These saved models accept unknown one-hot categories, so they returned real model scores, but those rows have unverified reliability; the score is not evidence of live calibration. Weather was missing at the origin for 16,592 T−2h / 16,737 T−30m rows and destination for 16,272 / 16,291 rows; missing-weather indicators remain in the output.

### Reproduction and checks

Inference command (run from the audit worktree with the existing original-checkout virtual environment):

```powershell
& '<project-root>\.venv\Scripts\python.exe' -B src\export_flight_weather_scores_2024.py `
  --feature-root '<project-root>\data\processed\flight_weather_score_prep_2024_h2_v1\weather' `
  --cohort-dir '<project-root>\data\processed\shared_cohort_2024_20261016_v1' `
  --model-root '<project-root>\data\processed\depdel15\weather_models' `
  --output-dir '<project-root>\data\processed\flight_weather_score_exports_2024_h2_v1' `
  --batch-size 50000 --threads 4
```

Recorded runtime versions were Python 3.13.2, NumPy 2.5.3, pandas 3.0.6, scikit-learn 1.9.1, joblib 1.6.0, and DuckDB 1.5.6. The run used batch size 50,000 and four DuckDB threads; these values are also in `run_manifest.json`.

Independent validation command:

```powershell
& '<project-root>\.venv\Scripts\python.exe' -B src\validate_flight_weather_score_exports_2024.py `
  --output-dir '<project-root>\data\processed\flight_weather_score_exports_2024_h2_v1' `
  --cohort-dir '<project-root>\data\processed\shared_cohort_2024_20261016_v1'
```

The first validation attempt compared H2 output against all-year membership and correctly failed the ID-set assertion; after applying the documented July–December filter, the independent validator returned PASS for both horizons. Focused tests passed: 26 tests across the score-export regression tests, BTS pipeline tests, and canonical identity tests. Syntax parsing passed for the export/validation and feature-build scripts, and `git diff --check` passed. The complete suite ran 72 tests: 56 passed, 3 failed, 13 errored. Failures are in existing dashboard/provider tests that require ignored `data/processed/depdel15/weather/station_map.csv` or model/metric artifacts absent from this audit worktree; the saved-evaluation test also expects a `period` key not returned by the current code. No dashboard or provider code was changed for this task.

### Interpretation and remaining work

The 2024 H2 results have already been inspected and remain exploratory. The export is a score-alignment input for a future matched comparison, not a new blind evaluation and not a performance comparison. The thresholds came from Jan–Jun 2024. There are no Airport/Network score exports yet, so no three-way comparison is possible. Retrospective BTS eligibility uses final cancellation and actual-departure outcomes. BTS history/schedule-vintage availability timestamps are unverified, and NOAA uses observation time plus the 15-minute buffer rather than actual receipt time. These exports do not prove operational eligibility or live feature availability. Keep them outside the live dashboard until the Airport/Network workstream supplies compatible, validated scores and its feature-availability evidence.
