# M3 retrospective logistic stacking experiment

This local experiment fits only the two M3 logistic stacking models using existing, matched September 2024 component probabilities. It does not retrain either base model or regenerate base predictions.

## Reproduction

The component score exports are private and are not distributed in this repository. To rerun the experiment in the private workspace, install scikit-learn 1.6.1 and run:

```powershell
python -B m3/train_evaluate_m3.py
```

Set `M3_FLIGHT_WEATHER_EXPORT_DIR` to the Flight/Weather score-export directory, `M3_AN_EXPORT_DIR` to the directory containing the Airport/Network `run_manifest.json`, score, target and feature exports, and optionally `M3_PRELIM_MANIFEST`, `M3_AN_BUILDER`, `M3_WORKSPACE_ROOT` for the provenance inputs. Without these private inputs the script cannot run; `test_m3_contract.py` exercises the input/partition helpers on synthetic data only.

The script validates full input hashes, canonical-ID uniqueness, target parity, probability/status validity, horizon/date membership, and exact September counts before fitting. It refuses to overwrite generated files.

## Protocol

- Meta-train: September 2024 only, independently per horizon.
- Fit `StandardScaler` on September logit-transformed component probabilities, then L2 `LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000, class_weight=None)`.
- Select each M3 decision threshold on October 2024 by F1; do not refit the model or scaler.
- Evaluate November–December 2024 with frozen M3 models/thresholds. This period was already inspected and is exploratory, not a blind final test.
- The arithmetic mean component probability is descriptive only; its binary metrics use a fixed 0.5 threshold, not a tuned value.

The score exports contain October threshold-selection and November–December evaluation predictions. September in-sample M3 predictions are deliberately not exported.

## Limitations

The original Airport/Network scoring-time builder source is unrecovered and its recorded hash differs from the current local builder. Airport/Network features use retrospective BTS outcomes; BTS schedule/history availability timestamps are unverified. NOAA actual receipt timestamps are unavailable; Flight/Weather uses an observation-time-plus-15-minute proxy. Flight/Weather score chronology is documented by its manifest, but its scoring implementation/calibrator artifact was not independently present in this workspace. These are retrospective research results, not live operational evidence. No untouched final-test period is established; the 2025 period was previously inspected.

See `m3_evaluation_report.json`, `m3_run_manifest.json`, and `SHA256SUMS.txt` for counts, metrics, source hashes, artifact hashes, validation outcomes, and provenance details.

See `m3_interpretation_and_error_analysis.md` for a matched metric comparison, recall/precision tradeoff, and the limits on interpreting these exploratory results.

See `M3_contribution_log.md` for artifact authorship and pending joint review status.


## Public-release note

This is the actual retrospective M3 training/evaluation script recovered from the private final-submission handoff archive (`airport_network_m3_final_submission_handoff.zip`), with only the external input paths parameterized via environment variables. The trained `m3_*.joblib` models and row-level `m3_*_scores.csv.gz` exports remain private and are not distributed; their SHA-256 hashes are recorded in `m3_run_manifest.json` and `m3_evaluation_report.json`. The contract test is synthetic-only.
