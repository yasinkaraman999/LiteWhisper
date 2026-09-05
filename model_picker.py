"""A searchable, always alphabetically-sorted model picker.

Drop-in replacement for an NSPopUpButton wherever the list comes from
OpenRouter: that API alone can return several hundred models in whatever
order it feels like, and a plain NSPopUpButton menu has no search and forces
scrolling through all of them by hand. This shows a button with the current
selection; clicking it opens a small popover with a search field on top of
an always-sorted, live-filtered list.

Usage:
    picker = ModelPicker(on_change=lambda model_id: ...)
    row = nsui.row("Cloud model", picker.view)
    ...
    picker.set_models(fetched)   # sorts by name internally
    picker.select(cfg["model"])  # selects by id; shows the raw id until
                                  # set_models() has a matching entry
    picker.selected_id()         # -> str or None, read back on Save
"""

import objc
from AppKit import (
    NSBezelStyleRounded,
    NSBox,
    NSBoxCustom,
    NSButton,
    NSColor,
    NSControlSizeSmall,
    NSFont,
    NSIndexSet,
    NSMaxYEdge,
    NSMinYEdge,
    NSNoTitle,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSScrollView,
    NSSearchField,
    NSTableCellView,
    NSTableColumn,
    NSTableView,
    NSView,
    NSViewController,
)
from Foundation import NSObject
from PyObjCTools import AppHelper

import nsui
import theme
from ui_helpers import ButtonTarget, keep_alive

POPOVER_WIDTH = 360.0
POPOVER_HEIGHT = 320.0
ROW_HEIGHT = 34.0


class _ContentController(NSViewController):
    """Wraps a pre-built view for NSPopover, which insists on a view
    controller rather than taking a view directly."""

    def initWithView_(self, view):
        self = objc.super(_ContentController, self).init()
        if self is None:
            return None
        self._content_view = view
        return self

    def loadView(self):
        self.setView_(self._content_view)


class _PickerDelegate(NSObject):
    """Table datasource/delegate and search field delegate combined — one
    picker, one popover, no need to split this into separate objects."""

    def initWithPicker_(self, picker):
        self = objc.super(_PickerDelegate, self).init()
        if self is None:
            return None
        self._picker = picker
        return self

    def numberOfRowsInTableView_(self, table_view):
        return len(self._picker._filtered)

    def tableView_viewForTableColumn_row_(self, table_view, column, row):
        m = self._picker._filtered[row]
        cell = NSTableCellView.alloc().init()
        badge = " 🖼" if m.get("supports_images") else ""
        title = nsui.label(f"{m['name']}{badge}", size=12.5)
        subtitle = nsui.secondary(m["id"], size=10.0)
        stack = nsui.vstack([title, subtitle], spacing=1.0)
        cell.addSubview_(stack)
        nsui.activate([
            stack.leadingAnchor().constraintEqualToAnchor_constant_(cell.leadingAnchor(), 8.0),
            stack.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                cell.trailingAnchor(), -8.0
            ),
            stack.centerYAnchor().constraintEqualToAnchor_(cell.centerYAnchor()),
        ])
        return cell

    def tableViewSelectionDidChange_(self, notification):
        table = notification.object()
        row = table.selectedRow()
        if 0 <= row < len(self._picker._filtered):
            self._picker._choose(self._picker._filtered[row])

    def controlTextDidChange_(self, notification):
        field = notification.object()
        self._picker._apply_filter(str(field.stringValue()))

    def control_textView_doCommandBySelector_(self, control, text_view, selector):
        if selector == "insertNewline:":
            if self._picker._filtered:
                self._picker._choose(self._picker._filtered[0])
            return True
        if selector in ("moveDown:", "moveUp:"):
            table = self._picker._table
            if table is not None and self._picker._filtered:
                table.window().makeFirstResponder_(table)
                table.selectRowIndexes_byExtendingSelection_(NSIndexSet.indexSetWithIndex_(0), False)
            return True
        return False


