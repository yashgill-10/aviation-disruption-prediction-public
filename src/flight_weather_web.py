"""Local web dashboard for refreshed flight-delay risk predictions.

The server binds to localhost by default. Flight-provider credentials stay on
the server and are read from environment variables or a git-ignored .env file.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from functools import lru_cache
import html
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time as time_module
from typing import Any
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, quote_plus, urlparse
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "web"
WEB_DATA = ROOT / "data" / "processed" / "depdel15" / "live_web"
STATE_PATH = WEB_DATA / "state.json"
SETTINGS_PATH = WEB_DATA / "settings.json"
SCHEDULE_PATH = WEB_DATA / "current_schedule.csv"
AVIATIONSTACK_USAGE_PATH = WEB_DATA / "aviationstack_usage.json"
HTML_PATH = WEB_ROOT / "index.html"
REQUIRED_COLUMNS = (
    "FlightDate",
    "Reporting_Airline",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "Distance",
    "CRSElapsedTime",
)
FORBIDDEN_COLUMNS = {
    "depdel15", "arrdel15", "depdelay", "depdelayminutes", "arrdelay", "arrdelayminutes",
    "actualdeptime", "actualdeparturetime", "deptime", "actualarrtime", "actualarrivaltime", "arrtime",
    "cancelled", "cancelledflag", "cancellationcode", "diverted", "taxiin", "taxiout",
    "wheelsoff", "wheelson", "airtime", "actualelapsedtime", "carrierdelay", "weatherdelay",
    "nasdelay", "securitydelay", "lateaircraftdelay",
}
DEFAULT_REFRESH_MINUTES = 15
MAX_BODY_BYTES = 1_000_000
MAX_FLIGHTS = 500
REFRESH_LOCK = threading.Lock()
AVIATIONSTACK_API_URL = "https://api.aviationstack.com/v1/flights"
AVIATIONSTACK_SCHEDULE_SOURCE = "Live Aviationstack data (Free real-time feed)"
AVIATIONSTACK_CACHE_SECONDS = 8 * 60 * 60
AVIATIONSTACK_LOCAL_REQUEST_CAP = 80
AVIATIONSTACK_CACHE: dict[str, Any] = {"key": None, "rows": None, "records_returned": None, "expires_at": 0.0}
AVIATIONSTACK_LAST_DIAGNOSTICS: dict[str, Any] = {}
AVIATIONSTACK_LAST_REQUEST_AT_UTC: str | None = None
AVIATIONSTACK_LAST_REQUEST_ORIGIN: str | None = None
AVIATIONSTACK_USAGE_LOCK = threading.Lock()
OFFLINE_ONLY = False


class AviationstackAPIError(RuntimeError):
    """A sanitized API error with fields safe to show in the smoke check."""

    def __init__(
        self,
        *,
        status_code: int,
        category: str,
        error_code: str,
        error_type: str,
        provider_message: str,
        content_type: str = "",
        server_header: str = "",
        body_summary: str = "",
    ) -> None:
        self.status_code = status_code
        self.category = category
        self.error_code = error_code
        self.error_type = error_type
        self.provider_message = provider_message
        self.content_type = content_type
        self.server_header = server_header
        self.body_summary = body_summary
        details = [f"HTTP {status_code}", category.replace("_", " ")]
        if error_code:
            details.append(f"code {error_code}")
        if error_type:
            details.append(f"type {error_type}")
        super().__init__("Aviationstack error (" + "; ".join(details) + "): " + provider_message)


def _sanitize_aviationstack_text(value: Any, api_key: str, *, limit: int = 300) -> str:
    """Remove secrets, URLs, and control characters before exposing provider text."""
    text = str(value or "")
    if api_key:
        for secret_form in {api_key, quote(api_key, safe=""), quote_plus(api_key, safe="")}:
            text = text.replace(secret_form, "[REDACTED]")
    # Remove credential-bearing URL fragments before the more general URL pass.
    text = re.sub(
        r"(?i)(?:https?://)?[^\s\"'<>]+(?:\?|%3f)[^\s\"'<>]*(?:access[_-]?key|api[_-]?key|token)(?:=|%3d)[^\s\"'<>]+",
        "[URL redacted]",
        text,
    )
    text = re.sub(r"(?i)https?://[^\s\"'<>]+", "[URL redacted]", text)
    text = re.sub(
        r"(?i)(access[_-]?key|api[_-]?key|token|authorization)\s*(?:[:=]|%3d)\s*[^&\s,;]+",
        r"\1=[REDACTED]",
        text,
    )
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return text[:limit] or "No provider message was returned."


def _aviationstack_response_metadata(response: Any, api_key: str) -> tuple[str, str, str]:
    headers = getattr(response, "headers", {}) or {}
    try:
        content_type = _sanitize_aviationstack_text(headers.get("Content-Type", ""), api_key, limit=120)
    except (AttributeError, TypeError):
        content_type = ""
    try:
        server_header = _sanitize_aviationstack_text(headers.get("Server", ""), api_key, limit=120)
    except (AttributeError, TypeError):
        server_header = ""

    raw_body = getattr(response, "text", "") or ""
    if not raw_body:
        body_summary = "No response body was returned."
    else:
        body_text = html.unescape(str(raw_body)[:5000])
        body_text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>", " ", body_text)
        body_text = re.sub(r"(?s)<[^>]+>", " ", body_text)
        truncated = len(body_text) > 300
        body_summary = _sanitize_aviationstack_text(body_text, api_key, limit=300)
        if truncated:
            body_summary += " [truncated]"
    return content_type, server_header, body_summary


def _aviationstack_api_error(
    status_code: int, payload: Any, api_key: str, response: Any = None
) -> AviationstackAPIError:
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else ({"message": error} if isinstance(error, str) else {})
    code = _sanitize_aviationstack_text(error.get("code"), api_key, limit=80) if error.get("code") else ""
    error_type = _sanitize_aviationstack_text(error.get("type"), api_key, limit=100) if error.get("type") else ""
    provider_message = next(
        (error.get(field) for field in ("info", "message", "description") if error.get(field)),
        "No structured provider message was returned.",
    )
    provider_message = _sanitize_aviationstack_text(provider_message, api_key)
    content_type, server_header, body_summary = (
        _aviationstack_response_metadata(response, api_key)
        if response is not None
        else ("", "", "")
    )
    code_and_type = f"{code} {error_type}".lower()

    if "missing_access_key" in code_and_type:
        category = "missing_key"
    elif "invalid_access_key" in code_and_type or code == "101":
        category = "invalid_key"
    elif "inactive_user" in code_and_type or code == "102":
        category = "inactive_account"
    elif "invalid_api_function" in code_and_type or code == "103":
        category = "invalid_endpoint_or_function"
    elif any(item in code_and_type for item in ("function_access_restricted", "https_access_restricted")) or code == "105":
        category = "plan_or_endpoint_restriction"
    elif any(item in code_and_type for item in ("usage_limit_reached", "rate_limit_reached")) or code == "104" or status_code == 429:
        category = "quota_or_rate_limit"
    elif status_code == 401:
        category = "authentication_rejected_unspecified"
    elif status_code == 403:
        category = "access_denied_unspecified"
    else:
        category = "provider_error"

    return AviationstackAPIError(
        status_code=status_code,
        category=category,
        error_code=code,
        error_type=error_type,
        provider_message=provider_message,
        content_type=content_type,
        server_header=server_header,
        body_summary=body_summary,
    )
# Live provider integrations are registered here only after the key's provider
# is confirmed and its supported API is verified. CSV and demo modes remain.
SCHEDULE_ADAPTERS: dict[str, Any] = {}


def load_dotenv() -> None:
    """Load simple KEY=value settings without printing or exposing secrets."""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text() -> str:
    return utc_now().isoformat(timespec="seconds")


def provider_name() -> str:
    return os.environ.get("FLIGHT_PROVIDER", "").strip().lower()


def api_key_configured() -> bool:
    return bool(os.environ.get("FLIGHT_API_KEY", "").strip())


def provider_ready() -> bool:
    return provider_name() in SCHEDULE_ADAPTERS and api_key_configured()


def refresh_minutes() -> int:
    try:
        value = int(os.environ.get("FLIGHT_REFRESH_MINUTES", DEFAULT_REFRESH_MINUTES))
    except ValueError:
        value = DEFAULT_REFRESH_MINUTES
    value = min(max(value, 5), 60)
    # The Free Aviationstack plan includes 100 calls/month. One cached request
    # every eight hours caps automatic use at about 90 calls in a 30-day month.
    if provider_name() == "aviationstack":
        return max(value, 8 * 60)
    return value


def safe_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp_path.replace(path)


def default_state() -> dict[str, Any]:
    return {
        "status": "ready",
        "message": "Add a flight schedule or configure a live schedule provider to begin.",
        "updated_at_utc": None,
        "schedule_source": None,
        "schedule_count": 0,
        "flights": [],
        "weather_source": "AviationWeather.gov decoded METAR observations",
        "last_error": None,
    }


def read_state() -> dict[str, Any]:
    if not STATE_PATH.is_file():
        return default_state()
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = default_state()
    base = default_state()
    base.update(value if isinstance(value, dict) else {})
    return base


def read_settings() -> dict[str, Any]:
    try:
        value = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, json.JSONDecodeError):
        pass
    # Keep first-run dashboard behavior aligned with .env.example and the
    # Aviationstack smoke check, both of which use ATL as the single origin.
    env_airports = os.environ.get("FLIGHT_AIRPORTS", "").strip() or "ATL"
    return {"airports": normalize_airports(env_airports.split(","))}


def normalize_airports(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    if not isinstance(values, list):
        raise ValueError("Airports must be a comma-separated list of airport codes.")
    airports = []
    for raw in values:
        code = str(raw).strip().upper()
        if not code:
            continue
        if not re.fullmatch(r"[A-Z0-9]{3,4}", code):
            raise ValueError(f"Invalid airport code: {code}")
        if code not in airports:
            airports.append(code)
    if len(airports) > 10:
        raise ValueError("Monitor at most 10 airports in this prototype to control API usage.")
    return airports


def parse_csv_schedule(csv_text: str) -> list[dict[str, str]]:
    if len(csv_text.encode("utf-8")) > MAX_BODY_BYTES:
        raise ValueError("Schedule CSV is too large (maximum 1 MB).")
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        raise ValueError("The CSV needs a header row.")
    cleaned_names = [str(name).strip() for name in reader.fieldnames]
    if len(cleaned_names) != len(set(cleaned_names)):
        raise ValueError("The CSV contains duplicate column names.")
    names = set(cleaned_names)
    normalized_names = {
        name: re.sub(r"[^a-z0-9]", "", name.lower()) for name in names
    }
    forbidden = sorted(name for name, normalized in normalized_names.items() if normalized in FORBIDDEN_COLUMNS)
    if forbidden:
        raise ValueError("Remove target or post-departure columns before upload: " + ", ".join(forbidden))
    missing = sorted(set(REQUIRED_COLUMNS) - names)
    if missing:
        raise ValueError("Missing schedule columns: " + ", ".join(missing))
    allowed = set(REQUIRED_COLUMNS) | {"Flight_Number_Reporting_Airline"}
    unexpected = sorted(names - allowed)
    if unexpected:
        raise ValueError("Unexpected columns are not accepted in schedule input: " + ", ".join(unexpected))

    rows = []
    for row_number, raw in enumerate(reader, start=2):
        if not raw or not any(value and value.strip() for value in raw.values() if isinstance(value, str)):
            continue
        normalized = {str(key).strip(): (value or "").strip() for key, value in raw.items() if key}
        flight_date = normalized.get("FlightDate", "")[:10]
        try:
            datetime.strptime(flight_date, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"Row {row_number}: FlightDate must use YYYY-MM-DD.") from exc

        for column in ("CRSDepTime", "CRSArrTime"):
            try:
                raw_time = float(normalized[column])
                hhmm = int(raw_time)
                hour, minute = divmod(hhmm, 100)
                if raw_time != hhmm or not 0 <= hhmm <= 2400 or minute >= 60 or hour > 24 or (hour == 24 and minute):
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Row {row_number}: {column} must be a valid local HHMM schedule time.") from exc

        for column in ("Distance", "CRSElapsedTime"):
            try:
                number = float(normalized[column])
                if not (number > 0 and number < 10000):
                    raise ValueError
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"Row {row_number}: {column} must be a positive number.") from exc

        for column in ("Reporting_Airline", "Origin", "Dest"):
            normalized[column] = normalized[column].upper()
            if not normalized[column]:
                raise ValueError(f"Row {row_number}: {column} cannot be blank.")
        normalized["FlightDate"] = flight_date
        output = {column: normalized[column] for column in REQUIRED_COLUMNS}
        flight_number = normalized.get("Flight_Number_Reporting_Airline", "")
        if flight_number:
            output["Flight_Number_Reporting_Airline"] = flight_number
        rows.append(output)
        if len(rows) > MAX_FLIGHTS:
            raise ValueError(f"Upload no more than {MAX_FLIGHTS} flights per refresh.")
    if not rows:
        raise ValueError("The CSV contains no valid scheduled flights.")
    return rows


def write_schedule(rows: list[dict[str, Any]], path: Path | None = None) -> None:
    target = path or SCHEDULE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        has_flight_number = any("Flight_Number_Reporting_Airline" in row for row in rows)
        columns = [
            *REQUIRED_COLUMNS,
            *( ["Flight_Number_Reporting_Airline"] if has_flight_number else []),
        ]
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(target)


def read_schedule_rows() -> list[dict[str, str]]:
    if not SCHEDULE_PATH.is_file():
        return []
    with SCHEDULE_PATH.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def fetch_provider_schedule(airports: list[str]) -> list[dict[str, str]]:
    """Call the confirmed provider adapter for the configured service."""
    name = provider_name()
    key = os.environ.get("FLIGHT_API_KEY", "").strip()
    if not name or not key:
        raise ValueError("Configure FLIGHT_PROVIDER and FLIGHT_API_KEY in the git-ignored .env file.")
    if not airports:
        raise ValueError("Add at least one airport to the monitor list before fetching live schedules.")
    adapter = SCHEDULE_ADAPTERS.get(name)
    if adapter is None:
        raise ValueError("The API-key provider is not confirmed or enabled yet. Upload a schedule CSV or use the offline demo.")
    return adapter(key, airports)


def fetch_flightaware_schedules(api_key: str, airports: list[str]) -> list[dict[str, str]]:
    import pandas as pd
    import requests

    map_path = ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
    if not map_path.is_file():
        raise FileNotFoundError(f"Airport/timezone map not found: {map_path}")
    airport_rows = list(csv.DictReader(map_path.open("r", newline="", encoding="utf-8-sig")))
    by_iata = {row["airport_code"].strip().upper(): row for row in airport_rows if row.get("airport_code")}
    by_icao = {row["airport_icao"].strip().upper(): row for row in airport_rows if row.get("airport_icao")}
    now = utc_now()
    try:
        lookahead = min(max(int(os.environ.get("FLIGHT_LOOKAHEAD_HOURS", "6")), 1), 48)
    except ValueError:
        lookahead = 6
    start_text = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    end_text = (now + timedelta(hours=lookahead)).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        max_pages = min(max(int(os.environ.get("FLIGHTAWARE_MAX_PAGES", "1")), 1), 5)
    except ValueError:
        max_pages = 1

    session = requests.Session()
    session.headers.update({"x-apikey": api_key, "Accept": "application/json", "User-Agent": "aviation-disruption-prediction/0.1"})
    scheduled: list[dict[str, str]] = []
    seen: set[str] = set()
    for watched_code in airports:
        airport = by_iata.get(watched_code) or by_icao.get(watched_code)
        if airport is None:
            raise ValueError(f"Airport {watched_code} is not in the project airport/timezone map.")
        query_code = (airport.get("airport_icao") or watched_code).strip().upper()
        url = f"https://aeroapi.flightaware.com/aeroapi/airports/{query_code}/flights/scheduled_departures"
        response = session.get(
            url,
            params={"start": start_text, "end": end_text, "type": "Airline", "max_pages": max_pages},
            timeout=25,
        )
        if response.status_code >= 400:
            try:
                details = response.json()
                detail = details.get("detail") or details.get("title") or response.reason
            except (ValueError, AttributeError):
                detail = response.reason
            raise RuntimeError(f"FlightAware returned HTTP {response.status_code} for {watched_code}: {detail}")
        payload = response.json()
        for flight in payload.get("scheduled_departures", []):
            if flight.get("cancelled") or not flight.get("scheduled_out") or not flight.get("scheduled_in"):
                continue
            origin_ref = flight.get("origin") or {}
            dest_ref = flight.get("destination") or {}
            origin = (origin_ref.get("code_iata") or origin_ref.get("code") or "").strip().upper()
            destination = (dest_ref.get("code_iata") or dest_ref.get("code") or "").strip().upper()
            origin_row = by_iata.get(origin) or by_icao.get(origin) or by_iata.get(watched_code) or by_icao.get(watched_code)
            dest_row = by_iata.get(destination) or by_icao.get(destination)
            if origin_row is None or dest_row is None:
                continue
            origin = origin_row["airport_code"].strip().upper()
            destination = dest_row["airport_code"].strip().upper()

            airline = (flight.get("operator_iata") or "").strip().upper()
            ident_iata = str(flight.get("ident_iata") or "").strip().upper()
            if not airline and ident_iata:
                airline_match = re.match(r"^([A-Z0-9]{2,3})", ident_iata)
                airline = airline_match.group(1) if airline_match else ""
            flight_number = str(flight.get("flight_number") or "").strip()
            if not airline or not flight_number:
                continue
            try:
                dep_utc = pd.Timestamp(flight["scheduled_out"])
                arr_utc = pd.Timestamp(flight["scheduled_in"])
                if dep_utc.tzinfo is None or arr_utc.tzinfo is None:
                    continue
                dep_utc = dep_utc.tz_convert("UTC")
                arr_utc = arr_utc.tz_convert("UTC")
                dep_local = dep_utc.tz_convert(ZoneInfo(origin_row["timezone"]))
                arr_local = arr_utc.tz_convert(ZoneInfo(dest_row["timezone"]))
                elapsed_minutes = (arr_utc - dep_utc).total_seconds() / 60.0
                distance = great_circle_miles(origin_row, dest_row)
            except (TypeError, ValueError, KeyError, OverflowError):
                continue
            if not 1 <= elapsed_minutes <= 1800 or not 1 <= distance <= 12000:
                continue
            flight_id = str(flight.get("fa_flight_id") or f"{ident_iata}-{dep_utc.isoformat()}")
            if flight_id in seen:
                continue
            seen.add(flight_id)
            scheduled.append(
                {
                    "FlightDate": dep_local.strftime("%Y-%m-%d"),
                    "Reporting_Airline": airline,
                    "Origin": origin,
                    "Dest": destination,
                    "CRSDepTime": str(dep_local.hour * 100 + dep_local.minute),
                    "CRSArrTime": str(arr_local.hour * 100 + arr_local.minute),
                    "Distance": f"{distance:.1f}",
                    "CRSElapsedTime": f"{elapsed_minutes:.0f}",
                    "Flight_Number_Reporting_Airline": flight_number,
                }
            )
    scheduled.sort(
        key=lambda row: (
            row["FlightDate"], row["Origin"], int(row["CRSDepTime"]),
            row["Reporting_Airline"], row["Flight_Number_Reporting_Airline"],
        )
    )
    return scheduled


def _parse_aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def reserve_aviationstack_request(now: datetime) -> int:
    """Persist a conservative rolling cap before spending a provider request."""
    cutoff = now.astimezone(timezone.utc) - timedelta(days=31)
    with AVIATIONSTACK_USAGE_LOCK:
        if AVIATIONSTACK_USAGE_PATH.is_file():
            try:
                payload = json.loads(AVIATIONSTACK_USAGE_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise RuntimeError(
                    "The local Aviationstack safety counter cannot be read; no API request was sent. "
                    "Use the CSV/demo fallback until the counter file is repaired."
                ) from None
            if not isinstance(payload, dict) or not isinstance(payload.get("request_times_utc"), list):
                raise RuntimeError(
                    "The local Aviationstack safety counter has an invalid format; no API request was sent. "
                    "Use the CSV/demo fallback until the counter file is repaired."
                )
            raw_log = payload["request_times_utc"]
        else:
            raw_log = []
        recent = []
        for raw in raw_log if isinstance(raw_log, list) else []:
            parsed = _parse_aware_timestamp(raw)
            if parsed is None:
                raise RuntimeError(
                    "The local Aviationstack safety counter contains an invalid timestamp; no API request was sent. "
                    "Use the CSV/demo fallback until the counter file is repaired."
                )
            if parsed and cutoff <= parsed <= now.astimezone(timezone.utc):
                recent.append(parsed.isoformat(timespec="seconds"))
        if len(recent) >= AVIATIONSTACK_LOCAL_REQUEST_CAP:
            raise RuntimeError(
                f"This app reached its local safety cap of {AVIATIONSTACK_LOCAL_REQUEST_CAP} Aviationstack requests "
                "in 31 days. No request was sent; the remaining Free-plan allowance is reserved for other key use."
            )
        recent.append(now.astimezone(timezone.utc).isoformat(timespec="seconds"))
        safe_json_write(AVIATIONSTACK_USAGE_PATH, {"request_times_utc": recent})
        return len(recent)


def normalize_aviationstack_flight(
    flight: dict[str, Any], airport_by_iata: dict[str, dict[str, str]], now: datetime
) -> dict[str, str] | None:
    """Map only a future scheduled record; never copy actual/outcome fields."""
    if str(flight.get("flight_status") or "").strip().lower() != "scheduled":
        return None
    departure = flight.get("departure")
    arrival = flight.get("arrival")
    airline = flight.get("airline")
    if not isinstance(departure, dict) or not isinstance(arrival, dict) or not isinstance(airline, dict):
        return None
    # A contradictory actual-off-block timestamp means this record is no
    # longer eligible, even if the provider's status has not updated yet.
    if departure.get("actual"):
        return None

    dep_utc = _parse_aware_timestamp(departure.get("scheduled"))
    arr_utc = _parse_aware_timestamp(arrival.get("scheduled"))
    if dep_utc is None or arr_utc is None or dep_utc <= now.astimezone(timezone.utc):
        return None

    origin = str(departure.get("iata") or "").strip().upper()
    destination = str(arrival.get("iata") or "").strip().upper()
    airline_code = str(airline.get("iata") or "").strip().upper()
    origin_meta = airport_by_iata.get(origin)
    destination_meta = airport_by_iata.get(destination)
    if not origin_meta or not destination_meta or not airline_code:
        return None
    try:
        dep_local = dep_utc.astimezone(ZoneInfo(origin_meta["timezone"]))
        arr_local = arr_utc.astimezone(ZoneInfo(destination_meta["timezone"]))
    except (KeyError, ValueError, TypeError):
        return None
    elapsed = (arr_utc - dep_utc).total_seconds() / 60.0
    if not 1 <= elapsed <= 1800:
        return None

    row = {
        "FlightDate": dep_local.strftime("%Y-%m-%d"),
        "Reporting_Airline": airline_code,
        "Origin": origin,
        "Dest": destination,
        "CRSDepTime": str(dep_local.hour * 100 + dep_local.minute),
        "CRSArrTime": str(arr_local.hour * 100 + arr_local.minute),
        # The trained encoder represents missing route distance explicitly.
        # Aviationstack's Free real-time feed does not include BTS route miles.
        "Distance": "",
        "CRSElapsedTime": str(round(elapsed)),
    }
    flight_details = flight.get("flight")
    if isinstance(flight_details, dict):
        flight_number = str(flight_details.get("iata") or flight_details.get("number") or "").strip()
        if flight_number:
            row["Flight_Number_Reporting_Airline"] = flight_number
    return row


def fetch_aviationstack_schedules(api_key: str, airports: list[str]) -> list[dict[str, str]]:
    """Fetch one airport's real-time scheduled departures on Aviationstack Free."""
    import requests

    AVIATIONSTACK_LAST_DIAGNOSTICS.clear()
    AVIATIONSTACK_LAST_DIAGNOSTICS.update(
        {"http_response_received": False, "http_status": None, "response_received_at_utc": None, "used_cached_response": False}
    )
    if len(airports) != 1:
        raise ValueError(
            "Aviationstack Free discovery uses one origin airport per request. "
            "Choose exactly one airport to protect the 100-request monthly quota."
        )
    origin = airports[0].strip().upper()
    now = utc_now()
    cache_key = f"{origin}:{hash(api_key)}"
    if AVIATIONSTACK_CACHE.get("key") == cache_key and time_module.monotonic() < AVIATIONSTACK_CACHE.get("expires_at", 0):
        cached_rows = [dict(row) for row in (AVIATIONSTACK_CACHE.get("rows") or [])]
        AVIATIONSTACK_LAST_DIAGNOSTICS.update(
            {
                "used_cached_response": True,
                "origin": origin,
                "records_returned": AVIATIONSTACK_CACHE.get("records_returned"),
                "eligible_scheduled_flights": len(cached_rows),
            }
        )
        if AVIATIONSTACK_CACHE.get("error"):
            raise RuntimeError(str(AVIATIONSTACK_CACHE["error"]))
        return cached_rows

    map_path = ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"
    if not map_path.is_file():
        raise FileNotFoundError("Project airport/timezone map is unavailable; live schedules cannot be mapped safely.")
    with map_path.open("r", newline="", encoding="utf-8-sig") as handle:
        airport_rows = list(csv.DictReader(handle))
    airport_by_iata = {
        row["airport_code"].strip().upper(): row
        for row in airport_rows
        if row.get("airport_code") and row.get("timezone")
    }
    if origin not in airport_by_iata:
        raise ValueError(f"Origin {origin} is not in the project's airport/timezone map.")

    AVIATIONSTACK_LAST_DIAGNOSTICS.clear()
    global AVIATIONSTACK_LAST_REQUEST_AT_UTC
    global AVIATIONSTACK_LAST_REQUEST_ORIGIN
    AVIATIONSTACK_LAST_REQUEST_AT_UTC = now.isoformat(timespec="seconds")
    AVIATIONSTACK_LAST_REQUEST_ORIGIN = origin
    reserve_aviationstack_request(now)
    try:
        response = requests.get(
            AVIATIONSTACK_API_URL,
            params={
                "access_key": api_key,
                "dep_iata": origin,
                "flight_status": "scheduled",
            },
            headers={"Accept": "application/json", "User-Agent": "aviation-disruption-prediction/0.1"},
            timeout=20,
        )
        AVIATIONSTACK_LAST_DIAGNOSTICS.update(
            {
                "http_response_received": True,
                "http_status": int(response.status_code),
                "response_received_at_utc": utc_text(),
            }
        )
    except requests.Timeout as exc:
        message = "Aviationstack request timed out. Try again later or use the CSV/demo fallback."
        AVIATIONSTACK_CACHE.update({"key": cache_key, "rows": [], "error": message, "expires_at": time_module.monotonic() + 15 * 60})
        raise RuntimeError(message) from None
    except requests.ConnectionError:
        message = "Could not reach Aviationstack. Check the network or use the CSV/demo fallback."
        AVIATIONSTACK_CACHE.update({"key": cache_key, "rows": [], "error": message, "expires_at": time_module.monotonic() + 15 * 60})
        raise RuntimeError(message) from None
    except requests.RequestException:
        message = "Aviationstack request failed. The credential and request URL were withheld."
        AVIATIONSTACK_CACHE.update({"key": cache_key, "rows": [], "error": message, "expires_at": time_module.monotonic() + 15 * 60})
        raise RuntimeError(message) from None

    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code >= 400 or (isinstance(payload, dict) and payload.get("error")):
        api_error = _aviationstack_api_error(response.status_code, payload, api_key, response)
        AVIATIONSTACK_CACHE.update(
            {"key": cache_key, "rows": [], "error": str(api_error), "expires_at": time_module.monotonic() + AVIATIONSTACK_CACHE_SECONDS}
        )
        raise api_error
    if not isinstance(payload, dict):
        if payload is None:
            raise RuntimeError("Aviationstack returned invalid JSON; no schedule was accepted.")
        raise RuntimeError("Aviationstack returned an unexpected response; no schedule was accepted.")

    records = payload.get("data")
    if not isinstance(records, list):
        raise RuntimeError("Aviationstack response omitted its flight data list.")
    rows = []
    seen = set()
    for flight in records:
        if not isinstance(flight, dict):
            continue
        row = normalize_aviationstack_flight(flight, airport_by_iata, now)
        if row is None:
            continue
        identity = (
            row.get("Flight_Number_Reporting_Airline", ""), row["Origin"], row["Dest"],
            row["FlightDate"], row["CRSDepTime"],
        )
        if identity in seen:
            continue
        seen.add(identity)
        rows.append(row)
    rows.sort(key=lambda row: (row["FlightDate"], int(row["CRSDepTime"]), row["Dest"]))
    AVIATIONSTACK_LAST_DIAGNOSTICS.update(
        {"origin": origin, "records_returned": len(records), "eligible_scheduled_flights": len(rows), "distance_missing": len(rows)}
    )
    AVIATIONSTACK_CACHE.update(
        {
            "key": cache_key,
            "rows": [dict(row) for row in rows],
            "records_returned": len(records),
            "error": None,
            "expires_at": time_module.monotonic() + AVIATIONSTACK_CACHE_SECONDS,
        }
    )
    return rows


