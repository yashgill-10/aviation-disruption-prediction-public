# Aggregate Airport/Network error-analysis evidence

This folder contains aggregate-only outputs for the retrospective October–December 2024 Airport/Network analysis. It does not contain per-flight records or the large ordinal-keyed score exports.

The period was already inspected. Keys are cleaned-source ordinals; canonical membership has not been joined. The exact score-builder hash in the run manifest is not available, and BTS-derived feature availability and live eligibility are unverified. See `../Airport_Network_Oct_Dec_Error_Analysis.md`, `../Airport_Network_Feature_Availability_Audit.md`, and `provenance_summary.json`.

Files include overall/month/carrier/origin summaries, Wilson intervals for subgroup recall, and an aggregate comparison against the saved holdout report. The row-level source scores are deliberately excluded from this repository package. The report reproduces aggregate counts and metrics; it does not establish byte-identical scoring code or matched Flight/Weather performance.

## Reproduce the aggregates when verified score files are available

Obtain the two score-export files by an approved, security-cleared transfer. Keep them outside Git. The analysis script checks their SHA-256 values against `provenance_summary.json` before reading them; it also checks the packaged saved-report CSV. A mismatch stops the run.

From the repository root, run:

```powershell
python airport-network/error_analysis/run_local_error_analysis.py `
  --t2h-scores "C:\path\to\preliminary_t_minus_2h_scores_by_source_ordinal.csv.gz" `
  --t30m-scores "C:\path\to\preliminary_t_minus_30m_scores_by_source_ordinal.csv.gz" `
  --saved-results airport-network/colab_model_artifacts/aviation_airport_network_results.csv `
  --output-dir airport-network/error_analysis
```

Then recalculate the Wilson intervals from the generated carrier/origin tables:

```powershell
powershell -ExecutionPolicy Bypass -File airport-network/error_analysis/summarize_recall_uncertainty.ps1 `
  -OutputDirectory airport-network/error_analysis
```

The scripts summarize existing predictions; they do not rebuild features or rescore flights. The score-generation builder-hash mismatch remains unresolved. The row-level inputs are ordinal-keyed, not a verified canonical cohort.
