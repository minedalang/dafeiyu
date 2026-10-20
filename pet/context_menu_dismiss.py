"""Keep a pet's context menu dismissible above a non-activating Win32 window.

The pet deliberately has WS_EX_NOACTIVATE so that typing in other apps keeps
working. Some Windows/Qt combinations then leave the first click outside a
QMenu unhandled. Unlike a Qt application event filter, GetAsyncKeyState can
observe that click even when another application owns the foreground window.

Only arm this tiny timer while a pet context menu is visible. No global mouse
hook, key logging, background polling or modification to the user's clicks.
"""

from __future__ import annotations

import os


def is_new_outside_click(was_down: bool, is_down: bool,
                         inside_any_visible_menu: bool) -> bool:
    """Close on the first *new* left press, but not inside a submenu."""
    return bool(is_down and not was_down and not inside_any_visible_menu)


def watch_windows_menu_outside_click(menu) -> None:
    """Ensure first left click outside a context menu dismisses it on Windows.

    This is specifically for the two pet surfaces (legacy and overlay), not
    ordinary dialogs, which are allowed to receive the system focus normally.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu

        get_async_state = ctypes.windll.user32.GetAsyncKeyState
    except (AttributeError, ImportError, OSError):
        return

    def left_down() -> bool:
        # Windows documents the high bit as the *current* state. The low
        # "pressed since last poll" bit is unreliable between processes.
        return bool(get_async_state(0x01) & 0x8000)

    timer = QTimer(menu)
    timer.setInterval(16)
    previous = [False]

    def start() -> None:
        previous[0] = left_down()
        timer.start()

    def check() -> None:
        try:
            if not menu.isVisible():
                timer.stop()
                return
            current = left_down()
            was_down = previous[0]
            previous[0] = current
            if not current or was_down:
                return
            cursor = QCursor.pos()
            visible_menus = (menu, *menu.findChildren(QMenu))
            inside = any(
                popup.isVisible() and popup.frameGeometry().contains(cursor)
                for popup in visible_menus
            )
            if is_new_outside_click(was_down, current, inside):
                timer.stop()
                menu.close()  # Releases a synchronous QMenu.exec() as well.
        except RuntimeError:
            # The pet or menu may be destroyed during character replacement.
            timer.stop()

    menu.aboutToShow.connect(start)
    menu.aboutToHide.connect(timer.stop)
    timer.timeout.connect(check)
