# Aggregate Airport/Network error-analysis evidence

This directory contains aggregate-only October–December 2024 Airport/Network diagnostics: overall/month/carrier/origin tables, confusion and calibration summaries, Wilson intervals for subgroup recall, and a reconciliation against the saved holdout report. It contains no individual flight IDs, row-level scores, or private datasets.

## Two different analysis populations

The diagnostics in this directory were calculated from ordinal-keyed Airport/Network scores. The original source-ordinal analysis includes 1,768,379 scored rows at T−2h and 1,768,261 at T−30m. Its unit of identity is `cleaned_input_row_ordinal`; it is not the canonical matched model comparison.

The later canonical-ID component comparison uses exact common scored membership across Flight/Weather and Airport/Network: 1,768,340 flights at T−2h and 1,768,222 at T−30m. The 39-row difference at each horizon consists of unresolved ambiguous identities without canonical IDs. They remain in ordinal diagnostics and are excluded from canonical comparisons; no IDs were inferred. See `../../docs/PROJECT_COMPLETION_STATUS.md` for the matched results.

## Interpretation guide

- False positives and false negatives are reported at the saved Airport/Network operating threshold. They describe the threshold trade-off: high false-positive counts reflect the low base rate and recall-oriented operating point; false negatives are delayed flights the threshold misses. Do not read these counts as live forecast outcomes.
- The saved aggregate report includes expected calibration error (10 bins) of 27.94% at T−2h and 27.36% at T−30m. This is a retrospective diagnostic on the ordinal-keyed scores. Probability calibration is not established for deployment, and these probabilities are not proven operationally calibrated.
- Carrier and origin summaries use a minimum subgroup size of 1,000. Wilson intervals are simple binomial intervals for subgroup recall; they do not account for model selection, temporal dependence, multiple comparisons, or uncertainty in the data pipeline.
- Subgroup differences are descriptive, not causal. No significance claim is made, and performance may reflect route, season, or cohort composition.
- The historical feature-ablation baseline did not retain row-level predictions, so paired uncertainty for those metric differences cannot be computed from the saved evidence.

## Provenance and limitations

The ordinal-keyed error-analysis metrics reconcile to the saved Airport/Network holdout report. The canonical matched comparison is separately documented. The exact original scoring-time builder has not been recovered: the score manifest records `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36`, its contemporaneous available-builder field is `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf`, and neither is the current public reference builder's byte hash. The original source-ordinal error-analysis inputs are private and omitted from this repository.

BTS schedule vintage/publication timestamps, airport event availability timestamps, and operational eligibility timestamps are unavailable. These diagnostics are retrospective and do not validate live readiness.

## Reproduce aggregate diagnostics

With the exact verified private source-ordinal score exports and saved aggregate report, stored outside Git, install the pinned dependencies and run:

```powershell
python -m pip install -r airport-network/requirements.txt
python airport-network/error_analysis/run_local_error_analysis.py `
  --t2h-scores "C:\secure\preliminary_t_minus_2h_scores_by_source_ordinal.csv.gz" `
  --t30m-scores "C:\secure\preliminary_t_minus_30m_scores_by_source_ordinal.csv.gz" `
  --saved-results "C:\secure\aviation_airport_network_results.csv" `
  --output-dir "C:\secure\airport_network_error_analysis"
```

Then recalculate Wilson intervals from the generated aggregate tables:

```powershell
powershell -ExecutionPolicy Bypass -File airport-network/error_analysis/summarize_recall_uncertainty.ps1 `
  -OutputDirectory "C:\secure\airport_network_error_analysis"
```

The script verifies expected source hashes before processing. This reproduces the ordinal-keyed aggregate analysis only; it does not build features, score flights, or reproduce the canonical matched comparison. The private input files are not supplied here.
