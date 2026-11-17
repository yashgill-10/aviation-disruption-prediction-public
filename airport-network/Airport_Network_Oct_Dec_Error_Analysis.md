# Airport/Network error analysis — October–December 2024

> **Retrospective ordinal-keyed diagnostics.** This already-inspected period is not a blind final test. The aggregate diagnostics below derive from `cleaned_input_row_ordinal` scores; a separate later comparison uses matched canonical IDs. BTS feature availability at prediction time remains unverified.

## Overall results

| Horizon | Scored rows | Actual delay rate | Predicted-positive rate | Precision | Recall | F1 | Balanced accuracy | PR-AUC | ROC-AUC | Brier score | 10-bin ECE |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| T−2h | 1,768,379 | 16.53% | 39.70% | 24.48% | 58.80% | 34.57% | 61.44% | 29.63% | 66.43% | 0.2095 | 0.2794 |
| T−30m | 1,768,261 | 16.53% | 36.75% | 25.68% | 57.10% | 35.43% | 62.19% | 31.38% | 67.37% | 0.2048 | 0.2736 |

The locally reconstructed counts and confusion matrices match the saved Airport/Network holdout report. That is an aggregate reconciliation, not complete code-level reproduction: the score run manifest records builder SHA-256 `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36`, while its contemporaneous available-builder field is `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf`. The exact recorded builder has not been recovered. The current public reference builder is a third version and is not a substitute. The saved bundle hash and trained feature names/order match, but this does not establish identical score-generation implementation. See [the feature-availability audit](Airport_Network_Feature_Availability_Audit.md) for related limits.

At the recorded thresholds, predicted-positive rates are much higher than the observed delay rate, and the equal-width ten-bin ECE values are high (27.94% and 27.36%). Treat these as uncalibrated ranking scores, not calibrated likelihoods. Do not retune or recalibrate using this already inspected period.

## Descriptive error patterns

- Delay prevalence is 21.6% in December, 13.6% in October, and 14.4% in November. Recall is higher in December (67.0% at T−2h and 67.7% at T−30m) than in October (51.9% and 48.2%). These are descriptive differences, not explanations of cause.
- Among carriers with at least 1,000 scored rows, recall is lowest for HA (25.1%) and YX (28.4%) at T−2h; at T−30m, it is lowest for HA (21.8%), YX (37.4%), and OO (40.9%).
- Among origins with at least 1,000 scored rows, examples of lower recall are JNU (13.3%) at T−2h and BIL (10.2%), OGG (12.4%), and JNU (16.8%) at T−30m. These are triage signals, not stable airport rankings.
- Wilson 95% intervals describe simple binomial sampling uncertainty for subgroup recall. They do not account for airport/carrier clustering, multiple comparisons, temporal drift, unresolved identities, or unverified feature availability.

At the existing thresholds, the models produce 530,213 false positives and miss 120,410 delayed flights at T−2h; at T−30m they produce 482,879 false positives and miss 125,380 delayed flights. This report does not choose a new operating threshold.

## Scope and limits

The saved aggregate report contains no row-level baseline predictions, so per-flight pairing and uncertainty for the original feature-ablation differences cannot be independently calculated. This ordinal-keyed analysis has 39 more rows per horizon than the later canonical matched comparison: the 39 unresolved ambiguous identities have no canonical IDs and were excluded from the matched comparison without inference. The canonical component comparison has 1,768,340 flights at T−2h and 1,768,222 at T−30m; see `../docs/PROJECT_COMPLETION_STATUS.md`. These ordinal diagnostics do not establish a blind final estimate or live readiness.

## Aggregate supporting files

The `error_analysis/` folder contains the metric JSON, monthly/carrier/origin tables, Wilson intervals, and saved-report reconciliation table. It contains aggregate outputs only; the large ordinal-keyed row-level score exports are not included. Their checksums, row counts, and the builder-hash mismatch are recorded in `error_analysis/provenance_summary.json`. The error-analysis calculation was performed locally from those score exports and the saved aggregate holdout report.
