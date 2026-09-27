import unittest
from pathlib import Path

import photo_search

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"


class RequirementsTests(unittest.TestCase):
    def test_first_launch_install_leaves_the_optional_features_out(self):
        installed = [
            line.strip() for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        names = {line.split(">")[0].split("<")[0].split("=")[0].lower() for line in installed}
        self.assertEqual(names, {"pillow", "pillow-heif"})

    def test_each_optional_feature_lists_its_packages(self):
        self.assertEqual(
            photo_search.optional_requirements("meaning-search"),
            ["open_clip_torch>=3.3,<4", "torch>=2.13,<3"],
        )
        self.assertEqual(
            photo_search.optional_requirements("face-locations"),
            ["insightface>=1.0.1,<2", "onnxruntime>=1.28,<2"],
        )

    def test_an_unknown_feature_is_an_error_not_an_empty_install(self):
        with self.assertRaises(ValueError):
            photo_search.optional_requirements("no-such-feature")


if __name__ == "__main__":
    unittest.main()
