"""Recover exact face rectangles for legacy LensLedger face embeddings.

The recovered catalog kept each 512-value face embedding but not the rectangle
that produced it.  This utility re-runs a compatible InsightFace model, matches
new embeddings to the stored vectors, and persists only unambiguous matches.
Photos and embeddings remain local.
"""

from __future__ import annotations

import argparse
import array
import contextlib
import math
import os
import sqlite3
import warnings
from pathlib import Path
from typing import Callable

from PIL import Image

from photo_index import connect, utc_now
from semantic_index import (
    cuda_wheel_tag, describe_device, distribution_version, installed_torch_version,
    nvidia_gpu_name, onnx_gpu_requirement, run_pip,
)

# Pillow's notes about oddities in a readable file (damaged camera data, a very
# large picture) otherwise print to the console as if something had failed.
for _category in (UserWarning, Image.DecompressionBombWarning):
    warnings.filterwarnings("ignore", category=_category, module=r"PIL\.")

_PILLOW_FIRST = {".jpg", ".jpeg", ".png"}
_UPRIGHT = {
    2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE",
    6: "ROTATE_270", 7: "TRANSVERSE", 8: "ROTATE_90",
}


def _exif_orientation(image) -> int:
    # Not image.getexif(): that also reads an orientation from XMP, which OpenCV
    # ignores, and stored face boxes follow OpenCV's reading.
    try:
        exif = Image.Exif()
        exif.load(image.info["exif"])
        return int(exif.get(274, 1))
    except Exception:
        return 1


def _read_upright_with_pillow(path: Path, cv2, np):
    with Image.open(path) as source:
        # Pillow clips 16-bit greyscale to white instead of scaling it down.
        if source.mode.startswith("I"):
            raise ValueError("16-bit greyscale is read by OpenCV")
        source.load()
        turn = _UPRIGHT.get(_exif_orientation(source))
        rgb = source if source.mode == "RGB" else source.convert("RGB")
        if turn:
            rgb = rgb.transpose(getattr(Image.Transpose, turn))
        return cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)


def read_photo_bgr(path: Path, cv2, np):
    """The photo as the upright BGR array face detection reads.

    JPEG and PNG go to Pillow first. The libraries inside OpenCV print their
    warnings straight to the console, where a harmless quirk in a file reads as
    a program fault; Pillow reads the same pixels without printing them.
    """
    if path.suffix.lower() in _PILLOW_FIRST:
        try:
            return _read_upright_with_pillow(path, cv2, np)
        except Exception:
            pass
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is not None:
        return image
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    with Image.open(path) as source:
        return cv2.cvtColor(np.array(source.convert("RGB")), cv2.COLOR_RGB2BGR)


def _unit(values) -> list[float]:
    vector = [float(value) for value in values]
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length else vector


def cosine(left, right) -> float:
    return sum(a * b for a, b in zip(_unit(left), _unit(right)))


def is_available() -> bool:
    """Whether the optional face-detection packages are importable, without
    loading the (large) model itself."""
    try:
        import cv2  # noqa: F401
        import insightface  # noqa: F401
    except ImportError:
        return False
    return True


def face_gpu_switch_available() -> bool:
    """True when face detection is installed without graphics-card support on a
    computer whose NVIDIA card and driver could give it. onnxruntime-gpu's
    PyPI build needs CUDA 13, hence the driver check.

    The processor package counts against it even beside the graphics-card one:
    both write into the same folder, so installing it replaces the card's files."""
    return bool(
        cuda_wheel_tag() == "cu130" and distribution_version("insightface")
        and (not distribution_version("onnxruntime-gpu") or distribution_version("onnxruntime"))
    )


def face_device_status() -> dict[str, str]:
    """What face detection will run on, without loading its model. Empty when
    face detection is not installed."""
    if not is_available():
        return {}
    cuda_name = ""
    if distribution_version("onnxruntime-gpu"):
        try:
            import onnxruntime
            if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
                cuda_name = nvidia_gpu_name()
        except Exception:
            pass
    return describe_device(cuda_name, nvidia_gpu_name(), "face-detection runtime",
                           cuda_wheel_tag() == "cu130")


