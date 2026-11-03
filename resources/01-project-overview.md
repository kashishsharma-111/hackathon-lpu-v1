# Project Overview

## Problem
Music apps organise songs by artist, album or genre, but people often choose music by feeling or situation: "gym after a breakup", "rainy evening", "dil toota hai". A large liked-songs library is hard to search this way, especially for Hindi and Punjabi songs described in Hinglish.

## Solution
Moodify searches **your own liked songs** by mood. It reads each song's lyrics, optionally has a local AI model describe the song's mood, and ranks songs against a request typed in plain English, Hindi or Hinglish.

## Key features
- **Two song sources:** a CSV export, or Spotify liked songs through the Spotify API.
- **Accurate lyrics matching:** lyrics are accepted only on a confident title and artist match.
- **Mood profiles:** a local LLM writes each song's summary, moods, occasions, themes, energy and positivity.
- **Hybrid search:** combines meaning (vector search), exact words (keyword search) and mood fields.
- **Explained results:** each result shows why it matched, for example `motivated · high energy`.
- **LLM on/off switch:** `LLM_ENABLED=false` gives a light mode for low-resource machines.
- **Resumable processing:** an interrupted job continues where it stopped.
- **One-click Docker setup:** `start.bat` on Windows.

## Tech stack
| Layer | Technology | Why |
|---|---|---|
| Frontend | Streamlit | Fast to build an interactive Python UI |
| Backend | FastAPI | Clean REST API with automatic docs |
| Embeddings | BAAI/bge-m3 | Multilingual, handles Hindi and English together |
| Vector database | ChromaDB | Simple, local and persistent |
| Keyword search | BM25 (own implementation) | Finds exact words, no extra service |
| Song catalog | SQLite | Tracks each song's processing state, no server needed |
| LLM | qwen2.5:3b via Ollama | Small enough to run on a CPU, private and free |
| Lyrics | Genius API | Broad lyrics coverage, including Indian songs |
| Music data | Spotify API (Spotipy) | Liked songs and playlist creation |
| Deployment | Docker Compose | Runs the same way on any machine |

## Credit
Based on the original [Moodify](https://github.com/vaishnavir15/Moodify) by Vaishnavi Ratnasabapathy and Paniz Ojaghi (MIT License). The pipeline, search and deployment were redesigned for this version.
