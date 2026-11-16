# Matched model evaluation protocol — draft for joint approval

**Status:** proposed; not jointly approved or run. This prepares the comparison without claiming canonical membership or combined-model results.

## Comparison population

- Use the jointly approved canonical `flight_id` cohort separately for T−2h and T−30m.
- Each cohort row must include the agreed `delay_15` target and, if used in reporting, a fixed partition column.
- Require every model export to contain exactly one row for every cohort ID, including explicit unavailable rows. Reject missing or extra IDs; do not silently intersect or drop rows.
- Report each model's scored/unavailable coverage over the full approved cohort. For metric comparisons, use the same intersection of IDs scored by all three variants and report its size and delay rate.
- The three variants are Flight/Weather-only, Airport/Network-only, and combined. A combined prediction file must be produced by the agreed combined model; do not synthesize it from the other two scores.

## Score and metric rules

- Preserve each model's existing saved probability, threshold, and thresholded prediction. Do not tune or recalibrate thresholds on October–December 2024, which has already been inspected.
- On the all-three-scored matched IDs, report average precision (PR-AUC), ROC-AUC, Brier score, log loss, accuracy, precision, recall, F1, balanced accuracy, confusion matrix, and positive-label rate using each model's recorded threshold.
- Report unavailable rows by model and explicit reason over the full approved cohort. Never replace missing predictions with zero.
- Preserve the same target and partition across all models; fail if any row-level target or partition disagrees.
- Label October–December results retrospective/exploratory, not a blind final estimate. Clearly distinguish retrospective BTS feature availability from operationally verified availability.

## Standardized score input schema

One file per model and horizon, normalized to these columns before evaluation:

| Column | Required | Rule |
| --- | --- | --- |
| `flight_id` | Yes | Canonical key, unique and nonmissing. |
| `score_status` | Yes | Exactly `scored` or `unavailable`. |
| `delay_probability` | Yes | Finite in [0, 1] when scored; null when unavailable. |
| `decision_threshold` | Yes | Finite, constant within each model/horizon, in [0, 1] when scored. |
| `predicted_delay_15` | Yes | 0/1 when scored and consistent with the recorded threshold; null when unavailable. |
| `unavailable_reason` | Yes | Nonempty for every unavailable row; empty for scored rows. |
| `forecast_horizon` | Optional | If present, every row must match the selected horizon. |
| `delay_15`, `partition` | Optional | If present, must match the canonical cohort file exactly. |

The comparison script refuses incomplete key coverage, duplicate IDs, extra IDs, score/status contradictions, target disagreement, threshold variation, or an empty all-three-scored set. It writes row-level matched predictions and a metrics/coverage JSON report only after those checks pass.

## Current dependencies

Before evaluation, re-key the local Airport/Network ordinal scores with `join_ordinal_scores_to_canonical_ids.py` using the verified canonical mapping, exact cohort-membership files, and score run manifest. The join must retain each cohort ID, preserve explicit unavailable statuses/reasons, and leave any non-unique mapping unresolved; it must not infer IDs or turn missing scores into zero. It records source file checksums and copies the model/builder/run provenance into its output manifest.

This workflow is prepared locally but blocked pending approved access to the canonical mapping and Flight/Weather score artifacts, which are restricted private handoff files not included in this repository. The join and evaluator have passed an end-to-end synthetic smoke check for both horizons (four synthetic flights per cohort); they have not been run against project mapping or cohort files. Restricted artifacts must only be obtained through the approved private handoff channel. No dashboard integration is authorized by this protocol draft.

## Run sequence after approved artifacts are available

First verify the mapping, membership, score-export, and run-manifest checksums against the signed/approved handoff manifest. Use only checksum-verified copies obtained through the approved private channel; do not substitute unverified files. Then run the ordinal join from the project root, using the local Airport/Network full-ordinal exports and the verified canonical mapping/membership inputs:

```powershell
python airport-network/evaluation/join_ordinal_scores_to_canonical_ids.py `
  --mapping "C:\handoff\cleaned_row_to_canonical_mapping.csv.gz" `
  --expected-mapping-sha256 "REPLACE_WITH_VERIFIED_SHA256" `
  --t2h-membership "C:\handoff\t_minus_2h_membership.csv.gz" `
  --t30m-membership "C:\handoff\t_minus_30m_membership.csv.gz" `
  --t2h-scores "C:\secure\preliminary_t_minus_2h_scores_by_source_ordinal.csv.gz" `
  --t30m-scores "C:\secure\preliminary_t_minus_30m_scores_by_source_ordinal.csv.gz" `
  --source-run-manifest "C:\secure\run_manifest.json" `
  --output-dir "C:\secure\phase2_canonical_exports"
```

Review the join manifest, confirm exact membership counts and no missing/extra IDs, retain any explicit unavailable rows, and resolve any target conflicts with the data owner. Only then run the evaluator separately for each horizon, supplying the approved cohort with `flight_id,delay_15[,partition]`, the Airport/Network canonical export generated above, the canonical Flight/Weather export, and an actual combined-model export:

```powershell
python airport-network/evaluation/evaluate_matched_models.py `
  --horizon 'T-2h' --cohort "C:\handoff\t_minus_2h_cohort.csv.gz" `
  --flight-weather "C:\handoff\flight_weather_t_minus_2h.csv.gz" `
  --airport-network "C:\secure\phase2_canonical_exports\airport_network_t_minus_2h_canonical_scores.csv.gz" `
  --combined "C:\handoff\combined_t_minus_2h.csv.gz" `
  --output-stem "C:\secure\matched_model_comparison\aviation_models"
```

Repeat with `--horizon 'T-30m'` and the corresponding T−30m inputs. Do not fabricate a combined score by blending the other models. These commands are documented only; they have not been run on project artifacts.
