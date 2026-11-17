# Aviation Disruption Prediction — Joint Exploratory Research Report

**Status: final exploratory report; published in the public source repository.** This report consolidates existing Flight/Weather, Airport/Network, and M3 artifacts. It does not describe a live operational system, a causal feature effect, or a blind final test. The saved results are retrospective and exploratory.

## Team and workstreams

- **Yash Gill:** Flight/Weather
- **Omruthik Nakkalapally:** Airport/Network
- **Joint:** common evaluation protocol, matched interpretation, and M3 review

## Research question and scope

How do the existing Flight/Weather and Airport/Network scores compare in ranking and classifying BTS departure delays at T−2h and T−30m on common 2024 flights, and how does a logistic stack of their continuous probabilities compare in the exploratory November–December evaluation?

The target is `DepDel15`: 1 when final BTS departure delay is at least 15 minutes among non-cancelled flights. Canonical identity uses the project’s `bts-flight-v1` flight ID. The work compares saved retrospective predictions; it does not establish real-time feature availability or operational readiness.

This is a matched comparison of different pipelines, **not a controlled feature ablation**. Flight/Weather and Airport/Network differ in estimator family, training population, feature engineering, and operating-threshold selection period. Performance differences therefore cannot be attributed exclusively to the addition of a feature group.

## Data, identity, and eligibility

The cleaned 2024 source contains 6,982,766 data rows. The canonical mapping resolves 6,982,525 rows uniquely; 220 rows are ambiguous, one is identity-incomplete, and 20 are unmatched. These exceptions were preserved rather than assigned inferred IDs.

The retrospective eligibility rules use final BTS outcomes:

- T−2h: `CANCELLED == 0` and `DEP_DELAY > -120` minutes.
- T−30m: `CANCELLED == 0` and `DEP_DELAY > -30` minutes.

These rules define research cohorts using final outcomes; they do not establish whether a flight was still eligible at a live prediction cutoff.

### The 39 ambiguous October–December rows

The earlier ordinal-keyed Airport/Network evaluation contains 1,768,379 T−2h rows and 1,768,261 T−30m rows. The canonical matched component comparison contains 1,768,340 and 1,768,222, respectively. The same 39 source rows account for the difference at both horizons. They were scored, non-cancelled, eligible at both cutoffs, and had matching targets, but their identity status is `ambiguous_multiple_raw_candidates`. They have no defensible canonical `flight_id`; they are reported as scored but unjoinable and excluded from the canonical matched comparison. They are not missing model scores.

The original 1-based cleaned-input row ordinals are:

```text
5273454, 5273804, 5305152, 5357192, 5418208, 5538025, 5546481, 5579766,
5624563, 5628256, 5670255, 5677655, 5692480, 5789670, 5884813, 5892185,
5897592, 5924385, 5940879, 6076023, 6142706, 6168228, 6204909, 6245358,
6348865, 6384443, 6392121, 6421953, 6446506, 6474110, 6504376, 6605535,
6616386, 6624980, 6729063, 6737112, 6737131, 6795736, 6926307
```

## Model methodology and chronology

### Flight/Weather

The saved horizon-specific `SGDClassifier` artifacts were trained on 2022–2023. A Platt `LogisticRegression` calibrator and the original component thresholds were fit/selected on January–June 2024. September scores used the saved classifier and calibrator without refitting. Applying that previously fitted calibrator to September is out-of-sample relative to calibration fitting and is not, by itself, calibration leakage. The complete estimator hyperparameter, feature-selection, and model-family selection history was **not recovered**. Calibration and operating-threshold selection are separate procedures; the available history does not establish how estimator families, hyperparameters, or features were selected.

### Airport/Network

The saved horizon-specific pipeline uses column preprocessing and a `RandomForestClassifier`: 50 trees, maximum depth 12, minimum leaf size 10, `class_weight="balanced_subsample"`, and random state 42. The documented fit used a stratified sample of up to 200,000 eligible January–August 2024 flights per horizon. September was used to choose the component operating threshold by maximizing F1. The complete history and rationale for selecting the final model family, hyperparameters, and features are **not recovered**. October–December was inspected in earlier Airport/Network feature-set comparisons; whether September influenced earlier feature or model selection is **not recovered**.

The saved notebook includes early Logistic Regression and Random Forest prototypes and later feature-set ablations. This shows that model and feature comparisons occurred, but does not recover their complete history or decision rationale. October–December was inspected in the original Airport/Network feature-set comparisons. The available records document September threshold optimization but do not establish that September was used **only** for threshold selection.

