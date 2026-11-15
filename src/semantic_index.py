#!/usr/bin/env python3
"""Optional local CLIP image indexing and natural-language photo search."""

from __future__ import annotations

import argparse
import array
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable

try:
    import numpy as np
except ImportError:  # NumPy is optional in LensLedger; it arrives with face detection.
    np = None

from PIL import Image, ImageFile, ImageOps
ImageFile.LOAD_TRUNCATED_IMAGES = True
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from app_paths import libraries_root
from photo_index import connect, unsupported_image_extensions, utc_now


def default_db() -> Path:
    """The database a command-line run uses when none is given."""
    return libraries_root() / "default.sqlite3"
DEFAULT_MODEL = "ViT-B-32/openai"

# Photos opened side by side within one batch, and the batch size used when a
# graphics card does the encoding (the card handles far more than the 16 a
# processor is given at a time).
IMAGE_LOAD_THREADS = max(2, min(8, os.cpu_count() or 4))
GPU_BATCH_SIZE = 64


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
                "Local meaning search is optional. Turn it on in Settings, or install the [meaning-search] packages from requirements.txt."
            ) from exc
        import logging
        import warnings
        logging.getLogger("huggingface_hub.utils._http").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", message="QuickGELU mismatch", module="open_clip")
        Image.MAX_IMAGE_PIXELS = 300_000_000
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.preferred_batch_size = GPU_BATCH_SIZE if self.device == "cuda" else 0
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=self.device
        )
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(model_name)
        self.identity = f"{model_name}/{pretrained}"

    def _encode_batch(self, tensors):
        """Unit-length image vectors for a list of preprocessed tensors. On a
        graphics card the model runs in half precision, which is much faster
        and changes the vectors only in the fourth decimal place."""
        with self.torch.no_grad(), self.torch.autocast(
            device_type="cuda", dtype=self.torch.float16, enabled=self.device == "cuda"
        ):
            batch = self.torch.stack(tensors).to(self.device)
            vectors = self.model.encode_image(batch).float()
            vectors = vectors / vectors.norm(dim=-1, keepdim=True)
        return vectors.cpu().tolist()

    def encode_images(self, paths: list[Path]) -> list[tuple[float, ...] | str | None]:
        """Return a vector per image, or a string error reason, or None."""
        result: list[tuple[float, ...] | str | None] = [None] * len(paths)

        def load(path):
            try:
                with Image.open(path) as image:
                    return self.preprocess(image.convert("RGB")), None
            except (OSError, ValueError) as exc:
                return None, str(exc) or "Unreadable image file"

        # Opening and shrinking photos is what keeps a graphics card waiting,
        # and Pillow releases the interpreter lock while it decodes, so the
        # photos of one batch are opened side by side.
        workers = max(1, min(len(paths), IMAGE_LOAD_THREADS))
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                loaded = list(pool.map(load, paths))
        else:
            loaded = [load(path) for path in paths]
        tensors = []
        positions = []
        for index, (tensor, error) in enumerate(loaded):
            if tensor is None:
                result[index] = error
            else:
                tensors.append(tensor)
                positions.append(index)
        if not tensors:
            return result
        for index, vector in zip(positions, self._encode_batch(tensors)):
            result[index] = tuple(float(value) for value in vector)
        return result

    def encode_crops(self, crops: list[Image.Image]) -> list[tuple[float, ...]]:
        """Return a vector for each already-loaded RGB image."""
        if not crops:
            return []
        vectors = self._encode_batch([self.preprocess(crop) for crop in crops])
        return [tuple(float(value) for value in vector) for vector in vectors]

    def encode_text(self, text: str) -> tuple[float, ...]:
        with self.torch.no_grad():
            tokens = self.tokenizer([text]).to(self.device)
            vector = self.model.encode_text(tokens)
            vector = vector / vector.norm(dim=-1, keepdim=True)
        return tuple(float(value) for value in vector[0].cpu().tolist())


