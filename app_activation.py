"""Toggles the app between menu-bar-only and Dock-visible.

LiteWhisper is a menu-bar accessory app (LSUIElement in setup.py) with no
Dock icon by default. But Settings and Chat are real windows, and without a
Dock icon there's no way back to them once you click away — no Cmd+Tab
target, nothing to click. So while either is open, the app switches to a
regular activation policy (Dock icon, Cmd+Tab entry); once neither is open,
it reverts to accessory-only.

A set of open window ids (not a counter) is used rather than incrementing/
decrementing, so it's never possible to end up out of sync — reshowing an
already-open window is a harmless no-op add, and windowWillClose: firing
(which happens *before* the window has actually finished closing, so
querying its own isVisible() there is unreliable) just removes its id.
"""

from AppKit import (
    NSApp,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyRegular,
)

_open_windows = set()


def note_window_shown(window_id):
    _open_windows.add(window_id)
    _apply()


def note_window_closed(window_id):
    _open_windows.discard(window_id)
    _apply()


def _apply():
    policy = (
        NSApplicationActivationPolicyRegular
        if _open_windows
        else NSApplicationActivationPolicyAccessory
    )
    NSApp.setActivationPolicy_(policy)
