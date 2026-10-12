"""Next-run helpers for durable schedules (interval + 5-field cron).

Pure datetime math — no free-text scraping of logs. Cron fields:
minute hour day-of-month month day-of-week (0=Sun or 7=Sun).
Supports ``*``, ``N``, ``A,B``, ``A-B``, ``*/N``, ``A-B/N``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def aware_utc(dt: datetime | None = None) -> datetime:
    """Return an aware UTC datetime (``dt`` or now)."""
    now = dt or datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _parse_field(raw: str, lo: int, hi: int) -> set[int]:
    text = (raw or "*").strip()
    if text == "*":
        return set(range(lo, hi + 1))
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        body = part
        if "/" in part:
            body, step_s = part.split("/", 1)
            step = max(1, int(step_s))
        if body == "*":
            out.update(range(lo, hi + 1, step))
            continue
        if "-" in body:
            a_s, b_s = body.split("-", 1)
            a, b = int(a_s), int(b_s)
            out.update(range(a, b + 1, step))
        else:
            val = int(body)
            if step > 1:
                out.update(range(val, hi + 1, step))
            else:
                out.add(val)
    return {n for n in out if lo <= n <= hi}


def parse_cron_five(expr: str) -> tuple[set[int], set[int], set[int], set[int], set[int]]:
    parts = (expr or "").strip().split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 fields: minute hour day month weekday")
    minutes = _parse_field(parts[0], 0, 59)
    hours = _parse_field(parts[1], 0, 23)
    days = _parse_field(parts[2], 1, 31)
    months = _parse_field(parts[3], 1, 12)
    weekdays = _parse_field(parts[4], 0, 7)
    if 7 in weekdays:
        weekdays.add(0)
    return minutes, hours, days, months, weekdays


def next_cron_after(expr: str, after: datetime, *, max_days: int = 366) -> datetime:
    """Return the next UTC-aware instant matching ``expr`` strictly after ``after``."""
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    minutes, hours, days, months, weekdays = parse_cron_five(expr)
    cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=max_days)
    while cursor <= limit:
        # cron weekday: 0/7=Sun … 6=Sat; Python weekday: Mon=0 … Sun=6
        py_to_cron = (cursor.weekday() + 1) % 7
        if (
            cursor.month in months
            and cursor.day in days
            and cursor.hour in hours
            and cursor.minute in minutes
            and py_to_cron in weekdays
        ):
            return cursor
        cursor += timedelta(minutes=1)
    raise ValueError(f"no cron match within {max_days} days for {expr!r}")


def next_interval_after(
    interval_seconds: int, after: datetime, *, anchor: datetime | None = None
) -> datetime:
    sec = max(1, int(interval_seconds or 0))
    if after.tzinfo is None:
        after = after.replace(tzinfo=timezone.utc)
    base = anchor or after
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    # Next boundary strictly after ``after``.
    elapsed = max(0, int((after - base).total_seconds()))
    steps = (elapsed // sec) + 1
    return base + timedelta(seconds=steps * sec)


def compute_next_run(
    *,
    kind: str,
    cron: str = "",
    interval_seconds: int = 0,
    after: datetime | None = None,
    anchor: datetime | None = None,
) -> datetime | None:
    """Compute next_run_at for a schedule kind. ``once`` returns None after first fire."""
    now = aware_utc(after)
    k = (kind or "").strip().lower()
    if k == "once":
        return now if anchor is None else None
    if k == "interval":
        if interval_seconds <= 0:
            raise ValueError("interval_seconds required for interval schedules")
        return next_interval_after(interval_seconds, now, anchor=aware_utc(anchor) if anchor else now)
    if k == "cron":
        return next_cron_after(cron, now)
    raise ValueError(f"unknown schedule kind {kind!r}")
