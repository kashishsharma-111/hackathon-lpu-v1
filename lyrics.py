"""
Accurate lyrics lookup on Genius.

Instead of trusting Genius' first search hit, several hits are scored on title and artist similarity, and lyrics
are only accepted from a confident match. Translations, romanizations and alternate versions (unplugged, remix,
lofi...) are avoided unless the requested title asks for them.
"""
import difflib
import logging
import os
import re

import lyricsgenius
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MAX_LYRICS_CHARS = 15000
# Genius pages that are not the original song
NON_ORIGINAL_ARTISTS = ("genius romanizations", "genius english translations", "genius traducciones",
                        "genius translations", "spotify")
VERSION_WORDS = ("unplugged", "remix", "lofi", "lo fi", "reprise", "acoustic", "cover", "slowed", "reverb",
                 "mashup", "live", "version", "instrumental", "karaoke", "romanized", "translation")

_token = os.getenv("GENIUS_API_TOKEN")
genius = lyricsgenius.Genius(_token, timeout=10, retries=1, remove_section_headers=True) if _token else None


def configured():
    return genius is not None


def normalize(text: str):
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (text or "").lower())).strip()


def clean_title(title: str):
    """
    Removes decorations that Spotify adds but Genius titles usually don't have:
    "(From "Film")", "- From Film", "(feat. X)", "- Remastered 2011" and similar.
    """
    t = re.sub(r"\s*[\(\[][^\)\]]*(from|feat\.?|ft\.|with|remaster|version|edit|soundtrack)[^\)\]]*[\)\]]", "",
               title, flags=re.I)
    t = re.sub(r"\s+-\s+.*(from|remaster|version|edit|soundtrack|live).*$", "", t, flags=re.I)
    return t.strip() or title.strip()


def _title_similarity(wanted: str, found: str):
    a, b = normalize(clean_title(wanted)), normalize(clean_title(found))
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _artist_match(wanted_artists, found_artists: str):
    found = normalize(found_artists)
    for artist in wanted_artists:
        a = normalize(artist)
        if a and (a in found or difflib.SequenceMatcher(None, a, found).ratio() >= 0.85):
            return True
    return False


def _is_version(title: str):
    t = normalize(title)
    return any(word in t.split() or f" {word} " in f" {t} " for word in VERSION_WORDS)


def score_hit(name: str, artists, hit: dict):
    """
    Returns a 0..1 confidence that a Genius search hit is the requested song.
    """
    title = hit.get("title", "")
    artist_names = hit.get("artist_names") or hit.get("primary_artist", {}).get("name", "")
    primary = normalize(hit.get("primary_artist", {}).get("name", ""))
    if any(primary.startswith(x) for x in NON_ORIGINAL_ARTISTS):
        return 0.0
    if hit.get("lyrics_state") not in (None, "complete"):
        return 0.0
    title_sim = _title_similarity(name, title)
    if _is_version(title) and not _is_version(name):
        title_sim -= 0.25
    # A page where one of our artists is the primary artist beats one where they are only featured (often a remix)
    if _artist_match(artists, primary):
        artist_score = 0.3
    elif _artist_match(artists, artist_names):
        artist_score = 0.2
    else:
        artist_score = 0.0
    return max(0.0, 0.7 * title_sim + artist_score)


ACCEPT_SCORE = 0.85  # needs a near-exact title AND an artist match


def _clean_lyrics(text: str):
    """Drops repeated lines (choruses) and Genius page leftovers."""
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and line not in lines and not re.fullmatch(r"\d*Embed|You might also like", line):
            lines.append(line)
    return "\n".join(lines)[:MAX_LYRICS_CHARS]


def _find_alternate(name: str, artists, tag: str, owner: str):
    """
    Finds a Genius page like "Arijit Singh - Channa Mereya (Romanized)", published by a Genius translation account.
    """
    wanted = clean_title(name)
    for query in (f"{artists[0] if artists else ''} {wanted} {tag}", f"{wanted} {tag}"):
        for hit in genius.search_songs(query, per_page=10).get("hits", []):
            result = hit.get("result", {})
            if not normalize(result.get("primary_artist", {}).get("name", "")).startswith(owner):
                continue
            title = re.sub(r"\s*\((English Translation|Romanized)\)\s*$", "", result.get("title", ""), flags=re.I)
            parts = title.split(" - ", 1)
            if len(parts) == 2 and _title_similarity(name, parts[1]) >= 0.85 and _artist_match(artists, parts[0]):
                return _clean_lyrics(genius.lyrics(song_id=result["id"], remove_section_headers=True))
    return ""


def find_alternates(name: str, artists):
    """
    Looks for English and romanized versions of the lyrics, which many Indian songs have on Genius.
    Returns (status, english, romanized) with status "done" or "error"; either text may be empty.
    """
    if genius is None:
        return "error", "", ""
    try:
        english = _find_alternate(name, artists, "English Translation", "genius english translations")
        romanized = _find_alternate(name, artists, "Romanized", "genius romanizations")
    except Exception as e:
        logger.warning(f"Genius lookup of alternate lyrics failed for '{name}': {e}")
        return "error", "", ""
    return "done", english, romanized


def find_lyrics(name: str, artists):
    """
    Returns (status, lyrics, match) where status is "found", "not_found" or "error" and match describes the
    accepted Genius song.
    """
    if genius is None:
        return "error", "", None
    best, best_score = None, 0.0
    try:
        for query in (f"{clean_title(name)} {artists[0] if artists else ''}", clean_title(name)):
            for hit in genius.search_songs(query, per_page=10).get("hits", []):
                result = hit.get("result", {})
                score = score_hit(name, artists, result)
                if score > best_score:
                    best, best_score = result, score
            if best_score >= ACCEPT_SCORE:
                break
        if best is None or best_score < ACCEPT_SCORE:
            if best:
                logger.info(f"No confident Genius match for '{name}': best was '{best.get('title')}' by "
                            f"{best.get('artist_names')} (score {best_score:.2f})")
            return "not_found", "", None
        text = genius.lyrics(song_id=best["id"], remove_section_headers=True) or ""
    except Exception as e:
        logger.warning(f"Genius lookup failed for '{name}': {e}")
        return "error", "", None

    lyrics = _clean_lyrics(text)
    if len(lyrics) < 40:
        return "not_found", "", None
    match = {"title": best.get("title", ""), "artist": best.get("artist_names", ""), "url": best.get("url", ""),
             "score": round(best_score, 2)}
    return "found", lyrics, match
