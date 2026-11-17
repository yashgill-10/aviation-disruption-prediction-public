# ES 335 Final Submission Checklist

Final release checkpoint: 2026-11-17, established by merged PR #5 (`docs/final-submission-checklist`). Refer to the PR merge commit on `main` for the exact release SHA rather than a pinned value that changes with each subsequent merge.

## Submission artifacts

| Item | Location | Status |
|---|---|---|
| Public source repository | https://github.com/yashgill-10/aviation-disruption-prediction-public | Published, public |
| Research website | https://yashgill-10.github.io/aviation-disruption-research-site/ | Deployed, synchronized with `web/research.html` |
| Website source | https://github.com/yashgill-10/aviation-disruption-research-site | Published |
| Private development archive | `aviation-disruption-prediction` (private) | Preserved; not required by reviewers |
| Release manifest | `RELEASE_MANIFEST_SHA256.txt` | 124 entries verified against canonical blobs; 125 tracked files including the manifest itself |
| AI-use disclosure | `CONTRIBUTIONS.md` + website section | Present |

## Verification evidence (fresh clean-clone run, 2026-11-17)

| Suite | Command | Result |
|---|---|---|
| Root | `python -B -m unittest discover -s tests` | 89 collected: 76 passed, 13 skipped (private-data-dependent), 0 failed |
| Airport/Network | `python -B -m unittest discover -s airport-network/tests` | 35/35 passed |
| M3 contract | `python -B -m unittest discover -s m3 -p "test_*.py"` | 8/8 passed (synthetic only) |
| Smoke checks | `python -B airport-network/airport_network_inference.py`, `airport_network_integration.py` | Pass (per Omruthik's recorded validation; synthetic guards) |
| Secret scan | Gitleaks 8.30.1 `git --log-opts="--all"` | No leaks |

No CI is configured; results above are local unittest runs, not GitHub Actions.

## Scientific status classification

- **Complete and verified:** canonical flight identity (`bts-flight-v1`), cohort reconciliation, score-contract tooling, public-safe test suites, documentation, website, contribution disclosure.
- **Complete with disclosed limitations:** Flight/Weather, Airport/Network, and M3 retrospective evaluations (exploratory, inspected periods only); M3 implementation published (`m3/`); aggregate error analysis published.
- **Partially complete:** reproducibility — all code/docs/tests public; data and model artifacts intentionally private.
- **Blocked by unavailable historical evidence:** blind final evaluation; AN scoring-time builder bytes; M3 September training-row ID list; historical feature-availability timestamps.
- **Future implementation:** AN/M3 live inference, live data feeds, end-to-end dashboard integration, uncertainty estimates.

## Future roadmap (outside this submission)

1. Acquire a genuinely independent evaluation period (new BTS months post-2025) and rerun frozen models — dependency: new data pull; difficulty: medium; evidence: untouched labels + cutoff-valid features.
2. Replace AN scoring-time provenance with a new explicitly reproducible feature pipeline (versioned, hashed) — dependency: item 3; difficulty: high.
3. Establish timestamp-correct operational features (schedule vintages, BTS publication times, airport-state event times) — dependency: new data sources; difficulty: high.
4. Validate AN live inference against timestamped feeds — dependency: 2–3; difficulty: high.
5. Validate M3 live inference — dependency: 4 + FW live path; difficulty: medium.
6. Wire combined prediction path into a replayable/live interface — dependency: 4–5; difficulty: medium.
7. Add uncertainty estimates (confidence intervals, significance tests) — dependency: saved score exports; difficulty: low–medium.

## Submission statement

This release is a **retrospective research prototype**. It is not a production or live-prediction service, and no result herein is a blind final-test or operational-readiness claim.
