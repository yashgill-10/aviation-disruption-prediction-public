# Airport/Network ML — integration handoff

This is the authoritative handoff for the Airport/Network workstream. The repository PR branch is the source of truth for source code and documentation; the ZIP is a distribution package generated from that branch plus the Colab model/evaluation artifacts. It does not include the Flight/Weather implementation.

> **Status update (2026-11-16):** Written before joint integration completed. Canonical-key Airport/Network score exports have since been produced and validated locally with full coverage of the retrospective eligible cohorts, the matched October–December component comparison and the November–December M3 stacking evaluation have been run, and the M3 implementation is published under `m3/`. Statements below about pending canonical coverage, blocked handoffs, or outstanding combined-model evaluation describe the state at the time of writing. Live inference for Airport/Network and M3 remains unvalidated; see `docs/PROJECT_COMPLETION_STATUS.md`.

## Model bundle compatibility

The compressed pickle payload of `aviation_airport_network_model_bundle.joblib` contains scikit-learn's embedded `_sklearn_version` tag `1.6.1`. The PR requirements now pin `scikit-learn==1.6.1`; loading a model saved by another scikit-learn version is unsupported and can emit an `InconsistentVersionWarning`.

The original Colab run recorded Python 3.13 and pandas 2.2.3, but its exact NumPy and joblib versions remain unknown. The bundle was created with `joblib.dump(..., compress=3)`.

**Deserialization verification: PASS (2026-10-12).** The package ZIP from PR head `d595ff63aa44885f576798ddc68873742ca8d683` matched its external sidecar before extraction (tested ZIP SHA-256: `2be938048f58c9955e5005dac94dc3d35ab380ef6072f423d104a8f293000d47`). The existing bundled estimator loaded with `joblib.load()` without warnings. The tested environment was isolated Python 3.13.2 (64-bit Windows), NumPy 2.5.4, pandas 2.2.3, scikit-learn 1.6.1, and joblib 1.6.0. The bundle contained both T−2h and T−30m `sklearn.pipeline.Pipeline` estimators. This was a deserialization-only check; no inference or retraining was run. The temporary environment had no pip module, so installed versions were recorded from distribution metadata rather than `pip freeze`. The original training-time NumPy/joblib versions remain unknown.

The package was rebuilt only to add this verification record; its embedded manifest and external sidecar were regenerated. The model bundle bytes are unchanged (SHA-256: `9dca5d3bb2e8e2af0cf9bca3a2245584b83b10e2cfb0e883fb90388fd0b87b5a`). The rebuilt ZIP's checksum is recorded in the adjacent external sidecar.

To reproduce a version-compatible load check after extracting the package, use:

This PASS establishes deserialization compatibility only for the exact environment above. It does not validate feature construction, inference outputs, evaluation quality, or live readiness. Do not retrain or reserialize to address a serialization-environment mismatch; if a later load fails, capture the complete traceback and all tested package versions.

## Prediction contract

- **Target:** `delay_15 = 1` when `DEP_DELAY >= 15` minutes, for non-cancelled flights. Evaluation also excludes flights already departed by the relevant cutoff using retrospectively reconstructed BTS data.
- **Horizons:** T−2h and T−30m before scheduled departure.
- **Inputs:** schedule/route fields plus prior-seven-day airport history, scheduled prior-two-hour airport volume, and horizon-specific reconstructed recent origin-airport state. See `Airport_Network_Feature_Schema.md`.
- **Output:** per-flight delay probability and thresholded label from `predict_airport_network_delay(feature_ready_flights, bundle, horizon=...)`.
- **Join:** use the same canonical `flight_id` in both team tracks and the same horizon. The PR's `attach_airport_network_predictions` score-handoff adapter is authoritative and must be used; the ZIP copy must be byte-for-byte sourced from the PR branch.
- **Canonical-ID export:** `airport_network_prediction_export.py` exports one row per canonical ID and horizon, with score coverage and an explicit reason for each unavailable score. It requires a reviewed canonical table and an explicit per-horizon eligibility table; it never generates replacement IDs or fills missing scores with zero. See `Prediction_Export_Contract.md`.

## Data cleaning and deduplication status

The notebook does **not** recreate `flights_2024_cleaned_verified.csv.gz`. Its cells expect that archive as input (77,226,559 bytes), decompress it to `flights_2024_cleaned_verified.csv` (407,151,184 bytes), and then load it. The verified compressed SHA-256 is `18a78b549736d2498c24cb625bdeef8be137aa710ab7b80e8a906dd204571445`; the uncompressed CSV SHA-256 is `01a4b75de0615063b24e31edda69d81730059199e76ccea45b37ca9d00379bf1`. The recorded run found 6,982,766 rows, 0 missing values in required modeling fields, and 110 exact duplicate rows; deduplication removes them in memory, leaving 6,982,656 modeling rows. The notebook now assigns `cleaned_input_row_ordinal` as the 1-based data-record position immediately after reading the CSV and before filtering/deduplication. Duplicate detection excludes that key, and removed duplicate ordinals are saved separately for quarantine. The input checksum match establishes that the source bytes and original row order match the crosswalk source; filtered or deduplicated frames must still carry this original key unchanged. The notebook checks 2024 dates, binary target, and `delay_15 == (DEP_DELAY >= 15)`, then normalizes `CRS_DEP_TIME=2400` to 0000 to derive scheduled hour/minute.

The source BTS download manifest/URLs, combined-file construction script, exact gzip command/options, and hashes were not saved with the artifacts. Therefore the compressed dataset cannot yet be reproduced exactly from this handoff. To close this gap, preserve the original BTS source archives and manifest, the cleaning/concatenation script, and SHA-256 checksums for both the CSV and gzip. Do not treat `flights_2024_cleaned.csv` as an independently verified raw BTS source.

