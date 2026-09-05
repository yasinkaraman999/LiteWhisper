from datetime import date, datetime

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleWarning,
    NSBox,
    NSBoxCustom,
    NSButton,
    NSColor,
    NSFontWeightSemibold,
    NSImage,
    NSNoTitle,
    NSTextAlignmentCenter,
    NSTimer,
    NSTrackingActiveInKeyWindow,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSView,
)

import history
import nsui
import output
import theme
from ui_helpers import ButtonTarget, keep_alive

BUBBLE_GAP = 8.0

CHIP_RADIUS = 9.0
COPY_BUTTON_SIZE = 22.0
COPY_FEEDBACK_SECONDS = 1.2

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_body = None


def build_history_page():
    global _body
    scroll, _body = nsui.scroll_body([], spacing=BUBBLE_GAP)
    return scroll


def _parse(raw):
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _day_title(when):
    if when is None:
        return "Unknown date"
    today = date.today()
    delta = (today - when.date()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return f"{MONTHS[when.month - 1]} {when.day}, {when.year}"


def _meta_text(entry, when):
    parts = []
    if entry.get("model"):
        parts.append(entry["model"])
    usage = entry.get("usage") or {}
    if "cost" in usage:
        parts.append(f"${usage['cost']:.5f}")
    if "seconds" in usage:
        parts.append(f"{usage['seconds']}s")
    if "total_tokens" in usage:
        parts.append(f"{usage['total_tokens']} token")
    if when is not None:
        parts.append(when.strftime("%H:%M"))
    return " · ".join(parts)


class BubbleView(NSView):
    """A chat bubble that reveals a copy button while the pointer is over it."""

    @objc.python_method
    def configure(self, text, copy_button):
        self._text = text
        self._copy_button = copy_button
        self._copy_button.setHidden_(True)

    def updateTrackingAreas(self):
        objc.super(BubbleView, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        # inVisibleRect keeps the area correct as the list scrolls, without
        # having to recompute it on every layout pass.
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                self.bounds(),
                NSTrackingMouseEnteredAndExited
                | NSTrackingActiveInKeyWindow
                | NSTrackingInVisibleRect,
                self,
                None,
            )
        )

    def mouseEntered_(self, event):
        self._copy_button.setHidden_(False)

    def mouseExited_(self, event):
        self._copy_button.setHidden_(True)

    @objc.python_method
    def copy_text(self):
        output.copy(self._text)
        self._show_copied()

    @objc.python_method
    def _show_copied(self):
        self._set_symbol("checkmark")
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            COPY_FEEDBACK_SECONDS, self, "restoreIcon:", None, False
        )

    def restoreIcon_(self, timer):
        self._set_symbol("doc.on.doc")

    @objc.python_method
    def _set_symbol(self, name):
        self._copy_button.setImage_(
            NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, "Copy")
        )


def _copy_button():
    button = nsui.anchor(NSButton.alloc().init())
    button.setImage_(
        NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "doc.on.doc", "Copy"
        )
    )
    button.setBordered_(False)
    button.setToolTip_("Copy text")
    button.setContentTintColor_(theme.TEXT_SECONDARY)
    nsui.activate([
        button.widthAnchor().constraintEqualToConstant_(COPY_BUTTON_SIZE),
        button.heightAnchor().constraintEqualToConstant_(COPY_BUTTON_SIZE),
    ])
    return button


def _bubble(entry):
    """One transcription, drawn as a full-width chat bubble."""
    when = _parse(entry["timestamp"])

    # An accent tint marks these as the user's own dictations, the way a
    # messaging app tints sent messages. Rebuilt on every visit to the page,
    # so a change of accent or appearance is picked up. The trailing gutter
    # reserves room so the first line never runs underneath the copy button
    # that appears on hover.
    box = nsui.bubble(
        entry["text"],
        meta=_meta_text(entry, when),
        tint=NSColor.controlAccentColor().colorWithAlphaComponent_(0.18),
        trailing_gutter=COPY_BUTTON_SIZE,
    )

    copy_button = _copy_button()

    row = nsui.anchor(BubbleView.alloc().init())
    row.addSubview_(box)
    row.addSubview_(copy_button)
    row.configure(entry["text"], copy_button)

    target = ButtonTarget.alloc().initWithCallback_(lambda _sender, r=row: r.copy_text())
    keep_alive(target)
    copy_button.setTarget_(target)
    copy_button.setAction_("clicked:")

    nsui.activate([
        box.topAnchor().constraintEqualToAnchor_(row.topAnchor()),
        box.bottomAnchor().constraintEqualToAnchor_(row.bottomAnchor()),
        box.leadingAnchor().constraintEqualToAnchor_(row.leadingAnchor()),
        box.trailingAnchor().constraintEqualToAnchor_(row.trailingAnchor()),
        copy_button.topAnchor().constraintEqualToAnchor_constant_(box.topAnchor(), 5.0),
        copy_button.trailingAnchor().constraintEqualToAnchor_constant_(
            box.trailingAnchor(), -5.0
        ),
    ])
    return row


