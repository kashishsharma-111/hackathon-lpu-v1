import csv
import io
import os
import time

import requests
import streamlit as st

# Streamlit frontend for the Moodify FastAPI backend.
#
# Data sources:
# - csv: liked songs come from an uploaded CSV (for example from exportify.net). No Spotify API needed.
# - spotify: liked songs come from the Spotify API. Needs a working Spotify developer app and a login at BASE_URL/login.
# Both go through the same pipeline in the backend: Genius lyrics -> LLM mood profile -> vector index.
#
# Note: comments are used instead of docstrings here, because Streamlit renders bare strings on the page.
#
# Busy handling: clicking an action button sets st.session_state.running in an on_click callback. The script
# then reruns with every widget disabled, runs the task, stores the outcome in st.session_state.result,
# clears the flag and reruns once more to re-enable the widgets and show the outcome.

# Where this app reaches the FastAPI backend, and where your browser reaches it (for the Spotify login link).
# They differ in Docker, where the backend is a separate container.
BASE_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8235")
PUBLIC_URL = os.getenv("PUBLIC_BACKEND_URL", "http://127.0.0.1:8235")
VALENCE_LABELS = {1: "very sad", 2: "sad", 3: "bittersweet", 4: "upbeat", 5: "very upbeat"}

st.set_page_config(page_title="Moodify", page_icon="🎵")
st.session_state.setdefault("running", None)
st.session_state.setdefault("result", None)
busy = st.session_state.running is not None


def start(task):
    st.session_state.running = task
    st.session_state.result = None


def cancel():
    st.session_state.running = None


def call_backend(method, path, timeout=300, **kwargs):
    """Calls the backend and returns (json data, error message)."""
    try:
        response = requests.request(method, f"{BASE_URL}{path}", allow_redirects=False, timeout=timeout, **kwargs)
    except requests.ConnectionError:
        return None, f"Backend is not reachable at {BASE_URL}. Is uvicorn running?"
    except requests.Timeout:
        return None, "The backend took too long to answer."
    except requests.RequestException as e:
        return None, f"Request failed: {e}"
    try:
        data = response.json()
    except ValueError:
        return None, f"Unexpected response from the backend (HTTP {response.status_code})."
    if response.status_code != 200:
        detail = data.get("detail") or data.get("message") or f"Request failed (HTTP {response.status_code})."
        return None, detail if isinstance(detail, str) else str(detail)
    return data, None


def finish(page, **result):
    """Stores the outcome of the running task, frees the UI and reruns to show it."""
    st.session_state.result = {"page": page, **result}
    st.session_state.running = None
    st.rerun()


def wait_for_job(page):
    """Polls the backend processing job with a progress bar until it finishes, then finishes the task."""
    progress = st.progress(0.0, text="Starting...")
    while True:
        job, error = call_backend("GET", "/job_status", timeout=10)
        if error:
            finish(page, error=error)
        if job["state"] != "running":
            break
        total = max(job["total"], 1)
        progress.progress(job["done"] / total,
                          text=f"Processed {job['done']} of {job['total']} songs. Now: {job['current'] or '...'}")
        time.sleep(1.5)
    if job["state"] == "error":
        finish(page, error=job["error"])
    if job["state"] != "done":
        finish(page, error="The processing job was interrupted (the backend restarted). "
                           "Finished songs are saved; process again to continue.")
    finish(page, message=job["message"])


def show_interpretation(parsed):
    parts = []
    if parsed.get("moods"):
        parts.append(f"moods: {', '.join(parsed['moods'])}")
    if parsed.get("occasions"):
        parts.append(f"for: {', '.join(parsed['occasions'])}")
    if parsed.get("energy"):
        parts.append(f"{parsed['energy']} energy")
    if parsed.get("valence"):
        parts.append(VALENCE_LABELS[parsed["valence"]])
    st.caption(f"🧠 Understood as: {parsed['description']}  \n{' · '.join(parts)}")


def show_tracks(result):
    """Lists tracks with reasons, summaries and Spotify links, plus notes and a CSV download."""
    if result.get("interpretation"):
        show_interpretation(result["interpretation"])
    for note in result.get("notes", []):
        st.info(note)
    tracks = result["tracks"]
    if not tracks:
        if not result.get("notes"):
            st.info("No matching songs found.")
        return
    rows = []
    for i, track in enumerate(tracks, 1):
        artists = [artist["name"] for artist in track.get("artists", [])]
        url = track.get("url", "")
        line = f"{i}. **{track.get('name')}** by {', '.join(artists)}"
        if url:
            line += f" ([open in Spotify]({url}))"
        st.markdown(line)
        details = [d for d in (track.get("reason"), track.get("summary")) if d]
        if details:
            st.caption("&nbsp;&nbsp;&nbsp;&nbsp;" + "  \n&nbsp;&nbsp;&nbsp;&nbsp;".join(details))
        rows.append([track.get("name"), ";".join(artists), url])

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Track Name", "Artist Name(s)", "Spotify URL"])
    writer.writerows(rows)
    st.download_button("Download as CSV", buffer.getvalue(), file_name=result["file_name"], mime="text/csv",
                       on_click="ignore", disabled=busy)