_available_cache: bool | None = None


def is_available() -> bool:
    """Whether the optional meaning-search packages are importable, without
    loading the (large) model itself -- cheap enough to call on every
    status check.  Caches the result, but a True result sticks permanently
    while a False result is re-checked on the next call to
    ``clear_available_cache()`` (called after pip-installing the packages)."""
    global _available_cache
    if _available_cache is True:
        return True
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        _available_cache = False
        return False
    _available_cache = True
    return True


def clear_available_cache() -> None:
    """Allow ``is_available()`` to re-check after a pip install."""
    global _available_cache
    _available_cache = None


@lru_cache(maxsize=1)
def nvidia_gpu_name() -> str:
    """The name of an NVIDIA graphics card in this computer, or "" when there
    is none (or its driver tool is missing). Asked once per run."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        done = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return lines[0] if done.returncode == 0 and lines else ""


def describe_device(cuda_name: str, nvidia_name: str) -> dict[str, str]:
    """What meaning search runs on, and why, in words for the person using it.

    ``cuda_name`` is the card PyTorch can use ("" if it cannot use one);
    ``nvidia_name`` is an NVIDIA card found in the computer whether or not
    PyTorch can use it."""
    if cuda_name:
        return {"device": "cuda", "name": cuda_name,
                "summary": f"Using your graphics card ({cuda_name})."}
    if nvidia_name:
        return {"device": "cpu", "name": "",
                "summary": (f"Using the processor, not your graphics card ({nvidia_name}): the installed "
                            "PyTorch has no NVIDIA support, so indexing is far slower than it needs to be. "
                            "Use the \"Use my graphics card\" button on the Scan your photos page to fix it.")}
    return {"device": "cpu", "name": "", "summary": "Using the processor."}


def device_status() -> dict[str, str]:
    """The device meaning search would use now, without loading the model.
    Empty when the optional packages are not installed."""
    if not is_available():
        return {"device": "", "name": "", "summary": ""}
    import torch
    cuda_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else ""
    return describe_device(cuda_name, nvidia_gpu_name())


CUDA_WHEEL_BASE = "https://download.pytorch.org/whl"
GPU_SWITCH_MARKER = "gpu-torch-pending"


def cuda_driver_version() -> tuple[int, int] | None:
    """The newest CUDA version the installed NVIDIA driver supports, from the
    header of ``nvidia-smi``, or None when it cannot be read."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_cuda_version(done.stdout or "")


def parse_cuda_version(header: str) -> tuple[int, int] | None:
    """The CUDA version in an ``nvidia-smi`` header. Older drivers print
    "CUDA Version: 12.6", newer ones "CUDA UMD Version: 13.5"."""
    found = re.search(r"CUDA\s+(?:\w+\s+)?Version:\s*(\d+)\.(\d+)", header)
    return (int(found.group(1)), int(found.group(2))) if found else None


def wheel_tag_for_driver(version: tuple[int, int] | None) -> str:
    """The PyTorch download folder that suits a driver's CUDA version: the
    newest build the driver can run, or "" when it is too old for any."""
    if version is None:
        return ""
    if version >= (13, 0):
        return "cu130"
    if version >= (12, 6):
        return "cu126"
    return ""


@lru_cache(maxsize=1)
def cuda_wheel_tag() -> str:
    """The PyTorch build to use for this computer's NVIDIA card. Only Windows
    needs asking: PyPI's Windows PyTorch has no card support, while Linux's
    already has it and macOS has none to give."""
    if sys.platform != "win32" or not nvidia_gpu_name():
        return ""
    return wheel_tag_for_driver(cuda_driver_version())


def installed_torch_version() -> str:
    """The version string of the installed PyTorch ("2.13.0+cpu"), without
    importing it; "" when it is not installed."""
    try:
        from importlib.metadata import version
        return version("torch")
    except Exception:
        return ""


