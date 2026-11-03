# Presentation Guide

Suggested length: 12 slides, about 10–12 minutes including the demo. Replace anything in [brackets].

| # | Slide | Content | Visual |
|---|---|---|---|
| 1 | Title | Moodify: search your music by mood. [Names, course, date] | Logo or app screenshot |
| 2 | Problem | People choose music by feeling, but apps organise by artist and genre. Hinglish requests are harder still. | Example: "dil toota hai" |
| 3 | Solution | Search your own liked songs by mood, in plain language, with a reason for each result. | Results screenshot |
| 4 | Features | Two sources, accurate lyrics, mood profiles, hybrid search, LLM switch, Docker. | Icon grid |
| 5 | Architecture | Streamlit, FastAPI, SQLite, ChromaDB, Ollama, Genius, Spotify. | Overview diagram from the architecture file |
| 6 | Processing | Ingest → lyrics → alternates → mood profile → index. Resumable. | Processing flow, sample profile |
| 7 | Search | Parse → retrieve → rank → explain. Show the score formula. | Search flow |
| 8 | Key decisions | Offline LLM, mood text over raw lyrics, fixed vocabulary, optional LLM. | Decision table, shortened |
| 9 | Demo | Live or recorded, see the script below. | Live app |
| 10 | Results | Smart 66%, light 60%, 6 s vs 0.1 s. State the small test size. | Bar chart |
| 11 | Limitations and future work | Misread lyrics, no audio, no discovery. Next: audio, filters, feedback. | Two columns |
| 12 | Conclusion | Mood search works on modest hardware, and the LLM can be switched off. Questions? | Repo link |

## Speaking points for the key slides
- **Architecture:** "The LLM is optional. Every other part keeps working if it's off or fails."
- **Processing:** "The heavy AI work happens once per song, so searching stays fast."
- **Search:** "Three signals: meaning, exact words and structured mood."
- **Results:** "This is a small test of 10 queries, so we use it to compare versions, not to claim real-world accuracy."
- **Limitations:** Mention Balam Pichkari yourself. Showing that you found and understood a failure is stronger than hiding it.

## Demo script (about 3 minutes)
**Before presenting:** process the songs in advance, because live processing is too slow. Have a screenshot or recording ready as a backup. Keep the app open and run one smart search, so the model is already loaded.

1. **Library page:** show the song counts and open the *Songs* table to show lyrics matches and mood profiles.
2. **Search "romantic date night":** a strong result, with reasons on screen.
3. **Search "gym after a breakup" with smart search on, then off:** shows why parsing the request helps.
4. **Search "holi festival":** keyword search finds Balam Pichkari.
5. **Show the LLM setting:** explain `LLM_ENABLED=false`. Don't restart it live.

**Avoid in the demo:** "spiritual and peaceful" and "breakup", which give weak results, and Spotify login, unless you've confirmed it works beforehand.

## Design tips
- One idea per slide, with six lines or fewer.
- Use real screenshots, not stock images.
- Export the Mermaid diagrams at https://mermaid.live.
- Always state the test size next to the percentages.
