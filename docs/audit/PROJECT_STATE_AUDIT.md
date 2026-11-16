# Project State Audit — Aviation Disruption Prediction

**Audit date:** 2026-11-10
**Auditor:** Devin (final release review)
**Branch audited:** `audit/final-release-review` (from `origin/main` at `8b452e7`)

## 1. Repository State

| Item | Value |
|------|-------|
| Source repository | `https://github.com/yashgill-10/aviation-disruption-prediction` |
| Public website repository | `https://github.com/yashgill-10/aviation-disruption-research-site` (PUBLIC) |
| Published website | `https://yashgill-10.github.io/aviation-disruption-research-site/` |
| `main` HEAD | `8b452e784be6d5c742be8015d04508b56d7da333` (PR #4 merge) |
| PR #4 | MERGED into `main` |
| PR #1 | OPEN, DRAFT — Omruthik's Airport/Network workstream |
| PR #3 | MERGED (base for PR #4) |

## 2. Branch Relationships

```
main (8b452e7) ── PR #4 merge
  └── codex/canonical-flight-identity-audit (8a03ff7) ── PR #4 head
        └── codex/canonical-flight-audit (e11f676) ── original worktree (uncommitted changes preserved)
feature/flight-weather-ml (07a7a71) ── PR #1 base
  └── codex/airport-network-workstream (c809ae1) ── PR #1 head (Omruthik)
```

**Worktrees:**
- `canonical-flight-audit` (cwd): uncommitted Flight/Weather + docs work — PRESERVED, not modified
- `main-after-pr3`: this audit branch
- `<local checkout>`: on `main`
- `baseline-test`, `pr3-validation`: detached validation worktrees

## 3. Merged vs Unmerged Work

### Merged (PR #4 → main)
- Canonical flight identity (`bts-flight-v1`): `src/canonical_flight_identity.py` + builders/validators
- Cleaned-row→canonical mapping (6,982,766 rows → 6,982,525 unique IDs)
- Shared cohort reconciliation (T−2h: 6,982,525; T−30m: 6,982,091)
- Flight/Weather score exports (H2: 3,569,958 / 3,569,770 rows)
- Score contract, data readiness, shared eligibility docs
- 34 files, +5,175/−35; 88/88 local tests pass (pinned env)

### Unmerged (PR #1 — DRAFT, do not merge)
- Airport/Network workstream: feature builder, inference helpers, export contract
- 53 commits, 32 files, +3,235/−0 on `codex/airport-network-workstream`
- Handoff ZIP `aviation_airport_network_team_handoff.zip`: committed blob SHA-256 `0c9721d1ae2ece6e439382003b07be6acb0f04a8bb4f019131e15287ba102f04` (47 manifest entries, internally verified). The committed `.sha256` sidecar reads `f28b8dae582295fec8921ca8d74493ab38a5a111d92895f640f11afa52087fc9` — a mismatched value updated in commit `c809ae1` without a matching committed ZIP. Outstanding integrity defect, awaiting Omruthik.
- Latest final-submission handoff (local-only, gitignored): `incoming_handoffs/2026-11-05/airport_network_m3_final_submission_handoff.zip`, SHA-256 `4ebdc06f013cedac9422830748227846d2b64b49684e7495c0ef885f64c12b15`, 39 manifest entries — contains the matched-evaluation results.
- Retrospective ordinal-keyed scores already integrated locally in `data/processed/airport_network_score_exports_2024_v1/`

### Local-only artifacts (not in Git, verified on disk)
- `data/processed/m3_score_exports_2024_v1/` — M3 scores + evaluation report
- `data/processed/shared_cohort_2024_20261016_v1/` — authoritative membership
- `data/processed/flight_weather_score_exports_2024_h2_v1/` — FW scores
- `data/processed/phase2_handoff_2024/` — canonical mapping
- `data/processed/depdel15/` — models, weather, history features

## 4. Implemented Components

| Component | Status | Evidence |
|-----------|--------|----------|
| Canonical identity | ✅ Implemented + validated | `validate_phase2_mapping.py` → PASS |
| Shared cohort | ✅ Implemented + hashed | membership CSVs verified |
| Flight/Weather pipeline | ✅ Implemented + exported | H2 score exports verified |
| Airport/Network | ✅ Scores present; builder unrecovered | manifest + hash mismatch documented |
| M3 stack | ✅ Retrospective eval complete | `m3_evaluation_report.json` |
| Research website | ✅ Deployed publicly | GitHub Pages live, HTTP 200 |
| Matched evaluation | ✅ Oct–Dec + Nov–Dec verified | metrics match saved JSON |

## 5. Existing Tests

- Devin suite: **88 tests, 88 passed** (pinned env: Python 3.13.2, sklearn 1.9.1, pandas 3.0.6, numpy 2.5.3)
- Omruthik suite (PR #1): **29 tests passed** per PR description (not independently re-run here)
- Test suites are **separate** — do not sum counts; overlap unknown pending joint run

## 6. Missing Artifacts / Outstanding Issues

1. **Airport/Network original scoring-time builder** — recorded hash `a77c70db...` vs available `c107a5eb...` (scoring runner). Original source unrecovered → provenance limitation stands.
2. **M3 September training-ID list** — row-level IDs not saved; only counts/joins recorded.
3. **Feature-availability timestamps** — schedule vintages, BTS publication/ingestion, airport-state events unavailable.
4. **Blind final test** — none exists; 2025 previously inspected.
5. **Live inference** — Airport/Network and M3 not validated for live use.
6. **PR #1 review** — draft pending joint review. The matched evaluation itself is complete and verified: Oct–Dec component comparison (`matched_evaluation_oct_dec_2024.json`, N=1,768,340/1,768,222) and Nov–Dec M3 evaluation (`m3_evaluation_report.json`, N=1,159,155/1,159,076) both PASS and reconcile with the research report and website. Remaining PR #1 items are the ZIP/sidecar reconciliation and owner confirmation of the "29 tests" claim.

## 7. Key Verified Numbers

| Population | T−2h | T−30m |
|---|---:|---:|
| Cleaned source rows | 6,982,766 | — |
| Unique canonical IDs (mapping) | 6,982,525 | 6,982,091 |
| Ambiguous / incomplete / unmatched | 220 / 1 / 20 | same |
| Flight/Weather scored (H2) | 3,569,958 | 3,569,770 |
| Airport/Network scored (full) | 6,982,525 | 6,982,091 |
| Oct–Dec matched | 1,768,340 | 1,768,222 |
| Nov–Dec matched (M3 eval) | 1,159,155 | 1,159,076 |

**39-row Oct–Dec discrepancy explained:** older ordinal-keyed AN exports had 1,768,379/1,768,261 rows; canonical matched comparison has 1,768,340/1,768,222. The 39 rows are `ambiguous_multiple_raw_candidates` — scored but no defensible canonical ID; excluded from ID-joined comparison. Not missing scores.

## 8. Conclusion

Project is in a **verified retrospective state**: identity, cohorts, exports, metrics, and website are consistent and hash-verified. Outstanding items are documented provenance/availability limitations and the unmerged PR #1 review — none are hidden defects.
