from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import requests
import pandas as pd
from train_baseline import numeric_features

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import flight_weather_web as web  # noqa: E402
import predict_current_flights as inference  # noqa: E402

STATION_MAP_PATH = web.ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
PRIVATE_DATA_PRESENT = STATION_MAP_PATH.is_file() and (
    web.ROOT / "data" / "processed" / "depdel15" / "weather_models"
).is_dir()
requires_private_data = unittest.skipUnless(
    PRIVATE_DATA_PRESENT,
    "requires restricted private data artifacts (station map and saved weather models) not included in the public repository",
)


VALID_CSV = (
    "FlightDate,Reporting_Airline,Origin,Dest,CRSDepTime,CRSArrTime,Distance,CRSElapsedTime\n"
    "2026-10-12,UA,ATL,BOS,900,1135,946,155\n"
)


class ScheduleInputTests(unittest.TestCase):
    def test_bundled_schedule_passes_prediction_time_validation(self) -> None:
        content = (ROOT / "data" / "demo" / "flight_schedule_example.csv").read_text(encoding="utf-8")
        rows = web.parse_csv_schedule(content)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Origin"], "ATL")
        self.assertNotIn("DepDel15", rows[0])

    def test_target_and_post_departure_columns_are_rejected(self) -> None:
        csv_text = VALID_CSV.replace("CRSElapsedTime\n", "CRSElapsedTime,DepDel15\n").replace("946,155\n", "946,155,0\n")
        with self.assertRaisesRegex(ValueError, "target or post-departure"):
            web.parse_csv_schedule(csv_text)

    def test_unknown_columns_are_rejected(self) -> None:
        csv_text = VALID_CSV.replace("CRSElapsedTime\n", "CRSElapsedTime,Weather\n").replace("946,155\n", "946,155,clear\n")
        with self.assertRaisesRegex(ValueError, "Unexpected columns"):
            web.parse_csv_schedule(csv_text)


