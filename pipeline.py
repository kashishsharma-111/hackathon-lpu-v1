"""
Library ingestion and processing pipeline.

Sources (CSV upload or Spotify liked songs) only turn their input into song records and add them to the catalog.
A background job then runs every song through the same stages, each recorded in the catalog so the job can resume:

    lyrics   accurate Genius match, or marked not_found (song stays searchable by title and genre)
    alternates  English and romanized versions of the lyrics, when Genius has them
    profile  structured mood profile from the whole lyrics, written by the local LLM
    index    mood document embedded into the vector index (re-done when the document changes)

Songs become searchable as soon as they are indexed, in batches, while the job is still running.
"""
import csv
import hashlib
import io
import logging
import re
import threading
import time

import catalog
import index
import llm
import lyrics as lyrics_source

logger = logging.getLogger(__name__)

CSV_LIBRARY = "csv_local"
INDEX_BATCH_SIZE = 5


def make_song(track_id, name, artists, album="", genres="", spotify_id=""):
    return {"id": track_id, "name": name, "artists": artists, "album": album, "genres": genres,
            "spotify_id": spotify_id, "url": f"https://open.spotify.com/track/{spotify_id}" if spotify_id else ""}


# ---------------------------------------------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------------------------------------------

def parse_csv(raw: bytes):
    """
    Parses a liked-songs CSV (Exportify format or any CSV with track name and artist columns) into song records.
    Duplicate songs are dropped.
    """
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace")))
    songs, seen = [], set()
    for row in reader:
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if isinstance(v, str) or v is None}
        name = r.get("track name") or r.get("name") or r.get("title") or r.get("song")
        artists_raw = (r.get("artist name(s)") or r.get("artist names") or r.get("artist name")
                       or r.get("artists") or r.get("artist"))
        if not name or not artists_raw:
            continue
        artists = [a.strip() for a in re.split(r"[;|]", artists_raw) if a.strip()]
        uri = r.get("track uri") or r.get("uri") or r.get("track id") or r.get("spotify id") or ""
        spotify_id = uri.rsplit(":", 1)[-1].rsplit("/", 1)[-1] if uri else ""
        track_id = spotify_id or hashlib.md5(f"{name}|{artists[0]}".lower().encode()).hexdigest()
        if track_id in seen:
            continue
        seen.add(track_id)
        songs.append(make_song(track_id, name, artists, album=r.get("album name") or r.get("album") or "",
                               genres=r.get("genres") or r.get("genre") or "", spotify_id=spotify_id))
    return songs


def read_spotify_liked_songs(sp, limit: int):
    """
    Reads up to `limit` liked songs from Spotify (newest first), with artist genres when Spotify provides them.
    """
    tracks, offset = [], 0
    while len(tracks) < limit:
        page = sp.current_user_saved_tracks(limit=min(50, limit - len(tracks)), offset=offset)
        if not page["items"]:
            break
        tracks += [item["track"] for item in page["items"] if item.get("track") and item["track"].get("id")]
        offset += len(page["items"])
        if not page.get("next"):
            break

    genres_by_artist = {}
    artist_ids = list({a["id"] for t in tracks for a in t["artists"] if a.get("id")})
    try:
        for i in range(0, len(artist_ids), 50):
            for artist in sp.artists(artist_ids[i:i + 50])["artists"]:
                if artist:
                    genres_by_artist[artist["id"]] = artist.get("genres", [])
    except Exception as e:
        logger.warning(f"Could not read artist genres from Spotify, continuing without them: {e}")

    songs = []
    for t in tracks:
        genres = list(dict.fromkeys(g for a in t["artists"] for g in genres_by_artist.get(a.get("id"), [])))
        songs.append(make_song(t["id"], t["name"], [a["name"] for a in t["artists"]], album=t["album"]["name"],
                               genres=", ".join(genres[:5]), spotify_id=t["id"]))
    return songs


def add_songs(library: str, songs, replace: bool = False):
    if replace:
        catalog.delete_library(library)
        index.reset(library)
    return catalog.upsert_songs(library, songs)


# ---------------------------------------------------------------------------------------------------------------
# Processing job (one at a time)
# ---------------------------------------------------------------------------------------------------------------

_job_lock = threading.Lock()
_job = {"state": "idle", "library": None, "done": 0, "total": 0, "current": "", "message": "", "error": None,
        "started_at": None, "finished_at": None}


def job_snapshot():
    with _job_lock:
        return dict(_job)


def _update_job(**fields):
    with _job_lock:
        _job.update(fields)


def _needs_lyrics(song: dict):
    return song["lyrics_status"] in ("pending", "error") and lyrics_source.configured()


def _needs_alternates(song: dict):
    return song["lyrics_status"] == "found" and song["alt_status"] != "done" and lyrics_source.configured()


def _needs_profile(song: dict):
    return song["lyrics_status"] == "found" and (
        song["profile_status"] != "done" or song["profile_version"] < llm.PROFILE_VERSION)


def needs_work(song: dict, use_llm: bool):
    if _needs_lyrics(song) or _needs_alternates(song) or (use_llm and _needs_profile(song)):
        return True
    return song["indexed_hash"] != index.document_hash(index.mood_document(song))


