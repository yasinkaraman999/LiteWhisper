"""SQLite storage for the mini chat window's conversations and messages.

Mirrors history.py's connect-per-call pattern, but in its own database file
— chat conversations are a different kind of data from the dictation log,
with their own lifecycle (renamed, deleted, multi-turn).
"""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

DB_PATH = Path.home() / ".config" / "lite-whisper" / "chat.db"


def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            model TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            image_paths_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    return conn


def list_conversations():
    """Most recently active first."""
    with closing(_connect()) as conn:
        # id DESC as a tiebreaker: two conversations created within the same
        # second (e.g. rapid "New Chat" clicks) would otherwise sort in an
        # unspecified order.
        rows = conn.execute(
            "SELECT id, title, model, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    return [
        {"id": r[0], "title": r[1], "model": r[2], "created_at": r[3], "updated_at": r[4]}
        for r in rows
    ]


def create_conversation(model=None, title=None):
    now = datetime.now().isoformat(timespec="seconds")
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO conversations (title, model, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (title, model, now, now),
        )
        conn.commit()
        return cur.lastrowid


def rename_conversation(conversation_id, title):
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?", (title, conversation_id)
        )
        conn.commit()


def set_conversation_model(conversation_id, model):
    with closing(_connect()) as conn:
        conn.execute(
            "UPDATE conversations SET model = ? WHERE id = ?", (model, conversation_id)
        )
        conn.commit()


def delete_conversation(conversation_id):
    with closing(_connect()) as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()


def load_messages(conversation_id):
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT id, role, text, image_paths_json, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
    return [
        {
            "id": mid,
            "role": role,
            "text": text,
            "image_paths": json.loads(image_paths_json) if image_paths_json else [],
            "created_at": created_at,
        }
        for mid, role, text, image_paths_json, created_at in rows
    ]


def append_message(conversation_id, role, text, image_paths=None):
    now = datetime.now().isoformat(timespec="seconds")
    image_paths_json = json.dumps(image_paths) if image_paths else None
    with closing(_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, text, image_paths_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (conversation_id, role, text, image_paths_json, now),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )
        conn.commit()
        return cur.lastrowid


def update_message_text(message_id, text):
    with closing(_connect()) as conn:
        conn.execute("UPDATE messages SET text = ? WHERE id = ?", (text, message_id))
        conn.commit()


def delete_messages_from(conversation_id, message_id):
    """Deletes `message_id` and every message after it — used by regenerate,
    which drops the stale assistant reply (and anything past it) before
    resending."""
    with closing(_connect()) as conn:
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
            (conversation_id, message_id),
        )
        conn.commit()


def delete_messages_after(conversation_id, message_id):
    """Deletes every message after `message_id`, keeping it — used by edit,
    which rewrites one user turn in place and drops whatever replies had
    followed it before resending."""
    with closing(_connect()) as conn:
        conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id > ?",
            (conversation_id, message_id),
        )
        conn.commit()
