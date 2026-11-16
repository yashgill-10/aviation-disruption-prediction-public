# Airport/Network Feature Schema

Each model feature must be available by the forecast cutoff. `flight_id` is a stable join key, not a model input.

| feature | role | type | horizon | definition | availability_rule |
| --- | --- | --- | --- | --- | --- |
| flight_id | stable_join_key | string | All | Canonical `bts-flight-v1:<SHA-256>` ID from `src/canonical_flight_identity.py`, assigned once to the canonical deduplicated flight table before either workstream branches. Never a row-position or arbitrary integer. | Preserve the same ID in all feature tables; do not regenerate after filtering, sorting, sampling, or feature engineering. |
| MONTH | model_feature | integer | T−2h and T−30m | Scheduled calendar month. | Known before departure; derives from scheduled date. |
| DAY_OF_MONTH | model_feature | integer | T−2h and T−30m | Scheduled day of month. | Known before departure; derives from scheduled date. |
| DEP_HOUR | model_feature | integer | T−2h and T−30m | Scheduled local departure hour. | Known before departure; derived from CRS_DEP_TIME. |
| DEP_MINUTE | model_feature | integer | T−2h and T−30m | Scheduled local departure minute. | Known before departure; derived from CRS_DEP_TIME. |
| OP_UNIQUE_CARRIER | model_feature | category | T−2h and T−30m | Operating carrier. | Known from the published flight schedule. |
| ORIGIN | model_feature | category | T−2h and T−30m | Origin airport code. | Known from the published flight schedule. |
| DEST | model_feature | category | T−2h and T−30m | Destination airport code. | Known from the published flight schedule. |
| airport_prior7_flights | model_feature | integer | T−2h and T−30m | Operated departures from the origin airport over the prior seven calendar days. | Exclude the prediction date; only prior dates may contribute. |
| airport_prior7_delay_rate | model_feature | float | T−2h and T−30m | Share of prior-seven-day operated origin departures delayed at least 15 minutes. | Exclude the prediction date; compute from prior outcomes only. |
| airport_prior7_mean_delay_min | model_feature | float | T−2h and T−30m | Mean departure delay in minutes for prior-seven-day operated origin departures. | Exclude the prediction date; compute from prior outcomes only. |
| airport_prior7_avg_destinations_per_active_day | model_feature | float | T−2h and T−30m | Average distinct destinations served per active day by the origin airport in the prior seven calendar days. | Exclude the prediction date; only prior dates may contribute. |
| scheduled_origin_departures_prior2h | model_feature | integer | T−2h and T−30m | Other flights in the BTS table scheduled at the origin airport in the half-open interval [target scheduled departure − 2 hours, target scheduled departure). Computed from the target flight's scheduled local departure time; shared by T−2h and T−30m. | Retrospective BTS schedule count. No schedule snapshots, publication timestamps, revision history, or cancellation timestamps are present to establish schedule-vintage availability at either forecast cutoff; operational availability is unverified. |
| tminus2h_recent_departures_2h | model_feature | integer | T−2h | Observed operated departures from the origin airport in the two hours before the T−2h cutoff. | Include only events observed before the cutoff; BTS reconstruction is retrospective. |
| tminus2h_recent_delay_rate_2h | model_feature | float | T−2h | Fraction of those recent origin departures delayed at least 15 minutes. | Only use delay status known before the cutoff; BTS reconstruction is retrospective. |
| tminus2h_recent_mean_delay_min_2h | model_feature | float | T−2h | Mean departure delay in minutes among those recent origin departures. | Only use delay values known before the cutoff; BTS reconstruction is retrospective. |
| tminus30m_recent_departures_2h | model_feature | integer | T−30m | Observed operated departures from the origin airport in the two hours before the T−30m cutoff. | Include only events observed before the cutoff; BTS reconstruction is retrospective. |
| tminus30m_recent_delay_rate_2h | model_feature | float | T−30m | Fraction of those recent origin departures delayed at least 15 minutes. | Only use delay status known before the cutoff; BTS reconstruction is retrospective. |
| tminus30m_recent_mean_delay_min_2h | model_feature | float | T−30m | Mean departure delay in minutes among those recent origin departures. | Only use delay values known before the cutoff; BTS reconstruction is retrospective. |
| FL_DATE | join_or_split_field | date | T−2h and T−30m | Scheduled local flight date, used for chronological partitions and airport history joins. | Never pass as a fitted feature unless the model is explicitly trained to use it. |
| delay_15 | target | binary | T−2h and T−30m | 1 when DEP_DELAY is at least 15 minutes, otherwise 0. | Outcome only; never use as a prediction-time feature for the scored flight. |
| scheduled_departure_utc | availability_audit_field | timezone-aware datetime | T−2h and T−30m | Scheduled departure instant in UTC; used only to establish the forecast cutoff. | Required by validate_operational_snapshot_cutoff; must be timezone-aware, never pass as a model feature unless explicitly trained. |
| snapshot_available_at_utc | availability_audit_field | timezone-aware datetime | T−2h and T−30m | Timestamp when the feature snapshot was available to the prediction system. | Must be timezone-aware and at or before the horizon cutoff; also verify source lineage for every underlying event and status. |
