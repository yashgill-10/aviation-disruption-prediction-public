# Aircraft Rotation Pilot Results

This is a separate retrospective matched-sample experiment for the Airport/Network track. It is not directly comparable with the larger 2024 Airport/Network test metrics.

The pilot samples T−2h and T−30m populations separately. Within each horizon, the baseline and aircraft-augmented variants use the same retained rows; the two horizon samples are not a paired cross-horizon cohort. Interpret the feature comparison within each horizon only.

| Horizon | Feature set | Sep threshold | Test n | F1 | PR-AUC | Recall | Precision | Balanced accuracy |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| T−2h | Schedule and route | 0.5242 | 19,990 | 0.3138 | 0.2311 | 0.5670 | 0.2169 | 0.5813 |
| T−2h | Schedule, route, prior aircraft arrival | 0.5219 | 19,990 | 0.3410 | 0.3298 | 0.4897 | 0.2616 | 0.6083 |
| T−30m | Schedule and route | 0.5031 | 19,986 | 0.3240 | 0.2332 | 0.6660 | 0.2141 | 0.5913 |
| T−30m | Schedule, route, prior aircraft arrival | 0.5262 | 19,986 | 0.4958 | 0.4697 | 0.5487 | 0.4523 | 0.6219 |

At the chosen F1 thresholds, the aircraft-augmented configuration had F1 differences of +0.0272 at T−2h and +0.1718 at T−30m; PR-AUC differences were +0.0987 and +0.2365. Recall was lower at both horizons while precision was higher. These are descriptive pilot comparisons, not causal estimates. A unique tail match was available for 119,952 of 120,000 pilot rows; 47 sampled keys were ambiguous. The reported prior-arrival feature coverage of 99.90% is conditional on a unique tail match and a valid scheduled cutoff UTC: the pilot drops rows without either before computing that percentage. It is not coverage over all sampled eligible rows. The saved results do not include separate counts for unmatched tail keys or invalid cutoff conversions, so overall feature coverage across the full pilot sample cannot be inferred.

The pilot script now prints and saves these match, cutoff-conversion, retained-row, and conditional-coverage denominators to `aviation_aircraft_rotation_pilot_diagnostics.json` when rerun. That diagnostic was not regenerated here because the Colab BTS monthly archives are not present in this workspace.

These are retrospective BTS-derived features. The project has not established that tail assignments and prior-leg arrival data were available from a timestamped live feed before each prediction cutoff. Treat these scores as a pilot, not a production claim.
