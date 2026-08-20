import objc
from AppKit import NSBezierPath, NSColor, NSImage, NSMakeRect
from Foundation import NSObject

_handlers = []  # keeps control targets alive for the process lifetime


class ButtonTarget(NSObject):
    def initWithCallback_(self, callback):
        self = objc.super(ButtonTarget, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def clicked_(self, sender):
        self._callback(sender)


def keep_alive(target):
    """Prevents a PyObjC target object from being garbage-collected once its
    Python reference goes out of scope (AppKit holds targets weakly)."""
    _handlers.append(target)


class WindowCloseObserver(NSObject):
    """A minimal NSWindowDelegate that only cares about windowWillClose:.

    Shared by any window that wants a callback when it closes — e.g. to
    drop out of app_activation's open-window set — without every such
    window needing its own delegate class.
    """

    def initWithCallback_(self, callback):
        self = objc.super(WindowCloseObserver, self).init()
        if self is None:
            return None
        self._callback = callback
        return self

    def windowWillClose_(self, notification):
        self._callback()


BADGE_TILE_WHITE = 0.94
BADGE_TILE_ALPHA = 0.92


def compose_badge_image(symbol_name, tint_color, size=20):
    """A rounded-square icon badge baked into one NSImage: an off-white tile
    carrying a tinted glyph.

    Returned flat rather than as a view so it can be handed to
    `NSTableCellView.imageView` — which is what lets the source list style
    and lay it out natively. Being a bitmap, it is composed once at startup
    and does not re-render on an appearance change, so the tile is kept
    slightly translucent to sit acceptably on either sidebar material.
    """
    symbol = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        symbol_name, symbol_name
    )

    def draw(rect):
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, rect.size.width * 0.25, rect.size.height * 0.25
        )
        NSColor.colorWithCalibratedWhite_alpha_(
            BADGE_TILE_WHITE, BADGE_TILE_ALPHA
        ).setFill()
        path.fill()

        inset = rect.size.width * 0.22
        tint_color.set()
        symbol.drawInRect_(
            NSMakeRect(inset, inset, rect.size.width - inset * 2, rect.size.height - inset * 2)
        )
        return True

    return NSImage.imageWithSize_flipped_drawingHandler_((size, size), False, draw)
