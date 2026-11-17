# Project completion status

Updated: 2026-11-17 (final release). This is an evidence checkpoint, not a claim that every planned phase is complete.

## Public release status

- Public source repository: https://github.com/yashgill-10/aviation-disruption-prediction-public — a sanitized single-commit snapshot of private `main` @ `40c188ede876f36beea0bc34c2e3378e95c3a0b6` (includes merged PR #11 AI-use disclosure). The original private repository `aviation-disruption-prediction` retains full development history and restricted local artifacts and is not required by reviewers.
- Research website: https://yashgill-10.github.io/aviation-disruption-research-site/ (source: https://github.com/yashgill-10/aviation-disruption-research-site), kept in agreement with `web/research.html`.
- Clean-checkout tests: root suite 76 passed / 13 skipped (private-data-dependent `requires_private_data` tests), Airport/Network suite 35 passed. No secrets detected by Gitleaks 8.30.1 on the public history.
- M3 reproducibility: the actual retrospective `train_evaluate_m3.py` implementation was recovered from the private final-submission handoff archive and is published under `m3/` with only external input paths parameterized via environment variables; its synthetic contract tests pass (8 tests). Trained models and row-level score exports remain private; input score exports are required to rerun and are not distributed.
- `airport-network/error_analysis/` aggregate-only tables and reproduction script are now included; `airport-network/README.md` and dated status notes reconcile earlier pending/outstanding statements with the completed canonical exports, matched comparison, and M3 evaluation.
- This document records evidence status; it does not upgrade exploratory results into validated operational claims.
- Final checkpoint 2026-11-17: Omruthik's verified docs patch merged via PR #4 (patch SHA-256 `38DC7CC9…5B42`, 15 files, docs+manifest only). See `docs/FINAL_SUBMISSION_CHECKLIST.md` for the submission checklist, classification table, and future roadmap.

## Completed and verified locally

- Canonical identity is `bts-flight-v1:<sha256>` over nine normalized BTS schedule fields. The saved 2024 report records 7,079,060 unique canonical flights, zero duplicate-ID groups and zero hash-collision groups.
- The Phase 2 mapping keeps all 6,982,766 cleaned-input ordinals. It resolves 6,982,525 uniquely; 220 ambiguous, 1 identity-incomplete and 20 unmatched rows remain explicit and unassigned.
- Separate retrospective memberships exist: T-2h has 6,982,525 unambiguous IDs and T-30m has 6,982,091. They use final cancellation/departure outcomes and are not proof of operational availability.
- Flight/Weather H2 (Jul–Dec 2024) exports exist locally with exact membership alignment: 3,569,958 T−2h and 3,569,770 T−30m scored IDs. The original exports remain local and unchanged.
- The exporter code now sources reporting-flight metadata from authoritative membership fields, correcting the blank `Flight_Number_Reporting_Airline` defect for any **new** local export. Existing flagged files were not rewritten.
- An explicit `canonical_flight_id`/`flight_id` adapter and score-contract validation helpers are provided in `src/score_contract.py`.
- Airport/Network canonical-key score exports exist locally and passed the recorded validation checks: 6,982,525 T−2h and 6,982,091 T−30m scored IDs with 100% coverage of the retrospective eligible cohorts (`data/processed/airport_network_score_exports_2024_v1/canonical_export_run_manifest.json`). The ordinal-keyed exports (~6.98M scored rows per horizon, 110 explicit unavailable rows each) are preserved unchanged. Prediction-time feature availability remains unverified; these are retrospective scores only. Model bundle SHA-256: `9dca5d3bb2e8e2af0cf9bca3a2245584b83b10e2cfb0e883fb90388fd0b87b5a`.
- M3 logistic stack evaluated on September–December 2024. Meta-training on September, threshold selection on October, evaluation on November–December. Both horizons evaluated with zero target mismatches, zero duplicate IDs, zero missing probabilities, and exact ID joins.
- Final exploratory joint research report created at `reports/final_joint_exploratory_research_report.md`.
- Interactive research website updated at `web/research.html` with Flight/Weather, Airport/Network, and M3 results, including separate tables for October–December component comparisons and November–December M3 evaluation.
- **Live inference status:** Flight/Weather has a local inference prototype (`src/flight_weather_web.py`, `src/predict_current_flights.py`). Airport/Network and M3 live inference are not validated; the research website displays stored evaluation results only.

## Confirmed Flight/Weather development results

