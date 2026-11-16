# Airport/Network feature availability audit

**Status: retrospective only; operational availability at T−2h and T−30m is unverified.** This audit describes the existing trained feature definitions. It does not change feature values, retrain the model, or claim live readiness.

The builder applies schedule cutoffs in a naive origin-local BTS clock; it does not convert airport-local times to timezone-aware UTC instants. DST folds/gaps and schedule timezone interpretation therefore also need resolution before translating these retrospective windows into a live, timestamped service.

## Feature-by-feature evidence

| Trained feature(s) | Existing definition and source | T−2h evidence | T−30m evidence | Finding |
| --- | --- | --- | --- | --- |
| `MONTH`, `DAY_OF_MONTH`, `DEP_HOUR`, `DEP_MINUTE`, `OP_UNIQUE_CARRIER`, `ORIGIN`, `DEST` | Scheduled date/time, operating carrier, origin and destination from BTS schedule fields; hour/minute are derived from `CRS_DEP_TIME`. | Schedule values logically describe the planned flight, but the archived input has no schedule-vintage/publication timestamp or revision history. | Same evidence gap. | **Unverified vintage.** The final retrospective BTS row does not prove the exact value was known at either cutoff. |
| `airport_prior7_flights`, `airport_prior7_delay_rate`, `airport_prior7_mean_delay_min`, `airport_prior7_avg_destinations_per_active_day` | Prior seven calendar dates at the origin, excluding the prediction date; aggregates use BTS operation and final outcome fields. | Historical dates precede the cutoff, but BTS release, publication, ingestion and correction timestamps are not available in the source used. | Same evidence gap. | **Retrospective history only.** Earlier event dates alone do not prove those values had been published by the cutoff. |
| `scheduled_origin_departures_prior2h` | Count of other BTS scheduled origin departures in `[target scheduled departure − 2 hours, target scheduled departure)`. This is anchored to the target scheduled departure and is the same feature definition at both horizons. | The window ends at scheduled departure, two hours after this forecast cutoff. No schedule snapshot, publication time, revision history or cancellation time is recorded to show which schedule rows were available at the cutoff. | The window ends at scheduled departure, 30 minutes after this forecast cutoff. Same missing schedule-vintage evidence. | **Availability unverified.** Do not reinterpret the feature as a count as-of the forecast cutoff. |
| `tminus2h_recent_departures_2h`, `tminus2h_recent_delay_rate_2h`, `tminus2h_recent_mean_delay_min_2h` | Recent origin activity reconstructed for the two hours before the T−2h cutoff from BTS scheduled local time plus final `DEP_DELAY`, with final cancellation fields. | The source has final outcomes, not event-status, observation, publication or ingestion timestamps representing the information state at T−2h. | Not used by the T−30m model. | **Retrospective reconstruction.** Temporal filtering by reconstructed event time is not evidence that delay/status was observable then. |
| `tminus30m_recent_departures_2h`, `tminus30m_recent_delay_rate_2h`, `tminus30m_recent_mean_delay_min_2h` | Recent origin activity reconstructed for the two hours before the T−30m cutoff from BTS scheduled local time plus final `DEP_DELAY`, with final cancellation fields. | Not used by the T−2h model. | The source has final outcomes, not event-status, observation, publication or ingestion timestamps representing the information state at T−30m. | **Retrospective reconstruction.** Operational availability is unverified. |

## Eligibility is also retrospective

The experimental cohort uses final BTS `CANCELLED` and `DEP_DELAY` to exclude cancelled flights and flights reconstructed as already departed by the horizon. These final outcomes cannot establish whether a flight was eligible for a live prediction at the cutoff. No timestamped operational departure/cancellation status source is included in this handoff.

## What the row-level exports establish

The local ordinal score exports identify each source row and its model score/status. Their `feature_availability_status` is `retrospective_bts_availability_unverified`; the run manifest records unknown schedule-vintage and BTS event/publication/ingestion times. The exports do not contain feature-specific operational availability timestamps. Consequently, they cannot establish that any BTS-derived network feature was genuinely available at either cutoff.

## Evidence needed to revisit the findings

- Versioned schedule snapshots or an equivalent schedule feed with publication/ingestion times, revisions and cancellations, so the exact scheduled-volume count can be reconstructed as of each cutoff.
- Timestamped operational departure and cancellation events, including when the event/status became observable, for live eligibility and recent airport activity.
- BTS historical release/ingestion timestamps or archived as-of snapshots for the prior-seven-day aggregates.
- A reproducible audit showing that each feature value uses only records available by the respective cutoff, while preserving the trained feature definition.

Until such evidence is supplied and checked, retain the `retrospective_bts_availability_unverified` status, describe the experiment as retrospective, and do not claim live readiness.
