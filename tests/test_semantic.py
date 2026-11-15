from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image


class FakeEncoder:
    identity = "ViT-B-32/openai"

    def encode_images(self, paths):
        vectors = {
            "beach.jpg": (1.0, 0.0, 0.0),
            "document.jpg": (0.0, 1.0, 0.0),
            "dog.jpg": (0.0, 0.0, 1.0),
        }
        return [vectors.get(path.name) for path in paths]

    def encode_text(self, text):
        return {
            "ocean vacation": (0.9, 0.1, 0.0),
            "paper receipt": (0.0, 1.0, 0.0),
        }[text]


class FakeEncoderWithErrors(FakeEncoder):
    def encode_images(self, paths):
        vectors = {
            "beach.jpg": (1.0, 0.0, 0.0),
            "document.jpg": (0.0, 1.0, 0.0),
            "dog.jpg": "cannot identify image file 'dog.jpg'",
        }
        return [vectors.get(path.name) for path in paths]


class SemanticIndexTests(unittest.TestCase):
    def test_optional_index_builds_incrementally_and_ranks_text_queries(self):
        from photo_index import scan_library
        from semantic_index import build_index, search, status

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)

            progress = []
            built = build_index(
                database, FakeEncoder(), batch_size=2, progress=progress.append
            )
            self.assertEqual(built["indexed"], 3)
            self.assertEqual(built["errors"], 0)
            self.assertEqual(status(database)["remaining"], 0)
            self.assertEqual(build_index(database, FakeEncoder())["indexed"], 0)

            ocean = search(database, "ocean vacation", encoder=FakeEncoder())
            receipt = search(database, "paper receipt", encoder=FakeEncoder())
            con = sqlite3.connect(database)
            ids = dict(con.execute("SELECT filename, id FROM assets"))
            con.close()
            self.assertEqual(ocean[0][0], ids["beach.jpg"])
            self.assertEqual(receipt[0][0], ids["document.jpg"])
            self.assertGreater(ocean[0][1], ocean[1][1])


    def test_numpy_and_python_paths_return_the_same_ranking(self):
        import semantic_index
        from photo_index import scan_library
        from semantic_index import build_index, clear_matrix_cache, search

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)
            build_index(database, FakeEncoder())

            with_numpy = search(database, "ocean vacation", encoder=FakeEncoder())
            original = semantic_index.np
            try:
                semantic_index.np = None
                clear_matrix_cache(database)
                without_numpy = search(database, "ocean vacation", encoder=FakeEncoder())
            finally:
                semantic_index.np = original
                clear_matrix_cache(database)

            self.assertEqual(
                [asset_id for asset_id, _ in with_numpy],
                [asset_id for asset_id, _ in without_numpy],
            )
            for (_, numpy_score), (_, python_score) in zip(with_numpy, without_numpy):
                self.assertAlmostEqual(numpy_score, python_score, places=5)

    def test_cached_matrix_reloads_after_the_table_changes(self):
        from photo_index import scan_library
        from semantic_index import build_index, search

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)
            build_index(database, FakeEncoder())

            # Populate the in-memory cache, then change the table out from under it.
            self.assertEqual(len(search(database, "ocean vacation", encoder=FakeEncoder())), 3)
            con = sqlite3.connect(database)
            con.execute(
                "DELETE FROM semantic_embeddings WHERE asset_id IN "
                "(SELECT id FROM assets WHERE filename='dog.jpg')"
            )
            con.commit()
            con.close()
            self.assertEqual(len(search(database, "ocean vacation", encoder=FakeEncoder())), 2)

    def test_encode_errors_store_actual_reason(self):
        from photo_index import connect, scan_library
        from semantic_index import build_index

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "photos"
            library.mkdir()
            for name, color in (
                ("beach.jpg", "blue"), ("document.jpg", "white"), ("dog.jpg", "brown")
            ):
                Image.new("RGB", (12, 8), color).save(library / name)
            database = root / "library.sqlite3"
            self.assertEqual(scan_library(library, database), 0)

            built = build_index(database, FakeEncoderWithErrors())
            self.assertEqual(built["indexed"], 2)
            self.assertEqual(built["errors"], 1)
            with connect(database) as con:
                row = con.execute(
                    "SELECT semantic_error FROM assets WHERE relative_path LIKE '%dog.jpg'"
                ).fetchone()
            self.assertIn("cannot identify image file", row["semantic_error"])


