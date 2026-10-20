"""Small frame-range looping helper for existing GIF/WebM sleep clips.

No new images are generated. We seek inside the already playing source clip,
skipping the initial lie-down and final wake-up when the decoder supports it.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path


DEFAULT_START_PERCENT = 35.0
DEFAULT_END_PERCENT = 67.0
# Source installations (including 2-Run-Windows.bat) can edit this file.
LOOP_SETTINGS_PATH = Path(__file__).resolve().parent.parent / "sleep-loop.json"
_cached_percentages = (DEFAULT_START_PERCENT, DEFAULT_END_PERCENT)
_next_settings_check = 0.0
_last_settings_signature = None


def read_loop_percentages(path: Path) -> tuple[float, float]:
    """Read user preferences without ever trusting malformed loop bounds."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("expected object")
        start = data.get("start_percent", DEFAULT_START_PERCENT)
        end = data.get("end_percent", DEFAULT_END_PERCENT)
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))):
            raise ValueError("frame percentages must be numbers")
        start, end = float(start), float(end)
        if (not math.isfinite(start) or not math.isfinite(end)
                or not 0 <= start < end <= 100 or end - start < 2):
            raise ValueError("invalid frame range")
        return start, end
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


def advance_middle_loop(clip) -> str:
    """Return 'waiting', 'looped' or 'unavailable'. GUI thread only.

    A WebM provider may reject backward seeking while playing. Callers must
    fall back to whole-clip replay in that case rather than freezing the pet.
    """
    if clip is None:
        return "unavailable"
    try:
        bounds = middle_frames(int(clip.frameCount()))
        index = int(clip.currentFrameNumber())
        seek = getattr(clip, "jumpToFrame", None)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return "unavailable"
    if bounds is None or not callable(seek):
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
