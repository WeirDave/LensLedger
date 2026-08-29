#!/usr/bin/env python3
"""Detect faces in photos that have never been face-scanned before.

Unlike face_locations.py (which recovers exact rectangles for embeddings
that already exist), this module runs full face detection and embedding
extraction from scratch for photos with no face_embeddings row at all. New
embeddings feed directly into face_learning.learn()'s existing suggestion
and auto-confirm pipeline -- nothing else needs to change to pick them up.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from PIL import ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True

from face_learning import encode_vector
from face_locations import is_available, load_insightface_runtime
from photo_index import connect, utc_now


def status(db_path: Path) -> dict[str, object]:
    with connect(db_path) as con:
        eligible = int(con.execute(
            "SELECT COUNT(*) FROM assets WHERE media_type='image' AND metadata_scanned=1 AND in_review_bin=0 AND extension != '.gif'"
        ).fetchone()[0])
        scanned = int(con.execute(
            """SELECT COUNT(*) FROM assets
               WHERE media_type='image' AND metadata_scanned=1 AND in_review_bin=0 AND face_scanned=1 AND extension != '.gif'"""
        ).fetchone()[0])
        faces_found = int(con.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE source='lensledger_scan'"
        ).fetchone()[0])
    return {
        "eligible": eligible,
        "scanned": scanned,
        "remaining": max(0, eligible - scanned),
        "faces_found": faces_found,
        "installed": is_available(),
    }


def list_errors(db_path: Path, limit: int = 200) -> list[dict[str, str]]:
    """Return the photos face detection could not process, most recent first."""
    with connect(db_path) as con:
        rows = con.execute(
            """SELECT path, relative_path, face_scan_error FROM assets
               WHERE face_scan_error<>'' ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [{"path": row["relative_path"], "full_path": row["path"], "error": row["face_scan_error"]} for row in rows]


def scan_for_faces(
    database: Path,
    library_root: Path,
    *,
    model_name: str = "buffalo_l",
    model_root: Path | None = None,
    limit: int | None = None,
    progress: Callable[[dict], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """Detect faces in every eligible image not yet face-scanned.

    A photo with zero faces in it is still marked face_scanned so it is
    never retried forever just because it never got a face_embeddings row
    -- the same convention ocr_scanned already uses for OCR."""
    cv2, np, analyzer = load_insightface_runtime(model_name, model_root)
    con = connect(database)
    try:
        rows = con.execute(
            """SELECT a.id,a.relative_path FROM assets a
               WHERE a.in_review_bin=0 AND a.media_type='image' AND a.metadata_scanned=1
                 AND a.face_scanned=0 AND a.extension != '.gif'
               ORDER BY a.capture_date,a.relative_path"""
        ).fetchall()
        if limit is not None:
            rows = rows[: max(0, limit)]
        result = {"total": len(rows), "processed": 0, "faces_found": 0, "errors": 0, "cancelled": False}
        next_face_id = int(con.execute(
            "SELECT COALESCE(MAX(source_face_id),0) FROM face_embeddings WHERE source='lensledger_scan'"
        ).fetchone()[0]) + 1
        for asset in rows:
            if should_cancel and should_cancel():
                result["cancelled"] = True
                break
            try:
                path = library_root / Path(asset["relative_path"])
                image_bytes = np.fromfile(str(path), dtype=np.uint8)
                image = cv2.imdecode(image_bytes, cv2.IMREAD_COLOR)
                if image is None:
                    from PIL import Image
                    try:
                        from pillow_heif import register_heif_opener
                        register_heif_opener()
                    except ImportError:
                        pass
                    with Image.open(path) as pil_img:
                        rgb = pil_img.convert("RGB")
                        image = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
                height, width = image.shape[:2]
                faces = analyzer.get(image)
                for face in faces:
                    embedding = getattr(face, "normed_embedding", None)
                    box = getattr(face, "bbox", None)
                    if embedding is None or box is None:
                        continue
                    left = max(0.0, min(1.0, float(box[0]) / width))
                    top = max(0.0, min(1.0, float(box[1]) / height))
                    right = max(0.0, min(1.0, float(box[2]) / width))
                    bottom = max(0.0, min(1.0, float(box[3]) / height))
                    if right <= left or bottom <= top:
                        continue
                    con.execute(
                        """INSERT INTO face_embeddings(
                               source,source_face_id,asset_id,relative_path,dimensions,embedding_f32,
                               box_left,box_top,box_right,box_bottom,localization_similarity,localized_at
                           ) VALUES ('lensledger_scan',?,?,?,?,?,?,?,?,?,1.0,?)""",
                        (next_face_id, int(asset["id"]), asset["relative_path"], len(embedding),
                         encode_vector(tuple(float(value) for value in embedding)),
                         left, top, right, bottom, utc_now()),
                    )
                    next_face_id += 1
                    result["faces_found"] += 1
                con.execute(
                    "UPDATE assets SET face_scanned=1,face_scan_error='' WHERE id=?", (int(asset["id"]),)
                )
            except Exception as exc:
                result["errors"] += 1
                con.execute(
                    "UPDATE assets SET face_scan_error=? WHERE id=?",
                    (str(exc)[:1000], int(asset["id"])),
                )
                print(f"FACE_SCAN_ERROR\t{asset['relative_path']}\t{exc}", file=sys.stderr)
            result["processed"] += 1
            if result["processed"] % 25 == 0:
                con.commit()
            if progress:
                progress(dict(result))
        con.commit()
        return result
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--model", default="buffalo_l")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = scan_for_faces(
        args.db, args.library, model_name=args.model, model_root=args.model_root, limit=args.limit,
        progress=lambda state: print(
            f"face scan progress: {state['processed']}/{state['total']} ({state['faces_found']} faces found)",
            flush=True,
        ),
    )
    print("faces found:", result["faces_found"])
    print("errors:", result["errors"])
    return 0 if result["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