class FaceAppearanceEncoder:
    """Whole photos are told apart by file name and face crops by brightness:
    a dark crop reads as 'beard', a light one as 'clean-shaven'."""

    identity = "ViT-B-32/openai"

    def __init__(self):
        self.crop_sizes = []

    def encode_images(self, paths):
        vectors = {"dark.jpg": (0.0, 0.0, 1.0), "light.jpg": (0.3, 0.0, 0.954), "group.jpg": (0.0, 0.0, 1.0)}
        return [vectors.get(path.name) for path in paths]

    def encode_crops(self, crops):
        result = []
        for crop in crops:
            self.crop_sizes.append(crop.size)
            brightness = sum(crop.resize((1, 1)).getpixel((0, 0))) / 3
            result.append((1.0, 0.0, 0.0) if brightness < 128 else (0.0, 1.0, 0.0))
        return result

    def encode_text(self, text):
        return {"beard": (1.0, 0.0, 0.0), "clean shaven": (0.0, 1.0, 0.0)}[text]


class FaceAppearanceTests(unittest.TestCase):
    def build_library(self, root):
        from photo_index import connect, scan_library

        library = root / "photos"
        library.mkdir()
        Image.new("RGB", (200, 200), (20, 20, 20)).save(library / "dark.jpg")
        Image.new("RGB", (200, 200), (230, 230, 230)).save(library / "light.jpg")
        Image.new("RGB", (200, 100), (20, 20, 20)).save(library / "group.jpg")
        database = root / "library.sqlite3"
        self.assertEqual(scan_library(library, database), 0)
        faces = {}
        with connect(database) as con:
            person = con.execute("INSERT INTO people(name) VALUES ('Sample Person')").lastrowid
            other = con.execute("INSERT INTO people(name) VALUES ('Other Person')").lastrowid
            ids = dict(con.execute("SELECT filename, id FROM assets"))
            for number, (name, who, box) in enumerate((
                ("dark.jpg", person, (0.35, 0.35, 0.65, 0.65)),
                ("light.jpg", person, (0.35, 0.35, 0.65, 0.65)),
                ("group.jpg", other, (0.1, 0.1, 0.3, 0.5)),
            ), start=1):
                face_id = con.execute(
                    """INSERT INTO face_embeddings(source,source_face_id,asset_id,relative_path,
                           dimensions,embedding_f32,box_left,box_top,box_right,box_bottom)
                       VALUES ('test',?,?,?,1,x'00000000',?,?,?,?)""",
                    (number, ids[name], name, *box),
                ).lastrowid
                con.execute(
                    """INSERT INTO asset_people(asset_id,person_id,state,face_id,source,updated_at)
                       VALUES (?,?,'confirmed',?,'manual','2024-01-01')""",
                    (ids[name], who, face_id),
                )
                faces[name] = face_id
        return database, person, other, ids, faces

    def test_face_crop_is_a_square_inside_the_photo(self):
        from semantic_index import face_crop

        image = Image.new("RGB", (400, 200), "white")
        crop = face_crop(image, 0.0, 0.0, 0.1, 0.2)
        self.assertEqual(crop.size[0], crop.size[1])
        self.assertLessEqual(crop.size[0], 200)
        self.assertIsNone(face_crop(image, 0.5, 0.5, 0.5, 0.5))

    def test_face_index_is_incremental_and_skips_ignored_faces(self):
        from photo_index import connect
        from semantic_index import build_face_index, face_crop_pending

        with tempfile.TemporaryDirectory() as temporary:
            database, _person, _other, _ids, faces = self.build_library(Path(temporary))
            with connect(database) as con:
                con.execute("UPDATE face_embeddings SET ignored_at='2024-01-01' WHERE id=?", (faces["group.jpg"],))
            self.assertEqual(face_crop_pending(database), 2)
            encoder = FaceAppearanceEncoder()
            built = build_face_index(database, encoder, batch_size=1)
            self.assertEqual((built["indexed"], built["errors"]), (2, 0))
            self.assertEqual(face_crop_pending(database), 0)
            self.assertEqual(build_face_index(database, encoder)["indexed"], 0)
            self.assertTrue(all(width == height for width, height in encoder.crop_sizes))

    def test_a_person_with_a_beard_ranks_above_a_better_whole_photo_match(self):
        from semantic_index import blend_person_faces, build_face_index, build_index, search

        with tempfile.TemporaryDirectory() as temporary:
            database, person, _other, ids, _faces = self.build_library(Path(temporary))
            encoder = FaceAppearanceEncoder()
            build_index(database, encoder)
            build_face_index(database, encoder)

            whole = [(asset_id, score) for asset_id, score in search(database, "beard", encoder=encoder)
                     if asset_id in (ids["dark.jpg"], ids["light.jpg"])]
            self.assertEqual(whole[0][0], ids["light.jpg"])
            blended = blend_person_faces(database, "beard", whole, [person], encoder=encoder)
            self.assertEqual(blended[0][0], ids["dark.jpg"])
            self.assertAlmostEqual(blended[0][1], 1.0, places=5)

    def test_other_peoples_faces_and_missing_crops_do_not_change_the_score(self):
        from semantic_index import blend_person_faces, build_face_index, build_index, search

        with tempfile.TemporaryDirectory() as temporary:
            database, person, other, ids, _faces = self.build_library(Path(temporary))
            encoder = FaceAppearanceEncoder()
            build_index(database, encoder)
            scored = search(database, "beard", encoder=encoder)
            self.assertEqual(blend_person_faces(database, "beard", scored, [person], encoder=encoder), scored)
            build_face_index(database, encoder)
            blended = dict(blend_person_faces(database, "beard", scored, [person], encoder=encoder))
            original = dict(scored)
            self.assertEqual(blended[ids["group.jpg"]], original[ids["group.jpg"]])
            self.assertEqual(blend_person_faces(database, "beard", scored, [], encoder=encoder), scored)
            self.assertGreater(
                dict(blend_person_faces(database, "beard", scored, [other], encoder=encoder))[ids["group.jpg"]],
                original[ids["group.jpg"]],
            )