Original training-time runtime: Colab recorded Python 3.13 and pandas 2.2.3; exact original NumPy and joblib versions remain unknown. Validation runtime on 2026-10-12: Python 3.13.2 (64-bit Windows), NumPy 2.5.4, pandas 2.2.3, scikit-learn 1.6.1, and joblib 1.6.0. The temporary validation venv omitted pip; package versions were recorded through installed distribution metadata.

## Exact training, threshold selection, and test procedure

Notebook `01_data_exploration_clean_colab_export.ipynb`: run cells **2 → 4 → 5 → 8 → 27 → 28** for this workstream.

1. Cell 4 gunzips the supplied archive and asserts the output is exactly 407,151,184 bytes.
2. Cell 5 reads the combined CSV with pandas; checks required columns/date range/target, filters rows missing modeling inputs (none in the recorded run), casts compact dtypes, removes 110 exact duplicate rows in memory, and checks the target identity.
3. Cell 8 maps scheduled time 2400 to 0000 and derives `DEP_HOUR`/`DEP_MINUTE`.
4. Cell 27 builds prior-seven-calendar-day origin features (target date excluded), scheduled prior-two-hour airport counts, and recent same-origin event summaries. Recent departures are retrospectively reconstructed as scheduled local time plus final BTS `DEP_DELAY`. For each horizon independently, the model excludes cancelled flights and those already departed at the cutoff.
5. The classifier is a `ColumnTransformer` + one-hot encoder and `RandomForestClassifier(n_estimators=50, max_depth=12, min_samples_leaf=10, class_weight="balanced_subsample", n_jobs=-1, random_state=42)`. Fit on up to 200,000 stratified eligible flights from Jan–Aug. Stratified samples use NumPy `default_rng(42)` for fitting and `default_rng(143)` for September calibration. The fixed eligibility counts differ slightly by horizon because flights already departed by cutoff are removed.
6. Select each horizon/model threshold on up to 200,000 stratified September flights by maximizing F1 from the precision-recall curve. Keep that threshold fixed for Oct–Dec evaluation. The Oct–Dec eligible flights are all scored in chunks; this period has already been inspected and is retrospective/exploratory, not a blind final test.
7. Cell 27 writes `aviation_airport_network_results.csv`, `aviation_airport_error_by_origin.csv`, `aviation_airport_feature_importance.csv`, and `aviation_airport_network_model_bundle.joblib`. Cell 28 runs one T−30m inference smoke example and creates the four-file `aviation_airport_network_artifacts.zip`.

The bundle records fitted estimators and thresholds; it does not include row-level test predictions, target labels, calibration scores, confusion matrices by flight, or feature-ready partitions. Those per-flight evaluation artifacts were not saved in the available Colab ZIP. The three aggregate evaluation CSVs are the complete saved model-evaluation tables currently available; the fifth file is a join diagnostic. A canonical-ID export can be produced with the preserved estimator once the joint table and eligibility decisions are ready, but it cannot recreate the unavailable historical row-level feature table or prove those records were operationally scoreable. Never use inspected Oct–Dec results as an untouched final estimate.

## Canonical-ID reconciliation status

The separate BTS reconciliation audit reported 6,982,526 unique one-to-one cleaned-to-raw matches, 110 cleaned rows with ambiguous matches to 220 eligible BTS rows, and 20 unmatched cleaned rows. The remaining 2024-08-25 F9 MIA→ATL 06:00 identity has a blank reporting flight number and must stay quarantined unless an authoritative correction is found. The reports do not create the canonical table or crosswalk. The team must review and approve the exception/quarantine rules before creating durable IDs. Do not describe that new table as a bit-for-bit recreation of the upstream cleaned file.

The audit reports were recorded under a worktree path that is not available in this handoff environment. The approximately 20.3 GB SQLite scratch database was reported as still present; its process-use state and cleanup safety could not be checked here. Do not rerun the full reconciliation or delete the scratch database until its owner verifies the reports and confirms no process is using it.

## Results and limits

The available results are listed in `Airport_Network_ML_Summary.md` and the aggregate CSV. They use 2024 Oct–Dec. BTS has final outcome fields but no event ingestion/publication timestamps. Thus recent state and aircraft propagation are retrospective proxies; feature event availability before prediction cutoffs is not proven. The combined Flight/Weather + Airport/Network evaluation and an untouched future holdout remain outstanding.

## PR and ZIP status

Keep PR #1 open and draft, based on `main`; do not merge. The PR branch is authoritative for builder, inference, score-handoff adapter, requirements, tests, and documentation. The ZIP is rebuilt from those exact PR-branch files (including the shared `src/canonical_flight_identity.py` validator), the package verifier, and the four Colab artifacts. The corrected distribution package is `aviation_airport_network_team_handoff.zip`; it includes this handoff, the PR source snapshot, the Colab notebook export and saved artifacts. Its external checksum is provided in `aviation_airport_network_team_handoff.zip.sha256`. The package was rebuilt after the `bts-flight-v1` export-boundary validation correction; earlier ZIP snapshots predate that fix.

## Smoke-sample correction (2026-11-02)

The old packaged `sample_scored_record.csv` used a placeholder `flight_id=0` and schedule fields `DEP_HOUR=8`, `DEP_MINUTE=0`. The verified cleaned source's first record has `CRS_DEP_TIME=8`, which the notebook parses as `DEP_HOUR=0`, `DEP_MINUTE=8`. The corrected sample uses `cleaned_input_row_ordinal=1` and source-consistent feature values. The existing T−30m bundle/helper returns probability `0.32188825709718105`, exactly matching the saved notebook smoke score. The defect was in the sample fixture; the model was not retrained or reserialized.
