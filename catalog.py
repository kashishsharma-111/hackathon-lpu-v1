"""
Local song catalog (SQLite).

One row per song per library, with the state of every processing stage, so the pipeline can resume after a crash
and re-run a single stage (for example new mood profiles) without fetching lyrics again.

A library is the set of songs from one source: "csv_local" for the uploaded CSV, or the Spotify user ID.
"""
import json
import os
import sqlite3
import threading
import time

DATA_DIR = os.getenv("DATA_DIR", ".")
DB_PATH = os.path.join(DATA_DIR, "moodify.db")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    library        TEXT NOT NULL,
    track_id       TEXT NOT NULL,
    position       INTEGER NOT NULL,
    name           TEXT NOT NULL,
    artists        TEXT NOT NULL,          -- JSON list
    album          TEXT NOT NULL DEFAULT '',
    genres         TEXT NOT NULL DEFAULT '',
    spotify_id     TEXT NOT NULL DEFAULT '',
    url            TEXT NOT NULL DEFAULT '',
    lyrics_status  TEXT NOT NULL DEFAULT 'pending',   -- pending | found | not_found | error
    lyrics         TEXT NOT NULL DEFAULT '',
    genius_title   TEXT NOT NULL DEFAULT '',
    genius_artist  TEXT NOT NULL DEFAULT '',
    genius_url     TEXT NOT NULL DEFAULT '',
    profile        TEXT NOT NULL DEFAULT '',          -- JSON mood profile from the LLM
    profile_status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error | none (no lyrics)
    profile_version INTEGER NOT NULL DEFAULT 0,
    indexed_hash   TEXT NOT NULL DEFAULT '',          -- hash of the document currently in the vector index
    alt_status     TEXT NOT NULL DEFAULT 'pending',   -- pending | done | error: looked for translated/romanized lyrics
    lyrics_english TEXT NOT NULL DEFAULT '',
    lyrics_romanized TEXT NOT NULL DEFAULT '',
    updated_at     REAL NOT NULL,
    PRIMARY KEY (library, track_id)
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# Columns added after the first version, for databases created before them
MIGRATIONS = {
    "alt_status": "TEXT NOT NULL DEFAULT 'pending'",
    "lyrics_english": "TEXT NOT NULL DEFAULT ''",
    "lyrics_romanized": "TEXT NOT NULL DEFAULT ''",
}


def init():
    with _lock, connect() as conn:
        conn.executescript(SCHEMA)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(songs)")}
        for column, definition in MIGRATIONS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE songs ADD COLUMN {column} {definition}")


def _row_to_song(row):
    song = dict(row)
    song["artists"] = json.loads(song["artists"])
    song["profile"] = json.loads(song["profile"]) if song["profile"] else None
    return song


def upsert_songs(library: str, songs):
    """
    Adds new songs and refreshes the metadata of known ones. Processing state of known songs is kept.
    Returns the number of new songs.
    """
    now = time.time()
    with _lock, connect() as conn:
        existing = {r["track_id"] for r in conn.execute("SELECT track_id FROM songs WHERE library = ?", (library,))}
        start = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM songs WHERE library = ?",
                             (library,)).fetchone()[0]
        new = 0
        for song in songs:
            if song["id"] in existing:
                conn.execute("UPDATE songs SET name = ?, artists = ?, album = ?, genres = ?, spotify_id = ?, url = ?, "
                             "updated_at = ? WHERE library = ? AND track_id = ?",
                             (song["name"], json.dumps(song["artists"]), song["album"], song["genres"],
                              song["spotify_id"], song["url"], now, library, song["id"]))
            else:
                conn.execute("INSERT INTO songs (library, track_id, position, name, artists, album, genres, spotify_id, "
                             "url, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (library, song["id"], start + new, song["name"], json.dumps(song["artists"]),
                              song["album"], song["genres"], song["spotify_id"], song["url"], now))
                existing.add(song["id"])
                new += 1
        return new


def update_song(library: str, track_id: str, **fields):
    if "profile" in fields and fields["profile"] is not None and not isinstance(fields["profile"], str):
        fields["profile"] = json.dumps(fields["profile"])
    fields["updated_at"] = time.time()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with _lock, connect() as conn:
        conn.execute(f"UPDATE songs SET {assignments} WHERE library = ? AND track_id = ?",
                     (*fields.values(), library, track_id))


def get_songs(library: str, track_ids=None):
    with connect() as conn:
        if track_ids is None:
            rows = conn.execute("SELECT * FROM songs WHERE library = ? ORDER BY position", (library,)).fetchall()
        else:
            ids = list(track_ids)
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            rows = conn.execute(f"SELECT * FROM songs WHERE library = ? AND track_id IN ({marks})",
                                (library, *ids)).fetchall()
    return [_row_to_song(r) for r in rows]


def delete_library(library: str):
    with _lock, connect() as conn:
        conn.execute("DELETE FROM songs WHERE library = ?", (library,))


def library_counts(library: str):
    with connect() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(lyrics_status = 'found') AS lyrics_found,
                   SUM(lyrics_status = 'not_found') AS lyrics_not_found,
                   SUM(lyrics_status IN ('pending', 'error')) AS lyrics_pending,
                   SUM(profile_status = 'done') AS profiled,
                   SUM(indexed_hash != '') AS indexed
            FROM songs WHERE library = ?""", (library,)).fetchone()
    return {key: row[key] or 0 for key in row.keys()}


def library_stamp(library: str):
    """Changes whenever a song in the library changes."""
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM songs WHERE library = ?", (library,)).fetchone()
    return tuple(row)


def set_meta(key: str, value: str):
    with _lock, connect() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


def get_meta(key: str, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default
