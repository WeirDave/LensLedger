"""Find People records that probably name the same person."""

from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata

from face_learning import decode_vector, dot
from photo_index import utc_now


# Profile centroids are averaged over several confirmed faces, so two labels
# for one person land close together. Below this level different relatives
# start to appear, which is noise in a list that asks for a merge.
FACE_DUPLICATE_THRESHOLD = 0.75
NAME_TYPO_RATIO = 0.88
MAX_PAIRS = 200
GENERATIONAL_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "2nd", "3rd", "4th"}


def normalize_name(name: str) -> tuple[str, ...]:
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return tuple(re.sub(r"[^\w\s]", " ", plain.casefold()).split())


def _suffix(tokens: tuple[str, ...]) -> str:
    return tokens[-1] if len(tokens) > 1 and tokens[-1] in GENERATIONAL_SUFFIXES else ""


def _initials_match(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    if len(left) != len(right) or len(left) < 2:
        return False
    used_initial = False
    full_matches = 0
    for a, b in zip(left, right):
        if a == b:
            full_matches += 1 if len(a) > 1 else 0
        elif (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b)):
            used_initial = True
        else:
            return False
    return used_initial and full_matches >= 1


def _contains(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    if not short or len(short) >= len(long):
        return False
    remaining = list(long)
    for token in short:
        if token not in remaining:
            return False
        remaining = remaining[remaining.index(token) + 1:]
    return True


def name_reason(left_names: list[tuple[str, ...]], right_names: list[tuple[str, ...]],
                token_owners: dict[str, int]) -> str:
    """Return why two sets of names look alike, or an empty string."""
    best = ""
    for left in left_names:
        for right in right_names:
            if not left or not right:
                continue
            if _suffix(left) and _suffix(right) and _suffix(left) != _suffix(right):
                continue
            if left == right:
                return "Same name apart from punctuation, accents or capitals"
            if _initials_match(left, right):
                best = best or "An initial matches the full name"
                continue
            short, long = (left, right) if len(left) < len(right) else (right, left)
            if _contains(short, long) and (len(short) > 1 or token_owners.get(short[0], 0) <= 2):
                best = best or "One name is part of the other"
                continue
            joined_left, joined_right = " ".join(left), " ".join(right)
            if min(len(joined_left), len(joined_right)) < 5:
                continue
            matcher = difflib.SequenceMatcher(None, joined_left, joined_right)
            if (matcher.real_quick_ratio() >= NAME_TYPO_RATIO and matcher.quick_ratio() >= NAME_TYPO_RATIO
                    and matcher.ratio() >= NAME_TYPO_RATIO):
                best = best or "Names are spelled almost the same"
    return best


def _pair_key(left: int, right: int) -> tuple[int, int]:
    return (left, right) if left < right else (right, left)


def find_possible_duplicates(con: sqlite3.Connection) -> list[dict[str, object]]:
    people = {
        int(row["id"]): {
            "id": int(row["id"]), "name": str(row["name"]),
            "confirmed_count": int(row["confirmed_count"]),
            "representative_id": row["representative_id"],
        }
        for row in con.execute(
            """SELECT p.id,p.name,
                   (SELECT COUNT(*) FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                    WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0) confirmed_count,
                   COALESCE(
                     p.card_asset_id,
                     (SELECT ap.asset_id FROM asset_people ap JOIN assets a ON a.id=ap.asset_id
                      WHERE ap.person_id=p.id AND ap.state='confirmed' AND a.in_review_bin=0 AND a.media_type='image'
                      ORDER BY a.capture_date DESC,a.id DESC LIMIT 1)
                   ) representative_id
               FROM people p"""
        )
    }
    if len(people) < 2:
        return []
    names: dict[int, list[tuple[str, ...]]] = {pid: [normalize_name(p["name"])] for pid, p in people.items()}
    for row in con.execute("SELECT person_id,alias FROM person_aliases"):
        if int(row["person_id"]) in names:
            names[int(row["person_id"])].append(normalize_name(str(row["alias"])))
    token_owners: dict[str, int] = {}
    for pid, forms in names.items():
        for token in {token for form in forms for token in form}:
            token_owners[token] = token_owners.get(token, 0) + 1

    dismissed = {
        (int(row[0]), int(row[1]))
        for row in con.execute("SELECT person_low,person_high FROM person_duplicate_dismissals")
    }
    # Two people confirmed in the same photo are two different people.
    seen_together = {
        _pair_key(int(row[0]), int(row[1]))
        for row in con.execute(
            """SELECT a.person_id,b.person_id FROM asset_people a
               JOIN asset_people b ON b.asset_id=a.asset_id AND b.person_id>a.person_id
               WHERE a.state='confirmed' AND b.state='confirmed'"""
        )
    }
    profiles = {
        int(row["person_id"]): decode_vector(row["centroid_f32"])
        for row in con.execute("SELECT person_id,centroid_f32 FROM person_face_profiles")
        if int(row["person_id"]) in people
    }

    reasons: dict[tuple[int, int], dict[str, object]] = {}
    profile_ids = sorted(profiles)
    for index, left in enumerate(profile_ids):
        for right in profile_ids[index + 1:]:
            if not profiles[left] or len(profiles[left]) != len(profiles[right]):
                continue
            similarity = dot(profiles[left], profiles[right])
            if similarity >= FACE_DUPLICATE_THRESHOLD:
                reasons[(left, right)] = {"face": round(similarity, 4), "name": ""}
    person_ids = sorted(people)
    for index, left in enumerate(person_ids):
        for right in person_ids[index + 1:]:
            reason = name_reason(names[left], names[right], token_owners)
            if reason:
                reasons.setdefault((left, right), {"face": None})["name"] = reason

    pairs = []
    for (left, right), found in reasons.items():
        if (left, right) in dismissed or (left, right) in seen_together:
            continue
        pair_reasons = []
        if found.get("name"):
            pair_reasons.append(found["name"])
        if found.get("face") is not None:
            pair_reasons.append("Their learned faces look alike")
        first, second = sorted((people[left], people[right]), key=lambda p: (-p["confirmed_count"], p["name"].casefold()))
        rank = (-len(pair_reasons), -(found.get("face") or 0), first["name"].casefold())
        pairs.append((rank, {
            "people": [first, second],
            "reasons": pair_reasons,
            "face_similarity": found.get("face"),
        }))
    pairs.sort(key=lambda item: item[0])
    return [pair for _, pair in pairs[:MAX_PAIRS]]


def dismiss_pair(con: sqlite3.Connection, left: int, right: int) -> None:
    if left == right:
        raise ValueError("choose two different people")
    low, high = _pair_key(left, right)
    found = con.execute("SELECT COUNT(*) FROM people WHERE id IN (?,?)", (low, high)).fetchone()[0]
    if int(found) != 2:
        raise ValueError("one of those people no longer exists")
    con.execute(
        "INSERT OR REPLACE INTO person_duplicate_dismissals(person_low,person_high,dismissed_at) VALUES (?,?,?)",
        (low, high, utc_now()),
    )
