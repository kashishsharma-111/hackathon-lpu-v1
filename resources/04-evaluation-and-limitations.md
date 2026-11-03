# Evaluation and Limitations

## Method
`eval.py` runs 10 labelled mood queries over 30 Hindi songs and measures **precision@5**: the share of the top 5 results that are in a hand-written expected set. When a query has fewer than 5 relevant songs, it's scored against that smaller number.

## Results
| Mode | Precision@5 | Search time |
|---|---|---|
| Smart search (LLM on) | **66%** | about 6.6 s |
| Plain search on LLM-profiled songs | 59% | about 0.1 s |
| Light mode (no LLM anywhere) | 60% | about 0.1 s |

Progress during development:

| Stage | Plain | Smart |
|---|---|---|
| First redesign | 49% | 55% |
| Keyword search added | 63% | not measured |
| Songs re-profiled from romanized/English lyrics | 59% | 66% |

**How to read these:** one query is worth 10 points, so gaps under about 10 points may be noise. The test set is small and the labels were written by the project's developers. The numbers are useful for comparing versions, not as a claim of real-world accuracy.

## Resource use (CPU)
| | LLM on | Light mode |
|---|---|---|
| Processing per song | about 15–20 s | about 8 s |
| Memory | about 5–6 GB (estimate, not measured) | about 3 GB processing, 1 GB idle (measured) |
| Docker download | about 10 GB | about 5 GB |

## Coverage
26 of the 30 sample songs got a confident lyrics match. The other 4 are searchable by title and genre only.

## Known limitations
- **The small LLM misreads some Hindi lyrics.** It tagged Balam Pichkari, a Holi party song, as "longing, heartbroken", so it can surface for breakup searches.
- **Lyrics don't capture sound.** Tempo and energy are inferred from text, which hurts queries like "gym".
- **No discovery.** Results only come from songs you already have.
- **Results always fill the list.** It returns `k` songs even when fewer fit.
- **No hard filters.** Requests like "not Arijit" or "only Punjabi" aren't treated as rules.
- **Keyword noise.** Generic LLM keywords such as "end" can boost unrelated songs.
- **Single user, no login.** Suitable for local use, not public hosting.
- **No automated tests.** `eval.py` measures quality, not correctness of the code.

## Verification status
| Area | Status |
|---|---|
| CSV pipeline and search | Tested end to end |
| Docker, LLM on and light mode | Tested on Docker Desktop |
| Spotify sync and playlist creation | Implemented, not verified end to end: Spotify returned a Premium-required error during testing |
| Switching from light mode to LLM on | Logic implemented, full switch not yet tested |

## Future work
1. Add an audio signal for tempo and energy.
2. Add a minimum score, so the app can say when nothing fits.
3. Extract artist and language rules from the request as hard filters.
4. Limit songs per artist and order playlists by energy.
5. Let users correct mood tags and rate results.
6. Recommend new songs using similar-track data.
7. Add automated tests and a larger evaluation on real libraries.
