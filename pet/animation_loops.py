"""Per-animation frame loops for sustained activities using existing clips.

No new images are generated. We seek inside the already playing source clip,
skipping the initial lie-down and final wake-up when the decoder supports it.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path


DEFAULT_START_PERCENT = 35.0
DEFAULT_END_PERCENT = 67.0
# Source installations (including 2-Run-Windows.bat) can edit this file.
LOOP_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "sleep-loop.json"
ACTIVITY_LOOP_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "activity-loops.json"
SLEEP_ANIMATION_NAME = "原地小憩沉眠"
_cached_percentages = (DEFAULT_START_PERCENT, DEFAULT_END_PERCENT)
_next_settings_check = 0.0
_last_settings_signature = None
_cached_clip_settings: dict[str, tuple[float, float]] = {}
_next_clip_settings_check = 0.0
_last_clip_signature = None


def _valid_percentages(start, end) -> tuple[float, float] | None:
    if (isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))):
        return None
    first, last = float(start), float(end)
    if (not math.isfinite(first) or not math.isfinite(last)
            or not 0 <= first < last <= 100 or last - first < 2):
        return None
    return first, last


def read_loop_percentages(path: Path) -> tuple[float, float]:
    """Read user preferences without ever trusting malformed loop bounds."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("expected object")
        start = data.get("start_percent", DEFAULT_START_PERCENT)
        end = data.get("end_percent", DEFAULT_END_PERCENT)
        valid = _valid_percentages(start, end)
        if valid is None:
            raise ValueError("invalid frame range")
        return valid
    except (OSError, ValueError, TypeError, UnicodeError):
        return DEFAULT_START_PERCENT, DEFAULT_END_PERCENT


def _current_percentages() -> tuple[float, float]:
    """Hot-reload about once per second, without disk I/O on each drawn frame."""
    global _cached_percentages, _last_settings_signature, _next_settings_check
    now = time.monotonic()
    if now < _next_settings_check:
        return _cached_percentages
    _next_settings_check = now + 1.0
    try:
        stat = LOOP_SETTINGS_PATH.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    if signature != _last_settings_signature:
        _last_settings_signature = signature
        _cached_percentages = read_loop_percentages(LOOP_SETTINGS_PATH)
    return _cached_percentages


def read_clip_loop_settings(path: Path = ACTIVITY_LOOP_SETTINGS_PATH) -> dict[str, tuple[float, float]]:
    """Parse only bounded percent pairs; unknown clips stay on full-clip replay."""
    try:
        if path.stat().st_size > 64 * 1024:
            return {}
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError, UnicodeError):
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("clips"), dict):
        return {}
    result = {}
    for name, values in list(data["clips"].items())[:256]:
        if not isinstance(name, str) or len(name) > 120 or not isinstance(values, dict):
            continue
        bounds = _valid_percentages(values.get("start_percent"),
                                    values.get("end_percent"))
        if bounds is not None:
            result[name] = bounds
    return result


def _current_clip_settings() -> dict[str, tuple[float, float]]:
    global _cached_clip_settings, _next_clip_settings_check, _last_clip_signature
    now = time.monotonic()
    if now < _next_clip_settings_check:
        return _cached_clip_settings
    _next_clip_settings_check = now + 1.0
    try:
        stat = ACTIVITY_LOOP_SETTINGS_PATH.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    if signature != _last_clip_signature:
        _last_clip_signature = signature
        _cached_clip_settings = read_clip_loop_settings()
    return _cached_clip_settings


def loop_percentages_for(animation: str) -> tuple[float, float] | None:
    """Per-clip UI override, then legacy sleep preference, else whole animation."""
    custom = _current_clip_settings().get(animation)
    if custom is not None:
        return custom
    if animation == SLEEP_ANIMATION_NAME:
        return _current_percentages()
    return None


def save_clip_loop(animation: str, start_percent: float | None,
                   end_percent: float | None, *,
                   path: Path = ACTIVITY_LOOP_SETTINGS_PATH) -> None:
    """Atomic write by the settings window; None removes the custom override."""
    if not isinstance(animation, str) or not animation or len(animation) > 120:
        raise ValueError("invalid animation")
    if (start_percent is None) != (end_percent is None):
        raise ValueError("both percentages must be specified")
    valid = (None if start_percent is None else
             _valid_percentages(start_percent, end_percent))
    if start_percent is not None and valid is None:
        raise ValueError("invalid percentages")
    clips = read_clip_loop_settings(path)
    if valid is None:
        clips.pop(animation, None)
    else:
        clips[animation] = valid
    payload = {"clips": {
        key: {"start_percent": bounds[0], "end_percent": bounds[1]}
        for key, bounds in clips.items()
    }}
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def middle_frames(total: int, *, start_percent: float | None = None,
                  end_percent: float | None = None) -> tuple[int, int] | None:
    """Conservative middle loop; short/unknown clips cannot be cropped safely."""
    if total < 16:
        return None
    defaults = _current_percentages() if start_percent is None or end_percent is None else ()
    first = defaults[0] if start_percent is None else start_percent
    last = defaults[1] if end_percent is None else end_percent
    if not (0 <= first < last <= 100):
        return None
    start = max(0, min(total - 2, int(total * first / 100)))
    end = max(1, min(total - 2, int(total * last / 100)))
    return (start, end) if end - start >= 5 else None


def advance_middle_loop(clip, animation: str | None = None) -> str:
    """Return 'disabled', 'waiting', 'looped' or 'unavailable'. GUI thread only.

    A WebM provider may reject backward seeking while playing. Callers must
    fall back to whole-clip replay in that case rather than freezing the pet.
    """
    if clip is None:
        return "unavailable"
    percentages = loop_percentages_for(animation) if animation is not None else _current_percentages()
    if percentages is None:
        return "disabled"
    try:
        bounds = middle_frames(int(clip.frameCount()), start_percent=percentages[0],
                               end_percent=percentages[1])
        index = int(clip.currentFrameNumber())
        seek = getattr(clip, "jumpToFrame", None)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return "unavailable"
    # The WebM frame count may still be warming on another thread; a small
    # temporary value should not permanently disable a held loop.
    if bounds is None:
        return "waiting"
    if not callable(seek):
        return "unavailable"
    start, end = bounds
    if index < 0 or index < end:
        return "waiting"
    try:
        accepted = seek(start)
        if accepted is False:
            return "unavailable"
        if accepted is None and int(clip.currentFrameNumber()) >= end:
            # A decoder exposing jumpToFrame but silently ignoring seeks must
            # not be marked as a successful partial-frame loop.
            return "unavailable"
        return "looped"
    except (RuntimeError, TypeError, ValueError, OSError):
        return "unavailable"
