"""The ChatGPT-style mini chat window.

Runs independently of Option+Space / Shift+Option+Space dictation — the only
things it shares with them are the microphone (see mic_lock.py) and the
OpenRouter API key. Conversations and messages persist via chat_history.py;
images are copied into config.CHAT_IMAGES_DIR and referenced by path.

The transcript itself (message list, markdown/code rendering, streaming,
hover actions) is a local WKWebView loaded from chat_transcript/ — fully
offline, no network access, driven entirely by the window.LW.* functions in
chat_transcript/app.js. Everything else (sidebar, model picker, input pill,
mic/attach/send buttons, window chrome) is native AppKit exactly as before;
that split is deliberate, see the plan discussed for this round. Rendering
markdown/code/LaTeX to the standard native apps' fidelity is fundamentally a
browser-engine problem — reimplementing it in NSAttributedString would be
far more code for a worse result than handing it to WebKit's own engine.
"""

import base64
import json
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import objc
import rumps
from AppKit import (
    NSApp,
    NSBox,
    NSBoxCustom,
    NSButton,
    NSColor,
    NSEvent,
    NSEventModifierFlagShift,
    NSFont,
    NSFontWeightMedium,
    NSFontWeightSemibold,
    NSImage,
    NSImageSymbolConfiguration,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSNoBorder,
    NSNoTitle,
    NSOpenPanel,
    NSScrollView,
    NSSplitViewController,
    NSSplitViewItem,
    NSTableCellView,
    NSTableColumn,
    NSTableView,
    NSTextView,
    NSTitlebarSeparatorStyleNone,
    NSToolbar,
    NSToolbarDisplayModeIconOnly,
    NSView,
    NSViewController,
    NSViewWidthSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowToolbarStyleUnified,
)
from Foundation import NSIndexSet, NSObject, NSURL
from PyObjCTools import AppHelper
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

import app_activation
import chat_bubble
import chat_engine
import chat_history
import config
import mic_lock
import model_picker
import models
import nsui
import theme
from audio_recorder import AudioRecorder
from main_window import ToolbarDelegate
from resources import resource_path
from transcriber import get_transcriber
from ui_helpers import ButtonTarget, WindowCloseObserver, keep_alive

WINDOW_WIDTH = 880.0
WINDOW_HEIGHT = 640.0
SIDEBAR_MIN_WIDTH = 180.0
SIDEBAR_MAX_WIDTH = 260.0
INPUT_TEXT_MIN_HEIGHT = 34.0  # roughly one line, matches the icon buttons' own height
INPUT_TEXT_MAX_HEIGHT = 140.0  # about six lines before the text view scrolls internally
ICON_BUTTON_SIZE = 34.0
ICON_GLYPH_POINT_SIZE = 16.0
SEND_BUTTON_SIZE = 38.0
SEND_GLYPH_POINT_SIZE = 20.0
MIC_OWNER = "chat"
IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"]
_MIME_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}

_window = None
_webview = None
_webview_ready = False
_pending_js = []
_bridge_handler = None
_nav_delegate = None

_sidebar_table = None
_sidebar_delegate = None
_conversations = []  # cached rows from chat_history.list_conversations()
_current_conversation_id = None
_model_picker = None
_available_models = []  # [{"id", "name", "supports_images"}]
_input_view = None
_input_scroll_height = None  # mutable NSLayoutConstraint — see _update_input_height()
_attach_button = None
_mic_button = None
_send_button = None
_pending_images = []  # local file paths staged for the next send
_recording = False
_recorder = None

# "idle" or "streaming" — drives the send/stop button and whether the
# input row accepts new input.
_send_state = "idle"
_stream_cancel_event = None


# --------------------------------------------------------------- helpers


def _display_title(convo):
    return convo["title"] or "New Chat"


def _parse(raw):
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _icon_button(symbol, callback, tooltip=""):
    button = nsui.anchor(NSButton.alloc().init())
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, tooltip)
    configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        ICON_GLYPH_POINT_SIZE, NSFontWeightMedium
    )
    button.setImage_(image.imageWithSymbolConfiguration_(configuration))
    button.setBordered_(False)
    button.setToolTip_(tooltip)
    target = ButtonTarget.alloc().initWithCallback_(lambda _sender: callback())
    keep_alive(target)
    button.setTarget_(target)
    button.setAction_("clicked:")
    nsui.activate([
        button.widthAnchor().constraintEqualToConstant_(ICON_BUTTON_SIZE),
        button.heightAnchor().constraintEqualToConstant_(ICON_BUTTON_SIZE),
    ])
    return button


