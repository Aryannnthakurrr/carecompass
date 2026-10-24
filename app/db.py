"""SQLite store (stdlib) for users, sessions, OTP codes, past chats and bookings.

The file lives in data/ and is git-ignored. Demo data only: chats are synthetic, never real patients.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "carecompass.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id          INTEGER PRIMARY KEY,
  google_sub  TEXT UNIQUE,
  phone       TEXT UNIQUE,
  email       TEXT,
  name        TEXT,
  picture     TEXT,
  created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash  TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS otps (
  phone            TEXT PRIMARY KEY,
  code_hash        TEXT NOT NULL,
  expires_at       INTEGER NOT NULL,
  attempts         INTEGER NOT NULL,
  last_sent_at     INTEGER NOT NULL,
  window_start     INTEGER NOT NULL,
  sends_in_window  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
  id          TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  messages    TEXT NOT NULL,
  outcome     TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_by_user ON chats(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS bookings (
  id          TEXT PRIMARY KEY,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  chat_id     TEXT,
  caregiver   TEXT NOT NULL,
  care_type   TEXT NOT NULL,
  urgency     TEXT,
  slot        TEXT,
  location    TEXT,
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bookings_by_user ON bookings(user_id, created_at DESC);
"""


@contextmanager
def connect():
    """One connection per request; commits on success, rolls back on exception."""
    DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
