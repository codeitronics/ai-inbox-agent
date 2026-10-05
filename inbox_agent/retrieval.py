"""Finds past replies similar to a new email (BM25 over approved replies).

A personal inbox holds hundreds to a few thousand approved replies, where keyword ranking is fast,
dependency-free and easy to inspect. Swap in an embedding index here if you need semantic matching.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

STOP = set(
    "a about after again all also am an and any are as at be been before being but by can could did do does done "
    "for from get got had has have he hello her here hi him his how i if in into is it its just know let like me "
    "more my need no not now of off on one or our out over please re she should so some than that the their them "
    "then there these they this those to too up us very was we were what when where which while who why will with "
    "would you your thanks thank regards best cheers dear".split()
)


def stem(t: str) -> str:
    """Tiny suffix stripper so "hours"/"hour" and "pallets"/"pallet" match."""
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokens(text: str, ignore: set[str] = frozenset()) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and t not in ignore and len(t) > 1 and not t.isdigit()]


def similar(
    query: str, memory: list[dict[str, Any]], k: int = 3, category: str | None = None, ignore: set[str] = frozenset(), k1: float = 1.4, b: float = 0.75
) -> list[dict[str, Any]]:
    """Top-k memory rows by BM25 score against `query`, each with a `score`.

    `ignore` drops words that match without meaning anything (people's names); replies to emails of the same
    `category` get a boost, since an urgent incident is answered differently from an invoice query.
    """
    if not memory:
        return []
    # Index both sides of each exchange: the reply often carries the words that say what the case was about.
    docs = [tokens(f"{m.get('subject') or ''} {m['incoming']} {m['reply']}", ignore) for m in memory]
    avg = sum(map(len, docs)) / len(docs) or 1
    df = Counter(t for d in docs for t in set(d))
    n = len(docs)
    q = set(tokens(query, ignore))
    scored = []
    for m, d in zip(memory, docs):
        tf = Counter(d)
        s = 0.0
        for t in q & tf.keys():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        if s > 0 and category and m.get("category") == category:
            s = s * 1.5 + 1
        if s > 0:
            scored.append({**m, "score": round(s, 2)})
    ranked = sorted(scored, key=lambda r: r["score"], reverse=True)[:k]
    # Keep only matches in the same league as the best one; weak tail matches add noise to the prompt.
    return [r for r in ranked if r["score"] >= 0.4 * ranked[0]["score"]] if ranked else []
