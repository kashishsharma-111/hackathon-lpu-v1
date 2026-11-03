"""
Optional LLM layer for Moodify, backed by a local Ollama model.

The LLM is used in two places, both producing the same structured mood fields:
- profile_song: once per song while processing the library, from the song's whole lyrics.
- parse_query: once per search, turning a mood request into the fields to match against.

The whole layer can be switched off per machine with LLM_ENABLED=false, for machines that cannot run a local model.
Moods and occasions come from fixed vocabularies (enforced through a JSON schema), so a query and a song can be
compared directly without another LLM call. Every function returns None when Ollama is not running, the model is
not pulled, a call times out or the output is malformed; callers then fall back to plain vector search.
"""
import json
import logging
import os
import threading
import time

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen2.5:3b")
LLM_ENABLED = os.getenv("LLM_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))
STATUS_TTL = 30  # seconds to cache the availability check

# Bump when the profile prompt or schema changes, so songs get re-profiled
PROFILE_VERSION = 2
# Context window for profile calls: whole lyrics (even in Devanagari) plus the prompt fit comfortably
PROFILE_CONTEXT = 8192
MAX_PROFILE_LYRICS_CHARS = 9000

MOODS = ["happy", "sad", "romantic", "longing", "heartbroken", "angry", "energetic", "calm", "nostalgic",
         "hopeful", "melancholic", "playful", "confident", "peaceful", "devotional", "patriotic", "motivated",
         "lonely", "celebratory", "sensual", "dreamy", "intense"]
OCCASIONS = ["workout", "party", "dance", "road trip", "driving", "rain", "late night", "study", "sleep",
             "date night", "breakup", "celebration", "wedding", "festival", "prayer", "independence day",
             "relaxing", "morning", "travel", "friends", "sports"]
ENERGIES = ["low", "medium", "high"]
# Valence: 1 = very sad/dark, 3 = neutral/bittersweet, 5 = very happy/uplifting

_status_lock = threading.Lock()
_status_cache = {"at": 0.0, "value": None}
_query_cache = {}


def status(force: bool = False):
    """
    Returns {"enabled": bool, "available": bool, "model": str, "reason": str | None}. Cached for STATUS_TTL seconds.
    "enabled" is the machine's LLM_ENABLED setting; "available" also needs Ollama and the model to be there.
    """
    if not LLM_ENABLED:
        return {"enabled": False, "available": False, "model": LLM_MODEL, "reason": "LLM_ENABLED=false in .env."}
    with _status_lock:
        if not force and _status_cache["value"] and time.time() - _status_cache["at"] < STATUS_TTL:
            return _status_cache["value"]
        try:
            response = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
            response.raise_for_status()
            names = {m.get("name") for m in response.json().get("models", [])}
            if LLM_MODEL in names or f"{LLM_MODEL}:latest" in names:
                value = {"enabled": True, "available": True, "model": LLM_MODEL, "reason": None}
            else:
                value = {"enabled": True, "available": False, "model": LLM_MODEL,
                         "reason": f"Model {LLM_MODEL} is not pulled. Run: ollama pull {LLM_MODEL}"}
        except (requests.RequestException, ValueError):
            value = {"enabled": True, "available": False, "model": LLM_MODEL,
                     "reason": f"Ollama is not reachable at {OLLAMA_URL}. Start it with: ollama serve"}
        _status_cache.update(at=time.time(), value=value)
        return value


def _mark_unavailable():
    with _status_lock:
        _status_cache["at"] = 0.0


def _chat_json(system: str, user: str, schema: dict, max_tokens: int, context: int = 4096):
    """
    Sends one chat request to Ollama, constrained to `schema`, and returns the parsed JSON object or None.
    Malformed output is retried once; connection problems and timeouts are not.
    """
    payload = {
        "model": LLM_MODEL,
        "stream": False,
        "format": schema,
        "think": False,  # thinking models (qwen3) would otherwise spend their token budget before answering
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": context},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    for attempt in range(2):
        try:
            response = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=LLM_TIMEOUT)
            response.raise_for_status()
            content = response.json()["message"]["content"]
            data = json.loads(content)
            if isinstance(data, dict):
                return data
            logger.warning(f"LLM returned non-object JSON: {content[:200]}")
        except requests.RequestException as e:
            logger.warning(f"LLM request failed: {e}")
            _mark_unavailable()
            return None
        except (ValueError, KeyError) as e:
            logger.warning(f"LLM returned malformed output (attempt {attempt + 1}): {e}")
    return None


def _pick(values, vocabulary, limit):
    """Keeps only known vocabulary terms, in order, without duplicates."""
    if not isinstance(values, list):
        return []
    known = [str(v).strip().lower() for v in values if str(v).strip().lower() in vocabulary]
    return list(dict.fromkeys(known))[:limit]


def _free_list(values, limit, max_len=40):
    if not isinstance(values, list):
        return []
    items = [str(v).strip()[:max_len] for v in values if str(v).strip()]
    return list(dict.fromkeys(items))[:limit]


def _valence(value, allow_any=False):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if allow_any and v == 0:
        return None
    return v if 1 <= v <= 5 else None


PROFILE_SYSTEM = f"""You analyse songs for a mood-based music search.
You get a song's title, artists, album, genres and its complete lyrics. Lyrics may be in Hindi (Devanagari or
romanized), Punjabi, Urdu or English. Read the whole lyrics and describe how the song feels. Answer in English.

Fields:
- summary: one or two sentences on what the song is about and how it feels.
- moods: 1 to 4 moods, only from: {", ".join(MOODS)}.
- occasions: 0 to 3 situations the song clearly suits, only from: {", ".join(OCCASIONS)}.
  Leave it empty rather than guessing; slow songs do not suit workout or party, and only lullabies suit sleep.
- themes: 1 to 4 short free-text themes (for example "unrequited love", "self-belief").
- energy: low, medium or high.
- valence: 1 (very sad or dark) to 5 (very happy or uplifting); 3 is neutral or bittersweet.
- language: the main language of the lyrics.
Base everything on the lyrics. Do not invent facts like the film plot or release year."""

PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "moods": {"type": "array", "items": {"type": "string", "enum": MOODS}, "minItems": 1, "maxItems": 4},
        "occasions": {"type": "array", "items": {"type": "string", "enum": OCCASIONS}, "maxItems": 3},
        "themes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
        "energy": {"type": "string", "enum": ENERGIES},
        "valence": {"type": "integer", "minimum": 1, "maximum": 5},
        "language": {"type": "string"},
    },
    "required": ["summary", "moods", "occasions", "themes", "energy", "valence", "language"],
}


