# Matched model evaluation protocol and executed scope

This protocol records the rules used for the retrospective matched comparison. The project evaluation has been run on private, checksum-verified inputs; this public repository contains the implementation and aggregate reports but not row-level score exports, mappings, or cohort files.

## Cohorts and horizon handling

- Use canonical `flight_id` membership independently for T−2h and T−30m; never combine the horizon populations.
- Require one row per canonical ID and common `delay_15` target. Preserve unavailable scores and explicit reasons in coverage summaries; do not replace them with zero.
- For each component comparison, calculate metrics on the exact common IDs scored by both Flight/Weather and Airport/Network and report the denominator. Do not infer membership from aggregate counts.
- The combined model must be an actual saved M3 prediction. Never synthesize it by averaging component outputs.

## Scores and metrics

Existing probabilities, saved thresholds, and binary predictions are used unchanged. Metrics include average precision, ROC-AUC, Brier score where available, precision, recall, F1, balanced accuracy, and coverage. The Oct–Dec component comparison uses 1,768,340 common flights at T−2h and 1,768,222 at T−30m. The November–December M3 evaluation has separate denominators of 1,159,155 and 1,159,076. Results are retrospective/exploratory; neither period is a blind final test. No significance tests or confidence intervals were saved for the joint point-estimate comparisons.

## M3 chronology

M3 was trained separately per horizon on September 2024 component probabilities, selected an operating threshold on October 2024, and evaluated on November–December 2024. It did not retrain the component models. The September row-level M3 training-ID list was not saved. Its fitted model files were serialized after evaluation predictions and metrics were calculated; no Nov–Dec refit is documented, but a pre-evaluation serialized model hash is not established. The result remains exploratory.

## Canonical key construction

`evaluation/join_ordinal_scores_to_canonical_ids.py` consumes the preserved cleaned-input ordinal and the approved mapping/membership artifacts. It keeps unresolved or unscorable cohort rows explicit and validates exact IDs, targets, status, and provenance. `evaluation/evaluate_matched_models.py` enforces key uniqueness and exact cohort coverage, rejects target/horizon inconsistencies, and calculates comparison metrics. The full project invocation requires private approved inputs that are intentionally not in this repository; synthetic contract tests can be run with the commands in `README.md`.

The historical source-ordinal error-analysis tables are not this canonical matched comparison. They include 39 unresolved ambiguous records per horizon without canonical IDs. Those 39 remain unresolved and are excluded from the matched canonical denominators above. See `error_analysis/README.md`.

## Scope and remaining limitations

The comparison is between existing pipelines on matched flights; it does not isolate a causal effect of feature additions. Airport/Network scoring-builder provenance remains unresolved, and schedule-vintage, BTS history publication/ingestion, recent airport-state, and live eligibility timestamps are unverified. Flight/Weather weather receipt time is approximated from observation time plus 15 minutes. Model-family, hyperparameter, and feature-selection histories were not fully recovered. No live readiness claim follows from the retrospective comparison.

See `../docs/PROJECT_COMPLETION_STATUS.md` and `../reports/final_joint_exploratory_research_report.md` for the saved results and exact denominators.
