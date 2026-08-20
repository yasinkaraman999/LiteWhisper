"""Shared screen-dock geometry for floating panels.

Both the recording overlay and the chat bubble snap to the same frame of
slots around the screen edges — five along each edge — so this pure
geometry (no panel/window state) is shared rather than duplicated.
"""

from AppKit import NSScreen

import config

SCREEN_MARGIN = 18.0


def dock_points(visible_size, margin=SCREEN_MARGIN):
    """Centre point for every slot in the frame of docks around the screen,
    for a panel of the given (width, height)."""
    screen = NSScreen.mainScreen()
    if screen is None:
        return {}
    area = screen.visibleFrame()
    width, height = visible_size
    half_w, half_h = width / 2.0, height / 2.0

    points = {}
    for slot in range(config.DOCK_SLOTS):
        fraction = (slot + 0.5) / config.DOCK_SLOTS
        x = area.origin.x + area.size.width * fraction
        y = area.origin.y + area.size.height * fraction
        points[f"top-{slot}"] = (x, area.origin.y + area.size.height - margin - half_h)
        points[f"bottom-{slot}"] = (x, area.origin.y + margin + half_h)
        points[f"left-{slot}"] = (area.origin.x + margin + half_w, y)
        points[f"right-{slot}"] = (area.origin.x + area.size.width - margin - half_w, y)
    return points


def nearest_dock(center, visible_size, margin=SCREEN_MARGIN):
    """The dock slot closest to `center` (cx, cy) — e.g. where a panel was
    dropped."""
    cx, cy = center
    best, best_distance = None, None
    for name, (px, py) in dock_points(visible_size, margin).items():
        distance = (px - cx) ** 2 + (py - cy) ** 2
        if best_distance is None or distance < best_distance:
            best, best_distance = name, distance
    return best
