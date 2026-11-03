# Moodify

Moodify finds songs from your own library that match a mood, such as "rainy evening", "gym after a breakup" or
"dil toota hai". It reads the lyrics of your liked songs, has a small local LLM describe each song's mood once,
and then searches by mood. With a working Spotify app it can also create the playlist in your Spotify account.

## How it works

The same pipeline runs for both sources:

```
CSV upload / Spotify liked songs
  -> catalog (SQLite, one row per song with the state of every stage)
  -> background job, per song:
       lyrics   Genius, accepted only on a confident title + artist match
       alternates  English and romanized lyrics, when Genius has them
       profile  local LLM reads the whole lyrics: summary, moods, occasions, themes, energy, valence (1-5)
       index    compact "mood document" embedded with BAAI/bge-m3 into ChromaDB

mood query
  -> LLM parses it into the same fields (moods, occasions, energy, valence, description)
  -> vector search with the query and the parsed fields, plus keyword search
  -> score = 50% vector similarity + 50% match on moods / occasions / energy / valence + a keyword bonus
  -> songs with the reason they matched ("workout · motivated · high energy · mentions gym")
```

- **Data sources.** `csv` reads liked songs from an exported CSV and needs no Spotify API access. `spotify` reads
  them from the Spotify API. Choose in the app's sidebar, or per request with `?source=csv|spotify`.
- **Lyrics accuracy.** Several Genius hits are scored and only a near-exact title with a matching artist is
  accepted; translations, romanizations and remixes are skipped. Songs without a confident match stay in the
  library but are searchable by title and genre only.
- **Keyword search.** A BM25 search over title, artists, album, genres, mood profile and lyrics (all versions) finds
  specific words like "holi", "arijit" or "baarish". It needs no LLM, so plain search uses it too.
- **The LLM does its heavy work once per song**, not per search. A search makes one short LLM call (cached) and
  ranks deterministically, so it takes a few seconds and the reasons are always accurate.
- **Robustness.** Every stage is recorded in the catalog, so the job resumes after a crash and only re-runs what
  is missing (for example, new profiles after a prompt change: bump `PROFILE_VERSION` in `llm.py`). Songs become
  searchable in batches while the job runs. If Ollama is down or returns unusable output, profiles are retried
  on the next run and searches fall back to plain search, with a note saying so.

| File | Purpose |
|------|---------|
| `backend.py` | FastAPI endpoints: library, processing, search, Spotify login and playlists |
| `pipeline.py` | Sources (CSV, Spotify) and the background processing job |
| `catalog.py` | SQLite song catalog (`moodify.db`) |
| `lyrics.py` | Genius lookup with match scoring |
| `llm.py` | Ollama client: song profiles and query parsing, with schema validation and fallbacks |
| `index.py` | Mood documents and the ChromaDB vector index |
| `keyword_search.py` | BM25 keyword search over the library |
| `search.py` | Mood search and ranking |
| `app.py` | Streamlit frontend |
| `eval.py` | Precision@5 of plain and smart search on the sample library |
| `Dockerfile`, `docker-compose.yml` | Container setup: backend, frontend and a local LLM (Ollama) |
| `start.bat`, `stop.bat` | One-click start and stop on Windows (Docker Desktop) |
| `run.sh` | Starts backend and frontend together without Docker |
| `sample_hindi_songs_30.csv` | 30 Hindi songs across moods, to try the app with (`sample_hindi_songs.csv` has 50) |

## LLM on or off

The local LLM is optional. One setting in `.env` switches it for the whole pipeline on that machine:

```
LLM_ENABLED=true    # mood profiles and smart search (needs about 8 GB of RAM for Docker)
LLM_ENABLED=false   # light mode, for machines that can't run a local LLM
```

| | LLM on | Light mode |
|---|---|---|
| What gets embedded | Mood profile written by the LLM from the lyrics | An excerpt of the lyrics |
| Search | Mood parsing, vector, keyword and mood-field ranking | Vector and keyword search |
| Reasons on results | Matched moods, occasions, energy, keywords | Matched keywords only |
| Docker | Also runs Ollama and downloads the model | No Ollama, about 5 GB less to download |
| Memory | About 5-6 GB | About 3 GB while processing songs, 1 GB idle |
| Processing (CPU) | About 15-20 seconds per song | About 8 seconds per song |
| Search time (CPU) | About 6 seconds | About 0.1 seconds |
| Precision@5 on the sample (`eval.py`) | 66% | 60% |

Both modes need the embedding model (BAAI/bge-m3, about 2 GB), which runs on the CPU. You can switch at any time:
turning the LLM on later only adds profiles to songs that lack one (the *Process* button on the Library page), and
turning it off keeps the profiles already written. In Docker, restart with `start.bat` after changing the setting.

