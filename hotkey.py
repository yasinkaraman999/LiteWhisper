import threading
import time

from pynput import keyboard
from Quartz import (
    CGEventGetFlags,
    CGEventGetIntegerValueField,
    CGRequestListenEventAccess,
    kCGEventFlagMaskAlternate,
    kCGEventKeyDown,
    kCGKeyboardEventAutorepeat,
    kCGKeyboardEventKeycode,
)

SPACE_KEYCODE = 49  # kVK_Space
ESCAPE_KEYCODE = 53  # kVK_Escape
DEBOUNCE_SECONDS = 0.4


def start_listener(on_toggle, on_cancel=None, is_recording=None):
    """Runs a global hotkey listener in a background thread.

    Option+Space toggles recording. The key combo is consumed at the OS
    event-tap level (via darwin_intercept) so it never reaches the
    frontmost app as a literal space character.

    Escape cancels an in-progress recording and is swallowed only while
    recording — the rest of the time it must pass straight through, or
    every dialog and text field on the system would stop responding to it.

    Returns the listener so the caller can stop() it on app exit.
    """
    # CGPreflightListenEventAccess() alone never triggers the native
    # permission dialog. This actively requests Input Monitoring access,
    # which is what makes the system prompt (and the Settings entry) appear.
    CGRequestListenEventAccess()

    last_triggered = [0.0]

    def intercept(event_type, event):
        keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
        flags = CGEventGetFlags(event)

        if keycode == ESCAPE_KEYCODE:
            recording = is_recording() if is_recording is not None else False
            if not recording or on_cancel is None:
                return event
            if event_type == kCGEventKeyDown:
                threading.Thread(target=on_cancel, daemon=True).start()
            return None

        is_option_space = (
            keycode == SPACE_KEYCODE and flags & kCGEventFlagMaskAlternate
        )

        if not is_option_space:
            return event

        is_autorepeat = bool(
            CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat)
        )

        if event_type == kCGEventKeyDown and not is_autorepeat:
            now = time.monotonic()
            # Holding the combo a beat too long can still deliver two
            # separate key-down events (not flagged as autorepeat) for
            # what the user experiences as a single press. Debounce on
            # top of the autorepeat check so on_toggle() only fires once.
            if now - last_triggered[0] > DEBOUNCE_SECONDS:
                last_triggered[0] = now
                # The event tap must return almost instantly or macOS will
                # silently and permanently disable it (kCGEventTapDisabledByTimeout).
                # on_toggle() opens an audio stream, which is too slow to run
                # inline here, so hand it off to a background thread.
                threading.Thread(target=on_toggle, daemon=True).start()
        # Suppress both key-down and key-up for the combo (including
        # autorepeat) so nothing leaks through to the focused app.
        return None

    listener = keyboard.Listener(darwin_intercept=intercept)
    listener.start()
    return listener
