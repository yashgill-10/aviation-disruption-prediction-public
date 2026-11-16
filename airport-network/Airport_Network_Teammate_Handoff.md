# Airport/Network workstream handoff

This concise handoff points to the authoritative, detailed notes in [Airport_Network_Integration_Handoff.md](Airport_Network_Integration_Handoff.md). Use that file for the verified model compatibility tag, setup instructions, data-cleaning limits, training/calibration/test procedure, and artifact inventory.

The existing model bundle's embedded scikit-learn pickle tag is **1.6.1**. Deserialization is **PASS** for the package ZIP verified against SHA-256 `2be938048f58c9955e5005dac94dc3d35ab380ef6072f423d104a8f293000d47` at PR head `d595ff63aa44885f576798ddc68873742ca8d683`. Using isolated Python 3.13.2, NumPy 2.5.4, pandas 2.2.3, scikit-learn 1.6.1, and joblib 1.6.0, `joblib.load()` succeeded without warnings and returned both horizon `Pipeline` estimators. This verifies loading only; it is not an inference or evaluation result. The original Colab NumPy/joblib versions remain unknown, and no retraining or reserialization was done.

The PR branch is the authoritative source for the builder, inference helper, score-handoff adapter, tests, dependencies, and documentation. The handoff ZIP is a package built from these branch files plus the Colab model and saved aggregate evaluation CSVs. Keep PR #1 draft/open against `feature/flight-weather-ml`; do not merge to `main`.

The BTS features are retrospective. Final BTS delay records do not provide ingestion timestamps proving that the recent-state values were available at either prediction cutoff. The 2024 Oct–Dec evaluation was inspected and is not an untouched final test.