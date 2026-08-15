"""The single LiteWhisper window, built the way macOS builds System Settings.

Structure follows WWDC25 "Build an AppKit app with the new design":

  * The sidebar is an `NSSplitViewItem` created with
    `sidebarWithViewController:`.  AppKit applies the macOS 26 Liquid Glass
    material itself — the session is explicit that putting an
    `NSVisualEffectView` in a sidebar *blocks* that material, so there is
    deliberately none here.
  * The sidebar list is a real source-list `NSTableView`, which gives the
    native rounded selection, hover, and keyboard navigation for free.
  * The content item sets `automaticallyAdjustsSafeAreaInsets`, so content
    extends edge to edge beneath the floating sidebar and the scroll-edge
    effect appears under the toolbar.
"""

import objc
from AppKit import (
    NSApp,
    NSColor,
    NSImageView,
    NSScrollView,
    NSSearchField,
    NSSplitViewController,
    NSSplitViewItem,
    NSTableColumn,
    NSTableCellView,
    NSTableView,
    NSTableViewStyleSourceList,
    NSTitlebarSeparatorStyleNone,
    NSToolbar,
    NSToolbarDisplayModeIconOnly,
    NSToolbarFlexibleSpaceItemIdentifier,
    NSToolbarSidebarTrackingSeparatorItemIdentifier,
    NSToolbarToggleSidebarItemIdentifier,
    NSView,
    NSViewController,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowToolbarStyleUnified,
)
from Foundation import NSIndexSet, NSObject

import history_window
import home_page
import nsui
import permissions_window
import settings_window
from ui_helpers import compose_badge_image, keep_alive

SIDEBAR_MIN_WIDTH = 204.0
SIDEBAR_MAX_WIDTH = 280.0
SIDEBAR_ROW_HEIGHT = 35.0  # taller than the badge, which is what spaces the rows apart
BADGE_SIZE = 20.0
WINDOW_WIDTH = 880.0
WINDOW_HEIGHT = 620.0

# key, sidebar title, SF Symbol, badge tint, page builder, refresh hook
NAV_ITEMS = [
    ("home", "Home", "house.fill", NSColor.systemBlueColor(),
     home_page.build, None),
    ("config", "Configuration", "gearshape.fill", NSColor.systemGrayColor(),
     settings_window.build_configuration_page, settings_window.refresh_all),
    ("sound", "Sound", "speaker.wave.2.fill", NSColor.systemRedColor(),
     settings_window.build_sound_page, settings_window.refresh_all),
    ("overlay", "Recording Window", "waveform", NSColor.systemTealColor(),
     settings_window.build_overlay_page, settings_window.refresh_all),
    ("models", "Model Library", "books.vertical.fill", NSColor.systemOrangeColor(),
     settings_window.build_models_page, settings_window.refresh_all),
    ("history", "History", "clock.arrow.circlepath", NSColor.systemPurpleColor(),
     history_window.build_history_page, history_window.refresh_history),
    ("permissions", "Permissions", "checkmark.shield.fill", NSColor.systemGreenColor(),
     permissions_window.build_permissions_page, permissions_window.refresh_permissions),
]

_ITEMS_BY_KEY = {item[0]: item for item in NAV_ITEMS}

_window = None
_sidebar_controller = None
_content_controller = None


class PageController(NSViewController):
    """Hosts one page's view; rebuilds it on demand for live data."""

    @objc.python_method
    def configure(self, builder, refresh):
        self._builder = builder
        self._refresh = refresh
        self._built = False

    def loadView(self):
        self.setView_(nsui.anchor(NSView.alloc().init()))

    @objc.python_method
    def present(self):
        if not self._built:
            body = nsui.anchor(self._builder())
            self.view().addSubview_(body)
            nsui.pin_page(body, self.view())
            self._built = True
        if self._refresh is not None:
            self._refresh()


class ContentController(NSViewController):
    def loadView(self):
        self.setView_(nsui.anchor(NSView.alloc().init()))

    @objc.python_method
    def show_page(self, controller):
        for child in list(self.childViewControllers()):
            child.view().removeFromSuperview()
            child.removeFromParentViewController()
        self.addChildViewController_(controller)
        page_view = nsui.anchor(controller.view())
        self.view().addSubview_(page_view)
        nsui.pin(page_view, self.view())
        controller.present()


