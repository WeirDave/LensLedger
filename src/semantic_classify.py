"""Classify photos against a vocabulary using stored CLIP embeddings."""

from __future__ import annotations

import array
import math
from pathlib import Path

from photo_index import connect


VOCABULARY = [
    "portrait", "landscape", "beach", "mountain", "food", "architecture",
    "sunset", "sunrise", "night sky", "cityscape", "street photography",
    "nature", "garden", "flower", "animal", "pet", "dog", "cat", "bird",
    "wildlife", "sports", "concert", "wedding", "birthday", "holiday",
    "christmas", "halloween", "travel", "camping", "hiking",
    "underwater", "aerial", "macro", "black and white", "snow", "rain",
    "forest", "lake", "river", "ocean", "desert", "rural",
    "interior", "still life", "selfie", "group photo", "family",
    "car", "boat", "autumn", "spring",
]

DEFAULT_THRESHOLD = 0.22
DEFAULT_TOP_N = 5


def _decode(blob: bytes) -> tuple[float, ...]:
    values = array.array("f")
    values.frombytes(blob)
    return tuple(values)


def _normalize(values: tuple[float, ...]) -> tuple[float, ...]:
    length = math.sqrt(sum(v * v for v in values))
    return tuple(v / length for v in values) if length else ()


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if len(a) != len(b):
        return -1.0
    return sum(x * y for x, y in zip(a, b))


def _encode_vocabulary(encoder, vocabulary: list[str]) -> list[tuple[str, tuple[float, ...]]]:
    result = []
    for term in vocabulary:
        prompt = f"a photo of {term}"
        vec = _normalize(encoder.encode_text(prompt))
        if vec:
            result.append((term, vec))
    return result


_vocab_cache: dict[str, list[tuple[str, tuple[float, ...]]]] = {}


def classify_asset(
    embedding: bytes,
    encoder,
    vocabulary: list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
) -> list[tuple[str, float]]:
    """Classify a single photo embedding against the vocabulary.

    Returns up to top_n (term, score) pairs above the threshold, sorted
    by score descending.
    """
    vocab = vocabulary or VOCABULARY
    model_id = str(encoder.identity)
    if model_id not in _vocab_cache:
        _vocab_cache[model_id] = _encode_vocabulary(encoder, vocab)
    vocab_vectors = _vocab_cache[model_id]

    photo_vec = _normalize(_decode(embedding))
    if not photo_vec:
        return []

    scores = []
    for term, term_vec in vocab_vectors:
        score = _dot(photo_vec, term_vec)
        if score >= threshold:
            scores.append((term, score))

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_n]


def classify_library(
    db_path: Path,
    encoder=None,
    vocabulary: list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
    progress=None,
    should_cancel=None,
) -> dict[str, int]:
    """Classify all semantically-indexed photos and store results as tags.

    Tags are stored in asset_tags with source='semantic_auto'.
    Returns counts of photos classified and tags added.
    """
    from semantic_index import encoder_for, DEFAULT_MODEL
    encoder = encoder or encoder_for(DEFAULT_MODEL)

    with connect(db_path) as con:
        rows = con.execute(
            """SELECT se.asset_id, se.embedding_f32
               FROM semantic_embeddings se
               JOIN assets a ON a.id = se.asset_id
               WHERE a.in_review_bin = 0
               AND NOT EXISTS (
                   SELECT 1 FROM asset_tags at
                   WHERE at.asset_id = se.asset_id AND at.source = 'semantic_auto'
               )"""
        ).fetchall()

    counts = {"total": len(rows), "classified": 0, "tags_added": 0, "cancelled": False}
    if progress:
        progress(dict(counts))

    for row in rows:
        if should_cancel and should_cancel():
            counts["cancelled"] = True
            break

        matches = classify_asset(
            row["embedding_f32"], encoder, vocabulary, threshold, top_n,
        )
        if not matches:
            counts["classified"] += 1
            if progress:
                progress(dict(counts))
            continue

        with connect(db_path) as con:
            for term, score in matches:
                tag_id = con.execute(
                    "INSERT INTO tags(name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name=name RETURNING id",
                    (term,),
                ).fetchone()[0]
                con.execute(
                    """INSERT INTO asset_tags(asset_id, tag_id, source, confidence)
                       VALUES (?, ?, 'semantic_auto', ?)
                       ON CONFLICT(asset_id, tag_id, source) DO UPDATE SET confidence=excluded.confidence""",
                    (row["asset_id"], tag_id, round(score, 4)),
                )
                counts["tags_added"] += 1

        counts["classified"] += 1
        if progress:
            progress(dict(counts))

    return counts


def clear_classifications(db_path: Path) -> int:
    """Remove all semantic_auto tags. Returns count removed."""
    with connect(db_path) as con:
        cursor = con.execute("DELETE FROM asset_tags WHERE source='semantic_auto'")
        return cursor.rowcount


def get_asset_classifications(db_path: Path, asset_id: int) -> list[dict]:
    """Return the semantic_auto tags for a single asset."""
    with connect(db_path) as con:
        rows = con.execute(
            """SELECT t.name, at.confidence
               FROM asset_tags at JOIN tags t ON t.id = at.tag_id
               WHERE at.asset_id = ? AND at.source = 'semantic_auto'
               ORDER BY at.confidence DESC""",
            (asset_id,),
        ).fetchall()
    return [{"tag": r["name"], "confidence": r["confidence"]} for r in rows]
