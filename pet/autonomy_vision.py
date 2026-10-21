"""Private, foreground-window-only visual observation for AI autonomy.

An opt-in screen switch by itself is NOT permission to capture: the foreground
process name must exactly match a configured executable name.  Never fall back
to the full desktop when the process cannot be identified or the grab fails.
"""
from __future__ import annotations

import io
import re
from typing import Any

_EXE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.+ -]{0,119}\.exe$", re.IGNORECASE)


def normalize_whitelist(raw: Any) -> tuple[str, ...]:
    """Allow only explicit Windows .exe basenames, not wildcards/paths/titles."""
    if not isinstance(raw, (list, tuple)):
        return ()
    names = []
    for entry in raw[:100]:
        name = str(entry).strip().lower()
        if _EXE.fullmatch(name) and name not in names:
            names.append(name)
    return tuple(names[:50])


def process_allowed(process: str, whitelist: tuple[str, ...]) -> bool:
    """Fail closed on unknown executable names; substring matching is unsafe."""
    return bool(whitelist) and str(process or '').strip().lower() in whitelist


def capture_allowed_foreground(whitelist: tuple[str, ...], *, cancelled=None):
    """Return (JPEG bytes, exe name) or (None, '').  Worker thread only.

    We capture only the approved *foreground rectangle*, not all monitors in
    the result. Note that an overlapping floating window may still be visible
    inside that rectangle; this is a normal screen-capture limitation.
    """
    if not whitelist or (cancelled is not None and cancelled.is_set()):
        return None, ''
    from . import vision
    try:
        before = vision.foreground_window_info()
        if not before or not process_allowed(before.get('process', ''), whitelist):
            return None, ''
        rect = before.get('rect')
        if not (isinstance(rect, (tuple, list)) and len(rect) == 4
                and 0 < int(rect[2]) <= 16384 and 0 < int(rect[3]) <= 16384):
            return None, ''
        # Bounding-box grab: unlike grab(all_screens=True), the returned bitmap
        # never contains data outside the approved front window rectangle.
        from PIL import Image, ImageGrab
        x, y, width, height = (int(n) for n in rect)
        img = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
        if cancelled is not None and cancelled.is_set():
            return None, ''
        after = vision.foreground_window_info()
        if (not after or after.get('hwnd') != before.get('hwnd')
                or after.get('process', '').strip().lower() != before['process'].strip().lower()
                or not process_allowed(after.get('process', ''), whitelist)):
            return None, ''
        if img is None or img.width <= 0 or img.height <= 0:
            return None, ''
        scale = vision.MAX_EDGE / max(img.width, img.height)
        if scale < 1:
            img = img.resize((max(1, round(img.width * scale)),
                              max(1, round(img.height * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert('RGB').save(buf, 'JPEG', quality=vision.JPEG_QUALITY)
        return buf.getvalue(), before['process'].strip().lower()
    except (ValueError, TypeError, OSError, ImportError, AttributeError):
        return None, ''
