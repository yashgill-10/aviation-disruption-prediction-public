"""Versioned canonical identity for BTS flight records."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import datetime as dt
import hashlib
import json
import re

VERSION = "bts-flight-v1"
CANONICAL_ID_RE = re.compile(rf"^{VERSION}:[0-9a-f]{{64}}$")
ID_COLS = [
    "FlightDate",
    "DOT_ID_Reporting_Airline",
    "Reporting_Airline",
    "Flight_Number_Reporting_Airline",
    "OriginAirportID",
    "Origin",
    "DestAirportID",
    "Dest",
    "CRSDepTime",
]


def missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if value.__class__.__name__ == "NAType":
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def num(value: object) -> Decimal | None:
    if missing(value):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def inttok(value: object) -> str:
    number = num(value)
    if number is None or number != number.to_integral_value():
        raise ValueError("missing/nonintegral numeric identity field")
    return str(int(number))


def dateval(value: object) -> str:
    if missing(value):
        raise ValueError("missing flight date")
    return dt.date.fromisoformat(str(value).strip()[:10]).isoformat()


def timeval(value: object) -> str:
    number = int(inttok(value))
    if number == 2400:
        return "2400"
    if number < 0 or number > 2359:
        raise ValueError("scheduled departure outside HHMM range")
    hour, minute = divmod(number, 100)
    if hour > 23 or minute > 59:
        raise ValueError("invalid scheduled departure HHMM")
    return f"{hour:02d}{minute:02d}"


def code(value: object) -> str:
    return "" if missing(value) else str(value).strip().upper()


def ident(row: dict[str, object]) -> list[str]:
    identity = [
        dateval(row.get("FlightDate")),
        inttok(row.get("DOT_ID_Reporting_Airline")),
        code(row.get("Reporting_Airline")),
        inttok(row.get("Flight_Number_Reporting_Airline")),
        inttok(row.get("OriginAirportID")),
        code(row.get("Origin")),
        inttok(row.get("DestAirportID")),
        code(row.get("Dest")),
        timeval(row.get("CRSDepTime")),
    ]
    if not identity[2] or not identity[5] or not identity[7]:
        raise ValueError("missing carrier/airport code")
    return identity


def fid(identity: list[str]) -> str:
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{VERSION}:{hashlib.sha256(payload).hexdigest()}"


def flight_id_for_row(row: dict[str, object]) -> str:
    """Return the canonical ID or raise when a required identity value is invalid."""
    return fid(ident(row))


def is_canonical_flight_id(value: object) -> bool:
    """True only for ``bts-flight-v1:<64 lowercase hex>`` string IDs."""
    return isinstance(value, str) and CANONICAL_ID_RE.fullmatch(value) is not None