def show_result(page):
    """Shows the stored outcome of the last task if it belongs to the current page."""
    result = st.session_state.result
    if not result or result["page"] != page:
        return
    if result.get("error"):
        st.error(result["error"])
        return
    if result.get("message"):
        st.success(result["message"])
    if result.get("playlist_url"):
        st.markdown(f"[Open the playlist in Spotify]({result['playlist_url']})")
    if "tracks" in result:
        show_tracks(result)
    if "lyrics" in result:
        st.text(result["lyrics"])


st.title("🎵 Moodify")
st.caption("Find songs from your library that match a mood.")

health, health_error = call_backend("GET", "/health", timeout=10)
llm_available = bool(health and health["llm"]["available"])

source = st.sidebar.radio(
    "Data source", ("csv", "spotify"),
    format_func=lambda s: "CSV file" if s == "csv" else "Spotify account",
    disabled=busy,
)

# Creating a playlist needs the Spotify API, so it is only offered for the Spotify source
if source == "csv":
    options = ("Library", "Find Songs by Mood", "Get Lyrics")
else:
    options = ("Library", "Find Songs by Mood", "Create Playlist", "Get Lyrics")
option = st.sidebar.selectbox("Select Option", options, disabled=busy)
page = (source, option)

# Status of the backend and its integrations
st.sidebar.divider()
if health_error:
    st.sidebar.error(health_error)
else:
    llm_info = health["llm"]
    if llm_available:
        st.sidebar.success(f"LLM: {llm_info['model']}")
    else:
        st.sidebar.warning(f"LLM off: {llm_info['reason']}")
    if not health["genius_configured"]:
        st.sidebar.warning("Genius not configured: songs are searchable by title and genre only.")
    if source == "spotify":
        if not health["spotify_configured"]:
            st.sidebar.error("Spotify is not configured in .env.")
        elif health["spotify_logged_in"]:
            st.sidebar.success(f"Spotify: logged in ([log in again]({PUBLIC_URL}/login))")
        else:
            st.sidebar.warning(f"Spotify: not logged in. [Log in]({PUBLIC_URL}/login)")
if busy:
    # Safety valve in case a run was interrupted and left the UI locked
    st.sidebar.button("Unlock UI", on_click=cancel, help="Use this if the app seems stuck. "
                      "The server may still finish the current job.")

running = st.session_state.running


def llm_toggle():
    return st.checkbox("Write mood profiles with the LLM (best results, about 20 s per song on CPU)",
                       value=llm_available, disabled=busy or not llm_available)


def smart_toggle():
    return st.toggle("Smart search (LLM)", value=llm_available, disabled=busy or not llm_available,
                     help="The LLM reads your mood request and songs are ranked on mood, occasion, energy and "
                          "positivity as well as text similarity. Takes a few seconds.")


