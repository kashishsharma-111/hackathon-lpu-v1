# How It Works

The system has two phases: **processing** songs once, then **searching** them as often as needed. CSV and Spotify differ only in how songs enter the system.

## Phase 1: Processing (background job)

| Step | What happens | Output |
|---|---|---|
| 1. Ingest | Read songs from the CSV or Spotify | Row in the SQLite catalog |
| 2. Lyrics | Score up to 10 Genius results per search on title and artist; accept only a confident match | Lyrics, or "not found" |
| 3. Alternates | Look for English and romanized versions on Genius | Extra lyric versions |
| 4. Mood profile *(LLM on)* | The LLM reads the whole lyrics: English first, then romanized, then original | Structured JSON profile |
| 5. Index | Build a short text document for the song and embed it with bge-m3 | Vector in ChromaDB |

**Lyrics match rule:** `score = 0.7 × title similarity + artist score`, accepted at 0.85 or above. The artist score is 0.3 for the primary artist and 0.2 for a featured artist. Remix, unplugged and similar versions lose 0.25 of title similarity unless the requested title is one too.

**What gets embedded:**
- With a mood profile: title, artists, album, genres, summary, moods, occasions, themes, energy and language. Raw lyrics are left out because they would drown out the mood signal.
- Without a profile (light mode): the same metadata plus up to 1500 characters of lyrics.
- Without lyrics: metadata only, so the song is searchable by title and genre only.

**Example mood profile**
```json
{
  "summary": "A motivational journey through life's challenges.",
  "moods": ["energetic", "motivated"],
  "occasions": ["workout"],
  "themes": ["perseverance"],
  "energy": "high",
  "valence": 3,
  "language": "Hindi"
}
```
Moods and occasions must come from fixed word lists, enforced by a JSON schema. This lets songs and requests be compared directly.

## Phase 2: Searching

### Smart search (LLM on, about 6 s)
1. **Parse:** the LLM converts the request into moods, occasions, energy, valence, a description and keywords. This is cached per query.
2. **Retrieve:** run vector search with the typed query and with the parsed fields, and keyword search with the query words and LLM keywords. Each search returns at least 20 candidates.
3. **Rank** every candidate:
   ```
   score = 0.5 × vector similarity + 0.5 × structured match + 0.35 × keyword match
   structured match = moods 0.35 + occasions 0.30 + valence 0.20 + energy 0.15
   ```
4. **Explain:** build each song's reason from the fields that matched.

### Plain search (no LLM at search time, about 0.1 s)
Run vector search and keyword search on the typed query, then merge the lists with reciprocal rank fusion: `score = Σ 1 / (60 + rank)`.

## Failure handling
| Situation | Behaviour |
|---|---|
| Ollama down or LLM output invalid | Search falls back to plain and shows a note |
| Profile fails for a song | Marked as an error and retried on the next run |
| Job interrupted | Finished songs are kept and the next run continues |
| No confident lyrics match | Song stays searchable by title and genre |
| Prompt changed | Raising `PROFILE_VERSION` re-profiles songs without fetching lyrics again |