def profile_song(name: str, artists, album: str, genres: str, lyrics: str):
    """
    Returns a validated mood profile dict for the song, or None.
    """
    if not lyrics or not status()["available"]:
        return None
    user = (f"Title: {name}\nArtists: {', '.join(artists)}\nAlbum: {album or 'unknown'}\n"
            f"Genres: {genres or 'unknown'}\n\nLyrics:\n{lyrics[:MAX_PROFILE_LYRICS_CHARS]}")
    data = _chat_json(PROFILE_SYSTEM, user, PROFILE_SCHEMA, 300, context=PROFILE_CONTEXT)
    if not data:
        return None
    profile = {
        "summary": str(data.get("summary", "")).strip()[:500],
        "moods": _pick(data.get("moods"), MOODS, 4),
        "occasions": _pick(data.get("occasions"), OCCASIONS, 4),
        "themes": _free_list(data.get("themes"), 4),
        "energy": data.get("energy") if data.get("energy") in ENERGIES else None,
        "valence": _valence(data.get("valence")),
        "language": str(data.get("language", "")).strip()[:30],
    }
    if not profile["summary"] and not profile["moods"]:
        return None
    return profile


QUERY_SYSTEM = f"""You turn a mood request into search fields for a music library. The request may be in English,
Hindi or Hinglish (for example "dil toota hai" means heartbroken).

Fields:
- description: one sentence describing the songs that would fit, in English (emotions, themes, energy, setting).
  Do not name specific songs or artists.
- moods: 0 to 3 moods the songs should have, only from: {", ".join(MOODS)}.
- occasions: 0 to 2 situations from the request, only from: {", ".join(OCCASIONS)}.
- energy: low, medium, high, or any if the request does not imply one.
- valence: 1 (very sad or dark) to 5 (very happy or uplifting), or 0 if the request does not imply one.
- keywords: 0 to 6 specific words that could literally appear in a fitting song's title, genres or lyrics, in
  English and romanized Hindi (for example "holi", "rain", "baarish", "maa"). Skip generic words like "song" or "love".
Only fill moods, occasions, energy and valence when the request clearly implies them.
The activity sets the energy: workout, dance, party and sports need high energy even when the feeling is sad
(for example "gym after a breakup" wants angry, motivated, high-energy songs, not slow sad ones)."""

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "moods": {"type": "array", "items": {"type": "string", "enum": MOODS}, "maxItems": 3},
        "occasions": {"type": "array", "items": {"type": "string", "enum": OCCASIONS}, "maxItems": 2},
        "energy": {"type": "string", "enum": ENERGIES + ["any"]},
        "valence": {"type": "integer", "minimum": 0, "maximum": 5},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
    },
    "required": ["description", "moods", "occasions", "energy", "valence", "keywords"],
}


def parse_query(query: str):
    """
    Returns {"description", "moods", "occasions", "energy", "valence", "keywords"} for the mood request, or None.
    energy and valence are None when the request does not imply them.
    """
    key = query.strip().lower()
    if key in _query_cache:
        return _query_cache[key]
    if not status()["available"]:
        return None
    data = _chat_json(QUERY_SYSTEM, f"Mood request: {query.strip()}", QUERY_SCHEMA, 200)
    if not data:
        return None
    parsed = {
        "description": str(data.get("description", "")).strip()[:300],
        "moods": _pick(data.get("moods"), MOODS, 3),
        "occasions": _pick(data.get("occasions"), OCCASIONS, 2),
        "energy": data.get("energy") if data.get("energy") in ENERGIES else None,
        "valence": _valence(data.get("valence"), allow_any=True),
        "keywords": _free_list(data.get("keywords"), 6),
    }
    if not parsed["description"] and not parsed["moods"] and not parsed["occasions"]:
        return None
    if len(_query_cache) > 500:
        _query_cache.clear()
    _query_cache[key] = parsed
    return parsed
