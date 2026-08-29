#!/usr/bin/env python3
"""Learn conservative person profiles from confirmed photos and suggest new matches."""

from __future__ import annotations

import argparse
import array
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

from app_paths import libraries_root
from photo_index import connect, rebuild_search_row, sync_person_tags, utc_now


MIN_PROFILE_FACES = 2
PROFILE_FACE_THRESHOLD = 0.66
SUGGESTION_THRESHOLD = 0.70
SUGGESTION_MARGIN = 0.02
# A match this confident, with this wide a gap over the runner-up, is safe to
# confirm immediately instead of waiting on a human decision: the reviewer
# can still reject it afterward exactly like any other confirmed match, so
# the cost of a rare wrong auto-confirm is the same one-click correction as
# today, not a silent, unreviewable mistake.
AUTO_CONFIRM_THRESHOLD = 0.90
AUTO_CONFIRM_MARGIN = 0.08
# Profile centroids are more stable than individual photo embeddings. A score
# above this level is strong evidence that two labels learned the same person;
# quarantine both instead of spreading a likely naming mistake.
PROFILE_COLLISION_THRESHOLD = 0.82


@dataclass
class Face:
    id: int
    asset_id: int
    vector: tuple[float, ...]


@dataclass
class Profile:
    person_id: int
    name: str
    centroid: tuple[float, ...]
    face_ids: list[int]
    asset_count: int
    cohesion: float


def decode_vector(blob: bytes) -> tuple[float, ...]:
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        return ()
    return tuple(value / norm for value in values)


def encode_vector(vector: tuple[float, ...]) -> bytes:
    values = array.array("f", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right))


def centroid(vectors: list[tuple[float, ...]]) -> tuple[float, ...]:
    if not vectors:
        return ()
    totals = [0.0] * len(vectors[0])
    for vector in vectors:
        for index, value in enumerate(vector):
            totals[index] += value
    norm = math.sqrt(sum(value * value for value in totals))
    return tuple(value / norm for value in totals) if norm else ()


def choose_seed(asset_faces: list[list[Face]]) -> tuple[float, ...]:
    single_faces = [faces[0].vector for faces in asset_faces if len(faces) == 1]
    if single_faces:
        provisional = centroid(single_faces)
        aligned = [vector for vector in single_faces if dot(vector, provisional) >= 0.62]
        return centroid(aligned or single_faces)

    candidates = [face for faces in asset_faces for face in faces]
    best_vector: tuple[float, ...] = ()
    best_rank = (-1, -1.0, -1.0)
    for candidate in candidates:
        bag_scores = [max(dot(candidate.vector, face.vector) for face in faces) for faces in asset_faces]
        support = sum(score >= 0.68 for score in bag_scores)
        rank = (support, statistics.median(bag_scores), sum(bag_scores) / len(bag_scores))
        if rank > best_rank:
            best_rank = rank
            best_vector = candidate.vector
    return best_vector


def build_profile(person_id: int, name: str, asset_faces: list[list[Face]]) -> Profile | None:
    profile_vector = choose_seed(asset_faces)
    if not profile_vector:
        return None
    selected: list[Face] = []
    for _ in range(4):
        selected = []
        for faces in asset_faces:
            candidate = max(faces, key=lambda face: dot(profile_vector, face.vector))
            if dot(profile_vector, candidate.vector) >= PROFILE_FACE_THRESHOLD:
                selected.append(candidate)
        if len(selected) < MIN_PROFILE_FACES:
            return None
        next_vector = centroid([face.vector for face in selected])
        if not next_vector:
            return None
        profile_vector = next_vector

    scores = [dot(profile_vector, face.vector) for face in selected]
    median = statistics.median(scores)
    floor = max(PROFILE_FACE_THRESHOLD, median - 0.10)
    selected = [face for face, score in zip(selected, scores) if score >= floor]
    if len(selected) < MIN_PROFILE_FACES:
        return None
    profile_vector = centroid([face.vector for face in selected])
    cohesion = statistics.median(dot(profile_vector, face.vector) for face in selected)
    if cohesion < 0.70:
        return None
    return Profile(person_id, name, profile_vector, [face.id for face in selected], len(selected), cohesion)