def _date_chip(title):
    """The centred day marker between groups of bubbles."""
    label = nsui.secondary(title, size=10.0)
    label.setAlignment_(NSTextAlignmentCenter)

    box = nsui.anchor(NSBox.alloc().init())
    box.setBoxType_(NSBoxCustom)
    box.setTitlePosition_(NSNoTitle)
    box.setBorderWidth_(0.0)
    box.setCornerRadius_(CHIP_RADIUS)
    box.setFillColor_(theme.GROUP_FILL)
    box.setContentViewMargins_((0.0, 0.0))

    holder = nsui.anchor(NSView.alloc().init())
    holder.addSubview_(label)
    nsui.activate([
        label.topAnchor().constraintEqualToAnchor_constant_(holder.topAnchor(), 3.0),
        label.bottomAnchor().constraintEqualToAnchor_constant_(holder.bottomAnchor(), -3.0),
        label.leadingAnchor().constraintEqualToAnchor_constant_(holder.leadingAnchor(), 10.0),
        label.trailingAnchor().constraintEqualToAnchor_constant_(holder.trailingAnchor(), -10.0),
    ])
    box.setContentView_(holder)
    nsui.pin(holder, box)

    row = nsui.anchor(NSView.alloc().init())
    row.addSubview_(box)
    nsui.activate([
        box.topAnchor().constraintEqualToAnchor_constant_(row.topAnchor(), 6.0),
        box.bottomAnchor().constraintEqualToAnchor_constant_(row.bottomAnchor(), -2.0),
        box.centerXAnchor().constraintEqualToAnchor_(row.centerXAnchor()),
    ])
    return row


def _header(count, total_cost):
    """Summary on the left, the destructive action on the right."""
    view = nsui.anchor(NSView.alloc().init())

    summary = nsui.label(
        f"{count} entries · ${total_cost:.4f}", size=13.0, weight=NSFontWeightSemibold
    )
    clear = nsui.button("Clear History", _on_clear)
    view.addSubview_(summary)
    view.addSubview_(clear)

    nsui.activate([
        summary.leadingAnchor().constraintEqualToAnchor_(view.leadingAnchor()),
        summary.centerYAnchor().constraintEqualToAnchor_(view.centerYAnchor()),
        clear.trailingAnchor().constraintEqualToAnchor_(view.trailingAnchor()),
        clear.topAnchor().constraintEqualToAnchor_(view.topAnchor()),
        clear.bottomAnchor().constraintEqualToAnchor_(view.bottomAnchor()),
        clear.leadingAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
            summary.trailingAnchor(), 12.0
        ),
    ])
    return view


def _on_clear():
    alert = NSAlert.alloc().init()
    alert.setAlertStyle_(NSAlertStyleWarning)
    alert.setMessageText_("Clear all history?")
    alert.setInformativeText_(
        "Every saved transcription is permanently deleted. This cannot be undone."
    )
    alert.addButtonWithTitle_("Delete")
    alert.addButtonWithTitle_("Cancel")
    # Escape backs out; without this the cancel button has no key equivalent
    # because AppKit only wires that up for a button titled "Cancel".
    alert.buttons()[1].setKeyEquivalent_("\033")

    if alert.runModal() != NSAlertFirstButtonReturn:
        return

    history.clear()
    refresh_history()


def _empty_state():
    return nsui.section(None, [
        nsui.row(
            "No transcriptions yet",
            subtitle="Press Option + Space and speak; your transcriptions collect here.",
        )
    ])


def refresh_history():
    if _body is None:
        return

    entries = list(reversed(history.load()))
    if not entries:
        nsui.set_arranged(_body, [_empty_state()])
        return

    total_cost = sum((e.get("usage") or {}).get("cost", 0.0) for e in entries)
    views = [_header(len(entries), total_cost)]

    current_day = None
    for entry in entries:
        when = _parse(entry["timestamp"])
        day = when.date() if when else None
        if day != current_day:
            views.append(_date_chip(_day_title(when)))
            current_day = day
        views.append(_bubble(entry))

    nsui.set_arranged(_body, views)
