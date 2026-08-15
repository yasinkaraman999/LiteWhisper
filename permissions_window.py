from AppKit import NSImage, NSImageView, NSView

import nsui
import permissions
import theme

BADGE = 15.0

# The order matters: macOS will not surface the Input Monitoring prompt
# until Accessibility is already granted, so the rows are numbered and the
# subtitles say so explicitly.
_STEP_HINTS = {
    "accessibility": "Needed to capture the hotkey and paste text. Grant this one first.",
    "input_monitoring": "Stops Option + Space from leaking into the app underneath.",
    "microphone": "Needed to record your voice.",
}

_rows = {}  # key -> (image_view, status_label)


def _status_badge():
    view = nsui.anchor(NSImageView.alloc().init())
    nsui.activate([
        view.widthAnchor().constraintEqualToConstant_(BADGE),
        view.heightAnchor().constraintEqualToConstant_(BADGE),
    ])
    return view


def build_permissions_page():
    _rows.clear()
    rows = []
    for i, key in enumerate(permissions.status().keys(), start=1):
        badge = _status_badge()
        status = nsui.secondary("", size=11.0)
        # Exact widths keep the badges and buttons in a straight column
        # regardless of how long each status string is.
        nsui.activate([status.widthAnchor().constraintEqualToConstant_(66.0)])
        open_button = nsui.button("Open in Settings", lambda k=key: permissions.open_settings(k))
        nsui.activate([open_button.widthAnchor().constraintEqualToConstant_(118.0)])

        name, _granted = permissions.status()[key]
        rows.append(nsui.row(
            f"{i}. {name}",
            nsui.hstack_control([badge, status, open_button]),
            subtitle=_STEP_HINTS.get(key),
        ))
        _rows[key] = (badge, status)

    actions = nsui.anchor(NSView.alloc().init())
    buttons = nsui.hstack_control([
        nsui.button("Refresh Status", refresh_permissions),
        nsui.button("Reset Permissions", permissions.reset_all_and_relaunch),
        nsui.button("Restart App", permissions.relaunch),
    ])
    actions.addSubview_(buttons)
    nsui.activate([
        buttons.leadingAnchor().constraintEqualToAnchor_(actions.leadingAnchor()),
        buttons.trailingAnchor().constraintLessThanOrEqualToAnchor_(actions.trailingAnchor()),
        buttons.topAnchor().constraintEqualToAnchor_(actions.topAnchor()),
        buttons.bottomAnchor().constraintEqualToAnchor_(actions.bottomAnchor()),
    ])

    return nsui.scroll_page([
        nsui.section(
            "Required Permissions",
            rows,
            footer="Grant them in the order above; each step depends on the one before it.",
        ),
        actions,
    ])


def refresh_permissions():
    for key, (name, granted) in permissions.status().items():
        if key not in _rows:
            continue
        badge, status = _rows[key]
        symbol = "checkmark.circle.fill" if granted else "exclamationmark.circle.fill"
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, name)
        # Template rendering lets contentTintColor drive the symbol color,
        # so it tracks light/dark instead of being baked in.
        image.setTemplate_(True)
        badge.setImage_(image)
        badge.setContentTintColor_(theme.SUCCESS if granted else theme.DANGER)
        status.setStringValue_("Granted" if granted else "Not granted")
