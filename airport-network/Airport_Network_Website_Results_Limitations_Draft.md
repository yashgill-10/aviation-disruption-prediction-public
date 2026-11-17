# Airport/Network results and limitations

> **Review draft.** These are retrospective 2024 prototype results, not production performance claims. The October–December 2024 test period has already been inspected. The draft is not yet approved or published as website content.

> **Status update (2026-11-16):** Written before joint integration completed. Canonical-key Airport/Network score exports have since been produced and validated locally with full coverage of the retrospective eligible cohorts, the matched October–December component comparison and the November–December M3 stacking evaluation have been run, and the M3 implementation is published under `m3/`. Statements below about pending canonical coverage, blocked handoffs, or outstanding combined-model evaluation describe the state at the time of writing. Live inference for Airport/Network and M3 remains unvalidated; see `docs/PROJECT_COMPLETION_STATUS.md`.

## Retrospective model comparison

The saved experiment compared schedule/route features with schedule/route plus Airport/Network context. Models used a 200,000-flight January–August training sample; September was used to select the threshold, which was then fixed for the October–December test period. The target is departure delay of at least 15 minutes among eligible non-cancelled flights.

| Horizon | Feature set | Threshold | Accuracy | Precision | Recall | F1 | Balanced accuracy | PR-AUC |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| T−2h | Schedule/route | 0.5227 | 60.67% | 22.46% | 56.29% | 32.11% | 58.91% | 22.62% |
| T−2h | Schedule/route + Airport/Network | 0.4704 | 63.21% | 24.48% | 58.80% | 34.57% | 61.44% | 29.63% |
| T−30m | Schedule/route | 0.5201 | 58.50% | 22.23% | 60.49% | 32.51% | 59.30% | 22.97% |
| T−30m | Schedule/route + Airport/Network | 0.4631 | 65.60% | 25.68% | 57.10% | 35.43% | 62.19% | 31.38% |

The archived report shows higher PR-AUC and F1 for the Airport/Network configuration at both horizons. At T−30m, it also shows the threshold-specific trade-off: precision is higher while recall is lower than for schedule/route alone. The always-on-time baseline had 83.47% accuracy but detected no delayed flights, so accuracy alone is not informative for this imbalanced target.

These are aggregate results from the original retrospective feature-ablation report. The archived report does not retain row-level predictions for the schedule/route baseline, so paired per-flight differences and uncertainty for the metric changes cannot be independently estimated. The feature-set comparison is not causal proof that Airport/Network features alone caused the gains.

## Prediction coverage and aircraft-rotation pilot

The local full-year retrospective Airport/Network exports scored 6,982,656 of 6,982,766 source-ordinal rows at T−2h and 6,982,222 of 6,982,332 at T−30m. Each horizon retains 110 explicit unavailable rows, all marked `duplicate_source_row_removed_before_training_features`; they are not assigned zero scores. These are source-ordinal coverage figures, not coverage on the approved canonical-ID cohorts. Canonical coverage remains unknown until the verified mapping is joined.

A separate aircraft-rotation pilot reported a unique tail match for 119,952 of 120,000 sampled rows and 47 ambiguous sampled keys. The reported 99.90% prior-arrival feature coverage is conditional on a unique tail match and valid scheduled cutoff UTC because rows missing either are removed before that percentage is computed. Overall prior-arrival coverage across the full pilot sample was not established. The horizon samples differ; only baseline versus aircraft-augmented results within each horizon use the same retained rows. These pilot figures are retrospective BTS evidence, not live operational availability.

## What the features represent

The saved Airport/Network model uses prior-seven-day airport aggregates, scheduled origin-airport departure volume, and recent origin-airport activity. The scheduled-volume feature counts departures in `[target scheduled departure − 2 hours, target scheduled departure)` at both horizons. It is anchored to the target flight's scheduled departure, not to the forecast cutoff.

## Availability, eligibility, and reproducibility limits

- Schedule-vintage timestamps have not been verified. It is not established that the scheduled-volume values reflect information available at either prediction cutoff.
- Prior-seven-day aggregates and recent activity are derived or reconstructed from BTS records. Publication, event, and ingestion timestamps establishing prediction-time availability are missing; do not describe these as verified live airport conditions.
- The retrospective cohort uses final BTS cancellation and departure outcomes to exclude cancelled flights and flights already departed by the cutoff. Those final outcomes do not establish live eligibility. Timestamped operational status is still needed.
- The latest local row-level Airport/Network exports are keyed by cleaned-source row ordinal, not verified canonical `flight_id`. They have not been matched to the Flight/Weather scores or a jointly verified common cohort.
- The locally verified 110-unavailable-row counts do not establish whether those records are members of the canonical cohort; the source-row ordinal crosswalk is required to determine canonical score coverage.
- The score manifest records a builder SHA-256 that differs from the currently available builder. The trained feature names/order match, but the exact scoring implementation has not been recovered, so full code-level reproduction remains unresolved.
- The shown October–December period has already been inspected. It must not be described as an untouched final test or reused for threshold or calibration tuning.

Until the builder provenance, canonical cohorts, row-level predictions, and operational feature/eligibility timestamps are reviewed, treat these metrics as retrospective prototype results only. A joint Flight/Weather-only, Airport/Network-only, and combined-model comparison on identical canonical flights and temporal partitions remains outstanding.

See [the feature-availability audit](Airport_Network_Feature_Availability_Audit.md) for the evidence and missing timestamps for each trained feature and the retrospective eligibility rules.

See [the October–December error analysis](Airport_Network_Oct_Dec_Error_Analysis.md) for aggregate counts, calibration diagnostics, subgroup patterns, and their interpretation limits.

## Source

The aggregate results are in `colab_model_artifacts/aviation_airport_network_results.csv`. Feature definitions and prediction-output semantics are documented in `Airport_Network_Feature_Schema.md` and `Prediction_Export_Contract.md`.