class LocalConfigurationTests(unittest.TestCase):
    def test_dotenv_loads_provider_and_key_without_exposing_secret(self) -> None:
        secret = "mock-only-api-secret"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".env").write_text(
                "FLIGHT_PROVIDER=aviationstack\nFLIGHT_API_KEY=" + secret + "\n",
                encoding="utf-8",
            )
            with patch.object(web, "ROOT", root), patch.dict(os.environ, {}, clear=True):
                web.load_dotenv()
                self.assertEqual(web.provider_name(), "aviationstack")
                self.assertTrue(web.api_key_configured())
                self.assertTrue(web.provider_ready())
                self.assertNotIn(secret, json.dumps(web.api_state()))

    def test_first_run_dashboard_watchlist_defaults_to_atl(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(web, "SETTINGS_PATH", Path(temp) / "settings.json"), patch.dict(
                os.environ, {"FLIGHT_AIRPORTS": ""}, clear=False
            ):
                self.assertEqual(web.read_settings(), {"airports": ["ATL"]})


class ProviderBoundaryTests(unittest.TestCase):
    def test_unconfirmed_provider_is_not_called_and_secret_is_not_returned(self) -> None:
        secret = "provider-secret-test-value"
        with patch.dict(os.environ, {"FLIGHT_PROVIDER": "unknown-provider", "FLIGHT_API_KEY": secret}, clear=False):
            with patch.object(web, "read_state", return_value=web.default_state()):
                state = web.api_state()
            self.assertTrue(state["provider_key_configured"])
            self.assertFalse(state["provider_ready"])
            self.assertTrue(state["provider_adapter_pending"])
            self.assertNotIn(secret, json.dumps(state))
            with patch("requests.get") as get:
                with self.assertRaisesRegex(ValueError, "not confirmed or enabled"):
                    web.fetch_provider_schedule(["ATL"])
                get.assert_not_called()

    def _scheduled_record(self, *, status: str = "scheduled", dep_delta_hours: int = 3) -> dict:
        now = web.utc_now()
        departure = now + timedelta(hours=dep_delta_hours)
        arrival = departure + timedelta(hours=2, minutes=30)
        return {
            "flight_status": status,
            "airline": {"iata": "UA"},
            "flight": {"iata": "UA123", "number": "123"},
            "departure": {"iata": "ATL", "scheduled": departure.isoformat(), "actual": None, "delay": None},
            "arrival": {"iata": "BOS", "scheduled": arrival.isoformat(), "actual": None, "delay": None},
        }

    @requires_private_data
    def test_aviationstack_maps_scheduled_fields_only_and_keeps_distance_missing(self) -> None:
        map_path = web.ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
        with map_path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(web.csv.DictReader(handle))
        airport_by_iata = {row["airport_code"]: row for row in rows}
        mapped = web.normalize_aviationstack_flight(self._scheduled_record(), airport_by_iata, web.utc_now())
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped["Reporting_Airline"], "UA")
        self.assertEqual(mapped["Flight_Number_Reporting_Airline"], "UA123")
        self.assertEqual(mapped["Origin"], "ATL")
        self.assertEqual(mapped["Dest"], "BOS")
        self.assertEqual(mapped["Distance"], "")
        self.assertGreater(float(mapped["CRSElapsedTime"]), 0)
        self.assertFalse(any("actual" in key.lower() or "delay" in key.lower() for key in mapped))

        features = numeric_features(pd.DataFrame([{**mapped, "DepDel15": 0}]))
        self.assertEqual(float(features[0, 13]), 1.0)  # trained distance_missing flag

    @requires_private_data
    def test_aviationstack_excludes_departed_or_non_scheduled_flights(self) -> None:
        map_path = web.ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
        with map_path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(web.csv.DictReader(handle))
        airport_by_iata = {row["airport_code"]: row for row in rows}
        now = web.utc_now()
        self.assertIsNone(web.normalize_aviationstack_flight(self._scheduled_record(status="active"), airport_by_iata, now))
        self.assertIsNone(web.normalize_aviationstack_flight(self._scheduled_record(dep_delta_hours=-1), airport_by_iata, now))
        departed = self._scheduled_record()
        departed["departure"]["actual"] = (now - timedelta(minutes=5)).isoformat()
        self.assertIsNone(web.normalize_aviationstack_flight(departed, airport_by_iata, now))

    @requires_private_data
    def test_aviationstack_skips_incomplete_records_and_reports_missing_data_list(self) -> None:
        map_path = web.ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
        with map_path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(web.csv.DictReader(handle))
        airport_by_iata = {row["airport_code"]: row for row in rows}
        incomplete = self._scheduled_record()
        incomplete["departure"].pop("scheduled")
        self.assertIsNone(web.normalize_aviationstack_flight(incomplete, airport_by_iata, web.utc_now()))

        response = type("Response", (), {"status_code": 200, "json": lambda self: {"pagination": {}}})()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", Path(temp) / "usage.json"), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch("requests.get", return_value=response):
                with self.assertRaisesRegex(RuntimeError, "omitted its flight data list"):
                    web.fetch_aviationstack_schedules("test-secret", ["ATL"])

    @requires_private_data
    def test_aviationstack_uses_one_https_realtime_query_and_never_exposes_key_on_error(self) -> None:
        record = self._scheduled_record()
        response = type("Response", (), {"status_code": 200, "json": lambda self: {"data": [record]}})()
        with tempfile.TemporaryDirectory() as temp:
            usage_path = Path(temp) / "usage.json"
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", usage_path), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch("requests.get", return_value=response) as get:
                rows = web.fetch_aviationstack_schedules("test-secret", ["ATL"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(get.call_count, 1)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://api.aviationstack.com/v1/flights")
        self.assertEqual(kwargs["params"]["dep_iata"], "ATL")
        self.assertEqual(kwargs["params"]["flight_status"], "scheduled")
        self.assertEqual(kwargs["params"]["access_key"], "test-secret")

        denied = type(
            "Response",
            (),
            {"status_code": 401, "json": lambda self: {"error": {"code": "invalid_access_key", "message": "test-secret"}}},
        )()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", Path(temp) / "usage.json"), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch("requests.get", return_value=denied):
                with self.assertRaises(RuntimeError) as caught:
                    web.fetch_aviationstack_schedules("test-secret", ["ATL"])
        self.assertNotIn("test-secret", str(caught.exception))
        self.assertIsInstance(caught.exception, web.AviationstackAPIError)
        self.assertEqual(caught.exception.status_code, 401)
        self.assertEqual(caught.exception.error_code, "invalid_access_key")
        self.assertEqual(caught.exception.category, "invalid_key")
        self.assertEqual(caught.exception.provider_message, "[REDACTED]")

    @requires_private_data
    def test_aviationstack_classifies_inactive_account_and_plan_restriction(self) -> None:
        cases = (
            (401, {"error": {"code": 102, "type": "inactive_user", "info": "Account inactive"}}, "inactive_account"),
            (403, {"error": {"code": 105, "type": "function_access_restricted", "info": "Feature restricted"}}, "plan_or_endpoint_restriction"),
        )
        for status, payload, expected_category in cases:
            with self.subTest(category=expected_category), tempfile.TemporaryDirectory() as temp:
                response = type("Response", (), {"status_code": status, "json": lambda self, body=payload: body})()
                with patch.object(web, "AVIATIONSTACK_USAGE_PATH", Path(temp) / "usage.json"), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch("requests.get", return_value=response):
                    with self.assertRaises(web.AviationstackAPIError) as caught:
                        web.fetch_aviationstack_schedules("test-secret", ["ATL"])
                self.assertEqual(caught.exception.category, expected_category)
                self.assertEqual(caught.exception.status_code, status)

    def test_aviationstack_sanitizes_urls_and_credentials_in_provider_message(self) -> None:
        payload = {
            "error": {
                "code": "invalid_access_key",
                "type": "authentication_error",
                "info": "Rejected test-secret at https://api.aviationstack.com/path?access_key=test-secret",
            }
        }
        error = web._aviationstack_api_error(401, payload, "test-secret")
        self.assertNotIn("test-secret", str(error))
        self.assertNotIn("api.aviationstack.com", error.provider_message)
        self.assertNotIn("access_key=", error.provider_message)

    @requires_private_data
    def test_aviationstack_reports_sanitized_metadata_for_non_json_401(self) -> None:
        class NonJsonResponse:
            status_code = 401
            headers = {"Content-Type": "text/html; charset=utf-8", "Server": "edge-proxy"}
            text = (
                "<html><title>Unauthorized</title><p>access_key=test-secret</p>"
                "<p>https://api.aviationstack.com/v1/flights?access_key=test-secret</p></html>"
            )

            def json(self):
                raise ValueError("not JSON")

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", Path(temp) / "usage.json"), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch("requests.get", return_value=NonJsonResponse()):
                with self.assertRaises(web.AviationstackAPIError) as caught:
                    web.fetch_aviationstack_schedules("test-secret", ["ATL"])

        error = caught.exception
        self.assertEqual(error.status_code, 401)
        self.assertEqual(error.content_type, "text/html; charset=utf-8")
        self.assertEqual(error.server_header, "edge-proxy")
        self.assertNotIn("test-secret", error.body_summary)
        self.assertNotIn("api.aviationstack.com", error.body_summary)

    @requires_private_data
    def test_aviationstack_handles_rate_limit_and_network_failure_without_echoing_request_url(self) -> None:
        rate_limited = type("Response", (), {"status_code": 429, "json": lambda self: {"error": {"code": "usage_limit_reached"}}})()
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", Path(temp) / "usage.json"), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch("requests.get", return_value=rate_limited):
                with self.assertRaisesRegex(RuntimeError, "quota or rate limit"):
                    web.fetch_aviationstack_schedules("never-print-this", ["ATL"])

        with tempfile.TemporaryDirectory() as temp:
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", Path(temp) / "usage.json"), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch(
                "requests.get", side_effect=requests.ConnectionError("access_key=never-print-this")
            ):
                with self.assertRaises(RuntimeError) as caught:
                    web.fetch_aviationstack_schedules("never-print-this", ["ATL"])
        self.assertNotIn("never-print-this", str(caught.exception))

    def test_aviationstack_free_rejects_multi_origin_request(self) -> None:
        with self.assertRaisesRegex(ValueError, "one origin airport"):
            web.fetch_aviationstack_schedules("test-secret", ["ATL", "BOS"])

    @requires_private_data
    def test_aviationstack_local_usage_cap_stops_before_network_request(self) -> None:
        now = web.utc_now()
        with tempfile.TemporaryDirectory() as temp:
            usage_path = Path(temp) / "usage.json"
            timestamps = [(now - timedelta(hours=index + 1)).isoformat() for index in range(web.AVIATIONSTACK_LOCAL_REQUEST_CAP)]
            web.safe_json_write(usage_path, {"request_times_utc": timestamps})
            with patch.object(web, "AVIATIONSTACK_USAGE_PATH", usage_path), patch("requests.get") as get:
                with self.assertRaisesRegex(RuntimeError, "local safety cap"):
                    web.fetch_aviationstack_schedules("test-secret", ["ATL"])
            get.assert_not_called()

    @requires_private_data
    def test_refresh_failures_preserve_previous_results_and_source(self) -> None:
        empty = type("Response", (), {"status_code": 200, "json": lambda self: {"data": []}})()
        denied = type(
            "Response",
            (),
            {"status_code": 401, "json": lambda self: {"error": {"code": "invalid_access_key", "message": "Rejected"}}},
        )()
        cases = (
            ("empty response", empty),
            ("authentication failure", denied),
            ("network failure", requests.ConnectionError("access_key=mock-only-api-secret")),
        )
        for label, outcome in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temp:
                data_dir = Path(temp)
                state_path = data_dir / "state.json"
                previous = {
                    "status": "ready",
                    "message": "Scored a synthetic demo example.",
                    "schedule_source": "Bundled synthetic schedule demo",
                    "schedule_count": 1,
                    "flights": [{"Flight_Number_Reporting_Airline": "DEMO-ONLY"}],
                    "updated_at_utc": web.utc_text(),
                }
                web.safe_json_write(state_path, previous)
                with patch.multiple(
                    web,
                    WEB_DATA=data_dir,
                    STATE_PATH=state_path,
                    SETTINGS_PATH=data_dir / "settings.json",
                    AVIATIONSTACK_USAGE_PATH=data_dir / "usage.json",
                ), patch.dict(
                    os.environ,
                    {"FLIGHT_PROVIDER": "aviationstack", "FLIGHT_API_KEY": "mock-only-api-secret", "FLIGHT_AIRPORTS": ""},
                    clear=False,
                ), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch.object(
                    web, "AVIATIONSTACK_LAST_REQUEST_AT_UTC", None
                ), patch.object(web, "AVIATIONSTACK_LAST_REQUEST_ORIGIN", None), patch(
                    "requests.get", side_effect=outcome if isinstance(outcome, BaseException) else None,
                    return_value=None if isinstance(outcome, BaseException) else outcome,
                ) as get:
                    result = web.refresh_predictions(force=True)

                self.assertEqual(get.call_count, 1)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["schedule_source"], "Bundled synthetic schedule demo")
                self.assertEqual(result["schedule_count"], 1)
                self.assertEqual(result["flights"], previous["flights"])
                self.assertIn("last successful results from Bundled synthetic schedule demo", result["message"])
                self.assertNotIn("mock-only-api-secret", json.dumps(result))
                self.assertTrue(result["live_refresh_attempted_at_utc"])
                if label == "network failure":
                    self.assertFalse(result["live_response_received"])
                    self.assertIsNone(result["live_http_status"])
                else:
                    self.assertTrue(result["live_response_received"])
                    self.assertEqual(result["live_http_status"], 200 if label == "empty response" else 401)

    def test_public_provider_diagnostics_are_safe_and_identify_displayed_source(self) -> None:
        secret = "must-not-appear-in-dashboard-diagnostics"
        with patch.dict(
            os.environ,
            {"FLIGHT_PROVIDER": "aviationstack", "FLIGHT_API_KEY": secret},
            clear=False,
        ), patch.object(
            web, "read_state", return_value={
                "schedule_source": "Bundled synthetic schedule demo",
                "live_refresh_attempted_at_utc": "2026-10-10T12:00:00+00:00",
                "live_response_received": True,
                "live_http_status": 200,
            }
        ):
            result = web.api_state()
        diagnostics = result["provider_diagnostics"]
        self.assertTrue(diagnostics["provider_configured"])
        self.assertTrue(diagnostics["api_key_present"])
        self.assertTrue(diagnostics["live_refresh_attempted"])
        self.assertTrue(diagnostics["live_response_received"])
        self.assertEqual(diagnostics["live_http_status"], 200)
        self.assertEqual(diagnostics["displayed_data_source"], "Bundled synthetic schedule demo")
        self.assertNotIn(secret, json.dumps(result))

    @requires_private_data
    def test_saved_models_score_aviationstack_schedule_with_distance_explicitly_missing(self) -> None:
        map_path = web.ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
        with map_path.open("r", newline="", encoding="utf-8-sig") as handle:
            rows = list(web.csv.DictReader(handle))
        airport_by_iata = {row["airport_code"]: row for row in rows}
        schedule = web.normalize_aviationstack_flight(self._scheduled_record(), airport_by_iata, web.utc_now())
        self.assertIsNotNone(schedule)

        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            input_path = Path(temp) / "aviationstack_schedule.csv"
            output_path = Path(temp) / "predictions.csv"
            web.write_schedule([schedule], input_path)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "src" / "predict_current_flights.py"),
                    "--input-csv", str(input_path),
                    "--history-dir", str(ROOT / "data" / "processed" / "depdel15" / "flights"),
                    "--history-splits", "train", "operational_history",
                    "--weather-mode", "none",
                    "--station-map", str(map_path),
                    "--model-t-minus-2h", str(ROOT / "data" / "processed" / "depdel15" / "weather_models" / "t_minus_2h" / "weather_logistic_sgd.joblib"),
                    "--model-t-minus-30m", str(ROOT / "data" / "processed" / "depdel15" / "weather_models" / "t_minus_30m" / "weather_logistic_sgd.joblib"),
                    "--output-csv", str(output_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            with output_path.open("r", newline="", encoding="utf-8-sig") as handle:
                prediction = next(web.csv.DictReader(handle))
            self.assertEqual(prediction["Distance"], "")
            self.assertTrue(0 <= float(prediction["predicted_delay_probability_t_minus_2h"]) <= 1)


class WeatherFallbackTests(unittest.TestCase):
    def test_offline_mode_skips_weather_network_request(self) -> None:
        with patch.object(inference, "fetch_metars") as fetch:
            reports, _, status, warning = inference.fetch_weather_for_inference(["KATL"], 3, 6, "none")
        self.assertEqual(reports, {})
        self.assertEqual(status, "omitted")
        self.assertIsNone(warning)
        fetch.assert_not_called()

    def test_weather_service_failure_uses_explicit_missing_weather(self) -> None:
        with patch.object(inference, "fetch_metars", side_effect=requests.ConnectionError("offline")):
            reports, _, status, warning = inference.fetch_weather_for_inference(["KATL"], 3, 6, "live")
        self.assertEqual(reports, {})
        self.assertEqual(status, "unavailable")
        self.assertIn("marked missing", warning or "")


class SavedEvaluationTests(unittest.TestCase):
    @requires_private_data
    def test_evaluation_summary_uses_saved_2024_validation_metrics(self) -> None:
        summary = web.evaluation_summary()
        self.assertEqual(set(summary["horizons"]), {"t_minus_2h", "t_minus_30m"})
        self.assertEqual(summary["horizons"]["t_minus_30m"]["period"], "2024-07 through 2024-12")
        self.assertFalse(summary["horizons"]["t_minus_30m"]["test_2025_read_by_this_model"])
        self.assertGreater(summary["horizons"]["t_minus_30m"]["pr_auc"], 0.3)
        self.assertGreater(summary["horizons"]["t_minus_30m"]["prior_baseline"]["pr_auc"], 0.19)
        self.assertGreater(summary["history_only"]["pr_auc"], 0.36)
        self.assertFalse(summary["history_only"]["test_2025_read_by_this_model"])
        self.assertEqual(set(summary["history_only"]["confusion_matrix"]), {"tn", "fp", "fn", "tp"})
        self.assertEqual(set(summary["horizons"]["t_minus_30m"]["confusion_matrix"]), {"tn", "fp", "fn", "tp"})

    def test_web_inference_reads_only_training_and_operational_history(self) -> None:
        command = web.prediction_command(ROOT / "input.csv", ROOT / "output.csv", "none")
        history_index = command.index("--history-splits")
        self.assertEqual(command[history_index + 1:history_index + 3], ["train", "operational_history"])
        self.assertEqual(command[command.index("--weather-mode") + 1], "none")

    def test_cli_default_history_splits_exclude_evaluation_partitions(self) -> None:
        argv = sys.argv
        try:
            sys.argv = [
                "predict_current_flights.py",
                "--input-csv", "input.csv",
                "--model-t-minus-2h", "model2h.joblib",
                "--model-t-minus-30m", "model30m.joblib",
                "--output-csv", "output.csv",
            ]
            args = inference.parse_args()
        finally:
            sys.argv = argv
        self.assertEqual(tuple(args.history_splits), ("train", "operational_history"))


class DashboardDataQualityTests(unittest.TestCase):
    @staticmethod
    def category_support(airlines=("UA", "DL"), destinations=("BOS", "ATL")):
        support = {"available": True, "airlines": frozenset(airlines), "destinations": frozenset(destinations)}
        return {"t_minus_2h": support, "t_minus_30m": support}

    def test_elapsed_horizon_uses_origin_timezone_and_is_not_an_active_flag(self) -> None:
        departure = inference.scheduled_departure_utc("2026-10-12", 900, "America/New_York")
        self.assertEqual(departure, datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc))
        row = {
            "FlightDate": "2026-10-12", "Reporting_Airline": "UA", "Origin": "ATL", "Dest": "BOS",
            "CRSDepTime": "900", "Distance": "946",
            "prediction_cutoff_utc_t_minus_2h": (departure - timedelta(hours=2)).isoformat(),
            "prediction_cutoff_utc_t_minus_30m": (departure - timedelta(minutes=30)).isoformat(),
            "predicted_delay_t_minus_2h": 1, "predicted_delay_t_minus_30m": 1,
        }
        now = datetime(2026, 10, 12, 11, 15, tzinfo=timezone.utc)
        with patch.object(web, "model_category_support", return_value=self.category_support()):
            quality = web.dashboard_data_quality({}, [row], now)
        self.assertEqual(row["prediction_horizon_status_t_minus_2h"], "elapsed")
        self.assertEqual(row["prediction_horizon_status_t_minus_30m"], "actionable")
        self.assertEqual(quality["horizon_status"]["t_minus_2h"]["active_threshold_flags"], 0)
        self.assertEqual(quality["horizon_status"]["t_minus_30m"]["active_threshold_flags"], 1)
        self.assertEqual(row["predicted_delay_t_minus_2h"], 1)  # Retained saved decision for traceability.

    def test_model_support_warnings_count_unknown_categories_and_missing_distance(self) -> None:
        rows = [
            {"Reporting_Airline": "UA", "Dest": "BOS", "Distance": 500},
            {"Reporting_Airline": "XX", "Dest": "XYZ", "Distance": ""},
        ]
        with patch.object(web, "model_category_support", return_value=self.category_support()):
            quality = web.dashboard_data_quality({"schedule_source": "uploaded schedule CSV"}, rows)
        for horizon in ("t_minus_2h", "t_minus_30m"):
            support = quality["model_input_support"][horizon]
            self.assertEqual(support["unsupported_carrier_value_count"], 1)
            self.assertEqual(support["unsupported_carrier_rows"], 1)
            self.assertEqual(support["unsupported_destination_value_count"], 1)
            self.assertEqual(support["unsupported_destination_rows"], 1)
        self.assertEqual(quality["missing_route_distance_rows"], 1)
        self.assertFalse(rows[0]["model_input_support_unverified"])
        self.assertTrue(rows[1]["model_input_support_unverified"])

    def test_data_quality_counts_are_computed_from_rows_and_provider_metadata(self) -> None:
        rows = [
            {
                "FlightDate": "2026-10-12", "Reporting_Airline": "UA", "Origin": "ATL", "Dest": "BOS",
                "CRSDepTime": "0900", "Distance": "946", "history_source_max_date": "2026-10-02",
                "history_age_days": 10, "prev7d_history_missing": 1, "prev30d_history_missing": 1,
                "prev3m_history_missing": 0, "origin_weather_missing_t_minus_2h": 0,
                "destination_weather_missing_t_minus_2h": 1, "origin_weather_missing_t_minus_30m": 0,
                "destination_weather_missing_t_minus_30m": 0,
            },
            {
                "FlightDate": "2026-10-12", "Reporting_Airline": "DL", "Origin": "ATL", "Dest": "BOS",
                "CRSDepTime": "900", "Distance": "", "history_source_max_date": "2026-10-01",
                "history_age_days": 11, "prev7d_history_missing": 1, "prev30d_history_missing": 0,
                "prev3m_history_missing": 0, "origin_weather_missing_t_minus_2h": 0,
                "destination_weather_missing_t_minus_2h": 0, "origin_weather_missing_t_minus_30m": 0,
                "destination_weather_missing_t_minus_30m": 0,
            },
        ]
        state = {
            "schedule_source": web.AVIATIONSTACK_SCHEDULE_SOURCE,
            "provider_records_returned": 100,
            "provider_eligible_records": 2,
        }
        with patch.object(web, "model_category_support", return_value=self.category_support()):
            quality = web.dashboard_data_quality(state, rows, datetime(2026, 10, 10, tzinfo=timezone.utc))
        self.assertEqual(quality["raw_provider_records"], 100)
        self.assertEqual(quality["eligible_provider_records"], 2)
        self.assertEqual(quality["eligible_scored_records"], 2)
        self.assertEqual(quality["potential_duplicate_route_time_groups"], 1)
        self.assertEqual(quality["records_in_repeated_route_time_groups"], 2)
        self.assertEqual(quality["additional_records_in_repeated_route_time_groups"], 1)
        self.assertIn("does not establish unique physical flights", quality["duplicate_grouping_note"])
        self.assertEqual(quality["missing_route_distance_rows"], 1)
        self.assertEqual(quality["history"]["latest_history_date"], "2026-10-02")
        self.assertEqual(quality["history"]["age_days_min"], 10)
        self.assertEqual(quality["history"]["age_days_max"], 11)
        self.assertEqual(quality["history"]["unavailable_feature_rows"]["7d"], 2)
        self.assertEqual(quality["history"]["unavailable_feature_rows"]["30d"], 1)
        self.assertEqual(quality["weather"]["t_minus_2h"]["origin_available"], 2)
        self.assertEqual(quality["weather"]["t_minus_2h"]["destination_available"], 1)
        self.assertEqual(quality["weather"]["t_minus_30m"]["destination_available"], 2)

    def test_demo_and_csv_sources_are_not_labeled_as_live_provider_data(self) -> None:
        with patch.object(web, "model_category_support", return_value=self.category_support()), patch.dict(
            os.environ, {"FLIGHT_PROVIDER": "", "FLIGHT_API_KEY": ""}, clear=False
        ):
            for source in ("Bundled synthetic schedule demo", "uploaded schedule CSV"):
                with self.subTest(source=source), patch.object(
                    web, "read_state", return_value={"schedule_source": source, "flights": []}
                ):
                    result = web.api_state()
                    self.assertEqual(result["data_quality"]["schedule_source"], source)
                    self.assertFalse(result["data_quality"]["is_live_aviationstack"])
                    self.assertIsNone(result["data_quality"]["raw_provider_records"])


