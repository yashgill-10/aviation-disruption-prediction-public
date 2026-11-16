"""Make one sanitized Aviationstack API smoke request from the ignored .env."""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import flight_weather_web as web  # noqa: E402


def main() -> int:
    web.load_dotenv()
    print(f"FLIGHT_API_KEY present: {'yes' if web.api_key_configured() else 'no'}")
    if web.provider_name() != "aviationstack":
        print("No request sent: set FLIGHT_PROVIDER=aviationstack in the ignored .env file.")
        return 2
    if not web.api_key_configured():
        print("No request sent: configure FLIGHT_API_KEY using src/configure_flight_api_key.ps1.")
        return 2
    airports = web.normalize_airports(os.environ.get("FLIGHT_AIRPORTS", "ATL"))
    if len(airports) != 1:
        print("No request sent: Aviationstack Free smoke checks use exactly one origin airport.")
        return 2

    print("Endpoint: HTTPS api.aviationstack.com, path /v1/flights")
    print(f"Authentication: access_key query parameter (value withheld); filters: dep_iata={airports[0]}, flight_status=scheduled")
    # The adapter sanitizes provider errors before they reach this diagnostic.
    try:
        rows = web.fetch_aviationstack_schedules(os.environ["FLIGHT_API_KEY"].strip(), airports)
    except web.AviationstackAPIError as exc:
        print("Aviationstack returned an API error; no retry was made.")
        print(f"HTTP status: {exc.status_code}")
        print(f"API error code: {exc.error_code or 'not provided'}")
        print(f"API error type: {exc.error_type or 'not provided'}")
        print(f"Classification: {exc.category}")
        print(f"Content-Type: {exc.content_type or 'not provided'}")
        print(f"Server header: {exc.server_header or 'not provided'}")
        print(f"Sanitized provider message: {exc.provider_message}")
        print(f"Sanitized response-body summary: {exc.body_summary or 'not provided'}")
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Aviationstack request failed: {exc}")
        return 1

    diagnostics = web.AVIATIONSTACK_LAST_DIAGNOSTICS
    print("Aviationstack returned a valid response for the documented real-time flights endpoint.")
    print(f"Origin: {airports[0]}; records returned: {diagnostics.get('records_returned', 0)}")
    print(f"Eligible future scheduled flights: {len(rows)}")
    print("API request: one HTTPS GET; key withheld; filter: flight_status=scheduled")
    print("Route distance: not supplied by the Free flight feed; model input remains explicitly missing.")
    if len(rows) == 0:
        print("No upcoming scheduled record was available to score in this response.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
