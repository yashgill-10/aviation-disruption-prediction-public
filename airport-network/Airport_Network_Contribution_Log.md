# Airport/Network and M3 contribution record

This workstream record describes completed contributions and their evidence locations. It does not infer authorship from commit counts. Joint work is labelled joint, and private row-level artifacts are not included in this public repository.

## Airport/Network — Omruthik Nakkalapally

- Developed the Airport/Network feature builder, separate T−2h and T−30m saved-model inference paths, canonical-ID integration adapter, and score-export contract. Relevant source: `airport_network_feature_builder.py`, `airport_network_inference.py`, `airport_network_integration.py`, and `airport_network_prediction_export.py`.
- Added synthetic interface, feature-builder, export, handoff-verifier, and matched-evaluation tests under `tests/`. These validate contracts and guards; they do not reproduce the private model's full score generation.
- Produced the retrospective Airport/Network error analysis, aggregate subgroup/calibration tables, and score-coverage/provenance documentation. Error-analysis tables remain keyed to `cleaned_input_row_ordinal`; they are separate from the canonical matched model comparison.
- Reconciled Airport/Network scores to the Phase 2 canonical mapping and separate retrospective T−2h/T−30m memberships in the private workspace. `docs/PROJECT_COMPLETION_STATUS.md` records 6,982,525 T−2h and 6,982,091 T−30m canonical cohort IDs, full score coverage, and the October–December matched comparison counts. The row-level mapping and scores remain private.
- Audited the airport-history, schedule-volume, recent-activity, and eligibility definitions. Their retrospective BTS values do not establish that the inputs were observable at either prediction cutoff.
- Documented that the exact scoring-time builder recorded by the run manifest was not recovered. The public builder is a reference implementation, not a byte-identical substitute for that original.

## M3 — source and evidence

- Recovered the actual M3 logistic-stacking training/evaluation script from the private final-submission handoff and included a public copy at `../m3/train_evaluate_m3.py`. Its external input paths are parameterized; the public copy does not contain private data, score exports, or fitted models.
- Added synthetic contract tests and documented the actual sequence: September 2024 meta-training, October threshold selection, and November–December exploratory evaluation, independently for T−2h and T−30m.
- Preserved the manifest and evaluation report as generated evidence. The row-level September M3 training-ID list was not saved. The serialized M3 model files were written after evaluation predictions and metrics were calculated; no November–December refit is documented, but a pre-evaluation serialized model hash is not established.

## Joint work — Omruthik Nakkalapally and Yash Gill

- Established the common canonical flight identity and horizon-specific retrospective eligibility cohorts; reconciled the model score exports on matched canonical IDs; reviewed the October–December component comparison and November–December M3 exploratory comparison.
- Prepared the joint research report and website evidence/limitations content. Metric values and limitations are summarized in `docs/PROJECT_COMPLETION_STATUS.md` and `reports/final_joint_exploratory_research_report.md`.
- Kept the two horizons separate and retained explicit unavailable-score semantics in the export interfaces. No missing prediction is replaced by zero.

## Reproducibility and attribution limits

- The full data-dependent Airport/Network score run and M3 experiment require private inputs and/or model artifacts that are intentionally absent from this public repository. Only synthetic contract checks are reproducible from this checkout.
- Complete model-family, feature-selection, and hyperparameter-selection histories were not recovered for all workstreams. The documented thresholds and chronology should not be misrepresented as a complete search history.
- Airport/Network scoring-builder provenance and timestamped operational feature availability remain unresolved. None of these artifacts establishes live readiness.
- This file records workstream contributions, not a commit-by-commit authorship ledger. Joint review does not change the primary ownership stated above.
