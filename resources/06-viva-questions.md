# Viva Questions

## Concepts
**What is an embedding?**
A list of numbers representing the meaning of text. Similar meanings end up close together, so we can search by meaning.

**Why bge-m3?**
It's multilingual, which suits songs mixing Hindi, Punjabi and English.

**What is BM25?**
A keyword ranking formula. It rewards words that are frequent in a document and rare across the library, and it adjusts for document length.

**Why use both vector and keyword search?**
Vector search finds meaning but misses specifics. Keyword search finds exact words like "holi". Adding it raised plain search from 49% to 63%.

**What is reciprocal rank fusion?**
A way to merge ranked lists using positions instead of raw scores: `1 / (60 + rank)`, summed per song. It's useful because the two searches score on different scales.

**What is precision@5?**
The share of the top 5 results that are relevant.

**Is this RAG?**
Not exactly. It retrieves songs and ranks them, but the LLM doesn't generate an answer from retrieved results. The LLM structures songs and requests.

## Design
**Why not call the LLM for every search result?**
An early version re-ranked results with the LLM. It took 40–57 seconds and failed on 2 of 4 test queries. Running it once per song and scoring with a formula takes about 6 seconds, and the ranking step has no LLM to fail.

**Why fixed mood lists?**
Free text like "sorrowful" and "sad" wouldn't match directly. A shared vocabulary lets a formula compare requests with songs.

**Why not embed raw lyrics when a profile exists?**
They're long, often in Devanagari or Gurmukhi script, and drown out the mood signal. They're still searchable by keyword.

**Why SQLite as well as ChromaDB?**
ChromaDB stores vectors. SQLite stores each song's state, so a crashed job can resume and a single stage can be re-run.

**Why romanized lyrics for the LLM?**
In a small test, the 3B model did slightly better on them and ran about twice as fast. The sample was small, so this is a practical choice, not a proven result.

**Why a local LLM rather than a hosted API?**
It's free and private, and needs no API key. The cost is lower speed and accuracy.

**Why make the LLM optional?**
Some machines can't run it. Light mode uses much less memory, and it scored 60% against 66% for smart search, a gap small enough to be noise on this test.

## Challenges
**What was the hardest problem?**
Getting reliable mood labels from a small model reading Hindi lyrics. Structured output and romanized lyrics helped, but some songs are still wrong.

**What happened with Spotify?**
New developer apps are restricted: the app owner needs Premium, and some endpoints, such as recommendations and audio features, are limited. So we added the CSV path, and all later stages are shared by both sources.

**How do you avoid wrong lyrics?**
We score several Genius results on title and artist, skip remixes and translation pages, and accept only a score of 0.85 or above. Otherwise, the song is marked as having no lyrics.

## Critical questions
**How reliable is 66%?**
It's only a rough guide: 10 queries, 30 songs and labels we wrote ourselves. One query moves it by 10 points.

**Would it scale to 10,000 songs?**
Search probably would, but this hasn't been tested. Processing is the bottleneck: at about 15–20 seconds per song on CPU, 10,000 songs would take roughly 2 days. It would need a GPU, a hosted model or light mode.

**What would you do next?**
Add an audio signal, because lyrics can't describe how a song sounds, and an option to say when nothing fits.
