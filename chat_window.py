"""The ChatGPT-style mini chat window.

Runs independently of Option+Space / Shift+Option+Space dictation — the only
things it shares with them are the microphone (see mic_lock.py) and the
OpenRouter API key. Conversations and messages persist via chat_history.py;
images are copied into config.CHAT_IMAGES_DIR and referenced by path.
"""

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
    NSControlSizeSmall,
    NSEvent,
    NSEventModifierFlagShift,
    NSFont,
    NSFontWeightMedium,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageSymbolConfiguration,
    NSImageView,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSModalResponseOK,
    NSNoBorder,
    NSNoTitle,
    NSOpenPanel,
    NSPopUpButton,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
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
from Foundation import NSIndexSet, NSObject
from PyObjCTools import AppHelper

import app_activation
import chat_engine
import chat_history
import config
import mic_lock
import models
import nsui
import theme
from audio_recorder import AudioRecorder
from main_window import ToolbarDelegate
from transcriber import get_transcriber
from ui_helpers import ButtonTarget, WindowCloseObserver, keep_alive

WINDOW_WIDTH = 880.0
WINDOW_HEIGHT = 640.0
SIDEBAR_MIN_WIDTH = 180.0
SIDEBAR_MAX_WIDTH = 260.0
BUBBLE_MAX_WIDTH = 560.0
THUMBNAIL_SIZE = 72.0
INPUT_HEIGHT = 76.0
ICON_BUTTON_SIZE = 34.0
ICON_GLYPH_POINT_SIZE = 16.0
MIC_OWNER = "chat"
IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"]

_window = None
_sidebar_table = None
_sidebar_delegate = None
_conversations = []  # cached rows from chat_history.list_conversations()
_current_conversation_id = None
_message_body = None
_message_scroll = None
_model_popup = None
_available_models = []  # [{"id", "name", "supports_images"}]
_input_view = None
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
_streaming_row = None
_streaming_label = None
_streaming_spinner = None


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


def _set_icon(button, symbol, tooltip):
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, tooltip)
    configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        ICON_GLYPH_POINT_SIZE, NSFontWeightMedium
    )
    button.setImage_(image.imageWithSymbolConfiguration_(configuration))
    button.setToolTip_(tooltip)


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


class ChatSidebarController(NSViewController):
    """Hosts the conversation list as a real NSSplitViewItem sidebar —
    draggable, resizable and collapsible via the toolbar, the same as the
    main window's — instead of a fixed-width plain view."""

    def loadView(self):
        self.setView_(_build_sidebar())


class ChatMainController(NSViewController):
    """Hosts the model row, message list and input row."""

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


def _build_model_row():
    global _model_popup

    _model_popup = nsui.anchor(NSPopUpButton.alloc().init())
    _model_popup.setPullsDown_(False)
    refresh = nsui.button("Refresh", _on_refresh_chat_models)

    row = nsui.anchor(NSView.alloc().init())
    row.addSubview_(_model_popup)
    row.addSubview_(refresh)

    nsui.activate([
        _model_popup.leadingAnchor().constraintEqualToAnchor_constant_(row.leadingAnchor(), 12.0),
        _model_popup.topAnchor().constraintEqualToAnchor_constant_(row.topAnchor(), 8.0),
        _model_popup.bottomAnchor().constraintEqualToAnchor_constant_(row.bottomAnchor(), -8.0),

        refresh.leadingAnchor().constraintEqualToAnchor_constant_(_model_popup.trailingAnchor(), 8.0),
        refresh.centerYAnchor().constraintEqualToAnchor_(_model_popup.centerYAnchor()),
        refresh.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(row.trailingAnchor(), -12.0),
    ])
    return row


