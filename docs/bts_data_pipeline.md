# BTS archive inventory and preprocessing

This first data build uses BTS monthly on-time-performance ZIPs to create a compact dataset for the `DepDel15` departure-delay target. Run the commands below from the repository root in Windows PowerShell.

## Downloading missing months

Download only the months that are still missing. The downloader takes an OS-managed lock on its output directory and exits with an error if another copy is already running. On reruns, it checks the existing ZIP directory and expected CSV member without decompressing the archive or repeating a CRC scan. Use the inventory command below after downloads finish for full CRC verification.

```powershell
.\.venv\Scripts\python.exe src\download_bts_months.py --years 2025 --months 10 11 12
```

## Safe workflow

1. Leave any running downloaders alone. The inventory and preprocessing scripts ignore `.zip.part` files and never delete, rename, or extract raw archives. Full CRC verification and CSV processing read completed ZIPs and can add disk load, so run those steps after the downloaders have finished.

2. Check archive names and missing months without reading the CSV data or checking member CRCs:

   ```powershell
   .\.venv\Scripts\python.exe src\inventory_bts_archives.py --data-dir data\raw --years 2022 2023 2024 2025 --list-only
   ```

3. After downloads finish, verify each available archive by reading every ZIP member and checking its CRC. Use `--require-complete` when you want a nonzero exit code if any requested archive is missing, partial, structurally invalid, or fails CRC verification:

   ```powershell
   .\.venv\Scripts\python.exe src\inventory_bts_archives.py --data-dir data\raw --years 2022 2023 2024 2025 --require-complete
   ```

   Without `--require-complete`, the command reports gaps and still exits successfully. It never changes source files.

4. Build the processed dataset into a new or empty output directory:

   ```powershell
   .\.venv\Scripts\python.exe src\prepare_bts_dataset.py --input-dir data\raw --output-dir data\processed\depdel15
   ```

   The command refuses to write inside `data\raw` or into a nonempty output directory. Pick another output path if `data\processed\depdel15` already contains files. It processes each completed monthly ZIP in chunks and does not extract raw CSVs.

5. Read `data\processed\depdel15\monthly_audit.csv` and `metadata.json` before modelling. The audit reports source row counts, cancellation and label exclusions, eligible target counts, observed date coverage, missing dates, CRC status, and each month's output status.

## Dataset definition

- Target: BTS `DepDel15`, retained as binary 0/1.
- Eligible rows: `Cancelled == 0`, a valid 0/1 `DepDel15`, and a valid `FlightDate` inside the year/month represented by the source archive. Every cancelled flight is excluded, even when BTS supplies a target value.
- Chronological dataset partitions: 2022-2023 train, 2024 validation, and a complete 2025 historical partition stored under `test/`. Because the frozen schedule/history baseline was previously evaluated on 2025, this partition is not a blind final test for the integrated project.
- Test protection: no 2025 Parquet files are retained unless all 12 monthly archives are structurally valid, every month has at least one valid in-month `FlightDate`, and all 12 CSV streams finish with valid CRCs. If a 2025 month is missing or fails processing, the entire 2025 test year is withheld.
- Features written: `FlightDate`, `Reporting_Airline`, `Origin`, `Dest`, scheduled `CRSDepTime` and `CRSArrTime`, `Distance`, and `CRSElapsedTime`. `Cancelled` is used only for row eligibility.
- Post-outcome fields are not read into the dataset, including actual departure/arrival times, delay minutes, delay causes, `Diverted`, and `CancellationCode`. Weather and network features are not included yet.

## Target spot check

A fixed-seed random sample of 50 eligible, non-cancelled flights from the July 2024 BTS archive was checked by recomputing `DepDel15` as `DepDelayMinutes >= 15`. All 50 recomputed labels matched the published `DepDel15` value. The selected rows and comparison flag are saved at `data/processed/depdel15/target_validation/2024-07_sample_50.csv`. This validates a small sample of the label mapping; it does not replace the full monthly audit or imply that post-departure delay minutes are valid predictors.

## Output layout

```text
data/processed/depdel15/
  metadata.json
  monthly_audit.csv
  flights/
    train/       # one Parquet file per successfully processed 2022-2023 month
    validation/  # one Parquet file per successfully processed 2024 month
    test/        # created only after the complete 2025 test year passes
```

Months without eligible rows have an audit entry but no empty Parquet file. `metadata.json` records the exact archive coverage and split rules. The resulting files are prepared data, not model results; do not use the 2025 split for feature selection, threshold tuning, or model selection.

## Current data limits

Do not infer complete-year coverage from a few files or from a running download. Use the inventory output and the monthly audit to state exactly which years and months are present. A missing month stays missing until its archive is available and passes verification.

## Verified archive snapshot (October 10, 2026)

I rechecked all ZIP members in `data/raw` with CRC verification. The 48 requested archives for 2022–2025 are all present, each has its BTS CSV member, and all members pass CRC. The 2026 January–July archives also pass; August and September are not present. The processed operational-history partition contains exactly January–July 2026 and is not part of model fitting or validation.

| Year / split | Months processed | Source rows | Cancelled excluded | Missing `DepDel15` | Eligible rows | CRC |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 2022 train | 12/12 | 6,729,125 | 181,286 | 177,348 | 6,547,839 | all pass |
| 2023 train | 12/12 | 6,847,899 | 87,943 | 84,529 | 6,759,956 | all pass |
| 2024 validation | 12/12 | 7,079,061 | 96,315 | 92,970 | 6,982,746 | all pass |
| 2025 historical test partition (previously inspected) | 12/12 | 7,001,619 | 102,876 | 98,591 | 6,898,743 | all pass |
| 2026 operational history | 7/12 available | — | — | — | Parquet files for Jan–Jul | all available archives pass |

The 2022–2025 audits report no unknown cancellation values, invalid or non-binary targets, invalid dates, or out-of-month dates. Eligible rows are retained only after excluding cancellations and missing labels. The 2025 partition is complete as a dataset split, but it is **not a blind final evaluation**: an earlier frozen schedule/history baseline comparison already scored and inspected it. See `docs/final_test_evaluation.md`.

The separate Flight/Weather runtime handoff reviewed for this submission contains operational-history Parquet files for January–July 2026 only. An August 2026 archive/Parquet update was not present in that handoff, so the reported August row count and date coverage are not verified here. Keep operational-history data separate from the training, validation, and historical test partitions.
