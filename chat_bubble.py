"""The persistent floating chat icon.

A small always-on-top circular button, docked and draggable the same way
the recording overlay is (see overlay_dock.py), but with its own dock key
(`chat_bubble_dock`) so the two floating panels don't fight over position.
A click that didn't drag opens the chat window.
"""

import objc
from AppKit import (
    NSColor,
    NSEvent,
    NSFontWeightMedium,
    NSGlassEffectView,
    NSImage,
    NSImageSymbolConfiguration,
    NSImageView,
    NSMakePoint,
    NSMakeRect,
    NSPanel,
    NSPointInRect,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Quartz import CGPathCreateWithEllipseInRect, CGRectMake

import config
import overlay_dock

ICON_SIZE = 44.0
CORNER_RADIUS = ICON_SIZE / 2.0

# Transparent slack around the glass so its shadow has somewhere to fall
# off — same reasoning as recording_window.py's SHADOW_PADDING.
SHADOW_PADDING = 14.0

# Pixels of movement before a mouse-down/up counts as a drag rather than a
# click, so a slightly shaky click still opens the chat window.
DRAG_THRESHOLD = 4.0

_panel = None
_on_click = None
_image_view = None
_active = False

IDLE_SYMBOL = "bubble.left.and.bubble.right"
ACTIVE_SYMBOL = "bubble.left.and.bubble.right.fill"


def _panel_size():
    return (ICON_SIZE + SHADOW_PADDING * 2, ICON_SIZE + SHADOW_PADDING * 2)


def _move_to_dock(dock_name, animate=False):
    points = overlay_dock.dock_points((ICON_SIZE, ICON_SIZE))
    center = points.get(dock_name) or points.get(config.DEFAULTS["chat_bubble_dock"])
    if center is None or _panel is None:
        return
    width, height = _panel_size()
    frame = NSMakeRect(center[0] - width / 2.0, center[1] - height / 2.0, width, height)
    _panel.setFrame_display_animate_(frame, True, animate)


def _nearest_dock():
    frame = _panel.frame()
    cx = frame.origin.x + frame.size.width / 2.0
    cy = frame.origin.y + frame.size.height / 2.0
    return overlay_dock.nearest_dock((cx, cy), (ICON_SIZE, ICON_SIZE))


class BubbleIconView(NSView):
    """The draggable circular icon."""

    def initWithFrame_(self, frame):
        self = objc.super(BubbleIconView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._drag_origin = None
        self._moved = False
        return self

    def hitTest_(self, point):
        # The window is larger than the visible icon by SHADOW_PADDING on
        # every side; that slack must stay click-through.
        local = self.convertPoint_fromView_(point, self.superview())
        bounds = self.bounds()
        visible = NSMakeRect(
            SHADOW_PADDING,
            SHADOW_PADDING,
            bounds.size.width - SHADOW_PADDING * 2,
            bounds.size.height - SHADOW_PADDING * 2,
        )
        if not NSPointInRect(local, visible):
            return None
        return objc.super(BubbleIconView, self).hitTest_(point)

    def mouseDown_(self, event):
        frame = self.window().frame()
        location = NSEvent.mouseLocation()
        self._drag_origin = (location.x - frame.origin.x, location.y - frame.origin.y)
        self._moved = False

    def mouseDragged_(self, event):
        if self._drag_origin is None:
            return
        location = NSEvent.mouseLocation()
        new_origin = NSMakePoint(
            location.x - self._drag_origin[0], location.y - self._drag_origin[1]
        )
        current = self.window().frame().origin
        if (
            abs(new_origin.x - current.x) > DRAG_THRESHOLD
            or abs(new_origin.y - current.y) > DRAG_THRESHOLD
        ):
            self._moved = True
        self.window().setFrameOrigin_(new_origin)

    def mouseUp_(self, event):
        if self._drag_origin is None:
            return
        self._drag_origin = None
        if not self._moved:
            if _on_click is not None:
                _on_click()
            return
        dock = _nearest_dock()
        if dock is None:
            return
        cfg = config.load()
        cfg["chat_bubble_dock"] = dock
        config.save(cfg)
        _move_to_dock(dock, animate=True)


def _glass_view(frame):
    glass = NSGlassEffectView.alloc().initWithFrame_(frame)
    glass.setCornerRadius_(CORNER_RADIUS)
    glass.setWantsLayer_(True)
    return glass


def _shadow_view(frame):
    """A separate, invisible view sitting behind the glass purely to cast a
    shadow — the glass view's own layer clips to its rounded corners
    (masksToBounds), and a shadow is drawn *outside* those bounds, so
    setting it on the glass layer directly clips the shadow away (or, via
    the NSView-level setShadow: API used previously, falls back to a
    shadow that follows the layer's square bounds instead of the visible
    circle — either way it read as an unpolished, boxy smudge rather than
    the soft round drop shadow every native floating macOS control has).

    Only setWantsLayer_ happens here — configuring the CALayer's shadow
    properties has to wait until *after* this view is in the window's view
    hierarchy (see _apply_shadow below): AppKit swaps in its own backing
    layer once a layer-backed view is parented, which silently resets
    shadowOpacity (though not shadowRadius/shadowPath, oddly) back to 0 if
    it was set beforehand.
    """
    view = NSView.alloc().initWithFrame_(frame)
    view.setWantsLayer_(True)
    return view


def _apply_shadow(view):
    frame = view.frame()
    layer = view.layer()
    layer.setShadowColor_(NSColor.blackColor().CGColor())
    layer.setShadowOpacity_(0.28)
    layer.setShadowRadius_(9.0)
    layer.setShadowOffset_((0.0, -2.0))
    layer.setShadowPath_(
        CGPathCreateWithEllipseInRect(
            CGRectMake(0.0, 0.0, frame.size.width, frame.size.height), None
        )
    )


def _build_panel():
    global _panel

    panel_width, panel_height = _panel_size()
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, panel_width, panel_height),
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        2,
        False,
    )
    panel.setLevel_(25)  # NSStatusWindowLevel: above normal and floating windows
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setMovableByWindowBackground_(False)  # handled by BubbleIconView, so drops can snap
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )

    background = BubbleIconView.alloc().initWithFrame_(NSMakeRect(0, 0, panel_width, panel_height))
    panel.setContentView_(background)

    icon_frame = NSMakeRect(SHADOW_PADDING, SHADOW_PADDING, ICON_SIZE, ICON_SIZE)
    shadow_view = _shadow_view(icon_frame)
    background.addSubview_(shadow_view)
    _apply_shadow(shadow_view)

    content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON_SIZE, ICON_SIZE))
    image_view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON_SIZE, ICON_SIZE))
    content.addSubview_(image_view)

    global _image_view
    _image_view = image_view

    glass = _glass_view(icon_frame)
    glass.setContentView_(content)
    background.addSubview_(glass)

    _panel = panel
    _move_to_dock(config.load()["chat_bubble_dock"])
    _apply_icon()
    return panel


