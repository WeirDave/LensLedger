from __future__ import annotations

import array
import math
import random
import unittest
from unittest.mock import patch

import face_math
from face_math import EmbeddingSet, best_two_matches, modal_dimension


def blob(*values: float) -> bytes:
    return array.array("f", values).tobytes()


def unit(angle_degrees: float) -> tuple[float, float]:
    radians = math.radians(angle_degrees)
    return (math.cos(radians), math.sin(radians))


def without_numpy():
    return patch.object(face_math, "np", None)


class BackendCase(unittest.TestCase):
    """Runs each test body on NumPy (when installed) and on the fallback."""

    def backends(self):
        if face_math.np is not None:
            yield "numpy", patch.object(face_math, "np", face_math.np)
        yield "pure python", without_numpy()


class EmbeddingSetTests(BackendCase):
    def test_scores_pair_with_the_rows_that_decoded(self):
        blobs = [blob(*unit(0)), None, blob(1.0, 2.0, 3.0), blob(0.0, 0.0), blob(*unit(60))]
        for label, backend in self.backends():
            with self.subTest(label), backend:
                embeddings = EmbeddingSet(blobs, 2)
                self.assertEqual(embeddings.indexes, [0, 4])
                scores = embeddings.scores(unit(0))
                self.assertAlmostEqual(scores[0], 1.0, places=5)
                self.assertAlmostEqual(scores[1], 0.5, places=5)

    def test_non_finite_and_all_zero_rows_are_dropped(self):
        blobs = [blob(float("nan"), 1.0), blob(float("inf"), 0.0), blob(0.0, 0.0), blob(3.0, 4.0)]
        for label, backend in self.backends():
            with self.subTest(label), backend:
                self.assertEqual(EmbeddingSet(blobs, 2).indexes, [3])

    def test_query_of_the_wrong_size_is_refused_instead_of_truncated(self):
        for label, backend in self.backends():
            with self.subTest(label), backend:
                with self.assertRaises(ValueError):
                    EmbeddingSet([blob(1.0, 0.0)], 2).scores((1.0, 0.0, 0.0))

    def test_neighbor_counts_include_the_row_itself(self):
        blobs = [blob(*unit(angle)) for angle in (0, 5, -5, 90)]
        for label, backend in self.backends():
            with self.subTest(label), backend:
                embeddings = EmbeddingSet(blobs, 2)
                self.assertEqual(embeddings.neighbor_counts([unit(0), unit(90)], 0.72), [3, 1])
                self.assertEqual(EmbeddingSet([], 2).neighbor_counts([unit(0)], 0.72), [0])

    def test_mean_direction_points_between_the_rows(self):
        blobs = [blob(*unit(-20)), blob(*unit(20))]
        for label, backend in self.backends():
            with self.subTest(label), backend:
                direction = EmbeddingSet(blobs, 2).mean_direction()
                self.assertAlmostEqual(direction[0], 1.0, places=5)
                self.assertAlmostEqual(direction[1], 0.0, places=5)
                self.assertEqual(EmbeddingSet([], 2).mean_direction(), ())

    def test_modal_dimension_ignores_ragged_and_empty_blobs(self):
        self.assertEqual(modal_dimension([blob(1.0, 0.0), blob(0.0, 1.0), blob(1.0, 2.0, 3.0), b"", None, b"abc"]), 2)
        self.assertEqual(modal_dimension([None, b""]), 0)


@unittest.skipIf(face_math.np is None, "NumPy is not installed")
class DensestGroupTests(unittest.TestCase):
    def test_groups_come_out_biggest_first_with_their_sizes(self):
        angles = [200] + [90, 94] + [0, 4, -4, 2, -2]
        embeddings = EmbeddingSet([blob(*unit(angle)) for angle in angles], 2)
        groups = embeddings.densest_groups(0.72, 10)
        self.assertEqual([size for _, size in groups], [5, 2, 1])
        heart_angles = [angles[embeddings.indexes[heart]] for heart, _ in groups]
        self.assertTrue(-5 <= heart_angles[0] <= 5)
        self.assertIn(heart_angles[1], (90, 94))
        self.assertEqual(heart_angles[2], 200)

    def test_each_row_is_claimed_by_one_group_only(self):
        # A chain: 0 and 40 are not alike, but both resemble 20.
        embeddings = EmbeddingSet([blob(*unit(angle)) for angle in (0, 20, 40)], 2)
        groups = embeddings.densest_groups(0.85, 10)
        self.assertEqual(sum(size for _, size in groups), 3)

    def test_limit_caps_the_number_of_groups(self):
        embeddings = EmbeddingSet([blob(*unit(angle)) for angle in (0, 90, 180, 270)], 2)
        self.assertEqual(len(embeddings.densest_groups(0.72, 2)), 2)
        self.assertEqual(embeddings.densest_groups(0.72, 0), [])

    def test_without_numpy_it_says_so(self):
        embeddings = EmbeddingSet([blob(1.0, 0.0)], 2)
        with without_numpy():
            with self.assertRaises(RuntimeError):
                embeddings.densest_groups(0.72, 1)