def _set_send_icon(button, symbol, tooltip, fill_color):
    """The send/stop button renders as a filled, two-tone circle (white
    glyph on a solid color disc) via SF Symbols' palette rendering, instead
    of the flat single-color glyph the other input-row icons use — it's the
    primary action of the whole row and needs to read as one at a glance,
    the way ChatGPT/Claude's own send buttons do, rather than blending in
    with attach/mic/model/refresh."""
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, tooltip)
    size_configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        SEND_GLYPH_POINT_SIZE, NSFontWeightSemibold
    )
    palette_configuration = NSImageSymbolConfiguration.configurationWithPaletteColors_(
        [NSColor.whiteColor(), fill_color]
    )
    configuration = size_configuration.configurationByApplyingConfiguration_(palette_configuration)
    button.setImage_(image.imageWithSymbolConfiguration_(configuration))
    button.setToolTip_(tooltip)


def _build_send_button(callback):
    button = nsui.anchor(NSButton.alloc().init())
    button.setBordered_(False)
    target = ButtonTarget.alloc().initWithCallback_(lambda _sender: callback())
    keep_alive(target)
    button.setTarget_(target)
    button.setAction_("clicked:")
    nsui.activate([
        button.widthAnchor().constraintEqualToConstant_(SEND_BUTTON_SIZE),
        button.heightAnchor().constraintEqualToConstant_(SEND_BUTTON_SIZE),
    ])
    _set_send_icon(button, "arrow.up.circle.fill", "Send", NSColor.controlAccentColor())
    return button


def _image_data_uri(path):
    """Inlines an attached image as a data: URI rather than handing the
    webview a file:// path — the transcript page and the images in
    config.CHAT_IMAGES_DIR don't share a directory tree the way the page and
    its own vendor/ assets do, and inlining sidesteps needing to widen the
    webview's file read access to cover it."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    ext = Path(path).suffix.lstrip(".").lower()
    mime = _MIME_TYPES.get(ext, "image/png")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _message_to_json(message):
    when = _parse(message.get("created_at"))
    images = []
    for p in message.get("image_paths") or []:
        uri = _image_data_uri(p)
        if uri:
            images.append({"data": uri, "name": Path(p).name})
    return {
        "id": message["id"],
        "role": message["role"],
        "text": message.get("text") or "",
        "images": images,
        "time": when.strftime("%H:%M") if when else "",
    }


# ------------------------------------------------------------- AppKit glue


class SidebarDelegate(NSObject):
    """Data source, delegate and context-menu target for the conversation
    list, combined — there's only ever one sidebar, so one small class
    covers all three protocols."""

    def numberOfRowsInTableView_(self, table_view):
        return len(_conversations)

    def tableView_viewForTableColumn_row_(self, table_view, column, row):
        convo = _conversations[row]
        cell = NSTableCellView.alloc().init()
        text_field = nsui.label(_display_title(convo), size=12.0)
        cell.addSubview_(text_field)
        cell.setTextField_(text_field)
        nsui.activate([
            text_field.leadingAnchor().constraintEqualToAnchor_constant_(
                cell.leadingAnchor(), 6.0
            ),
            text_field.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                cell.trailingAnchor(), -6.0
            ),
            text_field.centerYAnchor().constraintEqualToAnchor_(cell.centerYAnchor()),
        ])
        return cell

    def tableViewSelectionDidChange_(self, notification):
        row = _sidebar_table.selectedRow()
        if 0 <= row < len(_conversations):
            _select_conversation(_conversations[row]["id"])

    def deleteSelected_(self, sender):
        row = _sidebar_table.clickedRow()
        if 0 <= row < len(_conversations):
            _on_delete_conversation(_conversations[row]["id"])


class InputDelegate(NSObject):
    """Catches Return in the message text view: sends the message unless
    Shift is held, in which case a plain newline is inserted as usual."""

    def initWithCallback_(self, callback):
        self = objc.super(InputDelegate, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def textView_doCommandBySelector_(self, text_view, selector):
        if selector == "insertNewline:":
            shift_held = bool(NSEvent.modifierFlags() & NSEventModifierFlagShift)
            if not shift_held:
                self._callback()
                return True
        return False

    def textDidChange_(self, notification):
        _update_input_height()


class ChatBridge(NSObject):
    """Receives window.webkit.messageHandlers.bridge.postMessage() calls
    from chat_transcript/app.js — the transcript's hover actions (copy is
    handled entirely on the JS side; regenerate/edit/retry need Python since
    they touch chat_history and re-call chat_engine)."""

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        try:
            msg_type = body.get("type")
        except AttributeError:
            return

        if msg_type == "regenerate":
            _on_regenerate(body.get("id"))
        elif msg_type == "edit":
            _on_edit(body.get("id"), body.get("text"))
        elif msg_type == "retry":
            _on_retry(body.get("id"))
        # "feedback" (like/dislike) is intentionally local-only — there's no
        # backend to send it to, so app.js just toggles its own button state.


class ChatWebViewNavDelegate(NSObject):
    """Flushes any window.LW.* calls made before the page finished loading
    — evaluateJavaScript_completionHandler_ silently no-ops on a page that
    hasn't loaded yet, so show()'s initial _refresh_messages() would
    otherwise be lost."""

    def webView_didFinishNavigation_(self, webview, navigation):
        global _webview_ready
        _webview_ready = True
        pending = list(_pending_js)
        _pending_js.clear()
        for js in pending:
            _webview.evaluateJavaScript_completionHandler_(js, None)


class ChatSidebarController(NSViewController):
    """Hosts the conversation list as a real NSSplitViewItem sidebar —
    draggable, resizable and collapsible via the toolbar, the same as the
    main window's — instead of a fixed-width plain view."""

    def loadView(self):
        self.setView_(_build_sidebar())