On the already-inspected Jul–Dec 2024 retrospective evaluation, the recorded history-only AP is 0.3611 and the T-30m weather AP is 0.3648. T-2h weather was effectively neutral. These are development evidence, not an untouched final test.

## Confirmed Airport/Network and M3 results

### October–December 2024 Component Comparison (exploratory)

| Horizon | Model | N | AP | ROC-AUC | Precision | Recall | F1 | Balanced accuracy |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| T−2h | Flight/Weather | 1,768,340 | 0.301968 | 0.674348 | 0.268672 | 0.536774 | 0.358103 | 0.623741 |
| T−2h | Airport/Network | 1,768,340 | 0.296262 | 0.664327 | 0.244777 | 0.588000 | 0.345660 | 0.614400 |
| T−30m | Flight/Weather | 1,768,222 | 0.301948 | 0.674965 | 0.271437 | 0.523707 | 0.357554 | 0.622683 |
| T−30m | Airport/Network | 1,768,222 | 0.313754 | 0.673690 | 0.256832 | 0.570994 | 0.354301 | 0.621918 |

### November–December 2024 M3 Evaluation (exploratory)

| Horizon | Model | N | AP | ROC-AUC | Precision | Recall | F1 | Balanced accuracy | Brier |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| T−2h | Flight/Weather | 1,159,155 | 0.327948 | 0.676451 | 0.273891 | 0.617136 | 0.379400 | 0.628216 | 0.139181 |
| T−2h | Airport/Network | 1,159,155 | 0.322279 | 0.668014 | 0.262302 | 0.615436 | 0.367832 | 0.616919 | 0.213314 |
| T−2h | M3 | 1,159,155 | 0.344739 | 0.684306 | 0.259718 | 0.689488 | 0.377310 | 0.628104 | 0.137256 |
| T−30m | Flight/Weather | 1,159,076 | 0.327614 | 0.677258 | 0.275969 | 0.608435 | 0.379711 | 0.628237 | 0.139040 |
| T−30m | Airport/Network | 1,159,076 | 0.342772 | 0.679061 | 0.274216 | 0.606095 | 0.377596 | 0.626196 | 0.208690 |
| T−30m | M3 | 1,159,076 | 0.357079 | 0.691513 | 0.264095 | 0.691795 | 0.382260 | 0.633381 | 0.136060 |

**Key finding:** M3 achieved higher AP, ROC-AUC, and recall, and lower Brier scores than both component models at both horizons. Precision was lower. F1 and balanced accuracy were mixed. These are descriptive point estimates; no confidence intervals or significance tests were saved.

## Blockers and intentionally incomplete work

- **No blind final test:** October–December 2024 and 2025 have been inspected; neither is a blind final test. A genuinely independent evaluation period remains unresolved.
- **Provenance gaps:** Airport/Network original scoring-time feature builder not recovered. M3 row-level September training-ID list not saved. Complete model-family, hyperparameter, and feature-selection histories for all three models not recovered.
- **Feature-availability limitations:** Schedule-vintage timestamps, BTS publication/ingestion times, and recent airport-state availability timestamps unavailable. Flight/Weather uses 15-minute weather observation-time proxy, not actual receipt timestamps. Airport/Network recent activity reconstructed from final BTS outcomes.
- **Threshold selection differences:** Component thresholds selected in different periods (Flight/Weather Jan–Jun, Airport/Network Sep); M3 threshold selected October. Procedures not identical.
- **Matched comparison only:** Different pipelines compared on matched IDs; differences cannot be attributed exclusively to feature additions.
- **Live inference limitations:** Flight/Weather has a local inference prototype. Airport/Network and M3 live inference are not validated. The research website displays stored evaluation results only and does not generate live predictions.

## Current commands

```powershell
python -B src/data_readiness.py
python -B -m unittest discover -s tests -p "test_*score*.py" -v
python -B -m unittest discover -s tests -p test_canonical_flight_identity.py -v
python -m http.server 8000 -d web
```

Open `http://localhost:8000/research.html` for the static project site. See `SAFE_LOCAL_HANDOFF.md` and `LOCAL_EXECUTION_GUIDE.md` for the legitimate local handoff protocol.

## Checks in this worktree

`git diff --check` passed on 2026-11-05. Focused Python tests and syntax compilation were attempted in a sandbox without a usable Python interpreter; earlier test outcomes in the retained readiness reports are historical evidence, not a fresh result for the changes made in this checkpoint. For the current verified test results, see the 2026-11-17 clean-clone run recorded in `docs/FINAL_SUBMISSION_CHECKLIST.md`; the November 5 worktree limitation above is historical and superseded.