def torch_lacks_gpu_support(installed: str, tag: str) -> bool:
    """True when a card PyTorch could use exists but the installed build is one
    without card support."""
    return bool(tag and installed and "+cu" not in installed)


def gpu_switch_available() -> bool:
    return torch_lacks_gpu_support(installed_torch_version(), cuda_wheel_tag())


def cuda_torch_pip_args(requirement: str, tag: str) -> list[str]:
    """pip arguments that install the NVIDIA build of PyTorch. The build lives
    on PyTorch's own download site, which carries what it needs, so PyPI is
    not consulted for it (that is what keeps pip from picking the card-less
    PyPI build over it)."""
    return ["install", "--upgrade", requirement, "--index-url", f"{CUDA_WHEEL_BASE}/{tag}"]


def run_pip(args: list[str], timeout: int, stream: bool = False) -> tuple[bool, str]:
    """Run ``python -m pip`` with ``args``; return (succeeded, last lines of output)."""
    command = [sys.executable, "-m", "pip", *args]
    try:
        if stream:
            done = subprocess.run(command, timeout=timeout)
            return done.returncode == 0, ""
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    tail = "\n".join(((done.stderr or "") + (done.stdout or "")).strip().splitlines()[-8:])
    return done.returncode == 0, tail


def effective_batch_size(encoder, requested: int) -> int:
    """The requested batch size, raised to what the encoder's device handles well."""
    preferred = getattr(encoder, "preferred_batch_size", 0)
    if isinstance(preferred, int) and not isinstance(preferred, bool):
        return max(requested, preferred)
    return requested


SUPPORTED_MODELS = {
    "ViT-B-32/openai": ("ViT-B-32", "openai"),
    "ViT-B-16/openai": ("ViT-B-16", "openai"),
    "ViT-L-14/openai": ("ViT-L-14", "openai"),
}

MODEL_HF_REPOS = {
    "ViT-B-32/openai": "models--timm--vit_base_patch32_clip_224.openai",
    "ViT-B-16/openai": "models--timm--vit_base_patch16_clip_224.openai",
    "ViT-L-14/openai": "models--timm--vit_large_patch14_clip_224.openai",
}


def _hf_cache_dir() -> Path:
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def model_cache_info() -> dict[str, dict]:
    """Return download status and file size for each supported model."""
    hub_dir = _hf_cache_dir()
    result: dict[str, dict] = {}
    for model_id, repo_dir in MODEL_HF_REPOS.items():
        model_dir = hub_dir / repo_dir / "snapshots"
        found = False
        total_bytes = 0
        if model_dir.is_dir():
            for snapshot in model_dir.iterdir():
                for f in snapshot.iterdir():
                    if f.suffix in (".safetensors", ".pt", ".bin"):
                        found = True
                        total_bytes += f.stat().st_size
        if found:
            size_mb = total_bytes / (1024 * 1024)
            result[model_id] = {"downloaded": True, "size_mb": round(size_mb, 1)}
        else:
            result[model_id] = {"downloaded": False, "size_mb": 0}
    return result


def delete_cached_model(model_id: str) -> bool:
    """Delete a downloaded model from the HuggingFace cache. Returns True if deleted."""
    repo_dir = MODEL_HF_REPOS.get(model_id)
    if not repo_dir:
        raise ValueError(f"Unknown model: {model_id}")
    path = _hf_cache_dir() / repo_dir
    if path.is_dir():
        import shutil
        shutil.rmtree(path)
        return True
    return False


def encoder_for(model: str = DEFAULT_MODEL):
    parts = SUPPORTED_MODELS.get(model)
    if not parts:
        raise RuntimeError(f"Unsupported semantic model: {model}. Supported: {', '.join(SUPPORTED_MODELS)}")
    return OpenClipEncoder(model_name=parts[0], pretrained=parts[1])


