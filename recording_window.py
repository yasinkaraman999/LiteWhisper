"""The floating recording overlay.

A borderless, non-activating `NSPanel` that sits above every other window
(including full-screen apps) and never takes focus, so recording never
interrupts whatever the user is typing into.

Three states drive one custom-drawn meter:

  idle        a flat resting line (only ever visible with "always show")
  recording   a live waveform scrolling right to left off the mic level
  processing  a travelling swell, so the wait for transcription reads as
              work in progress rather than a freeze
"""

import math
from collections import deque

import objc
from AppKit import (
    NSBezierPath,
    NSButton,
    NSColor,
    NSEvent,
    NSFont,
    NSFontWeightMedium,
    NSGlassEffectView,
    NSImage,
    NSImageSymbolConfiguration,
    NSMakePoint,
    NSMakeRect,
    NSPanel,
    NSPointInRect,
    NSScreen,
    NSShadow,
    NSTextField,
    NSTimer,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)

import config
from ui_helpers import ButtonTarget, keep_alive

STYLE_CLASSIC = "classic"
STYLE_MINI = "mini"
STYLE_NONE = "none"

CLASSIC_SIZE = (300.0, 88.0)
MINI_SIZE = (146.0, 38.0)
CORNER_RADIUS = 16.0
SCREEN_MARGIN = 18.0

# Transparent slack around the glass so its shadow has somewhere to fall
# off. Without it the shadow is clipped at the window edge and reads as a
# hard dark outline rather than a soft drop.
SHADOW_PADDING = 18.0

# Red through violet. Stopping short of a full turn keeps the last bar from
# looping back to the red it started on.
RAINBOW_HUE_SPAN = 0.82

# Visual gain on the bars. Normal speech measures around half of the meter's
# range, so drawing it one to one leaves the bars looking flat; this lifts
# them without touching the level itself, which stays a true 0-1 reading.
METER_GAIN = 1.3

FPS = 30.0
HISTORY_LENGTH = 56  # waveform samples kept on screen

_panel = None
_meter = None
_hint_label = None
_title_label = None
_state = "idle"
_stop_callback = None
_live_stop_callback = None
_style = STYLE_CLASSIC


# ---------------------------------------------------------------- docking


def _dock_points(size):
    """Centre point for every slot in the frame of docks around the screen."""
    screen = NSScreen.mainScreen()
    if screen is None:
        return {}
    area = screen.visibleFrame()
    width, height = size
    half_w, half_h = width / 2.0, height / 2.0

    points = {}
    for slot in range(config.DOCK_SLOTS):
        fraction = (slot + 0.5) / config.DOCK_SLOTS
        x = area.origin.x + area.size.width * fraction
        y = area.origin.y + area.size.height * fraction
        points[f"top-{slot}"] = (
            x, area.origin.y + area.size.height - SCREEN_MARGIN - half_h,
        )
        points[f"bottom-{slot}"] = (x, area.origin.y + SCREEN_MARGIN + half_h)
        points[f"left-{slot}"] = (area.origin.x + SCREEN_MARGIN + half_w, y)
        points[f"right-{slot}"] = (
            area.origin.x + area.size.width - SCREEN_MARGIN - half_w, y,
        )
    return points


def _current_size():
    """Size of the visible glass box."""
    return MINI_SIZE if _style == STYLE_MINI else CLASSIC_SIZE


def _panel_size():
    """Size of the window, which is the glass plus its transparent shadow slack."""
    width, height = _current_size()
    return (width + SHADOW_PADDING * 2, height + SHADOW_PADDING * 2)


def _move_to_dock(dock_name, animate=False):
    # Docking is measured against the visible box, so the shadow padding
    # does not push the panel away from the screen edge.
    points = _dock_points(_current_size())
    center = points.get(dock_name) or points.get(config.DEFAULTS["recording_window_dock"])
    if center is None or _panel is None:
        return
    width, height = _panel_size()
    frame = NSMakeRect(center[0] - width / 2.0, center[1] - height / 2.0, width, height)
    _panel.setFrame_display_animate_(frame, True, animate)