class ModelPicker:
    def __init__(self, on_change=None, placeholder="Select a model", compact=False):
        """`compact=True` shrinks the button and its minimum width for use
        inline in an input bar (see chat_window.py) instead of a Settings
        row, and opens the popover upward — a compact picker lives near the
        bottom of its window, where opening downward would show it clipped
        against (or below) the window edge."""
        self.on_change = on_change
        self.placeholder = placeholder
        self.compact = compact
        self._popover_edge = NSMaxYEdge if compact else NSMinYEdge
        self._models = []  # all known models, sorted A-Z by name
        self._filtered = []  # currently visible rows in the open popover
        self._selected = None  # the selected model dict, once known
        self._pending_id = None  # an id selected before its model data arrived

        self._button = nsui.anchor(NSButton.alloc().init())
        self._target = ButtonTarget.alloc().initWithCallback_(lambda _s: self.toggle())
        keep_alive(self._target)
        self._button.setTarget_(self._target)
        self._button.setAction_("clicked:")

        if compact:
            # A native rounded-bezel push button reads as a stray system
            # control sitting in a custom-drawn input pill next to borderless
            # icon buttons — bordered off, sized and centered by hand inside
            # a small themed chip instead, so it matches the rest of the bar.
            self._button.setBordered_(False)
            self._button.setFont_(NSFont.systemFontOfSize_(11.5))
            self._button.setControlSize_(NSControlSizeSmall)

            chip = nsui.anchor(NSBox.alloc().init())
            chip.setBoxType_(NSBoxCustom)
            chip.setTitlePosition_(NSNoTitle)
            chip.setBorderWidth_(1.0)
            chip.setBorderColor_(NSColor.separatorColor())
            chip.setCornerRadius_(12.0)
            chip.setFillColor_(theme.GROUP_FILL)
            chip.setContentViewMargins_((0.0, 0.0))

            inner = nsui.anchor(NSView.alloc().init())
            inner.addSubview_(self._button)
            nsui.activate([
                self._button.leadingAnchor().constraintEqualToAnchor_constant_(inner.leadingAnchor(), 10.0),
                self._button.trailingAnchor().constraintEqualToAnchor_constant_(inner.trailingAnchor(), -10.0),
                self._button.centerYAnchor().constraintEqualToAnchor_(inner.centerYAnchor()),
            ])
            chip.setContentView_(inner)
            nsui.pin(inner, chip)
            nsui.activate([chip.heightAnchor().constraintEqualToConstant_(26.0)])
            self.view = chip
            min_width = 130.0
        else:
            self._button.setBezelStyle_(NSBezelStyleRounded)
            self.view = self._button
            min_width = 220.0

        nsui.activate([self.view.widthAnchor().constraintGreaterThanOrEqualToConstant_(min_width)])
        self._update_title()

        self._delegate = _PickerDelegate.alloc().initWithPicker_(self)
        keep_alive(self._delegate)

        self._popover = None
        self._search = None
        self._table = None

    # -------------------------------------------------------------- data

    def set_models(self, models_list):
        self._models = sorted(models_list or [], key=lambda m: m["name"].lower())
        if self._pending_id is not None:
            match = next((m for m in self._models if m["id"] == self._pending_id), None)
            if match is not None:
                self._selected = match
                self._pending_id = None
        query = str(self._search.stringValue()) if self._search is not None else ""
        self._apply_filter(query)
        self._update_title()

    def select(self, model_id):
        match = next((m for m in self._models if m["id"] == model_id), None)
        if match is not None:
            self._selected = match
            self._pending_id = None
        else:
            self._selected = None
            self._pending_id = model_id
        self._update_title()

    def selected_id(self):
        if self._selected is not None:
            return self._selected["id"]
        return self._pending_id

    # ----------------------------------------------------------- popover

    def toggle(self):
        if self._popover is not None and self._popover.isShown():
            self._popover.close()
            return
        self._show_popover()

    def _show_popover(self):
        container = nsui.anchor(NSView.alloc().init())

        search = nsui.anchor(NSSearchField.alloc().init())
        search.setPlaceholderString_("Search models")
        search.setDelegate_(self._delegate)

        table = NSTableView.alloc().init()
        table.setHeaderView_(None)
        table.setRowHeight_(ROW_HEIGHT)
        table.setBackgroundColor_(NSColor.clearColor())
        column = NSTableColumn.alloc().initWithIdentifier_("model")
        table.addTableColumn_(column)
        table.setDataSource_(self._delegate)
        table.setDelegate_(self._delegate)

        scroll = nsui.anchor(NSScrollView.alloc().init())
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDocumentView_(table)

        container.addSubview_(search)
        container.addSubview_(scroll)
        nsui.activate([
            search.topAnchor().constraintEqualToAnchor_constant_(container.topAnchor(), 8.0),
            search.leadingAnchor().constraintEqualToAnchor_constant_(container.leadingAnchor(), 8.0),
            search.trailingAnchor().constraintEqualToAnchor_constant_(container.trailingAnchor(), -8.0),

            scroll.topAnchor().constraintEqualToAnchor_constant_(search.bottomAnchor(), 6.0),
            scroll.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
            scroll.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
            scroll.bottomAnchor().constraintEqualToAnchor_constant_(container.bottomAnchor(), 0.0),

            container.widthAnchor().constraintEqualToConstant_(POPOVER_WIDTH),
            container.heightAnchor().constraintEqualToConstant_(POPOVER_HEIGHT),
        ])

        self._search = search
        self._table = table
        search.setStringValue_("")
        self._apply_filter("")

        content_controller = _ContentController.alloc().initWithView_(container)
        keep_alive(content_controller)

        popover = NSPopover.alloc().init()
        popover.setContentViewController_(content_controller)
        popover.setBehavior_(NSPopoverBehaviorTransient)
        popover.showRelativeToRect_ofView_preferredEdge_(
            self.view.bounds(), self.view, self._popover_edge
        )
        self._popover = popover

        def focus_search():
            window = search.window()
            if window is not None:
                window.makeFirstResponder_(search)

        AppHelper.callAfter(focus_search)

    def _apply_filter(self, query):
        q = (query or "").strip().lower()
        if q:
            self._filtered = [
                m for m in self._models if q in m["name"].lower() or q in m["id"].lower()
            ]
        else:
            self._filtered = list(self._models)
        if self._table is not None:
            self._table.reloadData()
            if self._selected is not None and self._selected in self._filtered:
                idx = self._filtered.index(self._selected)
                self._table.selectRowIndexes_byExtendingSelection_(
                    NSIndexSet.indexSetWithIndex_(idx), False
                )

    def _choose(self, model):
        self._selected = model
        self._pending_id = None
        self._update_title()
        if self._popover is not None:
            self._popover.close()
        if self.on_change is not None:
            self.on_change(model["id"])

    def _update_title(self):
        if self._selected is not None:
            badge = " 🖼" if self._selected.get("supports_images") else ""
            self._button.setTitle_(f"{self._selected['name']}{badge}  ⌄")
        elif self._pending_id is not None:
            self._button.setTitle_(f"{self._pending_id}  ⌄")
        else:
            self._button.setTitle_(f"{self.placeholder}  ⌄")