class ChatMainController(NSViewController):
    """Hosts the model row, message transcript and input row."""

    def loadView(self):
        self.setView_(_build_main_pane())


# --------------------------------------------------------------- building


def _build_sidebar():
    global _sidebar_table, _sidebar_delegate

    _sidebar_delegate = SidebarDelegate.alloc().init()
    keep_alive(_sidebar_delegate)

    container = nsui.anchor(NSView.alloc().init())

    new_chat = nsui.button("New Chat", _on_new_chat)

    table = NSTableView.alloc().init()
    table.setHeaderView_(None)
    table.setRowHeight_(28.0)
    table.setAllowsEmptySelection_(True)
    table.setAllowsMultipleSelection_(False)
    table.setBackgroundColor_(NSColor.clearColor())
    column = NSTableColumn.alloc().initWithIdentifier_("conversation")
    table.addTableColumn_(column)
    table.setDataSource_(_sidebar_delegate)
    table.setDelegate_(_sidebar_delegate)

    menu = NSMenu.alloc().init()
    delete_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Delete", "deleteSelected:", ""
    )
    delete_item.setTarget_(_sidebar_delegate)
    menu.addItem_(delete_item)
    table.setMenu_(menu)

    scroll = nsui.anchor(NSScrollView.alloc().init())
    scroll.setDrawsBackground_(False)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setDocumentView_(table)

    container.addSubview_(new_chat)
    container.addSubview_(scroll)

    nsui.activate([
        new_chat.topAnchor().constraintEqualToAnchor_constant_(
            container.safeAreaLayoutGuide().topAnchor(), 10.0
        ),
        new_chat.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), 10.0),
        new_chat.trailingAnchor().constraintEqualToAnchor_constant_(container.trailingAnchor(), -10.0),

        scroll.topAnchor().constraintEqualToAnchor_constant_(new_chat.bottomAnchor(), 8.0),
        scroll.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
        scroll.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
        scroll.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
    ])

    _sidebar_table = table
    return container


