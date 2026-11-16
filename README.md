# Aviation Disruption Prediction — ES 335 Research Project

A retrospective machine-learning study of U.S. flight departure delays, comparing Flight/Weather, Airport/Network, and a logistic-stacking model (M3) at two prediction horizons: two hours and 30 minutes before scheduled departure.

> **Research scope:** This is an academic, retrospective research prototype. The evaluation periods have been inspected during development. The results do not establish live operational availability, production readiness, causal effects, or passenger-service validity.

## Project links

- **Research website:** [Aviation Disruption Prediction — Research](https://yashgill-10.github.io/aviation-disruption-research-site/)
- **Research website source:** [aviation-disruption-research-site](https://github.com/yashgill-10/aviation-disruption-research-site)
- **Source repository:** [aviation-disruption-prediction](https://github.com/yashgill-10/aviation-disruption-prediction)

The research website is a separate static GitHub Pages presentation of stored retrospective results. It does not generate predictions. The source repository is currently private; access is controlled by the repository owner. The source repository also contains a separate local Flight/Weather dashboard prototype.

## Research question and target

Can models rank and classify flights that depart at least 15 minutes late using information intended to be available before scheduled departure?

The target is the BTS **`DepDel15`** indicator (departure delay of at least 15 minutes). The two forecast horizons are:

- **T−2h:** two hours before scheduled departure.
- **T−30m:** 30 minutes before scheduled departure.

### Retrospective eligibility

The comparison cohorts are reconstructed using final BTS outcomes:

- T−2h: `CANCELLED == 0` and `DEP_DELAY > -120` minutes.
- T−30m: `CANCELLED == 0` and `DEP_DELAY > -30` minutes.

Because cancellation and actual departure delay are final outcomes, these rules define retrospective research cohorts. They do **not** establish which flights could have been identified as eligible using information available in real time at either cutoff.

## Workstreams and methodology

### Flight/Weather

- **Training:** `SGDClassifier` on 2022–2023 data.
- **Calibration and threshold:** Platt `LogisticRegression` calibration and F1-based operating threshold selected on January–June 2024. September scores used the saved classifier and calibrator without refitting.
- **Features:** scheduled-flight fields; strictly prior historical delay rates using 7-day, 30-day and 3-calendar-month windows; and GHCNh FM15 METAR observations.
- **Weather timing:** historical availability uses NOAA observation time plus a 15-minute proxy, with a 180-minute staleness limit. The archive does not establish actual receipt or ingestion timestamps.
- **Local inference:** a separate prototype is provided in [`src/flight_weather_web.py`](src/flight_weather_web.py) and [`src/predict_current_flights.py`](src/predict_current_flights.py). The inference default reads `train` and `operational_history`, not validation or test partitions. The prototype requires private, locally prepared model and data artifacts.

The July–December 2024 development ablation reported AP 0.3611 for schedule plus historical delay features and AP 0.3648 when weather was added at T−30m; T−2h weather was effectively neutral. These ablation values are carried from the saved report in [`docs/weather_data_plan.md`](docs/weather_data_plan.md).

### Airport/Network

- **Model:** `RandomForestClassifier` with 50 trees, maximum depth 12, minimum leaf size 10, `class_weight="balanced_subsample"`, and random state 42.
- **Training sample:** stratified sample of up to 200,000 eligible January–August 2024 flights per horizon.
- **Threshold:** September 2024, selected by maximizing F1.
- **Features:** schedule information, prior-7-day airport aggregates, scheduled departures in the prior two hours, and recent origin activity reconstructed from final BTS outcomes.
- **Identity:** canonical `bts-flight-v1:<64-hex>` IDs derived from nine normalized BTS fields. Of 6,982,766 cleaned rows, 6,982,525 received unique canonical identities; 241 unresolved rows (220 ambiguous, 1 identity-incomplete, 20 unmatched) were left explicitly unresolved.
- **Limitations:** the original scoring-time feature builder was not recovered. The available compatibility/reference implementation is not proof of equivalence to that original builder.

### M3 logistic stack

M3 is a logistic-regression meta-model using the logit-transformed Flight/Weather and Airport/Network component probabilities. Probabilities are clipped to `[1e-6, 1−1e-6]`; a `StandardScaler` is fit on September rows only.

- September 2024: meta-training.
- October 2024: threshold selection by maximizing F1.
- November–December 2024: exploratory evaluation.

The exact row-level September meta-training ID list was not saved. Exact reconstruction of that original meta-training cohort is therefore not established; evaluation and inference from the saved artifacts remain distinct from exact retraining.

## Evaluation results

The component comparison and M3 evaluation use **different periods and denominators**. They must not be combined.

### Component comparison — matched October–December 2024 cohort

The matched cohorts contain 1,768,340 flights at T−2h and 1,768,222 at T−30m.

| Metric | T−2h Flight/Weather | T−2h Airport/Network | T−30m Flight/Weather | T−30m Airport/Network |
|---|---:|---:|---:|---:|
| Average precision (AP) | **0.301968** | 0.296262 | 0.301948 | **0.313754** |
| ROC-AUC | **0.674348** | 0.664327 | **0.674965** | 0.673690 |
| Precision | 0.268672 | 0.244777 | **0.271437** | 0.256832 |
| Recall | 0.536774 | **0.588000** | 0.523707 | **0.570994** |
| F1 | **0.358103** | 0.345660 | **0.357554** | 0.354301 |
| Balanced accuracy | **0.623741** | 0.614400 | **0.622683** | 0.621918 |
| Threshold (selection period) | 0.217442 (Jan–Jun) | 0.470405 (Sep) | 0.218222 (Jan–Jun) | 0.463141 (Sep) |

These are matched retrospective comparisons of different pipelines, not a controlled feature ablation. Estimator families, training populations, feature construction and threshold-selection periods differ. Airport/Network October–December metrics were independently recomputed by Omruthik from saved score, target and feature tables and matched the reported values. Flight/Weather matched values are carried from saved hash-pinned reports.

### M3 evaluation — November–December 2024

This is a separate exploratory evaluation, with 1,159,155 matched rows at T−2h and 1,159,076 at T−30m.

| Horizon | Model | AP | ROC-AUC | Precision | Recall | F1 | Balanced accuracy | Brier | Threshold (selection period) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| T−2h | Flight/Weather | 0.327948 | 0.676451 | 0.273891 | 0.617136 | 0.379400 | 0.628216 | 0.139181 | 0.217442 (Jan–Jun) |
| T−2h | Airport/Network | 0.322279 | 0.668014 | 0.262302 | 0.615436 | 0.367832 | 0.616919 | 0.213314 | 0.470405 (Sep) |
| T−2h | **M3 Stack** | **0.344739** | **0.684306** | 0.259718 | **0.689488** | 0.377310 | 0.628104 | **0.137256** | 0.178095 (Oct) |
| T−30m | Flight/Weather | 0.327614 | 0.677258 | 0.275969 | 0.608435 | 0.379711 | 0.628237 | 0.139040 | 0.218222 (Jan–Jun) |
| T−30m | Airport/Network | 0.342772 | 0.679061 | 0.274216 | 0.606095 | 0.377596 | 0.626196 | 0.208690 | 0.463141 (Sep) |
| T−30m | **M3 Stack** | **0.357079** | **0.691513** | 0.264095 | **0.691795** | **0.382260** | **0.633381** | **0.136060** | 0.175470 (Oct) |

On this previously inspected sample, M3 has higher AP, ROC-AUC and recall, and lower (better) Brier score than both component models at both horizons. Its precision is lower; F1 and balanced accuracy are mixed by horizon. These are descriptive point estimates. No blind-test, statistical-significance or clustered-confidence-interval claim is made.

### Interpreting the metrics

AP, ROC-AUC and Brier are threshold-independent ranking/probability metrics. Precision, recall, F1 and balanced accuracy depend on the selected operating threshold. The thresholds were selected in different periods for the three models; the procedures are disclosed above and are not identical.

## Evidence provenance

- **Independently checked artifacts:** canonical identity mapping and canonical export hashes, manifest checks and cohort integrity checks.
- **Independently reproduced by Omruthik:** Airport/Network model loading and one-row-per-horizon inference; October–December Airport/Network metric recomputation from saved score, target and feature tables, matching the saved report.
- **Independently reproduced by Devin:** canonical mapping/export checks and the documented 500-row-per-horizon Airport/Network inference reproduction.
- **Carried from saved, hash-pinned reports:** Flight/Weather matched-comparison metrics, Flight/Weather July–December ablation, M3 metrics and thresholds, except where a specific independent recomputation is stated above.
- **Exploratory/descriptive:** M3 comparisons and deltas, Airport/Network subgroup descriptions, and the aircraft-rotation pilot. The rotation pilot was not independently reproduced from the original raw archives.

Checking an artifact hash or reproducing selected inference rows is not the same as independently rebuilding every original training and scoring pipeline.

## Threats to validity and limitations

1. **No blind final test:** October–December 2024 and November–December 2024 evaluation periods were inspected during development; 2025 was also previously inspected.
2. **Retrospective eligibility:** final BTS cancellation and departure outcomes define the cohorts; they do not establish live eligibility at prediction time.
3. **Feature availability:** schedule-vintage timestamps, BTS publication/ingestion times and recent airport-state availability timestamps are unavailable.
4. **Weather availability:** NOAA observation time plus 15 minutes is a proxy, not verified receipt/ingestion time.
5. **Airport/Network provenance:** the original scoring-time feature builder was not recovered; the compatibility/reference builder is not proof of original equivalence.
6. **Incomplete selection history:** complete model-family, feature-selection and hyperparameter-selection histories were not recovered; the M3 September row-level training-ID list was not saved.
7. **No uncertainty analysis:** no paired/date-clustered confidence intervals or statistical-significance tests are claimed.
8. **Rotation pilot:** not independently reproduced from the original raw archives.
9. **Live inference:** Flight/Weather has a local inference prototype; Airport/Network and M3 live inference are not validated. The research website displays stored evaluation results only.
10. **No operational claims:** these results do not establish real-time feature availability, operational readiness, causal effects or deployment validity.

## Rebuild checks and local preview

The commands below perform local data-readiness checks, selected artifact-independent tests and a static website preview. They do **not** retrain models or regenerate the reported evaluation metrics. Full reproduction of private model evaluations requires the corresponding approved local datasets and model artifacts.

Tested environment: Python 3.13.2 for the root project, with the pinned dependencies in `requirements.txt`. Flight/Weather model artifacts require scikit-learn 1.9.1. The Airport/Network bundle has a separately documented environment for exact artifact reproduction.

From the repository root in PowerShell:

```powershell
python -m pip install -r requirements.txt
python -B src/data_readiness.py
python -B -m unittest discover -s tests -p "test_*score*.py" -v
python -B -m unittest discover -s tests -p test_shared_cohort_reconciliation.py -v
python -m http.server 8000 -d web
```

Open [http://localhost:8000](http://localhost:8000) to preview the static files in `web/`.

For a deliberate full local file-hash check (which can take time for large files):

```powershell
python -B src/data_readiness.py --verify-hashes
```

For full test-suite reproduction in the verified local environment:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
.\.venv\Scripts\python.exe -m unittest discover -s airport-network\tests -v
```

The previously reported merged-main results were 89/89 root tests and 35/35 Airport/Network tests. These results should be understood as the recorded local test evidence, not as GitHub Actions status checks.

### Flight/Weather local inference prototype

The separate local dashboard can be launched with:

```powershell
.\.venv\Scripts\python.exe src\flight_weather_web.py
```

Then open [http://127.0.0.1:8765](http://127.0.0.1:8765). The scoring CLI and documentation are in `src/predict_current_flights.py` and `docs/flight_weather_website.md`.

The prototype requires private model artifacts, a station map and prepared BTS history that are not distributed in this repository. Its default inference history uses only `train` and `operational_history`; validation and test partitions must not be used as inference history. An offline smoke test can run with `--weather-mode none`; live METAR enrichment requires network access. Current METAR observations do not validate historical feed receipt times.

See [`docs/LOCAL_EXECUTION_GUIDE.md`](docs/LOCAL_EXECUTION_GUIDE.md), [`docs/SAFE_LOCAL_HANDOFF.md`](docs/SAFE_LOCAL_HANDOFF.md), [`docs/flight_weather_website.md`](docs/flight_weather_website.md), and [`docs/WEBSITE_RESEARCH_CONTENT_HANDOFF.md`](docs/WEBSITE_RESEARCH_CONTENT_HANDOFF.md) for detailed instructions and safety boundaries.

## Data access and privacy

The study uses public-source data including U.S. BTS TranStats flight records, NOAA/NCEI GHCNh hourly observations, and FAA aviation-weather and airport information. Some prepared datasets, row-level score/feature/target tables, canonical mappings and model binaries are restricted local research artifacts. They are not included in this repository and must not be placed in public releases or personal cloud storage. Access to approved institutional handoffs is not implied by this repository.

## AI-use disclosure

AI-assisted tools were used during project development by the contributors. The disclosed tools include ChatGPT, Codex/Codex CLI, and Devin. They assisted with technical explanations, planning, code inspection and review, debugging suggestions, test-fixture and documentation drafting, repository/task workflows, summarization of saved outputs, and website text revisions.

AI-generated suggestions, code or summaries were not treated as scientific evidence by themselves. Human contributors remained responsible for project decisions, reviewing and accepting changes, executing relevant tests, interpreting results, and the final submitted claims. The evidence-provenance section above distinguishes checks actually reproduced from results carried forward from saved reports. This README is a project-level summary; any course-required AI-use form or declaration should also be completed using the institution's required format.

## Contributions

The work was organized into two primary workstreams:

- **Yash Gill — Flight/Weather:** flight-data preparation, historical delay features, weather integration, Flight/Weather model development and local prediction prototype.
- **Omruthik Nakkalapally — Airport/Network:** airport/network feature engineering, Airport/Network model development, inference/integration helpers and aircraft-rotation pilot.

Both contributors participated in research framing, leakage and evaluation discussions, integration review, interpretation, reproducibility and final presentation. See [`CONTRIBUTIONS.md`](CONTRIBUTIONS.md) for the work distribution and tooling disclosure. The available source snapshots do not support reconstructing every contribution by individual file authorship; Git commit authorship should not be treated as a complete contribution record.

## External references

- [U.S. BTS TranStats — Airline On-Time Performance](https://www.transtats.bts.gov/)
- [NOAA/NCEI — Global Historical Climatology Network Hourly](https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-hourly)
- [FAA Aviation Weather Center API](https://aviationweather.gov/data/api/)
- [FAA System Wide Information Management (SWIM)](https://www.faa.gov/air_traffic/technology/swim)

## Final status

This project is complete as a retrospective academic research prototype with documented evaluation, selected independently reproduced checks, a local Flight/Weather demonstration and an interactive static research website. A genuinely untouched final test, complete recovery of every original scoring/training artifact, and validation of live operational availability remain outside the established evidence.