def _nearest_dock():
    """The dock slot closest to where the panel was dropped."""
    frame = _panel.frame()
    cx = frame.origin.x + frame.size.width / 2.0
    cy = frame.origin.y + frame.size.height / 2.0
    best, best_distance = None, None
    for name, (px, py) in _dock_points(_current_size()).items():
        distance = (px - cx) ** 2 + (py - cy) ** 2
        if best_distance is None or distance < best_distance:
            best, best_distance = name, distance
    return best


# ------------------------------------------------------------------ views


class DragView(NSView):
    """The panel background. Dragging it anywhere re-docks the overlay."""

    def initWithFrame_(self, frame):
        self = objc.super(DragView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._drag_origin = None
        return self

    def hitTest_(self, point):
        # The window is larger than the glass by SHADOW_PADDING on every
        # side. That slack is invisible, so it must also be untouchable —
        # otherwise a click just outside the panel would be swallowed here
        # instead of reaching the app underneath.
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
        return objc.super(DragView, self).hitTest_(point)

    def mouseDown_(self, event):
        frame = self.window().frame()
        location = NSEvent.mouseLocation()
        self._drag_origin = (location.x - frame.origin.x, location.y - frame.origin.y)

    def mouseDragged_(self, event):
        if self._drag_origin is None:
            return
        location = NSEvent.mouseLocation()
        self.window().setFrameOrigin_(
            NSMakePoint(location.x - self._drag_origin[0], location.y - self._drag_origin[1])
        )

    def mouseUp_(self, event):
        if self._drag_origin is None:
            return
        self._drag_origin = None
        dock = _nearest_dock()
        if dock is None:
            return
        cfg = config.load()
        cfg["recording_window_dock"] = dock
        config.save(cfg)
        _move_to_dock(dock, animate=True)


class MeterView(NSView):
    """Draws the waveform, the resting line, and the processing swell."""

    def initWithFrame_(self, frame):
        self = objc.super(MeterView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._levels = deque([0.0] * HISTORY_LENGTH, maxlen=HISTORY_LENGTH)
        self._level_source = None
        self._phase = 0.0
        return self

    @objc.python_method
    def set_level_source(self, source):
        self._level_source = source

    @objc.python_method
    def reset(self):
        self._levels = deque([0.0] * HISTORY_LENGTH, maxlen=HISTORY_LENGTH)
        self._phase = 0.0

    def tick_(self, timer):
        if _state in ("recording", "live"):
            level = self._level_source() if self._level_source else 0.0
            self._levels.append(level)
        elif _state == "processing":
            self._phase += 0.22
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        bounds = self.bounds()
        if _state in ("recording", "live"):
            self._draw_waveform(bounds)
        elif _state == "processing":
            self._draw_swell(bounds)
        else:
            self._draw_resting_line(bounds)

    @objc.python_method
    def _draw_resting_line(self, bounds):
        NSColor.secondaryLabelColor().setStroke()
        line = NSBezierPath.bezierPath()
        line.setLineWidth_(2.0)
        line.setLineCapStyle_(1)  # round
        mid = bounds.size.height / 2.0
        line.moveToPoint_(NSMakePoint(2.0, mid))
        line.lineToPoint_(NSMakePoint(bounds.size.width - 2.0, mid))
        line.stroke()

    @objc.python_method
    def _draw_waveform(self, bounds):
        count = len(self._levels)
        if not count:
            return
        gap = 2.0
        bar_width = max(2.0, (bounds.size.width - gap * (count - 1)) / count)
        mid = bounds.size.height / 2.0
        max_half = mid - 2.0

        for i, level in enumerate(self._levels):
            # Hue is fixed to the bar's position rather than its height, so
            # the rainbow stays put across the meter while the bars move —
            # tying hue to level instead would make the whole strip flash.
            hue = (i / max(1, count - 1)) * RAINBOW_HUE_SPAN
            NSColor.colorWithHue_saturation_brightness_alpha_(
                hue, 0.95, 1.0, 1.0
            ).setFill()

            # Clamped so a loud peak tops out at the meter's edge instead of
            # drawing past it.
            half = max(1.0, min(1.0, level * METER_GAIN) * max_half)
            x = i * (bar_width + gap)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, mid - half, bar_width, half * 2.0),
                bar_width / 2.0,
                bar_width / 2.0,
            )
            path.fill()

    @objc.python_method
    def _draw_swell(self, bounds):
        """Three offset sine waves, which read as water rather than a spinner."""
        width, height = bounds.size.width, bounds.size.height
        mid = height / 2.0
        amplitude = min(mid - 3.0, height * 0.3)

        for wavelength, speed, alpha in (
            (1.0, 1.0, 0.95), (1.7, -0.6, 0.55), (2.4, 0.35, 0.35)
        ):
            NSColor.whiteColor().colorWithAlphaComponent_(alpha).setStroke()
            path = NSBezierPath.bezierPath()
            path.setLineWidth_(2.0)
            path.setLineCapStyle_(1)
            steps = int(width)
            for step in range(steps + 1):
                x = float(step)
                t = x / width * math.pi * 2.0 * wavelength
                # Tapering the ends keeps the strokes from clipping flat
                # against the panel edges.
                envelope = math.sin(x / width * math.pi)
                y = mid + math.sin(t + self._phase * speed) * amplitude * envelope
                point = NSMakePoint(x, y)
                if step == 0:
                    path.moveToPoint_(point)
                else:
                    path.lineToPoint_(point)
            path.stroke()


