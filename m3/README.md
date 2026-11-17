# M3 retrospective logistic stacking experiment

M3 is a retrospective combined model that stacks existing Flight/Weather and Airport/Network probabilities. It is not a live prediction service. This repository contains the genuine training/evaluation implementation, generated aggregate reports/manifests, and synthetic contract tests. It intentionally excludes fitted model binaries, private component score exports, canonical mapping, and row-level predictions.

## Method and time partitions

Each horizon is processed independently:

1. Fit the M3 `StandardScaler` and L2 `LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, class_weight=None)` on matched September 2024 component probabilities (579,111 T−2h rows; 579,074 T−30m rows).
2. Choose the M3 operating threshold on October 2024 by maximizing F1. Do not refit the stack or scaler on October.
3. Evaluate on November–December 2024 with the fitted September stack and October threshold. This period was inspected and is exploratory, not a blind final test.

The base models and their probability columns are input artifacts; the M3 script does not retrain the base models or regenerate their predictions. No complete hyperparameter/model-family/feature-selection search history was recovered. The September row-level meta-training ID list was not saved, although the run manifest records counts, exact source hashes, and validation outcomes.

## Reproduction and test commands

The published contract tests are synthetic and need no private artifacts. From the repository root:

```powershell
python -m pip install -r airport-network/requirements.txt
python -B -m unittest discover -s m3 -p "test_m3_contract.py" -v
```

The saved run manifest records Python 3.12.14, NumPy 2.5.4, scikit-learn 1.6.1, joblib 1.6.0, and SciPy 1.18.1. The public requirements file pins pandas 2.2.3 and scikit-learn 1.6.1 but does not pin NumPy or joblib. For a historical rerun, recreate the manifest's runtime versions explicitly rather than assuming the current unconstrained resolver will select them.

To rerun the data-dependent retrospective experiment in an approved private workspace, place the exact verified component score/feature/target exports and manifests outside the repository. The script accepts these path overrides:

| Environment variable | Purpose |
| --- | --- |
| `M3_AN_EXPORT_DIR` | Airport/Network score/feature/target exports and run manifest directory. |
| `M3_FLIGHT_WEATHER_EXPORT_DIR` | Flight/Weather horizon score exports and run manifest directory. |
| `M3_WORKSPACE_ROOT` | Workspace root used to locate the private preliminary-score manifest. |
| `M3_PRELIM_MANIFEST` | Explicit path to the preliminary-score manifest. |
| `M3_AN_BUILDER` | Explicit path to the Airport/Network builder source referenced for provenance checks. |

Example PowerShell setup (replace placeholders with approved local paths; do not put private values in Git):

```powershell
$env:M3_AN_EXPORT_DIR = "<private Airport/Network export directory>"
$env:M3_FLIGHT_WEATHER_EXPORT_DIR = "<private Flight/Weather export directory>"
$env:M3_WORKSPACE_ROOT = "<private workspace root>"
$env:M3_PRELIM_MANIFEST = "<private preliminary manifest path>"
$env:M3_AN_BUILDER = "<Airport/Network builder path>"
```

Then run:

```powershell
python -B m3/train_evaluate_m3.py
```

The required Flight/Weather and Airport/Network row-level inputs are private and are not in this repository. Without them the training/evaluation command cannot run. Do not fetch validation/test partitions or substitute reconstructed scores. The saved report is evidence of the existing run; it is not a command to regenerate it.

## Source and artifact provenance

The genuine experiment script was recovered from the private final-submission handoff archive. The original script SHA-256 recorded by the run manifest is `5e3647f5837bd34880de2607e6940fcfc8b2be5a9bdccb843a0c866a4c6d119a`. The public source is parameterized so external input locations are provided by environment variables instead of a personal filesystem path; it does not include the private inputs. The run manifest's original script hash refers to the experiment source used for the run, not necessarily the sanitized public copy's bytes.

The Airport/Network score manifest recorded builder SHA-256 `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36`, and its contemporaneous local-builder field is `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf`. Neither is the current public reference builder's Git-blob-content SHA-256 `86b690c7151ceecda0d7a067dbd3de07ad4f53d8bedb595d1740633ffe57d0f4`. The exact original scoring-time builder remains unrecovered. The current public file is a reference implementation and cannot establish byte-identical feature generation.

The M3 fitted model files were serialized by the script after the evaluation predictions and metrics had been calculated. No refit during November–December is documented, but a pre-evaluation serialized model hash was not established. Model files and row-level score exports remain private; their identifiers/checksums are in the generated manifests.

## Results and limitations

See `m3_evaluation_report.json`, `m3_run_manifest.json`, and `M3_interpretation_and_error_analysis.md` for the existing aggregate evidence. Both horizons have exact matched component IDs and target parity per the saved run validation. The report describes M3 as higher on average precision, ROC-AUC, recall, and lower Brier score than the components at both horizons, while precision is lower and F1/balanced accuracy are mixed. These are descriptive retrospective results; no significance tests or confidence intervals were saved.

Feature availability is not established: schedule-vintage and BTS publication/ingestion timestamps, recent Airport/Network operational event timestamps, and live departure/cancellation eligibility are unavailable. Flight/Weather weather uses an observation-time-plus-15-minute proxy, not confirmed receipt time. No untouched final test period exists; 2025 was inspected. Do not claim live readiness or causal improvement.

## Related evidence

- [Project completion status](../docs/PROJECT_COMPLETION_STATUS.md)
- [Joint exploratory research report](../reports/final_joint_exploratory_research_report.md)
- [Root contribution record](../CONTRIBUTIONS.md)
- [Release checksum manifest](../RELEASE_MANIFEST_SHA256.txt)

The generated run manifest still references an unavailable `M3_contribution_log.md` attachment and an external/private artifact inventory. Its `joint_review_status: PENDING` field is a snapshot captured at run time, not a current repository review-status indicator. This repository-level contribution record and release manifest are the available public equivalents; they are not replacements for omitted restricted files.
