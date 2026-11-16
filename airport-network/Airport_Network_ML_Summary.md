# Aviation Disruption Prediction — Airport/Network ML

## Scope

This report covers the Partner — Airport/Network ML work. It summarizes the Airport/Network experiments in the packaged Google Colab export `01_data_exploration_clean_colab_export.ipynb` and does not claim completion of the separate Flight/Weather track.

## Dataset and evaluation

- Source: the downloaded combined 2024 BTS CSV contains 6,982,766 rows. A streaming full-row fingerprint audit confirmed 110 exact duplicate rows, leaving 6,982,656 unique rows, matching the modeling report. All rows are for 2024, and `delay_15` is binary with no missing or invalid values in the downloaded file.
- Target: `delay_15`, indicating departure delay of at least 15 minutes.
- Chronological evaluation: January–August training sample (200,000 flights), September threshold-selection sample (200,000 flights), and all eligible October–December test flights.
- Forecast cutoffs: two hours and 30 minutes before scheduled departure.
- Thresholds were chosen on September by maximizing F1, then kept fixed for the final October–December test.
- The no-delay baseline achieved 83.47% accuracy but zero recall, so accuracy alone is misleading for this imbalanced target.

## Test results

| Forecast cutoff | Features | Threshold | F1 | PR-AUC | Recall | Balanced accuracy |
|---|---|---:|---:|---:|---:|---:|
| T−2h | Schedule and route | 0.5227 | 0.3211 | 0.2262 | 0.5629 | 0.5891 |
| T−2h | Schedule, route, airport/network | 0.4704 | 0.3457 | 0.2963 | 0.5880 | 0.6144 |
| T−30m | Schedule and route | 0.5201 | 0.3251 | 0.2297 | 0.6049 | 0.5930 |
| T−30m | Schedule, route, airport/network | 0.4631 | 0.3543 | 0.3138 | 0.5710 | 0.6219 |

Adding airport/network context increased PR-AUC by 0.0701 at T−2h and 0.0841 at T−30m. F1 increased by 0.0246 and 0.0292 respectively. At T−30m, the threshold trade-off raised precision from 0.2223 to 0.2568 while recall moved from 0.6049 to 0.5710.

## Airport and network features

The model compares schedule/route inputs with added operational context:

- Origin-airport departure volume, delay rate, and mean delay over the prior seven calendar days.
- Average destinations served per active day in that prior period.
- Scheduled departures from the origin airport in the two hours before the cutoff.
- Departures observed at the same origin airport in the two hours before the cutoff, including volume, delay rate, and mean delay.

The rolling airport history is shifted so a prediction date does not use that date's own completed outcomes. Recent activity is restricted to times before the selected cutoff and uses the BTS local clock. The fitted model bundle, score table, per-origin error table, and feature-importance table are saved in the Colab session.

## Aircraft-chain feasibility

A 2024 BTS monthly-data join diagnostic found a unique tail-number match for 6,979,477 of 6,982,656 project rows (99.95%). It identified 3,159 project rows with ambiguous matches. This establishes that aircraft identifiers can be linked for most records; it is not an aircraft-rotation or propagation model. Building that model still requires timezone-aware arrival/departure ordering and testing with features available by each forecast cutoff.

## Limitations

- This is a retrospective experiment, not a validated live prediction service. Final BTS delay and actual departure records are not necessarily available at the prediction time.
- The reported model uses a 200,000-flight training sample rather than all January–August training rows.
- Aircraft rotations were assessed separately in the matched-sample pilot below; cross-airport propagation still needs live timestamped state to be validated.
- There are no weather variables in this Airport/Network model; weather belongs to the separate Flight/Weather workstream.
- Cancelled flights and flights already departed before a cutoff are excluded from the departure-delay prediction.
- The 2024 results show an improvement from added airport/network context, but the PR-AUC values remain modest and should not be presented as a production guarantee.

The repository does not include the cleaned flight data or serialized model bundle. The trained model and diagnostics remain in the Colab archive. The project-level final 2025 holdout evaluation remains outstanding; the 2024 Oct–Dec figures below are retrospective prototype results.

## Reproduce in Colab

In the saved notebook, the introductory instructions identify the Airport/Network reproduction cells as `2 → 4 → 5 → 8 → 27 → 28`. Run those cells in order. Skip the separate Flight/Weather cells identified in the notebook instructions. The verified Colab archive `aviation_airport_network_complete_artifacts.zip` contains nine files, including the Airport/Network model artifacts and aircraft-pilot outputs. The archive passed its ZIP integrity check.

