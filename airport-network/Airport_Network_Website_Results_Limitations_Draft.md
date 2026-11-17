# Airport/Network results and limitations — website reference

This document is a supporting content reference. These are retrospective 2024 research results, not production-performance claims. October–December 2024 has been inspected and is exploratory, not a blind final test.

## Original feature-ablation experiment

The archived experiment compared schedule/route features with schedule/route plus Airport/Network context. It used a 200,000-flight January–August training sample and September operating-threshold selection; the threshold was fixed for October–December. Its aggregate metrics are in `Airport_Network_ML_Summary.md`. The baseline retained no row-level predictions, so paired uncertainty cannot be calculated and the comparison is not causal evidence.

## Canonical matched comparison

The later joint comparison used exactly matched canonical IDs and targets for Flight/Weather and Airport/Network, separately by horizon: 1,768,340 flights at T−2h and 1,768,222 at T−30m. The corresponding component metrics are reported in `../docs/PROJECT_COMPLETION_STATUS.md` and the final joint report. The full-year canonical memberships contain 6,982,525 T−2h and 6,982,091 T−30m IDs, with 100% Airport/Network score coverage recorded in the private run artifacts.

These denominators are distinct from the source-ordinal error-analysis counts: 1,768,379 at T−2h and 1,768,261 at T−30m. The 39 additional ordinal records in each horizon are unresolved ambiguous identities without canonical IDs. They remain in ordinal diagnostics but are excluded from the canonical matched comparison; no identity was inferred.

## Feature definition and retrospective eligibility

Airport/Network evaluates separate T−2h and T−30m models. Its scheduled origin-volume feature counts scheduled departures in `[target scheduled departure − 2 hours, target scheduled departure)` at both horizons. It is anchored to the target scheduled departure. Prior-seven-day airport summaries and recent airport activity are derived/reconstructed from BTS data.

Retrospective cohorts exclude cancelled flights and flights whose reconstructed actual departure was at or before the relevant cutoff. This uses final BTS outcomes; it does not establish live flight eligibility. Schedule-vintage, BTS publication/ingestion, and recent operational-event timestamps have not been verified.

## Error, calibration, and subgroup evidence

The ordinal-keyed Airport/Network October–December analysis reports false positives/false negatives at the saved thresholds, monthly/carrier/origin recall, and 10-bin expected calibration error of 27.94% (T−2h) and 27.36% (T−30m). The errors and subgroup patterns are descriptive; the score probabilities are not shown to be operationally calibrated. Subgroup intervals are simple Wilson binomial intervals with a minimum group size of 1,000; they do not account for clustering, multiple comparisons, temporal dependence, selection, or data-pipeline uncertainty. Refer to `Airport_Network_Oct_Dec_Error_Analysis.md` and `error_analysis/README.md` for counts and interpretation.

The separate November–December M3 evaluation is completed and exploratory. It uses September meta-training and October threshold selection. M3 has higher AP, ROC-AUC, recall and lower Brier score than both components at both horizons; precision is lower, and F1/balanced accuracy are mixed. These descriptive values do not establish statistical significance or live readiness. See `../m3/` and the final joint report.

## Provenance and limits

The exact Airport/Network scoring-time feature builder recorded in the run manifest has not been recovered. The public feature builder is a reference implementation; it is not byte-identical evidence for the original scoring-time implementation. The canonical score exports, model, mapping, and row-level predictions are private and intentionally absent from this public repository. Synthetic tests validate interfaces and contracts, not full model reproducibility.

Flight/Weather and Airport/Network inputs were not proven available at prediction cutoffs. M3's September row-level training-ID list was not saved, and its serialized model files were written after evaluation predictions/metrics were calculated; no pre-evaluation model artifact hash is established. No untouched final test has been established; 2025 was inspected. Do not claim causality, statistical significance, or production/live readiness.