### M3 logistic stack

For each horizon, M3 uses the continuous Flight/Weather calibrated probability and Airport/Network `p_delay15`. The two probabilities are clipped to `[1e-6, 1 − 1e-6]`, transformed to logits, and standardized by a `StandardScaler` fit on September rows. The stacker is L2 Logistic Regression with `C=1`, `solver="lbfgs"`, `max_iter=1000`, and no class weighting.

| Period | M3 role | Rows T−2h | Rows T−30m |
|---|---|---:|---:|
| September 2024 | Meta-training | 579,111 | 579,074 |
| October 2024 | Threshold selection; maximize F1 | 609,185 | 609,146 |
| November–December 2024 | Evaluation | 1,159,155 | 1,159,076 |

The September positive counts are 87,286 at both horizons (15.0724% T−2h; 15.0734% T−30m). The November–December positive counts are 209,393 at both horizons (18.0643% T−2h; 18.0655% T−30m). The exact row-level M3 training-ID lists were not saved; the manifest records counts and join validation, not a September subset file/hash. The complete M3 architecture, hyperparameter, feature-selection, and model-family selection history is **not recovered**. The saved script uses September for stacker fitting and October for operating-threshold selection; November–December is the exploratory evaluation interval.

The saved implementation fits on September, selects thresholds in October, and then evaluates November–December without refitting. The joblib files are serialized after evaluation predictions were calculated, so a serialized model artifact saved before evaluation is not established.

Binary metrics use different historically selected operating thresholds and selection periods: Flight/Weather, January–June 2024; Airport/Network, September 2024; and M3, October 2024. Threshold optimization is distinct from calibration and from estimator-family, hyperparameter, and feature selection. The available evidence does not establish that all three models used an identical threshold-selection procedure.

## Input provenance and reproducibility

### Flight/Weather inputs

| Horizon | Saved score input | SHA-256 | H2 rows |
|---|---|---|---:|
| T−2h | `<private-handoff>\data\flight_weather_score_exports_2024_h2_v1\t_minus_2h_scores.csv.gz` | `4a44b8405b41380f46c310f129e819651a496c6e86cbda81d700494f7fcc4d93` | 3,569,958 |
| T−30m | `<private-handoff>\data\flight_weather_score_exports_2024_h2_v1\t_minus_30m_scores.csv.gz` | `5f9950a3a3c98e8032755c3a043886838b6dd08d08033fbfe64a376bed047753` | 3,569,770 |

The Flight/Weather run manifest SHA-256 is `baab3af39573206b831fa3424bd25155c0c4bc1e4850857cafe20b882ead48d7`. Yash’s independent check reported matching hashes for both scores and the manifest. The M3 run manifest links these hashes to the declared source manifest; the M3 script hashes its inputs and validates the score joins.

### Airport/Network inputs

| Horizon | Saved score input | SHA-256 | Rows |
|---|---|---|---:|
| T−2h | `outputs/airport_network_canonical_integration/airport_network_t_minus_2h_scores.csv.gz` | `88746f367717663488b942cd213d2eb2f9ad6a65fea102bc166a6798db9e5618` | 6,982,525 |
| T−30m | `outputs/airport_network_canonical_integration/airport_network_t_minus_30m_scores.csv.gz` | `da52c8e80b33fe29f9cc3c669c55e47624e021b8b73d53efe678e9cd506ff0ab` | 6,982,091 |

The model bundle SHA-256 is `9dca5d3bb2e8e2af0cf9bca3a2245584b83b10e2cfb0e883fb90388fd0b87b5a`. The M3 stack consumes the continuous `p_delay15` field, not the thresholded label. The available scoring implementation uses positive-class `predict_proba()`, but the original scoring-time feature builder was not recovered, so byte-level provenance of that original implementation remains incomplete.

The recorded original scoring-builder SHA-256 is `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36`. The often cited `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf` hash identifies `outputs/build_airport_network_preliminary_scores.py`, the scoring runner—not the standalone original feature builder. Available standalone builder copies have other hashes; the original source remains unrecovered.

### M3 artifacts

