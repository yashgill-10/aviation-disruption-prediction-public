# Flight and weather website

## What it does

`src/flight_weather_web.py` serves a local dashboard at `http://127.0.0.1:8765`. It loads the existing T−2h and T−30m models, adds schedule fields, strictly prior BTS history, and decoded METAR observations, and does not train or refit. Website inference reads only `train` and `operational_history`; it does not read validation or test partitions.

The page has a bundled offline example, prediction-time schedule CSV upload, saved model validation metrics, per-flight details, and a manual/browser refresh control. **Load offline demo** scores a clearly labeled synthetic schedule row using both saved models and explicitly marks weather missing. It makes no flight-provider or weather network requests. CSV predictions request current METARs from the [AviationWeather.gov Data API](https://aviationweather.gov/data/api/). If that service fails, the model still runs with weather marked missing and the warning is shown.

Live flight discovery uses `https://api.aviationstack.com/v1/flights`, the base URL and example endpoint shown in the user's signed-in Aviationstack Quickstart Guide. This account-specific guide was selected because it is the documentation provided for the account/key being configured; newer public documentation describes a different APILayer gateway URL. The user later reported a successful smoke response from the selected endpoint. The request uses `access_key`, `dep_iata`, and `flight_status=scheduled`. The connector accepts only records whose status is explicitly scheduled, whose scheduled departure is still in the future, and whose scheduled airport/time fields map through the project airport/timezone table. It never puts actual departure, arrival outcome, delay, or status-after-departure fields into model inputs. The real-time endpoint is not the dedicated future-flight/timetable product; coverage of upcoming schedules is therefore not guaranteed. See the [public Aviationstack API endpoint reference](https://docs.apilayer.com/apilayer/docs/aviationstack) for the documented discrepancy.

The Aviationstack Free plan currently lists 100 monthly requests, real-time flights, and HTTPS. Its plan matrix lists historical flights, airline routes, flight schedules, and Future Flight as paid features. The official FAQ documents `flight_status`, `dep_iata`, and related flight filters. This connector makes at most one origin request per refresh, caches it for eight hours, and stops at 80 app requests in any rolling 31-day window. The one-time smoke check counts toward those 80; the remaining 20 calls are reserved for other apps that share the key or provider-side counting differences. A second watched airport is rejected instead of silently consuming another call. A `429`, plan restriction, or local safety cap is shown without retrying or sending another request. The account quota is shared with any other apps that use this key. See [Aviationstack pricing](https://aviationstack.com/pricing) and the [official FAQ](https://aviationstack.com/faq).

Aviationstack's Free real-time records do not include BTS route distance. The saved model has an explicit missing-distance indicator, so this connector passes `Distance` as missing and surfaces that fact in the dashboard; it does not estimate or impute route mileage. Scheduled elapsed time is calculated from the API's scheduled departure and arrival timestamps, converted into each airport's local time using the project map. Flights with missing required schedule fields, unknown airports/time zones, non-scheduled status, an actual departure timestamp, or a past scheduled departure are excluded. If no eligible flights are returned, the website explains the limitation and the CSV/demo fallback remains available.

## Run locally

From the repository root, create the virtual environment and install dependencies if they are not already set up:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The dashboard loads the existing saved models from `data/processed/depdel15/weather_models/`; it does not train or modify them. It loads `.env` when the server starts. A fresh installation defaults to watching ATL unless `FLIGHT_AIRPORTS` or a saved dashboard watchlist specifies another origin. Stop an older server with Ctrl+C before restarting it so it reads the current `.env` and source code. Start the local server:

```powershell
.\.venv\Scripts\python.exe src\flight_weather_web.py
```

Open `http://127.0.0.1:8765`. Click **Load offline demo** for a synthetic example clearly marked as non-live, or click **Refresh data** to request live schedules when Aviationstack is configured. A provider failure leaves the last successful results on screen with the error and retained source shown. To predict from a real schedule, upload a CSV with these required columns:

`FlightDate, Reporting_Airline, Origin, Dest, CRSDepTime, CRSArrTime, Distance, CRSElapsedTime`

Do not include the target or post-departure information (for example `DepDel15`, actual times, delay minutes, cancellation status, or taxi-out). The server rejects those columns and unknown fields.

Configure the key without putting it in chat or a command line. From the repository root, run this script and type the key into its hidden prompt:

```powershell
powershell -ExecutionPolicy Bypass -File .\src\configure_flight_api_key.ps1
```

The script writes `FLIGHT_PROVIDER=aviationstack` and `FLIGHT_API_KEY` to the git-ignored `.env`. It does not display the key. Restart the local server so its environment loader reads the new file. To make one sanitized live request and check the response:

```powershell
.\.venv\Scripts\python.exe src\check_aviationstack.py
```

The check prints only response counts and eligibility information; it withholds the credential and request URL. The Free account is described as non-commercial, and the website caps automatic requests to stay under its monthly request allowance. Never put the key in browser code or commit `.env`. Public hosting needs a separately deployed secure backend, authentication, and server-side secret management; this local server is not a public production service.

Run the project checks from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For a fresh BTS dataset build or a separate model experiment, follow `docs/bts_data_pipeline.md` and `docs/weather_data_plan.md`. Choose a new output directory for rebuilds or training so the current dataset, models, and reports stay intact.

The refresh interval is browser-driven: keep the local server and dashboard tab open. It is 15 minutes for non-provider/CSV mode, but Aviationstack Free is clamped to an eight-hour cache interval. Manual refreshes during that cache window reuse the response instead of spending another request. Automatic refresh is available for live and uploaded schedules. The offline demo stays synthetic until the user selects **Refresh data**; its dates are examples, not live operational information. Update timestamps are shown in India Standard Time (IST); scheduled departure and arrival times remain local to each airport.

## Saved model validation

The page compares the saved train-prior baseline and calibrated logistic model using metrics stored in each horizon's `metrics.json`. A separate ablation table compares the saved history-only run with both weather horizons. All logistic models are evaluated on July–December 2024, after Platt calibration and F1 threshold selection on January–June 2024. The recorded results show a small T−30m PR-AUC increase, with essentially flat or slightly lower ROC-AUC, Brier, and F1; T−2h is neutral. The model data include BTS monthly On-Time Performance and NOAA/NCEI GHCNh FM15 observations. The weather-model training records say `test_2025_read: false`. However, 2025 was previously opened for a separate frozen schedule/history baseline comparison, so it is not an untouched project-wide final test. The earlier exploration and its limits are documented in `docs/final_test_evaluation.md`; those 2025 results are not presented as weather-model test results.

The dashboard's model flag uses each saved model's threshold. It is a demonstration operating point, not an airline action recommendation. The probability estimates whether departure delay reaches 15 minutes; the classifier does not estimate delay duration.

## Input and output safeguards

- API secrets are read server-side and never returned by `/api/state`; API errors are sanitized so query-string credentials are not copied into dashboard state.
- Provider schedules are staged and become the saved schedule only after inference succeeds.
- Prediction outputs are written into an isolated temporary run folder. Existing models, data, evaluation reports, and smoke-test artifacts are not overwritten.
- The dashboard binds to `127.0.0.1` by default.
- Airport-state and delay-propagation analysis are not connected in this Flight/Weather lane; the dashboard labels that scope instead of showing invented values.

## Weather and history limits

METARs are observations, not forecasts. For a future horizon cutoff, inference uses only reports available at refresh time and marks stale or missing observations. If the weather endpoint is unreachable, prediction proceeds with weather marked missing. Historical BTS outcomes can lag recent flights; the page shows the latest history date used. These contextual fields are predictive associations, not causal explanations.

An inference smoke test confirms model loading and score generation only. It does not establish generalization quality or constitute a live prediction for an upcoming flight.
