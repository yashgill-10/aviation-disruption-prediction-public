# Airport/Network and M3 public-checkout validation

Validation was run on 2026-11-17 from a fresh Python virtual environment on local branch `omruthik/airport-network-m3-docs`, based on public `main` commit `ea6ab55928caad9ead17820c456e2586a14fa9c3`. It preceded the final documentation-only commit; no source or test files changed after validation. No private data, score exports, or fitted model files were loaded. The checks exercise public interfaces and synthetic fixtures only.

## Environment

- OS: Windows 64-bit
- Python: 3.12.14
- NumPy: 2.5.5
- pandas: 2.2.3
- scikit-learn: 1.6.1
- joblib: 1.6.0

Environment setup from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r airport-network/requirements.txt
```

The requirements pin pandas and scikit-learn but leave NumPy and joblib unpinned. The versions above identify this validation environment, not the original Colab environment.

## Commands and results

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s airport-network/tests -v
```

Result: **35 tests passed** (`Ran 35 tests in 1.891s`, `OK`). This includes synthetic checks for feature schema/windows, separate horizon behavior, inference contracts, canonical IDs, score export coverage and unavailable reasons, handoff verification, and matched-evaluation validation. The feature-builder checks emitted non-fatal NumPy timedelta deprecation warnings.

```powershell
.\.venv\Scripts\python.exe -B airport-network/airport_network_inference.py
.\.venv\Scripts\python.exe -B airport-network/airport_network_integration.py
```

Results: both smoke checks passed. Inference smoke covered the two horizons, thresholds, row preservation, no input mutation, missing-feature, horizon, leakage, and snapshot-cutoff guards. Integration smoke covered exact key sets, one-to-one joins, row order, time split, leakage, duplicate keys, and coverage.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s m3 -p "test_m3_contract.py" -v
```

Result: **8 tests passed** (`Ran 8 tests in 0.867s`, `OK`). These are synthetic contract tests, not M3 model training/evaluation.

## What these checks do not establish

The public checks do not load or reproduce the private Airport/Network model bundle, regenerate the historical Airport/Network score exports, fit M3, or reproduce the saved M3 evaluation. Those tasks require private artifacts absent from this repository. The original Airport/Network scoring-time feature builder remains unrecovered, and operational feature-availability timestamps remain unverified. See `../airport-network/Airport_Network_Integration_Handoff.md`, `../m3/README.md`, and `PROJECT_COMPLETION_STATUS.md`.