| Horizon | Exact score-export filename and path | Model path | Model SHA-256 | Score-export SHA-256 |
|---|---|---|---|---|
| T−2h | `m3_t_minus_2h_scores.csv.gz` — `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/m3_t_minus_2h_scores.csv.gz` | `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/m3_t_minus_2h_model.joblib` | `4594d58fd0a8c483a9bd5c72a811e72d64b42d01073398c26692e53b1bb2c77a` | `1ba0110b9890aa53b2bdf740fe6f2339fa5fa89be708692a3aa7dde6549c02a8` |
| T−30m | `m3_t_minus_30m_scores.csv.gz` — `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/m3_t_minus_30m_scores.csv.gz` | `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/m3_t_minus_30m_model.joblib` | `29cda9838316fa482afa288f44f8056598ae0ec55c43c8c2c6ad06e46f967a59` | `e3b0905706c4370b62cad00891a6a8bab8e37176568a913f09ac49ad70ef2a45` |

The M3 evaluation report path is `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/m3_evaluation_report.json`; SHA-256: `346f0de579ab495d308e085b25b19392af381abcec872906a5ddcdf1271830eb`. The exact run-manifest sidecar is `m3_run_manifest.sha256`, containing SHA-256 `5b77a6f09ed121f9f7d2df34f77cc6369eada1e5ba2bc74a79de84f1990d629d` for `m3_run_manifest.json`. The October–December component evaluation report is `outputs/airport_network_canonical_integration/matched_evaluation_oct_dec_2024.json`, SHA-256 `34dc990c6b0bab558e6a63f32704be0cb13cd9aa5ada731a327f1f97d0c911d9`.

The fitted M3 models were serialized **after** the November–December evaluation predictions were calculated. The saved code documents no refitting during November–December, but a pre-evaluation serialized model hash is not established; the later artifact cannot independently prove which exact serialized bytes existed before scoring.

## Results

The two evaluation periods are distinct. The October–December table is the component comparison. The November–December table is the M3 evaluation. Confusion matrices are ordered TN / FP / FN / TP.

### October–December component comparison

| Horizon | Model | N | AP | ROC-AUC | Precision | Recall | F1 | Balanced accuracy | Threshold (selection period) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| T−2h | Flight/Weather | 1,768,340 | 0.301968 | 0.674348 | 0.268672 | 0.536774 | 0.358103 | 0.623741 | 0.217442 (Jan–Jun) |
| T−2h | Airport/Network | 1,768,340 | 0.296262 | 0.664327 | 0.244777 | 0.588000 | 0.345660 | 0.614400 | 0.470405 (September) |
| T−30m | Flight/Weather | 1,768,222 | 0.301948 | 0.674965 | 0.271437 | 0.523707 | 0.357554 | 0.622683 | 0.218222 (Jan–Jun) |
| T−30m | Airport/Network | 1,768,222 | 0.313754 | 0.673690 | 0.256832 | 0.570994 | 0.354301 | 0.621918 | 0.463141 (September) |

The saved matched October–December report does not provide Brier scores for this table. This period had already been inspected and is exploratory.

### November–December M3 evaluation

| Horizon | Model | N | Positive prevalence | AP | ROC-AUC | Precision | Recall | F1 | Balanced accuracy | Brier | Threshold (selection period) | Confusion matrix |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| T−2h | Flight/Weather | 1,159,155 | 18.0643% | 0.327948 | 0.676451 | 0.273891 | 0.617136 | 0.379400 | 0.628216 | 0.139181 | 0.217442 (Jan–Jun) | 607,178 / 342,584 / 80,169 / 129,224 |
| T−2h | Airport/Network | 1,159,155 | 18.0643% | 0.322279 | 0.668014 | 0.262302 | 0.615436 | 0.367832 | 0.616919 | 0.213314 | 0.470405 (September) | 587,334 / 362,428 / 80,525 / 128,868 |
| T−2h | M3 | 1,159,155 | 18.0643% | 0.344739 | 0.684306 | 0.259718 | 0.689488 | 0.377310 | 0.628104 | 0.137256 | 0.178095 (October) | 538,248 / 411,514 / 65,019 / 144,374 |
| T−30m | Flight/Weather | 1,159,076 | 18.0655% | 0.327614 | 0.677258 | 0.275969 | 0.608435 | 0.379711 | 0.628237 | 0.139040 | 0.218222 (Jan–Jun) | 615,431 / 334,252 / 81,991 / 127,402 |
| T−30m | Airport/Network | 1,159,076 | 18.0655% | 0.342772 | 0.679061 | 0.274216 | 0.606095 | 0.377596 | 0.626196 | 0.208690 | 0.463141 (September) | 613,778 / 335,905 / 82,481 / 126,912 |
| T−30m | M3 | 1,159,076 | 18.0655% | 0.357079 | 0.691513 | 0.264095 | 0.691795 | 0.382260 | 0.633381 | 0.136060 | 0.175470 (October) | 546,036 / 403,647 / 64,536 / 144,857 |

