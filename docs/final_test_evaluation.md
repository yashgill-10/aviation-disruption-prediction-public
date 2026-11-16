# Early 2025 baseline score check

This exploratory check compares the frozen schedule-only model with the frozen schedule-plus-historical-delay model. It is not the scheduled out-of-time evaluation of the integrated weather/horizon model: those features had not yet been built when 2025 was opened. Weather, airport-state, and aircraft-rotation features are not part of these results.

## Protocol

- Both classifiers were trained on 2022-2023 flight rows.
- Each Platt calibrator and F1 threshold was fitted on January-June 2024. The July-December 2024 results were reviewed before opening the final test year.
- Both models were then scored on the same 2025 BTS population. No 2025 result was used to train, select, calibrate, or set a threshold for either frozen baseline.
- The historical model uses the predeclared three-complete-month lagged delay rates. For later test months, earlier complete 2025 months can contribute to those lagged features; the current and future target months are excluded. This is a chronological rolling evaluation, not a test in which every row is independent of all earlier test outcomes.

The test set contains 6,898,743 flights. Its `DepDel15` prevalence is 21.73%, which is the average-precision reference for a constant ranking.

## Results

Operating metrics use each model's own threshold, frozen from January-June 2024. Probability metrics use the same model's frozen 2024 Platt calibrator.

| Model | Average precision | ROC-AUC | Brier score | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Schedule only | 0.3178 | 0.6494 | 0.1624 | 0.2985 | 0.6591 | 0.4109 |
| Schedule + historical delay | **0.3434** | **0.6644** | **0.1604** | **0.3031** | **0.6752** | **0.4184** |

Compared with schedule-only, adding historical delay rates increased average precision by 0.0257 and ROC-AUC by 0.0150. F1 increased by 0.0075 at the frozen operating thresholds, with recall up 0.0161 and precision up 0.0046. The calibrated Brier score improved by 0.0021. The historical model's average precision is above the test prevalence baseline of 0.2173.

At the selected historical-model threshold, the confusion matrix is 3,072,406 true negatives, 2,327,182 false positives, 486,896 false negatives, and 1,012,259 true positives. It catches about two thirds of delayed flights, but flags many flights that are not delayed. This threshold is a development choice rather than an operational recommendation; an airline would need to choose a threshold based on the cost and capacity of follow-up actions.

The historical model improved average precision in each 2025 quarter. Its F1 was slightly lower in Q1, Q3, and Q4, and higher in Q2, so the overall operating-point gain was modest and varied by season.

| Quarter | Schedule AP | Historical AP | Schedule F1 | Historical F1 |
| --- | ---: | ---: | ---: | ---: |
| 2025 Q1 | 0.2603 | 0.2722 | 0.3564 | 0.3556 |
| 2025 Q2 | 0.3438 | 0.3742 | 0.4335 | 0.4480 |
| 2025 Q3 | 0.3481 | 0.3715 | 0.4370 | 0.4358 |
| 2025 Q4 | 0.3308 | 0.3340 | 0.4140 | 0.4126 |

The complete metrics, including confusion matrices, calibration bins, monthly slices, and Brier scores, are in [`comparison_metrics.json`](../data/processed/depdel15/final_test/comparison_metrics.json). The folder name `final_test` is retained from the first run; its contents are exploratory baseline results, not the scheduled final evaluation.

## Limits and next use

- The lagged features assume prior complete-month BTS outcomes are available. The archive's publication delay is not modeled, so these results do not establish that every historical rate would have been available in real time.
- Weather, airport-state, aircraft-rotation, and delay-propagation features are absent.
- The 2025 year has already been inspected before the planned weather/horizon work. Do not use these results to make further feature, model, calibration, or threshold choices. The scheduled Oct 13 evaluation cannot be presented as an independent blind test on 2025. A new untouched test period is needed for an independent estimate of the integrated model; if none is available by the deadline, label the 2025 result exploratory and disclose the reuse.