## Run with Docker (recommended, works on Windows, macOS and Linux)

Everything runs in containers, including the local LLM, so nothing else needs installing.

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it. On Windows it needs the
   WSL 2 backend (the default) and at least **8 GB of memory** for Docker with the LLM on: the embedding model and
   the LLM together use about 5-6 GB. Light mode needs less (see [LLM on or off](#llm-on-or-off)). If needed, raise the limit in `%UserProfile%\.wslconfig`:
   ```
   [wsl2]
   memory=10GB
   ```
   and run `wsl --shutdown` once.
2. Optional: copy `.env.example` to `.env` and add your Genius token and Spotify credentials. Without a `.env` the
   app still runs, with lyrics and Spotify switched off (`start.bat` creates the file for you). Set
   `LLM_ENABLED=false` here for light mode.
3. Start it:
   - **Windows:** double-click `start.bat`. It reads `LLM_ENABLED` and starts the right services.
   - **macOS / Linux, LLM on:** `docker compose --profile llm up -d --build --wait`
   - **macOS / Linux, light mode:** `docker compose up -d --build --wait`

   The first start builds the image and downloads about 10 GB (the app image, the Ollama image, the 2 GB
   embedding model and the 2 GB LLM), which takes 15-30 minutes depending on your connection. Light mode skips
   the Ollama image and the LLM, so about 5 GB. Smart search switches on by itself when the LLM download is done; the sidebar shows its status.
4. Open http://localhost:8501.

Stop with `stop.bat` or `docker compose --profile llm down`. Your songs, mood profiles, Spotify login and downloaded models are
kept in Docker volumes; adding `-v` to that command deletes them too. The ports are only opened on your own machine.
Logs: `docker compose logs -f backend`. After changing code or `.env`, run `docker compose up -d --build` again.
To use your Spotify account in Docker, the redirect URI in the Spotify dashboard must be
`http://127.0.0.1:8235/callback` (the default).

## Setup without Docker

Needs Python 3.10+ and about 5 GB of disk for the models. On Windows, use Docker above or run `run.sh` in WSL.

1. Create a virtual environment and install the dependencies:
   ```bash
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   ```
2. Create your `.env` from the example and fill it in:
   ```bash
   cp .env.example .env
   ```
   - `GENIUS_API_TOKEN`: create a client at https://genius.com/api-clients and use its *Client Access Token*.
     Without it, songs are searchable by title and genre only.
   - `SPOTIFY_*`: only for the `spotify` source. Create an app at https://developer.spotify.com/dashboard, add
     the redirect URI `http://127.0.0.1:8235/callback` and your account under *User Management*. The owner of the
     app needs Spotify Premium.
3. Optional, for mood profiles and smart search (skip it with `LLM_ENABLED=false`), install [Ollama](https://ollama.com) and pull the model:
   ```bash
   ollama pull qwen2.5:3b
   ```

## Run without Docker

```bash
./run.sh
```

Then open http://localhost:8501. The first start downloads the embedding model (about 2 GB) and can take a few
minutes. API docs are at http://127.0.0.1:8235/docs.

## Using the app

1. **Library**: upload your liked songs CSV (for example from https://exportify.net, or try
   `sample_hindi_songs_30.csv`) and click *Upload and process*, or sync your Spotify liked songs. Processing takes
   about 20 seconds per song on CPU with mood profiles; songs become searchable while it runs. The *Songs* table
   shows each song's lyrics match and mood profile.
2. **Find Songs by Mood**: type a mood in English, Hindi or Hinglish. The app shows how it understood the mood and
   why each song matched. Download the list as CSV to import it elsewhere.
3. **Create Playlist** (Spotify source): creates a private playlist in your Spotify account. Log in first with
   the link in the sidebar.
4. **Get Lyrics**: looks up lyrics on Genius with the same matching as the pipeline.

To measure search quality after a change, process `sample_hindi_songs_30.csv` and run `venv/bin/python eval.py`.

## Configuration

All settings live in `.env` (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MOODIFY_SOURCE` | `csv` | Default data source for API requests |
| `LLM_ENABLED` | `true` | Set to `false` for light mode on machines that can't run a local LLM |
| `LLM_MODEL` | `qwen2.5:3b` | Any Ollama chat model, for example `llama3.2` or `qwen3:8b` |
| `OLLAMA_URL` | `http://localhost:11434` | Where Ollama runs |
| `LLM_TIMEOUT` | `180` | Seconds before an LLM call is abandoned |

---
Credit:

|Name|Email|
|----|-----|
|Vaishnavi Ratnasabapathy|vratnasa@uwaterloo.ca|
|Paniz Ojaghi|paniz.ojaghi@uwaterloo.ca|

MIT License

Copyright (c) 2024, Vaishnavi Ratnasabapathy, Paniz Ojaghi

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
