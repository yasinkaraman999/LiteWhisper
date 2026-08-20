"""Process-wide mutual exclusion for the microphone.

Batch dictation (Option+Space), live dictation (Shift+Option+Space) and the
chat window's mic button all want exclusive use of the input device — two of
them recording at once would either fight over the same hardware stream or
produce audio meant for the wrong destination. A single lock, checked before
any of them opens a stream, keeps at most one active at a time.
"""

import threading

_lock = threading.Lock()
_owner = None


def acquire(owner: str) -> bool:
    """Claims the microphone for `owner`. Returns False if already held by
    someone else; re-acquiring for the current owner is a harmless no-op."""
    global _owner
    with _lock:
        if _owner is not None and _owner != owner:
            return False
        _owner = owner
        return True


def release(owner: str) -> None:
    """Releases the microphone if `owner` is the one currently holding it."""
    global _owner
    with _lock:
        if _owner == owner:
            _owner = None


def current_owner():
    with _lock:
        return _owner
