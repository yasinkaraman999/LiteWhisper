import threading

import sounddevice as sd
from AppKit import (
    NSFont,
    NSFontWeightSemibold,
    NSPopUpButton,
    NSSlider,
    NSTextAlignmentRight,
    NSTextField,
    NSView,
)
from PyObjCTools import AppHelper

import config
import models
import nsui
import recording_window
import theme
from transcriber.local_whisper import (
    MODEL_INFO,
    delete_model,
    downloaded_size_bytes,
    format_size,
    is_downloaded,
)
from ui_helpers import ButtonTarget, keep_alive

TEXT_FIELD_MIN_WIDTH = 260.0
SLIDER_WIDTH = 160.0

ENGINE_OPTIONS = [("cloud", "Cloud (OpenRouter)"), ("local", "Local (on this Mac)")]
OVERLAY_STYLE_OPTIONS = [
    ("classic", "Classic"),
    ("mini", "Mini"),
    ("none", "Hidden"),
]
DOCK_EDGE_OPTIONS = [
    ("top", "Top"),
    ("bottom", "Bottom"),
    ("left", "Left"),
    ("right", "Right"),
]

_api_key_field = None
_model_popup = None
_status_label = None
_available_models = []  # [{"id": ..., "name": ...}]
_engine_popup = None
_active_local_popup = None
_local_rows = {}  # size -> {"status": label, "button": button}
_input_device_popup = None
_input_device_ids = []  # index i -> sounddevice index, or None for system default
_noise_slider = None
_noise_value_label = None
_debug_audio_checkbox = None
_vad_checkbox = None
_overlay_style_popup = None
_overlay_always_checkbox = None
_dock_edge_popup = None
_dock_slot_popup = None


def _list_input_devices():
    devices = []
    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devices.append((i, d["name"]))
    except Exception:
        pass
    return devices


def _popup():
    popup = nsui.anchor(NSPopUpButton.alloc().init())
    popup.setPullsDown_(False)
    return popup


def _secure_text_field(placeholder):
    field = nsui.anchor(NSTextField.alloc().init())
    field.setPlaceholderString_(placeholder)
    field.setBezeled_(True)
    field.setEditable_(True)
    field.setSelectable_(True)
    field.setFont_(NSFont.monospacedSystemFontOfSize_weight_(12.0, 0.0))
    field.cell().setUsesSingleLineMode_(True)
    field.cell().setScrollable_(True)
    nsui.activate([
        field.widthAnchor().constraintGreaterThanOrEqualToConstant_(TEXT_FIELD_MIN_WIDTH)
    ])
    return field


def build_configuration_page():
    global _api_key_field, _engine_popup, _model_popup, _status_label

    _api_key_field = _secure_text_field("sk-or-v1-…")

    _engine_popup = _popup()
    for _, label_text in ENGINE_OPTIONS:
        _engine_popup.addItemWithTitle_(label_text)

    _model_popup = _popup()

    _status_label = nsui.secondary("", size=11.0)
    _status_label.setAlignment_(NSTextAlignmentRight)
    nsui.activate([_status_label.widthAnchor().constraintEqualToConstant_(150.0)])

    engine_section = nsui.section("Engine", [
        nsui.row("Transcription engine", _engine_popup),
    ], footer="The local engine works without a network connection, and no audio\n"
           "ever leaves your computer.")

    cloud_section = nsui.section("OpenRouter", [
        nsui.row("API key", _api_key_field, stretch=True),
        nsui.row("Cloud model", _model_popup),
        nsui.row("Model list", nsui.hstack_control([
            _status_label,
            nsui.button("Refresh", _on_refresh_models),
        ])),
    ])

    return nsui.scroll_page([
        engine_section,
        cloud_section,
        _save_footer(),
    ])


