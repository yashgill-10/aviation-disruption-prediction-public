# Feature Availability and Leakage Audit

**Audit date:** 2026-11-10
**Scope:** Flight/Weather, Airport/Network, M3 features at T−2h and T−30m cutoffs.

## 1. Problem Formulation (verified)

| Question | Answer |
|----------|--------|
| What is predicted? | `DepDel15 = 1` iff final BTS departure delay ≥ 15 min, among non-cancelled flights |
| Positive class | Departure delayed ≥ 15 minutes |
| Negative class | Departure delayed < 15 minutes (non-cancelled) |
| Prediction unit | One canonical flight (`bts-flight-v1` SHA-256 ID) |
| T−2h cutoff | Information available ≥ 2 hours before scheduled departure |
| T−30m cutoff | Information available ≥ 30 minutes before scheduled departure |
| Intended use | Retrospective research evaluation only — NOT live deployment |

## 2. Eligibility Rules (verified in code)

- **T−2h:** `CANCELLED == 0` AND `DEP_DELAY > -120`
- **T−30m:** `CANCELLED == 0` AND `DEP_DELAY > -30`
- Target consistency: same `DepDel15` definition across all three components; verified by target-parity checks in validators.

### 2a. What the DEP_DELAY conditions actually do (code-verified)

`DEP_DELAY` is the **final** BTS departure-delay outcome. Actual departure ≈ scheduled departure + `DEP_DELAY` minutes. Therefore:

- `DEP_DELAY > -120` ⟺ actual departure is later than scheduled−120 min ⟺ **the flight had not yet departed at the T−2h cutoff instant**.
- `DEP_DELAY > -30` ⟺ actual departure is later than scheduled−30 min ⟺ **the flight had not yet departed at the T−30m cutoff instant**.

In operational terms the rule approximates "still on the ground / not departed at prediction time" — the correct semantics for a pre-departure delay predictor.

### 2b. Is DEP_DELAY a feature or the target? (code-verified)

**Neither.** `src/export_flight_weather_scores_2024.py` states explicitly: cohort membership uses final BTS cancellation and `DEP_DELAY` *only* for retrospective eligibility; "neither is a predictor. DEP_DELAY is included in exports only as eligibility audit provenance" (column `final_DEP_DELAY_used_for_retrospective_eligibility`). `DepDel15` (the ≥15-min label) is the target; raw `DEP_DELAY` does not enter any feature vector. Prior-flight delay features use *other* flights' historical outcomes, not the scored flight's final delay.

### 2c. Classification of the eligibility filter

This is **not feature leakage and not target leakage** — `DEP_DELAY` never reaches the model. It is a **retrospective cohort-selection effect**: membership is conditioned on a realized (post-hoc) quantity, the final departure time. Consequences:

- The estimand is "flights still not departed at the cutoff," which is the intended prediction population — the evaluation is **not invalid** for that conditioned question.
- However, the cohort cannot be reconstructed in real time from `DEP_DELAY` alone. Reproducing eligibility with as-of operational data would require a timestamped departure-status signal at/before the cutoff (e.g., gate-out/takeoff-off event time or airline departure messages), plus cancellation status as known at that time.
- The filter excludes flights that departed early enough to be gone before the cutoff — a small, outcome-adjacent selection that slightly narrows the population relative to an unconditioned schedule.

### 2d. Availability requirement (clarification)

Every feature must be knowable **no later than the relevant prediction cutoff** (scheduled departure − 2h or − 30m). A timestamp describing when an event *occurred* does not by itself prove when the information became *available* to the prediction system; where only event times exist, availability is marked unverified (see §3–§5).

## 3. Feature-by-Feature Availability

### Flight/Weather Features

