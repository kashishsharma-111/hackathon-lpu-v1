"""
Moodify backend (FastAPI).

Flow (the same for both sources):
    source (CSV upload or Spotify liked songs) -> catalog (SQLite) -> background processing job
    (Genius lyrics -> LLM mood profile -> mood document in ChromaDB) -> mood search -> list or Spotify playlist

Modules: catalog.py (song catalog), lyrics.py (Genius matching), llm.py (local LLM), index.py (vector index),
pipeline.py (sources and processing job), search.py (mood search and ranking).
"""
import logging
import os
import re

import spotipy
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from spotipy.exceptions import SpotifyException, SpotifyOauthError
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

# These modules read settings from the environment loaded above
import catalog  # noqa: E402
import llm  # noqa: E402
import lyrics as lyrics_source  # noqa: E402
import pipeline  # noqa: E402
import search as mood_search  # noqa: E402

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Data source default; can be overridden per request with ?source=csv|spotify
DEFAULT_SOURCE = os.getenv("MOODIFY_SOURCE", "csv").lower()
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8501")
SPOTIFY_LIBRARY_KEY = "spotify_library"

catalog.init()
app = FastAPI(title="Moodify")

# Spotify OAuth configuration. The app still runs without Spotify credentials (csv source only).
try:
    sp_oauth = SpotifyOAuth(
        client_id=os.getenv("SPOTIFY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8235/callback"),
        scope="user-library-read playlist-modify-public playlist-modify-private",
        cache_path=os.path.join(os.getenv("DATA_DIR", "."), ".cache"),
    )
except SpotifyOauthError as e:
    logger.warning(f"Spotify is not configured, only the csv source will work: {e}")
    sp_oauth = None

LOGIN_HINT = "Not logged in to Spotify. Open http://127.0.0.1:8235/login in your browser, then try again."


# ---------------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------------

def get_spotify_client():
    """
    Returns an authorized Spotify client. Raises 503 when Spotify is not configured and 401 when not logged in.
    """
    if sp_oauth is None:
        raise HTTPException(status_code=503, detail="Spotify is not configured. Set SPOTIFY_CLIENT_ID and "
                                                    "SPOTIFY_CLIENT_SECRET in .env, or use the csv source.")
    token_info = sp_oauth.get_cached_token()
    if not token_info:
        raise HTTPException(status_code=401, detail=LOGIN_HINT)
    return spotipy.Spotify(auth=token_info["access_token"])


def spotify_error(e: SpotifyException, action: str):
    """
    Turns a Spotify API error into a JSON response with a useful message.
    """
    logger.error(f"Spotify error while {action}: {e}")
    if e.http_status == 429:
        return JSONResponse(status_code=429, content={"detail": "Spotify rate limit exceeded, please try again later."})
    if e.http_status == 401:
        return JSONResponse(status_code=401, content={"detail": LOGIN_HINT})
    # Spotipy messages look like "https://api.spotify.com/v1/me/:\n Active premium subscription required..."
    message = re.sub(r"^https?://\S+:\s*", "", (e.msg or "").strip()) or str(e)
    return JSONResponse(status_code=502, content={"detail": f"Spotify refused the request while {action}: {message}"})


def library_for(source: str):
    """
    The catalog library of a source. The Spotify library is the last synced Spotify user, so searching it does not
    need the Spotify API.
    """
    if source == "csv":
        return pipeline.CSV_LIBRARY
    if source != "spotify":
        raise HTTPException(status_code=400, detail="source must be csv or spotify")
    library = catalog.get_meta(SPOTIFY_LIBRARY_KEY)
    if not library:
        raise HTTPException(status_code=400, detail="No Spotify songs yet. Sync your liked songs first.")
    return library


def start_processing(library: str, use_llm: bool, limit: int = None):
    try:
        return pipeline.start(library, use_llm=use_llm, limit=limit)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ---------------------------------------------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------------------------------------------

@app.get("/")
def read_root():
    return {"message": "Welcome to the Moodify API. See /docs for the endpoints."}


@app.get("/health")
def health():
    """
    Reports which integrations are configured and whether the LLM is available.
    """
    logged_in = False
    if sp_oauth is not None:
        try:
            logged_in = bool(sp_oauth.cache_handler.get_cached_token())
        except Exception:
            logged_in = False
    return {
        "spotify_configured": sp_oauth is not None,
        "spotify_logged_in": logged_in,
        "genius_configured": lyrics_source.configured(),
        "llm": llm.status(force=True),
        "default_source": DEFAULT_SOURCE,
    }