class GraphicsCardSetupTests(unittest.TestCase):
    def test_the_cuda_version_is_read_from_both_driver_header_styles(self):
        from semantic_index import parse_cuda_version

        older = "| NVIDIA-SMI 552.22    Driver Version: 552.22    CUDA Version: 12.4     |"
        newer = "| NVIDIA-SMI 621.24    KMD Version: 621.24    CUDA UMD Version: 13.5     |"
        self.assertEqual(parse_cuda_version(older), (12, 4))
        self.assertEqual(parse_cuda_version(newer), (13, 5))
        self.assertIsNone(parse_cuda_version("NVIDIA-SMI has failed"))

    def test_the_pytorch_build_follows_what_the_driver_supports(self):
        from semantic_index import wheel_tag_for_driver

        self.assertEqual(wheel_tag_for_driver((13, 2)), "cu130")
        self.assertEqual(wheel_tag_for_driver((13, 0)), "cu130")
        self.assertEqual(wheel_tag_for_driver((12, 9)), "cu126")
        self.assertEqual(wheel_tag_for_driver((12, 6)), "cu126")
        self.assertEqual(wheel_tag_for_driver((12, 5)), "", "a driver too old for any build gets none")
        self.assertEqual(wheel_tag_for_driver(None), "")

    def test_only_a_build_without_card_support_is_swapped(self):
        from semantic_index import torch_lacks_gpu_support

        self.assertTrue(torch_lacks_gpu_support("2.13.0+cpu", "cu126"))
        self.assertTrue(torch_lacks_gpu_support("2.13.0", "cu126"))
        self.assertFalse(torch_lacks_gpu_support("2.14.1+cu126", "cu126"))
        self.assertFalse(torch_lacks_gpu_support("2.13.0+cpu", ""), "no usable card: nothing to switch to")
        self.assertFalse(torch_lacks_gpu_support("", "cu126"), "PyTorch is not installed: setup handles that")

    def test_the_card_build_comes_only_from_pytorchs_own_site(self):
        from semantic_index import cuda_torch_pip_args

        args = cuda_torch_pip_args("torch>=2.13,<3", "cu126")
        self.assertEqual(args[:3], ["install", "--upgrade", "torch>=2.13,<3"])
        self.assertEqual(args[3:], ["--index-url", "https://download.pytorch.org/whl/cu126"])
        self.assertNotIn("--extra-index-url", args)

    def test_a_queued_switch_is_carried_out_once_at_start(self):
        import os
        from unittest.mock import patch
        import photo_search

        with tempfile.TemporaryDirectory() as temporary:
            previous = os.environ.get("LENSLEDGER_DATA_DIR")
            os.environ["LENSLEDGER_DATA_DIR"] = temporary
            try:
                marker = Path(temporary) / photo_search.SEMANTIC_GPU_SWITCH_MARKER
                with patch.object(photo_search, "semantic_run_pip", return_value=(True, "")) as run, \
                        patch.object(photo_search, "console_log"), \
                        patch.object(photo_search, "face_gpu_switch_available", return_value=False), \
                        patch.object(photo_search, "semantic_cuda_wheel_tag", return_value="cu126"):
                    photo_search.apply_pending_gpu_switch()
                    run.assert_not_called()
                    marker.write_text("1")
                    photo_search.apply_pending_gpu_switch()
                    self.assertFalse(marker.exists())
                    run.assert_called_once()
                    self.assertIn("https://download.pytorch.org/whl/cu126", run.call_args[0][0])
                    photo_search.apply_pending_gpu_switch()
                    run.assert_called_once()
            finally:
                if previous is None:
                    os.environ.pop("LENSLEDGER_DATA_DIR", None)
                else:
                    os.environ["LENSLEDGER_DATA_DIR"] = previous

    def test_a_queued_face_switch_leaves_pytorch_alone(self):
        import json
        import os
        from unittest.mock import patch
        import photo_search

        with tempfile.TemporaryDirectory() as temporary:
            previous = os.environ.get("LENSLEDGER_DATA_DIR")
            os.environ["LENSLEDGER_DATA_DIR"] = temporary
            try:
                marker = Path(temporary) / photo_search.SEMANTIC_GPU_SWITCH_MARKER
                marker.write_text(json.dumps({"parts": ["faces"]}))
                with patch.object(photo_search, "semantic_run_pip", return_value=(True, "")) as torch_pip, \
                        patch.object(photo_search, "switch_faces_to_gpu", return_value=(True, "")) as faces, \
                        patch.object(photo_search, "console_log"), \
                        patch.object(photo_search, "face_gpu_switch_available", return_value=True), \
                        patch.object(photo_search, "semantic_cuda_wheel_tag", return_value="cu130"):
                    photo_search.apply_pending_gpu_switch()
                torch_pip.assert_not_called()
                faces.assert_called_once()
                self.assertFalse(marker.exists())
            finally:
                if previous is None:
                    os.environ.pop("LENSLEDGER_DATA_DIR", None)
                else:
                    os.environ["LENSLEDGER_DATA_DIR"] = previous

    def test_setup_requirement_line_is_taken_from_the_package_list(self):
        import photo_search

        self.assertEqual(photo_search.torch_requirement(["open_clip_torch>=3.3,<4", "torch>=2.13,<3"]),
                         "torch>=2.13,<3")
        self.assertEqual(photo_search.torch_requirement(["open_clip_torch>=3.3,<4"]), "torch")