def profile_lyrics(song: dict):
    """
    The version of the lyrics the LLM reads to write the mood profile. A small model reads English and romanized
    lyrics more reliably than Devanagari or Gurmukhi script, so those are preferred when Genius has them.
    """
    return song["lyrics_english"] or song["lyrics_romanized"] or song["lyrics"]


def _process_song(library: str, song: dict, use_llm: bool):
    """Runs the lyrics and profile stages for one song and returns the updated song."""
    started = time.time()
    if _needs_lyrics(song):
        _update_job(current=f"{song['name']}: lyrics")
        status, text, match = lyrics_source.find_lyrics(song["name"], song["artists"])
        fields = {"lyrics_status": status, "lyrics": text}
        if match:
            fields.update(genius_title=match["title"], genius_artist=match["artist"], genius_url=match["url"])
        if status == "not_found":
            fields.update(profile_status="none", profile="")
        catalog.update_song(library, song["track_id"], **fields)
        song.update(fields)
        if status == "not_found":
            song["profile"] = None
    if _needs_alternates(song):
        _update_job(current=f"{song['name']}: translated lyrics")
        alt_status, english, romanized = lyrics_source.find_alternates(song["name"], song["artists"])
        fields = {"alt_status": alt_status, "lyrics_english": english, "lyrics_romanized": romanized}
        catalog.update_song(library, song["track_id"], **fields)
        song.update(fields)
    lyrics_done = time.time()

    if use_llm and _needs_profile(song):
        _update_job(current=f"{song['name']}: mood profile")
        profile = llm.profile_song(song["name"], song["artists"], song["album"], song["genres"], profile_lyrics(song))
        if profile:
            catalog.update_song(library, song["track_id"], profile=profile, profile_status="done",
                                profile_version=llm.PROFILE_VERSION)
            song.update(profile=profile, profile_status="done", profile_version=llm.PROFILE_VERSION)
        else:
            catalog.update_song(library, song["track_id"], profile_status="error")
            song["profile_status"] = "error"
    logger.info(f"[{library}] {song['name']}: lyrics {song['lyrics_status']} ({lyrics_done - started:.1f}s), "
                f"profile {song['profile_status']} ({time.time() - lyrics_done:.1f}s)")
    return song


def _flush(library: str, batch):
    hashes = index.upsert(library, batch)
    for track_id, digest in hashes.items():
        catalog.update_song(library, track_id, indexed_hash=digest)


def _run(library: str, songs, use_llm: bool):
    profiled = no_lyrics = failed_profiles = 0
    batch = []
    try:
        for i, song in enumerate(songs):
            song = _process_song(library, song, use_llm)
            profiled += song["profile_status"] == "done"
            no_lyrics += song["lyrics_status"] == "not_found"
            failed_profiles += song["profile_status"] == "error"
            batch.append(song)
            if len(batch) >= INDEX_BATCH_SIZE or i == len(songs) - 1:
                _update_job(current=f"indexing {len(batch)} songs")
                _flush(library, batch)
                batch = []
            _update_job(done=i + 1)

        counts = catalog.library_counts(library)
        message = (f"Processed {len(songs)} songs. Library: {counts['total']} songs, {counts['lyrics_found']} with "
                   f"lyrics, {counts['profiled']} with mood profiles, {counts['indexed']} searchable.")
        if no_lyrics:
            message += f" {no_lyrics} had no reliable lyrics match and are searchable by title and genre only."
        if failed_profiles:
            message += f" {failed_profiles} mood profiles failed; run again to retry them."
        if use_llm and not llm.status()["available"]:
            message += f" Mood profiles were skipped: {llm.status()['reason']}"
        _update_job(state="done", message=message, current="", finished_at=time.time())
    except Exception as e:
        logger.exception("Processing job failed")
        if batch:
            try:
                _flush(library, batch)
            except Exception:
                logger.exception("Could not index the last batch")
        _update_job(state="error", current="", finished_at=time.time(),
                    error=f"Processing stopped after {job_snapshot()['done']} songs: {e}. "
                          "Finished songs are saved; run again to continue.")


def start(library: str, use_llm: bool = True, limit: int = None):
    """
    Starts processing the songs of `library` that still need work (at most `limit`). Returns the job snapshot.
    """
    use_llm = use_llm and llm.status(force=True)["available"]
    with _job_lock:
        if _job["state"] == "running":
            raise RuntimeError("A processing job is already running. Wait for it to finish.")
        todo = [s for s in catalog.get_songs(library) if needs_work(s, use_llm)]
        if limit:
            todo = todo[:limit]
        _job.update(state="running", library=library, done=0, total=len(todo), current="", message="", error=None,
                    started_at=time.time(), finished_at=None)
    if not todo:
        _update_job(state="done", message="Nothing to do: every song is already processed.", finished_at=time.time())
        return job_snapshot()
    threading.Thread(target=_run, args=(library, todo, use_llm), daemon=True).start()
    return job_snapshot()


def library_status(library: str):
    counts = catalog.library_counts(library)
    use_llm = llm.status()["available"]
    counts["pending"] = sum(needs_work(s, use_llm) for s in catalog.get_songs(library))
    return counts