class SidebarController(NSViewController):
    """Source-list sidebar. Owns the search field and the nav table."""

    @objc.python_method
    def configure(self, on_select):
        self._on_select = on_select
        self._visible = list(NAV_ITEMS)
        self._badges = {
            key: compose_badge_image(symbol, color, size=int(BADGE_SIZE))
            for key, _title, symbol, color, _b, _r in NAV_ITEMS
        }

    def loadView(self):
        container = nsui.anchor(NSView.alloc().init())

        self._search = nsui.anchor(NSSearchField.alloc().init())
        self._search.setPlaceholderString_("Search")
        self._search.setTarget_(self)
        self._search.setAction_("searchChanged:")
        # Fires on every keystroke rather than only on Return.
        self._search.setSendsWholeSearchString_(False)
        self._search.setSendsSearchStringImmediately_(True)
        container.addSubview_(self._search)

        self._table = NSTableView.alloc().init()
        self._table.setStyle_(NSTableViewStyleSourceList)
        self._table.setHeaderView_(None)
        self._table.setRowHeight_(SIDEBAR_ROW_HEIGHT)
        self._table.setAllowsEmptySelection_(False)
        self._table.setAllowsMultipleSelection_(False)
        self._table.setBackgroundColor_(NSColor.clearColor())
        column = NSTableColumn.alloc().initWithIdentifier_("nav")
        self._table.addTableColumn_(column)
        self._table.setDataSource_(self)
        self._table.setDelegate_(self)

        scroll = nsui.anchor(NSScrollView.alloc().init())
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setDocumentView_(self._table)
        container.addSubview_(scroll)

        nsui.activate([
            self._search.topAnchor().constraintEqualToAnchor_constant_(
                container.safeAreaLayoutGuide().topAnchor(), 8.0
            ),
            self._search.leadingAnchor().constraintEqualToAnchor_constant_(
                container.leadingAnchor(), 10.0
            ),
            self._search.trailingAnchor().constraintEqualToAnchor_constant_(
                container.trailingAnchor(), -10.0
            ),
            scroll.topAnchor().constraintEqualToAnchor_constant_(
                self._search.bottomAnchor(), 8.0
            ),
            scroll.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
            scroll.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
            scroll.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
        ])

        self.setView_(container)

    # -- search -----------------------------------------------------------

    def searchChanged_(self, sender):
        query = sender.stringValue().strip().lower()
        selected_key = self.selected_key()
        self._visible = [
            item for item in NAV_ITEMS if not query or query in item[1].lower()
        ]
        self._table.reloadData()
        self.select_key(selected_key)

    # -- table data source / delegate -------------------------------------

    def numberOfRowsInTableView_(self, table_view):
        return len(self._visible)

    def tableView_viewForTableColumn_row_(self, table_view, column, row):
        key, title, _symbol, _color, _builder, _refresh = self._visible[row]

        cell = NSTableCellView.alloc().init()

        image_view = nsui.anchor(NSImageView.alloc().init())
        image_view.setImage_(self._badges[key])
        cell.addSubview_(image_view)
        cell.setImageView_(image_view)

        text_field = nsui.label(title)
        cell.addSubview_(text_field)
        # Handing AppKit the text field is what makes it recolor the label
        # when the row is selected, instead of it staying dark on the
        # accent-colored highlight.
        cell.setTextField_(text_field)

        nsui.activate([
            image_view.leadingAnchor().constraintEqualToAnchor_constant_(
                cell.leadingAnchor(), 4.0
            ),
            image_view.centerYAnchor().constraintEqualToAnchor_(cell.centerYAnchor()),
            image_view.widthAnchor().constraintEqualToConstant_(BADGE_SIZE),
            image_view.heightAnchor().constraintEqualToConstant_(BADGE_SIZE),
            text_field.leadingAnchor().constraintEqualToAnchor_constant_(
                image_view.trailingAnchor(), 8.0
            ),
            text_field.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
                cell.trailingAnchor(), -4.0
            ),
            text_field.centerYAnchor().constraintEqualToAnchor_(cell.centerYAnchor()),
        ])
        return cell

    def tableViewSelectionDidChange_(self, notification):
        key = self.selected_key()
        if key is not None:
            self._on_select(key)

    # -- helpers ----------------------------------------------------------

    @objc.python_method
    def selected_key(self):
        row = self._table.selectedRow()
        if 0 <= row < len(self._visible):
            return self._visible[row][0]
        return None

    @objc.python_method
    def select_key(self, key):
        index = 0
        for i, item in enumerate(self._visible):
            if item[0] == key:
                index = i
                break
        if self._visible:
            self._table.selectRowIndexes_byExtendingSelection_(
                NSIndexSet.indexSetWithIndex_(index), False
            )


