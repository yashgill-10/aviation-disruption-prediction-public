# Airport/Network ML

This folder contains Omruthik's Airport/Network workstream for the joint Aviation Disruption Prediction project. It does not include the separate Flight/Weather implementation.

## Contents

- `Airport_Network_ML_Summary.md` — 2024 dataset audit, chronological modeling setup, results, and limitations.
- `Airport_Network_Website_Results_Limitations_Draft.md` — review-only website copy for the retrospective results and unresolved availability, provenance, and cohort limits.
- `Airport_Network_Feature_Availability_Audit.md` — feature-by-feature evidence and unresolved schedule-vintage, BTS-publication, event-time, timezone, and live-eligibility gaps.
- `Airport_Network_Oct_Dec_Error_Analysis.md` — retrospective Oct–Dec 2024 performance and error analysis, with subgroup and calibration caveats.
- `Airport_Network_Retrospective_Score_Coverage.md` — verified local ordinal-export hashes, score coverage, and explicit unavailable reasons; canonical cohort coverage is pending.
- `error_analysis/` — aggregate-only supporting tables, holdout-report reconciliation, provenance summary, and local reproduction script; row-level score files are not included.
- `evaluation/` and `MATCHED_MODEL_EVALUATION_PROTOCOL.md` — draft safe canonical-ID score join and strict three-model comparison tooling; synthetic-only validation, awaiting approved real handoff files.
- `Airport_Network_Integration_Handoff.md` — prediction contract, horizon-specific leakage rules, stable-ID join requirements, and combined-model handoff.
- `Airport_Network_Feature_Schema.md` — feature definitions and cutoff availability rules.
- `airport_network_inference.py` — horizon-aware inference helper and input/leakage/snapshot-cutoff guards.
- `airport_network_integration.py` — canonical `bts-flight-v1` flight-ID validation/generation via `src/canonical_flight_identity.py` (never row-position IDs), one-to-one feature joins, chronological split, and leakage checks.
- `airport_network_prediction_export.py` — per-flight, per-horizon prediction exports keyed by canonical `flight_id` or, before identity mapping, verified 1-based `cleaned_input_row_ordinal`, with coverage and unavailable-score reasons.
- `Prediction_Export_Contract.md` — input/output requirements and the unverified retrospective feature-availability caveat for prediction exports.
- `verify_handoff_package.py` — verifies the extracted package manifest and, optionally, ZIP integrity and its adjacent checksum sidecar.
- `Airport_Network_Contribution_Log.md` — authorship and pending joint-review items.
- `aircraft_rotation_propagation_pilot.py` — Colab pilot for prior aircraft-arrival features.
- `Aircraft_Rotation_Pilot_Results.md` — within-horizon matched pilot metrics, sample-scope caveats, and the conditional feature-coverage denominator.

## Reproducing the Colab experiments

Use the cleaned 2024 BTS data in Colab. In the project notebook, run the Airport/Network cells in order: `2 → 4 → 5 → 8 → 27 → 28`. The aircraft-rotation pilot is a separate run. Follow the notebook's notes and skip the Flight/Weather-only cells when reproducing this workstream.

The repository excludes raw/processed CSVs, ZIPs, and serialized models through `.gitignore`. The cleaned data and trained model bundle remain in Colab. The source helper `airport_network_inference.py` needs that saved model bundle to perform inference.

## Results caveat

The 2024 Oct–Dec metrics are a retrospective prototype comparison. The project README records that 2025 was previously opened for an exploratory frozen-baseline check and is therefore not a blind or untouched holdout; no blind final evaluation exists yet, and the combined Flight/Weather + Airport/Network model remains outstanding. The BTS-derived recent-activity and aircraft-arrival features have not been verified as available from a timestamped operational feed by each cutoff, so they do not establish production readiness.

## Reproducible interface checks

From the repository root, install the pinned Airport/Network dependencies, then run the data-independent integration tests:

```powershell
python -m pip install -r airport-network/requirements.txt
python -m unittest discover -s airport-network/tests -v
python airport-network/airport_network_inference.py
python airport-network/airport_network_integration.py
```

All tests are `unittest`-discoverable (`python -m unittest discover -s airport-network/tests -v` from the repository root) and use deterministic mock estimators or synthetic canonical-format IDs, so they run without the private Colab dataset, serialized model, or row-level data. They cover horizon-specific inference, exact flight-ID coverage, row-order preservation, score validation, leakage checks, and the chronological split. The two script smoke checks cover additional input and cutoff guards. A real prediction still requires the saved model bundle and cutoff-ready Airport/Network feature rows in Colab.

The per-flight export tests can also be run with `python -m unittest discover -s airport-network/tests -p "test_airport_network_prediction_export.py" -v`. The exporter requires the joint canonical flight table and an explicit eligibility table for each horizon; it never synthesizes IDs or turns unavailable scores into zero.

## Flight/Weather dashboard handoff

`attach_airport_network_predictions` in `airport_network_integration.py` is the narrow handoff for a combined dashboard. Score the same canonical flight rows with `predict_airport_network_delay`, then pass the Flight/Weather output rows and Airport/Network score rows to this adapter using the shared `flight_id` and expected horizon. It rejects missing/extra/duplicate IDs, invalid scores, mixed horizons, and output-column collisions; it preserves Flight/Weather row order and adds namespaced fields (`airport_network_delay_probability`, `airport_network_predicted_delay_15`, and the horizon/threshold when present).

This adapter is not yet called by `../src/flight_weather_web.py` (relative to the `airport-network/` directory). Connecting it there requires the Airport/Network model bundle and live or replayable, timestamped airport-state features to be available to that app. Until that work is done, the Flight/Weather dashboard remains Flight/Weather-only and must not display Airport/Network scores as live predictions.