def _build_input_row():
    global _input_view, _attach_button, _mic_button, _send_button, _model_picker

    _attach_button = _icon_button("paperclip", _on_attach_image, tooltip="Attach image")
    _mic_button = _icon_button("mic.fill", _on_mic_clicked, tooltip="Dictate")
    _send_button = _build_send_button(_on_send_button_clicked)

    # The model picker lives right in the input bar, left of Send — like
    # Claude/ChatGPT's own compact model switcher — instead of a separate
    # bar above the transcript. It's per-conversation: picking a model here
    # saves it onto whichever conversation is currently open (see
    # _on_model_picker_change), the same conversation.model column a normal
    # send already writes to.
    _model_picker = model_picker.ModelPicker(on_change=_on_model_picker_change, compact=True)
    refresh_button = _icon_button("arrow.clockwise", _on_refresh_chat_models, tooltip="Refresh model list")

    # NSTextView as an NSScrollView's documentView is sized the classic
    # autoresizing way, not via Auto Layout constraints on the text view
    # itself — skipping this configuration is what left an earlier version
    # with a degenerate frame that couldn't actually be typed into.
    _input_view = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 60))
    _input_view.setMinSize_((0.0, 0.0))
    _input_view.setMaxSize_((1.0e7, 1.0e7))
    _input_view.setVerticallyResizable_(True)
    _input_view.setHorizontallyResizable_(False)
    _input_view.setAutoresizingMask_(NSViewWidthSizable)
    _input_view.textContainer().setContainerSize_((0.0, 1.0e7))
    _input_view.textContainer().setWidthTracksTextView_(True)
    _input_view.setTextContainerInset_((6.0, 6.0))
    _input_view.setFont_(NSFont.systemFontOfSize_(14.0))
    _input_view.setRichText_(False)
    _input_view.setAutomaticQuoteSubstitutionEnabled_(False)
    _input_view.setEditable_(True)
    _input_view.setSelectable_(True)
    _input_view.setDrawsBackground_(False)
    delegate = InputDelegate.alloc().initWithCallback_(_on_send)
    keep_alive(delegate)
    _input_view.setDelegate_(delegate)

    input_scroll = nsui.anchor(NSScrollView.alloc().init())
    input_scroll.setHasVerticalScroller_(True)
    input_scroll.setHasHorizontalScroller_(False)
    input_scroll.setAutohidesScrollers_(True)
    input_scroll.setDrawsBackground_(False)
    input_scroll.setBorderType_(NSNoBorder)
    input_scroll.setDocumentView_(_input_view)

    global _input_scroll_height
    _input_scroll_height = input_scroll.heightAnchor().constraintEqualToConstant_(INPUT_TEXT_MIN_HEIGHT)
    nsui.activate([_input_scroll_height])

    # The whole row is one rounded pill (attach + mic + text + send all
    # inside it), the way ChatGPT's own input field is built, rather than
    # icon buttons sitting outside a separately boxed text field.
    row = nsui.anchor(NSBox.alloc().init())
    row.setBoxType_(NSBoxCustom)
    row.setTitlePosition_(NSNoTitle)
    # A hairline border gives the pill a defined edge against the window
    # background — with only a fill and no border it read as flatter and
    # less deliberate in dark mode, where the fill and the page behind it
    # sit closer in value than they do in light mode.
    row.setBorderWidth_(1.0)
    row.setBorderColor_(NSColor.separatorColor())
    row.setCornerRadius_(18.0)
    row.setFillColor_(theme.GROUP_FILL)
    row.setContentViewMargins_((0.0, 0.0))

    content = nsui.anchor(NSView.alloc().init())
    for view in (_attach_button, _mic_button, input_scroll, _model_picker.view, refresh_button, _send_button):
        content.addSubview_(view)

    # Every control's *bottom* edge sits on the same line (content.bottom -
    # 8, matching input_scroll's own bottom inset below) instead of each
    # being centered independently — centering the 34pt icon buttons on the
    # content view's full height while the text view's own line of text
    # top-aligns inside a separately-sized scroll view is what produced the
    # few-pixel vertical mismatch between the icons and the typed text.
    bottom_inset = -8.0
    nsui.activate([
        _attach_button.leadingAnchor().constraintEqualToAnchor_constant_(content.leadingAnchor(), 8.0),
        _attach_button.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), bottom_inset),

        _mic_button.leadingAnchor().constraintEqualToAnchor_constant_(_attach_button.trailingAnchor(), 2.0),
        _mic_button.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), bottom_inset),

    input_scroll.leadingAnchor().constraintEqualToAnchor_constant_(_mic_button.trailingAnchor(), 6.0),
        input_scroll.topAnchor().constraintEqualToAnchor_constant_(content.topAnchor(), 8.0),
        # content's height (and so the whole pill's) is *derived* from this,
        # not the other way around — _update_input_height() grows/shrinks
        # this one constraint as you type, instead of the row being a fixed
        # 76pt box that a multi-line message just scrolls inside of.
        content.bottomAnchor().constraintEqualToAnchor_constant_(input_scroll.bottomAnchor(), -bottom_inset),

        _model_picker.view.leadingAnchor().constraintEqualToAnchor_constant_(input_scroll.trailingAnchor(), 8.0),
        # Centered on the icon buttons' own center line rather than bottom-
        # aligned like them: the chip is shorter than their 34pt box, so
        # matching bottoms left it visually hanging low relative to the
        # buttons' larger, vertically-centered glyphs.
        _model_picker.view.centerYAnchor().constraintEqualToAnchor_(_attach_button.centerYAnchor()),

        refresh_button.leadingAnchor().constraintEqualToAnchor_constant_(
            _model_picker.view.trailingAnchor(), 2.0
        ),
        refresh_button.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), bottom_inset),

        _send_button.leadingAnchor().constraintEqualToAnchor_constant_(refresh_button.trailingAnchor(), 4.0),
        _send_button.trailingAnchor().constraintEqualToAnchor_constant_(content.trailingAnchor(), -8.0),
        _send_button.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), bottom_inset),
    ])

    row.setContentView_(content)
    nsui.pin(content, row)
    return row


def _build_transcript_webview():
    global _webview, _bridge_handler, _nav_delegate, _webview_ready

    _webview_ready = False
    _pending_js.clear()

    web_config = WKWebViewConfiguration.alloc().init()
    controller = WKUserContentController.alloc().init()
    _bridge_handler = ChatBridge.alloc().init()
    keep_alive(_bridge_handler)
    controller.addScriptMessageHandler_name_(_bridge_handler, "bridge")
    web_config.setUserContentController_(controller)

    webview = nsui.anchor(
        WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 100, 100), web_config)
    )

    _nav_delegate = ChatWebViewNavDelegate.alloc().init()
    keep_alive(_nav_delegate)
    webview.setNavigationDelegate_(_nav_delegate)

    root = Path(resource_path("chat_transcript"))
    webview.loadFileURL_allowingReadAccessToURL_(
        NSURL.fileURLWithPath_(str(root / "index.html")),
        NSURL.fileURLWithPath_(str(root)),
    )

    _webview = webview
    return webview