def status(db_path: Path) -> dict[str, object]:
    with connect(db_path) as con:
        indexed = int(con.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0])
        model_row = con.execute(
            "SELECT model,COUNT(*) AS count FROM semantic_embeddings GROUP BY model ORDER BY count DESC LIMIT 1"
        ).fetchone()
        eligible = int(con.execute(
            """SELECT COUNT(*) FROM assets WHERE media_type='image'
               AND metadata_scanned=1 AND in_review_bin=0 AND extension != '.gif'"""
        ).fetchone()[0])
        columns = {row[1] for row in con.execute("PRAGMA table_info(assets)")}
        if "semantic_error" in columns:
            failed = int(con.execute(
                "SELECT COUNT(*) FROM assets WHERE semantic_error<>'' AND in_review_bin=0"
            ).fetchone()[0])
        else:
            failed = 0
        model = model_row["model"] if model_row else DEFAULT_MODEL
        missing = int(con.execute(
            """SELECT COUNT(*) FROM assets a
               LEFT JOIN semantic_embeddings se ON se.asset_id=a.id AND se.model=?
               WHERE a.media_type='image' AND a.metadata_scanned=1
                     AND a.in_review_bin=0 AND a.extension != '.gif' AND se.asset_id IS NULL""",
            (model,),
        ).fetchone()[0])
        if not failed and indexed > 0:
            if missing > 0 and missing < eligible * 0.05:
                failed = missing
    return {
        "indexed": indexed,
        "eligible": eligible,
        "remaining": max(0, eligible - indexed - failed),
        "missing": missing,
        "failed": failed,
        "model": model_row["model"] if model_row else DEFAULT_MODEL,
        "installed": is_available(),
    }


BUILD_MODES = ("new", "missing", "all")

MAX_REPORTED_FAILURES = 200


