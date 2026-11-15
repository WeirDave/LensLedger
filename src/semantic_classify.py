"""Classify photos against a vocabulary using stored CLIP embeddings."""

from __future__ import annotations

import array
import math
from pathlib import Path

try:
    import numpy as np
except ImportError:  # NumPy is optional; the pure-Python path below still works.
    np = None

from photo_index import connect


_BASE_TERMS = [
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

_PEOPLE = [
    "man", "woman", "child", "baby", "teenager", "crowd", "couple", "bride",
    "groom", "musician", "athlete", "dancer", "soldier", "farmer", "fisherman",
    "chef", "student", "graduation", "parade", "protest", "festival",
]

_MAMMALS = [
    "deer", "elk", "moose", "antelope", "horse", "pony", "donkey", "cow",
    "bull", "sheep", "lamb", "goat", "pig", "rabbit", "hare", "squirrel",
    "chipmunk", "raccoon", "fox", "wolf", "coyote", "bear", "polar bear",
    "panda", "lion", "tiger", "leopard", "cheetah", "elephant", "giraffe",
    "zebra", "rhinoceros", "hippopotamus", "monkey", "gorilla", "kangaroo",
    "koala", "camel", "llama", "bison", "buffalo", "otter", "beaver", "seal",
    "sea lion", "dolphin", "whale", "hedgehog", "skunk", "badger", "puppy",
    "kitten", "hamster", "guinea pig", "ferret",
]

_BIRDS_AND_OTHER_ANIMALS = [
    "duck", "goose", "swan", "chicken", "rooster", "turkey", "eagle", "hawk",
    "owl", "parrot", "hummingbird", "seagull", "pelican", "flamingo", "penguin",
    "heron", "peacock", "sparrow", "robin", "woodpecker",
    "butterfly", "bee", "dragonfly", "ladybug", "spider", "ant", "snail",
    "frog", "turtle", "lizard", "snake", "crocodile", "fish", "goldfish",
    "shark", "jellyfish", "crab", "starfish", "octopus", "coral reef",
]

_PLANTS = [
    "tree", "palm tree", "oak tree", "pine tree", "cherry blossom", "rose",
    "tulip", "sunflower", "daisy", "orchid", "lily", "lavender", "cactus",
    "mushroom", "moss", "fern", "grass", "meadow", "field", "wheat field",
    "vineyard", "orchard", "pumpkin", "fruit", "vegetables", "leaves",
    "fall foliage", "bamboo", "bonsai",
]

_LANDSCAPES = [
    "waterfall", "canyon", "cliff", "cave", "volcano", "glacier", "iceberg",
    "island", "bay", "coast", "cove", "lighthouse", "pier", "dock", "harbor",
    "sand dunes", "hill", "valley", "mountain peak", "snowy mountain",
    "pond", "stream", "swamp", "marsh", "rainbow", "clouds", "fog", "mist",
    "storm", "lightning", "starry sky", "milky way", "moon", "full moon",
    "aurora", "horizon", "blue sky", "dramatic sky", "frozen lake",
    "tropical beach", "rocky shore", "national park", "countryside",
]

_PLACES_AND_BUILDINGS = [
    "house", "cabin", "barn", "farmhouse", "castle", "palace", "cathedral",
    "church", "temple", "mosque", "pagoda", "monument", "statue", "fountain",
    "bridge", "tower", "skyscraper", "city street", "alley", "market",
    "town square", "train station", "airport", "road", "highway", "tunnel",
    "stairs", "doorway", "window", "fence", "gate", "wall", "ruins",
    "cemetery", "playground", "stadium", "museum", "library", "school",
    "hospital", "restaurant", "cafe", "shop", "hotel", "office",
    "kitchen", "living room", "bedroom", "bathroom", "garage", "backyard",
    "patio", "swimming pool", "park", "zoo", "farm", "campsite", "tent",
    "campfire", "fireworks", "city lights", "neon sign",
]

_VEHICLES = [
    "truck", "bus", "motorcycle", "bicycle", "scooter", "train", "tram",
    "subway", "airplane", "helicopter", "sailboat", "ship", "ferry", "kayak",
    "canoe", "tractor", "race car", "vintage car", "police car", "ambulance",
    "fire truck", "taxi", "RV", "trailer", "hot air balloon", "drone",
    "rocket", "spaceship",
]

_FOOD_AND_DRINK = [
    "pizza", "burger", "sandwich", "salad", "soup", "pasta", "sushi",
    "steak", "chicken dish", "seafood", "tacos", "rice", "noodles", "bread",
    "cake", "cupcake", "pie", "cookies", "ice cream", "chocolate", "candy",
    "donuts", "pancakes", "breakfast", "fries", "barbecue", "cheese",
    "apple", "banana", "oranges", "strawberries", "grapes", "watermelon",
    "coffee", "tea", "beer", "wine", "cocktail", "juice", "bottle",
]

_OBJECTS = [
    "book", "newspaper", "map", "painting", "drawing", "sculpture", "poster",
    "sign", "screenshot", "document", "receipt", "whiteboard", "computer",
    "laptop", "phone", "television", "camera", "keyboard", "headphones",
    "guitar", "piano", "violin", "drums", "microphone", "clock", "watch",
    "jewelry", "ring", "hat", "shoes", "bag", "backpack", "umbrella",
    "sunglasses", "clothing", "dress", "costume", "mask", "toy", "doll",
    "teddy bear", "balloon", "gift", "candle", "lamp", "chair", "table",
    "sofa", "bed", "mirror", "plant pot", "vase", "tools", "bicycle helmet",
    "fishing rod", "tent", "luggage", "flag", "trophy", "medal", "certificate",
]

_ACTIVITIES_AND_EVENTS = [
    "swimming", "surfing", "skiing", "snowboarding", "skating", "running",
    "cycling", "climbing", "fishing", "sailing", "kayaking", "golf",
    "tennis", "soccer", "basketball", "baseball", "football", "hockey",
    "volleyball", "gymnastics", "yoga", "martial arts", "horse riding",
    "dancing", "singing", "playing music", "cooking", "picnic", "party",
    "dinner", "meeting", "classroom", "ceremony", "funeral", "baptism",
    "road trip", "sightseeing", "gardening", "building", "painting a wall",
    "playing in the snow", "playing on the beach", "birthday cake",
    "christmas tree", "easter", "thanksgiving", "fourth of july",
]

_STYLES = [
    "close-up", "silhouette", "reflection", "long exposure", "panorama",
    "bokeh", "blurry photo", "abstract", "pattern", "texture", "minimalist",
    "vintage photo", "old photograph", "illustration", "cartoon", "meme",
    "diagram", "chart", "infographic",
]


def _combined() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in (_BASE_TERMS, _PEOPLE, _MAMMALS, _BIRDS_AND_OTHER_ANIMALS, _PLANTS,
                  _LANDSCAPES, _PLACES_AND_BUILDINGS, _VEHICLES, _FOOD_AND_DRINK,
                  _OBJECTS, _ACTIVITIES_AND_EVENTS, _STYLES):
        for term in group:
            if term not in seen:
                seen.add(term)
                result.append(term)
    return result


VOCABULARY = _combined()

# Bump when VOCABULARY or the way a photo is matched to it changes, so photos
# tagged under an earlier version are tagged again on the next classification.
VOCABULARY_VERSION = 2
VOCABULARY_VERSION_KEY = "classify_vocab_version"

# A term is kept when it scores at least DEFAULT_THRESHOLD and is within
# DEFAULT_MARGIN of the photo's best-scoring term, so a photo gets the terms
# that stand out for it rather than whatever clears one fixed bar.
DEFAULT_THRESHOLD = 0.19
DEFAULT_MARGIN = 0.03
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


_vocab_cache: dict[tuple, dict] = {}


def _vocabulary_entry(encoder, vocabulary: list[str] | None) -> dict:
    """The encoded vocabulary for this model, built once and kept.

    With NumPy the term vectors become one matrix so a photo scores against
    every term in a single product; without it they are scored one at a time."""
    vocab = vocabulary or VOCABULARY
    key = (str(encoder.identity), None if vocabulary is None else tuple(vocabulary))
    entry = _vocab_cache.get(key)
    if entry is None:
        pairs = _encode_vocabulary(encoder, vocab)
        entry = {"pairs": pairs, "terms": [term for term, _vec in pairs], "matrix": None}
        if np is not None and pairs and len({len(vec) for _term, vec in pairs}) == 1:
            entry["matrix"] = np.asarray([vec for _term, vec in pairs], dtype="<f4")
        _vocab_cache[key] = entry
    return entry


def _score_terms(entry: dict, photo_vec: tuple[float, ...]) -> list[tuple[str, float]]:
    matrix = entry["matrix"]
    if matrix is not None and matrix.shape[1] == len(photo_vec):
        scores = matrix @ np.asarray(photo_vec, dtype="<f4")
        return list(zip(entry["terms"], (float(value) for value in scores)))
    return [(term, _dot(photo_vec, vec)) for term, vec in entry["pairs"]]


def classify_asset(
    embedding: bytes,
    encoder,
    vocabulary: list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
    margin: float = DEFAULT_MARGIN,
) -> list[tuple[str, float]]:
    """Classify a single photo embedding against the vocabulary.

    Returns up to top_n (term, score) pairs, sorted by score descending. A term
    is kept only if it scores at least ``threshold`` and is within ``margin`` of
    the photo's best-scoring term.
    """
    entry = _vocabulary_entry(encoder, vocabulary)
    photo_vec = _normalize(_decode(embedding))
    if not photo_vec:
        return []

    scored = _score_terms(entry, photo_vec)
    if not scored:
        return []
    cutoff = max(threshold, max(score for _term, score in scored) - margin)
    kept = [(term, score) for term, score in scored if score >= cutoff]
    kept.sort(key=lambda item: item[1], reverse=True)
    return kept[:top_n]


def _stored_vocabulary_version(db_path: Path) -> int:
    with connect(db_path) as con:
        row = con.execute(
            "SELECT value FROM library_metadata WHERE key=?", (VOCABULARY_VERSION_KEY,)
        ).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _store_vocabulary_version(db_path: Path) -> None:
    with connect(db_path) as con:
        con.execute(
            "INSERT INTO library_metadata(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (VOCABULARY_VERSION_KEY, str(VOCABULARY_VERSION)),
        )


def classify_library(
    db_path: Path,
    encoder=None,
    vocabulary: list[str] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    top_n: int = DEFAULT_TOP_N,
    progress=None,
    should_cancel=None,
    retag: bool = False,
    margin: float = DEFAULT_MARGIN,
) -> dict[str, int]:
    """Classify semantically-indexed photos and store results as tags.

    Tags are stored in asset_tags with source='semantic_auto'. Photos that
    already carry automatic tags are left alone, except when ``retag`` is set or
    the library was last tagged under an older vocabulary: then every photo is
    tagged again and its earlier automatic tags are replaced.
    Returns counts of photos classified and tags added.
    """
    from semantic_index import encoder_for, DEFAULT_MODEL

    redo = retag or (vocabulary is None and _stored_vocabulary_version(db_path) != VOCABULARY_VERSION)
    skip_clause = "" if redo else """AND NOT EXISTS (
                   SELECT 1 FROM asset_tags at
                   WHERE at.asset_id = se.asset_id AND at.source = 'semantic_auto'
               )"""
    with connect(db_path) as con:
        rows = con.execute(
            f"""SELECT se.asset_id, se.model, se.embedding_f32
               FROM semantic_embeddings se
               JOIN assets a ON a.id = se.asset_id
               WHERE a.in_review_bin = 0 {skip_clause}"""
        ).fetchall()

    if rows and encoder is None:
        models: dict[str, int] = {}
        for row in rows:
            models[str(row["model"])] = models.get(str(row["model"]), 0) + 1
        model = max(models, key=models.get) if models else DEFAULT_MODEL
        encoder = encoder_for(model)
    if rows:
        identity = str(encoder.identity)
        rows = [row for row in rows if str(row["model"]) == identity]

    counts = {"total": len(rows), "classified": 0, "tags_added": 0, "cancelled": False}
    if progress:
        progress(dict(counts))

    for row in rows:
        if should_cancel and should_cancel():
            counts["cancelled"] = True
            break

        matches = classify_asset(
            row["embedding_f32"], encoder, vocabulary, threshold, top_n, margin,
        )
        if not matches and not redo:
            counts["classified"] += 1
            if progress:
                progress(dict(counts))
            continue

        with connect(db_path) as con:
            if redo:
                con.execute(
                    "DELETE FROM asset_tags WHERE asset_id=? AND source='semantic_auto'",
                    (row["asset_id"],),
                )
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

    if not counts["cancelled"] and vocabulary is None:
        _store_vocabulary_version(db_path)
    return counts


def clear_classifications(db_path: Path) -> int:
    """Remove all semantic_auto tags. Returns count removed."""
    with connect(db_path) as con:
        cursor = con.execute("DELETE FROM asset_tags WHERE source='semantic_auto'")
        return cursor.rowcount
