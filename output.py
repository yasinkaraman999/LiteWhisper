import time

from AppKit import NSPasteboard, NSPasteboardTypeString
from pynput.keyboard import Controller, Key

_kb = Controller()


def copy(text):
    """Puts text on the clipboard."""
    # NSPasteboard is used directly (instead of shelling out to pbcopy)
    # because pbcopy relies on the LANG env var to decide the text encoding,
    # which GUI-launched .app bundles don't inherit — that silently mangles
    # non-ASCII characters (e.g. Turkish ş/ü/ı) into mojibake.
    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, NSPasteboardTypeString)


def deliver(text):
    """Copies text to the clipboard and pastes it into the focused app."""
    copy(text)

    # Give the target app a moment to regain focus after any UI interaction.
    time.sleep(0.1)
    with _kb.pressed(Key.cmd):
        _kb.press("v")
        _kb.release("v")
