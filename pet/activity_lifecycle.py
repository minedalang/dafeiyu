"""Bounded long-lived activity policies (no Qt, media decoding or model calls).

The model selects an activity and optionally a duration. The local controller
enforces conservative limits so a mistaken response cannot block the pet for
hours or replay an unsupported animation indefinitely.
"""

from __future__ import annotations

from datetime import datetime, timedelta


# Minimum/maximum/ordinary duration in minutes. Only pre-existing animations
# are ever played; these names are semantic activities, not generated assets.
ACTIVITY_MINUTES = {
    "breakfast": (2, 5, 3),
    "lunch": (2, 5, 3),
    "dinner": (2, 5, 4),
    "nap": (15, 30, 20),
    "work": (5, 15, 8),
    "games": (3, 10, 5),
    "music": (3, 10, 5),
    "rest": (3, 10, 5),
    "play": (3, 10, 5),
}

SLEEP_ANIMATION = "原地小憩沉眠"
WAKE_ANIMATIONS = ("打瞌睡被惊醒", "超大伸懒腰", "哈欠连天")


def is_night(now: datetime | None = None) -> bool:
    local = now or datetime.now().astimezone()
    return local.hour >= 22 or local.hour < 7


def sleep_pressure(now: datetime | None = None) -> float:
    """A stable night-time tendency, not a random probability per animation."""
    local = now or datetime.now().astimezone()
    hour = local.hour + local.minute / 60
    if hour >= 23 or hour < 6.5:
        return 1.0
    if hour >= 22 or hour < 7:
        return 0.9
    if hour >= 21 or hour < 8:
        return 0.6
    if 13 <= hour < 14.5:
        return 0.4
    return 0.1


def duration_seconds(activity: str, proposed_minutes=None, *,
                     now: datetime | None = None) -> int:
    """Only model-proposed values within activity-specific limits are used."""
    values = ACTIVITY_MINUTES.get(activity)
    if values is None:
        return 0
    low, high, ordinary = values
    if activity == "nap" and is_night(now):
        # Real overnight sleep: aim for a normal 07:00 waking boundary.
        # Starting at 23:00 -> eight hours; 05:30 -> 1.5h remaining.
        local = now or datetime.now().astimezone()
        wake = local.replace(hour=7, minute=0, second=0, microsecond=0)
        if wake <= local:
            wake += timedelta(days=1)
        return int(max(60, min(9 * 3600, (wake - local).total_seconds())))
    if isinstance(proposed_minutes, (int, float)) and not isinstance(proposed_minutes, bool):
        try:
            proposed = float(proposed_minutes)
            if proposed != proposed or proposed == float("inf") or proposed == float("-inf"):
                proposed = ordinary
        except (ValueError, OverflowError):
            proposed = ordinary
    else:
        proposed = ordinary
    return int(round(max(low, min(high, proposed)) * 60))
