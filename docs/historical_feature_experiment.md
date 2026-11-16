# Historical delay feature experiment

This experiment answers how much prior flight performance improves the schedule-only `DepDel15` model.

## Feature definitions

The original model uses BTS outcomes from the three complete calendar months before each flight month. It creates global, airline, origin-airport, and origin-destination route delay rates with log flight counts. Airline, airport, and route rates are smoothed toward the same-window global rate with a 50-flight prior.

The expanded model adds 7-day and 30-day rates for global, airline, origin, route, and origin-airport-by-scheduled-departure-hour groups. These daily windows exclude the target flight's entire date. The daily rates use a 50-flight prior weighted by the corresponding global rate for the same window. No same-day outcome is used.

The feature builds read only the 2022-2024 train and validation partitions. The 2025 test partition was not opened by these builds or model runs. The 2025 split was opened earlier for a separate frozen-baseline exploration, so it is not a blind test for later work.

## Reproduction

The expanded build uses a separate output directory so the earlier three-month dataset remains available:

```powershell
python src/build_historical_features.py --output-dir data/processed/depdel15/historical_expanded --threads 4
python src/train_baseline.py --historical-features-dir data/processed/depdel15/historical_expanded --output-dir data/processed/depdel15/models/historical_expanded
```

The builder requires all 24 train and 12 validation monthly files, checks enriched row counts, stages the full output, and refuses to replace a non-empty directory. The model run uses 2022-2023 for training and 2024 for validation. Jan-Jun 2024 supplies calibration and threshold selection; Jul-Dec 2024 is the chronological evaluation period.

## Validation results

All three history-only model runs used 13,307,795 training flights and 6,982,746 validation flights. The earlier schedule-only and three-month-history results are retained here for comparison.

| Model / period | Average precision | ROC-AUC | Brier score |
| --- | ---: | ---: | ---: |
| Schedule only, all of 2024 | 0.3030 | 0.6459 | 0.2122 |
| Schedule + three-month history, all of 2024 | 0.3270 | 0.6593 | 0.1743 |
| Expanded monthly + 7/30-day history, all of 2024 | 0.3608 | 0.6871 | 0.2063 |
| Schedule only, Jul-Dec raw | 0.2820 | 0.6374 | 0.2152 |
| Schedule + three-month history, Jul-Dec raw | 0.3139 | 0.6558 | 0.1641 |
| Schedule + three-month history, Jul-Dec calibrated | 0.3139 | 0.6558 | 0.1521 |
| Expanded history, Jul-Dec raw | 0.3611 | 0.6903 | 0.1962 |
| Expanded history, Jul-Dec calibrated | 0.3611 | 0.6903 | 0.1452 |

For the expanded model, the threshold selected on Jan-Jun 2024 was 0.2173. On Jul-Dec 2024 it gave precision 0.3166, recall 0.5833, and F1 0.4105. At a calibrated threshold of 0.5, recall was 0.0488, showing why the operating point should be selected on calibration data. Calibration improved second-half Brier score from 0.1962 to 0.1452; the second-half train-prevalence baseline Brier score was 0.1579.

The expanded model's Jul-Dec average precision is 0.0472 higher and ROC-AUC is 0.0345 higher than the earlier three-month model. The raw full-year Brier score is worse than the earlier three-month model, but the calibrated second-half Brier score is better. Treat this as development evidence, not a final test estimate.

The saved run also includes error-analysis segments by month, airline, origin airport, scheduled departure hour, and weather availability in `data/processed/depdel15/models/historical_expanded/metrics.json`.

## Limits

Historical rates summarize non-cancelled flights with valid BTS `DepDel15` labels. The three-month rates are frozen by month; the 7/30-day rates update at date boundaries and exclude same-day outcomes. BTS reporting delay can make the most recent outcomes unavailable to a live system; current-flight inference records the latest local history date and the resulting history age. Weather, airport capacity, and aircraft rotation are not part of this history-only experiment. See [weather_data_plan.md](weather_data_plan.md) for the weather experiment and [final_test_evaluation.md](final_test_evaluation.md) for the earlier exploratory 2025 baseline check.