class FaceGraphicsCardTests(unittest.TestCase):
    PACKAGES = ["insightface>=1.0.1,<2", "onnxruntime>=1.28,<2"]

    def test_the_graphics_card_runtime_keeps_the_version_range(self):
        from semantic_index import onnx_gpu_requirement

        self.assertEqual(onnx_gpu_requirement(self.PACKAGES, False), "onnxruntime-gpu>=1.28,<2")
        self.assertEqual(onnx_gpu_requirement(self.PACKAGES, True), "onnxruntime-gpu[cuda,cudnn]>=1.28,<2",
                         "without PyTorch's CUDA libraries to share, the libraries come with it")

    def test_the_cuda_libraries_are_shared_with_a_cuda_pytorch(self):
        import face_locations
        from unittest.mock import patch

        for torch_version, expected in (("2.14.1+cu130", "onnxruntime-gpu>=1.28,<2"),
                                        ("2.13.0+cpu", "onnxruntime-gpu[cuda,cudnn]>=1.28,<2"),
                                        ("", "onnxruntime-gpu[cuda,cudnn]>=1.28,<2")):
            with self.subTest(torch=torch_version), \
                    patch.object(face_locations, "installed_torch_version", return_value=torch_version), \
                    patch.object(face_locations, "run_pip", return_value=(True, "")) as pip:
                face_locations.switch_faces_to_gpu(self.PACKAGES)
                self.assertEqual(pip.call_args_list[0][0][0], ["uninstall", "-y", "onnxruntime"])
                self.assertEqual(pip.call_args_list[1][0][0], ["install", expected])

    def test_a_failed_switch_puts_the_processor_runtime_back(self):
        import face_locations
        from unittest.mock import patch

        calls = []

        def pip(args, timeout, stream=False):
            calls.append(args)
            return (args[0] != "install" or "onnxruntime-gpu" not in args[1], "download failed")

        with patch.object(face_locations, "installed_torch_version", return_value="2.14.1+cu130"), \
                patch.object(face_locations, "run_pip", side_effect=pip):
            ok, _tail = face_locations.switch_faces_to_gpu(self.PACKAGES)
        self.assertFalse(ok)
        self.assertEqual(calls[-2:], [["uninstall", "-y", "onnxruntime-gpu"], ["install", "onnxruntime>=1.28,<2"]])

    def test_an_old_driver_is_told_to_update_instead_of_to_press_a_button(self):
        from semantic_index import describe_device

        report = describe_device("", "Example GPU 1000", "face-detection runtime", fixable=False)
        self.assertEqual(report["device"], "cpu")
        self.assertIn("driver", report["summary"])
        self.assertNotIn("button", report["summary"])

    def test_face_detection_uses_the_card_when_onnxruntime_has_it(self):
        import face_locations
        import sys
        import types
        from unittest.mock import patch

        fake = types.SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
                                     preload_dlls=lambda: None)
        with patch.dict(sys.modules, {"onnxruntime": fake}):
            self.assertEqual(face_locations._gpu_providers(), (["CUDAExecutionProvider", "CPUExecutionProvider"], 0))
        cpu_only = types.SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"])
        with patch.dict(sys.modules, {"onnxruntime": cpu_only}):
            self.assertEqual(face_locations._gpu_providers(), (["CPUExecutionProvider"], -1))


