# Artifact Provenance Audit — Aviation Disruption Prediction

**Audit date:** 2026-11-10
**Scope:** All score exports, manifests, models, and handoff artifacts used in the matched evaluation.

## 1. Canonical Identity Artifacts

| Artifact | Path | SHA-256 / ID | Status |
|----------|------|--------------|--------|
| Cleaned source (compressed) | local file `flights_2024_cleaned_verified.csv.gz` (outside repo) | `18a78b549736d2498c24cb625bdeef8be137aa710ab7b80e8a906dd204571445` | Verified |
| Cleaned source (decompressed) | same | `01a4b75de0615063b24e31edda69d81730059199e76ccea45b37ca9d00379bf1` | Verified |
| Canonical mapping | `data/processed/phase2_handoff_2024/cleaned_row_to_canonical_mapping.csv.gz` | `a0b9d6657d3be7148bbf8dc6bed2ff47a6d4301c9bd776c071cee627a0292168` | Verified |
| Mapping manifest | `data/processed/phase2_handoff_2024/mapping_manifest.json` | `81f8038f0b82590fbb654e623c74f434d3b9ac7addd0f40a4edb4eca644bc1e7` | Verified |
| Mapping validator | `src/validate_phase2_mapping.py` | Result: PASS | Verified |

**Mapping results:** 6,982,766 rows → 6,982,525 unique recovered; 220 ambiguous; 1 identity incomplete; 20 no exact match; 241 unresolved total. Namespace `bts-flight-v1` = SHA-256 over 9 normalized BTS schedule fields.

## 2. Shared Cohort Membership

| Artifact | Path | SHA-256 | Rows | Status |
|----------|------|---------|-----:|--------|
| T−2h membership | `data/processed/shared_cohort_2024_20261016_v1/t_minus_2h_membership.csv.gz` | `ddc2d679bb6c93fdc1ce6758ccee570b8110dbf726d05febc7e75d714d2ae1be` | 6,982,525 | Verified, 0 dup/null IDs |
| T−30m membership | same dir | `988639c03df4213b448fa509f0999d35c74c372be5a907e460d0ac149748add3` | 6,982,091 | Verified, 0 dup/null IDs |

## 3. Model Score Exports

| Export | Path | SHA-256 | Rows | Status |
|--------|------|---------|-----:|--------|
| Flight/Weather T−2h | `flight_weather_score_exports_2024_h2_v1/` | `4a44b8405b41380f46c310f129e819651a496c6e86cbda81d700494f7fcc4d93` | 3,569,958 | Verified |
| Flight/Weather T−30m | same | `5f9950a3a3c98e8032755c3a043886838b6dd08d08033fbfe64a376bed047753` | 3,569,770 | Verified |
| Airport/Network T−2h | `airport_network_score_exports_2024_v1/airport_network_t_minus_2h_scores.csv.gz` | `88746f367717663488b942cd213d2eb2f9ad6a65fea102bc166a6798db9e5618` | 6,982,525 | Verified |
| Airport/Network T−30m | same | `da52c8e80b33fe29f9cc3c669c55e47624e021b8b73d53efe678e9cd506ff0ab` | 6,982,091 | Verified |
| M3 T−2h | `m3_score_exports_2024_v1/m3_t_minus_2h_scores.csv.gz` | `1ba0110b9890aa53b2bdf740fe6f2339fa5fa89be708692a3aa7dde6549c02a8` | 1,768,340 scored rows (Oct–Dec); Nov–Dec eval subset = 1,159,155 | Verified |
| M3 T−30m | same | `e3b0905706c4370b62cad00891a6a8bab8e37176568a913f09ac49ad70ef2a45` | 1,768,222 scored rows (Oct–Dec); Nov–Dec eval subset = 1,159,076 | Verified |

All score files: row-level local artifacts, not tracked in Git; target parity verified against membership `DepDel15`; probabilities in [0,1]; thresholds applied consistently.

## 4. Model Artifacts

| Model | Artifact | SHA-256 | Training | Calibration | Status |
|-------|----------|---------|----------|-------------|--------|
| Flight/Weather | saved `SGDClassifier` bundle | (per export manifest) | 2022–2023 | Platt, fitted Jan–Jun 2024 | Verified load; sklearn 1.9.1 |
| Airport/Network | model bundle | `9dca5d3bb2e8e2af0cf9bca3a2245584b83b10e2cfb0e883fb90388fd0b87b5a` | (per manifest) | — | Verified hash |
| M3 T−2h | logistic stack | `4594d58fd0a8c483a9bd5c72a811e72d64b42d01073398c26692e53b1bb2c77a` | Sep 2024 | logit-clip + standardize on Sep | Verified |
| M3 T−30m | logistic stack | `29cda9838316fa482afa288f44f8056598ae0ec55c43c8c2c6ad06e46f967a59` | Sep 2024 | same | Verified |

**Thresholds (selected Jan–Jun 2024 for FW; Oct 2024 for M3; not tuned on eval):**
- Airport/Network: T−2h `0.4704047724395039`, T−30m `0.46314085073651107`
- M3: T−2h `0.17809467649913194`, T−30m `0.175470...` (full value in `m3_evaluation_report.json`)

