"""Home page, laid out like the top pane of System Settings.

Numbers come straight from history.db; nothing here is illustrative.
"""

from AppKit import (
    NSFont,
    NSFontWeightSemibold,
    NSImage,
    NSImageScaleProportionallyUpOrDown,
    NSImageView,
    NSView,
)

import history
import nsui
import theme
from resources import resource_path

ICON_SIZE = 44.0


def _identity_row():
    """The app-identity row, mirroring the Apple ID row System Settings
    puts above everything else."""
    view = nsui.anchor(NSView.alloc().init())

    icon_view = nsui.anchor(NSImageView.alloc().init())
    icon_view.setImage_(
        NSImage.alloc().initByReferencingFile_(
            resource_path("logos/lite-whisper-no-bg-colored.png")
        )
    )
    icon_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
    view.addSubview_(icon_view)

    name = nsui.label("LiteWhisper", size=15.0, weight=NSFontWeightSemibold)
    tagline = nsui.secondary("Press Option + Space, speak, and it becomes text.", size=12.0)
    text = nsui.vstack([name, tagline], spacing=2.0)
    view.addSubview_(text)

    nsui.activate([
        view.heightAnchor().constraintGreaterThanOrEqualToConstant_(68.0),
        icon_view.leadingAnchor().constraintEqualToAnchor_constant_(
            view.leadingAnchor(), theme.ROW_INSET
        ),
        icon_view.centerYAnchor().constraintEqualToAnchor_(view.centerYAnchor()),
        icon_view.widthAnchor().constraintEqualToConstant_(ICON_SIZE),
        icon_view.heightAnchor().constraintEqualToConstant_(ICON_SIZE),
        text.leadingAnchor().constraintEqualToAnchor_constant_(
            icon_view.trailingAnchor(), 12.0
        ),
        text.trailingAnchor().constraintLessThanOrEqualToAnchor_constant_(
            view.trailingAnchor(), -theme.ROW_INSET
        ),
        text.centerYAnchor().constraintEqualToAnchor_(view.centerYAnchor()),
    ])
    return view


def _value(text):
    field = nsui.label(text, size=13.0, weight=NSFontWeightSemibold)
    field.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(13.0, NSFontWeightSemibold))
    field.setTextColor_(theme.TEXT_SECONDARY)
    return field


def build():
    stats = history.stats()

    summary = nsui.section("Summary", [
        nsui.row("Transcriptions", _value(str(stats["total_count"]))),
        nsui.row("Words", _value(str(stats["total_words"]))),
        nsui.row("Average Speed", _value(f"{stats['avg_wpm']} words/min")),
        nsui.row("Total Cost", _value(f"${stats['total_cost']:.4f}")),
    ])

    getting_started = nsui.section("Getting Started", [
        nsui.row("Start recording", subtitle="In any app, press Option + Space, speak, then press it again."),
        nsui.row("Grant permissions", subtitle="Accessibility, Input Monitoring and Microphone, in that order."),
        nsui.row("Pick an engine", subtitle="Choose the cloud or the fully offline local model under Configuration."),
        nsui.row("Tune your audio", subtitle="Set your microphone and noise reduction strength under Sound."),
    ])

    return nsui.scroll_page([
        nsui.group([_identity_row()]),
        summary,
        getting_started,
    ])
