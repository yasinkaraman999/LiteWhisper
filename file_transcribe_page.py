"""Transcribe File page, in the main window's sidebar — pick any audio file
and get its transcript back, using whichever engine (local/cloud) is
configured in Configuration, regardless of file length. Independent of the
mic-based batch/live/chat dictation flows; the only thing it shares with
them is transcriber.get_transcriber() (via file_transcribe.py).
"""

import threading

from AppKit import (
    NSFont,
    NSMakeRect,
    NSModalResponseOK,
    NSNoBorder,
    NSOpenPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSProgressIndicator,
    NSProgressIndicatorBarStyle,
    NSScrollView,
    NSTextView,
    NSViewWidthSizable,
)
from PyObjCTools import AppHelper

import file_transcribe
import nsui

RESULT_MIN_HEIGHT = 260.0

_progress = None
_status_label = None
_result_view = None
_copy_button = None
_choose_button = None


def build():
    global _progress, _status_label, _result_view, _copy_button, _choose_button

    _choose_button = nsui.button("Choose Audio File…", _on_choose)
    _copy_button = nsui.button("Copy Transcript", _on_copy)
    _copy_button.setEnabled_(False)

    _status_label = nsui.secondary(
        "wav, mp3, m4a, aiff, opus, ogg, mp4, caf — any length.", multiline=True
    )

    progress = nsui.anchor(NSProgressIndicator.alloc().init())
    progress.setStyle_(NSProgressIndicatorBarStyle)
    progress.setIndeterminate_(False)
    progress.setMinValue_(0.0)
    progress.setMaxValue_(1.0)
    progress.setDoubleValue_(0.0)
    progress.setHidden_(True)
    _progress = progress

    result_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 100))
    result_view.setEditable_(False)
    result_view.setSelectable_(True)
    result_view.setRichText_(False)
    result_view.setFont_(NSFont.systemFontOfSize_(13.0))
    result_view.setVerticallyResizable_(True)
    result_view.setHorizontallyResizable_(False)
    result_view.setAutoresizingMask_(NSViewWidthSizable)
    result_view.textContainer().setWidthTracksTextView_(True)
    result_view.setTextContainerInset_((8.0, 8.0))
    _result_view = result_view

    scroll = nsui.anchor(NSScrollView.alloc().init())
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(NSNoBorder)
    scroll.setDocumentView_(result_view)
    nsui.activate([scroll.heightAnchor().constraintGreaterThanOrEqualToConstant_(RESULT_MIN_HEIGHT)])

    actions = nsui.hstack_control([_choose_button, _copy_button])

    body = nsui.vstack([
        nsui.heading("Transcribe File"),
        _status_label,
        actions,
        progress,
        scroll,
    ], spacing=10.0)

    return nsui.scroll_page([body])


def _on_choose():
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setAllowedFileTypes_(file_transcribe.AUDIO_EXTENSIONS)
    if panel.runModal() != NSModalResponseOK:
        return
    urls = panel.URLs()
    if not urls:
        return
    path = urls[0].path()
    if path:
        _start(path)


def _on_copy():
    text = str(_result_view.string())
    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, NSPasteboardTypeString)


def _start(path):
    _choose_button.setEnabled_(False)
    _copy_button.setEnabled_(False)
    _result_view.setString_("")
    _progress.setHidden_(False)
    _progress.setDoubleValue_(0.0)
    _status_label.setStringValue_(f"Transcribing {path.split('/')[-1]}…")

    def on_progress(fraction):
        AppHelper.callAfter(_update_progress, fraction)

    def run():
        try:
            text = file_transcribe.transcribe_file(path, on_progress=on_progress)
        except Exception as e:
            AppHelper.callAfter(_finish, None, str(e))
            return
        AppHelper.callAfter(_finish, text, None)

    threading.Thread(target=run, daemon=True).start()


def _update_progress(fraction):
    _progress.setDoubleValue_(fraction)
    _status_label.setStringValue_(f"Transcribing… {int(fraction * 100)}%")


def _finish(text, error):
    _choose_button.setEnabled_(True)
    _progress.setHidden_(True)
    if error is not None:
        _status_label.setStringValue_(f"Failed: {error}")
        return
    _status_label.setStringValue_("Done.")
    _result_view.setString_(text or "(no speech detected)")
    _copy_button.setEnabled_(bool(text and text.strip()))