# Library: add songs, process them and see their state
if option == "Library":
    st.header("Your Library")
    job, _ = call_backend("GET", "/job_status", timeout=10)
    job_running = bool(job and job["state"] == "running")

    if source == "csv":
        st.write("Upload your liked songs as a CSV (for example from exportify.net). "
                 "It needs track name and artist columns.")
        uploaded = st.file_uploader("Liked songs CSV", type="csv", disabled=busy)
        replace = st.checkbox("Replace the songs uploaded before", value=True, disabled=busy,
                              help="Untick to add these songs to the ones already in the library.")
        use_llm = llm_toggle()
        if uploaded is not None:
            st.button("Upload and process", type="primary", disabled=busy or job_running, on_click=start,
                      args=("csv_upload",))
        if running == "csv_upload":
            with st.spinner("Uploading..."):
                data, error = call_backend("POST", "/upload_csv", data=uploaded.getvalue(),
                                           params={"replace": replace, "use_llm": use_llm})
            if error:
                finish(page, error=error)
            wait_for_job(page)
    else:
        st.write("Reads your latest liked songs from Spotify. Songs already in the library are kept as they are.")
        limit = st.slider("Number of liked songs to read:", min_value=10, max_value=500, value=50, step=10,
                          disabled=busy)
        use_llm = llm_toggle()
        st.button("Sync liked songs and process", type="primary", disabled=busy or job_running, on_click=start,
                  args=("spotify_sync",))
        if running == "spotify_sync":
            with st.spinner("Reading your liked songs from Spotify..."):
                data, error = call_backend("POST", "/sync_spotify", params={"limit": limit, "use_llm": use_llm})
            if error:
                finish(page, error=error)
            wait_for_job(page)

    if running == "process":
        data, error = call_backend("POST", "/process", params={"source": source, "use_llm": use_llm})
        if error:
            finish(page, error=error)
        wait_for_job(page)
    if running == "watch_job":
        wait_for_job(page)

    status, _ = call_backend("GET", "/library_status", params={"source": source}, timeout=30)
    if status and status["total"]:
        st.subheader("Status")
        cols = st.columns(4)
        cols[0].metric("Songs", status["total"])
        cols[1].metric("With lyrics", status["lyrics_found"])
        cols[2].metric("Mood profiles", status["profiled"])
        cols[3].metric("Searchable", status["indexed"])
        if status["lyrics_not_found"]:
            st.caption(f"{status['lyrics_not_found']} songs had no reliable lyrics match on Genius; they are "
                       "searchable by title and genre only.")
        if job_running:
            st.info(f"Processing: {job['done']} of {job['total']} songs done. Songs become searchable as they finish.")
            st.button("Show progress", on_click=start, args=("watch_job",), disabled=busy)
        elif status["pending"]:
            st.button(f"Process {status['pending']} pending songs", type="primary", disabled=busy,
                      on_click=start, args=("process",))

        with st.expander("Songs"):
            songs, error = call_backend("GET", "/songs", params={"source": source}, timeout=30)
            if error:
                st.error(error)
            else:
                st.dataframe(songs["songs"], hide_index=True,
                             column_order=["name", "artists", "lyrics", "profile", "moods", "occasions", "energy",
                                           "valence", "searchable", "summary", "genius_match"])

# Find songs by mood (both sources)
elif option == "Find Songs by Mood":
    st.header("Find Songs by Mood")
    st.write("Finds the songs in your library that best match the mood.")
    query = st.text_input("Mood or theme", placeholder="e.g. rainy evening, gym after a breakup, dil toota hai",
                          disabled=busy)
    k = st.slider("Number of songs:", min_value=1, max_value=20, value=5, disabled=busy)
    smart = smart_toggle()
    st.button("Find songs", type="primary", disabled=busy, on_click=start, args=("search",))

    if running == "search":
        if not query.strip():
            finish(page, error="Enter a mood or theme first.")
        with st.spinner("Understanding your mood and picking songs..." if smart else "Searching..."):
            data, error = call_backend("GET", "/search",
                                       params={"query": query, "k": k, "source": source, "smart": smart})
        if error:
            finish(page, error=error)
        finish(page, tracks=data["tracks"], interpretation=data.get("interpretation"),
               notes=data.get("notes", []), file_name=f"moodify - {query}.csv")

# Create Playlist (Spotify source only)
elif option == "Create Playlist":
    st.header("Create a Playlist")
    st.write("Creates a private playlist in your Spotify account with the songs matching the mood.")
    query = st.text_input("Mood or theme", placeholder="e.g. rainy evening, gym after a breakup", disabled=busy)
    k = st.slider("Number of songs:", min_value=1, max_value=30, value=10, disabled=busy)
    smart = smart_toggle()
    st.button("Create Playlist", type="primary", disabled=busy, on_click=start, args=("playlist",))

    if running == "playlist":
        if not query.strip():
            finish(page, error="Enter a mood or theme first.")
        with st.spinner("Finding songs and creating your playlist..."):
            data, error = call_backend("POST", "/create_playlist",
                                       params={"query": query, "k": k, "source": "spotify", "smart": smart})
        if error:
            finish(page, error=error)
        finish(page, message=data["message"], playlist_url=data.get("playlist_url"), tracks=data["tracks"],
               interpretation=data.get("interpretation"), notes=data.get("notes", []),
               file_name=f"moodify - {query}.csv")

# Get Lyrics (works with either source, uses Genius)
elif option == "Get Lyrics":
    st.header("Get Lyrics")
    title = st.text_input("Song title", disabled=busy)
    artist = st.text_input("Artist", disabled=busy)
    st.button("Get Lyrics", type="primary", disabled=busy, on_click=start, args=("lyrics",))

    if running == "lyrics":
        if not title.strip() or not artist.strip():
            finish(page, error="Enter both a song title and an artist.")
        with st.spinner("Fetching lyrics from Genius..."):
            data, error = call_backend("GET", "/lyrics", params={"title": title, "artist": artist})
        if error:
            finish(page, error=error)
        finish(page, message=f"{data['title']} by {data['artist']}", lyrics=data["lyrics"])

show_result(page)
