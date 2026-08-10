#!/usr/bin/env python3
"""Optional local CLIP image indexing and natural-language photo search."""

from __future__ import annotations

import argparse
import array
import math
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image

from app_paths import libraries_root
from photo_index import connect, utc_now


DEFAULT_DB = libraries_root() / "default.sqlite3"
DEFAULT_MODEL = "ViT-B-32/openai"


def encode_vector(values: Iterable[float]) -> bytes:
    result = array.array("f", values)
    if result.itemsize != 4:
        raise RuntimeError("This Python build does not use 32-bit floats")
    return result.tobytes()


def decode_vector(blob: bytes) -> tuple[float, ...]:
    values = array.array("f")
    values.frombytes(blob)
    return tuple(values)


def normalize(values: Iterable[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    length = math.sqrt(sum(value * value for value in vector))
    return tuple(value / length for value in vector) if length else ()


def dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        return -1.0
    return sum(a * b for a, b in zip(left, right))


class OpenClipEncoder:
    """Lazy optional OpenCLIP adapter; base LensLedger never imports ML packages."""

    def __init__(self, model_name: str = "ViT-B-32", pretrained: str = "openai"):
        try:
            import open_clip
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "Local meaning search is optional. Install requirements-semantic.txt first."
            ) from exc
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=self.device
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.identity = f"{model_name}/{pretrained}"

    def encode_images(self, paths: list[Path]) -> list[tuple[float, ...] | None]:
        tensors = []
        positions = []
        result: list[tuple[float, ...] | None] = [None] * len(paths)
        for index, path in enumerate(paths):
            try:
                with Image.open(path) as image:
                    tensors.append(self.preprocess(image.convert("RGB")))
                    positions.append(index)
            except (OSError, ValueError):
                continue
        if not tensors:
            return result
        with self.torch.no_grad():
            batch = self.torch.stack(tensors).to(self.device)
            vectors = self.model.encode_image(batch)
            vectors = vectors / vectors.norm(dim=-1, keepdim=True)
        for index, vector in zip(positions, vectors.cpu().tolist()):
            result[index] = tuple(float(value) for value in vector)
        return result

    def encode_text(self, text: str) -> tuple[float, ...]:
        with self.torch.no_grad():
            tokens = self.tokenizer([text]).to(self.device)
            vector = self.model.encode_text(tokens)
            vector = vector / vector.norm(dim=-1, keepdim=True)
        return tuple(float(value) for value in vector[0].cpu().tolist())


@lru_cache(maxsize=2)
def is_available() -> bool:
    """Whether the optional meaning-search packages are importable, without
    loading the (large) model itself -- cheap enough to call on every
    status check."""
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def encoder_for(model: str = DEFAULT_MODEL):
    if model != DEFAULT_MODEL:
        raise RuntimeError(f"This release does not provide the semantic model: {model}")
    return OpenClipEncoder()


def status(db_path: Path) -> dict[str, object]:
    with connect(db_path) as con:
        indexed = int(con.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0])
        model_row = con.execute(
            "SELECT model,COUNT(*) AS count FROM semantic_embeddings GROUP BY model ORDER BY count DESC LIMIT 1"
        ).fetchone()
        eligible = int(con.execute(
            """SELECT COUNT(*) FROM assets WHERE media_type='image'
               AND metadata_scanned=1 AND in_review_bin=0"""
        ).fetchone()[0])
    return {
        "indexed": indexed,
        "eligible": eligible,
        "remaining": max(0, eligible - indexed),
        "model": model_row["model"] if model_row else DEFAULT_MODEL,
        "installed": is_available(),
    }


def build_index(
    db_path: Path,
    encoder=None,
    batch_size: int = 16,
    limit: int | None = None,
    progress: Callable[[dict[str, int]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, int | bool]:
    encoder = encoder or encoder_for(DEFAULT_MODEL)
    model = str(encoder.identity)
    with connect(db_path) as con:
        rows = con.execute(
            """SELECT a.id,a.path FROM assets a
               LEFT JOIN semantic_embeddings se ON se.asset_id=a.id AND se.model=?
               WHERE a.media_type='image' AND a.metadata_scanned=1 AND a.in_review_bin=0
                     AND se.asset_id IS NULL
               ORDER BY a.capture_date,a.relative_path""",
            (model,),
        ).fetchall()
    if limit is not None:
        rows = rows[:max(0, limit)]
    counts: dict[str, int | bool] = {
        "total": len(rows), "indexed": 0, "errors": 0, "cancelled": False,
    }
    if progress:
        progress(dict(counts))
    for start in range(0, len(rows), max(1, batch_size)):
        if should_cancel and should_cancel():
            counts["cancelled"] = True
            break
        batch = rows[start:start + max(1, batch_size)]
        vectors = encoder.encode_images([Path(row["path"]) for row in batch])
        with connect(db_path) as con:
            for row, vector in zip(batch, vectors):
                if not vector:
                    counts["errors"] += 1
                    continue
                con.execute(
                    """INSERT INTO semantic_embeddings(asset_id,model,dimensions,embedding_f32,updated_at)
                       VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET
                       model=excluded.model,dimensions=excluded.dimensions,
                       embedding_f32=excluded.embedding_f32,updated_at=excluded.updated_at""",
                    (int(row["id"]), model, len(vector), encode_vector(normalize(vector)), utc_now()),
                )
                counts["indexed"] += 1
        if progress:
            progress(dict(counts))
    return counts


def search(
    db_path: Path,
    text: str,
    limit: int = 250,
    offset: int = 0,
    encoder=None,
) -> list[tuple[int, float]]:
    text = " ".join(text.split())
    if not text:
        return []
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT asset_id,model,embedding_f32 FROM semantic_embeddings ORDER BY asset_id"
        ).fetchall()
    if not rows:
        raise RuntimeError(
            "Meaning search has not been indexed yet. Run semantic_index.py build first."
        )
    model = str(rows[0]["model"])
    encoder = encoder or encoder_for(model)
    query = normalize(encoder.encode_text(text))
    ranked = sorted(
        ((int(row["asset_id"]), dot(query, decode_vector(row["embedding_f32"]))) for row in rows),
        key=lambda item: item[1], reverse=True,
    )
    return ranked[max(0, offset):max(0, offset) + max(1, limit)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="download/load the optional model and index local images")
    build.add_argument("--batch-size", type=int, default=16)
    build.add_argument("--limit", type=int)
    query = sub.add_parser("query", help="find images matching a natural-language description")
    query.add_argument("text")
    query.add_argument("--limit", type=int, default=20)
    sub.add_parser("status", help="show semantic-index coverage without loading the model")
    args = parser.parse_args()
    if args.command == "status":
        for key, value in status(args.db).items():
            print(f"{key}: {value}")
        return 0
    if args.command == "build":
        result = build_index(args.db, batch_size=args.batch_size, limit=args.limit)
        for key, value in result.items():
            print(f"{key}: {value}")
        return 3 if result["cancelled"] else (2 if result["errors"] else 0)
    for asset_id, score in search(args.db, args.text, args.limit):
        print(f"{score:.4f}\t{asset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