def learn(db_path: Path, *, apply: bool = True) -> dict[str, object]:
    with connect(db_path) as con:
        faces: list[Face] = []
        faces_by_asset: dict[int, list[Face]] = {}
        for row in con.execute(
            """SELECT id,asset_id,embedding_f32 FROM face_embeddings
               WHERE asset_id IS NOT NULL AND ignored_at IS NULL AND unknown_at IS NULL ORDER BY id"""
        ):
            vector = decode_vector(row["embedding_f32"])
            if not vector:
                continue
            face = Face(int(row["id"]), int(row["asset_id"]), vector)
            faces.append(face)
            faces_by_asset.setdefault(face.asset_id, []).append(face)

        profiles: list[Profile] = []
        people = con.execute(
            """SELECT p.id,p.name FROM people p WHERE EXISTS (
                   SELECT 1 FROM asset_people ap
                   WHERE ap.person_id=p.id AND ap.state='confirmed'
               ) ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()
        faces_by_id = {face.id: face for face in faces}
        for person in people:
            confirmed_rows = con.execute(
                "SELECT asset_id,face_id FROM asset_people WHERE person_id=? AND state='confirmed' ORDER BY asset_id",
                (person["id"],),
            ).fetchall()
            asset_faces = []
            for row in confirmed_rows:
                asset_id = int(row["asset_id"])
                if asset_id not in faces_by_asset:
                    continue
                face_id = int(row["face_id"]) if row["face_id"] is not None else None
                exact_face = faces_by_id.get(face_id) if face_id is not None else None
                if exact_face and exact_face.asset_id == asset_id:
                    asset_faces.append([exact_face])
                elif len(faces_by_asset[asset_id]) == 1:
                    asset_faces.append(faces_by_asset[asset_id])
                # A photo-level name on a group photo does not identify which
                # face belongs to that person. Treating every face as a bag can
                # accidentally learn the photographer who appears consistently
                # with many different friends. Wait for an exact reviewed face.
            if len(asset_faces) < MIN_PROFILE_FACES:
                continue
            profile = build_profile(int(person["id"]), str(person["name"]), asset_faces)
            if profile:
                profiles.append(profile)

        ambiguous_profile_ids: set[int] = set()
        ambiguous_pairs: list[dict[str, object]] = []
        for index, left in enumerate(profiles):
            for right in profiles[index + 1:]:
                similarity = dot(left.centroid, right.centroid)
                if similarity >= PROFILE_COLLISION_THRESHOLD:
                    ambiguous_profile_ids.update((left.person_id, right.person_id))
                    ambiguous_pairs.append({
                        "left": left.name, "right": right.name,
                        "similarity": round(similarity, 4),
                    })
        eligible_profiles = [
            profile for profile in profiles if profile.person_id not in ambiguous_profile_ids
        ]
        names = {profile.person_id: profile.name for profile in profiles}

        existing = {
            (int(row["asset_id"]), int(row["person_id"])): str(row["state"])
            for row in con.execute(
                """SELECT asset_id,person_id,state FROM asset_people
                   WHERE NOT (state='suggested' AND source='learned_face')"""
            )
        }
        proposals: dict[tuple[int, int], tuple[float, int]] = {}
        auto_confirms: dict[tuple[int, int], tuple[float, int]] = {}
        for face in faces:
            ranked = sorted(
                ((dot(face.vector, profile.centroid), profile) for profile in eligible_profiles),
                key=lambda item: item[0], reverse=True,
            )
            if not ranked:
                continue
            best_score, best_profile = ranked[0]
            second_score = ranked[1][0] if len(ranked) > 1 else -1.0
            margin = best_score - second_score
            key = (face.asset_id, best_profile.person_id)
            if key in existing:
                continue
            if best_score >= AUTO_CONFIRM_THRESHOLD and margin >= AUTO_CONFIRM_MARGIN:
                previous = auto_confirms.get(key)
                if previous is None or best_score > previous[0]:
                    auto_confirms[key] = (best_score, face.id)
                continue
            if best_score < SUGGESTION_THRESHOLD or margin < SUGGESTION_MARGIN:
                continue
            previous = proposals.get(key)
            if previous is None or best_score > previous[0]:
                proposals[key] = (best_score, face.id)

        auto_confirmed_details: list[dict[str, object]] = []
        if apply:
            con.execute("DELETE FROM person_face_profiles")
            for profile in profiles:
                con.execute(
                    """INSERT INTO person_face_profiles(
                           person_id,dimensions,centroid_f32,training_face_ids_json,
                           training_assets,cohesion,updated_at
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (profile.person_id, len(profile.centroid), encode_vector(profile.centroid),
                     json.dumps(profile.face_ids), profile.asset_count, profile.cohesion, utc_now()),
                )
            old_suggestions = con.execute(
                "SELECT face_id FROM asset_people WHERE state='suggested' AND source='learned_face' AND face_id IS NOT NULL"
            ).fetchall()
            con.execute("DELETE FROM asset_people WHERE state='suggested' AND source='learned_face'")
            for row in old_suggestions:
                con.execute("UPDATE face_embeddings SET person_id=NULL WHERE id=? AND person_id IS NOT NULL", (row["face_id"],))
            for (asset_id, person_id), (score, face_id) in proposals.items():
                con.execute(
                    """INSERT INTO asset_people(
                           asset_id,person_id,state,confidence,face_id,source,updated_at
                       ) VALUES (?,?,'suggested',?,?,'learned_face',?)""",
                    (asset_id, person_id, score, face_id, utc_now()),
                )
                con.execute("UPDATE face_embeddings SET person_id=? WHERE id=?", (person_id, face_id))
            for (asset_id, person_id), (score, face_id) in auto_confirms.items():
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,confidence,face_id,source,updated_at)
                       VALUES (?,?,'confirmed',?,?,'learned_face_auto',?)
                       ON CONFLICT(asset_id,person_id) DO UPDATE SET
                           state='confirmed',confidence=excluded.confidence,face_id=excluded.face_id,
                           source='learned_face_auto',updated_at=excluded.updated_at""",
                    (asset_id, person_id, score, face_id, utc_now()),
                )
                con.execute("UPDATE face_embeddings SET person_id=? WHERE id=?", (person_id, face_id))
                sync_person_tags(con, asset_id)
                rebuild_search_row(con, asset_id)
                auto_confirmed_details.append({
                    "asset_id": asset_id, "person_id": person_id,
                    "name": names.get(person_id, ""), "confidence": round(score, 4),
                })

        profile_summary = [
            {"person_id": profile.person_id, "name": profile.name,
             "training_assets": profile.asset_count, "cohesion": round(profile.cohesion, 4)}
            for profile in profiles
        ]
        suggestion_counts: dict[str, int] = {}
        for _, person_id in proposals:
            name = names[person_id]
            suggestion_counts[name] = suggestion_counts.get(name, 0) + 1
        proposal_samples: dict[str, list[dict[str, object]]] = {}
        for (asset_id, person_id), (score, face_id) in sorted(
            proposals.items(), key=lambda item: item[1][0], reverse=True
        ):
            name = names[person_id]
            samples = proposal_samples.setdefault(name, [])
            if len(samples) >= 5:
                continue
            asset = con.execute(
                "SELECT filename,folder FROM assets WHERE id=?", (asset_id,)
            ).fetchone()
            samples.append({
                "asset_id": asset_id, "face_id": face_id, "score": round(score, 4),
                "filename": asset["filename"], "folder": asset["folder"],
            })
        return {
            "faces": len(faces),
            "profiles": len(profiles),
            "eligible_profiles": len(eligible_profiles),
            "ambiguous_profile_pairs": ambiguous_pairs,
            "suggestions": len(proposals),
            "suggestions_by_person": dict(sorted(suggestion_counts.items())),
            "proposal_samples": proposal_samples,
            "profile_summary": profile_summary,
            "auto_confirmed": auto_confirmed_details,
            "applied": apply,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=libraries_root() / "default.sqlite3")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = learn(args.db, apply=not args.dry_run)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
