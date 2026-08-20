import threading

import rumps
from PyObjCTools import AppHelper

import chat_bubble
import chat_window
import config
import history
import hotkey
import live_transcribe
import main_window
import mic_lock
import output
import recording_window
import sounds
from audio_recorder import AudioRecorder
from resources import resource_path
from transcriber import get_transcriber

MIC_OWNER = "dictation"

STATE_IDLE = ""
STATE_RECORDING = " 🔴"
STATE_BUSY = " …"

APP_ICON = resource_path("logos/lite-whisper-no-bg-colored.png")


class LiteWhisperApp(rumps.App):
    def __init__(self):
        super().__init__(
            "lite-whisper", title=STATE_IDLE, icon=APP_ICON, template=True, quit_button=None
        )
        self.record_item = rumps.MenuItem("Start Recording", callback=self.on_toggle)
        self.menu = [
            self.record_item,
            "Open LiteWhisper",
            "Chat",
            "History",
            "Permissions",
            "Settings",
            None,
            "Quit",
        ]
        self.recorder = AudioRecorder()
        self.live_dictation = live_transcribe.LiveDictation()
        recording_window.configure(
            stop_callback=self.on_toggle,
            level_source=lambda: self.recorder.level,
            live_stop_callback=self.on_live_toggle,
        )
        chat_bubble.configure(on_click=chat_window.show)
        self._overlay_state("idle")
        self._listener = hotkey.start_listener(
            self.on_toggle,
            on_live_toggle=self.on_live_toggle,
            on_cancel=self.on_cancel,
            is_active=lambda: self.recorder.is_recording or self.live_dictation.is_active,
        )

    @rumps.clicked("Open LiteWhisper")
    def open_main_window(self, _):
        self._show_window("home")

    @rumps.clicked("Chat")
    def open_chat(self, _):
        chat_window.show()

    @rumps.clicked("History")
    def show_history(self, _):
        self._show_window("history")

    @rumps.clicked("Permissions")
    def show_permissions(self, _):
        self._show_window("permissions")

    @rumps.clicked("Settings")
    def show_settings(self, _):
        self._show_window("config")

    def _show_window(self, page):
        try:
            main_window.show(page=page)
        except Exception as e:
            rumps.notification("lite-whisper", "Could not open window", str(e))

    @rumps.clicked("Quit")
    def quit_app(self, _):
        self._listener.stop()
        rumps.quit_application()

    def _overlay_state(self, state):
        """Drive the overlay from any thread.

        The hotkey tap and the transcription worker both call this, and
        AppKit only tolerates window changes on the main thread.
        """
        AppHelper.callAfter(recording_window.set_state, state)

    def on_toggle(self, sender=None):
        if self.recorder.is_recording:
            sounds.play_stop()
            self.title = STATE_BUSY
            self.record_item.title = "Start Recording"
            self._overlay_state("processing")
            wav_bytes = self.recorder.stop()
            mic_lock.release(MIC_OWNER)
            threading.Thread(target=self._transcribe, args=(wav_bytes,), daemon=True).start()
        else:
            if self.live_dictation.is_active:
                rumps.notification("lite-whisper", "", "Microphone is busy")
                return
            if not mic_lock.acquire(MIC_OWNER):
                rumps.notification("lite-whisper", "", "Microphone is busy")
                return
            sounds.play_start()
            self.title = STATE_RECORDING
            self.record_item.title = "Stop Recording"
            self.recorder.start()
            self._overlay_state("recording")

    def on_live_toggle(self, sender=None):
        if self.live_dictation.is_active:
            self.live_dictation.stop()
            self._overlay_state("idle")
            return
        if self.recorder.is_recording:
            rumps.notification("lite-whisper", "", "Microphone is busy")
            return
        if not self.live_dictation.start():
            rumps.notification("lite-whisper", "", "Microphone is busy")
            return
        sounds.play_start()
        self._overlay_state("live")

    def on_cancel(self, sender=None):
        """Escape during a recording: drop it without transcribing."""
        if self.live_dictation.is_active:
            self.live_dictation.cancel()
            sounds.play_stop()
            self._overlay_state("idle")
            return
        if not self.recorder.is_recording:
            return
        self.recorder.cancel()
        mic_lock.release(MIC_OWNER)
        sounds.play_stop()
        self.title = STATE_IDLE
        self.record_item.title = "Start Recording"
        self._overlay_state("idle")

    def _transcribe(self, wav_bytes):
        try:
            if not wav_bytes:
                raise ValueError("Recording is empty")

            if not self.recorder.last_had_speech:
                rumps.notification("lite-whisper", "", "No speech detected")
                return

            cfg = config.load()
            transcriber = get_transcriber(
                engine=cfg["engine"],
                model=cfg["model"],
                local_model_size=cfg["local_model_size"],
                api_key=cfg["openrouter_api_key"],
            )
            text = transcriber.transcribe(wav_bytes)

            if text.strip():
                usage = getattr(transcriber, "last_usage", None)
                if cfg["engine"] == "local":
                    model_label = f"Local · {cfg['local_model_size']}"
                else:
                    model_label = f"Cloud · {cfg['model']}"
                history.append(text, usage=usage, model=model_label)
                output.deliver(text)
            else:
                rumps.notification("lite-whisper", "", "No speech detected")
        except Exception as e:
            rumps.notification("lite-whisper", "Error", str(e))
        finally:
            self.title = STATE_IDLE
            self._overlay_state("idle")


if __name__ == "__main__":
    LiteWhisperApp().run()
