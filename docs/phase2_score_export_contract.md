# Phase 2 Airport/Network Score Export Contract

**Status:** Draft handoff specification. No mapping file, feature rows, or scores have been generated or transferred.

## 1. Reduced cleaned-row to canonical-ID mapping

The useful handoff is a gzip-compressed CSV with one row for each data row in Omruthik's original cleaned input. It preserves that file's original row ordinal and provides only the identity result needed to align the workstreams.

Proposed path: `data/processed/phase2_handoff_2024/cleaned_row_to_canonical_mapping.csv.gz` (generated locally, ignored, and shared only through an approved private channel).

| Column | Type | Meaning |
|---|---|---|
| `cleaned_input_row_ordinal` | integer | 1-based original cleaned CSV data-record ordinal (header excluded), as recorded by the saved crosswalk. It identifies a source row; it is not a post-filter row position. |
| `canonical_flight_id` | nullable string | Exact `bts-flight-v1:<sha256>` ID only for a unique, complete cleaned-to-raw match. Empty for unresolved rows. |
| `match_status` | string enum | Copy the authoritative status from the saved crosswalk; values are listed below. |

Include every original input row, including exact duplicate rows, so Omruthik can match his original row ordinals without relying on sort order or route/time joins. Preserve each duplicate row's own ordinal. Do not assign IDs to ambiguous, unmatched, or identity-incomplete rows.

The status values and observed counts in the pre-dedup crosswalk are:

| `match_status` | Rows | `canonical_flight_id` |
|---|---:|---|
| `unique_exact_raw_match_identity_recovered` | 6,982,525 | Required |
| `ambiguous_multiple_raw_candidates` | 220 | Empty |
| `unique_exact_raw_match_identity_incomplete` | 1 | Empty |
| `no_exact_raw_match` | 20 | Empty |
| **Total original cleaned rows** | **6,982,766** | |

The 220 ambiguous original rows correspond to 110 deduplicated cleaned rows, each with two identical original rows. Those are unresolved; the duplicate copies are not separate canonical flights. The one incomplete row is the quarantined F9 MIA–ATL record. The 20 unmatched rows remain unresolved. These counts are from the existing run report; they are not a new scan.

Use a small JSON sidecar and SHA-256 file to record the mapping schema version, `identity_version=bts-flight-v1`, source filename, compressed and decompressed source SHA-256 values, source row count, ordinal convention, source crosswalk filename/hash, output hash, and status counts. The source cleaned input is `flights_2024_cleaned_verified.csv.gz` (compressed SHA-256 `18a78b549736d2498c24cb625bdeef8be137aa710ab7b80e8a906dd204571445`; decompressed SHA-256 `01a4b75de0615063b24e31edda69d81730059199e76ccea45b37ca9d00379bf1`). The saved crosswalk is `data/processed/canonical_identity_2024_20261015_v1/cleaned_to_canonical_crosswalk.csv.gz` (SHA-256 `3856b381d375bbc1cf04fc1fcbf2df63132ed12ced4e62df0def86b9f195da84`).

**Planning size only:** the three-column CSV is expected to be about 0.9 GB uncompressed and roughly 0.55–0.70 GB gzip-compressed. This estimate is based on 6,982,766 rows and the 78-character versioned hash ID; the actual output size must be measured and hashed when the mapping is generated. The mapping is not included in this Git change.

### Private exchange

No connected private file-sharing destination or access for Omruthik has been verified in this workspace. A local Windows path and an ignored Git artifact are not a handoff. Recommended options are an institution-managed OneDrive/SharePoint/Drive folder restricted to Omruthik, or an approved encrypted file-transfer channel with the decryption secret shared separately. Do not place the mapping or source dataset in a Git commit, public link, or repository release. Choose the channel and confirm recipient access before generating or sending the file.

## 2. Airport/Network score export

Export one row per cohort candidate and horizon. Join only by `flight_id` plus the exact horizon label; never join by row position, route/time, or a regenerated sequential ID.

Required score CSV columns:

| Column | Requirement |
|---|---|
| `flight_id` | Canonical `bts-flight-v1` ID from the reduced mapping; required. |
| `horizon` | Exactly `t_minus_2h` or `t_minus_30m`. |
| `score_status` | `scored` or `unavailable`. A failed or unsupported score is unavailable, not zero. |
| `p_delay15` | Finite probability in `[0,1]` for `DepDel15=1`; required only when scored. |
| `decision_threshold` | The saved threshold used for this model/horizon; required when scored. Do not tune it on the evaluation cohort. |
| `predicted_label` | Thresholded `0`/`1` decision using the exact saved comparison rule; required when scored. Record the comparison operator in the run manifest. |
| `input_support_status` | `in_support`, `out_of_support`, or `unknown`; an out-of-support score remains the model's score but its live reliability is unverified. |
| `unavailable_reason` | Empty when scored; required when unavailable, using a specific reason code. |

