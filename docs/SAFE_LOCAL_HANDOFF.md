# Safe local handoff

The flagged CSVs stay local. A local integrity or static inspection result is not proof that they are safe, and this project does not upload, rename, transform, split, encode, or distribute them.

Yash can run `python -B src/data_readiness.py` and the Flight/Weather validator against artifacts already on this machine. Source code, schemas, tests, manifests, docs, aggregate reports, and synthetic fixtures may be shared through the repository. Raw BTS/cleaned data, the canonical mapping, score CSVs, model binaries, and any flagged files must remain local unless IITGN IT explicitly approves a route.

Omruthik must run the Airport/Network scorer on his own local inputs, first applying `canonical_flight_id` to `flight_id` through `src/score_contract.py`. He must produce one local score file per horizon conforming to `docs/phase2_score_export_contract.md`, plus a model/feature/run manifest and availability evidence. Do not use row order or route/time matching.

A true matched evaluation is possible only if both local executions can access the same canonical IDs and one legitimate, approved route makes compatible row-level scores available for a local join. Aggregate reports can establish each workstream's separate summary metrics, but cannot establish paired differences, score coverage intersection, or a controlled M0--M3 comparison.

To independently validate a generated score file, run the contract tests and check unique `(flight_id, horizon)` keys, explicit unavailable reasons, probability bounds, target parity with membership, saved threshold consistency, and manifest hashes. Keep any unresolved identity rows excluded from scored membership with their reason retained.
