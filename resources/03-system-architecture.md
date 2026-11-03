# System Architecture

## Overview
```mermaid
flowchart LR
    U[User browser] --> FE[Streamlit frontend :8501]
    FE -->|REST| BE[FastAPI backend :8235]
    BE --> CAT[(SQLite catalog)]
    BE --> VEC[(ChromaDB vectors)]
    BE --> EMB[bge-m3 embeddings]
    BE -.optional.-> LLM[Ollama: qwen2.5:3b]
    BE --> G[Genius API]
    BE -.spotify source only.-> S[Spotify API]
```

## Processing flow
```mermaid
flowchart TD
    A[CSV upload] --> C[Catalog]
    B[Spotify liked songs] --> C
    C --> D[Fetch and match lyrics]
    D -->|found| E[Fetch English and romanized lyrics]
    D -->|not found| H[Build document: metadata only]
    E -->|LLM on| F[Write mood profile]
    E -->|LLM off| G[Build document: metadata + lyrics]
    F --> I[Build document: metadata + profile]
    G --> J[Embed and store in ChromaDB]
    H --> J
    I --> J
```

## Search flow
```mermaid
flowchart TD
    Q[Mood request] --> M{Smart search and LLM available?}
    M -->|No| P[Vector + keyword search on typed words]
    P --> R[Merge by rank fusion]
    M -->|Yes| L[LLM parses the request into fields]
    L --> V[Vector + keyword search]
    V --> W[Score: vector + mood fields + keyword]
    R --> T[Ranked songs with reasons]
    W --> T
```

## Modules
| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI: library, mood search, playlist and lyrics pages |
| `backend.py` | REST endpoints and Spotify login |
| `pipeline.py` | CSV and Spotify sources, background processing job |
| `catalog.py` | SQLite storage of songs and stage status |
| `lyrics.py` | Genius lookup and match scoring |
| `llm.py` | Ollama client, schemas, validation and the on/off switch |
| `index.py` | Mood documents, embeddings and ChromaDB |
| `keyword_search.py` | BM25 keyword index |
| `search.py` | Smart and plain search, scoring and reasons |
| `eval.py` | Search-quality measurement |

## Data model
Main table: `songs`, with primary key `(library, track_id)`.

| Group | Columns |
|---|---|
| Identity | `library`, `track_id`, `position`, `spotify_id`, `url` |
| Metadata | `name`, `artists`, `album`, `genres` |
| Lyrics | `lyrics_status`, `lyrics`, `lyrics_english`, `lyrics_romanized`, `alt_status`, `genius_title`, `genius_artist`, `genius_url` |
| Profile | `profile` (JSON), `profile_status`, `profile_version` |
| Index | `indexed_hash` (detects when a song's document has changed) |

Each stage has its own status, which is what makes processing resumable. A library is either `csv_local` or `spotify_<user id>`.

## Main API endpoints
| Method and path | Purpose |
|---|---|
| `POST /upload_csv` | Add CSV songs and start processing |
| `POST /sync_spotify` | Read liked songs and start processing |
| `POST /process` | Process songs with pending work |
| `GET /job_status` | Processing progress |
| `GET /library_status`, `GET /songs` | Stage counts and per-song state |
| `GET /search` | Mood search |
| `POST /create_playlist` | Create a private Spotify playlist |
| `GET /login`, `GET /callback` | Spotify login |
| `GET /lyrics` | Lyrics lookup |
| `GET /health` | Status of Spotify, Genius and the LLM |

Interactive docs are at `http://127.0.0.1:8235/docs`.

## Deployment
```mermaid
flowchart LR
    subgraph Docker Compose
      F[frontend]
      B[backend]
      O[ollama: llm profile]
      P[ollama-pull: llm profile, runs once]
    end
    F --> B
    B -.-> O
    P --> O
    B --- V1[(data volume)]
    O --- V2[(ollama volume)]
```
- Frontend and backend share one 2.29 GB image with CPU-only PyTorch.
- `start.bat` reads `LLM_ENABLED` and starts Ollama only when it's on.
- Volumes keep songs, models and the Spotify login across restarts.
- Ports bind to `127.0.0.1` only, because the API has no authentication.

## Key design decisions
| Decision | Reason | Trade-off |
|---|---|---|
| LLM runs once per song, not per result | Searches stay fast and repeatable | A wrong profile stays until re-processed |
| Embed mood text, not raw lyrics | Stronger mood signal | Vector search can't find exact lyric lines (keyword search can) |
| Fixed mood vocabulary | Direct comparison without another LLM call | Less expressive than free text |
| Local LLM | Private and free | Slower and less accurate than hosted models |
| LLM optional | Runs on low-resource machines | Light mode has less mood understanding |