def switch_faces_to_gpu(packages: list[str]) -> tuple[bool, str]:
    """Replace the processor-only onnxruntime with the graphics-card one. If the
    replacement cannot be installed, the processor one is put back so face
    detection keeps working. Must run while onnxruntime is not loaded."""
    requirement = onnx_gpu_requirement(packages, "+cu130" not in installed_torch_version())
    cpu_requirement = next((p for p in packages if p.lower().startswith("onnxruntime")), "onnxruntime")
    # Both go: removing one deletes files they share, and pip would leave an
    # already installed graphics-card package as it is.
    run_pip(["uninstall", "-y", "onnxruntime", "onnxruntime-gpu"], timeout=600)
    ok, tail = run_pip(["install", requirement], timeout=3600, stream=True)
    if not ok:
        run_pip(["uninstall", "-y", "onnxruntime-gpu"], timeout=600)
        run_pip(["install", cpu_requirement], timeout=1800)
    return ok, tail


def _gpu_providers() -> tuple[list[str], int]:
    """Execution providers and device number to prepare the face models with:
    the graphics card when onnxruntime has it, else the processor."""
    try:
        import onnxruntime
    except ImportError:
        return ["CPUExecutionProvider"], -1
    if "CUDAExecutionProvider" not in onnxruntime.get_available_providers():
        return ["CPUExecutionProvider"], -1
    try:
        # Finds CUDA and cuDNN in PyTorch's folder or the NVIDIA pip packages.
        onnxruntime.preload_dlls()
    except Exception:
        pass
    return ["CUDAExecutionProvider", "CPUExecutionProvider"], 0


def _insightface_model_root() -> Path:
    return Path(os.environ.get("INSIGHTFACE_HOME", Path.home() / ".insightface")) / "models"


def model_cache_info(model_name: str = "buffalo_l") -> dict:
    """Return download status and file size for the face detection model."""
    model_dir = _insightface_model_root() / model_name
    total_bytes = 0
    found = False
    if model_dir.is_dir():
        for f in model_dir.iterdir():
            if f.suffix == ".onnx":
                found = True
                total_bytes += f.stat().st_size
    size_mb = round(total_bytes / (1024 * 1024), 1) if found else 0
    return {
        "model": model_name,
        "downloaded": found,
        "size_mb": size_mb,
        "path": str(model_dir) if found else "",
    }


def _patch_face_align():
    """Replace deprecated SimilarityTransform.estimate() call in insightface
    with the newer from_estimate() class constructor so scikit-image >= 0.26
    doesn't warn and >= 2.2 doesn't break."""
    from insightface.utils import face_align
    from skimage import transform as trans
    import numpy as np

    _original = face_align.estimate_norm

    def _patched_estimate_norm(lmk, image_size=112, mode='arcface'):
        assert lmk.shape == (5, 2)
        assert image_size % 112 == 0 or image_size % 128 == 0
        if image_size % 112 == 0:
            ratio = float(image_size) / 112.0
            diff_x = 0
        else:
            ratio = float(image_size) / 128.0
            diff_x = 8.0 * ratio
        dst = face_align.arcface_dst * ratio
        dst[:, 0] += diff_x
        if hasattr(trans.SimilarityTransform, 'from_estimate'):
            tform = trans.SimilarityTransform.from_estimate(lmk, dst)
        else:
            tform = trans.SimilarityTransform()
            tform.estimate(lmk, dst)
        return tform.params[0:2, :]

    face_align.estimate_norm = _patched_estimate_norm


def load_insightface_runtime(model_name: str, model_root: Path | None):
    try:
        import cv2
        import numpy as np
        from insightface.app import FaceAnalysis
    except ImportError as exc:
        raise RuntimeError(
            "Face location recovery is optional. Install the [face-locations] packages from requirements.txt "
            "with this Python interpreter first."
        ) from exc
    _patch_face_align()
    # OpenCV's own notices, such as an unrecognised tag in a TIFF.
    with contextlib.suppress(AttributeError):
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    providers, device_id = _gpu_providers()
    options = {
        "name": model_name, "providers": providers,
        # buffalo_l's full pack also includes gender/age and 3D/2D landmark
        # models. Neither face_scan.py nor this module reads anything but
        # .bbox and .normed_embedding (detection + recognition) -- on a
        # photo with many faces, running the unused models for every face
        # found dominates runtime for no reason (verified: an 19-face photo
        # dropped from ~13s to ~1.5s with them excluded, same bbox/embedding
        # output since neither of those two models changes).
        "allowed_modules": ["detection", "recognition"],
    }
    if model_root:
        options["root"] = str(model_root)
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
        analyzer = FaceAnalysis(**options)
        analyzer.prepare(ctx_id=device_id, det_size=(640, 640))
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
    cv2, np, analyzer = load_insightface_runtime(model_name, model_root)
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
            image = read_photo_bgr(library_root / Path(asset["relative_path"]), cv2, np)
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