@app.get("/library_status")
def library_status(source: str = Query(default=DEFAULT_SOURCE, description="csv or spotify")):
    """
    Song counts per processing stage for the source's library.
    """
    try:
        library = library_for(source)
    except HTTPException:
        return {"total": 0, "lyrics_found": 0, "lyrics_not_found": 0, "lyrics_pending": 0, "profiled": 0,
                "indexed": 0, "pending": 0}
    return pipeline.library_status(library)


@app.get("/songs")
def songs(source: str = Query(default=DEFAULT_SOURCE, description="csv or spotify")):
    """
    Every song in the library with its processing state, for inspection.
    """
    result = []
    for s in catalog.get_songs(library_for(source)):
        profile = s["profile"] or {}
        result.append({
            "name": s["name"], "artists": ", ".join(s["artists"]), "lyrics": s["lyrics_status"],
            "genius_match": f"{s['genius_title']} by {s['genius_artist']}" if s["genius_title"] else "",
            "profile": s["profile_status"], "moods": ", ".join(profile.get("moods", [])),
            "occasions": ", ".join(profile.get("occasions", [])), "energy": profile.get("energy"),
            "valence": profile.get("valence"), "summary": profile.get("summary", ""),
            "searchable": bool(s["indexed_hash"]),
        })
    return {"songs": result}


@app.get("/job_status")
def job_status():
    """
    Progress of the current or last processing job.
    """
    return pipeline.job_snapshot()


# ---------------------------------------------------------------------------------------------------------------
# Adding and processing songs
# ---------------------------------------------------------------------------------------------------------------

@app.post("/upload_csv")
async def upload_csv(request: Request,
                     replace: bool = Query(default=False, description="Remove the previously uploaded songs"),
                     process: bool = Query(default=True, description="Start processing right away"),
                     use_llm: bool = Query(default=True, description="Write mood profiles with the LLM"),
                     limit: int = Query(default=None, ge=1, description="Process at most this many songs")):
    """
    Adds the songs of a liked-songs CSV (raw bytes as the request body) to the CSV library and starts processing.
    """
    if pipeline.job_snapshot()["state"] == "running":
        raise HTTPException(status_code=409, detail="A processing job is running. Wait for it to finish.")
    songs = pipeline.parse_csv(await request.body())
    if not songs:
        raise HTTPException(status_code=400, detail="No songs found. The CSV needs track name and artist columns.")
    new = pipeline.add_songs(pipeline.CSV_LIBRARY, songs, replace=replace)
    result = {"message": f"Read {len(songs)} songs from the CSV ({new} new).", "job": None}
    if process:
        result["job"] = start_processing(pipeline.CSV_LIBRARY, use_llm, limit)
    return result


@app.post("/sync_spotify")
def sync_spotify(limit: int = Query(default=50, ge=1, le=2000, description="Number of liked songs to read"),
                 process: bool = Query(default=True, description="Start processing right away"),
                 use_llm: bool = Query(default=True, description="Write mood profiles with the LLM")):
    """
    Reads your latest liked songs from Spotify into the Spotify library and starts processing.
    """
    sp = get_spotify_client()
    try:
        user_id = sp.current_user()["id"]
        songs = pipeline.read_spotify_liked_songs(sp, limit)
    except SpotifyException as e:
        return spotify_error(e, "reading your liked songs")
    library = f"spotify_{user_id}"
    catalog.set_meta(SPOTIFY_LIBRARY_KEY, library)
    new = pipeline.add_songs(library, songs)
    result = {"message": f"Read {len(songs)} liked songs from Spotify ({new} new).", "job": None}
    if process:
        result["job"] = start_processing(library, use_llm)
    return result


@app.post("/process")
def process(source: str = Query(default=DEFAULT_SOURCE, description="csv or spotify"),
            use_llm: bool = Query(default=True, description="Write mood profiles with the LLM"),
            limit: int = Query(default=None, ge=1, description="Process at most this many songs")):
    """
    Processes the songs of the library that still need work (new songs, failed steps, outdated profiles).
    """
    return start_processing(library_for(source), use_llm, limit)


# ---------------------------------------------------------------------------------------------------------------
# Search and playlists
# ---------------------------------------------------------------------------------------------------------------