def build_index(
    db_path: Path,
    encoder=None,
    batch_size: int = 16,
    limit: int | None = None,
    progress: Callable[[dict[str, int]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    mode: str = "new",
) -> dict[str, object]:
    """Index images for meaning search.

    ``mode`` selects which photos are considered:

    ``new``      only photos with no embedding that have never failed.
    ``missing``  every photo with no embedding, including ones whose last
                 attempt recorded an error -- the recorded error is cleared
                 first so the attempt is a genuine retry rather than a skip.
    ``all``      every eligible photo, re-encoding ones already indexed.
    """
    if mode not in BUILD_MODES:
        raise ValueError(f"Unknown build mode: {mode}. Expected one of {', '.join(BUILD_MODES)}")
    encoder = encoder or encoder_for(DEFAULT_MODEL)
    model = str(encoder.identity)
    skip_exts = unsupported_image_extensions() | {".gif"}
    ext_clause = " AND ".join(["a.extension != ?"] * len(skip_exts))
    if mode == "new":
        scope_clause = "AND a.semantic_error='' AND se.asset_id IS NULL"
    elif mode == "missing":
        scope_clause = "AND se.asset_id IS NULL"
    else:
        scope_clause = ""
    with connect(db_path) as con:
        rows = con.execute(
            f"""SELECT a.id,a.path,a.relative_path FROM assets a
               LEFT JOIN semantic_embeddings se ON se.asset_id=a.id AND se.model=?
               WHERE a.media_type='image' AND a.metadata_scanned=1 AND a.in_review_bin=0
                     AND {ext_clause} {scope_clause}
               ORDER BY a.capture_date,a.relative_path""",
            (model, *sorted(skip_exts)),
        ).fetchall()
        if limit is not None:
            rows = rows[:max(0, limit)]
        if mode in ("missing", "all") and rows:
            con.executemany(
                "UPDATE assets SET semantic_error='' WHERE id=?",
                [(int(row["id"]),) for row in rows],
            )
    counts: dict[str, object] = {
        "total": len(rows), "indexed": 0, "errors": 0, "cancelled": False,
        "mode": mode, "failures": [],
    }

    def snapshot() -> dict[str, object]:
        copy = dict(counts)
        copy["failures"] = list(counts["failures"])
        return copy

    if progress:
        progress(snapshot())
    batch_size = effective_batch_size(encoder, max(1, batch_size))
    for start in range(0, len(rows), batch_size):
        if should_cancel and should_cancel():
            counts["cancelled"] = True
            break
        batch = rows[start:start + batch_size]
        vectors = encoder.encode_images([Path(row["path"]) for row in batch])
        with connect(db_path) as con:
            for row, vector in zip(batch, vectors):
                if not vector or isinstance(vector, str):
                    counts["errors"] += 1
                    error_msg = vector if isinstance(vector, str) else "Image could not be encoded by the meaning-search model."
                    con.execute(
                        "UPDATE assets SET semantic_error=? WHERE id=?",
                        (error_msg, int(row["id"])),
                    )
                    if len(counts["failures"]) < MAX_REPORTED_FAILURES:
                        counts["failures"].append({
                            "path": row["relative_path"],
                            "full_path": row["path"],
                            "error": error_msg,
                        })
                    continue
                con.execute(
                    """INSERT INTO semantic_embeddings(asset_id,model,dimensions,embedding_f32,updated_at)
                       VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET
                       model=excluded.model,dimensions=excluded.dimensions,
                       embedding_f32=excluded.embedding_f32,updated_at=excluded.updated_at""",
                    (int(row["id"]), model, len(vector), encode_vector(normalize(vector)), utc_now()),
                )
                con.execute("UPDATE assets SET semantic_error='' WHERE id=?", (int(row["id"]),))
                counts["indexed"] += 1
        if progress:
            progress(snapshot())
    if counts["indexed"]:
        clear_matrix_cache(db_path)
    return counts


# The crop around a detected face is this many face-widths across, centred a
# little above the face so the top of the head and the hair are included.
FACE_CROP_SPAN = 2.4
FACE_CROP_LIFT = 0.15
FACE_CROP_MIN_PIXELS = 224


def face_crop(image: Image.Image, left: float, top: float, right: float, bottom: float) -> Image.Image | None:
    """A square head-and-shoulders crop around a face box given as fractions of
    the (already upright) image, or None when the box is empty."""
    width, height = image.size
    box_w = (right - left) * width
    box_h = (bottom - top) * height
    if box_w < 2 or box_h < 2:
        return None
    side = min(max(box_w, box_h) * FACE_CROP_SPAN, width, height)
    centre_x = (left + right) / 2 * width
    centre_y = (top + bottom) / 2 * height - box_h * FACE_CROP_LIFT
    crop_left = min(max(0.0, centre_x - side / 2), width - side)
    crop_top = min(max(0.0, centre_y - side / 2), height - side)
    return image.crop((int(crop_left), int(crop_top), int(crop_left + side), int(crop_top + side)))


def _open_upright(path: Path, box_fraction: float) -> Image.Image:
    """Open a photo upright, decoding a JPEG at reduced scale when the face is
    large enough that the crop would still be at least FACE_CROP_MIN_PIXELS."""
    image = Image.open(path)
    try:
        if image.format == "JPEG" and box_fraction > 0:
            scale = min(1.0, FACE_CROP_MIN_PIXELS / (max(image.size) * box_fraction * FACE_CROP_SPAN))
            image.draft("RGB", (max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale))))
        return ImageOps.exif_transpose(image).convert("RGB")
    finally:
        image.close()


def face_crop_pending(db_path: Path, model: str = DEFAULT_MODEL) -> int:
    """How many detected faces have no crop vector yet for ``model``."""
    with connect(db_path) as con:
        return int(con.execute(
            """SELECT COUNT(*) FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
               LEFT JOIN face_semantic_embeddings fs ON fs.face_id=f.id AND fs.model=?
               WHERE a.media_type='image' AND a.in_review_bin=0 AND f.ignored_at IS NULL
                     AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                     AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                     AND fs.face_id IS NULL""",
            (model,),
        ).fetchone()[0])


