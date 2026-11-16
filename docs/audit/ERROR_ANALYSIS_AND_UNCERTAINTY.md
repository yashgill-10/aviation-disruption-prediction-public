# Error Analysis and Statistical Uncertainty

**Audit date:** 2026-11-10
**Scope:** Matched Oct–Dec 2024 (Flight/Weather + Airport/Network only — M3 used October for threshold selection, so it has no Oct–Dec component row) and Nov–Dec 2024 (all three components) on identical canonical cohorts.

## 1. Evaluation Design (verified)

- **Paired evaluation:** All components scored on the **same canonical flight IDs** per horizon.
- **Cohorts:** Oct–Dec T−2h N=1,768,340; T−30m N=1,768,222. Nov–Dec T−2h N=1,159,155; T−30m N=1,159,076.
- **Target:** `DepDel15` (dep delay ≥ 15 min, non-cancelled) — identical across components (target parity verified).
- **Thresholds:** Fixed per model, selected on earlier periods — not tuned on eval sets.
- **Period status:** Oct–Dec and Nov–Dec are **exploratory/previously inspected**, NOT a blind final test.

## 2. Verified Metrics

### October–December 2024 — component comparison (N_T−2h=1,768,340; N_T−30m=1,768,222)

Source: `matched_evaluation_oct_dec_2024.json` (SHA-256 `34dc990c…`). No Brier scores saved for this period; no M3 row (October was M3 threshold selection).

| Horizon | Model | AP | ROC-AUC | Prec | Recall | F1 | Bal.Acc | Threshold |
|---------|-------|-----|---------|------|--------|-----|---------|-----------|
| T−2h | Flight/Weather | 0.301968 | 0.674348 | 0.268672 | 0.536774 | 0.358103 | 0.623741 | 0.217442 (Jan–Jun) |
| T−2h | Airport/Network | 0.296262 | 0.664327 | 0.244777 | 0.588000 | 0.345660 | 0.614400 | 0.470405 (Sep) |
| T−30m | Flight/Weather | 0.301948 | 0.674965 | 0.271437 | 0.523707 | 0.357554 | 0.622683 | 0.218222 (Jan–Jun) |
| T−30m | Airport/Network | 0.313754 | 0.673690 | 0.256832 | 0.570994 | 0.354301 | 0.621918 | 0.463141 (Sep) |

### November–December 2024 — M3 evaluation (primary window)

### November–December 2024, T−2h (N=1,159,155)

| Model | AP | ROC-AUC | Prec | Recall | F1 | Bal.Acc | Brier |
|-------|-----|---------|------|--------|-----|---------|-------|
| Flight/Weather | 0.327948 | 0.676451 | 0.273891 | 0.617136 | 0.379400 | 0.628216 | 0.139181 |
| Airport/Network | 0.322279 | 0.668014 | 0.262302 | 0.615436 | 0.367832 | 0.616919 | 0.213314 |
| M3 Stack | 0.344739 | 0.684306 | 0.259718 | 0.689488 | 0.377310 | 0.628104 | 0.137256 |

### November–December 2024, T−30m (N=1,159,076)

| Model | AP | ROC-AUC | Prec | Recall | F1 | Bal.Acc | Brier |
|-------|-----|---------|------|--------|-----|---------|-------|
| Flight/Weather | 0.327614 | 0.677258 | 0.275969 | 0.608435 | 0.379711 | 0.628237 | 0.139040 |
| Airport/Network | 0.342772 | 0.679061 | 0.274216 | 0.606095 | 0.377596 | 0.626196 | 0.208690 |
| M3 Stack | 0.357079 | 0.691513 | 0.264095 | 0.691795 | 0.382260 | 0.633381 | 0.136060 |

**Reconciliation:** Nov–Dec values recomputed from score exports and match `m3_evaluation_report.json` (SHA-256 `346f0de5…`) and the published website exactly; Oct–Dec values match `matched_evaluation_oct_dec_2024.json` (SHA-256 `34dc990c…`) and the research report. F1 correction `0.379400` confirmed. The research report, saved evaluation JSONs, and website carry identical metrics and cohort definitions.

