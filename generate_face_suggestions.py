#!/usr/bin/env python3
"""Build review-only per-photo people suggestions from recovered face data."""

from __future__ import annotations

import argparse
import pickle
from pathlib import Path

try:
    import numpy as np
except ImportError as exc:
    raise SystemExit("This utility requires NumPy. LensLedger itself does not.") from exc

from app_paths import face_data_root, libraries_root
from photo_index import connect, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=libraries_root() / "default.sqlite3")
    parser.add_argument("--face-data", type=Path, default=face_data_root())
    parser.add_argument("--threshold", type=float, default=0.75)
    args = parser.parse_args()

    references_path = args.face_data / "named_refs.pkl"
    if not references_path.is_file():
        raise SystemExit(f"Named face references were not found: {references_path}")
    # This pickle is locally generated recovered state, not downloaded input.
    with references_path.open("rb") as stream:
        references = pickle.load(stream)
    names = list(references)
    reference_matrix = np.stack([references[name] for name in names])
    reference_matrix /= np.linalg.norm(reference_matrix, axis=1, keepdims=True)

    inserted = updated = 0
    with connect(args.db) as con:
        person_ids: dict[str, int] = {}
        for name in names:
            con.execute("INSERT OR IGNORE INTO people(name) VALUES (?)", (name,))
            person_ids[name] = int(con.execute(
                "SELECT id FROM people WHERE name=? COLLATE NOCASE", (name,)
            ).fetchone()[0])

        faces = con.execute(
            "SELECT id,asset_id,embedding_f32 FROM face_embeddings WHERE asset_id IS NOT NULL"
        ).fetchall()
        for face in faces:
            vector = np.frombuffer(face["embedding_f32"], dtype=np.float32)
            norm = np.linalg.norm(vector)
            if not norm:
                continue
            scores = reference_matrix @ (vector / norm)
            best = int(np.argmax(scores))
            confidence = float(scores[best])
            if confidence < args.threshold:
                continue
            person_id = person_ids[names[best]]
            old = con.execute(
                "SELECT state,confidence FROM asset_people WHERE asset_id=? AND person_id=?",
                (face["asset_id"], person_id),
            ).fetchone()
            if old is None:
                con.execute(
                    """INSERT INTO asset_people
                       (asset_id,person_id,state,confidence,face_id,source,updated_at)
                       VALUES (?,?,'suggested',?,?,'recovered_face',?)""",
                    (face["asset_id"], person_id, confidence, face["id"], utc_now()),
                )
                inserted += 1
            elif old["state"] == "suggested" and (old["confidence"] is None or confidence > old["confidence"]):
                con.execute(
                    "UPDATE asset_people SET confidence=?,face_id=?,updated_at=? WHERE asset_id=? AND person_id=?",
                    (confidence, face["id"], utc_now(), face["asset_id"], person_id),
                )
                updated += 1

        assets = int(con.execute(
            "SELECT COUNT(DISTINCT asset_id) FROM asset_people WHERE state='suggested'"
        ).fetchone()[0])
        suggestions = int(con.execute(
            "SELECT COUNT(*) FROM asset_people WHERE state='suggested'"
        ).fetchone()[0])

    print(f"new suggestions: {inserted}")
    print(f"updated suggestions: {updated}")
    print(f"photos awaiting people review: {assets}")
    print(f"people suggestions awaiting review: {suggestions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