def _build_main_pane():
    transcript = _build_transcript_webview()
    input_row = _build_input_row()

    container = nsui.anchor(NSView.alloc().init())
    container.addSubview_(transcript)
    container.addSubview_(input_row)

    nsui.activate([
        transcript.topAnchor().constraintEqualToAnchor_(container.safeAreaLayoutGuide().topAnchor()),
        transcript.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
        transcript.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
        transcript.bottomAnchor().constraintEqualToAnchor_(input_row.topAnchor()),

        input_row.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
        input_row.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
        input_row.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
        # No fixed height here — the row's height is now intrinsic, derived
        # bottom-up from _input_scroll_height (see _build_input_row() and
        # _update_input_height()). These are just safety rails.
        input_row.heightAnchor().constraintGreaterThanOrEqualToConstant_(INPUT_TEXT_MIN_HEIGHT + 16.0),
        input_row.heightAnchor().constraintLessThanOrEqualToConstant_(INPUT_TEXT_MAX_HEIGHT + 16.0),
    ])
    return container


def _build_window():
    sidebar_controller = ChatSidebarController.alloc().init()
    main_controller = ChatMainController.alloc().init()

    split = NSSplitViewController.alloc().init()

    sidebar_item = NSSplitViewItem.sidebarWithViewController_(sidebar_controller)
    sidebar_item.setMinimumThickness_(SIDEBAR_MIN_WIDTH)
    sidebar_item.setMaximumThickness_(SIDEBAR_MAX_WIDTH)
    sidebar_item.setAllowsFullHeightLayout_(True)
    sidebar_item.setTitlebarSeparatorStyle_(NSTitlebarSeparatorStyleNone)
    # Starts collapsed — a chat window opens straight into the conversation,
    # like ChatGPT's own; the toolbar's native sidebar-toggle button (wired
    # up automatically by NSSplitViewController) brings it back.
    sidebar_item.setCollapsed_(True)
    split.addSplitViewItem_(sidebar_item)

    main_item = NSSplitViewItem.splitViewItemWithViewController_(main_controller)
    main_item.setAutomaticallyAdjustsSafeAreaInsets_(True)
    main_item.setTitlebarSeparatorStyle_(NSTitlebarSeparatorStyleNone)
    split.addSplitViewItem_(main_item)

    style = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
        | NSWindowStyleMaskFullSizeContentView
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (WINDOW_WIDTH, WINDOW_HEIGHT)), style, 2, False
    )
    window.setContentViewController_(split)
    window.setContentSize_((WINDOW_WIDTH, WINDOW_HEIGHT))
    window.setReleasedWhenClosed_(False)
    window.setTitle_("Chat")
    window.setMinSize_((520, 380))

    toolbar_delegate = ToolbarDelegate.alloc().init()
    keep_alive(toolbar_delegate)
    toolbar = NSToolbar.alloc().initWithIdentifier_("LiteWhisperChatToolbar")
    toolbar.setDelegate_(toolbar_delegate)
    toolbar.setAllowsUserCustomization_(False)
    toolbar.setDisplayMode_(NSToolbarDisplayModeIconOnly)
    window.setToolbar_(toolbar)
    window.setToolbarStyle_(NSWindowToolbarStyleUnified)

    def _on_close():
        app_activation.note_window_closed("chat")
        chat_bubble.set_active(False)

    close_observer = WindowCloseObserver.alloc().initWithCallback_(_on_close)
    keep_alive(close_observer)
    window.setDelegate_(close_observer)

    window.center()
    return window


# ------------------------------------------------------------ JS bridge


def _eval_js(js):
    if _webview is None:
        return
    if not _webview_ready:
        _pending_js.append(js)
        return
    _webview.evaluateJavaScript_completionHandler_(js, None)


def _call_js(func, *args):
    payload = ", ".join(json.dumps(a) for a in args)
    _eval_js(f"window.LW.{func}({payload});")


# --------------------------------------------------------------- messages


def _refresh_messages():
    if _current_conversation_id is None:
        _call_js("setMessages", [])
        return
    messages = chat_history.load_messages(_current_conversation_id)
    _call_js("setMessages", [_message_to_json(m) for m in messages])


# ------------------------------------------------------ streaming replies


def _start_streaming_row():
    _call_js("startStreaming")


def _update_streaming_text(text):
    _call_js("updateStreaming", text)


def _end_streaming_row(final_message=None):
    _call_js("endStreaming", final_message)


def _stream_error(message_text):
    _call_js("setStreamError", message_text)


# ---------------------------------------------------------- conversations


def _refresh_sidebar(select_id=None):
    global _conversations
    _conversations = chat_history.list_conversations()
    if _sidebar_table is not None:
        _sidebar_table.reloadData()
    target = select_id if select_id is not None else _current_conversation_id
    _select_row_for(target)