def _apply_icon():
    if _image_view is None:
        return
    symbol = ACTIVE_SYMBOL if _active else IDLE_SYMBOL
    icon = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "Chat")
    configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        ICON_SIZE * 0.42, NSFontWeightMedium
    )
    _image_view.setImage_(icon.imageWithSymbolConfiguration_(configuration))
    # Colored while Chat is open, muted gray otherwise — a second, more
    # visible cue than the filled-vs-outline glyph swap alone, so it reads
    # at a glance even at a small size or in peripheral vision.
    _image_view.setContentTintColor_(
        NSColor.controlAccentColor() if _active else NSColor.secondaryLabelColor()
    )


# ------------------------------------------------------------------- API


def configure(on_click):
    """Wire the bubble to the app. Called once at startup."""
    global _on_click
    _on_click = on_click
    set_visible(config.load()["chat_bubble_visible"])


def set_visible(visible):
    if not visible:
        if _panel is not None:
            _panel.orderOut_(None)
        return
    if _panel is None:
        _build_panel()
    _panel.orderFrontRegardless()


def refresh_from_config():
    """Re-apply settings (visibility, dock position) after Settings saves."""
    cfg = config.load()
    set_visible(cfg["chat_bubble_visible"])
    if cfg["chat_bubble_visible"]:
        _move_to_dock(cfg["chat_bubble_dock"])


def set_active(active):
    """Swaps the icon to its filled variant while the Chat window is open —
    otherwise the bubble looks identical whether or not you already have
    Chat open, the "icon doesn't change" complaint. chat_window.py calls
    this from show() and from its window-close observer."""
    global _active
    if active == _active:
        return
    _active = active
    _apply_icon()