| Feature group | Source | Availability evidence | Status | Limitation |
|---------------|--------|----------------------|--------|------------|
| Schedule fields (date, route, times) | BTS schedule | Known at booking; pre-cutoff | ✅ Available | Schedule vintage not pinned to prediction-time version |
| Historical delay statistics | Prior BTS departures | Computed from pre-cutoff history | ⚠️ Partially verified | No `available_at` timestamp per row; assumes lag adequate |
| Weather observations | GHCNh station data | Obs time + 15-min proxy | ⚠️ Proxy only | Uses observation-time + 15-min availability **proxy**, not actual receipt timestamps |
| Weather forecasts | — | Not used | n/a | Model uses observed weather, not forecast — limits live applicability |

### Airport/Network Features

| Feature group | Source | Availability evidence | Status | Limitation |
|---------------|--------|----------------------|--------|------------|
| Airport state | Historical BTS aggregates | Pre-cutoff aggregates | ⚠️ Unverified | Original scoring-time builder **not recovered** (hash mismatch) |
| Network congestion | Route/airport history | Pre-cutoff aggregates | ⚠️ Unverified | Same builder provenance gap |
| Aircraft rotation / prior arrival | Tail-number linkage | 99.90% coverage conditional on unique tail match + valid UTC cutoff | ⚠️ Conditional | Coverage denominator is matched subset, not full sample |
| Schedule features | BTS | Pre-cutoff | ✅ Available | Same schedule-vintage caveat |

### M3 Features (stack inputs)

| Input | Source | Availability |
|-------|--------|--------------|
| FW probability | Flight/Weather model output | Same as FW |
| AN probability | Airport/Network model output | Same as AN |
| Logit transform | clipped logits of both probs | Deterministic |
| Standardization | mean/std fit on **September 2024 training rows only** | ✅ No eval leakage |

## 4. Leakage Checks Performed

| Check | Result |
|-------|--------|
| Target leakage (features derived from outcome) | ✅ None found — features are pre-departure aggregates/weather |
| Future-information leakage | ⚠️ **Unverified** — no per-row `available_at` timestamps; weather uses proxy |
| Train/test temporal leakage | ✅ Chronological splits respected (train 2022–23 / calibrate Jan–Jun 24 / eval Oct–Dec 24) |
| Duplicate-row leakage | ✅ Canonical IDs unique in membership; 220 ambiguous excluded from matched pop |
| Preprocessing leakage | ✅ M3 scaler fit on Sep training rows only; FW Platt on Jan–Jun 24 |
| Feature-selection leakage | ⚠️ Model-selection histories not fully recovered — cannot fully audit |
| Threshold leakage | ✅ Thresholds selected on Jan–Jun (FW) / Oct (M3), not on Nov–Dec eval set |
| Calibration leakage | ✅ Platt fitted Jan–Jun 2024; Sep M3 probs are out-of-sample w.r.t. calibrator |
| Cohort-selection conditioning | ⚠️ Eligibility uses final `DEP_DELAY`/`CANCELLED` — retrospective selection effect (§2c), not feature/target leakage |

## 5. Key Limitations (must be disclosed)

1. **No usable `available_at`/publication timestamps** — cannot prove every feature was knowable at cutoff in a real system.
2. **Weather availability is a proxy** (obs time + 15 min), not verified ingestion time.
3. **Airport/Network builder unrecovered** — feature construction at scoring time cannot be exactly audited.
4. **Schedule vintages** — which schedule version was visible at prediction time is not pinned.
5. **Retrospective ≠ deployable** — all results assume features would be available in a live pipeline; this is asserted, not proven.
6. **Outcome-conditioned cohort** — eligibility conditions on final departure/cancellation outcomes (§2c). Valid for the defined retrospective estimand; not reproducible in real time without as-of departure-status and cancellation feeds.

## 6. Verdict

The design is **methodologically defensible for retrospective evaluation** with explicit caveats. It is **not validated for live operational use**. No evidence of target or temporal leakage was found in the verified components. Two distinct caveats must not be conflated: (a) feature-availability verification is incomplete due to missing timestamps; (b) cohort membership is conditioned on realized outcomes — a selection effect, not leakage. Both are documented limitations, not hidden defects.