Recommended unavailable reasons include `identity_unresolved`, `retrospective_eligibility_failed`, `missing_required_feature`, `missing_route_distance`, `unsupported_carrier`, `unsupported_destination`, `historical_state_unavailable`, `feature_timestamp_after_cutoff`, and `inference_error`. Add a documented reason rather than silently omitting a row or substituting zero. If an ID cannot be mapped, it is not a cohort score row and must remain in the mapping exception status.

Keep target labels in the separate canonical evaluation table, not among model inputs. The target is BTS `DepDel15`: positive class `1` means departure delay of at least 15 minutes. Cancelled flights and invalid/missing binary labels are excluded from the primary cohort.

### Run manifest and feature provenance

Provide `run_manifest.json` with at least:

- Run ID and UTC creation time; model name and horizon(s).
- Exact model-bundle SHA-256; saved threshold(s) and exact comparison operator(s); feature-schema names/order/hash.
- Feature-builder Git commit and source-file SHA-256; inference entry point and status.
- Python, NumPy, pandas, scikit-learn, and joblib versions used for inference.
- Input artifact names, SHA-256 values, row counts, source periods, and filtering/eligibility rule.
- Per-horizon candidate, scored, unavailable-by-reason, extra, and duplicate row counts; score-file and provenance-file hashes.
- Explicit statement that 2024 and 2025 results previously inspected by the project are exploratory; 2025 is not a blind final test.

Provide row-level `feature_provenance.csv.gz` for the actual inputs used, keyed by `flight_id`, `horizon`, and `feature_name`. Include the feature value or a missing marker, source dataset and source-record key, event/vintage timestamp, `available_at_utc`, the horizon cutoff in UTC, and an availability-evidence status. Represent unknown timestamps as unknown; do not infer availability from event time. For scheduled features, record the schedule snapshot/vintage. For historical aggregates, record the source records and when they became available. This provenance is required to assess cutoff validity; absent evidence means retrospective-only.

## 3. Cohort, cutoff, and known limitations

The shared target is `DepDel15` with cancelled flights excluded. The saved retrospective eligibility rules use final BTS outcomes:

- T−2h: `Cancelled == 0` and final `DEP_DELAY > -120`.
- T−30m: `Cancelled == 0` and final `DEP_DELAY > -30`.

These strict rules exclude a flight whose actual departure equals the cutoff. Since they use final outcomes, they define retrospective research cohorts, not operational live eligibility. An operational scorer needs timestamped status showing the flight had not departed by the origin-local scheduled-departure cutoff; no such source has been validated.

| Horizon | Raw eligible source rows, includes unresolved F9 | Valid raw canonical IDs | Unambiguous cleaned-ID membership rows |
|---|---:|---:|---:|
| T−2h | 6,982,746 | 6,982,745 | 6,982,525 |
| T−30m | 6,982,312 | 6,982,311 | 6,982,091 |

These are pre-score candidate counts. The final scored and paired-model cohort is unknown until row-level outputs exist. Missing or invalid scores reduce coverage and are excluded from paired metrics; report each model's coverage and the exact paired-ID denominator by horizon.

Known Airport/Network availability risks remain unresolved:

- `scheduled_origin_departures_prior2h` counts schedules in `[scheduled departure − 2 hours, scheduled departure)` at both model horizons. It is anchored to scheduled departure, not the forecast cutoff; schedule snapshot/vintage availability is unverified.
- Prior-seven-day and recent airport-event features are reconstructed from BTS records/outcomes. Source publication/availability timestamps are missing or unverified; recent-event features use final BTS `DEP_DELAY` retrospectively.
- A successful deserialization, inference, or ID join does not prove inputs were available at the prediction cutoff. Until row-level source availability is evidenced, Airport/Network outputs remain unavailable for live-use claims. The 15-minute weather observation buffer in the Flight/Weather pipeline is also an availability proxy, not receipt-time evidence.

## 4. Export validation and acceptance checks

Before any score alignment or comparison:

1. Require unique `(flight_id, horizon)` keys. Reject duplicate keys; do not select the first row or join by position.
2. Reconcile each horizon export against its exact membership ID set. Report missing and extra IDs separately. Every missing score must be represented by an explicit `unavailable` row and reason; unexpected extra IDs are excluded and investigated.
3. For `scored`, require a finite `p_delay15` in `[0,1]`, a valid saved threshold in `[0,1]`, label consistent with the saved threshold rule, and support status. For `unavailable`, require null score/label and a non-empty reason.
4. Verify all feature names and order against the saved bundle schema. Report missing, unsupported, and out-of-support inputs; never fabricate route distance or historical state.
5. Verify model, builder, input, provenance, and output hashes against the run manifest. Check timestamps against the correct origin-local cutoff converted to UTC.
6. Report candidate count, scored coverage, unavailable reasons, and valid-score coverage for each horizon. For a later three-way evaluation, compute metrics only on the exact same IDs, labels, horizon, and split for Flight/Weather-only, Airport/Network-only, and combined approaches. Do not interpret 2024/2025 results already inspected as blind final results.

**Current gate:** mapping transfer, Airport/Network row-level score export, and source-availability evidence are still outstanding. This document defines the handoff contract only; it does not certify model quality or live readiness.