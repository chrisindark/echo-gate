"""Shared time-range resolution for analytics-style read endpoints."""

from datetime import UTC, datetime, timedelta

TIME_RANGE_MAP: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "1d": timedelta(days=1),
    "24h": timedelta(days=1),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
    "30d": timedelta(days=30),
}


def resolve_time_range(
    time_range: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> tuple[datetime, datetime, str | None]:
    """Resolve ``time_range`` / explicit bounds to a concrete UTC window.

    Returns ``(start_time, end_time, resolved_range_label)``. Defaults to the
    last 7 days when nothing usable is supplied, matching the analytics
    dashboard.
    """
    if not end_time:
        end_time = datetime.now(UTC)
    elif end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=UTC)

    resolved_range = time_range.lower() if time_range else None
    if resolved_range in TIME_RANGE_MAP:
        start_time = end_time - TIME_RANGE_MAP[resolved_range]
    elif not start_time:
        start_time = end_time - timedelta(days=7)
        resolved_range = "7d"
    elif start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)

    return start_time, end_time, resolved_range
