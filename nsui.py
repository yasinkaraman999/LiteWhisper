"""Constraint-based building blocks shaped like native System Settings.

Nothing in here uses hard-coded frames: WWDC25's "Build an AppKit app with
the new design" calls out fixed control heights as the main thing that
breaks under the macOS 26 design, because system controls changed size.
Everything is Auto Layout so the window is genuinely resizable and the
controls keep whatever metrics the OS gives them.
"""

from AppKit import (
    NSBezelStyleRounded,
    NSBox,
    NSBoxCustom,
    NSBoxSeparator,
    NSButton,
    NSFont,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSLayoutAttributeLeading,
    NSLayoutConstraint,
    NSLayoutConstraintOrientationHorizontal,
    NSLayoutPriorityDefaultLow,
    NSLayoutPriorityRequired,
    NSLineBreakByWordWrapping,
    NSNoTitle,
    NSScrollView,
    NSStackView,
    NSSwitchButton,
    NSTextField,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
)

import theme
from ui_helpers import ButtonTarget, keep_alive

# Kept as a module constant so a future AppKit rename degrades loudly here
# rather than silently drawing the wrong thing.
_SEPARATOR_BOX_TYPE = NSBoxSeparator


class FlippedView(NSView):
    """A view whose origin is top-left.

    AppKit's default origin is bottom-left, which inside an NSScrollView
    means a document shorter than the viewport sinks to the bottom and a
    taller one opens scrolled to its end. Flipping it makes content start
    at the top and read downward, the way every settings pane does.
    """

    def isFlipped(self):
        return True


def anchor(view):
    """Opt a view into Auto Layout."""
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return view


def activate(constraints):
    NSLayoutConstraint.activateConstraints_(constraints)


def pin(child, parent, inset=0.0, top=None, leading=None, trailing=None, bottom=None):
    """Pin all four edges of `child` to `parent`, per-edge insets optional."""
    top = inset if top is None else top
    leading = inset if leading is None else leading
    trailing = inset if trailing is None else trailing
    bottom = inset if bottom is None else bottom
    activate([
        child.topAnchor().constraintEqualToAnchor_constant_(parent.topAnchor(), top),
        child.leadingAnchor().constraintEqualToAnchor_constant_(parent.leadingAnchor(), leading),
        child.trailingAnchor().constraintEqualToAnchor_constant_(parent.trailingAnchor(), -trailing),
        child.bottomAnchor().constraintEqualToAnchor_constant_(parent.bottomAnchor(), -bottom),
    ])


def pin_page(child, parent):
    """Pin a page body inside a content pane.

    Horizontally the body stops at the safe area, so it never slides under
    the floating glass sidebar.  Vertically it runs to the window edge, so
    it scrolls beneath the toolbar and picks up the macOS 26 scroll-edge
    effect; the scroll view's automatic content insets keep the first row
    clear of the titlebar.
    """
    guide = parent.safeAreaLayoutGuide()
    activate([
        child.topAnchor().constraintEqualToAnchor_(parent.topAnchor()),
        child.bottomAnchor().constraintEqualToAnchor_(parent.bottomAnchor()),
        child.leadingAnchor().constraintEqualToAnchor_(guide.leadingAnchor()),
        child.trailingAnchor().constraintEqualToAnchor_(guide.trailingAnchor()),
    ])


def label(text, size=13.0, weight=NSFontWeightRegular, color=None, multiline=False):
    field = anchor(NSTextField.alloc().init())
    field.setStringValue_(text)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(theme.TEXT_PRIMARY if color is None else color)
    if multiline:
        field.setLineBreakMode_(NSLineBreakByWordWrapping)
        field.cell().setWraps_(True)
        field.setMaximumNumberOfLines_(0)
        # Let the field grow vertically instead of stretching horizontally.
        field.setContentCompressionResistancePriority_forOrientation_(
            NSLayoutPriorityDefaultLow, 0
        )
    return field


def secondary(text, size=11.0, multiline=False):
    return label(text, size=size, color=theme.TEXT_SECONDARY, multiline=multiline)


def heading(text):
    """A section heading, as System Settings draws above a group."""
    return label(text, size=13.0, weight=NSFontWeightSemibold)


