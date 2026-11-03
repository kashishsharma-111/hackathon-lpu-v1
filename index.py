"""
Vector index of mood documents (ChromaDB + BAAI/bge-m3).

Each song is indexed as one compact "mood document" built from its metadata and mood profile. Raw lyrics are not
embedded: they are long, often in Devanagari or Gurmukhi script and drown out the mood signal. Songs without
lyrics have no profile, so their document only holds title, artists, album and genres.
"""
import hashlib
import logging
import os

from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceBgeEmbeddings

logger = logging.getLogger(__name__)

PERSIST_DIRECTORY = os.path.join(os.getenv("DATA_DIR", "."), "chroma_langchain_db")
MODEL_NAME = "BAAI/bge-m3"

embedding_function = HuggingFaceBgeEmbeddings(model_name=MODEL_NAME)
_stores = {}


def _collection_name(library: str):
    return f"moodify_{library}"


def get_store(library: str):
    name = _collection_name(library)
    if name not in _stores:
        _stores[name] = Chroma(collection_name=name, embedding_function=embedding_function,
                               persist_directory=PERSIST_DIRECTORY)
    return _stores[name]


def reset(library: str):
    get_store(library).delete_collection()
    _stores.pop(_collection_name(library), None)


def count(library: str):
    return get_store(library)._collection.count()


def mood_document(song: dict):
    """
    The text that represents a song in the vector index.
    """
    lines = [f"{song['name']} by {', '.join(song['artists'])}"]
    if song["album"]:
        lines.append(f"Album: {song['album']}")
    if song["genres"]:
        lines.append(f"Genres: {song['genres']}")
    profile = song.get("profile")
    if profile:
        if profile.get("summary"):
            lines.append(profile["summary"])
        if profile.get("moods"):
            lines.append(f"Moods: {', '.join(profile['moods'])}")
        if profile.get("occasions"):
            lines.append(f"Good for: {', '.join(profile['occasions'])}")
        if profile.get("themes"):
            lines.append(f"Themes: {', '.join(profile['themes'])}")
        if profile.get("energy"):
            lines.append(f"Energy: {profile['energy']}")
        if profile.get("language"):
            lines.append(f"Language: {profile['language']}")
    return "\n".join(lines)


def document_hash(document: str):
    return hashlib.sha1(document.encode()).hexdigest()


def upsert(library: str, songs):
    """
    Embeds the songs' mood documents and writes them to the index. Returns {track_id: document hash}.
    """
    if not songs:
        return {}
    documents = [mood_document(s) for s in songs]
    ids = [s["track_id"] for s in songs]
    get_store(library).add_texts(texts=documents, metadatas=[{"track_id": i} for i in ids], ids=ids)
    return {i: document_hash(d) for i, d in zip(ids, documents)}


def search(library: str, text: str, k: int):
    """
    Returns [(track_id, similarity)] for the k nearest songs. Similarity is cosine similarity (embeddings are
    normalized, and Chroma's default distance is squared L2, so cos = 1 - d / 2).
    """
    store = get_store(library)
    total = store._collection.count()
    if total == 0:
        return []
    results = store.similarity_search_with_score(text, k=min(k, total))
    return [(doc.metadata["track_id"], 1 - distance / 2) for doc, distance in results]