def _select_row_for(conversation_id):
    if _sidebar_table is None:
        return
    for i, convo in enumerate(_conversations):
        if convo["id"] == conversation_id:
            _sidebar_table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(i), False
            )
            return


def _select_conversation(conversation_id):
    global _current_conversation_id
    if conversation_id == _current_conversation_id:
        return
    if _send_state == "streaming":
        # Switching conversations mid-reply would leave the streaming
        # update writing into a conversation that's no longer on screen.
        _select_row_for(_current_conversation_id)
        return
    _current_conversation_id = conversation_id
    _refresh_messages()
    convo = next((c for c in _conversations if c["id"] == conversation_id), None)
    if convo is not None:
        _populate_chat_model_popup(convo.get("model") or config.load()["chat_model"])


def _on_new_chat():
    cfg = config.load()
    conversation_id = chat_history.create_conversation(model=cfg["chat_model"])
    _refresh_sidebar(select_id=conversation_id)
    global _current_conversation_id
    _current_conversation_id = conversation_id
    _refresh_messages()


def _on_delete_conversation(conversation_id):
    global _current_conversation_id
    chat_history.delete_conversation(conversation_id)
    if _current_conversation_id == conversation_id:
        _current_conversation_id = None
    _refresh_sidebar()
    _refresh_messages()


# --------------------------------------------------------------- models


def _populate_chat_model_popup(selected_id):
    _model_picker.set_models(_available_models)
    _model_picker.select(selected_id)
    _update_attach_availability()


def _current_model_id():
    return _model_picker.selected_id()


def _update_attach_availability():
    """Disables the attach button for a model that can't accept images —
    otherwise you can stage an attachment the model will just ignore (or
    error on) once you actually send. Left enabled for a model id we don't
    have data for yet (still loading, or a stale saved id not in the
    current list) rather than guessing it can't take images."""
    if _attach_button is None:
        return
    model_id = _model_picker.selected_id() if _model_picker is not None else None
    model = next((m for m in _available_models if m["id"] == model_id), None)
    supports = True if model is None else bool(model.get("supports_images"))
    _attach_button.setEnabled_(supports)
    _attach_button.setToolTip_(
        "Attach image" if supports else "This model doesn't support image messages"
    )


def _on_model_picker_change(model_id):
    """Picking a model from the input bar's chip saves it onto whichever
    conversation is open right away — the model is per-conversation, not
    global, so this shouldn't wait for the next send to stick."""
    if _current_conversation_id is not None:
        chat_history.set_conversation_model(_current_conversation_id, model_id)
        _refresh_sidebar()
    _update_attach_availability()


def _on_refresh_chat_models():
    api_key = config.load()["openrouter_api_key"]
    if not api_key:
        return

    def fetch():
        try:
            fetched = models.fetch_chat_models(api_key)
        except Exception:
            return

        def apply():
            global _available_models
            _available_models = fetched
            selected = _current_model_id() or config.load()["chat_model"]
            _populate_chat_model_popup(selected)

        AppHelper.callAfter(apply)

    threading.Thread(target=fetch, daemon=True).start()


# ---------------------------------------------------------------- images


def _persist_image(source_path):
    try:
        config.CHAT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(source_path).suffix or ".png"
        dest = config.CHAT_IMAGES_DIR / f"{uuid.uuid4().hex}{ext}"
        shutil.copy(source_path, dest)
        return str(dest)
    except OSError:
        return None


def _on_attach_image():
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(True)
    panel.setAllowedFileTypes_(IMAGE_EXTENSIONS)
    if panel.runModal() != NSModalResponseOK:
        return
    for url in panel.URLs():
        path = url.path()
        if path:
            _pending_images.append(path)
    _attach_button.setToolTip_(f"{len(_pending_images)} image(s) attached")


# ------------------------------------------------------------------- mic


def _on_mic_clicked():
    global _recording, _recorder
    if _recording:
        _stop_chat_recording()
        return
    if not mic_lock.acquire(MIC_OWNER):
        rumps.notification("lite-whisper", "", "Microphone is busy")
        return

    # Opening the audio stream is slow enough to noticeably freeze the app
    # if done on the main thread — hotkey.py's on_toggle() dispatches for
    # exactly this reason, and this button needs the same treatment.
    _recorder = AudioRecorder()
    recorder = _recorder
    _recording = True
    _mic_button.setContentTintColor_(NSColor.systemRedColor())

    def start_stream():
        try:
            recorder.start()
        except Exception as e:
            def on_error():
                global _recording
                _recording = False
                _mic_button.setContentTintColor_(None)
                mic_lock.release(MIC_OWNER)
                rumps.notification("lite-whisper", "Chat", f"Couldn't start recording: {e}")

            AppHelper.callAfter(on_error)

    threading.Thread(target=start_stream, daemon=True).start()