def button(title, callback, default=False):
    btn = anchor(NSButton.alloc().init())
    btn.setTitle_(title)
    btn.setBezelStyle_(NSBezelStyleRounded)
    if default:
        btn.setKeyEquivalent_("\r")
    target = ButtonTarget.alloc().initWithCallback_(lambda _sender: callback())
    keep_alive(target)
    btn.setTarget_(target)
    btn.setAction_("clicked:")
    return btn


def checkbox(callback=None):
    box = anchor(NSButton.alloc().init())
    box.setButtonType_(NSSwitchButton)
    box.setTitle_("")
    if callback is not None:
        target = ButtonTarget.alloc().initWithCallback_(lambda _sender: callback())
        keep_alive(target)
        box.setTarget_(target)
        box.setAction_("clicked:")
    return box


def vstack(views, spacing=theme.ROW_GAP):
    """Vertical stack whose arranged subviews all span the full width.

    NSStackView's `alignment` cannot stretch children in its own axis, so
    the width is matched explicitly — otherwise rows collapse to their
    intrinsic size and the groups look ragged.
    """
    stack = anchor(NSStackView.alloc().init())
    stack.setOrientation_(NSUserInterfaceLayoutOrientationVertical)
    stack.setSpacing_(spacing)
    stack.setAlignment_(NSLayoutAttributeLeading)
    set_arranged(stack, views)
    return stack


def set_arranged(stack, views):
    """Replace a vertical stack's contents, re-applying the width match."""
    for existing in list(stack.arrangedSubviews()):
        stack.removeArrangedSubview_(existing)
        existing.removeFromSuperview()
    for view in views:
        anchor(view)
        stack.addArrangedSubview_(view)
        activate([view.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor())])


def separator():
    """A hairline inset to the row text, the way System Settings insets it."""
    holder = anchor(NSView.alloc().init())
    line = anchor(NSBox.alloc().init())
    line.setBoxType_(_SEPARATOR_BOX_TYPE)
    holder.addSubview_(line)
    activate([
        holder.heightAnchor().constraintEqualToConstant_(1.0),
        line.leadingAnchor().constraintEqualToAnchor_constant_(
            holder.leadingAnchor(), theme.ROW_INSET
        ),
        line.trailingAnchor().constraintEqualToAnchor_(holder.trailingAnchor()),
        line.centerYAnchor().constraintEqualToAnchor_(holder.centerYAnchor()),
    ])
    return holder


def row(title, control=None, subtitle=None, stretch=False):
    """One System Settings row: text on the left, control on the right.

    The text lives in its own column with a *definite* trailing edge.  That
    matters for the subtitle: anchoring it to the title's own trailing edge
    would size it to the title's intrinsic width, so a short title like
    "tiny" would wrap its subtitle into a 27pt ribbon.

    `stretch` lets a control (a text field, say) take the leftover width
    instead of hugging its intrinsic size the way a popup should.
    """
    view = anchor(NSView.alloc().init())

    column = anchor(NSView.alloc().init())
    view.addSubview_(column)

    title_field = label(title)
    text_fields = [title_field]
    if subtitle:
        text_fields.append(secondary(subtitle, multiline=True))

    previous = None
    for field in text_fields:
        column.addSubview_(field)
        edges = [
            field.leadingAnchor().constraintEqualToAnchor_(column.leadingAnchor()),
            field.trailingAnchor().constraintEqualToAnchor_(column.trailingAnchor()),
        ]
        if previous is None:
            edges.append(field.topAnchor().constraintEqualToAnchor_(column.topAnchor()))
        else:
            edges.append(
                field.topAnchor().constraintEqualToAnchor_constant_(
                    previous.bottomAnchor(), 3.0
                )
            )
        activate(edges)
        previous = field
    activate([previous.bottomAnchor().constraintEqualToAnchor_(column.bottomAnchor())])

    constraints = [
        column.leadingAnchor().constraintEqualToAnchor_constant_(
            view.leadingAnchor(), theme.ROW_INSET
        ),
        column.topAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
            view.topAnchor(), theme.ROW_PAD_Y
        ),
        column.bottomAnchor().constraintLessThanOrEqualToAnchor_constant_(
            view.bottomAnchor(), -theme.ROW_PAD_Y
        ),
        column.centerYAnchor().constraintEqualToAnchor_(view.centerYAnchor()),
        view.heightAnchor().constraintGreaterThanOrEqualToConstant_(theme.ROW_HEIGHT),
    ]

    if control is None:
        constraints.append(
            column.trailingAnchor().constraintEqualToAnchor_constant_(
                view.trailingAnchor(), -theme.ROW_INSET
            )
        )
    else:
        anchor(control)
        view.addSubview_(control)
        control.setContentCompressionResistancePriority_forOrientation_(
            NSLayoutPriorityRequired - 1, 0
        )
        if not stretch:
            # Keeps the control at its natural width and hands the slack to
            # the text column, which is how System Settings aligns popups.
            control.setContentHuggingPriority_forOrientation_(
                NSLayoutPriorityRequired - 1, 0
            )
        constraints += [
            control.trailingAnchor().constraintEqualToAnchor_constant_(
                view.trailingAnchor(), -theme.ROW_INSET
            ),
            control.centerYAnchor().constraintEqualToAnchor_(view.centerYAnchor()),
            column.trailingAnchor().constraintEqualToAnchor_constant_(
                control.leadingAnchor(), -16.0
            ),
        ]

    activate(constraints)
    return view