## Aircraft rotation / propagation pilot — executed in Colab

The Airport/Network-only pilot was run against all 12 monthly BTS archives in Colab. It sampled Jan–Aug for fitting, September for threshold selection, and Oct–Dec for final testing; the target remained departure delay of at least 15 minutes among eligible non-cancelled, non-diverted flights. The two forecast cutoffs were evaluated separately. Each split used up to 30,000 training, 10,000 calibration, and 20,000 test records before tail matching and cutoff eligibility were applied.

| Forecast cutoff | Features | September threshold | Test n | F1 | PR-AUC | Recall | Precision | Balanced accuracy |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| T−2h | Schedule and route | 0.5242 | 19,990 | 0.3138 | 0.2311 | 0.5670 | 0.2169 | 0.5813 |
| T−2h | Schedule, route, prior aircraft arrival | 0.5219 | 19,990 | 0.3410 | 0.3298 | 0.4897 | 0.2616 | 0.6083 |
| T−30m | Schedule and route | 0.5031 | 19,986 | 0.3240 | 0.2332 | 0.6660 | 0.2141 | 0.5913 |
| T−30m | Schedule, route, prior aircraft arrival | 0.5262 | 19,986 | 0.4958 | 0.4697 | 0.5487 | 0.4523 | 0.6219 |

Prior-tail arrival features improved test F1 by 0.0272 at T−2h and 0.1718 at T−30m; PR-AUC increased by 0.0987 and 0.2365 respectively. Precision increased at both horizons, while recall decreased at both selected F1 thresholds. The sampled join found unique BTS tail numbers for 119,952 of 120,000 pilot rows; 47 sampled keys were ambiguous, and prior-aircraft-arrival feature coverage among cutoff-ready rows was 99.90%. The per-horizon test sizes differ slightly because flights already departed before a cutoff are ineligible.

The run saved `aviation_aircraft_rotation_pilot_results.csv` and `aviation_aircraft_rotation_pilot_sample_features.csv` in Colab. A combined ZIP, `aviation_airport_network_complete_artifacts.zip`, was created and passed the ZIP integrity check; it contains the earlier Airport/Network model package plus this pilot's script, results, sample features, and README.

## Pilot-specific limitations

- This remains a retrospective pilot, not a validated live service. BTS tail assignments and completed arrival records are not guaranteed to have been available at the forecast cutoff.
- Actual arrival time is approximated from local actual departure time plus actual elapsed time. Airport IANA time zones are used; DST-ambiguous or nonexistent local times are excluded.
- Only uniquely matched sampled flights are evaluated. These pilot scores use a matched held-out sample and are not directly comparable to full-period Airport/Network metrics without noting the different sample and eligibility rules.
- The original Airport/Network model still uses a 200,000-flight training sample rather than the full Jan–Aug data. The pilot uses smaller, matched samples to make the BTS tail-chain experiment feasible in Colab.
- The pilot has no weather variables. Flight/Weather belongs to the teammate's separate workstream.
- The 2024 results should not be presented as a production guarantee. A live prediction needs a timestamped operational feed that makes tail assignment and prior-leg state available by each cutoff.


## Live Colab inference smoke check

On October 11, 2026, the Colab runtime reconnected and the saved Airport/Network bundle was available. The one-flight T−30m inference cell ran successfully on an eligible SFO → ORD row dated 2024-01-01: predicted delay probability 0.321888, predicted label 0. This confirms that the saved bundle can score a row through the Colab inference function and returns a probability in [0, 1]. It is a smoke check only; it does not validate current data availability or production performance. The same cell rebuilt `/content/aviation_airport_network_artifacts.zip` with the model bundle, result table, per-origin diagnostics, and feature-importance table.

**Sample-row correction (2026-11-02):** The original fixture used a placeholder `flight_id=0` and incorrectly recorded `DEP_HOUR=8`, `DEP_MINUTE=0`. Verified source row ordinal 1 has `CRS_DEP_TIME=8`, parsed as `DEP_HOUR=0`, `DEP_MINUTE=8`. The existing T−30m bundle/helper returns `0.32188825709718105` on the corrected source-consistent sample, exactly matching the notebook output. No model retraining or reserialization occurred.
