# Website Research Content Handoff

**Purpose:** verified scientific content for the research website. Website design, frontend implementation, charts and visual testing are owned separately; this document supplies only the research facts, their evidence, and their classification.

**Repository:** `yashgill-10/aviation-disruption-prediction` · merged PR #1 · `main` @ `81b2fa79f7bc5c8923e315510b7ede077409365d`

---

## A. Project description

**Research question:** can machine-learning models predict flight delay/disruption (`DepDel15` — departure delayed ≥15 minutes among non-cancelled flights) at two operational cutoffs, **T−2h** and **T−30m** before scheduled departure?

**Workstreams:**

1. **Flight/Weather** — schedule fields + lagged historical delay rates + observed weather (GHCNh), trained 2022–2023, calibrated/thresholded Jan–Jun 2024, evaluated Jul–Dec 2024 (previously inspected; not blind).
2. **Airport/Network** — schedule features + prior-7-day airport aggregates + reconstructed recent airport activity (partner workstream; merged via PR #1).
3. **M3** — logistic stacking model combining the two component probability scores (logit features, StandardScaler, LogisticRegression), September meta-training → October threshold selection → Nov–Dec exploratory evaluation.

**Chronology:** Phase 1 canonical identity audit → Phase 2 ordinal→canonical mapping → component training/scoring → canonical exports → matched Oct–Dec component evaluation → M3 Sep–Dec evaluation → final exploratory joint report.

## B. Verified Airport/Network results

### Canonical identity mapping (verified)

| Item | Value |
|---|---:|
| Cleaned source records | 6,982,766 |
| Unambiguous canonical identities (`bts-flight-v1:<64 hex>`) | 6,982,525 |
| Unresolved (explicit, unassigned) | 241 = 220 ambiguous + 1 identity-incomplete + 20 unmatched |

Evidence: `cleaned_row_to_canonical_mapping.csv.gz` SHA-256 `a0b9d6657d3be7148bbf8dc6bed2ff47a6d4301c9bd776c071cee627a0292168`; `mapping_manifest.json`; all handoff checksums independently re-verified.

### Canonical score exports (verified)

| Horizon | Scored canonical IDs | Coverage of verified retrospective eligible cohort |
|---|---:|---:|
| T−2h | 6,982,525 | 100% |
| T−30m | 6,982,091 | 100% |

Evidence: `data/processed/airport_network_score_exports_2024_v1/canonical_export_run_manifest.json` — all recorded checks PASS; both score files re-hashed and match manifest (`88746f36…5618`, `da52c8e8…ff0a`). Ordinal-keyed exports preserved unchanged. Byte-identical score files ship inside Package A v3.

### October–December 2024 matched metrics (saved report values)

Source: `airport_network/evaluation/matched_evaluation_oct_dec_2024.json` (Package A v3; also `reports/final_joint_exploratory_research_report.md` lines ~111–116). Matched common-scored cohorts: **n = 1,768,340 (T−2h), 1,768,222 (T−30m)** — identical flights scored by both components, zero missing/duplicate/target-mismatch.

| Metric | T−2h | T−30m |
|---|---:|---:|
| Average precision | 0.296262 | 0.313754 |
| ROC-AUC | 0.664327 | 0.673690 |
| Precision | 0.244777 | 0.256832 |
| Recall | 0.588000 | 0.570994 |
| F1 | 0.345660 | 0.354301 |
| Balanced accuracy | 0.614400 | 0.621918 |
| Brier score | 0.209521 | 0.204786 |

Component comparison on the same matched cohorts — **Flight/Weather** for reference: T−2h AP 0.301968 / ROC-AUC 0.674348 / Brier (not saved in this table for FW; see report); T−30m AP 0.301948 / ROC-AUC 0.674965. Full per-model values in the matched-evaluation JSON.

**Independently recomputed/verified:** Devin verified the mapping checksums, canonical export hashes, and model-bundle load plus 500-row-per-horizon inference reproduction (max |Δp| ≈ 2.2e-16, labels 100% match, sklearn 1.6.1 env). Omruthik independently loaded the model and performed one-row-per-horizon inference reproduction, and independently recomputed the October–December Airport/Network metrics from the saved score, target and feature tables — matching the saved report exactly.

## C. M3 combined-model results

Source: `m3/m3_evaluation_report.json` (Package A v3); report `m3/m3_interpretation_and_error_analysis.md`; reproducibility check `saved_export_metric_reproducibility: PASS`.

**Methodology/chronology:** features = `[logit(fw_p), logit(an_p)]` (p clipped to [1e-6, 1−1e-6]); StandardScaler fit **September only**; LogisticRegression(l2, C=1.0, lbfgs, max_iter=1000, no class weight). Meta-train Sep (579,111 rows T−2h) → threshold select Oct (609,185) maximizing F1 → evaluate **Nov–Dec 2024** (1,159,155 T−2h / 1,159,076 T−30m matched rows; zero duplicates, zero target mismatches, zero missing probabilities).

| Horizon | Model | N | AP | ROC-AUC | Precision | Recall | F1 | Bal. acc. | Brier | Threshold |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| T−2h | Flight/Weather | 1,159,155 | 0.327948 | 0.676451 | 0.273891 | 0.617136 | 0.379400 | 0.628216 | 0.139181 | 0.217442 |
| T−2h | Airport/Network | 1,159,155 | 0.322279 | 0.668014 | 0.262302 | 0.615436 | 0.367832 | 0.616919 | 0.213314 | 0.470405 |
| T−2h | **M3** | 1,159,155 | **0.344739** | **0.684306** | 0.259718 | **0.689488** | 0.377310 | 0.628104 | **0.137256** | 0.178095 |
| T−30m | Flight/Weather | 1,159,076 | 0.327614 | 0.677258 | 0.275969 | 0.608435 | 0.379711 | 0.628237 | 0.139040 | 0.218222 |
| T−30m | Airport/Network | 1,159,076 | 0.342772 | 0.679061 | 0.274216 | 0.606095 | 0.377596 | 0.626196 | 0.208690 | 0.463141 |
| T−30m | **M3** | 1,159,076 | **0.357079** | **0.691513** | 0.264095 | **0.691795** | **0.382260** | **0.633381** | **0.136060** | 0.175470 |

M3 shows higher AP, ROC-AUC, recall and lower (better) Brier than both components at both horizons; **lower precision**; F1/balanced-accuracy mixed by horizon. Positive prevalence ≈18.06% both horizons. **Exploratory — November–December was already inspected; not a blind test. No confidence intervals or significance tests were computed.**

## D. Flight/Weather results

Source: `docs/weather_data_plan.md` (Jul–Dec 2024 development ablation); matched Oct–Dec values from the same matched-evaluation JSON above.

Jul–Dec 2024 ablation (2022–23 train; Platt calibrator + F1 threshold fit Jan–Jun 2024 only):

| Feature set | AP | ROC-AUC | Raw Brier | Cal. Brier | Threshold | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Schedule only | 0.2820 | 0.6374 | 0.2152 | — | — | — | — | — |
| Schedule + historical delay | 0.3611 | 0.6903 | 0.1962 | 0.1452 | 0.2173 | 0.3166 | 0.5833 | 0.4105 |
| + weather, T−2h | 0.3608 | 0.6882 | 0.1961 | 0.1457 | 0.2174 | 0.3300 | 0.5293 | 0.4065 |
| + weather, T−30m | 0.3648 | 0.6898 | 0.1959 | 0.1454 | 0.2182 | 0.3344 | 0.5185 | 0.4066 |

Weather build: 20,290,541 rows/horizon, ~99.5% endpoint coverage, as-of audit clean. Historical Flight/Weather weather availability is based on NOAA observation time plus a 15-minute availability proxy. The historical archive does not establish actual receipt or ingestion time by an operational prediction system. The as-of audit found no post-cutoff observations under this proxy, but this does not prove actual historical feed availability. **Classification: carried from saved reports.** Flight/Weather row-level private artifacts were not independently available to Omruthik; matched-comparison values remain carried from saved hash-pinned reports.

**Aircraft-rotation pilot:** partner workstream; not independently reproduced from original raw archives — present only as pilot/descriptive if mentioned at all.

## E. Scientific limitations (must appear prominently)

1. Original Airport/Network scoring-time feature builder **not recovered** (fingerprint `a77c70db…` searched exhaustively; local reference builder `c107a5eb…` is a compatibility implementation, not proof of recovery).
2. Operational feature-availability timestamps **unverified** — retrospective BTS reconstruction; `feature_availability_status = retrospective_bts_availability_unverified`.
3. Eligibility used **final retrospective outcomes** (Cancelled=0, DEP_DELAY thresholds) — not live eligibility.
4. **No blind final holdout** — 2025 was previously inspected; Oct–Dec 2024 and Nov–Dec M3 periods were already opened.
5. Complete model-selection/hyperparameter/feature-selection history **incomplete** (AN training-row ordinals not saved).
6. **No clustered/paired confidence intervals** — only descriptive point estimates (AN error analysis has Wilson intervals, descriptive only).
7. Aircraft-rotation pilot **not independently reproduced**.
8. M3 results are **exploratory**, retrospective.
9. **Live operational prediction not validated** for Airport/Network or M3; Flight/Weather has only a local prototype. Website displays stored results only.
10. Flight/Weather historical weather availability rests on NOAA observation time plus a 15-minute availability proxy — not verified receipt/ingestion timestamps (see section D).

## F. Evidence classification for the website

| Classification | Items |
|---|---|
| **Independently verified — Devin** | Canonical mapping checksums; canonical export hashes + manifest checks; AN model-bundle deserialization and 500-row-per-horizon inference reproduction under scikit-learn 1.6.1 |
| **Independently verified — Omruthik** | Model load + one-row-per-horizon inference reproduction; October–December Airport/Network metrics independently recomputed from saved score/target/feature tables, matching the saved report exactly |
| **Carried from saved, hash-pinned reports** | Flight/Weather matched-comparison values (raw row-level score files not independently available to Omruthik); M3 Sep–Dec figures; Flight/Weather Jul–Dec ablation; confusion matrices; thresholds |
| **Exploratory / descriptive only** | M3 vs component deltas; AN subgroup/error-analysis figures; rotation pilot (not independently reproduced from original raw archives); 2025 baseline check |
| **Restricted — never publish** | Row-level score/feature/target CSVs, model `.joblib` bundles, raw BTS data, local paths, the ~2.5 GB Package A archive |

## G. Source-of-truth file map

| Content | Path (in repo or Package A v3) |
|---|---|
| Final joint report | `reports/final_joint_exploratory_research_report.md` |
| AN matched Oct–Dec metrics | `airport_network/evaluation/matched_evaluation_oct_dec_2024.json` (Package A) |
| AN canonical export manifest | `data/processed/airport_network_score_exports_2024_v1/canonical_export_run_manifest.json` (local) / Package A `manifests/` |
| AN feature availability audit | `airport_network/reports/feature_availability_audit.md` (Package A); `docs/audit/FEATURE_AVAILABILITY_AND_LEAKAGE_AUDIT.md` |
| M3 results | `m3/m3_evaluation_report.json`, `m3/m3_run_manifest.json` (Package A) |
| FW development results | `docs/weather_data_plan.md` |
| Project status/limitations | `docs/PROJECT_COMPLETION_STATUS.md`, `README.md` |
| Identity contract | `src/canonical_flight_identity.py`, `docs/canonical_flight_identity_build_2024.md` |