def _stop_chat_recording():
    global _recording
    recorder = _recorder
    _recording = False
    _mic_button.setContentTintColor_(None)
    mic_lock.release(MIC_OWNER)

    def transcribe():
        # Stopping the stream (and the noise-cleanup pass on the captured
        # audio) is the same kind of slow, main-thread-unsafe work as
        # starting it — see the comment in _on_mic_clicked().
        wav_bytes = recorder.stop()
        if not wav_bytes or not recorder.last_had_speech:
            return
        try:
            cfg = config.load()
            transcriber = get_transcriber(
                engine=cfg["engine"],
                model=cfg["model"],
                local_model_size=cfg["local_model_size"],
                api_key=cfg["openrouter_api_key"],
            )
            text = transcriber.transcribe(wav_bytes).strip()
        except Exception as e:
            # This used to fail silently (a bare except: return) — from the
            # user's side that looks exactly like "the mic doesn't work",
            # whatever the actual cause (no API key, no network, a local
            # model that isn't downloaded yet).
            rumps.notification("lite-whisper", "Chat", f"Couldn't transcribe: {e}")
            return
        if text:
            AppHelper.callAfter(_insert_dictated_text, text)

    threading.Thread(target=transcribe, daemon=True).start()


def _insert_dictated_text(text):
    current = str(_input_view.string())
    separator = " " if current and not current.endswith(" ") else ""
    _input_view.setString_(current + separator + text)
    _update_input_height()


def _update_input_height():
    """Grows/shrinks the input row to fit what's typed, clamped to
    [INPUT_TEXT_MIN_HEIGHT, INPUT_TEXT_MAX_HEIGHT] — past the max it just
    scrolls internally instead of continuing to grow. InputDelegate calls
    this on every user edit (textDidChange_); a couple of call sites that
    change the text programmatically (clearing on send, inserting dictated
    text) call it directly since setString_ doesn't fire that delegate
    method itself.
    """
    if _input_view is None or _input_scroll_height is None:
        return
    layout_manager = _input_view.layoutManager()
    container = _input_view.textContainer()
    layout_manager.ensureLayoutForTextContainer_(container)
    used_height = layout_manager.usedRectForTextContainer_(container).size.height
    vertical_inset = _input_view.textContainerInset()[1] * 2.0
    target = max(INPUT_TEXT_MIN_HEIGHT, min(INPUT_TEXT_MAX_HEIGHT, used_height + vertical_inset))
    if abs(_input_scroll_height.constant() - target) > 0.5:
        _input_scroll_height.setConstant_(target)
    # Once the row has hit its max height, further typing scrolls inside it
    # rather than growing it further — without this, the caret can end up
    # below the clipped, visible area (you keep typing but can't see the
    # characters landing), which reads as broken/janky the same way a
    # growing-in-the-wrong-direction box would.
    _input_view.scrollRangeToVisible_(_input_view.selectedRange())


# ---------------------------------------------------------------- send


def _set_sending_ui(streaming):
    """Toggles between the normal "ready to send" state and "a reply is
    streaming in" — swaps the send/stop icon and locks the input row so a
    new message can't be started until the current one is stopped."""
    global _send_state
    _send_state = "streaming" if streaming else "idle"
    _input_view.setEditable_(not streaming)
    _mic_button.setEnabled_(not streaming)
    if streaming:
        _attach_button.setEnabled_(False)
        _set_send_icon(_send_button, "stop.circle.fill", "Stop", NSColor.systemRedColor())
    else:
        # Not just setEnabled_(True) — re-enabling has to respect whether
        # the current model even supports images, or it would silently
        # override _update_attach_availability()'s decision every time a
        # reply finishes streaming.
        _update_attach_availability()
        _set_send_icon(_send_button, "arrow.up.circle.fill", "Send", NSColor.controlAccentColor())


def _on_send_button_clicked():
    if _send_state == "streaming":
        _on_stop_streaming()
    else:
        _on_send()


def _on_stop_streaming():
    if _stream_cancel_event is not None:
        _stream_cancel_event.set()


