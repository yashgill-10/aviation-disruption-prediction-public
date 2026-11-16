# Canonical flight identity build for 2024

## Scope

This build creates a stable ID table from raw 2024 BTS records and an exact-match crosswalk to every original row ordinal in the supplied cleaned Airport/Network input. It does not recreate the upstream cleaning process byte for byte. It does not train, tune, infer with, or score models, generate features, or establish live readiness.

## Identity

Version: bts-flight-v1. The SHA-256 ID uses compact UTF-8 JSON of normalized fields in this order: FlightDate; DOT_ID_Reporting_Airline; Reporting_Airline; Flight_Number_Reporting_Airline; OriginAirportID; Origin; DestAirportID; Dest; CRSDepTime.

ID construction uses the existing functions in src/audit_canonical_flights_2024.py. Dates are ISO, numeric IDs are canonical integral text, codes are trimmed and uppercased, and scheduled time is valid zero-padded HHMM (2400 remains 2400). Targets, actual outcomes, cancellation, weather, features, row number, and dataframe order are excluded. Codeshares are not merged by route and time.

## Provenance and exceptions

The builder opens the completed reconciliation SQLite database read-only and checks counts and archive/month/hash/row inventory against audit_report.json. It does not reopen the monthly ZIPs or rebuild indexes. The supplied cleaned gzip is recorded with compressed SHA-256 18a78b549736d2498c24cb625bdeef8be137aa710ab7b80e8a906dd204571445 and decompressed SHA-256 01a4b75de0615063b24e31edda69d81730059199e76ccea45b37ca9d00379bf1.

The F9 MIA to ATL 2024-08-25 06:00 row with blank reporting flight number remains quarantined. The 110 ambiguous cleaned groups get no ID, and no raw candidate is selected. The 20 unmatched groups also get no ID. A unique exact match receives an ID only if the source identity is complete.

## Outputs

Files are written under ignored data/processed/canonical_identity_2024_20261015_v1:

- canonical_flights.csv.gz: complete-identity raw BTS rows with archive hash, CSV member, and source row ordinal. This includes rows outside the cleaned input; it is not the Airport/Network training table.
- cleaned_to_canonical_crosswalk.csv.gz: all original cleaned data-row ordinals, including duplicate copies, with explicit match statuses.
- identity_quarantine.csv: raw records without a defensible identity.
- ambiguous_cleaned_matches.csv and unmatched_cleaned_rows.csv: separate mapping exceptions.
- identity_conflicts.csv: duplicate, conflict, or digest collision evidence; header only if none are detected.
- canonical_identity_run_report.json and its SHA-256 sidecar: provenance, counts, tests, validation, and output hashes.

## Reproduce

From the separate audit worktree, use system Python 3.13, not the project virtual environment:

    python -m unittest discover -s tests -p test_canonical_flight_identity.py -v

    python src\build_canonical_flight_table_2024.py --reconciliation-dir data\processed\airport_network_reconciliation_2024_20261012_retry1 --out-dir data\processed\canonical_identity_2024_20261015_v1

The builder refuses existing output and staging directories. Keep the reconciliation database until output hashes, gzip integrity, row counts, and tests are verified.

## Later evaluation protocol (not performed)

Predeclare chronological splits and common eligible flight IDs per horizon. Fit preprocessing on training only, use calibration rows for calibration and threshold selection, and reserve evaluation rows from all selection. Previously inspected 2024 and 2025 results remain exploratory. BTS retrospective fields are not demonstrated available at T-2h or T-30m without source-availability timestamps.

## Recover an existing completed staging run

If the builder completed both gzip tables but stopped during report finalization, preserve the staging directory and run the output-only validator below. It validates the generated tables, creates the compact report and missing empty conflict header when there are no duplicate IDs, and publishes the staging directory. It does not rescan the reconciliation database, rebuild indexes, or reopen BTS archives.

    python src\finalize_canonical_flight_table_2024.py --stage-dir data\processed\canonical_identity_2024_20261015_v1.staging --out-dir data\processed\canonical_identity_2024_20261015_v1 --reconciliation-dir data\processed\airport_network_reconciliation_2024_20261012_retry1
