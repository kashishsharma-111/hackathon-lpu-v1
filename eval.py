"""
Small evaluation of mood search on the sample library (sample_hindi_songs_30.csv).

Runs labelled mood queries against the running backend and reports precision@k (share of the top k results that
are in the expected set) for plain vector search and for smart search. Use it to compare changes to prompts,
weights or the mood document.

    venv/bin/python eval.py            # both modes
    venv/bin/python eval.py --plain    # plain vector search only (fast)
"""
import os
import sys
import time

import requests

BASE_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8235")
K = 5

# Mood query -> songs from sample_hindi_songs_30.csv that clearly fit
CASES = {
    "workout motivation": {"Zinda", "Apna Time Aayega", "Chak De India", "Roobaroo", "Kandhon Se Milte Hain Kandhe"},
    "gym after a breakup": {"Zinda", "Apna Time Aayega", "Chak De India", "Roobaroo", "Bekhayali"},
    "heartbreak": {"Channa Mereya", "Agar Tum Saath Ho", "Bekhayali", "Lut Gaye", "Phir Le Aya Dil", "Tera Ghata"},
    "dil toota hai": {"Channa Mereya", "Agar Tum Saath Ho", "Bekhayali", "Lut Gaye", "Phir Le Aya Dil", "Tera Ghata"},
    "party dance floor": {"Badtameez Dil", "Kala Chashma", "Lungi Dance", "Balam Pichkari", "Ghungroo",
                          "Tauba Tauba", "Ye Jawaani Hai Deewani"},
    "romantic date night": {"Tum Hi Ho", "Tum Se Hi", "Raabta", "Gerua", "Kesariya", "Tujhe Kitna Chahne Lage"},
    "road trip with friends": {"Ilahi", "Dil Chahta Hai", "Ye Jawaani Hai Deewani", "Badtameez Dil", "Roobaroo"},
    "independence day": {"Ae Watan", "Kandhon Se Milte Hain Kandhe", "Vande Mataram", "Chak De India"},
    "spiritual and peaceful": {"Kun Faya Kun", "Khwaja Mere Khwaja"},
    "holi festival": {"Balam Pichkari"},
}


def run(smart: bool):
    scores, total_time = [], 0.0
    print(f"\n=== {'smart' if smart else 'plain'} search, precision@{K} ===")
    for query, expected in CASES.items():
        started = time.time()
        response = requests.get(f"{BASE_URL}/search", params={"query": query, "k": K, "smart": smart}, timeout=300)
        response.raise_for_status()
        elapsed = time.time() - started
        total_time += elapsed
        names = [t["name"] for t in response.json()["tracks"]]
        # Queries with fewer than K relevant songs are scored against what is achievable
        precision = sum(n in expected for n in names) / min(K, len(expected))
        scores.append(precision)
        marked = ", ".join(f"{n}{'' if n in expected else ' ✗'}" for n in names)
        print(f"{precision:4.0%}  {elapsed:4.1f}s  {query:24} {marked}")
    print(f"mean {sum(scores) / len(scores):.0%}, {total_time / len(CASES):.1f}s per query")
    return sum(scores) / len(scores)


if __name__ == "__main__":
    run(smart=False)
    if "--plain" not in sys.argv:
        run(smart=True)