def build_face_index(
    db_path: Path,
    encoder=None,
    batch_size: int = 16,
    progress: Callable[[dict[str, int]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Encode the crop around every located face that has no crop vector yet,
    so appearance words (beard, bald, glasses) can be matched to a person's
    face rather than to the whole photo. Each photo is opened once."""
    encoder = encoder or encoder_for(DEFAULT_MODEL)
    model = str(encoder.identity)
    skip_exts = unsupported_image_extensions() | {".gif"}
    ext_clause = " AND ".join(["a.extension != ?"] * len(skip_exts))
    with connect(db_path) as con:
        rows = con.execute(
            f"""SELECT f.id AS face_id, f.asset_id, a.path,
                       f.box_left, f.box_top, f.box_right, f.box_bottom
                FROM face_embeddings f JOIN assets a ON a.id=f.asset_id
                LEFT JOIN face_semantic_embeddings fs ON fs.face_id=f.id AND fs.model=?
                WHERE a.media_type='image' AND a.in_review_bin=0 AND f.ignored_at IS NULL
                      AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
                      AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
                      AND fs.face_id IS NULL AND {ext_clause}
                ORDER BY f.asset_id, f.id""",
            (model, *sorted(skip_exts)),
        ).fetchall()
    by_asset: dict[int, list] = {}
    for row in rows:
        by_asset.setdefault(int(row["asset_id"]), []).append(row)
    counts: dict[str, object] = {"total": len(rows), "indexed": 0, "errors": 0, "cancelled": False}
    if progress:
        progress(dict(counts))
    pending: list[tuple[int, Image.Image]] = []

    def flush() -> None:
        if not pending:
            return
        vectors = encoder.encode_crops([crop for _face_id, crop in pending])
        with connect(db_path) as con:
            for (face_id, _crop), vector in zip(pending, vectors):
                if not vector:
                    counts["errors"] += 1
                    continue
                con.execute(
                    """INSERT INTO face_semantic_embeddings(face_id,model,dimensions,embedding_f32,updated_at)
                       VALUES (?,?,?,?,?) ON CONFLICT(face_id) DO UPDATE SET
                       model=excluded.model,dimensions=excluded.dimensions,
                       embedding_f32=excluded.embedding_f32,updated_at=excluded.updated_at""",
                    (face_id, model, len(vector), encode_vector(normalize(vector)), utc_now()),
                )
                counts["indexed"] += 1
        pending.clear()
        if progress:
            progress(dict(counts))

    for faces in by_asset.values():
        if should_cancel and should_cancel():
            counts["cancelled"] = True
            break
        try:
            widest = max(max(f["box_right"] - f["box_left"], f["box_bottom"] - f["box_top"]) for f in faces)
            image = _open_upright(Path(faces[0]["path"]), float(widest))
        except (OSError, ValueError, Image.DecompressionBombError):
            counts["errors"] += len(faces)
            continue
        for face in faces:
            crop = face_crop(image, face["box_left"], face["box_top"], face["box_right"], face["box_bottom"])
            if crop is None:
                counts["errors"] += 1
                continue
            pending.append((int(face["face_id"]), crop))
        image.close()
        if len(pending) >= max(1, batch_size):
            flush()
    flush()
    return counts


def rank_faces(
    db_path: Path,
    text: str,
    face_ids: Iterable[int],
    encoder=None,
    chunk: int = 800,
) -> dict[int, float]:
    """Cosine score of ``text`` against each face's crop vector, for the faces
    in ``face_ids`` that have one. Faces without a crop vector are left out."""
    text = " ".join(text.split())
    wanted = sorted({int(face_id) for face_id in face_ids})
    if not text or not wanted:
        return {}
    with connect(db_path) as con:
        model_row = con.execute(
            "SELECT model FROM face_semantic_embeddings GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1"
        ).fetchone()
        if not model_row:
            return {}
        model = str(model_row["model"])
        rows = []
        for start in range(0, len(wanted), chunk):
            part = wanted[start:start + chunk]
            marks = ",".join("?" for _ in part)
            rows.extend(con.execute(
                f"SELECT face_id,embedding_f32 FROM face_semantic_embeddings WHERE model=? AND face_id IN ({marks})",
                (model, *part),
            ).fetchall())
    if not rows:
        return {}
    encoder = encoder or encoder_for(model)
    query = normalize(encoder.encode_text(text))
    scores: dict[int, float] = {}
    for row in rows:
        scores[int(row["face_id"])] = dot(query, decode_vector(row["embedding_f32"]))
    return scores


def blend_person_faces(
    db_path: Path,
    text: str,
    scored: list[tuple[int, float]],
    person_ids: Iterable[int],
    encoder=None,
    chunk: int = 800,
) -> list[tuple[int, float]]:
    """Re-rank ``(asset_id, score)`` pairs so each photo scores by the better of
    its whole-photo match and the best match among the crops of the chosen
    people's confirmed faces in it. Photos whose faces have no crop vector keep
    their whole-photo score."""
    people = sorted({int(person_id) for person_id in person_ids})
    if not scored or not people:
        return scored
    asset_ids = [asset_id for asset_id, _score in scored]
    person_marks = ",".join("?" for _ in people)
    links: dict[int, list[int]] = {}
    with connect(db_path) as con:
        for start in range(0, len(asset_ids), chunk):
            part = asset_ids[start:start + chunk]
            marks = ",".join("?" for _ in part)
            for row in con.execute(
                f"""SELECT asset_id,face_id FROM asset_people
                    WHERE state='confirmed' AND face_id IS NOT NULL
                          AND person_id IN ({person_marks}) AND asset_id IN ({marks})""",
                (*people, *part),
            ):
                links.setdefault(int(row["asset_id"]), []).append(int(row["face_id"]))
    face_scores = rank_faces(
        db_path, text, (face_id for faces in links.values() for face_id in faces), encoder
    )
    if not face_scores:
        return scored
    blended = []
    for asset_id, score in scored:
        best = max((face_scores[face_id] for face_id in links.get(asset_id, ()) if face_id in face_scores),
                   default=None)
        blended.append((asset_id, score if best is None else max(score, best)))
    blended.sort(key=lambda item: item[1], reverse=True)
    return blended


# A decoded copy of a library's embeddings, kept in memory so repeated and
# paged searches don't re-read and re-decode the whole table every time. Only
# the stored side is cached; the query is still encoded on each call.
_matrix_cache: dict[str, dict] = {}
_matrix_cache_lock = threading.Lock()


def clear_matrix_cache(db_path: Path | None = None) -> None:
    """Drop the cached embeddings for one library, or all of them."""
    with _matrix_cache_lock:
        if db_path is None:
            _matrix_cache.clear()
        else:
            _matrix_cache.pop(str(db_path), None)


def _embeddings_signature(con) -> tuple[int, str]:
    """A cheap fingerprint of the embedding table: a changed row count or a
    newer write invalidates the cache without reading any vector."""
    row = con.execute(
        "SELECT COUNT(*) AS n, COALESCE(MAX(updated_at),'') AS stamp FROM semantic_embeddings"
    ).fetchone()
    return int(row["n"]), str(row["stamp"])


def _load_matrix(db_path: Path) -> dict:
    """The decoded embeddings for ``db_path``, cached and rebuilt only when the
    table has changed since it was last read.

    With NumPy the vectors become one ``(rows, dimensions)`` float32 matrix, so a
    query scores as a single matrix-vector product; without it they stay a list of
    tuples scored by the pure-Python ``dot`` fallback. Vectors are stored already
    L2-normalized, so neither path renormalizes them. Any row whose blob width does
    not match the library's dominant dimension is set aside (``off_ids``) and scored
    -1.0, exactly as ``dot`` treats a length mismatch."""
    key = str(db_path)
    with connect(db_path) as con:
        signature = _embeddings_signature(con)
        with _matrix_cache_lock:
            cached = _matrix_cache.get(key)
            if cached is not None and cached["signature"] == signature:
                return cached
        rows = con.execute(
            "SELECT asset_id,model,embedding_f32 FROM semantic_embeddings ORDER BY asset_id"
        ).fetchall()
    ids = [int(row["asset_id"]) for row in rows]
    model = str(rows[0]["model"]) if rows else DEFAULT_MODEL
    entry: dict = {"signature": signature, "ids": ids, "model": model,
                   "matrix": None, "matrix_ids": [], "off_ids": [], "vectors": None}
    if rows and np is not None:
        sizes = Counter(len(row["embedding_f32"]) // 4 for row in rows if row["embedding_f32"])
        width = (sizes.most_common(1)[0][0] if sizes else 0) * 4
        kept = [row for row in rows if row["embedding_f32"] and len(row["embedding_f32"]) == width]
        if kept:
            buffer = b"".join(row["embedding_f32"] for row in kept)
            entry["matrix"] = np.frombuffer(buffer, dtype="<f4").reshape(len(kept), width // 4)
            entry["matrix_ids"] = [int(row["asset_id"]) for row in kept]
        entry["off_ids"] = [int(row["asset_id"]) for row in rows
                            if not (row["embedding_f32"] and len(row["embedding_f32"]) == width)]
    elif rows:
        entry["vectors"] = [(int(row["asset_id"]), decode_vector(row["embedding_f32"])) for row in rows]
    with _matrix_cache_lock:
        _matrix_cache[key] = entry
    return entry


def _rank(entry: dict, query: tuple[float, ...]) -> list[tuple[int, float]]:
    """Score every cached vector against ``query`` (a cosine, since both sides are
    unit-length) and return (asset_id, score) pairs, highest first."""
    if entry["vectors"] is not None:
        return sorted(
            ((asset_id, dot(query, vector)) for asset_id, vector in entry["vectors"]),
            key=lambda item: item[1], reverse=True,
        )
    matrix = entry["matrix"]
    if matrix is not None and matrix.shape[1] == len(query):
        scores = matrix @ np.asarray(query, dtype="<f4")
        pairs = list(zip(entry["matrix_ids"], (float(value) for value in scores)))
    else:
        pairs = [(asset_id, -1.0) for asset_id in entry["matrix_ids"]]
    pairs.extend((asset_id, -1.0) for asset_id in entry["off_ids"])
    return sorted(pairs, key=lambda item: item[1], reverse=True)


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
    entry = _load_matrix(db_path)
    if not entry["ids"]:
        raise RuntimeError(
            "Meaning search has not been indexed yet. Run semantic_index.py build first."
        )
    encoder = encoder or encoder_for(entry["model"])
    query = normalize(encoder.encode_text(text))
    ranked = _rank(entry, query)
    return ranked[max(0, offset):max(0, offset) + max(1, limit)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db())
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="download/load the optional model and index local images")
    build.add_argument("--batch-size", type=int, default=16)
    build.add_argument("--limit", type=int)
    build.add_argument("--mode", choices=BUILD_MODES, default="new",
                       help="new: unseen photos only; missing: also retry previous failures; all: re-index everything")
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
        result = build_index(args.db, batch_size=args.batch_size, limit=args.limit, mode=args.mode)
        for failure in result.pop("failures", []):
            print(f"FAILED	{failure['path']}	{failure['error']}", file=sys.stderr)
        for key, value in result.items():
            print(f"{key}: {value}")
        return 3 if result["cancelled"] else (2 if result["errors"] else 0)
    for asset_id, score in search(args.db, args.text, args.limit):
        print(f"{score:.4f}\t{asset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