def _build_input_row():
    global _input_view, _attach_button, _mic_button, _send_button

    _attach_button = _icon_button("paperclip", _on_attach_image, tooltip="Attach image")
    _mic_button = _icon_button("mic.fill", _on_mic_clicked, tooltip="Dictate")
    _send_button = _icon_button("arrow.up.circle.fill", _on_send_button_clicked, tooltip="Send")

    # NSTextView as an NSScrollView's documentView is sized the classic
    # autoresizing way, not via Auto Layout constraints on the text view
    # itself — skipping this configuration is what left the previous
    # version with a degenerate frame that couldn't actually be typed into.
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

    # The whole row is one rounded pill (attach + mic + text + send all
    # inside it), the way ChatGPT's own input field is built, rather than
    # icon buttons sitting outside a separately boxed text field.
    row = nsui.anchor(NSBox.alloc().init())
    row.setBoxType_(NSBoxCustom)
    row.setTitlePosition_(NSNoTitle)
    row.setBorderWidth_(0.0)
    row.setCornerRadius_(18.0)
    row.setFillColor_(theme.GROUP_FILL)
    row.setContentViewMargins_((0.0, 0.0))

    content = nsui.anchor(NSView.alloc().init())
    for view in (_attach_button, _mic_button, input_scroll, _send_button):
        content.addSubview_(view)

    nsui.activate([
        _attach_button.leadingAnchor().constraintEqualToAnchor_constant_(content.leadingAnchor(), 8.0),
        _attach_button.centerYAnchor().constraintEqualToAnchor_(content.centerYAnchor()),

        _mic_button.leadingAnchor().constraintEqualToAnchor_constant_(_attach_button.trailingAnchor(), 2.0),
        _mic_button.centerYAnchor().constraintEqualToAnchor_(content.centerYAnchor()),

        input_scroll.leadingAnchor().constraintEqualToAnchor_constant_(_mic_button.trailingAnchor(), 6.0),
        input_scroll.topAnchor().constraintEqualToAnchor_constant_(content.topAnchor(), 8.0),
        input_scroll.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), -8.0),

        _send_button.leadingAnchor().constraintEqualToAnchor_constant_(input_scroll.trailingAnchor(), 6.0),
        _send_button.trailingAnchor().constraintEqualToAnchor_constant_(content.trailingAnchor(), -8.0),
        _send_button.centerYAnchor().constraintEqualToAnchor_(content.centerYAnchor()),
    ])

    row.setContentView_(content)
    nsui.pin(content, row)
    return row