M3 had higher AP, ROC-AUC, and recall, and lower (better) Brier scores than both component models at both horizons, but lower precision. F1 and balanced accuracy were mixed: at T−2h, M3 was slightly below Flight/Weather on both; at T−30m, M3 was above both component models on both. These are descriptive point estimates; no confidence intervals or significance tests were saved.

## Threats to validity and limitations

Airport/Network’s original scoring-time builder remains unrecovered. The recorded source-builder hash is `a77c70db…`; the `c107a5eb…` hash is the scoring-runner, not that builder. Complete estimator hyperparameter, feature-selection, and model-family selection histories for Airport/Network, Flight/Weather, and M3 are **not recovered** wherever evidence is unavailable. This does not mean no tuning or comparison occurred. Calibration and operating-threshold selection are documented separately and do not establish a complete model-selection history.

Historical BTS publication and ingestion timestamps, Airport/Network schedule vintages, and recent airport-state event-availability times are not available. Airport/Network recent activity is reconstructed from final BTS outcomes. `scheduled_origin_departures_prior2h` is anchored to scheduled departure and its availability at each cutoff is unverified. Flight/Weather uses a 15-minute weather observation-time proxy rather than actual receipt timestamps. Final BTS cancellation/departure fields define retrospective eligibility, not live eligibility.

The 39 ambiguous rows are excluded from the ID-based comparison. October–December 2024 and 2025 have already been inspected, so neither is a blind final test. Component thresholds were selected in different periods (Flight/Weather January–June; Airport/Network September), while M3’s threshold was selected in October. No statistical significance, causal feature effect, calibrated live risk, operational readiness, or deployment claim is supported.

## Conclusion

The saved retrospective results show that the M3 logistic stack ranked delayed flights better by average precision and captured more delayed flights by recall than either component on the November–December 2024 matched sample. Its precision was lower, and threshold-dependent results varied by horizon. These findings are exploratory, not a blind final-test result. Builder provenance, feature availability, selection history, and a genuinely untouched test period remain unresolved. No live-readiness or passenger-service claim is made.

## Future independent evaluation

January–June 2027 is a **provisional candidate only**, not an approved holdout. Before the period starts, both teammates must approve a split-lock protocol and confirm access to the required data. Freeze cohort rules, canonical identity logic, feature definitions, model/calibration/threshold artifacts, primary metrics, exclusions, and code/dependency hashes before any holdout results are inspected. Prospectively retain schedule vintages, weather messages, airport/flight-state messages, feature values, receipt and ingestion timestamps, and scores at both cutoffs. Keep missing inputs explicitly unavailable. After BTS labels are released, reconcile identities and evaluate the frozen predictions once. 2025 is not a blind holdout.

## Provenance references

- M3 implementation: `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/train_evaluate_m3.py` (input paths and hashes at lines 182–218; component probability fields at lines 248 and 302; joins at lines 318–361; fit at lines 475–488; threshold selection at line 489; evaluation at lines 492–510; serialization after evaluation at lines 514–521).
- M3 run manifest and report: `outputs/airport_network_canonical_integration/m3_september_2024_exploratory_v1/m3_run_manifest.json` and `m3_evaluation_report.json`.
- October–December matched comparison: `outputs/airport_network_canonical_integration/matched_evaluation_oct_dec_2024.json`.
- Airport/Network model configuration and lineage: `outputs/airport_network_canonical_handoff/manifests/airport_network_model_manifest.json` and `outputs/01_data_exploration_clean_colab_export.ipynb` (notebook cells 18, 20, and 27).
- Data-source references: BTS TranStats release information and airline on-time data ([TranStats release information](https://www.transtats.bts.gov/releaseinfo.asp)); NOAA/NCEI ASOS archive information ([NCEI ASOS](https://www.ncei.noaa.gov/news/whats-automated-surface-observing-system-asos)); Aviation Weather Center API documentation ([AWC API](https://www.connect.aviationweather.gov/data/api/)); and FAA SWIM access information, discussed only as a possible future operations source ([FAA SWIM access](https://www.faa.gov/air_traffic/technology/swim/products/get_connected)). Project-specific access, coverage, and data rights remain to be confirmed.
- This report is published in the public source repository. All project PRs, including PR #1, have been merged. The static research evidence viewer is published on the project website; a combined live prediction dashboard is not implemented.