class ToolbarDelegate(NSObject):
    """Minimal toolbar so the window gets the unified macOS 26 titlebar.

    Every identifier is system-supplied: because the window's content view
    controller is an `NSSplitViewController`, AppKit builds these items
    itself, wires the toggle to the sidebar, and pins the tracking
    separator to the sidebar divider.
    """

    _IDENTIFIERS = [
        NSToolbarToggleSidebarItemIdentifier,
        NSToolbarSidebarTrackingSeparatorItemIdentifier,
        NSToolbarFlexibleSpaceItemIdentifier,
    ]

    def toolbarAllowedItemIdentifiers_(self, toolbar):
        return self._IDENTIFIERS

    def toolbarDefaultItemIdentifiers_(self, toolbar):
        return self._IDENTIFIERS

    def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(
        self, toolbar, identifier, will_be_inserted
    ):
        # Required by NSToolbarDelegate. Omitting it makes AppKit reject the
        # delegate outright and build *no* items at all, which leaves the
        # title field unpositioned behind the window buttons. Returning nil
        # is correct here — these are all standard identifiers.
        return None


def _build_window():
    global _sidebar_controller, _content_controller

    _sidebar_controller = SidebarController.alloc().init()
    _sidebar_controller.configure(show_page)

    _content_controller = ContentController.alloc().init()

    split = NSSplitViewController.alloc().init()

    sidebar_item = NSSplitViewItem.sidebarWithViewController_(_sidebar_controller)
    sidebar_item.setMinimumThickness_(SIDEBAR_MIN_WIDTH)
    sidebar_item.setMaximumThickness_(SIDEBAR_MAX_WIDTH)
    sidebar_item.setAllowsFullHeightLayout_(True)
    sidebar_item.setTitlebarSeparatorStyle_(NSTitlebarSeparatorStyleNone)
    split.addSplitViewItem_(sidebar_item)

    content_item = NSSplitViewItem.splitViewItemWithViewController_(_content_controller)
    # Lets the content sit beneath the floating sidebar glass; AppKit then
    # feeds the overlap back through the safe area layout guides.
    content_item.setAutomaticallyAdjustsSafeAreaInsets_(True)
    content_item.setTitlebarSeparatorStyle_(NSTitlebarSeparatorStyleNone)
    split.addSplitViewItem_(content_item)

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
    # Assigning a content view controller resizes the window to the
    # controller's fitting size, so the intended size is applied after.
    window.setContentSize_((WINDOW_WIDTH, WINDOW_HEIGHT))
    window.setReleasedWhenClosed_(False)
    window.setTitle_("LiteWhisper")
    window.setMinSize_((720, 480))

    toolbar_delegate = ToolbarDelegate.alloc().init()
    keep_alive(toolbar_delegate)
    toolbar = NSToolbar.alloc().initWithIdentifier_("LiteWhisperToolbar")
    toolbar.setDelegate_(toolbar_delegate)
    toolbar.setAllowsUserCustomization_(False)
    # Icon only: the default mode captions the sidebar toggle with the word
    # "Sidebar", which no system app shows.
    toolbar.setDisplayMode_(NSToolbarDisplayModeIconOnly)
    window.setToolbar_(toolbar)
    window.setToolbarStyle_(NSWindowToolbarStyleUnified)

    return window


_page_controllers = {}


def _page_controller(key):
    if key not in _page_controllers:
        _key, _title, _symbol, _color, builder, refresh = _ITEMS_BY_KEY[key]
        controller = PageController.alloc().init()
        controller.configure(builder, refresh)
        _page_controllers[key] = controller
    return _page_controllers[key]


def show_page(key):
    if key not in _ITEMS_BY_KEY or _content_controller is None:
        return
    _content_controller.show_page(_page_controller(key))
    if _window is not None:
        # System Settings names the pane you are looking at in the titlebar
        # rather than repeating the app name, so the title tracks the page.
        _window.setTitle_(_ITEMS_BY_KEY[key][1])


def show(page="home"):
    global _window
    if _window is None:
        _window = _build_window()
        _window.center()

    _sidebar_controller.select_key(page)
    show_page(page)
    _window.makeKeyAndOrderFront_(None)
    NSApp.activateIgnoringOtherApps_(True)