def build_sound_page():
    global _input_device_popup, _input_device_ids
    global _noise_slider, _noise_value_label, _debug_audio_checkbox, _vad_checkbox

    _input_device_popup = _popup()
    _input_device_popup.addItemWithTitle_("System Default")
    _input_device_ids = [None]
    for idx, name in _list_input_devices():
        _input_device_popup.addItemWithTitle_(name)
        _input_device_ids.append(idx)

    _noise_slider = nsui.anchor(NSSlider.alloc().init())
    _noise_slider.setMinValue_(0.0)
    _noise_slider.setMaxValue_(1.0)
    _noise_slider.setTarget_(_slider_target())
    _noise_slider.setAction_("clicked:")
    nsui.activate([_noise_slider.widthAnchor().constraintEqualToConstant_(SLIDER_WIDTH)])

    _noise_value_label = nsui.label("", size=12.0, color=theme.TEXT_SECONDARY)
    _noise_value_label.setFont_(
        NSFont.monospacedDigitSystemFontOfSize_weight_(12.0, 0.0)
    )
    _noise_value_label.setAlignment_(NSTextAlignmentRight)
    # An exact width, not a minimum: with only a lower bound the layout is
    # ambiguous and the slack lands on the label at some window widths and
    # on the label column at others, so the row visibly jumps while resizing.
    nsui.activate([_noise_value_label.widthAnchor().constraintEqualToConstant_(40.0)])

    _debug_audio_checkbox = nsui.checkbox()
    _vad_checkbox = nsui.checkbox()

    return nsui.scroll_page([
        nsui.section("Input", [
            nsui.row("Microphone", _input_device_popup),
        ]),
        nsui.section("Processing", [
            nsui.row(
                "Noise reduction",
                nsui.hstack_control([_noise_slider, _noise_value_label]),
            ),
            nsui.row(
                "Skip recordings with no speech",
                _vad_checkbox,
                subtitle="Detects whether you actually said anything before "
                         "sending a recording off for transcription.",
            ),
        ], footer="0 is off, 1 is most aggressive. Higher values strip more "
                  "background noise but can make your voice sound less natural."),
        nsui.section("Developer", [
            nsui.row(
                "Save raw and cleaned audio",
                _debug_audio_checkbox,
                subtitle="Leaves two WAV files per recording so you can compare them by ear.",
            ),
        ]),
        _save_footer(),
    ])


def build_overlay_page():
    global _overlay_style_popup, _overlay_always_checkbox
    global _dock_edge_popup, _dock_slot_popup

    _overlay_style_popup = _popup()
    for _, label_text in OVERLAY_STYLE_OPTIONS:
        _overlay_style_popup.addItemWithTitle_(label_text)

    _overlay_always_checkbox = nsui.checkbox()

    _dock_edge_popup = _popup()
    for _, label_text in DOCK_EDGE_OPTIONS:
        _dock_edge_popup.addItemWithTitle_(label_text)

    _dock_slot_popup = _popup()
    for slot in range(config.DOCK_SLOTS):
        _dock_slot_popup.addItemWithTitle_(str(slot + 1))

    return nsui.scroll_page([
        nsui.section("Appearance", [
            nsui.row("Recording window", _overlay_style_popup),
            nsui.row(
                "Show when idle",
                _overlay_always_checkbox,
                subtitle="When off, the window appears only while recording or transcribing.",
            ),
        ], footer="Classic shows the waveform, a status line and the shortcut hint. "
                  "Mini shows only the waveform and a stop button."),
        nsui.section("Position", [
            nsui.row("Screen edge", _dock_edge_popup),
            nsui.row("Slot", _dock_slot_popup),
        ], footer="You can also drag the window itself; it snaps to whichever slot "
                  "is nearest where you drop it."),
        _save_footer(),
    ])


def build_models_page():
    global _active_local_popup

    _active_local_popup = _popup()
    for size in config.LOCAL_MODEL_SIZES:
        _active_local_popup.addItemWithTitle_(size)

    _local_rows.clear()
    model_rows = []
    for size in config.LOCAL_MODEL_SIZES:
        info = MODEL_INFO.get(size, {})
        status = nsui.secondary("", size=11.0)
        # Right-aligned against a fixed width so the download/delete buttons
        # line up down the column no matter how long each status string is.
        status.setAlignment_(NSTextAlignmentRight)
        nsui.activate([status.widthAnchor().constraintEqualToConstant_(122.0)])
        action = nsui.button("…", lambda s=size: _on_local_row_action(s))
        nsui.activate([action.widthAnchor().constraintEqualToConstant_(78.0)])

        model_rows.append(nsui.row(
            size,
            nsui.hstack_control([status, action]),
            subtitle=f"{info.get('disk', '?')} on disk · {info.get('ram', '?')} RAM",
        ))
        _local_rows[size] = {"status": status, "button": action}

    return nsui.scroll_page([
        nsui.section("Active Model", [
            nsui.row("Local model", _active_local_popup),
        ], footer="This model is used whenever the local engine is selected."),
        nsui.section("Model Library", model_rows),
        _save_footer(),
    ])


