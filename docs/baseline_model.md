# Schedule-only DepDel15 baseline

Run from the repository root in PowerShell after building `data\processed\depdel15`:

```powershell
.\.venv\Scripts\python.exe src\train_baseline.py
```

The baseline reads exactly 24 training Parquet files for 2022-2023 and 12 validation files for 2024. It does not open `flights\test`, read 2025 features, or use the test labels. The script streams each Parquet file in batches and fits a stochastic-gradient logistic model with log loss, plus a train-prior comparator.

Features available before departure are airline, origin, destination, scheduled departure/arrival time, day of week, month, distance, and scheduled elapsed time. Times are interpreted as BTS local HHMM values. The model excludes actual departure/arrival times, delay minutes and causes, cancellation outcomes, diverted status, and all other post-outcome fields.

The output directory is `data\processed\depdel15\baseline`:

- `metrics.json`: class prevalence, confusion matrices, precision, recall, F1, PR-AUC, ROC-AUC, and Brier score on 2024.
- `schedule_logistic_sgd.joblib`: fitted categorical encoder and logistic classifier for follow-up inference work.

The report includes raw 2024 validation metrics at threshold 0.5. It also fits Platt calibration and selects an F1 threshold on January-June 2024, then evaluates those choices on July-December 2024. Calibration bins summarize the second-half probabilities. The selected threshold is a development result, not a final operating policy.

The training script above still avoids the test split. A separate evaluator opened 2025 early for an exploratory score check of the frozen baseline, before weather and horizon models were built; see [final_test_evaluation.md](final_test_evaluation.md). Do not use that year for further tuning or call it an independent final evaluation of later models. Weather, airport-state, and aircraft-rotation features are not included in the evaluated baselines. Historical delay context is implemented as a separate feature experiment; see [historical_feature_experiment.md](historical_feature_experiment.md).

To change the number of streaming passes, use `--epochs N`. Use a separate output directory for another experiment, or pass `--overwrite` to deliberately replace the current baseline outputs.
