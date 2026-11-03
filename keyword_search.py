"""
Keyword search (BM25) over a library, complementing the vector search.

The vector index only holds mood documents, so it cannot find a specific word. This covers title, artists, album,
genres, the mood profile and the lyrics (original, romanized and English), which lets "holi", "arijit" or "baarish"
match directly. It needs no LLM. The index is built in memory and rebuilt when the library changes.
"""
import math
import re
from collections import Counter

# Devanagari and Gurmukhi vowel signs are not word characters for \w, so their blocks are listed explicitly
TOKEN = re.compile(r"[\wऀ-ॿ਀-੿]+")
STOPWORDS = {
    "a", "an", "and", "are", "about", "for", "from", "i", "in", "is", "it", "me", "my", "of", "on", "or", "some",
    "song", "songs", "music", "track", "tracks", "that", "the", "to", "with", "want", "need", "play", "give",
    "hai", "hain", "ho", "ka", "ke", "ki", "ko", "mein", "se", "aur", "wala", "wali", "wale", "gaane", "gana", "gaana",
}
# How much a word counts per field: words in the title or genres say more about a song than one line of lyrics
FIELD_WEIGHTS = {"name": 3, "artists": 2, "album": 1, "genres": 3, "profile": 2, "lyrics": 1}
K1 = 1.2
B = 0.75

_cache = {}


def tokenize(text: str):
    return [t for t in TOKEN.findall((text or "").lower()) if t not in STOPWORDS]


def _song_fields(song: dict):
    profile = song.get("profile") or {}
    profile_text = " ".join([profile.get("summary", ""), *profile.get("moods", []), *profile.get("occasions", []),
                             *profile.get("themes", [])])
    return {
        "name": song["name"],
        "artists": " ".join(song["artists"]),
        "album": song["album"],
        "genres": song["genres"],
        "profile": profile_text,
        "lyrics": " ".join([song["lyrics"], song.get("lyrics_romanized", ""), song.get("lyrics_english", "")]),
    }


def _build(songs):
    documents = {}
    for song in songs:
        terms = Counter()
        for field, text in _song_fields(song).items():
            for token in tokenize(text):
                terms[token] += FIELD_WEIGHTS[field]
        documents[song["track_id"]] = terms
    frequency = Counter(term for terms in documents.values() for term in terms)
    average_length = sum(sum(t.values()) for t in documents.values()) / max(len(documents), 1)
    return {"documents": documents, "frequency": frequency, "average_length": average_length or 1.0}


def search(library: str, stamp, songs_loader, weighted_terms: dict, limit: int):
    """
    weighted_terms: {term: weight}. Returns [(track_id, score, matched terms)], best first.
    songs_loader is only called when the library changed since the last search (stamp differs).
    """
    cached = _cache.get(library)
    if not cached or cached[0] != stamp:
        cached = (stamp, _build(songs_loader()))
        _cache[library] = cached
    index = cached[1]

    total = len(index["documents"])
    results = []
    for track_id, terms in index["documents"].items():
        length = sum(terms.values())
        score, matched = 0.0, []
        for term, weight in weighted_terms.items():
            count = terms.get(term, 0)
            if not count:
                continue
            df = index["frequency"][term]
            idf = math.log(1 + (total - df + 0.5) / (df + 0.5))
            score += weight * idf * count * (K1 + 1) / (count + K1 * (1 - B + B * length / index["average_length"]))
            matched.append(term)
        if score > 0:
            results.append((track_id, score, matched))
    results.sort(key=lambda item: item[1], reverse=True)
    return results[:limit]