def _save_footer():
    """The trailing-aligned Save button each settings page ends with."""
    holder = nsui.anchor(NSView.alloc().init())
    save = nsui.button("Save", _on_save, default=True)
    holder.addSubview_(save)
    nsui.activate([
        save.trailingAnchor().constraintEqualToAnchor_(holder.trailingAnchor()),
        save.topAnchor().constraintEqualToAnchor_(holder.topAnchor()),
        save.bottomAnchor().constraintEqualToAnchor_(holder.bottomAnchor()),
    ])
    return holder


def _slider_target():
    target = ButtonTarget.alloc().initWithCallback_(lambda _sender: _update_noise_label())
    keep_alive(target)
    return target


def _populate_model_popup(models_list, selected_id):
    _model_popup.removeAllItems()
    for m in models_list:
        _model_popup.addItemWithTitle_(f"{m['name']}  —  {m['id']}")

    ids = [m["id"] for m in models_list]
    if selected_id in ids:
        _model_popup.selectItemAtIndex_(ids.index(selected_id))


def _on_refresh_models():
    api_key = _api_key_field.stringValue().strip()
    if not api_key:
        _status_label.setStringValue_("Enter an API key first")
        return

    _status_label.setStringValue_("Loading...")

    def fetch():
        try:
            fetched = models.fetch_cloud_models(api_key)
        except Exception as e:
            AppHelper.callAfter(_status_label.setStringValue_, f"Error: {e}")
            return

        def apply():
            global _available_models
            _available_models = fetched
            cfg = config.load()
            _populate_model_popup(fetched, cfg["model"])
            _status_label.setStringValue_(f"{len(fetched)} models")

        AppHelper.callAfter(apply)

    threading.Thread(target=fetch, daemon=True).start()


def _refresh_local_rows():
    for size, widgets in _local_rows.items():
        if is_downloaded(size, config.LOCAL_MODELS_DIR):
            size_str = format_size(downloaded_size_bytes(size, config.LOCAL_MODELS_DIR))
            widgets["status"].setStringValue_(f"Downloaded · {size_str}")
            widgets["button"].setTitle_("Delete")
        else:
            widgets["status"].setStringValue_("Not downloaded")
            widgets["button"].setTitle_("Download")


def _on_local_row_action(size):
    if is_downloaded(size, config.LOCAL_MODELS_DIR):
        delete_model(size, config.LOCAL_MODELS_DIR)
        _refresh_local_rows()
        return

    _local_rows[size]["status"].setStringValue_("Downloading...")
    _local_rows[size]["button"].setEnabled_(False)

    def download():
        try:
            from transcriber.local_whisper import _get_model

            _get_model(size, config.LOCAL_MODELS_DIR)
        except Exception as e:
            def on_error():
                _local_rows[size]["status"].setStringValue_(f"Error: {e}")
                _local_rows[size]["button"].setEnabled_(True)

            AppHelper.callAfter(on_error)
            return

        def on_done():
            _local_rows[size]["button"].setEnabled_(True)
            _refresh_local_rows()

        AppHelper.callAfter(on_done)

    threading.Thread(target=download, daemon=True).start()


def _selected_active_local_size():
    idx = _active_local_popup.indexOfSelectedItem()
    if 0 <= idx < len(config.LOCAL_MODEL_SIZES):
        return config.LOCAL_MODEL_SIZES[idx]
    return config.LOCAL_MODEL_SIZES[0]


def _update_noise_label():
    _noise_value_label.setStringValue_(f"{_noise_slider.doubleValue():.2f}")