class DeviceReportTests(unittest.TestCase):
    def test_a_usable_graphics_card_is_named(self):
        from semantic_index import describe_device

        report = describe_device("Example GPU 1000", "Example GPU 1000")
        self.assertEqual(report["device"], "cuda")
        self.assertIn("Example GPU 1000", report["summary"])

    def test_an_unused_nvidia_card_is_called_out_with_the_fix(self):
        from semantic_index import describe_device

        report = describe_device("", "Example GPU 1000")
        self.assertEqual(report["device"], "cpu")
        self.assertIn("Example GPU 1000", report["summary"])
        self.assertIn("Use my graphics card", report["summary"])

    def test_a_computer_without_a_graphics_card_just_says_processor(self):
        from semantic_index import describe_device

        self.assertEqual(describe_device("", "")["summary"], "Using the processor.")

    def test_batches_grow_to_what_the_device_handles_but_never_shrink(self):
        from semantic_index import effective_batch_size

        class OnCard:
            preferred_batch_size = 64

        class OnProcessor:
            preferred_batch_size = 0

        self.assertEqual(effective_batch_size(OnCard(), 16), 64)
        self.assertEqual(effective_batch_size(OnCard(), 128), 128)
        self.assertEqual(effective_batch_size(OnProcessor(), 16), 16)
        self.assertEqual(effective_batch_size(FakeEncoder(), 16), 16)


if __name__ == "__main__":
    unittest.main()
