import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".config" / "lite-whisper" / "history.db"
LEGACY_JSON_PATH = Path.home() / ".config" / "lite-whisper" / "history.json"


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            text TEXT NOT NULL
        )
        """
    )
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(history)")}
    if "usage_json" not in existing_columns:
        conn.execute("ALTER TABLE history ADD COLUMN usage_json TEXT")
    if "model" not in existing_columns:
        conn.execute("ALTER TABLE history ADD COLUMN model TEXT")
    return conn


def _migrate_legacy_json_if_needed():
    if not LEGACY_JSON_PATH.exists():
        return

    with closing(_connect()) as conn:
        already_migrated = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] > 0
        if already_migrated:
            LEGACY_JSON_PATH.rename(LEGACY_JSON_PATH.with_suffix(".json.migrated"))
            return

        try:
            with open(LEGACY_JSON_PATH, encoding="utf-8") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
            entries = []

        conn.executemany(
            "INSERT INTO history (timestamp, text) VALUES (?, ?)",
            [(e["timestamp"], e["text"]) for e in entries],
        )
        conn.commit()

    LEGACY_JSON_PATH.rename(LEGACY_JSON_PATH.with_suffix(".json.migrated"))


_migrate_legacy_json_if_needed()


def load():
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT timestamp, text, usage_json, model FROM history ORDER BY id ASC"
        ).fetchall()
    return [
        {
            "timestamp": ts,
            "text": text,
            "usage": json.loads(usage_json) if usage_json else None,
            "model": model,
        }
        for ts, text, usage_json, model in rows
    ]


def append(text, usage=None, model=None):
    timestamp = datetime.now().isoformat(timespec="seconds")
    usage_json = json.dumps(usage) if usage else None
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT INTO history (timestamp, text, usage_json, model) VALUES (?, ?, ?, ?)",
            (timestamp, text, usage_json, model),
        )
        conn.commit()


def clear():
    """Delete every entry. Irreversible — callers must confirm first."""
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM history")
        conn.commit()
        # Reclaim the space rather than leaving a file full of free pages.
        conn.execute("VACUUM")


def stats():
    entries = load()

    total_count = len(entries)
    total_words = sum(len(e["text"].split()) for e in entries)
    total_cost = sum(
        e["usage"]["cost"] for e in entries if e["usage"] and "cost" in e["usage"]
    )

    wpm_samples = []
    for e in entries:
        usage = e["usage"]
        if usage and usage.get("seconds"):
            words = len(e["text"].split())
            wpm_samples.append(words / (usage["seconds"] / 60))
    avg_wpm = round(sum(wpm_samples) / len(wpm_samples)) if wpm_samples else 0

    return {
        "total_count": total_count,
        "total_words": total_words,
        "total_cost": total_cost,
        "avg_wpm": avg_wpm,
    }
