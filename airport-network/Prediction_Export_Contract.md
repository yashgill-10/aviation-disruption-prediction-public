# Airport/Network prediction export contract

`airport_network_prediction_export.py` exports one row per selected stable key and prediction horizon. It delegates probability and threshold computation to the existing `predict_airport_network_delay` function, so it uses the feature lists and thresholds already stored in the model bundle.

## Inputs

- `canonical_flights`: one row per selected key. Use the agreed canonical table for final exports or `cleaned_input_row_ordinal` for preliminary exports. The ordinal is the 1-based data-record position (excluding the header) in the exact input CSV, assigned before filtering or deduplication; record its SHA-256. Optional `delay_15` and `partition` columns are copied. This module does not create canonical IDs or reconcile raw records.
- `features_by_horizon`: a mapping with exactly `T−2h` and `T−30m`, each containing the selected key (`flight_id` or `cleaned_input_row_ordinal`) and the model's feature-ready values. The feature builder must preserve the key unchanged.
- `eligibility_by_horizon`: one table for each horizon with every base key exactly once and columns for the selected key, `eligible_for_scoring`, and `unavailable_reason`. Every ineligible key needs a concrete reason; eligible IDs must have a blank reason. The joint team must supply the agreed offline eligibility decisions.
- `bundle`: the existing saved model bundle. Do not change the model or its feature definitions to use this exporter.
- `key_column`: `"flight_id"` for the final canonical export, or `"cleaned_input_row_ordinal"` for a preliminary source-row export. The original `flight_id` argument remains supported as a compatibility alias.

## Output

The prediction table contains the selected key (`flight_id` or `cleaned_input_row_ordinal`), optional target/partition, `forecast_horizon`, `delay_probability`, `decision_threshold`, `predicted_delay_15`, `score_status`, `unavailable_reason`, and `feature_availability`. Missing scores remain null. Feature rows that are absent or have required model values missing are marked unavailable with a reason. Duplicate keys, extra keys, incomplete eligibility coverage, or absent eligibility reasons fail loudly.

The coverage table reports expected, scored, and unavailable rows, score coverage, and unavailable counts by reason, separately for each horizon. Save both returned tables as CSVs, for example:

```python
predictions, coverage = export_airport_network_predictions(
    canonical_flights,
    features_by_horizon,
    eligibility_by_horizon,
    bundle,
)
predictions.to_csv("airport_network_predictions_by_flight.csv", index=False)
coverage.to_csv("airport_network_prediction_coverage.csv", index=False)
```

The current feature builder reconstructs recent airport activity from final BTS outcomes. Therefore, all output rows are marked `retrospective_bts_availability_unverified`; this export is not evidence of operational availability by T−2h or T−30m. Do not populate an unavailable probability with zero. The unresolved 2024-08-25 F9 MIA→ATL identity must remain quarantined unless the joint canonicalization review establishes a defensible ID.

Run the export tests with:

```bash
python -m unittest discover -s airport-network/tests -p "test_airport_network_prediction_export.py" -v
```