## 5. Handoff Packages — Three Distinct Versions Identified

| Package | Location | SHA-256 | Entries | Manifest lines | Status |
|---------|----------|---------|--------:|---------------:|--------|
| Teammate handoff (older) | local Downloads copy of `aviation_airport_network_team_handoff.zip` (outside repo) | `8a7549d49b0919557723b5b23e9850d713c9aeaab5b5011b6d6f326fef827a99` | 17 | **no root `MANIFEST.sha256`** — contents not manifest-verifiable | Contains model bundle + CSVs; predates canonical integration |
| PR #1 committed ZIP | `aviation_airport_network_team_handoff.zip` on `codex/airport-network-workstream` | blob hashes to `0c9721d1ae2ece6e439382003b07be6acb0f04a8bb4f019131e15287ba102f04` | 48 (47 payload + MANIFEST) | **47** | All 47 internal hashes verified; `testzip` clean |
| Final submission handoff (latest) | `incoming_handoffs/2026-11-05/airport_network_m3_final_submission_handoff.zip` (gitignored, local-only) | `4ebdc06f013cedac9422830748227846d2b64b49684e7495c0ef885f64c12b15` | 40 | **39** | Of the 39 manifest entries: **31 small files re-hashed and verified in this audit (2026-11-10)**; **8 large files were hash-verified in the prior session** and skipped here due to size — not re-verified today |

### 5a. The "39 vs 49" discrepancy — RESOLVED

The three numbers refer to **different packages**, not conflicting counts of one package:

- **39** = manifest entries in the **latest** `airport_network_m3_final_submission_handoff.zip` (2026-11-05), which contains the matched-evaluation results.
- **47** = manifest entries in PR #1's committed `aviation_airport_network_team_handoff.zip` (verified directly from the ZIP).
- **49** = PR #1 description claim — does **not** match the committed ZIP's 47 payload files. PR description is stale/approximate.

### 5b. PR #1 ZIP/sidecar mismatch — REAL DEFECT

- Commit `c809ae1` ("Add handoff package verifier") changed `aviation_airport_network_team_handoff.zip.sha256` from `0c9721d1…` to `f28b8dae…` **without committing a matching ZIP**.
- The committed ZIP blob still hashes to `0c9721d1…`. The `f28b8dae…` ZIP is **not present** in the repository or local Downloads.
- Consequence: `verify_handoff_package.py` run against the committed ZIP + current sidecar would report a checksum mismatch. Omruthik must either commit the `f28b8dae` ZIP or revert the sidecar to `0c9721d1`.

## 6. Builder Hash Discrepancy (Airport/Network) — CORRECTED

Per `canonical_export_run_manifest.json` and the research report:

| Hash | What it actually identifies |
|------|------------------------------|
| `a77c70db0e033e85561186c7c947bd8957b024edfd7630e83e356ed175fe5b36` | Feature builder recorded in the **saved preliminary score run manifest** |
| `c107a5eb379da9830b7cb288082b1807422f5cfa43af7f9ba6283d3a1698a3cf` | `outputs/build_airport_network_preliminary_scores.py` — the **scoring runner**, NOT the standalone feature builder |

**Determination:** The original scoring-time feature builder source is **not present** in the workspace, PR #1, or the handoff packages. The earlier framing of "local builder hash" was imprecise — `c107a5eb` is the runner script. **Exact historical reproduction of the AN feature builder is not possible.** Retained as a provenance limitation; all AN metrics are retrospective and must not be described as exactly reproducible.

## 7. Reproduction Commands

```bash
# Environment
python -m venv .venv-verify && .venv-verify/Scripts/activate
pip install -r requirements.txt   # Python 3.13.2, sklearn 1.9.1, pandas 3.0.6, numpy 2.5.3

# Validators
python src/validate_phase2_mapping.py
python src/validate_flight_weather_score_exports_2024.py

# Tests
python -m unittest discover -s tests -p "test_*.py" -v   # 88 passed
```

## 8. Summary

**Verified:** canonical mapping, shared-cohort membership, all Flight/Weather, Airport/Network, and M3 score-export hashes; M3 and matched Oct–Dec evaluation report hashes; internal consistency of the two manifest-bearing handoff packages (PR #1 committed ZIP: 47/47 entries verified; latest final-submission ZIP: 31 files re-verified this audit + 8 large files verified in the prior session). The older teammate ZIP has no root manifest and is therefore not manifest-verifiable.

**Outstanding integrity defect:** PR #1's committed ZIP hashes to `0c9721d1…` but its sidecar declares `f28b8dae…` (changed in `c809ae1` without a matching ZIP). This package's end-to-end integrity is **not** verified until Omruthik supplies the matching archive or corrects the sidecar.

**Resolved:** the 39-vs-49 count difference reflects two different handoff packages (latest final-submission ZIP has 39 manifest entries; PR #1's committed ZIP has 47). New finding: PR #1's ZIP sidecar (`f28b8dae`) does not match the committed ZIP (`0c9721d1`) — flagged for Omruthik. Remaining limitation: Airport/Network original feature builder unrecovered — exact historical reproduction impossible.
