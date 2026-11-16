# Project Scope

## Working Title

Aviation Disruption Prediction and Delay Propagation Using Flight, Weather, Airport, and Network Data

## Main Research Question

Given only information available at a particular prediction time, how accurately can machine learning predict whether a scheduled flight will be delayed?

## Secondary Questions

1. How much do historical flight features improve prediction?
2. How much does weather information improve prediction?
3. How much do airport and network conditions improve prediction?
4. Can upstream flight information help predict downstream delay propagation?
5. How does prediction quality change as departure approaches?

## Primary Target

A flight is classified as delayed when departure delay is at least 15 minutes.

The primary prediction is:

P(departure delay >= 15 minutes | information available at prediction time)

## Main Feature Groups

### Flight
- Airline
- Origin
- Destination
- Scheduled departure
- Scheduled arrival
- Day and month
- Flight distance where available

### Historical
- Airline historical delay rate
- Airport historical delay rate
- Route historical delay rate
- Recent operational delay statistics

### Weather
- Temperature
- Visibility
- Wind
- Pressure
- Precipitation
- Weather conditions

### Airport and Network
- Airport traffic/state
- Capacity-related features where available
- Previous-flight information
- Aircraft rotation / turnaround information where reliably reconstructable
- Delay-propagation features

## Initial Models

- Majority-class baseline
- Logistic Regression
- Random Forest
- Optional gradient-boosting model

## Evaluation

- Precision
- Recall
- F1-score
- PR-AUC
- Confusion matrix
- Calibration
- Error analysis

## Temporal Evaluation

The final evaluation will use chronological data.

The 2025 test period was kept out of feature selection, model selection, calibration, and threshold selection for the baseline models, then opened early for an exploratory frozen-baseline score check. It cannot serve as a blind final evaluation for later weather/horizon models and must not be reused for further tuning.

## Leakage Rule

A feature is valid only if the information represented by that feature could have been available at or before the prediction timestamp.

## Deployment Prototype

The trained model will also be used for current-flight inference using currently available public information, without retraining the model on the current flight.

## Current Development Status

- BTS monthly archives for 2022-2025 passed inventory and CRC checks; the leakage-safe `DepDel15` Parquet build contains train 2022-2023, validation 2024, and a complete 2025 test split.
- The schedule-only logistic model reached 2024 average precision 0.3030 and ROC-AUC 0.6459.
- Adding delay rates from the preceding three completed months (system-wide, airline, origin airport, and route) raised 2024 average precision to 0.3270 and ROC-AUC to 0.6593. On the Jul-Dec 2024 holdout, historical features raised average precision from 0.2820 to 0.3139 and reduced the Platt-calibrated Brier score from 0.1532 to 0.1521.
- Expanded history adds leakage-safe 7-day, 30-day, and origin-by-departure-hour rates. The 2024 model reached AP 0.3608 and ROC-AUC 0.6871 overall; on Jul-Dec after chronological calibration, AP was 0.3611, ROC-AUC 0.6903, and Brier score 0.1452.
- The GHCNh pipeline maps 375 airport codes covering 100% of 2022-2024 movements to NOAA stations. A 1.51 GB station-year download is complete except for eight NOAA-unavailable station-years. Both T−2h/T−30m joins are complete for 20,290,541 2022-2024 flights per horizon; endpoint coverage is about 99.5%, with a 15-minute availability buffer, a three-hour freshness limit, and zero future joins in the as-of audit.
- On the Jul-Dec 2024 evaluation, history + T−30m weather reached average precision 0.3648 versus 0.3611 for history alone; T−2h weather was effectively neutral. The evaluation and calibration details are in [weather_data_plan.md](weather_data_plan.md).
- Current-flight inference recomputes historical features from local BTS partitions and joins available current METARs from AviationWeather.gov; weather outages are represented as missing inputs. The local website runs saved T−2h/T−30m models using a bundled synthetic offline schedule or a prediction-time CSV upload, and displays the saved 2024 validation metrics. A real operational prediction still needs a real schedule and sufficiently recent weather/history. The binary model does not estimate delay minutes. Current operational BTS history is kept separate from train, validation, and test.
- Live flight discovery is not enabled while the API-key provider is unidentified. A server-side provider adapter registry is in place; the offline demo and CSV path do not require that provider.
- The historical feature recipe and results are in [historical_feature_experiment.md](historical_feature_experiment.md); data-source, horizon, and availability rules are in [weather_data_plan.md](weather_data_plan.md).
- In an early exploratory 2025 baseline check, schedule plus historical delay reached average precision 0.3434, ROC-AUC 0.6644, and F1 0.4184, compared with 0.3178, 0.6494, and 0.4109 for schedule-only. The check occurred before weather and prediction-horizon models were built; see [final_test_evaluation.md](final_test_evaluation.md).
- The 2025 targets were not used for baseline training, calibration, or threshold selection, but the score results have now been inspected. Earlier 2025 months contribute only through the predeclared lagged historical features for later months. This year cannot be presented as an untouched final evaluation for later models.
