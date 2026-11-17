# Airport/Network and M3 integration handoff

This document describes the public source interfaces and the existing retrospective evidence. It does not distribute the private datasets, model bundle, canonical mapping, or row-level score exports. T−2h and T−30m are separate horizons throughout.

## Public implementation and prediction contract

- `airport_network_feature_builder.py` contains the currently available reference feature builder.
- `airport_network_inference.py` exposes horizon-aware saved-model inference.
- `airport_network_integration.py` validates canonical `bts-flight-v1` IDs and joins component scores without changing row order or silently accepting missing/extra/duplicate keys.
- `airport_network_prediction_export.py` writes one row per eligible canonical ID or validated pre-mapping `cleaned_input_row_ordinal`, with explicit `scored`/`unavailable` status and an unavailable reason. Missing predictions are never converted to zero.
- `Airport_Network_Feature_Schema.md` and `Prediction_Export_Contract.md` define feature and export semantics.

The export/inference interfaces and synthetic fixtures can be checked with the commands in `README.md`. They do not load the private saved estimator or reproduce millions of historical scores.

## Builder provenance

The Airport/Network run manifest records scoring-time builder SHA-256 `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36`; its contemporaneous available-builder field is `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf`. The exact original builder source could not be recovered from the public Git history, available local Git objects, or the saved notebook cells. The current public reference builder has Git-blob-content SHA-256 `86b690c7151ceecda0d7a067dbd3de07ad4f53d8bedb595d1740633ffe57d0f4`, a third hash. It is not byte-identical evidence for the builder that generated the saved scores. The notebook contains related feature logic but does not recover that original file. The discrepancy prevents a claim of full feature-generation code reproducibility.

## Original Airport/Network training/evaluation record

The available Colab record describes `delay_15 = 1` for departure delay of at least 15 minutes among non-cancelled flights. It trained separate Random Forest models for T−2h and T−30m using up to 200,000 stratified Jan–Aug examples, selected each operating threshold by F1 on up to 200,000 stratified September examples, and evaluated October–December. The recorded estimator used a one-hot/column-transformer pipeline with `RandomForestClassifier(n_estimators=50, max_depth=12, min_samples_leaf=10, class_weight="balanced_subsample", n_jobs=-1, random_state=42)`. Complete candidate/hyperparameter/model-family selection history was not recovered.

The model excluded cancelled flights and flights whose reconstructed actual departure was at or before the horizon cutoff. Those are final BTS outcomes and define a retrospective cohort, not live eligibility. The 2024 source file identity and its cleaned-input ordinal mapping are documented in the private handoff; the public repository deliberately excludes those row-level files.

## Canonical mapping and completed comparisons

The Phase 2 mapping preserves the original 1-based `cleaned_input_row_ordinal` and assigns no ID to ambiguous, incomplete, or unmatched records. It mapped 6,982,525 of 6,982,766 source rows uniquely; 220 ambiguous, one identity-incomplete, and 20 unmatched rows remain without canonical identity. The canonical retrospective memberships are 6,982,525 IDs at T−2h and 6,982,091 at T−30m. The Airport/Network canonical exports were validated with full coverage of each respective cohort.

The matched Flight/Weather versus Airport/Network component comparison is complete for the exploratory Oct–Dec 2024 period: 1,768,340 common scored flights at T−2h and 1,768,222 at T−30m. The 39-row difference from each ordinal-keyed error analysis is due to unresolved ambiguous records with no canonical ID; they were not assigned inferred IDs. M3 retrospective stacking is also complete: September meta-training, October threshold selection, and November–December exploratory evaluation. Results and exact metrics are in `../docs/PROJECT_COMPLETION_STATUS.md`, `../reports/final_joint_exploratory_research_report.md`, and `../m3/`.

## M3 provenance and limitations

The genuine M3 script was recovered from the private final-submission artifact package and sanitized for public use by parameterizing external input paths. The public implementation is `../m3/train_evaluate_m3.py`; `../m3/m3_run_manifest.json` records the original experiment source hash and source artifact hashes. The September row-level meta-training ID list was not saved. The script serialized the M3 model artifacts after calculating evaluation predictions and metrics; no refit during November–December is documented, but no pre-evaluation serialized model hash is established. The public checkout can run synthetic contract tests, but the data-dependent experiment cannot be rerun without private component score/feature/target inputs.

## Feature availability and scientific scope

Scheduled origin volume is defined over `[target scheduled departure − 2 hours, target scheduled departure)` at both horizons. That interval is target-schedule anchored, not cutoff-anchored. Schedule-vintage timestamps are unavailable. Prior-seven-day airport aggregates and recent activity are derived or reconstructed from final BTS outcomes; BTS publication/ingestion and event-availability timestamps are missing. Timestamped live departure/cancellation eligibility is also not established. Flight/Weather weather uses an observation-time-plus-15-minute proxy rather than verified receipt times.

The results are retrospective, already inspected and exploratory. October–December is not a blind final test; 2025 was also inspected. No significance or causal claim is supported. Airport/Network and M3 live inference are unvalidated. The website presents saved research results and is not a live Airport/Network or M3 scoring service.

## Historical package note

Earlier sections of this handoff referred to private ZIP distribution and an open draft PR. Those were historical workflow details and are not the state of this sanitized public release. Use the current repository files and checksum manifest as the public source; do not expect private artifacts to be present here.