class OfflineOnlyLaunchTests(unittest.TestCase):
    def test_offline_only_option_is_parsed(self) -> None:
        with patch.object(sys, "argv", ["flight_weather_web.py", "--offline-only"]):
            args = web.parse_args()
        self.assertTrue(args.offline_only)

    def test_offline_only_serves_saved_state_and_blocks_all_writes_without_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data_dir = Path(temp)
            state_path = data_dir / "state.json"
            settings_path = data_dir / "settings.json"
            schedule_path = data_dir / "current_schedule.csv"
            saved_state = {
                "status": "ready",
                "message": "Previously saved live result.",
                "updated_at_utc": "2026-10-10T14:44:02+00:00",
                "schedule_source": "Live Aviationstack data (Free real-time feed)",
                "schedule_count": 1,
                "flights": [{"Flight_Number_Reporting_Airline": "UA123", "Origin": "ATL", "Dest": "BOS"}],
            }
            web.safe_json_write(state_path, saved_state)
            original_state_bytes = state_path.read_bytes()
            settings_path.write_text('{"airports":["ATL"]}\n', encoding="utf-8")

            with patch.multiple(
                web,
                WEB_DATA=data_dir,
                STATE_PATH=state_path,
                SETTINGS_PATH=settings_path,
                SCHEDULE_PATH=schedule_path,
                AVIATIONSTACK_USAGE_PATH=data_dir / "aviationstack_usage.json",
            ), patch.object(web, "OFFLINE_ONLY", True), patch.dict(
                os.environ,
                {"FLIGHT_PROVIDER": "aviationstack", "FLIGHT_API_KEY": "mock-only-secret", "FLIGHT_AIRPORTS": "ATL"},
                clear=False,
            ), patch("requests.get") as provider_request, patch.object(
                web, "refresh_predictions", side_effect=AssertionError("offline-only route must not score")
            ) as refresh, patch.object(
                web, "prediction_command", side_effect=AssertionError("offline-only route must not run inference")
            ) as predict:
                server = ThreadingHTTPServer(("127.0.0.1", 0), web.DashboardHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    with urlopen(base + "/", timeout=5) as response:
                        page = response.read().decode("utf-8")
                    self.assertIn('id="offlineOnlyBanner"', page)
                    self.assertIn("Offline-only review mode", page)
                    for control_id in ("demoBtn", "refreshBtn", "autoBtn", "saveAirports", "airportsInput", "scheduleFile"):
                        self.assertIn(f'"{control_id}"', page)
                    self.assertIn('control.disabled=true;control.setAttribute("aria-disabled","true")', page)
                    self.assertIn('id="dashboardFooter"', page)
                    self.assertIn("Automatic refresh and live provider requests are disabled.", page)
                    self.assertIn("Automatic refresh runs on this computer while both the local server and dashboard tab are open. In the bundled demo, the synthetic schedule is scored by the saved model with weather explicitly marked unavailable. Uploaded schedules use current METAR observations when the weather service is reachable. Historical BTS outcomes may lag recent flights. The 2025 year was previously opened for an earlier baseline comparison and is not an untouched project-wide final test.", page)
                    self.assertIn("renderDashboardFooter(Boolean(s.offline_only))", page)
                    self.assertIn("Disabled in offline-only review mode.", page)
                    load_script = page.split("async function load()", 1)[1].split("async function refresh", 1)[0]
                    self.assertLess(load_script.index("if(state.offline_only)"), load_script.index("await refresh"))
                    self.assertIn("if(state?.offline_only){autoRefresh=false", page)
                    self.assertIn("renderDataQuality(s.data_quality)", page)

                    with urlopen(base + "/api/state", timeout=5) as response:
                        result = json.loads(response.read())
                    self.assertTrue(result["offline_only"])
                    self.assertTrue(result["provider_ready"])
                    self.assertEqual(result["schedule_source"], saved_state["schedule_source"])
                    self.assertEqual(len(result["flights"]), 1)
                    self.assertEqual(result["flights"][0]["Flight_Number_Reporting_Airline"], "UA123")
                    self.assertEqual(result["flights"][0]["Origin"], "ATL")
                    self.assertEqual(result["flights"][0]["Dest"], "BOS")
                    self.assertEqual(result["data_quality"]["schedule_source"], saved_state["schedule_source"])

                    writes = (
                        ("/api/refresh", b'{"force":true}'),
                        ("/api/demo", b"{}"),
                        ("/api/schedule", json.dumps({"csv": VALID_CSV}).encode("utf-8")),
                        ("/api/settings", b'{"airports":["LAX"]}'),
                    )
                    for route, body in writes:
                        request = Request(
                            base + route,
                            data=body,
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with self.subTest(route=route), self.assertRaises(HTTPError) as caught:
                            urlopen(request, timeout=5)
                        self.assertEqual(caught.exception.code, 409)
                        payload = json.loads(caught.exception.read())
                        self.assertTrue(payload["offline_only"])
                    provider_request.assert_not_called()
                    refresh.assert_not_called()
                    predict.assert_not_called()
                    self.assertEqual(state_path.read_bytes(), original_state_bytes)
                    self.assertFalse(schedule_path.exists())
                    self.assertEqual(settings_path.read_text(encoding="utf-8"), '{"airports":["ATL"]}\n')
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_horizon_ui_labels_separate_cutoff_status_from_threshold_flags(self) -> None:
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function thresholdFlag(row,horizon)", page)
        self.assertIn("probability>=threshold", page)
        self.assertIn("Cutoff pending · score at / above threshold (active flag)", page)
        self.assertIn("Cutoff pending · below threshold (no active flag)", page)
        self.assertIn("Cutoff pending · threshold decision unavailable", page)
        self.assertIn('status==="actionable"&&thresholdFlag(row,h)===true', page)
        self.assertIn('status.textContent=staleSnapshot?"Historical score · no active flag":activeFlags.length?"Active threshold flag"', page)
        self.assertNotIn("Below threshold · actionable", page)
        self.assertNotIn("actionable · current saved score", page)

    def test_offline_saved_snapshot_rebuilds_quality_and_recomputes_horizons_without_scoring(self) -> None:
        now = datetime.fromisoformat("2026-10-11T16:00:00+00:00")
        expired_departure = inference.scheduled_departure_utc("2026-10-10", 900, "America/New_York")
        future_departure = inference.scheduled_departure_utc("2026-10-12", 900, "America/New_York")

        def row(flight_date, departure, airline, destination):
            return {
                "FlightDate": flight_date,
                "Reporting_Airline": airline,
                "Origin": "ATL",
                "Dest": destination,
                "CRSDepTime": "900",
                "CRSArrTime": "1135",
                "CRSElapsedTime": "155",
                "Distance": "",
                "prediction_cutoff_utc_t_minus_2h": (departure - timedelta(hours=2)).isoformat(),
                "prediction_cutoff_utc_t_minus_30m": (departure - timedelta(minutes=30)).isoformat(),
                "predicted_delay_probability_t_minus_2h": 0.7,
                "predicted_delay_probability_t_minus_30m": 0.7,
                "predicted_delay_t_minus_2h": 1,
                "predicted_delay_t_minus_30m": 1,
                "history_source_max_date": "2026-07-31",
                "history_age_days": (datetime.strptime(flight_date, "%Y-%m-%d").date() - datetime(2026, 7, 31).date()).days,
                "prev7d_history_missing": 1,
                "prev30d_history_missing": 1,
                "prev3m_history_missing": 0,
                "origin_weather_missing_t_minus_2h": 1,
                "destination_weather_missing_t_minus_2h": 1,
                "origin_weather_missing_t_minus_30m": 1,
                "destination_weather_missing_t_minus_30m": 1,
            }

        saved_state = {
            "status": "ready",
            "updated_at_utc": "2026-10-10T15:13:59.909433+00:00",
            "schedule_source": web.AVIATIONSTACK_SCHEDULE_SOURCE,
            "schedule_count": 2,
            "provider_records_returned": 3,
            "provider_eligible_records": 2,
            "flights": [
                row("2026-10-10", expired_departure, "ZZ", "XYZ"),
                row("2026-10-12", future_departure, "UA", "BOS"),
            ],
        }
        support = {
            horizon: {
                "available": True,
                "airlines": frozenset({"UA"}),
                "destinations": frozenset({"BOS"}),
            }
            for horizon in ("t_minus_2h", "t_minus_30m")
        }

        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data_dir = Path(temp)
            state_path = data_dir / "state.json"
            web.safe_json_write(state_path, saved_state)
            original_state = state_path.read_bytes()
            settings_path = data_dir / "settings.json"
            settings_path.write_text('{"airports":["ATL"]}\n', encoding="utf-8")
            with patch.multiple(
                web,
                WEB_DATA=data_dir,
                STATE_PATH=state_path,
                SETTINGS_PATH=settings_path,
                SCHEDULE_PATH=data_dir / "schedule.csv",
                AVIATIONSTACK_USAGE_PATH=data_dir / "usage.json",
            ), patch.object(web, "OFFLINE_ONLY", True), patch.object(web, "utc_now", return_value=now), patch.object(
                web, "model_category_support", return_value=support
            ), patch.dict(
                os.environ,
                {"FLIGHT_PROVIDER": "aviationstack", "FLIGHT_API_KEY": "offline-test-key", "FLIGHT_AIRPORTS": "ATL"},
                clear=False,
            ), patch("requests.get") as provider_request, patch.object(
                web, "prediction_command", side_effect=AssertionError("offline snapshot must not run inference")
            ) as predict:
                server = ThreadingHTTPServer(("127.0.0.1", 0), web.DashboardHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with urlopen(f"http://127.0.0.1:{server.server_port}/api/state", timeout=5) as response:
                        result = json.loads(response.read())
                    quality = result["data_quality"]
                    self.assertTrue(result["offline_only"])
                    self.assertEqual(result["prediction_snapshot_status"], "stale")
                    self.assertEqual(result["prediction_snapshot_at_utc"], saved_state["updated_at_utc"])
                    self.assertGreater(result["prediction_snapshot_age_seconds"], 24 * 60 * 60)
                    self.assertEqual(quality["raw_provider_records"], 3)
                    self.assertEqual(quality["eligible_provider_records"], 2)
                    self.assertEqual(quality["eligible_scored_records"], 2)
                    self.assertEqual(quality["missing_route_distance_rows"], 2)
                    self.assertEqual(quality["model_input_support"]["t_minus_2h"]["unsupported_carrier_rows"], 1)
                    self.assertEqual(quality["model_input_support"]["t_minus_2h"]["unsupported_destination_rows"], 1)
                    self.assertTrue(result["flights"][0]["model_input_support_unverified"])
                    self.assertTrue(result["flights"][0]["model_input_support_by_horizon"]["t_minus_2h"]["missing_route_distance"])
                    self.assertEqual(result["flights"][0]["prediction_horizon_status_t_minus_2h"], "elapsed")
                    self.assertEqual(result["flights"][0]["prediction_horizon_status_t_minus_30m"], "elapsed")
                    self.assertEqual(result["flights"][1]["prediction_horizon_status_t_minus_2h"], "actionable")
                    self.assertEqual(result["flights"][1]["prediction_horizon_status_t_minus_30m"], "actionable")
                    self.assertEqual(quality["horizon_status"]["t_minus_2h"]["elapsed"], 1)
                    self.assertEqual(quality["horizon_status"]["t_minus_2h"]["actionable"], 1)
                    self.assertEqual(quality["horizon_status"]["t_minus_30m"]["elapsed"], 1)
                    self.assertEqual(quality["horizon_status"]["t_minus_30m"]["actionable"], 1)
                    self.assertEqual(quality["horizon_status"]["t_minus_2h"]["active_threshold_flags"], 0)
                    self.assertEqual(quality["horizon_status"]["t_minus_2h"]["stale_saved_scores"], 2)
                    provider_request.assert_not_called()
                    predict.assert_not_called()
                    self.assertEqual(state_path.read_bytes(), original_state)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)


class DashboardIntegrationTests(unittest.TestCase):
    @requires_private_data
    def test_browser_page_and_offline_demo_endpoint_run_saved_models(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data_dir = Path(temp)
            with patch.multiple(
                web,
                WEB_DATA=data_dir,
                STATE_PATH=data_dir / "state.json",
                SETTINGS_PATH=data_dir / "settings.json",
                SCHEDULE_PATH=data_dir / "schedule.csv",
            ), patch.dict(os.environ, {"FLIGHT_PROVIDER": "", "FLIGHT_API_KEY": ""}, clear=False):
                server = ThreadingHTTPServer(("127.0.0.1", 0), web.DashboardHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    with urlopen(base + "/", timeout=5) as response:
                        page = response.read().decode("utf-8")
                    self.assertIn("Load offline demo", page)
                    self.assertIn('id="dataQualityPanel"', page)
                    self.assertIn("Cutoff elapsed · saved score for traceability", page)
                    self.assertIn("Reliability unverified", page)

                    request = Request(base + "/api/demo", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
                    with urlopen(request, timeout=120) as response:
                        result = json.loads(response.read())
                    self.assertEqual(result["status"], "ready")
                    self.assertEqual(result["schedule_source"], "Bundled synthetic schedule demo")
                    self.assertEqual(result["data_quality"]["schedule_source"], "Bundled synthetic schedule demo")
                    self.assertFalse(result["data_quality"]["is_live_aviationstack"])
                    self.assertIsNone(result["data_quality"]["raw_provider_records"])
                    self.assertIn("not live operational data", result["message"])
                    self.assertEqual(result["weather_status"], "omitted")
                    self.assertEqual(result["history_splits_read"], ["train", "operational_history"])
                    self.assertEqual(len(result["flights"]), 1)
                    self.assertGreaterEqual(float(result["flights"][0]["predicted_delay_probability_t_minus_2h"]), 0.0)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    @requires_private_data
    def test_mocked_live_refresh_scores_models_labels_source_and_preserves_csv_fallback(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            data_dir = Path(temp)
            state_path = data_dir / "state.json"
            web.safe_json_write(
                state_path,
                {
                    "status": "ready",
                    "message": "Scored a bundled synthetic example.",
                    "updated_at_utc": web.utc_text(),
                    "schedule_source": "Bundled synthetic schedule demo",
                    "schedule_count": 1,
                    "flights": [{"Flight_Number_Reporting_Airline": "DEMO-ONLY"}],
                    "aviationstack_last_request_at_utc": web.utc_text(),
                    "aviationstack_last_request_origin": "ATL",
                },
            )
            record = ProviderBoundaryTests()._scheduled_record()

            class MockProviderResponse:
                status_code = 200

                def json(self):
                    return {"pagination": {"count": 1}, "data": [record]}

            with patch.multiple(
                web,
                WEB_DATA=data_dir,
                STATE_PATH=state_path,
                SETTINGS_PATH=data_dir / "settings.json",
                SCHEDULE_PATH=data_dir / "schedule.csv",
                AVIATIONSTACK_USAGE_PATH=data_dir / "usage.json",
            ), patch.dict(
                os.environ,
                {"FLIGHT_PROVIDER": "aviationstack", "FLIGHT_API_KEY": "mock-only-api-secret", "FLIGHT_AIRPORTS": ""},
                clear=False,
            ), patch.dict(web.AVIATIONSTACK_CACHE, {}, clear=True), patch.object(
                web, "AVIATIONSTACK_LAST_REQUEST_AT_UTC", None
            ), patch.object(web, "AVIATIONSTACK_LAST_REQUEST_ORIGIN", None):
                original_command = web.prediction_command

                def prediction_without_weather(input_path, output_path, weather_mode="live"):
                    return original_command(input_path, output_path, "none")

                with patch.object(web, "prediction_command", side_effect=prediction_without_weather), patch(
                    "requests.get", return_value=MockProviderResponse()
                ) as get:
                    server = ThreadingHTTPServer(("127.0.0.1", 0), web.DashboardHandler)
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    base = f"http://127.0.0.1:{server.server_port}"
                    try:
                        with urlopen(base + "/", timeout=5) as response:
                            page = response.read().decode("utf-8")
                        self.assertIn('source.textContent=s.schedule_source||"No schedule"', page)
                        self.assertIn('safe(s.schedule_source,"schedule")', page)
                        self.assertIn('timeZone:"Asia/Kolkata"', page)
                        self.assertIn('timeZoneName:"short"', page)
                        self.assertIn('allowErrorStatus=false', page)
                        self.assertIn('id="providerDiagnostics"', page)
                        self.assertIn("HTTP response received", page)
                        self.assertIn(
                            '$("refreshBtn").addEventListener("click",()=>state?.schedule_source==="Bundled synthetic schedule demo"&&!state.provider_ready?loadDemo():refresh(true',
                            page,
                        )

                        with urlopen(base + "/api/state", timeout=5) as response:
                            api_state = json.loads(response.read())
                        self.assertTrue(api_state["provider_ready"])
                        self.assertTrue(api_state["provider_key_configured"])
                        self.assertEqual(api_state["settings"]["airports"], ["ATL"])
                        self.assertTrue(api_state["provider_diagnostics"]["provider_configured"])
                        self.assertFalse(api_state["provider_diagnostics"]["live_refresh_attempted"])

                        refresh = Request(
                            base + "/api/refresh",
                            data=b'{"force":true}',
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(refresh, timeout=120) as response:
                            live_result = json.loads(response.read())
                        self.assertEqual(live_result["status"], "ready")
                        self.assertEqual(live_result["schedule_source"], "Live Aviationstack data (Free real-time feed)")
                        self.assertEqual(live_result["data_quality"]["schedule_source"], live_result["schedule_source"])
                        self.assertTrue(live_result["data_quality"]["is_live_aviationstack"])
                        self.assertEqual(live_result["data_quality"]["raw_provider_records"], 1)
                        self.assertEqual(live_result["data_quality"]["eligible_provider_records"], 1)
                        self.assertIn("Live Aviationstack data", live_result["message"])
                        self.assertTrue(live_result["provider_diagnostics"]["live_refresh_attempted"])
                        self.assertTrue(live_result["provider_diagnostics"]["live_response_received"])
                        self.assertEqual(live_result["provider_diagnostics"]["live_http_status"], 200)
                        self.assertEqual(live_result["provider_diagnostics"]["displayed_data_source"], live_result["schedule_source"])
                        self.assertEqual(len(live_result["flights"]), 1)
                        self.assertEqual(live_result["flights"][0]["Flight_Number_Reporting_Airline"], "UA123")
                        self.assertEqual(live_result["flights"][0]["Origin"], "ATL")
                        self.assertEqual(live_result["flights"][0]["Dest"], "BOS")
                        self.assertEqual(live_result["flights"][0]["Distance"], "")
                        self.assertTrue(0 <= float(live_result["flights"][0]["predicted_delay_probability_t_minus_2h"]) <= 1)
                        get.assert_called_once()
                        args, kwargs = get.call_args
                        self.assertEqual(args[0], web.AVIATIONSTACK_API_URL)
                        self.assertEqual(kwargs["params"]["dep_iata"], "ATL")
                        self.assertEqual(kwargs["params"]["flight_status"], "scheduled")

                        upload = Request(
                            base + "/api/schedule",
                            data=json.dumps({"csv": VALID_CSV}).encode("utf-8"),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(upload, timeout=5) as response:
                            uploaded = json.loads(response.read())
                        self.assertEqual(uploaded["uploaded_schedule_count"], 1)
                        self.assertEqual(uploaded["state"]["schedule_source"], live_result["schedule_source"])

                        score_csv = Request(
                            base + "/api/refresh",
                            data=b'{"force":true,"schedule_source":"uploaded schedule CSV"}',
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urlopen(score_csv, timeout=120) as response:
                            csv_result = json.loads(response.read())
                        self.assertEqual(csv_result["status"], "ready")
                        self.assertEqual(csv_result["schedule_source"], "uploaded schedule CSV")
                        self.assertEqual(csv_result["data_quality"]["schedule_source"], "uploaded schedule CSV")
                        self.assertFalse(csv_result["data_quality"]["is_live_aviationstack"])
                        self.assertIn("uploaded schedule CSV", csv_result["message"])
                        self.assertEqual(csv_result["flights"][0]["Origin"], "ATL")
                        get.assert_called_once()
                    finally:
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)

    def test_utc_update_timestamp_maps_to_india_standard_time(self) -> None:
        timestamp = datetime.fromisoformat("2026-10-10T14:44:02.939328+00:00")
        india_time = timestamp.astimezone(ZoneInfo("Asia/Kolkata"))
        self.assertEqual(india_time.strftime("%Y-%m-%d %H:%M:%S %z"), "2026-10-10 20:14:02 +0530")
        page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('timeZone:"Asia/Kolkata"', page)
        self.assertIn('timeZoneName:"short"', page)


if __name__ == "__main__":
    unittest.main()
