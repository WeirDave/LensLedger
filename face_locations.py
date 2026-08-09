"""Recover exact face rectangles for legacy LensLedger face embeddings.

The recovered catalog kept each 512-value face embedding but not the rectangle
that produced it.  This utility re-runs a compatible InsightFace model, matches
new embeddings to the stored vectors, and persists only unambiguous matches.
Photos and embeddings remain local.
"""

from __future__ import annotations

import argparse
import array
import math
import sqlite3
from pathlib import Path
from typing import Callable

from photo_index import connect, utc_now


def _unit(values) -> list[float]:
    vector = [float(value) for value in values]
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else vector


def cosine(left, right) -> float:
    return sum(a * b for a, b in zip(_unit(left), _unit(right)))


def _load_runtime(model_name: str, model_root: Path | None):
    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except ImportError as exc:
        raise RuntimeError(
            "Face location recovery is optional. Install requirements-face.txt "
            "with this Python interpreter first."
        ) from exc
    options = {"name": model_name, "providers": ["CPUExecutionProvider"]}
    if model_root:
        options["root"] = str(model_root)
    analyzer = FaceAnalysis(**options)
    analyzer.prepare(ctx_id=-1, det_size=(640, 640))
    return cv2, np, analyzer


def recover_face_locations(
    database: Path,
    library_root: Path,
    *,
    model_name: str = "buffalo_l",
    model_root: Path | None = None,
    min_similarity: float = 0.90,
    min_margin: float = 0.03,
    limit: int | None = None,
    face_ids: set[int] | None = None,
    suggestions_only: bool = False,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Locate legacy faces, accepting only strong and unambiguous vector matches."""
    cv2, np, analyzer = _load_runtime(model_name, model_root)
    con = connect(database)
    rows = con.execute(
        """SELECT DISTINCT f.asset_id,a.relative_path
           FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
           WHERE f.box_left IS NULL AND a.in_review_bin=0 AND (
               EXISTS (SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id)
               OR EXISTS (
                   SELECT 1 FROM person_face_profiles pfp,json_each(pfp.training_face_ids_json) training
                   WHERE CAST(training.value AS INTEGER)=f.id
               )
           )
           ORDER BY a.capture_date,a.relative_path"""
    ).fetchall()
    if face_ids:
        placeholders = ",".join("?" for _ in face_ids)
        selected_assets = {
            int(row[0]) for row in con.execute(
                f"SELECT DISTINCT asset_id FROM face_embeddings WHERE id IN ({placeholders})",
                tuple(sorted(face_ids)),
            )
        }
        rows = [row for row in rows if int(row["asset_id"]) in selected_assets]
    if suggestions_only:
        suggested_assets = {
            int(row[0]) for row in con.execute(
                "SELECT DISTINCT asset_id FROM asset_people WHERE state='suggested' AND face_id IS NOT NULL"
            )
        }
        rows = [row for row in rows if int(row["asset_id"]) in suggested_assets]
    if limit is not None:
        rows = rows[: max(0, limit)]
    result = {"total": len(rows), "processed": 0, "localized": 0, "unresolved": 0, "errors": 0}
    for asset in rows:
        try:
            target_rows = con.execute(
                """SELECT DISTINCT f.id,f.dimensions,f.embedding_f32
                   FROM face_embeddings f
                   WHERE f.asset_id=? AND f.box_left IS NULL AND (
                       EXISTS (SELECT 1 FROM asset_people ap WHERE ap.face_id=f.id)
                       OR EXISTS (
                           SELECT 1 FROM person_face_profiles pfp,json_each(pfp.training_face_ids_json) training
                           WHERE CAST(training.value AS INTEGER)=f.id
                       )
                   )""",
                (asset["asset_id"],),
            ).fetchall()
            path = library_root / Path(asset["relative_path"])
            image_bytes = np.fromfile(str(path), dtype=np.uint8)
            image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError("image could not be decoded")
            height, width = image.shape[:2]
            faces = analyzer.get(image)
            candidates = []
            for face in faces:
                embedding = getattr(face, "normed_embedding", None)
                box = getattr(face, "bbox", None)
                if embedding is not None and box is not None:
                    candidates.append((embedding, box))
            claimed: set[int] = set()
            for target in target_rows:
                stored = array.array("f")
                stored.frombytes(target["embedding_f32"])
                if len(stored) != target["dimensions"]:
                    result["unresolved"] += 1
                    continue
                scored = sorted(
                    ((cosine(stored, candidate[0]), index, candidate[1]) for index, candidate in enumerate(candidates)),
                    reverse=True,
                )
                available = [entry for entry in scored if entry[1] not in claimed]
                best = available[0] if available else None
                runner_up = available[1][0] if len(available) > 1 else -1.0
                if not best or best[0] < min_similarity or best[0] - runner_up < min_margin:
                    result["unresolved"] += 1
                    continue
                score, index, box = best
                left = max(0.0, min(1.0, float(box[0]) / width))
                top = max(0.0, min(1.0, float(box[1]) / height))
                right = max(0.0, min(1.0, float(box[2]) / width))
                bottom = max(0.0, min(1.0, float(box[3]) / height))
                if right <= left or bottom <= top:
                    result["unresolved"] += 1
                    continue
                con.execute(
                    """UPDATE face_embeddings SET box_left=?,box_top=?,box_right=?,box_bottom=?,
                       localization_similarity=?,localized_at=? WHERE id=?""",
                    (left, top, right, bottom, score, utc_now(), target["id"]),
                )
                claimed.add(index)
                result["localized"] += 1
            con.commit()
        except Exception:
            result["errors"] += 1
        result["processed"] += 1
        if progress:
            progress(dict(result))
    con.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--model", default="buffalo_l")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--face-id", type=int, action="append", dest="face_ids")
    parser.add_argument("--suggestions-only", action="store_true")
    args = parser.parse_args()
    result = recover_face_locations(
        args.db, args.library, model_name=args.model, model_root=args.model_root, limit=args.limit,
        face_ids=set(args.face_ids or []),
        suggestions_only=args.suggestions_only,
        progress=lambda state: print(
            f"face location progress: {state['processed']}/{state['total']} "
            f"({state['localized']} localized)", flush=True
        ),
    )
    print("face locations localized:", result["localized"])
    print("face locations unresolved:", result["unresolved"])
    print("face location errors:", result["errors"])
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