@app.get("/search")
def search(query: str = Query(min_length=1), k: int = Query(default=5, ge=1, le=50, description="Number of songs"),
           source: str = Query(default=DEFAULT_SOURCE, description="csv or spotify"),
           smart: bool = Query(default=True, description="Use the LLM to interpret the mood")):
    """
    Finds the songs in your library that best match the mood.
    """
    return mood_search.find_songs(library_for(source), query, k, smart)


@app.get("/get_recommendations")
def get_recommendations(query: str = Query(min_length=1), k: int = Query(default=5, ge=1, le=50),
                        source: str = Query(default=DEFAULT_SOURCE, description="csv or spotify"),
                        smart: bool = Query(default=True, description="Use the LLM to interpret the mood")):
    """
    Same as /search, with the songs under "recommendations" (kept for older clients).
    """
    result = mood_search.find_songs(library_for(source), query, k, smart)
    return {**result, "recommendations": result["tracks"]}


@app.post("/create_playlist")
def create_playlist(query: str = Query(min_length=1), k: int = Query(default=10, ge=1, le=50),
                    source: str = Query(default="spotify", description="csv or spotify"),
                    smart: bool = Query(default=True, description="Use the LLM to interpret the mood")):
    """
    Creates a private Spotify playlist with the songs matching the mood. Works for CSV songs too when the CSV had
    Spotify track URIs (Exportify exports do).
    """
    result = mood_search.find_songs(library_for(source), query, k, smart)
    uris = [f"spotify:track:{t['spotify_id']}" for t in result["tracks"] if t["spotify_id"]]
    if not uris:
        raise HTTPException(status_code=404, detail="No matching songs with Spotify IDs found for this mood.")
    sp = get_spotify_client()
    try:
        playlist = sp.user_playlist_create(sp.current_user()["id"], name=f"Moodify: {query}"[:100], public=False,
                                           description=f"Created by Moodify for the mood: {query}"[:300])
        sp.playlist_add_items(playlist["id"], uris)
    except SpotifyException as e:
        return spotify_error(e, "creating the playlist")
    logger.info(f"Created playlist {playlist['id']} with {len(uris)} songs")
    return {**result, "message": f"Playlist 'Moodify: {query}' created with {len(uris)} songs.",
            "playlist_url": playlist.get("external_urls", {}).get("spotify", "")}


# ---------------------------------------------------------------------------------------------------------------
# Spotify login and lyrics
# ---------------------------------------------------------------------------------------------------------------

@app.get("/login")
def login():
    """
    Redirects the user to Spotify's authorization page for OAuth login.
    """
    if sp_oauth is None:
        raise HTTPException(status_code=503, detail="Spotify is not configured in .env.")
    return RedirectResponse(sp_oauth.get_authorize_url())


@app.get("/callback")
def callback(request: Request):
    """
    Handles the redirect back from Spotify and exchanges the authorization code for an access token.
    """
    if sp_oauth is None:
        raise HTTPException(status_code=503, detail="Spotify is not configured in .env.")
    error = request.query_params.get("error")
    if error:
        raise HTTPException(status_code=400, detail=f"Spotify login was not completed: {error}")
    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code. Start the login at /login.")
    try:
        sp_oauth.get_access_token(code, as_dict=False)
    except SpotifyOauthError as e:
        raise HTTPException(status_code=401, detail=f"Could not authenticate with Spotify: {e}")
    logger.info("Access token obtained successfully!")
    return HTMLResponse(f"<h2>Logged in to Spotify.</h2><p>You can close this tab and go back to "
                        f"<a href='{FRONTEND_URL}'>Moodify</a>.</p>")


@app.get("/lyrics")
def lyrics(artist: str, title: str):
    """
    Looks up the lyrics of a song on Genius, using the same matching as the processing pipeline.
    """
    if not lyrics_source.configured():
        raise HTTPException(status_code=503, detail="Genius is not configured. Set GENIUS_API_TOKEN in .env.")
    status, text, match = lyrics_source.find_lyrics(title, [artist])
    if status == "error":
        raise HTTPException(status_code=502, detail="The Genius lookup failed. Try again in a moment.")
    if status != "found":
        raise HTTPException(status_code=404, detail="No reliable lyrics match found on Genius.")
    return {"title": match["title"], "artist": match["artist"], "url": match["url"], "lyrics": text}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8235)
