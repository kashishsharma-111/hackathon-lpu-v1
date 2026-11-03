"""
Mood search: vector retrieval plus a deterministic re-rank on structured mood fields.

    query -> LLM parses it into moods, occasions, energy, valence and a description (one call, cached)
          -> vector search with the query and the parsed fields, plus keyword search, merged
          -> each candidate is scored by vector similarity, by how well its mood profile matches the parsed
             fields and by keyword matches; reasons come from what matched

Without the LLM (or with smart=False) it merges vector and keyword search on the query as typed.
"""
import catalog
import index
import keyword_search
import llm

# Weights of the structured match. The structured score and the vector score count half each.
FIELD_WEIGHTS = {"moods": 0.35, "occasions": 0.30, "energy": 0.15, "valence": 0.20}
STRUCTURED_SHARE = 0.5
ENERGY_LEVELS = {"low": 0, "medium": 1, "high": 2}
# Added to the score of songs that literally contain the search words, scaled by how strong the match is
KEYWORD_BONUS = 0.35
LLM_KEYWORD_WEIGHT = 0.5  # the LLM's suggested words count less than the words the user typed
RRF_K = 60


def _structured_match(parsed, profile):
    """
    Returns (score 0..1, matched labels) for how well a song's profile fits the parsed query, or (None, [])
    when the query has no structured fields or the song has no profile.
    """
    if not parsed or not profile:
        return None, []
    total = weight = 0.0
    matched = []

    for field in ("moods", "occasions"):
        wanted = parsed.get(field) or []
        if wanted:
            hits = [w for w in wanted if w in (profile.get(field) or [])]
            total += FIELD_WEIGHTS[field] * len(hits) / len(wanted)
            weight += FIELD_WEIGHTS[field]
            matched += hits

    if parsed.get("energy") and profile.get("energy"):
        gap = abs(ENERGY_LEVELS[parsed["energy"]] - ENERGY_LEVELS[profile["energy"]])
        total += FIELD_WEIGHTS["energy"] * (1.0 if gap == 0 else 0.4 if gap == 1 else 0.0)
        weight += FIELD_WEIGHTS["energy"]
        if gap == 0:
            matched.append(f"{profile['energy']} energy")

    if parsed.get("valence") and profile.get("valence"):
        gap = abs(parsed["valence"] - profile["valence"])
        total += FIELD_WEIGHTS["valence"] * (1 - gap / 4)
        weight += FIELD_WEIGHTS["valence"]
        if gap == 0:
            matched.append({1: "very dark", 2: "sad", 3: "bittersweet", 4: "upbeat", 5: "very upbeat"}[profile["valence"]])

    if weight == 0:
        return None, []
    return total / weight, matched


def _track(song: dict, reason: str = "", score: float = None):
    profile = song.get("profile") or {}
    return {
        "id": song["track_id"],
        "name": song["name"],
        "artists": [{"name": a} for a in song["artists"]],
        "album": song["album"],
        "genres": song["genres"],
        "url": song["url"],
        "spotify_id": song["spotify_id"],
        "summary": profile.get("summary", ""),
        "moods": profile.get("moods", []),
        "occasions": profile.get("occasions", []),
        "has_lyrics": song["lyrics_status"] == "found",
        "reason": reason,
        "score": round(score, 3) if score is not None else None,
    }


def _keyword_hits(library: str, query: str, parsed, limit: int):
    """
    Runs the keyword search with the words of the query and, at half weight, the LLM's suggested keywords.
    Returns {track_id: (score scaled to 0..1, matched words)}.
    """
    terms = {}
    for keyword in (parsed or {}).get("keywords", []):
        for token in keyword_search.tokenize(keyword):
            terms[token] = LLM_KEYWORD_WEIGHT
    for token in keyword_search.tokenize(query):
        terms[token] = 1.0
    if not terms:
        return {}
    hits = keyword_search.search(library, catalog.library_stamp(library), lambda: catalog.get_songs(library), terms, limit)
    if not hits:
        return {}
    top = hits[0][1]
    return {track_id: (score / top, matched) for track_id, score, matched in hits}