SCHEDULE_ADAPTERS["aviationstack"] = fetch_aviationstack_schedules


def great_circle_miles(origin: dict[str, str], destination: dict[str, str]) -> float:
    """Approximate BTS route distance from airport coordinates in statute miles."""
    lat1, lon1 = float(origin["airport_latitude"]), float(origin["airport_longitude"])
    lat2, lon2 = float(destination["airport_latitude"]), float(destination["airport_longitude"])
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.7613 * 2 * math.asin(min(1.0, math.sqrt(hav)))


def prediction_command(input_path: Path, output_path: Path, weather_mode: str = "live") -> list[str]:
    return [
        sys.executable,
        str(ROOT / "src" / "predict_current_flights.py"),
        "--input-csv", str(input_path),
        "--history-dir", str(ROOT / "data" / "processed" / "depdel15" / "flights"),
        "--history-splits", "train", "operational_history",
        "--weather-mode", weather_mode,
        "--station-map", str(ROOT / "data" / "processed" / "depdel15" / "weather" / "station_map.csv"),
        "--model-t-minus-2h", str(ROOT / "data" / "processed" / "depdel15" / "weather_models" / "t_minus_2h" / "weather_logistic_sgd.joblib"),
        "--model-t-minus-30m", str(ROOT / "data" / "processed" / "depdel15" / "weather_models" / "t_minus_30m" / "weather_logistic_sgd.joblib"),
        "--output-csv", str(output_path),
    ]