def _build_main_pane():
    global _message_scroll, _message_body

    model_row = _build_model_row()
    _message_scroll, _message_body = nsui.scroll_body([], spacing=10.0)
    input_row = _build_input_row()

    container = nsui.anchor(NSView.alloc().init())
    container.addSubview_(model_row)
    container.addSubview_(_message_scroll)
    container.addSubview_(input_row)

    nsui.activate([
        model_row.topAnchor().constraintEqualToAnchor_(container.safeAreaLayoutGuide().topAnchor()),
        model_row.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
        model_row.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),

        _message_scroll.topAnchor().constraintEqualToAnchor_(model_row.bottomAnchor()),
        _message_scroll.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
        _message_scroll.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
        _message_scroll.bottomAnchor().constraintEqualToAnchor_(input_row.topAnchor()),

        input_row.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
        input_row.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
        input_row.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
        input_row.heightAnchor().constraintEqualToConstant_(INPUT_HEIGHT),
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

    close_observer = WindowCloseObserver.alloc().initWithCallback_(
        lambda: app_activation.note_window_closed("chat")
    )
    keep_alive(close_observer)
    window.setDelegate_(close_observer)

    window.center()
    return window


# --------------------------------------------------------------- messages


def _empty_state():
    return nsui.section(None, [
        nsui.row(
            "No messages yet",
            subtitle="Ask a quick question, or dictate one with the mic button.",
        ),
    ])


def _thumbnail(path):
    view = nsui.anchor(NSImageView.alloc().init())
    image = NSImage.alloc().initWithContentsOfFile_(str(path))
    if image is not None:
        view.setImage_(image)
    view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    nsui.activate([
        view.widthAnchor().constraintEqualToConstant_(THUMBNAIL_SIZE),
        view.heightAnchor().constraintEqualToConstant_(THUMBNAIL_SIZE),
    ])
    return view


def _aligned_row(inner, is_user):
    """Wraps `inner` in a full-width row, pinned to the leading edge for
    assistant messages or the trailing edge for the user's own — the
    iMessage-style alignment that tells the two apart at a glance."""
    row = nsui.anchor(NSView.alloc().init())
    row.addSubview_(inner)
    if is_user:
        edge = inner.trailingAnchor().constraintEqualToAnchor_(row.trailingAnchor())
        other = inner.leadingAnchor().constraintGreaterThanOrEqualToAnchor_(row.leadingAnchor())
    else:
        edge = inner.leadingAnchor().constraintEqualToAnchor_(row.leadingAnchor())
        other = inner.trailingAnchor().constraintLessThanOrEqualToAnchor_(row.trailingAnchor())
    nsui.activate([
        edge,
        other,
        inner.topAnchor().constraintEqualToAnchor_(row.topAnchor()),
        inner.bottomAnchor().constraintEqualToAnchor_(row.bottomAnchor()),
    ])
    return row


def _text_row(message, is_user):
    if is_user:
        # ChatGPT tints only the user's own messages as a bubble.
        when = _parse(message["created_at"])
        meta = when.strftime("%H:%M") if when else ""
        tint = NSColor.controlAccentColor().colorWithAlphaComponent_(0.18)
        inner = nsui.bubble(message["text"], meta=meta, tint=tint)
    else:
        # Assistant replies are plain text, no bubble — matches ChatGPT.
        inner = nsui.label(message["text"], size=14.0, multiline=True)
        inner.setSelectable_(True)
    nsui.activate([inner.widthAnchor().constraintLessThanOrEqualToConstant_(BUBBLE_MAX_WIDTH)])
    return _aligned_row(inner, is_user)


def _image_row(image_paths, is_user):
    thumbs = nsui.hstack_control([_thumbnail(p) for p in image_paths], spacing=6.0)
    return _aligned_row(thumbs, is_user)


def _refresh_messages():
    if _message_body is None:
        return
    if _current_conversation_id is None:
        nsui.set_arranged(_message_body, [_empty_state()])
        return

    messages = chat_history.load_messages(_current_conversation_id)
    if not messages:
        nsui.set_arranged(_message_body, [_empty_state()])
        return

    views = []
    for message in messages:
        is_user = message["role"] == "user"
        if message.get("image_paths"):
            views.append(_image_row(message["image_paths"], is_user))
        if message.get("text"):
            views.append(_text_row(message, is_user))
    nsui.set_arranged(_message_body, views)

    if views:
        AppHelper.callAfter(views[-1].scrollRectToVisible_, views[-1].bounds())


# ------------------------------------------------------ streaming replies


def _start_streaming_row():
    """Appends a placeholder assistant row — a small spinner plus an empty
    label — once, when a send begins. Unlike _refresh_messages(), this
    doesn't rebuild the whole list: the label is mutated directly as
    chunks arrive, so streaming text updates stay cheap."""
    global _streaming_row, _streaming_label, _streaming_spinner
    if _message_body is None:
        return

    spinner = nsui.anchor(NSProgressIndicator.alloc().init())
    spinner.setStyle_(NSProgressIndicatorStyleSpinning)
    spinner.setControlSize_(NSControlSizeSmall)
    spinner.setDisplayedWhenStopped_(False)
    spinner.startAnimation_(None)

    label = nsui.label("", size=14.0, multiline=True)

    inner = nsui.anchor(NSView.alloc().init())
    inner.addSubview_(spinner)
    inner.addSubview_(label)
    nsui.activate([
        spinner.leadingAnchor().constraintEqualToAnchor_(inner.leadingAnchor()),
        spinner.centerYAnchor().constraintEqualToAnchor_(inner.centerYAnchor()),
        spinner.topAnchor().constraintGreaterThanOrEqualToAnchor_(inner.topAnchor()),

        label.leadingAnchor().constraintEqualToAnchor_constant_(spinner.trailingAnchor(), 8.0),
        label.trailingAnchor().constraintLessThanOrEqualToAnchor_(inner.trailingAnchor()),
        label.topAnchor().constraintEqualToAnchor_(inner.topAnchor()),
        label.bottomAnchor().constraintEqualToAnchor_(inner.bottomAnchor()),
    ])
    nsui.activate([inner.widthAnchor().constraintLessThanOrEqualToConstant_(BUBBLE_MAX_WIDTH)])

    row = _aligned_row(inner, is_user=False)
    nsui.set_arranged(_message_body, list(_message_body.arrangedSubviews()) + [row])

    _streaming_row = row
    _streaming_label = label
    _streaming_spinner = spinner
    AppHelper.callAfter(row.scrollRectToVisible_, row.bounds())


def _update_streaming_text(text):
    if _streaming_label is None:
        return
    if text and _streaming_spinner is not None:
        _streaming_spinner.stopAnimation_(None)
        _streaming_spinner.setHidden_(True)
    _streaming_label.setStringValue_(text)
    if _streaming_row is not None:
        _streaming_row.scrollRectToVisible_(_streaming_row.bounds())


def _end_streaming_row():
    global _streaming_row, _streaming_label, _streaming_spinner
    _streaming_row = None
    _streaming_label = None
    _streaming_spinner = None


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
    _model_popup.removeAllItems()
    for m in _available_models:
        badge = " 🖼" if m.get("supports_images") else ""
        _model_popup.addItemWithTitle_(f"{m['name']}{badge}  —  {m['id']}")
    ids = [m["id"] for m in _available_models]
    if selected_id in ids:
        _model_popup.selectItemAtIndex_(ids.index(selected_id))
    elif selected_id:
        _model_popup.addItemWithTitle_(selected_id)
        _model_popup.selectItemAtIndex_(_model_popup.numberOfItems() - 1)


def _current_model_id():
    index = _model_popup.indexOfSelectedItem()
    if 0 <= index < len(_available_models):
        return _available_models[index]["id"]
    return None


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
    _recorder = AudioRecorder()
    _recorder.start()
    _recording = True
    _mic_button.setContentTintColor_(NSColor.systemRedColor())


def _stop_chat_recording():
    global _recording
    recorder = _recorder
    wav_bytes = recorder.stop()
    _recording = False
    _mic_button.setContentTintColor_(None)
    mic_lock.release(MIC_OWNER)

    def transcribe():
        try:
            if not wav_bytes or not recorder.last_had_speech:
                return
            cfg = config.load()
            transcriber = get_transcriber(
                engine=cfg["engine"],
                model=cfg["model"],
                local_model_size=cfg["local_model_size"],
                api_key=cfg["openrouter_api_key"],
            )
            text = transcriber.transcribe(wav_bytes).strip()
        except Exception:
            return
        if text:
            AppHelper.callAfter(_insert_dictated_text, text)

    threading.Thread(target=transcribe, daemon=True).start()


def _insert_dictated_text(text):
    current = str(_input_view.string())
    separator = " " if current and not current.endswith(" ") else ""
    _input_view.setString_(current + separator + text)


# ---------------------------------------------------------------- send


def _set_sending_ui(streaming):
    """Toggles between the normal "ready to send" state and "a reply is
    streaming in" — swaps the send/stop icon and locks the input row so a
    new message can't be started until the current one is stopped."""
    global _send_state
    _send_state = "streaming" if streaming else "idle"
    _input_view.setEditable_(not streaming)
    _attach_button.setEnabled_(not streaming)
    _mic_button.setEnabled_(not streaming)
    if streaming:
        _set_icon(_send_button, "stop.circle.fill", "Stop")
    else:
        _set_icon(_send_button, "arrow.up.circle.fill", "Send")


def _on_send_button_clicked():
    if _send_state == "streaming":
        _on_stop_streaming()
    else:
        _on_send()


def _on_stop_streaming():
    if _stream_cancel_event is not None:
        _stream_cancel_event.set()


def _on_send():
    global _pending_images, _stream_cancel_event

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
    chat_history.append_message(conversation_id, "user", text, image_paths=stored_images)
    if is_first_message:
        chat_history.rename_conversation(conversation_id, text[:40] if text else "Image")
    chat_history.set_conversation_model(conversation_id, model)

    _input_view.setString_("")
    _pending_images = []
    _attach_button.setToolTip_("Attach image")
    _refresh_sidebar(select_id=conversation_id)
    _refresh_messages()

    history_for_api = [
        {"role": m["role"], "text": m["text"], "image_paths": m["image_paths"]}
        for m in chat_history.load_messages(conversation_id)
    ]

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
            _end_streaming_row()
            _set_sending_ui(False)
            if final_text:
                chat_history.append_message(conversation_id, "assistant", final_text)
            if conversation_id == _current_conversation_id:
                _refresh_messages()
            _refresh_sidebar()
            if error is not None:
                rumps.notification("lite-whisper", "Chat error", str(error))

        AppHelper.callAfter(on_done)

    threading.Thread(target=call_api, daemon=True).start()


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