def group(rows):
    """A rounded, filled group of rows with hairlines between them."""
    box = anchor(NSBox.alloc().init())
    box.setBoxType_(NSBoxCustom)
    box.setTitlePosition_(NSNoTitle)
    box.setBorderWidth_(0.0)
    box.setCornerRadius_(theme.GROUP_CORNER_RADIUS)
    box.setFillColor_(theme.GROUP_FILL)
    box.setContentViewMargins_((0.0, 0.0))

    interleaved = []
    for i, item in enumerate(rows):
        if i:
            interleaved.append(separator())
        interleaved.append(item)

    stack = vstack(interleaved, spacing=0.0)
    box.setContentView_(stack)
    pin(stack, box)
    return box


def section(title, rows, footer=None):
    """A heading + group + optional explanatory footer, stacked."""
    parts = []
    if title:
        parts.append(heading(title))
    parts.append(group(rows))
    if footer:
        parts.append(secondary(footer, multiline=True))
    return vstack(parts, spacing=6.0)


def scroll_page(sections, spacing=theme.SECTION_GAP):
    scroll, _body = scroll_body(sections, spacing=spacing)
    return scroll


def scroll_body(sections, spacing=theme.SECTION_GAP):
    """A scrollable page body, plus the stack holding it.

    The scroll view is pinned edge to edge and keeps its automatic content
    insets, which is what produces the macOS 26 scroll-edge effect under
    the toolbar.  Callers that refresh their content keep the returned
    stack and hand it to `set_arranged`.
    """
    scroll = anchor(NSScrollView.alloc().init())
    scroll.setDrawsBackground_(False)
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)

    body = vstack(sections, spacing=spacing)
    document = anchor(FlippedView.alloc().init())
    document.addSubview_(body)
    scroll.setDocumentView_(document)

    clip = scroll.contentView()
    activate([
        document.widthAnchor().constraintEqualToAnchor_(clip.widthAnchor()),
        document.leadingAnchor().constraintEqualToAnchor_(clip.leadingAnchor()),
        document.topAnchor().constraintEqualToAnchor_(clip.topAnchor()),
    ])
    pin(body, document, inset=theme.PAGE_INSET)
    return scroll, body


def hstack_control(views, spacing=8.0):
    """A small horizontal cluster used as a single row control.

    NSStackView has its own hugging API, separate from a view's content
    hugging priority; without it the stack stretches across the whole row
    and drags its contents apart.
    """
    stack = anchor(NSStackView.stackViewWithViews_(list(views)))
    stack.setSpacing_(spacing)
    stack.setHuggingPriority_forOrientation_(
        NSLayoutPriorityRequired - 1, NSLayoutConstraintOrientationHorizontal
    )
    return stack