def refresh_predictions(
    force: bool = False,
    schedule_rows: list[dict[str, Any]] | None = None,
    schedule_source: str | None = None,
    weather_mode: str = "live",
) -> dict[str, Any]:
    with REFRESH_LOCK:
        if OFFLINE_ONLY:
            # Offline review is read-only: do not fetch, score, or persist.
            return read_state()
        state = read_state()
        last_updated = state.get("updated_at_utc")
        interval = refresh_minutes() * 60
        settings_airports = read_settings().get("airports", [])
        if isinstance(settings_airports, str):
            current_origin = settings_airports.strip().upper() if "," not in settings_airports else None
        elif isinstance(settings_airports, list) and len(settings_airports) == 1:
            current_origin = str(settings_airports[0]).strip().upper()
        else:
            current_origin = None
        current_state_is_aviationstack = state.get("schedule_source") in {
            AVIATIONSTACK_SCHEDULE_SOURCE,
            "Aviationstack Free live flight feed",  # legacy persisted state label
        }
        same_cached_aviationstack_origin = (
            current_state_is_aviationstack
            and state.get("aviationstack_last_request_origin")
            and state.get("aviationstack_last_request_origin") == current_origin
        )
        if schedule_rows is None and provider_ready() and provider_name() == "aviationstack" and same_cached_aviationstack_origin and state.get("aviationstack_last_request_at_utc"):
            try:
                age = (utc_now() - datetime.fromisoformat(state["aviationstack_last_request_at_utc"])).total_seconds()
                if age < AVIATIONSTACK_CACHE_SECONDS:
                    state["status"] = "ready"
                    state["message"] = (
                        "Aviationstack was unavailable on the last request; the next retry is due in about "
                        if state.get("last_error")
                        else "The Aviationstack response is within its Free-plan cache window; the next API request is due in about "
                    ) + f"{max(1, int((AVIATIONSTACK_CACHE_SECONDS - age) / 60))} min to stay within the Free-plan quota."
                    if state.get("last_error"):
                        state["status"] = "error"
                    return state
            except ValueError:
                pass
        if schedule_rows is None and provider_ready() and provider_name() == "aviationstack" and not same_cached_aviationstack_origin:
            last_updated = None
        if not force and last_updated:
            try:
                age = (utc_now() - datetime.fromisoformat(last_updated)).total_seconds()
                if age < interval:
                    state["status"] = "ready"
                    state["message"] = f"Showing cached data; next refresh is due in about {max(1, int((interval-age)/60))} min."
                    return state
            except ValueError:
                pass

        attempting_live_provider = schedule_rows is None and provider_ready()
        if attempting_live_provider:
            AVIATIONSTACK_LAST_DIAGNOSTICS.clear()
            state.update(
                {
                    "live_refresh_attempted_at_utc": utc_text(),
                    "live_response_received": False,
                    "live_http_status": None,
                    "live_response_received_at_utc": None,
                    "live_response_cached": False,
                }
            )
            # Persist attempt metadata before making a provider request so a
            # stopped server still reports that it started, without replacing
            # the last successful schedule or predictions.
            safe_json_write(STATE_PATH, state)

        try:
            source = schedule_source or (
                AVIATIONSTACK_SCHEDULE_SOURCE
                if provider_ready() and provider_name() == "aviationstack"
                else "live schedule provider" if provider_ready() else "uploaded schedule CSV"
            )
            provider_rows = None
            if schedule_rows is not None:
                rows = schedule_rows
            elif provider_ready():
                rows = fetch_provider_schedule(normalize_airports(read_settings().get("airports", [])))
                if provider_name() == "aviationstack":
                    diagnostics = AVIATIONSTACK_LAST_DIAGNOSTICS
                    state.update(
                        {
                            "live_response_received": bool(diagnostics.get("http_response_received")),
                            "live_http_status": diagnostics.get("http_status"),
                            "live_response_received_at_utc": diagnostics.get("response_received_at_utc"),
                            "live_response_cached": bool(diagnostics.get("used_cached_response")),
                        }
                    )
                if not rows:
                    if provider_name() == "aviationstack":
                        returned = AVIATIONSTACK_LAST_DIAGNOSTICS.get("records_returned", 0)
                        raise ValueError(
                            f"Aviationstack returned {returned} record(s), but none had usable scheduled fields and "
                            "a future departure with status scheduled. "
                            "The Free plan provides real-time flights, not the paid Future Flight/schedule feed. "
                            "Use the CSV upload or offline demo if no current scheduled records are available."
                        )
                    raise ValueError("The flight provider returned no scheduled departures for this watchlist/window.")
                provider_rows = rows
            else:
                rows = read_schedule_rows()
                if not rows:
                    raise ValueError("No schedule loaded. Configure the live feed or upload a schedule CSV.")

            with tempfile.TemporaryDirectory(prefix="web-run-", dir=str(WEB_DATA)) as temp_dir:
                prediction_input = SCHEDULE_PATH
                if schedule_rows is not None or provider_rows is not None:
                    prediction_input = Path(temp_dir) / "provider_schedule.csv"
                    write_schedule(rows, prediction_input)
                output_path = Path(temp_dir) / "predictions.csv"
                run = subprocess.run(
                    prediction_command(prediction_input, output_path, weather_mode),
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if run.returncode != 0:
                    diagnostic = (run.stderr or run.stdout or "Inference command failed.").strip()
                    raise RuntimeError(diagnostic[-1800:])
                with output_path.open("r", newline="", encoding="utf-8-sig") as handle:
                    predictions = list(csv.DictReader(handle))
                summary_path = output_path.with_suffix(".summary.json")
                summary = json.loads(summary_path.read_text(encoding="utf-8"))

            # Commit new provider schedules only after inference succeeds.
            if provider_rows is not None:
                write_schedule(provider_rows)

            if schedule_source == "Bundled synthetic schedule demo":
                success_message = (
                    f"Scored {len(predictions)} bundled synthetic example flight(s); "
                    "this is not live operational data."
                )
            elif schedule_source == "uploaded schedule CSV":
                success_message = f"Scored {len(predictions)} flight(s) from the uploaded schedule CSV."
            elif provider_name() == "aviationstack" and schedule_rows is None:
                success_message = f"Updated {len(predictions)} scheduled predictions from Live Aviationstack data."
            else:
                success_message = f"Updated {len(predictions)} scheduled flight predictions."

            state.update(
                {
                    "status": "ready",
                    "message": success_message,
                    "updated_at_utc": summary.get("generated_at_utc", utc_text()),
                    "aviationstack_last_request_at_utc": AVIATIONSTACK_LAST_REQUEST_AT_UTC if provider_name() == "aviationstack" and schedule_rows is None else state.get("aviationstack_last_request_at_utc"),
                    "aviationstack_last_request_origin": AVIATIONSTACK_LAST_REQUEST_ORIGIN if provider_name() == "aviationstack" and schedule_rows is None else state.get("aviationstack_last_request_origin"),
                    "schedule_source": source,
                    "schedule_count": len(predictions),
                    "flights": predictions,
                    "weather_source": summary.get("weather_source", "AviationWeather.gov METAR"),
                    "weather_status": summary.get("weather_status", "unknown"),
                    "weather_warning": summary.get("weather_warning"),
                    "history_latest_date": summary.get("history_archive_latest_date"),
                    "history_age_days": summary.get("history_age_days_max"),
                    "history_splits_read": summary.get("history_splits_read", ["train", "operational_history"]),
                    "provider_records_returned": (
                        int(AVIATIONSTACK_LAST_DIAGNOSTICS.get("records_returned"))
                        if provider_name() == "aviationstack" and schedule_rows is None and provider_rows is not None
                        and AVIATIONSTACK_LAST_DIAGNOSTICS.get("records_returned") is not None
                        else None
                    ),
                    "provider_eligible_records": (
                        len(provider_rows)
                        if provider_name() == "aviationstack" and schedule_rows is None and provider_rows is not None
                        else None
                    ),
                    "last_error": None,
                    "provider_warning": (
                        "Aviationstack Free does not include route distance. The saved model used its explicit "
                        "missing-distance indicator; no distance was estimated or imputed."
                        + (
                            f" Excluded {max(0, int(AVIATIONSTACK_LAST_DIAGNOSTICS.get('records_returned') or 0) - len(provider_rows))} "
                            "record(s) without eligible, mapped scheduled fields."
                            if int(AVIATIONSTACK_LAST_DIAGNOSTICS.get("records_returned") or 0) > len(provider_rows)
                            else ""
                        )
                        if provider_name() == "aviationstack" and schedule_rows is None and provider_rows is not None
                        else None
                    ),
                }
            )
            safe_json_write(STATE_PATH, state)
            return state
        except Exception as exc:  # Surface actionable provider/inference failures in the UI.
            state["status"] = "error"
            if attempting_live_provider and provider_name() == "aviationstack":
                diagnostics = AVIATIONSTACK_LAST_DIAGNOSTICS
                state.update(
                    {
                        "live_response_received": bool(diagnostics.get("http_response_received")),
                        "live_http_status": diagnostics.get("http_status"),
                        "live_response_received_at_utc": diagnostics.get("response_received_at_utc"),
                        "live_response_cached": bool(diagnostics.get("used_cached_response")),
                    }
                )
            previous_source = state.get("schedule_source")
            state["message"] = (
                f"Refresh failed; still showing the last successful results from {previous_source}."
                if previous_source
                else "Refresh failed; no new results are available."
            )
            state["last_error"] = str(exc)
            if provider_name() == "aviationstack" and AVIATIONSTACK_LAST_REQUEST_AT_UTC:
                state["aviationstack_last_request_at_utc"] = AVIATIONSTACK_LAST_REQUEST_AT_UTC
                state["aviationstack_last_request_origin"] = AVIATIONSTACK_LAST_REQUEST_ORIGIN
            safe_json_write(STATE_PATH, state)
            return state


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "FlightWeatherDashboard/1.0"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > MAX_BODY_BYTES:
            raise ValueError("Request body is empty or exceeds the 1 MB limit.")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        return payload

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler interface
        route = urlparse(self.path).path
        if route == "/":
            try:
                data = HTML_PATH.read_bytes()
            except OSError:
                self.send_error(404, "Dashboard page not found.")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if route == "/api/state":
            self._send_json(200, api_state())
            return
        self.send_error(404, "Not found.")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler interface
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
            if OFFLINE_ONLY and route in {"/api/schedule", "/api/settings", "/api/refresh", "/api/demo"}:
                self._send_json(
                    409,
                    {
                        "ok": False,
                        "offline_only": True,
                        "error": "Offline-only review mode is read-only; no refresh, upload, demo scoring, or settings change was performed.",
                    },
                )
                return
            if route == "/api/schedule":
                rows = parse_csv_schedule(str(payload.get("csv", "")))
                write_schedule(rows)
                state = read_state()
                state["message"] = f"Loaded {len(rows)} schedule row(s) from CSV; scoring is in progress."
                safe_json_write(STATE_PATH, state)
                self._send_json(200, {"ok": True, "uploaded_schedule_count": len(rows), "state": api_state(state)})
                return
            if route == "/api/settings":
                airports = normalize_airports(payload.get("airports", []))
                settings = {"airports": airports}
                safe_json_write(SETTINGS_PATH, settings)
                self._send_json(200, {"ok": True, "settings": settings})
                return
            if route == "/api/refresh":
                if payload.get("schedule_source") == "uploaded schedule CSV":
                    rows = read_schedule_rows()
                    state = refresh_predictions(
                        force=True,
                        schedule_rows=rows,
                        schedule_source="uploaded schedule CSV",
                    )
                else:
                    state = refresh_predictions(force=bool(payload.get("force", False)))
                self._send_json(200 if state.get("status") != "error" else 502, api_state(state))
                return
            if route == "/api/demo":
                demo_path = ROOT / "data" / "demo" / "flight_schedule_example.csv"
                if not demo_path.is_file():
                    raise FileNotFoundError("Bundled offline demo schedule is missing.")
                demo_rows = parse_csv_schedule(demo_path.read_text(encoding="utf-8-sig"))
                state = refresh_predictions(
                    force=True,
                    schedule_rows=demo_rows,
                    schedule_source="Bundled synthetic schedule demo",
                    weather_mode="none",
                )
                self._send_json(200 if state.get("status") != "error" else 502, api_state(state))
                return
            self.send_error(404, "Not found.")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Local dashboard file operation failed: {exc}"})

    def log_message(self, fmt: str, *args: Any) -> None:
        # Keep console logs useful while never logging request bodies or secrets.
        super().log_message(fmt, *args)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Defaults to localhost; do not expose without production security.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--offline-only",
        action="store_true",
        help="Serve saved dashboard state read-only; block live provider requests and all schedule/settings writes.",
    )
    return parser.parse_args()


