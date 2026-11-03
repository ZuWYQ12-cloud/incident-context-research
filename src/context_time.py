"""Timestamp audit using integer nanoseconds and explicit precision."""

import re
from datetime import datetime, timedelta, timezone

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
NS = 1_000_000_000

PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(\d{1,9}))?"
    r"(Z|[+-]\d{2}:\d{2})"
)


def timestamp_interval(value):
    """Return [start, end) assuming fractional digits represent truncation."""
    if not isinstance(value, str):
        raise ValueError("Timestamp must be a string")

    match = PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Expected ISO timestamp with explicit timezone")

    base, fraction, zone = match.groups()
    fraction = fraction or ""
    moment = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")

    if zone == "Z":
        tz = timezone.utc
    else:
        hours = int(zone[1:3])
        minutes = int(zone[4:6])
        if hours > 23 or minutes > 59:
            raise ValueError("Invalid timezone offset")
        offset = timedelta(hours=hours, minutes=minutes)
        if zone[0] == "-":
            offset = -offset
        tz = timezone(offset)

    utc = moment.replace(tzinfo=tz).astimezone(timezone.utc)
    delta = utc - EPOCH
    seconds = delta.days * 86400 + delta.seconds
    fractional_ns = int(fraction.ljust(9, "0")) if fraction else 0
    start = seconds * NS + fractional_ns
    resolution = 10 ** (9 - len(fraction))
    return start, start + resolution


def classify(value, cutoff):
    """Cutoff denotes an exact, exclusive boundary by convention."""
    boundary, _ = timestamp_interval(cutoff)

    if value is None:
        return "missing"

    try:
        start, end = timestamp_interval(value)
    except (ValueError, TypeError, OverflowError):
        return "invalid"

    if end <= boundary:
        return "before"
    if start >= boundary:
        return "at_or_after"
    return "overlaps"


def audit_observation(event_time, collected_at, cutoff):
    event_status = classify(event_time, cutoff)
    collection_status = classify(collected_at, cutoff)

    return {
        "event_time_status": event_status,
        "collection_time_status": collection_status,
        "strict_eligible": (
            event_status == "before"
            and collection_status == "before"
        ),
        "time_model": "truncated_timestamp_intervals",
        "clock_synchronization_verified": False,
    }
