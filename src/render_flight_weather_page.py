"""Render current flight-weather predictions as a self-contained HTML page."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from html import escape
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HORIZONS = ("t_minus_2h", "t_minus_30m")
WEATHER_FIELDS = (
    ("temperature_c", "Temperature", "°C"),
    ("dew_point_temperature_c", "Dew point", "°C"),
    ("wind_speed_m_s", "Wind speed", "m/s"),
    ("wind_gust_m_s", "Wind gust", "m/s"),
    ("wind_direction_deg", "Wind direction", "°"),
    ("visibility_km", "Visibility", "km"),
    ("sea_level_pressure_hpa", "Sea-level pressure", "hPa"),
)
HISTORY_FIELDS = (
    ("prev3m_airline_delay_rate", "Airline · prior 3 months"),
    ("prev3m_route_delay_rate", "Route · prior 3 months"),
    ("prev7d_airline_delay_rate", "Airline · prior 7 days"),
    ("prev7d_route_delay_rate", "Route · prior 7 days"),
    ("prev30d_airline_delay_rate", "Airline · prior 30 days"),
    ("prev30d_route_delay_rate", "Route · prior 30 days"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-csv", type=Path, required=True, help="CSV written by predict_current_flights.py")
    parser.add_argument("--output-html", type=Path, required=True)
    parser.add_argument(
        "--context-note",
        help="Optional visible note describing the provenance or limits of this prediction page",
    )
    return parser.parse_args()


def value(row: dict[str, str], key: str) -> str | None:
    raw = row.get(key)
    if raw is None or not raw.strip() or raw.strip().lower() in {"nan", "none", "null"}:
        return None
    return raw.strip()


def number(row: dict[str, str], key: str) -> float | None:
    raw = value(row, key)
    if raw is None:
        return None
    try:
        result = float(raw)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def display(row: dict[str, str], key: str, digits: int = 1) -> str:
    result = number(row, key)
    return "Unavailable" if result is None else f"{result:.{digits}f}"


def format_hhmm(raw: str | None) -> str:
    if raw is None:
        return "—"
    try:
        numeric = float(raw)
    except ValueError:
        return raw
    if not numeric.is_integer() or not 0 <= numeric <= 2400:
        return raw
    hhmm = int(numeric)
    hour, minute = divmod(hhmm, 100)
    if minute >= 60 or hour > 24 or (hour == 24 and minute != 0):
        return raw
    if hour == 24:
        return "00:00 (+1 day)"
    return f"{hour:02d}:{minute:02d}"


def probability(row: dict[str, str], horizon: str) -> float | None:
    raw = number(row, f"predicted_delay_probability_{horizon}")
    if raw is None:
        return None
    return min(max(raw, 0.0), 1.0)


def weather_block(row: dict[str, str], endpoint: str, horizon: str) -> str:
    missing = number(row, f"{endpoint}_weather_missing_{horizon}")
    age = number(row, f"{endpoint}_weather_age_minutes_{horizon}")
    available = value(row, f"{endpoint}_weather_available_at_utc_{horizon}")
    if missing is None or missing >= 1:
        status = "No fresh METAR available by cutoff"
    else:
        status = f"Report age: {age:.0f} min" if age is not None else "Fresh METAR available"

    lines = [f"<p class=\"weather-status\">{escape(status)}</p>"]
    if available:
        lines.append(f"<p class=\"muted\">Available at {escape(available)} UTC</p>")
    values = []
    for field, label, unit in WEATHER_FIELDS:
        field_value = display(row, f"{endpoint}_{field}_{horizon}")
        if field_value != "Unavailable":
            values.append(f"<li><span>{escape(label)}</span><strong>{escape(field_value)} {escape(unit)}</strong></li>")
    for field, label in (
        ("weather_rain", "Rain"),
        ("weather_snow", "Snow / ice"),
        ("weather_fog_or_mist", "Fog / mist"),
        ("weather_thunderstorm", "Thunderstorm"),
    ):
        flag = number(row, f"{endpoint}_{field}_{horizon}")
        if flag is not None:
            values.append(f"<li><span>{escape(label)}</span><strong>{'Yes' if flag >= 1 else 'No'}</strong></li>")
    if values:
        lines.append("<ul class=\"facts\">" + "".join(values) + "</ul>")
    return "".join(lines)


def flight_card(row: dict[str, str], index: int) -> str:
    date_value = value(row, "FlightDate") or "Date unavailable"
    date_value = date_value[:10]
    airline = value(row, "Reporting_Airline") or "Airline unavailable"
    flight_number = value(row, "Flight_Number_Reporting_Airline")
    identity = f"{airline} {flight_number}" if flight_number else airline
    origin = value(row, "Origin") or "???"
    destination = value(row, "Dest") or "???"
    departure = format_hhmm(value(row, "CRSDepTime"))
    arrival = format_hhmm(value(row, "CRSArrTime"))
    history_date = (value(row, "history_source_max_date") or "Unavailable")[:10]
    history_age = display(row, "history_age_days", 0)
    history_rows = []
    for field, label in HISTORY_FIELDS:
        rate = number(row, field)
        shown = "Unavailable" if rate is None else f"{rate:.1%}"
        history_rows.append(f"<li><span>{escape(label)}</span><strong>{shown}</strong></li>")

    horizon_cards = []
    for horizon, label in (("t_minus_2h", "T−2h"), ("t_minus_30m", "T−30m")):
        score = probability(row, horizon)
        threshold = number(row, f"decision_threshold_{horizon}")
        predicted = number(row, f"predicted_delay_{horizon}")
        cutoff = value(row, f"prediction_cutoff_utc_{horizon}")
        score_text = "Unavailable" if score is None else f"{score:.1%}"
        threshold_text = "Unavailable" if threshold is None else f"{threshold:.1%}"
        decision = "Unavailable" if predicted is None else ("15+ min delay risk" if predicted >= 1 else "Below model threshold")
        horizon_cards.append(
            f"<section class=\"horizon\"><h3>{label}</h3>"
            f"<p class=\"probability\">{score_text}</p><p>Probability of departure delay ≥15 min</p>"
            f"<p><strong>Decision:</strong> {escape(decision)}</p>"
            f"<p><strong>Calibration threshold:</strong> {threshold_text}</p>"
            f"<p class=\"muted\">Cutoff: {escape(cutoff or 'Unavailable')} UTC</p>"
            f"<div class=\"weather-grid\"><div><h4>Origin · {escape(origin)}</h4>{weather_block(row, 'origin', horizon)}</div>"
            f"<div><h4>Destination · {escape(destination)}</h4>{weather_block(row, 'destination', horizon)}</div></div></section>"
        )

    return (
        f"<article class=\"flight-card\"><header><div><p class=\"eyebrow\">Flight {index}</p>"
        f"<h2>{escape(identity)} · {escape(origin)} → {escape(destination)}</h2>"
        f"<p>{escape(date_value)} · scheduled local times {escape(departure)} → {escape(arrival)}</p></div></header>"
        f"<div class=\"horizon-grid\">{''.join(horizon_cards)}</div>"
        f"<section class=\"context\"><h3>Recent flight history</h3>"
        f"<p>Newest local BTS outcome: {escape(history_date)} · history age: {escape(history_age)} days</p>"
        f"<ul class=\"facts history-facts\">{''.join(history_rows)}</ul></section></article>"
    )


def main() -> int:
    args = parse_args()
    if not args.input_csv.is_file():
        raise FileNotFoundError(f"Prediction CSV does not exist: {args.input_csv}")
    if args.output_html.exists():
        raise FileExistsError(f"Refusing to overwrite existing HTML output: {args.output_html}")
    with args.input_csv.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError("Prediction CSV contains no flights.")

    cards = "".join(flight_card(row, index) for index, row in enumerate(rows, start=1))
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    context_note = (
        f'<section class="notice"><strong>Run context.</strong> {escape(args.context_note)}</section>'
        if args.context_note
        else ""
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flight and weather risk</title>
<style>
:root {{ color-scheme: light; font-family: Inter, Segoe UI, Arial, sans-serif; color: #1c2b36; background: #f4f7f9; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 28px 18px 48px; }}
main {{ max-width: 1120px; margin: 0 auto; }}
h1, h2, h3, h4, p {{ margin-top: 0; }}
h1 {{ margin-bottom: 8px; font-size: clamp(1.6rem, 3vw, 2.2rem); }}
h2 {{ margin-bottom: 6px; font-size: 1.2rem; }}
h3 {{ margin-bottom: 12px; font-size: 1rem; }}
h4 {{ margin-bottom: 8px; font-size: .95rem; }}
.muted, .eyebrow {{ color: #596b78; }}
.eyebrow {{ margin-bottom: 6px; font-size: .76rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
.intro {{ margin-bottom: 24px; }}
.flight-card {{ margin: 18px 0; padding: 22px; border: 1px solid #d9e1e6; border-radius: 16px; background: #fff; box-shadow: 0 6px 18px #1832470c; }}
.flight-card header {{ display: flex; justify-content: space-between; align-items: start; gap: 16px; margin-bottom: 18px; }}
.horizon-grid, .weather-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
.horizon {{ padding: 16px; border-radius: 12px; background: #f6f8fa; }}
.probability {{ margin: 2px 0; font-size: 2rem; font-weight: 750; color: #1d6373; }}
.horizon p {{ margin-bottom: 7px; }}
.weather-grid {{ margin-top: 14px; gap: 12px; }}
.weather-grid > div {{ min-width: 0; padding-top: 10px; border-top: 1px solid #d9e1e6; }}
.weather-status {{ font-weight: 650; }}
.facts {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px 16px; margin: 10px 0 0; padding: 0; list-style: none; }}
.facts li {{ display: flex; justify-content: space-between; gap: 8px; font-size: .86rem; }}
.facts li span {{ color: #596b78; }}
.facts strong {{ text-align: right; }}
.context {{ margin-top: 18px; padding-top: 16px; border-top: 1px solid #d9e1e6; }}
.history-facts {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
.notice {{ margin-top: 16px; padding: 14px 16px; border-left: 4px solid #7392a2; background: #edf3f5; line-height: 1.5; }}
footer {{ margin-top: 22px; color: #596b78; font-size: .84rem; }}
@media (max-width: 760px) {{ body {{ padding: 20px 12px 32px; }} .flight-card {{ padding: 16px; }} .horizon-grid {{ grid-template-columns: 1fr; }} .history-facts {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
@media (max-width: 440px) {{ .weather-grid, .facts, .history-facts {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body><main>
<section class="intro"><p class="eyebrow">Flight / Weather ML</p><h1>Departure delay risk</h1>
<p>Generated {escape(generated)} · {len(rows)} scheduled flight(s)</p></section>
{context_note}
{cards}
<section class="notice"><strong>How to read this page.</strong> The model estimates the probability of a departure delay of at least 15 minutes at each horizon. Expected delay minutes are not estimated by this binary classifier. Weather and history are shown as model context, not as causal explanations. The models use schedule, historical delay rates, and point-in-time observed METARs; destination METAR is not an arrival forecast.</section>
<footer>Historical rates exclude the scheduled flight date. The page shows the newest local BTS label date so reporting lag is visible.</footer>
</main></body></html>
"""
    args.output_html.parent.mkdir(parents=True, exist_ok=True)
    args.output_html.write_text(document, encoding="utf-8")
    print(f"Rendered {len(rows):,} flight page(s): {args.output_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