def _finite_number(value: Any) -> float | None:
    if value is None or str(value).strip().lower() in {"", "nan", "none", "null", "<na>"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _is_missing_feature(value: Any) -> bool:
    return value is None or str(value).strip().lower() in {"", "nan", "none", "null", "<na>"}


@lru_cache(maxsize=1)
def model_category_support() -> dict[str, dict[str, Any]]:
    """Read category vocabularies from the saved encoders; never refit them."""
    import joblib

    result: dict[str, dict[str, Any]] = {}
    for horizon in ("t_minus_2h", "t_minus_30m"):
        artifact_path = ROOT / "data" / "processed" / "depdel15" / "weather_models" / horizon / "weather_logistic_sgd.joblib"
        try:
            artifact = joblib.load(artifact_path)
            encoder = artifact["encoder"]
            categories = encoder.categories_
            feature_names = list(getattr(encoder, "feature_names_in_", ()))
            if feature_names and len(feature_names) == len(categories):
                category_by_name = dict(zip(feature_names, categories))
                airline_categories = category_by_name["Reporting_Airline"]
                destination_categories = category_by_name["Dest"]
            else:
                # This is the encoder schema in train_baseline.CATEGORICAL_FEATURES.
                airline_categories, destination_categories = categories[0], categories[2]
            result[horizon] = {
                "available": True,
                "airlines": frozenset(str(value).strip().upper() for value in airline_categories),
                "destinations": frozenset(str(value).strip().upper() for value in destination_categories),
            }
        except Exception:
            # An unavailable artifact means support cannot be verified. Keep
            # the dashboard usable and make the unknown support explicit.
            result[horizon] = {"available": False, "airlines": frozenset(), "destinations": frozenset()}
    return result


def classify_prediction_horizon(cutoff_utc: Any, now: datetime | None = None) -> str:
    """Classify a model cutoff generated from scheduled origin-local time."""
    if not isinstance(cutoff_utc, str) or not cutoff_utc.strip():
        return "unavailable"
    try:
        cutoff = datetime.fromisoformat(cutoff_utc.strip().replace("Z", "+00:00"))
    except ValueError:
        return "unavailable"
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        return "unavailable"
    current = now or utc_now()
    if current.tzinfo is None or current.utcoffset() is None:
        return "unavailable"
    return "elapsed" if cutoff.astimezone(timezone.utc) <= current.astimezone(timezone.utc) else "actionable"


def dashboard_data_quality(
    state: dict[str, Any], flights: list[dict[str, Any]], now: datetime | None = None
) -> dict[str, Any]:
    """Summarize only counts and statuses computed from the current saved rows."""
    current = now or utc_now()
    current_utc = current.astimezone(timezone.utc)
    horizons = ("t_minus_2h", "t_minus_30m")
    source = state.get("schedule_source")
    is_live_aviationstack = source in {
        AVIATIONSTACK_SCHEDULE_SOURCE,
        "Aviationstack Free live flight feed",
    }

    # The live cutoff timestamp is produced by predict_current_flights from
    # FlightDate/CRSDepTime in the origin airport's timezone. Comparing that
    # aware UTC timestamp avoids reinterpreting airport-local schedule times.
    snapshot_is_stale = bool(state.get("prediction_snapshot_stale"))
    horizon_summary: dict[str, dict[str, int]] = {}
    for horizon in horizons:
        counts = {
            "actionable": 0,
            "elapsed": 0,
            "unavailable": 0,
            "active_threshold_flags": 0,
            "stale_saved_scores": len(flights) if snapshot_is_stale else 0,
        }
        for row in flights:
            status = classify_prediction_horizon(row.get(f"prediction_cutoff_utc_{horizon}"), current_utc)
            row[f"prediction_horizon_status_{horizon}"] = status
            counts[status] += 1
            decision = _finite_number(row.get(f"predicted_delay_{horizon}"))
            if status == "actionable" and decision == 1 and not snapshot_is_stale:
                counts["active_threshold_flags"] += 1
        horizon_summary[horizon] = counts

    model_support = model_category_support()
    support_summary: dict[str, dict[str, Any]] = {}
    for horizon in horizons:
        support = model_support.get(horizon, {})
        available = bool(support.get("available"))
        airlines = support.get("airlines", frozenset())
        destinations = support.get("destinations", frozenset())
        unsupported_airlines: set[str] = set()
        unsupported_destinations: set[str] = set()
        unsupported_airline_rows = 0
        unsupported_destination_rows = 0
        for row in flights:
            airline = str(row.get("Reporting_Airline") or "").strip().upper()
            destination = str(row.get("Dest") or "").strip().upper()
            airline_out = available and (not airline or airline not in airlines)
            destination_out = available and (not destination or destination not in destinations)
            if airline_out:
                unsupported_airlines.add(airline or "(missing)")
                unsupported_airline_rows += 1
            if destination_out:
                unsupported_destinations.add(destination or "(missing)")
                unsupported_destination_rows += 1
            per_horizon = row.setdefault("model_input_support_by_horizon", {})
            per_horizon[horizon] = {
                "encoder_support_available": available,
                "unsupported_carrier_category": bool(airline_out),
                "unsupported_destination_category": bool(destination_out),
                "missing_route_distance": _is_missing_feature(row.get("Distance")),
            }
        support_summary[horizon] = {
            "encoder_support_available": available,
            "unsupported_carrier_value_count": len(unsupported_airlines) if available else None,
            "unsupported_carrier_rows": unsupported_airline_rows if available else None,
            "unsupported_destination_value_count": len(unsupported_destinations) if available else None,
            "unsupported_destination_rows": unsupported_destination_rows if available else None,
        }

    for row in flights:
        distance_missing = _is_missing_feature(row.get("Distance"))
        support_by_horizon = row.get("model_input_support_by_horizon", {})
        row["model_input_support_unverified"] = distance_missing or any(
            not support_by_horizon.get(horizon, {}).get("encoder_support_available", False)
            or support_by_horizon.get(horizon, {}).get("unsupported_carrier_category", False)
            or support_by_horizon.get(horizon, {}).get("unsupported_destination_category", False)
            for horizon in horizons
        )

    # Route/time groups can contain codeshares or multiple listings. They are
    # diagnostic groupings only: no flight rows are removed or merged here.
    route_time_counts: dict[tuple[str, str, str, str], int] = {}
    for row in flights:
        dep_time = _finite_number(row.get("CRSDepTime"))
        normalized_dep_time = f"{int(dep_time):04d}" if dep_time is not None and dep_time.is_integer() else str(row.get("CRSDepTime") or "").strip()
        key = (
            str(row.get("FlightDate") or "").strip()[:10],
            str(row.get("Origin") or "").strip().upper(),
            str(row.get("Dest") or "").strip().upper(),
            normalized_dep_time,
        )
        if all(key):
            route_time_counts[key] = route_time_counts.get(key, 0) + 1
    repeated_groups = [count for count in route_time_counts.values() if count > 1]

    missing_distance_rows = sum(_is_missing_feature(row.get("Distance")) for row in flights)
    history_age_days: list[int] = []
    history_dates: list[str] = []
    for row in flights:
        history_date = str(row.get("history_source_max_date") or "").strip()[:10]
        if history_date and history_date.lower() not in {"nan", "none", "null"}:
            try:
                datetime.strptime(history_date, "%Y-%m-%d")
                history_dates.append(history_date)
            except ValueError:
                pass
        age = _finite_number(row.get("history_age_days"))
        if age is None and history_date:
            try:
                scheduled = datetime.strptime(str(row.get("FlightDate"))[:10], "%Y-%m-%d").date()
                through = datetime.strptime(history_date, "%Y-%m-%d").date()
                age = (scheduled - through).days
            except (TypeError, ValueError):
                pass
        if age is not None:
            history_age_days.append(int(age))

    history_missing_rows: dict[str, int | None] = {}
    history_unknown_rows: dict[str, int] = {}
    for window, key in (("7d", "prev7d_history_missing"), ("30d", "prev30d_history_missing"), ("3m", "prev3m_history_missing")):
        values = [_finite_number(row.get(key)) for row in flights]
        history_missing_rows[window] = sum(value == 1 for value in values) if any(value is not None for value in values) else None
        history_unknown_rows[window] = sum(value is None for value in values)

    weather_summary: dict[str, dict[str, int]] = {}
    for horizon in horizons:
        weather_counts = {"origin_available": 0, "destination_available": 0, "unavailable_endpoints": 0, "unknown_endpoints": 0}
        for row in flights:
            for endpoint in ("origin", "destination"):
                missing = _finite_number(row.get(f"{endpoint}_weather_missing_{horizon}"))
                if missing == 0:
                    weather_counts[f"{endpoint}_available"] += 1
                elif missing == 1:
                    weather_counts["unavailable_endpoints"] += 1
                else:
                    weather_counts["unknown_endpoints"] += 1
        weather_summary[horizon] = weather_counts

    raw_provider_records = None
    eligible_provider_records = None
    if is_live_aviationstack:
        explicit_raw = _finite_number(state.get("provider_records_returned"))
        explicit_eligible = _finite_number(state.get("provider_eligible_records"))
        excluded_match = re.search(r"Excluded\s+(\d+)\s+record\(s\)", str(state.get("provider_warning") or ""), re.IGNORECASE)
        if explicit_eligible is not None:
            eligible_provider_records = int(explicit_eligible)
        elif flights:
            eligible_provider_records = len(flights)
        if explicit_raw is not None:
            raw_provider_records = int(explicit_raw)
        elif excluded_match and eligible_provider_records is not None:
            # Compatibility with the already-saved live state created before
            # explicit raw-count metadata was added.
            raw_provider_records = eligible_provider_records + int(excluded_match.group(1))

    return {
        "schedule_source": source,
        "is_live_aviationstack": is_live_aviationstack,
        "raw_provider_records": raw_provider_records,
        "eligible_provider_records": eligible_provider_records,
        "eligible_scored_records": len(flights),
        "potential_duplicate_route_time_groups": len(repeated_groups),
        "records_in_repeated_route_time_groups": sum(repeated_groups),
        "additional_records_in_repeated_route_time_groups": sum(count - 1 for count in repeated_groups),
        "duplicate_grouping_fields": ["FlightDate", "Origin", "Dest", "CRSDepTime"],
        "duplicate_grouping_note": "Route/time grouping can include codeshares or multiple listings and does not establish unique physical flights. No rows were deduplicated.",
        "missing_route_distance_rows": missing_distance_rows,
        "total_flights": len(flights),
        "model_input_support": support_summary,
        "model_input_support_note": "Saved scores are retained for traceability. Reliability and calibration on live-feed inputs have not been verified; unknown categories and missing distance are outside observed training support.",
        "history": {
            "latest_history_date": max(history_dates) if history_dates else state.get("history_latest_date"),
            "age_days_min": min(history_age_days) if history_age_days else None,
            "age_days_max": max(history_age_days) if history_age_days else _finite_number(state.get("history_age_days")),
            "unavailable_feature_rows": history_missing_rows,
            "availability_unknown_rows": history_unknown_rows,
        },
        "weather": weather_summary,
        "horizon_status_as_of_utc": current_utc.isoformat(timespec="seconds"),
        "horizon_status_timezone_basis": "Cutoffs were generated from scheduled departure in the origin airport timezone and are compared as UTC timestamps.",
        "prediction_snapshot_status": state.get("prediction_snapshot_status", "unknown"),
        "prediction_snapshot_stale": snapshot_is_stale,
        "horizon_status": horizon_summary,
    }


def prediction_snapshot_freshness(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Describe saved-score age without changing or recomputing the scores."""
    current = now or utc_now()
    snapshot_text = state.get("updated_at_utc")
    snapshot_at: datetime | None = None
    if isinstance(snapshot_text, str) and snapshot_text.strip():
        try:
            parsed = datetime.fromisoformat(snapshot_text.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                snapshot_at = parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    max_age_seconds = AVIATIONSTACK_CACHE_SECONDS
    if snapshot_at is None:
        age_seconds = None
        status = "unavailable"
    else:
        age = (current.astimezone(timezone.utc) - snapshot_at).total_seconds()
        age_seconds = max(0, int(age))
        if age < -60:
            status = "clock_skew"
        elif age > max_age_seconds:
            status = "stale"
        else:
            status = "current"
    has_scores = bool(state.get("flights"))
    return {
        "prediction_snapshot_at_utc": snapshot_text,
        "prediction_snapshot_age_seconds": age_seconds,
        "prediction_snapshot_stale_after_seconds": max_age_seconds,
        "prediction_snapshot_status": status,
        # If a populated saved state has no valid timestamp, freshness cannot
        # be established; keep its scores visible only for traceability.
        "prediction_snapshot_stale": status != "current" and has_scores,
    }


def api_state(state_override: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the public browser state without exposing environment values."""
    state = dict(state_override) if state_override is not None else read_state()
    now = utc_now()
    flights = [dict(row) for row in state.get("flights", []) if isinstance(row, dict)]
    state["flights"] = flights
    state["dashboard_now_utc"] = now.isoformat(timespec="seconds")
    state.update(prediction_snapshot_freshness(state, now))
    state["data_quality"] = dashboard_data_quality(state, flights, now)
    state["offline_only"] = OFFLINE_ONLY
    if OFFLINE_ONLY:
        state["message"] = (
            "Offline-only review mode: displaying saved dashboard results. "
            "Provider refresh, demo scoring, schedule upload, and settings changes are disabled."
        )
    state["settings"] = read_settings()
    state["provider"] = provider_name() or None
    state["provider_key_configured"] = api_key_configured()
    state["provider_ready"] = provider_ready()
    state["provider_adapter_pending"] = bool(provider_name()) and provider_name() not in SCHEDULE_ADAPTERS
    state["provider_diagnostics"] = {
        "provider_configured": provider_name() in SCHEDULE_ADAPTERS,
        "api_key_present": api_key_configured(),
        "live_refresh_attempted": bool(state.get("live_refresh_attempted_at_utc")),
        "live_refresh_attempted_at_utc": state.get("live_refresh_attempted_at_utc"),
        "live_response_received": bool(state.get("live_response_received")),
        "live_http_status": state.get("live_http_status"),
        "live_response_received_at_utc": state.get("live_response_received_at_utc"),
        "live_response_cached": bool(state.get("live_response_cached")),
        "displayed_data_source": state.get("schedule_source"),
    }
    state["refresh_minutes"] = refresh_minutes()
    state["evaluation"] = evaluation_summary()
    return state


def evaluation_summary() -> dict[str, Any]:
    """Expose only existing held-out validation summaries, never refit metrics."""
    history_only: dict[str, Any] = {"available": False}
    history_path = ROOT / "data" / "processed" / "depdel15" / "models" / "historical_expanded" / "metrics.json"
    try:
        history_metrics = json.loads(history_path.read_text(encoding="utf-8"))
        history_temporal = history_metrics["temporal_calibration_check"]
        history_model = history_temporal["evaluation_models"]["logistic_platt_calibrated_at_calibration_f1_threshold"]
        history_only = {
            "available": True,
            "rows": history_model.get("rows"),
            "period": history_temporal.get("evaluation_period"),
            "threshold": history_model.get("threshold"),
            "precision": history_model.get("precision"),
            "recall": history_model.get("recall"),
            "f1": history_model.get("f1"),
            "pr_auc": history_model.get("average_precision"),
            "roc_auc": history_model.get("roc_auc"),
            "brier_score": history_model.get("brier_score"),
            "confusion_matrix": history_model.get("confusion_matrix"),
            "test_2025_read_by_this_model": history_metrics.get("training", {}).get("test_2025_read"),
        }
    except (OSError, ValueError, KeyError, TypeError):
        pass
    horizons = {}
    for horizon in ("t_minus_2h", "t_minus_30m"):
        metrics_path = ROOT / "data" / "processed" / "depdel15" / "weather_models" / horizon / "metrics.json"
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            temporal = metrics["temporal_calibration_check"]
            models = temporal["evaluation_models"]
            selected = models["logistic_platt_calibrated_at_calibration_f1_threshold"]
            baseline = models["train_prior_baseline"]
            horizons[horizon] = {
                "rows": selected.get("rows"),
                "period": temporal.get("evaluation_period"),
                "threshold": selected.get("threshold"),
                "precision": selected.get("precision"),
                "recall": selected.get("recall"),
                "f1": selected.get("f1"),
                "pr_auc": selected.get("average_precision"),
                "roc_auc": selected.get("roc_auc"),
                "brier_score": selected.get("brier_score"),
                "confusion_matrix": selected.get("confusion_matrix"),
                "calibration": temporal.get("method"),
                "test_2025_read_by_this_model": metrics.get("training", {}).get("test_2025_read"),
                "prior_baseline": {
                    "rows": baseline.get("rows"),
                    "threshold": baseline.get("threshold"),
                    "precision": baseline.get("precision"),
                    "recall": baseline.get("recall"),
                    "f1": baseline.get("f1"),
                    "pr_auc": baseline.get("average_precision"),
                    "roc_auc": baseline.get("roc_auc"),
                    "brier_score": baseline.get("brier_score"),
                    "confusion_matrix": baseline.get("confusion_matrix"),
                },
            }
        except (OSError, ValueError, KeyError, TypeError):
            horizons[horizon] = {"available": False}
    return {
        "target": "BTS DepDel15 (departure delay ≥15 minutes)",
        "data_sources": ["BTS monthly On-Time Performance", "NOAA/NCEI GHCNh FM15 METAR observations"],
        "history_only": history_only,
        "split": "Train 2022–2023; validation 2024; 2025 weather-model test not read",
        "evaluation": "2024-07 through 2024-12 after Platt calibration and threshold selection on 2024-01 through 2024-06",
        "horizons": horizons,
        "limitations": [
            "Weather inputs are decoded METAR observations available by the scheduled cutoff, not archived forecasts.",
            "The selected F1 thresholds were tuned on the first half of 2024.",
            "2025 had already been opened for an earlier frozen baseline comparison, so it is not an untouched project-wide final test.",
        ],
    }


def main() -> int:
    load_dotenv()
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    args = parse_args()
    global OFFLINE_ONLY
    OFFLINE_ONLY = bool(args.offline_only)
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Flight / Weather dashboard: http://{args.host}:{args.port}")
    print(f"Mode: {'offline-only read-only review' if OFFLINE_ONLY else 'normal'}")
    print(f"Schedule provider: {provider_name() or 'not configured'}")
    print(f"Refresh interval: {refresh_minutes()} minutes")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
