# Airport/Network ML

This folder contains Omruthik's Airport/Network workstream for the joint Aviation Disruption Prediction project. It does not include the separate Flight/Weather implementation.

## Contents

- `Airport_Network_ML_Summary.md` — 2024 dataset audit, chronological modeling setup, results, and limitations.
- `Airport_Network_Website_Results_Limitations_Draft.md` — review-only website copy for the retrospective results and unresolved availability, provenance, and cohort limits.
- `Airport_Network_Feature_Availability_Audit.md` — feature-by-feature evidence and unresolved schedule-vintage, BTS-publication, event-time, timezone, and live-eligibility gaps.
- `Airport_Network_Oct_Dec_Error_Analysis.md` — retrospective Oct–Dec 2024 performance and error analysis, with subgroup and calibration caveats.
- `Airport_Network_Retrospective_Score_Coverage.md` — separate source-ordinal and canonical cohort coverage records. Canonical exports cover the retrospective eligible memberships at 100%; row-level files remain private.
- `error_analysis/` — aggregate-only ordinal-keyed diagnostics and reproduction script; the separate canonical matched comparison is summarized in `docs/PROJECT_COMPLETION_STATUS.md`.
- `evaluation/` and `MATCHED_MODEL_EVALUATION_PROTOCOL.md` — canonical-ID join/evaluation tooling and the historical protocol. The canonical join and matched component comparison were executed on approved private artifacts; the M3 evaluation is separately documented in `m3/`.
- `Airport_Network_Integration_Handoff.md` — prediction contract, horizon-specific leakage rules, stable-ID join requirements, and combined-model handoff.
- `Airport_Network_Feature_Schema.md` — feature definitions and cutoff availability rules.
- `airport_network_inference.py` — horizon-aware inference helper and input/leakage/snapshot-cutoff guards.
- `airport_network_integration.py` — canonical `bts-flight-v1` flight-ID validation/generation via `src/canonical_flight_identity.py` (never row-position IDs), one-to-one feature joins, chronological split, and leakage checks.
- `airport_network_prediction_export.py` — per-flight, per-horizon prediction exports keyed by canonical `flight_id` or, before identity mapping, verified 1-based `cleaned_input_row_ordinal`, with coverage and unavailable-score reasons.
- `Prediction_Export_Contract.md` — input/output requirements and the unverified retrospective feature-availability caveat for prediction exports.
- `verify_handoff_package.py` — verifies the extracted package manifest and, optionally, ZIP integrity and its adjacent checksum sidecar.
- `Airport_Network_Contribution_Log.md` — completed workstream contribution record and reproducibility limits.
- `aircraft_rotation_propagation_pilot.py` — Colab pilot for prior aircraft-arrival features.
- `Aircraft_Rotation_Pilot_Results.md` — within-horizon matched pilot metrics, sample-scope caveats, and the conditional feature-coverage denominator.

## Reproducing the Colab experiments

The historical Colab workflow used cleaned 2024 BTS data and ran Airport/Network notebook cells `2 → 4 → 5 → 8 → 27 → 28`; the notebook and data are private and are not included. This is historical workflow guidance, not a byte-for-byte reproduction recipe: the exact scoring-time builder recorded by the run manifest was not recovered. The public `airport_network_feature_builder.py` is an available reference implementation, not the original scoring-time source. The aircraft-rotation pilot was separate.

The repository excludes raw/processed CSVs, ZIPs, and serialized models through `.gitignore`. The cleaned data and trained model bundle remain in approved private storage. Real inference requires the saved bundle and feature rows; the synthetic contract checks need neither. Keep T−2h and T−30m as separate model/horizon runs.

## Results caveat

The 2024 Oct–Dec metrics are a retrospective prototype comparison. The project README records that 2025 was previously opened for an exploratory frozen-baseline check and is therefore not a blind or untouched holdout; no blind final evaluation exists yet. The combined model is the M3 logistic stack: its retrospective September meta-training / October threshold-selection / November–December evaluation is documented in `m3/` and `docs/PROJECT_COMPLETION_STATUS.md`; M3 and Airport/Network live inference remain unvalidated. The BTS-derived recent-activity and aircraft-arrival features have not been verified as available from a timestamped operational feed by each cutoff, so they do not establish production readiness.

## Reproducible interface checks

From the repository root, install the pinned Airport/Network dependencies, then run the data-independent integration tests:

```powershell
python -m pip install -r airport-network/requirements.txt
python -m unittest discover -s airport-network/tests -v
python -B airport-network/airport_network_inference.py
python -B airport-network/airport_network_integration.py
python -B -m unittest discover -s m3 -p "test_m3_contract.py" -v
```

The Airport/Network suite and M3 contract suite use deterministic mocks/synthetic data and run without private data or fitted models. The Airport/Network checks cover horizon-specific inference interfaces, feature schema, export contracts, unavailable-score behavior, canonical-ID validation, row order, leakage guards, and synthetic fixtures. The two script smoke checks exercise import/input guards; they do not load the private estimator. Full Airport/Network score regeneration and the M3 training/evaluation script require private artifacts and are not run by these commands. Requirements pin pandas 2.2.3 and scikit-learn 1.6.1; NumPy and joblib are not pinned, so this is not a complete lockfile.

The per-flight export tests can also be run with `python -m unittest discover -s airport-network/tests -p "test_airport_network_prediction_export.py" -v`. The exporter requires the joint canonical flight table and an explicit eligibility table for each horizon; it never synthesizes IDs or turns unavailable scores into zero.

## Flight/Weather dashboard handoff

`attach_airport_network_predictions` in `airport_network_integration.py` is the narrow handoff for a combined dashboard. Score the same canonical flight rows with `predict_airport_network_delay`, then pass the Flight/Weather output rows and Airport/Network score rows to this adapter using the shared `flight_id` and expected horizon. It rejects missing/extra/duplicate IDs, invalid scores, mixed horizons, and output-column collisions; it preserves Flight/Weather row order and adds namespaced fields (`airport_network_delay_probability`, `airport_network_predicted_delay_15`, and the horizon/threshold when present).

This adapter is not yet called by `../src/flight_weather_web.py` (relative to the `airport-network/` directory). Connecting it there requires the Airport/Network model bundle and live or replayable, timestamped airport-state features to be available to that app. Until that work is done, the Flight/Weather dashboard remains Flight/Weather-only and must not display Airport/Network scores as live predictions.