## 3. Interpretation

- **M3** shows the highest observed AP and ROC-AUC point estimates at both horizons. Whether this advantage is statistically significant is **not established** — paired clustered confidence intervals were not computed, and flight-level observations are correlated within aircraft/airport/day groups. The point-estimate ordering is consistent with the stack adding discriminative signal, but that interpretation is a hypothesis, not a tested conclusion.
- **M3 recall** is highest (0.689 T−2h / 0.692 T−30m) but **precision is lowest** → it flags more positives, catching more delays at the cost of more false alarms. Appropriate for a recall-oriented disruption-warning use case, but operational cost of false positives must be weighed.
- **Airport/Network** has the worst (highest) Brier scores (0.213 T−2h / 0.209 T−30m) → weakest overall probabilistic accuracy among the three. Brier conflates calibration and sharpness, so this alone does not prove miscalibration; it is consistent with uncalibrated Random-Forest `predict_proba` outputs versus Flight/Weather's Platt-calibrated probabilities. Its AP is competitive at T−30m (0.3428 vs FW 0.3276).
- **Flight/Weather** has the lowest (best) Brier among single components (0.139) → strongest probabilistic accuracy; consistent with its Platt calibration, though Brier alone does not isolate calibration quality.
- Differences in AP (~0.02–0.03) are modest point-estimate differences; statistical significance is untested and the **absolute** discrimination is moderate (ROC-AUC ~0.68–0.69).

## 4. Statistical Uncertainty

- **Resampling unit:** canonical flight (one row per flight ID; no duplicates).
- **Pairing:** Components evaluated on identical rows → paired comparison is valid.
- **Existing evidence:** Omruthik's Oct–Dec analysis reports 342 Wilson subgroup intervals (airport-network package). M3 evaluation used fixed thresholds; bootstrap CIs for the matched comparison were **not** computed as a dedicated deliverable in this audit.
- **Caveat:** Flights are not fully independent (same aircraft, airport, day correlations). Row-level bootstrap would understate uncertainty; a clustered/grouped bootstrap by airport-day or tail would be more appropriate. **Flagged as a limitation** — point estimates are verified; interval estimates are approximate.
- **Multiple comparisons:** Many subgroups/metrics reported; treat individual subgroup differences cautiously.

## 5. Error Analysis

**Descriptive (owner-produced, not independently reproduced here):** Omruthik's packaged `Airport_Network_Oct_Dec_Error_Analysis.md` (ordinal-keyed) reports calibration summaries, subgroup (airport/route/month) metrics with 342 Wilson intervals, and 110 explicit unavailable ordinal rows per horizon (`duplicate_source_row_removed_before_training_features`). These are documented findings; this audit verified the underlying score files and counts but did not re-derive the subgroup analysis.

**Independently verified in this audit:**
- **39 ambiguous rows** scored by AN but excluded from canonical matched comparison (no defensible ID) — reconciles the 1,768,379 vs 1,768,340 gap. Verified against the mapping status and the ordinal list in the research report.
- **Model-disagreement hypothesis (unverified):** it is plausible that cases where AN contributes signal beyond FW explain M3's AP/recall point-estimate gains, but no per-row disagreement analysis was performed. Classified as hypothesis, not finding.

## 6. Verified vs Hypothesis

**Verified:** all metrics, counts, hashes, target parity, threshold provenance, paired cohorts.
**Hypotheses (not proven):** that M3's gain is statistically significant or generalizes to live data; that feature availability holds in deployment; that the airport/network builder (unrecovered) used only pre-cutoff info; that model disagreements drive M3's advantage.

## 7. Baselines

- Majority-class / historical baselines were not separately re-run in this audit; existing experiments referenced where available. No artificial baselines were constructed to inflate results.