def _mentions(words):
    return f"mentions {', '.join(words[:3])}" if words else ""


def _plain_search(library: str, query: str, k: int):
    """
    Without the LLM: vector search and keyword search on the query as typed, merged by reciprocal rank fusion.
    """
    pool = max(4 * k, 20)
    vector_hits = index.search(library, query, pool)
    keyword_hits = _keyword_hits(library, query, None, pool)
    fused, similarity = {}, dict(vector_hits)
    for rank, (track_id, _) in enumerate(vector_hits):
        fused[track_id] = fused.get(track_id, 0.0) + 1.0 / (RRF_K + rank)
    for rank, track_id in enumerate(keyword_hits):
        fused[track_id] = fused.get(track_id, 0.0) + 1.0 / (RRF_K + rank)
    best = sorted(fused, key=fused.get, reverse=True)[:k]
    songs = {s["track_id"]: s for s in catalog.get_songs(library, best)}
    return [_track(songs[t], _mentions(keyword_hits.get(t, (0, []))[1]), similarity.get(t))
            for t in best if t in songs]


def find_songs(library: str, query: str, k: int, smart: bool = True):
    """
    Returns {"tracks": [...], "interpretation": dict | None, "llm_used": bool, "notes": [str]}.
    """
    notes = []
    if index.count(library) == 0:
        return {"tracks": [], "interpretation": None, "llm_used": False,
                "notes": ["No songs are searchable yet. Add songs and process them first."]}

    parsed = None
    if smart:
        llm_status = llm.status()
        if not llm_status["enabled"]:
            pass
        elif not llm_status["available"]:
            notes.append(f"Smart search is unavailable ({llm_status['reason']}). Showing plain search results.")
        else:
            parsed = llm.parse_query(query)
            if not parsed:
                notes.append("The LLM could not interpret the mood, so the search used your words as typed.")

    if not parsed:
        return {"tracks": _plain_search(library, query, k), "interpretation": None, "llm_used": False, "notes": notes}

    # Search with the query as typed and with the parsed fields phrased like a mood document, keep the best similarity
    expanded = [parsed["description"]]
    if parsed["moods"]:
        expanded.append(f"Moods: {', '.join(parsed['moods'])}")
    if parsed["occasions"]:
        expanded.append(f"Good for: {', '.join(parsed['occasions'])}")
    if parsed["energy"]:
        expanded.append(f"Energy: {parsed['energy']}")
    pool = max(4 * k, 20)
    similarity = {}
    for text in (query, "\n".join(expanded)):
        for track_id, sim in index.search(library, text, pool):
            similarity[track_id] = max(sim, similarity.get(track_id, -1.0))
    keyword_hits = _keyword_hits(library, query, parsed, pool)

    candidates = set(similarity) | set(keyword_hits)
    songs = {s["track_id"]: s for s in catalog.get_songs(library, candidates)}
    low, high = min(similarity.values()), max(similarity.values())
    scored = []
    for track_id in candidates:
        song = songs.get(track_id)
        if not song:
            continue
        # A song found only by keyword has no similarity from this search: treat it as the weakest vector match
        vector_score = (similarity.get(track_id, low) - low) / (high - low) if high > low else 1.0
        structured, matched = _structured_match(parsed, song.get("profile"))
        keyword_score, words = keyword_hits.get(track_id, (0.0, []))
        if structured is None:
            score = vector_score * (1 - STRUCTURED_SHARE) + 0.25 * STRUCTURED_SHARE  # unknown mood: below average
            reasons = ["matched on title and genre (no lyrics found)"] if song["lyrics_status"] != "found" else []
        else:
            score = vector_score * (1 - STRUCTURED_SHARE) + structured * STRUCTURED_SHARE
            reasons = matched
        score += KEYWORD_BONUS * keyword_score
        if words:
            reasons = [*reasons, _mentions(words)]
        scored.append((score, song, " · ".join(reasons)))

    scored.sort(key=lambda item: item[0], reverse=True)
    tracks = [_track(song, reason, score) for score, song, reason in scored[:k]]
    return {"tracks": tracks, "interpretation": parsed, "llm_used": True, "notes": notes}