@unittest.skipIf(face_math.np is None, "NumPy is not installed")
class RankedGroupTests(unittest.TestCase):
    def library(self):
        """Three people of 40, 15 and 5 faces, and a small sample that sees them as 1, 3 and 2."""
        everyone_angles = [i * 0.1 for i in range(40)] + [90 + i * 0.1 for i in range(15)] + [180 + i * 0.1 for i in range(5)]
        pool_angles = [0.0] + [90.0, 90.1, 90.2] + [180.0, 180.1]
        everyone = EmbeddingSet([blob(*unit(a)) for a in everyone_angles], 2)
        pool = EmbeddingSet([blob(*unit(a)) for a in pool_angles], 2)
        return pool, everyone

    def test_a_small_sample_does_not_decide_the_order(self):
        pool, everyone = self.library()
        sample_order = [size for _, size in pool.densest_groups(0.72, 3)]
        self.assertEqual(sample_order, [3, 2, 1], "the sample alone ranks the biggest person last")
        ranked = face_math.ranked_groups(pool, everyone, 0.72, 3)
        self.assertEqual([similar for _, similar in ranked], [39, 14, 4])

    def test_the_real_count_leaves_out_the_face_itself(self):
        pool, everyone = self.library()
        heart, similar = face_math.ranked_groups(pool, everyone, 0.72, 1)[0]
        self.assertEqual(similar, 39)
        self.assertAlmostEqual(pool.vector(heart)[0], 1.0, places=3)

    def test_limit_is_respected_and_a_group_beyond_it_is_dropped(self):
        pool, everyone = self.library()
        ranked = face_math.ranked_groups(pool, everyone, 0.72, 2)
        self.assertEqual([similar for _, similar in ranked], [39, 14])
        self.assertEqual(face_math.ranked_groups(pool, everyone, 0.72, 0), [])

    def test_counts_never_increase_down_the_list(self):
        import random
        generator = random.Random(3)
        angles = [generator.choice([0, 40, 80, 120, 160, 200, 240, 280, 320]) + generator.uniform(-2, 2) for _ in range(400)]
        everyone = EmbeddingSet([blob(*unit(a)) for a in angles], 2)
        pool = EmbeddingSet([blob(*unit(a)) for a in angles[::13]], 2)
        counts = [similar for _, similar in face_math.ranked_groups(pool, everyone, 0.9, 9)]
        self.assertEqual(counts, sorted(counts, reverse=True))


class BestTwoMatchesTests(BackendCase):
    def test_both_backends_agree_with_a_plain_ranking(self):
        generator = random.Random(7)

        def random_unit(size):
            values = [generator.gauss(0, 1) for _ in range(size)]
            norm = math.sqrt(sum(v * v for v in values))
            return tuple(v / norm for v in values)

        vectors = [random_unit(16) for _ in range(40)]
        profiles = [random_unit(16) for _ in range(6)]
        expected = []
        for vector in vectors:
            ranked = sorted(
                ((sum(a * b for a, b in zip(vector, p)), i) for i, p in enumerate(profiles)),
                key=lambda item: item[0], reverse=True,
            )
            expected.append((ranked[0][1], ranked[0][0], ranked[1][0]))
        for label, backend in self.backends():
            with self.subTest(label), backend:
                actual = best_two_matches(vectors, profiles)
                self.assertEqual([m[0] for m in actual], [m[0] for m in expected])
                for got, want in zip(actual, expected):
                    self.assertAlmostEqual(got[1], want[1], places=9)
                    self.assertAlmostEqual(got[2], want[2], places=9)

    def test_a_single_profile_has_no_runner_up(self):
        for label, backend in self.backends():
            with self.subTest(label), backend:
                self.assertEqual(best_two_matches([(1.0, 0.0)], [(1.0, 0.0)]), [(0, 1.0, -1.0)])

    def test_nothing_to_rank_returns_nothing(self):
        self.assertEqual(best_two_matches([], [(1.0, 0.0)]), [])
        self.assertEqual(best_two_matches([(1.0, 0.0)], []), [])


if __name__ == "__main__":
    unittest.main()