def _generate_reply(conversation_id):
    """Starts (or restarts) the assistant's turn for `conversation_id`:
    sends the full history so far to chat_engine.stream() and streams the
    reply into the transcript. Shared by a normal send, regenerate, retry-
    after-error and edit-and-resend — they differ only in how the history
    leading up to this point was produced, not in how the reply itself is
    generated and rendered."""
    global _stream_cancel_event

    cfg = config.load()
    convo = next((c for c in _conversations if c["id"] == conversation_id), None)
    model = (convo.get("model") if convo else None) or cfg["chat_model"]
    if not model:
        rumps.notification("lite-whisper", "Chat", "Pick a model first")
        return

    history_for_api = [
        {"role": m["role"], "text": m["text"], "image_paths": m["image_paths"]}
        for m in chat_history.load_messages(conversation_id)
    ]
    if not history_for_api:
        return

    cancel_event = threading.Event()
    _stream_cancel_event = cancel_event
    _set_sending_ui(True)
    _start_streaming_row()

    def call_api():
        accumulated = []
        error = None
        try:
            for chunk in chat_engine.stream(
                history_for_api, model, cfg["openrouter_api_key"], cancel_event
            ):
                accumulated.append(chunk)
                AppHelper.callAfter(_update_streaming_text, "".join(accumulated))
        except Exception as e:
            error = e

        final_text = "".join(accumulated)

        def on_done():
            _set_sending_ui(False)
            if conversation_id == _current_conversation_id:
                if final_text:
                    new_id = chat_history.append_message(conversation_id, "assistant", final_text)
                    _end_streaming_row({
                        "id": new_id,
                        "role": "assistant",
                        "text": final_text,
                        "images": [],
                        "time": datetime.now().strftime("%H:%M"),
                    })
                elif error is not None:
                    _stream_error(str(error))
                else:
                    _end_streaming_row(None)
            elif final_text:
                chat_history.append_message(conversation_id, "assistant", final_text)
            _refresh_sidebar()
            if error is not None:
                rumps.notification("lite-whisper", "Chat error", str(error))

        AppHelper.callAfter(on_done)

    threading.Thread(target=call_api, daemon=True).start()


def _on_send():
    global _pending_images

    if _send_state == "streaming":
        return

    if _current_conversation_id is None:
        _on_new_chat()
    conversation_id = _current_conversation_id

    text = str(_input_view.string()).strip()
    images = list(_pending_images)
    if not text and not images:
        return

    cfg = config.load()
    model = _current_model_id() or cfg["chat_model"]
    if not model:
        rumps.notification("lite-whisper", "Chat", "Pick a model first")
        return

    stored_images = [p for p in (_persist_image(p) for p in images) if p is not None]

    is_first_message = len(chat_history.load_messages(conversation_id)) == 0
    new_id = chat_history.append_message(conversation_id, "user", text, image_paths=stored_images)
    if is_first_message:
        chat_history.rename_conversation(conversation_id, text[:40] if text else "Image")
    chat_history.set_conversation_model(conversation_id, model)

    _input_view.setString_("")
    _update_input_height()
    _pending_images = []
    _attach_button.setToolTip_("Attach image")
    _refresh_sidebar(select_id=conversation_id)

    _call_js("appendMessage", {
        "id": new_id,
        "role": "user",
        "text": text,
        "images": [
            {"data": uri, "name": Path(p).name}
            for p, uri in ((p, _image_data_uri(p)) for p in stored_images)
            if uri
        ],
        "time": datetime.now().strftime("%H:%M"),
    })

    _generate_reply(conversation_id)


# ------------------------------------------------------- bridge actions


def _on_regenerate(message_id):
    if _send_state == "streaming" or _current_conversation_id is None:
        return
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return
    chat_history.delete_messages_from(_current_conversation_id, mid)
    _refresh_messages()
    _generate_reply(_current_conversation_id)


def _on_edit(message_id, text):
    if _send_state == "streaming" or _current_conversation_id is None:
        return
    try:
        mid = int(message_id)
    except (TypeError, ValueError):
        return
    text = (text or "").strip()
    if not text:
        return
    chat_history.update_message_text(mid, text)
    chat_history.delete_messages_after(_current_conversation_id, mid)
    _refresh_messages()
    _generate_reply(_current_conversation_id)


def _on_retry(message_id):
    # The error card that triggered this isn't a real stored message (a
    # failed reply is never persisted — see _generate_reply's on_done), so
    # there's nothing to delete; refreshing just drops that ephemeral row
    # before trying again.
    if _send_state == "streaming" or _current_conversation_id is None:
        return
    _refresh_messages()
    _generate_reply(_current_conversation_id)


# -------------------------------------------------------------------- API


def show():
    global _window
    if _window is None:
        _window = _build_window()
        _refresh_sidebar()
        _on_refresh_chat_models()
        if _conversations:
            _select_conversation(_conversations[0]["id"])
        else:
            _refresh_messages()

    _window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)
    app_activation.note_window_shown("chat")
    chat_bubble.set_active(True)


def hide():
    """Ordered out, not closed — the window (and everything in it: the
    selected conversation, whatever's in the input box) stays exactly as it
    was, ready to reappear via toggle()/show(). windowWillClose_: doesn't
    fire for orderOut_, so the Dock-icon/bubble bookkeeping that a real
    close would trigger has to happen here explicitly."""
    if _window is not None and _window.isVisible():
        _window.orderOut_(None)
        app_activation.note_window_closed("chat")
        chat_bubble.set_active(False)


def toggle():
    """The floating bubble icon's click handler: open it if it isn't
    showing, hide it (not close/quit the conversation) if it already is —
    the bubble is meant for opening/using chat, not for a close button."""
    if _window is not None and _window.isVisible():
        hide()
    else:
        show()
