"""Semantic colors and metrics matching native macOS System Settings.

Every value here is a *dynamic* NSColor: it resolves at draw time, so
light/dark mode and accent-color changes are picked up automatically.
Never call `.CGColor()` on these — that snapshots the color in whatever
appearance happened to be current at the time and freezes it.  Apply them
to views instead (`setFillColor_`, `setTextColor_`, …).
"""

from AppKit import NSColor

# System Settings draws its grouped rows on a translucent *overlay* fill.
# It deliberately does not use controlBackgroundColor, which on macOS 26 is
# byte-identical to windowBackgroundColor and would render the group
# invisible against the page behind it.
GROUP_FILL = NSColor.quaternarySystemFillColor()

TEXT_PRIMARY = NSColor.labelColor()
TEXT_SECONDARY = NSColor.secondaryLabelColor()
TEXT_TERTIARY = NSColor.tertiaryLabelColor()

ACCENT = NSColor.controlAccentColor()
SUCCESS = NSColor.systemGreenColor()
DANGER = NSColor.systemRedColor()

# Metrics lifted from System Settings' own layout.
GROUP_CORNER_RADIUS = 10.0
PAGE_INSET = 20.0
PAGE_MAX_WIDTH = 640.0
ROW_HEIGHT = 38.0
ROW_INSET = 14.0
ROW_PAD_Y = 9.0
ROW_GAP = 10.0
SECTION_GAP = 22.0