# ---------------------------------------------------------------- building


def _stop_button(symbol, size):
    button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, "Stop")
    configuration = NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        size * 0.62, NSFontWeightMedium
    )
    button.setImage_(image.imageWithSymbolConfiguration_(configuration))
    button.setBordered_(False)
    button.setContentTintColor_(NSColor.systemRedColor())
    target = ButtonTarget.alloc().initWithCallback_(lambda _sender: _on_stop_clicked())
    keep_alive(target)
    button.setTarget_(target)
    button.setAction_("clicked:")
    return button


def _label(text, size, color):
    field = NSTextField.alloc().init()
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(NSFont.systemFontOfSize_weight_(size, NSFontWeightMedium))
    field.setTextColor_(color)
    return field


def _build_panel():
    global _panel, _meter, _hint_label, _title_label

    width, height = _current_size()
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
    # AppKit's own window shadow traces the window rectangle, which around a
    # rounded translucent panel shows up as a hard dark border. The glass
    # carries a soft layer shadow of its own instead.
    panel.setHasShadow_(False)
    panel.setHidesOnDeactivate_(False)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setMovableByWindowBackground_(False)  # handled by DragView, so drops can snap
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
    )

    background = DragView.alloc().initWithFrame_(
        NSMakeRect(0, 0, panel_width, panel_height)
    )
    panel.setContentView_(background)

    # The controls go inside the glass rather than on top of it: WWDC25 is
    # explicit that glass must own its content, not sit behind it as a
    # sibling, or it cannot shade what it is covering.
    content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))

    if _style == STYLE_MINI:
        _meter = MeterView.alloc().initWithFrame_(NSMakeRect(12, 9, width - 52, height - 18))
        content.addSubview_(_meter)
        stop = _stop_button("stop.circle.fill", 22)
        stop.setFrame_(NSMakeRect(width - 32, (height - 22) / 2.0, 22, 22))
        content.addSubview_(stop)
        _title_label = None
        _hint_label = None
    else:
        _title_label = _label("Recording", 12.0, NSColor.labelColor())
        _title_label.setFrame_(NSMakeRect(16, height - 28, width - 60, 16))
        content.addSubview_(_title_label)

        stop = _stop_button("stop.circle.fill", 24)
        stop.setFrame_(NSMakeRect(width - 40, height - 32, 24, 24))
        content.addSubview_(stop)

        _meter = MeterView.alloc().initWithFrame_(NSMakeRect(16, 26, width - 32, 26))
        content.addSubview_(_meter)

        _hint_label = _label(
            "\u2325Space to stop \u00b7 esc to cancel", 10.0, NSColor.secondaryLabelColor()
        )
        _hint_label.setFrame_(NSMakeRect(16, 8, width - 32, 14))
        content.addSubview_(_hint_label)

    glass = _glass_view(NSMakeRect(SHADOW_PADDING, SHADOW_PADDING, width, height))
    glass.setContentView_(content)
    background.addSubview_(glass)

    _panel = panel
    _move_to_dock(config.load()["recording_window_dock"])
    return panel


