import os
import subprocess

import AVFoundation
from Foundation import NSBundle
from HIServices import AXIsProcessTrusted
from Quartz import CGPreflightListenEventAccess

BUNDLE_ID = "com.yasinkaraman.litewhisper"
TCC_SERVICES = ["Accessibility", "ListenEvent", "Microphone"]

SETTINGS_URLS = {
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input_monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}


def accessibility_granted():
    return bool(AXIsProcessTrusted())


def input_monitoring_granted():
    return bool(CGPreflightListenEventAccess())


def microphone_granted():
    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    )
    return status == 3  # AVAuthorizationStatusAuthorized


def status():
    return {
        "accessibility": ("Accessibility (pasting)", accessibility_granted()),
        "input_monitoring": ("Input Monitoring (hotkey)", input_monitoring_granted()),
        "microphone": ("Microphone (recording)", microphone_granted()),
    }


def open_settings(key):
    subprocess.Popen(["open", SETTINGS_URLS[key]])


def relaunch():
    bundle_path = NSBundle.mainBundle().bundlePath()
    subprocess.Popen(["open", "-n", bundle_path])
    os._exit(0)


def reset_all_and_relaunch():
    for service in TCC_SERVICES:
        subprocess.run(["tccutil", "reset", service, BUNDLE_ID], check=False)
    relaunch()
