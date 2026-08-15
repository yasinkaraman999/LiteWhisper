import subprocess

START_SOUND = "/System/Library/Sounds/Tink.aiff"
# Pop.aiff has an audible two-part "pop-pop" envelope (~1.6s), which reads
# as a double beep. Bottle.aiff is a short, single, clean hit instead.
STOP_SOUND = "/System/Library/Sounds/Bottle.aiff"


def _play(path):
    subprocess.Popen(
        ["afplay", path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def play_start():
    _play(START_SOUND)


def play_stop():
    _play(STOP_SOUND)
