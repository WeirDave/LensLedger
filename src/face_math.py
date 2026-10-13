"""Bulk similarity maths for face embeddings.

NumPy is optional in LensLedger (it arrives with face detection), so every
helper here has a pure-Python fallback that returns the same answers, only
slower. Callers never need to check which one is running, except
densest_groups, which has no pure-Python equivalent.
"""

from __future__ import annotations

import array
import math
import sys
from collections import Counter
from typing import Sequence

try:
    import numpy as np
except ImportError:
    np = None


def numpy_available() -> bool:
    return np is not None


def modal_dimension(blobs: Sequence[bytes | None]) -> int:
    """The embedding size most blobs have, or 0 when none is usable."""
    counts = Counter(
        len(blob) // 4 for blob in blobs if blob and len(blob) % 4 == 0
    )
    return counts.most_common(1)[0][0] if counts else 0


def _decode_py(blob: bytes) -> tuple[float, ...]:
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    norm = math.sqrt(sum(value * value for value in values))
    if not norm or not math.isfinite(norm):
        return ()
    return tuple(value / norm for value in values)


class EmbeddingSet:
    """Unit-length embeddings of one size, searchable in bulk.

    `indexes` lists, for every row of the set, the position of its blob in the
    list that was passed in. Blobs that are missing, a different size, all
    zero or not finite are left out instead of being compared on a truncated
    prefix, so callers pair scores back to their rows through `indexes`.
    """

    def __init__(self, blobs: Sequence[bytes | None], dimensions: int):
        self.dimensions = dimensions
        size = dimensions * 4
        positions = [i for i, blob in enumerate(blobs) if blob and len(blob) == size]
        if np is not None:
            if positions:
                raw = np.frombuffer(
                    b"".join(blobs[i] for i in positions), dtype="<f4"
                ).reshape(len(positions), dimensions)
                norms = np.sqrt(np.einsum("ij,ij->i", raw, raw))
                usable = np.isfinite(norms) & (norms > 0)
                if usable.all():
                    self._matrix = raw / norms[:, None]
                    self.indexes = positions
                else:
                    self._matrix = raw[usable] / norms[usable, None]
                    self.indexes = [i for i, ok in zip(positions, usable) if ok]
            else:
                self._matrix = np.empty((0, dimensions), dtype=np.float32)
                self.indexes = []
        else:
            vectors = [(i, _decode_py(blobs[i])) for i in positions]
            self._matrix = [vector for _, vector in vectors if vector]
            self.indexes = [i for i, vector in vectors if vector]

    def __len__(self) -> int:
        return len(self.indexes)

    def vector(self, row: int) -> tuple[float, ...]:
        if np is not None:
            return tuple(self._matrix[row].tolist())
        return tuple(self._matrix[row])

    def scores(self, query: Sequence[float]) -> list[float]:
        """Cosine similarity of `query` (unit length) to every row."""
        if len(query) != self.dimensions:
            raise ValueError("query size does not match the embeddings")
        if np is not None:
            return (self._matrix @ np.asarray(query, dtype=np.float32)).tolist()
        return [sum(a * b for a, b in zip(query, row)) for row in self._matrix]

    def neighbor_counts(self, queries: Sequence[Sequence[float]], threshold: float) -> list[int]:
        """How many rows score at least `threshold` against each query."""
        if np is not None:
            if not len(self.indexes) or not len(queries):
                return [0] * len(queries)
            grid = np.asarray(queries, dtype=np.float32) @ self._matrix.T
            return (grid >= threshold).sum(axis=1).tolist()
        return [
            sum(score >= threshold for score in self.scores(query)) for query in queries
        ]

    def mean_direction(self) -> tuple[float, ...]:
        """The unit vector pointing at the centre of the set, or () if empty."""
        if not len(self.indexes):
            return ()
        if np is not None:
            total = self._matrix.sum(axis=0, dtype=np.float64)
            norm = float(np.linalg.norm(total))
            return tuple((total / norm).tolist()) if norm else ()
        totals = [0.0] * self.dimensions
        for row in self._matrix:
            for position, value in enumerate(row):
                totals[position] += value
        norm = math.sqrt(sum(value * value for value in totals))
        return tuple(value / norm for value in totals) if norm else ()

    def densest_groups(self, threshold: float, limit: int) -> list[tuple[int, int]]:
        """Greedy groups of rows that look alike, biggest first.

        Returns (row, size) pairs: the row at the heart of each group and how
        many still-unclaimed rows scored at least `threshold` against it,
        counting itself. Needs NumPy; the pairwise grid is far too slow
        without it.
        """
        if np is None:
            raise RuntimeError("densest_groups needs NumPy")
        count = len(self.indexes)
        if not count or limit <= 0:
            return []
        near = (self._matrix @ self._matrix.T) >= threshold
        np.fill_diagonal(near, True)
        unclaimed = np.ones(count, dtype=bool)
        counts = near.sum(axis=1)
        groups: list[tuple[int, int]] = []
        while len(groups) < limit and unclaimed.any():
            heart = int(np.where(unclaimed, counts, -1).argmax())
            members = near[heart] & unclaimed
            groups.append((heart, int(members.sum())))
            unclaimed &= ~members
            counts = counts - near[:, members].sum(axis=1)
        return groups


def best_two_matches(
    vectors: Sequence[Sequence[float]], profiles: Sequence[Sequence[float]],
) -> list[tuple[int, float, float]]:
    """For each vector: (best profile, its score, the runner-up's score).

    The runner-up score is -1.0 when there is only one profile. Vectors and
    profiles must all be the same length; callers fall back to their own loop
    otherwise.
    """
    if not vectors or not profiles:
        return []
    if np is not None:
        grid_profiles = np.asarray(profiles, dtype=np.float64)
        results: list[tuple[int, float, float]] = []
        for start in range(0, len(vectors), 4096):
            block = np.asarray(vectors[start:start + 4096], dtype=np.float64) @ grid_profiles.T
            rows = np.arange(block.shape[0])
            best = block.argmax(axis=1)
            best_scores = block[rows, best]
            if block.shape[1] > 1:
                block[rows, best] = -np.inf
                second = block.max(axis=1)
            else:
                second = np.full(block.shape[0], -1.0)
            results.extend(zip(best.tolist(), best_scores.tolist(), second.tolist()))
        return results
    results = []
    for vector in vectors:
        scored = sorted(
            ((sum(a * b for a, b in zip(vector, profile)), index) for index, profile in enumerate(profiles)),
            key=lambda item: item[0], reverse=True,
        )
        second = scored[1][0] if len(scored) > 1 else -1.0
        results.append((scored[0][1], scored[0][0], second))
    return results