_timer = None


def _set_animating(animating):
    """Run the redraw timer only while something actually moves.

    The overlay can sit on screen all day in "always show" mode, and the
    resting line is static, so an unconditional 30fps timer would wake the
    CPU forever for nothing.
    """
    global _timer
    if animating and _timer is None and _meter is not None:
        _timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / FPS, _meter, "tick:", None, True
        )
    elif not animating and _timer is not None:
        _timer.invalidate()
        _timer = None


def _glass_view(frame):
    """The Liquid Glass backing macOS 26 uses for floating elements."""
    glass = NSGlassEffectView.alloc().initWithFrame_(frame)
    glass.setCornerRadius_(CORNER_RADIUS)
    glass.setWantsLayer_(True)

    # Wide and soft, offset only slightly downward: a diffuse lift off the
    # desktop rather than an outline hugging the panel.
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(NSColor.blackColor().colorWithAlphaComponent_(0.30))
    shadow.setShadowBlurRadius_(16.0)
    shadow.setShadowOffset_((0.0, -3.0))
    glass.setShadow_(shadow)
    return glass


def _on_stop_clicked():
    callback = _live_stop_callback if _state == "live" else _stop_callback
    if callback is not None:
        callback()


# ------------------------------------------------------------------- API


def configure(stop_callback, level_source, live_stop_callback=None):
    """Wire the overlay to the app. Called once at startup."""
    global _stop_callback, _live_stop_callback
    _stop_callback = stop_callback
    _live_stop_callback = live_stop_callback
    _rebuild_if_needed(level_source)


_level_source = None


def _rebuild_if_needed(level_source=None):
    """Rebuild the panel when the configured style changes.

    Classic and mini differ in size and contents, so a style change means a
    new panel rather than a reflow.
    """
    global _panel, _style, _level_source
    if level_source is not None:
        _level_source = level_source

    style = config.load()["recording_window_style"]
    if style == STYLE_NONE:
        if _panel is not None:
            _panel.orderOut_(None)
        _style = style
        return

    if _panel is not None and style == _style:
        return

    if _panel is not None:
        # The timer retains the old meter, so it must go before the rebuild.
        _set_animating(False)
        _panel.orderOut_(None)
        _panel = None

    _style = style
    _build_panel()
    if _meter is not None and _level_source is not None:
        _meter.set_level_source(_level_source)


def set_state(state):
    """Move the overlay to "idle", "recording" or "processing".

    Must run on the main thread; callers on worker threads should hand this
    to AppHelper.callAfter.
    """
    global _state
    _state = state

    cfg = config.load()
    if cfg["recording_window_style"] == STYLE_NONE:
        _set_animating(False)
        if _panel is not None:
            _panel.orderOut_(None)
        return

    _rebuild_if_needed()
    if _panel is None:
        return

    if state in ("recording", "live") and _meter is not None:
        _meter.reset()

    if _title_label is not None:
        _title_label.setStringValue_(
            {
                "recording": "Recording",
                "live": "Live",
                "processing": "Transcribing...",
            }.get(state, "Ready")
        )
    if _hint_label is not None:
        if state == "live":
            hint = "\u21e7\u2325Space to stop \u00b7 esc to cancel"
        elif state == "recording":
            hint = "\u2325Space to stop \u00b7 esc to cancel"
        else:
            hint = "\u2325Space to start \u00b7 \u21e7\u2325Space for live"
        _hint_label.setStringValue_(hint)

    if state == "idle" and not cfg["recording_window_always_show"]:
        _set_animating(False)
        _panel.orderOut_(None)
    else:
        _move_to_dock(cfg["recording_window_dock"])
        _panel.orderFrontRegardless()
        _set_animating(state in ("recording", "live", "processing"))
        if _meter is not None:
            _meter.setNeedsDisplay_(True)


def refresh_from_config():
    """Re-apply settings after the user saves them."""
    _rebuild_if_needed()
    set_state(_state)
