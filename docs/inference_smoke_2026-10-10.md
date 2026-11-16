# Protected current-inference smoke test

**Run date:** 2026-10-10 11:50 UTC
**Purpose:** Verify the saved Flight/Weather inference path without fitting a model, changing a split, or overwriting any experiment artifact.

## Input and safeguards

The separate input file is `data/processed/depdel15/inference_smoke_2026-10-10/current_flights.csv`. It contains exactly the eight required schedule fields and one BTS schedule row from `flights/train/2023-12.parquet`:

| Flight date | Airline | Route | Scheduled departure | Scheduled arrival | Distance | Scheduled elapsed |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 2023-12-31 | OO | LAS → LAX | 10:21 | 11:41 | 236 mi | 80 min |

No target, actual-time, delay-minute, cancellation, or post-departure field was included in the input. The model uses schedule features, weather available by the horizon cutoff, and strictly prior BTS delay history. Daily history excludes the entire target date; completed-month history ends before the target month. The inference adapter supplies a fixed internal target placeholder only because the shared encoder returns a `(features, target)` pair; only `features` are passed to the saved classifier.

The run used `--history-splits train`, so validation and test files were not read. Existing artifacts were loaded from:

- `data/processed/depdel15/weather_models/t_minus_2h/weather_logistic_sgd.joblib`
- `data/processed/depdel15/weather_models/t_minus_30m/weather_logistic_sgd.joblib`

The inference script does not call `fit`, `partial_fit`, or write model/metrics artifacts. Input and outputs are isolated under `data/processed/depdel15/inference_smoke_2026-10-10/`; the inference script now refuses to overwrite an existing CSV or summary output.

## Command

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe src\predict_current_flights.py --input-csv data\processed\depdel15\inference_smoke_2026-10-10\current_flights.csv --history-dir data\processed\depdel15\flights --history-splits train --model-t-minus-2h data\processed\depdel15\weather_models\t_minus_2h\weather_logistic_sgd.joblib --model-t-minus-30m data\processed\depdel15\weather_models\t_minus_30m\weather_logistic_sgd.joblib --output-csv data\processed\depdel15\inference_smoke_2026-10-10\predictions.csv
```

The command completed successfully after network access was approved for the AviationWeather.gov lookup. The renderer was also run against the prediction CSV, with an explicit smoke-test disclaimer.

## Output

| Horizon | Delay ≥15 min probability | Frozen threshold | Decision |
| --- | ---: | ---: | --- |
| T−2h | 20.55% | 21.74% | Below threshold |
| T−30m | 20.28% | 21.82% | Below threshold |

Both endpoints have missing weather at both cutoffs. The API returned stations, but the sample’s cutoffs were in December 2023 and the API supplies recent observations, so no report was available by those historical cutoffs. Historical features were computed from training rows only; the latest training date was 2023-12-31.

Files created by this smoke test:

- Input: `data/processed/depdel15/inference_smoke_2026-10-10/current_flights.csv`
- Predictions: `data/processed/depdel15/inference_smoke_2026-10-10/predictions.csv`
- Run summary: `data/processed/depdel15/inference_smoke_2026-10-10/predictions.summary.json`
- Labeled page: `data/processed/depdel15/inference_smoke_2026-10-10/prediction_smoke.html`

## Interpretation and limits

This is a pipeline smoke test, not an evaluation. The sample row belongs to 2022–2023, the models’ training period, so the fitted classifiers have seen this row during training. Its outcome was not included in the inference CSV or passed to the classifier, but its probability must not be treated as a held-out result. No validation or 2025 test row was used, and neither split was modified. The 2025 split was already opened during an earlier frozen-baseline exploration, so it is not a blind final test.

This is also not a live prediction: no upcoming flight schedule was supplied, and the 2023 sample cannot have contemporaneous METARs in the current feed. A live run needs actual scheduled-flight fields and recent BTS operational history; the binary model reports delay probability, not expected delay minutes.