def _on_save():
    """Saves whatever the built pages currently hold.

    Pages are built lazily, so each field is only read once its page exists —
    otherwise saving from Sound would wipe the API key with an empty string.
    """
    cfg = config.load()

    if _api_key_field is not None:
        cfg["openrouter_api_key"] = _api_key_field.stringValue().strip()

        selected_index = _model_popup.indexOfSelectedItem()
        if 0 <= selected_index < len(_available_models):
            cfg["model"] = _available_models[selected_index]["id"]

        engine_index = _engine_popup.indexOfSelectedItem()
        if 0 <= engine_index < len(ENGINE_OPTIONS):
            cfg["engine"] = ENGINE_OPTIONS[engine_index][0]

    if _active_local_popup is not None:
        cfg["local_model_size"] = _selected_active_local_size()

    if _input_device_popup is not None:
        device_index = _input_device_popup.indexOfSelectedItem()
        if 0 <= device_index < len(_input_device_ids):
            cfg["input_device"] = _input_device_ids[device_index]

        cfg["noise_reduction_strength"] = round(_noise_slider.doubleValue(), 2)
        cfg["debug_save_audio"] = bool(_debug_audio_checkbox.state())
        cfg["vad_enabled"] = bool(_vad_checkbox.state())

    if _overlay_style_popup is not None:
        style_index = _overlay_style_popup.indexOfSelectedItem()
        if 0 <= style_index < len(OVERLAY_STYLE_OPTIONS):
            cfg["recording_window_style"] = OVERLAY_STYLE_OPTIONS[style_index][0]

        cfg["recording_window_always_show"] = bool(_overlay_always_checkbox.state())

        edge_index = _dock_edge_popup.indexOfSelectedItem()
        slot_index = _dock_slot_popup.indexOfSelectedItem()
        if 0 <= edge_index < len(DOCK_EDGE_OPTIONS) and 0 <= slot_index < config.DOCK_SLOTS:
            cfg["recording_window_dock"] = f"{DOCK_EDGE_OPTIONS[edge_index][0]}-{slot_index}"

    config.save(cfg)

    # The overlay reads its style and dock at build time, so it has to be
    # told to pick the new ones up.
    recording_window.refresh_from_config()

    if _status_label is not None:
        _status_label.setStringValue_("Saved")


def refresh_all():
    """Called whenever a settings page becomes visible, so every built page
    reflects the config on disk."""
    cfg = config.load()

    if _api_key_field is not None:
        _api_key_field.setStringValue_(cfg["openrouter_api_key"])
        _populate_model_popup(_available_models, cfg["model"])
        if not _available_models:
            _model_popup.removeAllItems()
            _model_popup.addItemWithTitle_(cfg["model"])
        _status_label.setStringValue_("")

        engine_ids = [e[0] for e in ENGINE_OPTIONS]
        if cfg["engine"] in engine_ids:
            _engine_popup.selectItemAtIndex_(engine_ids.index(cfg["engine"]))

    if _active_local_popup is not None:
        if cfg["local_model_size"] in config.LOCAL_MODEL_SIZES:
            _active_local_popup.selectItemAtIndex_(
                config.LOCAL_MODEL_SIZES.index(cfg["local_model_size"])
            )
        _refresh_local_rows()

    if _input_device_popup is not None:
        if cfg["input_device"] in _input_device_ids:
            _input_device_popup.selectItemAtIndex_(
                _input_device_ids.index(cfg["input_device"])
            )
        else:
            _input_device_popup.selectItemAtIndex_(0)

        _noise_slider.setDoubleValue_(cfg["noise_reduction_strength"])
        _update_noise_label()
        _debug_audio_checkbox.setState_(1 if cfg["debug_save_audio"] else 0)
        _vad_checkbox.setState_(1 if cfg["vad_enabled"] else 0)

    if _overlay_style_popup is not None:
        style_ids = [s[0] for s in OVERLAY_STYLE_OPTIONS]
        if cfg["recording_window_style"] in style_ids:
            _overlay_style_popup.selectItemAtIndex_(
                style_ids.index(cfg["recording_window_style"])
            )
        _overlay_always_checkbox.setState_(
            1 if cfg["recording_window_always_show"] else 0
        )

        edge, _, slot = cfg["recording_window_dock"].partition("-")
        edge_ids = [e[0] for e in DOCK_EDGE_OPTIONS]
        if edge in edge_ids:
            _dock_edge_popup.selectItemAtIndex_(edge_ids.index(edge))
        if slot.isdigit() and int(slot) < config.DOCK_SLOTS:
            _dock_slot_popup.selectItemAtIndex_(int(slot))
