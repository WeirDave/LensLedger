#!/usr/bin/env python3
"""Measure how well LensLedger's face-similarity cutoffs fit this library.

LensLedger calls two faces "the same person" when their similarity is at
least 0.72 (0.75 and 0.90 for automatic confirmation). Those cutoffs are set
by hand. This reads the faces you have already named, measures how similar
one person's faces really are compared with different people's, and prints
what each possible cutoff would catch and what it would get wrong.

The catalogue is opened read-only and its contents are never changed. (The
catalogue uses SQLite's WAL mode, so SQLite itself may leave two empty
side files, ending in -wal and -shm, beside it; they are harmless and
LensLedger cleans them up.) The report holds only counts, percentages and
similarity values: no names, file paths, folder names, dates, face or photo
numbers, so it is safe to paste into an issue or a chat. Needs NumPy
(installed with face detection).
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError:
    np = None

from face_learning import (
    AUTO_CONFIRM_THRESHOLD, BULK_CONFIRM_SIMILARITY, FIND_MORE_AUTO_CONFIRM_SIMILARITY, REVIEW_SIMILARITY,
    SUGGESTION_THRESHOLD,
)
from face_math import EmbeddingSet, modal_dimension

GRID = [round(0.30 + 0.05 * step, 2) for step in range(12)]
CURRENT_CUTOFFS = tuple(sorted({
    REVIEW_SIMILARITY, SUGGESTION_THRESHOLD, BULK_CONFIRM_SIMILARITY,
    FIND_MORE_AUTO_CONFIRM_SIMILARITY, AUTO_CONFIRM_THRESHOLD,
}))
CUTOFFS = sorted({*GRID, *CURRENT_CUTOFFS})
PERCENTILES = (5, 25, 50, 75, 95)
MAX_FACES_PER_PERSON_FOR_PAIRS = 150
DIFFERENT_PERSON_PAIRS = 2_000_000
PAIR_CHUNK = 20_000
PERCENTILE_PAIRS_KEPT = 200_000
IMPOSTOR_SAMPLE = 3000
UNNAMED_SAMPLE = 2000
UNNAMED_POOL_LIMIT = 50_000


def default_catalogue() -> Path:
    """The catalogue LensLedger itself would open: the saved current library's."""
    from library_config import library_db_path, load_library_state
    return library_db_path(load_library_state())


def open_read_only(path: Path) -> sqlite3.Connection:
    text = str(path)
    if "<" in text and ">" in text:
        raise SystemExit(
            f"{text} is the placeholder from the instructions, not a real path. Leave --db off "
            "and the report finds the catalogue of your current library, or give the real file."
        )
    if not path.is_file():
        raise SystemExit(
            f"No catalogue at {path}. Leave --db off to use your current library's catalogue, "
            "or pass the file with --db (LensLedger's Settings page shows it as 'Database')."
        )
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def percentiles(values) -> dict[str, float] | None:
    if not len(values):
        return None
    return {f"p{p}": round(float(np.percentile(values, p)), 3) for p in PERCENTILES}


def share_at_least(values, cutoff: float) -> float | None:
    return round(float((values >= cutoff).mean()), 4) if len(values) else None


def load_named(con: sqlite3.Connection):
    """Confirmed faces tied to one exact face: (vectors, person index, order within person)."""
    rows = con.execute(
        """SELECT f.embedding_f32 AS blob, ap.person_id AS person, ap.updated_at AS named_at
           FROM asset_people ap
           JOIN face_embeddings f ON f.id = ap.face_id
           JOIN assets a ON a.id = ap.asset_id
           WHERE ap.state = 'confirmed' AND ap.face_id IS NOT NULL AND a.in_review_bin = 0
             AND f.ignored_at IS NULL AND f.unknown_at IS NULL
           ORDER BY ap.person_id, ap.updated_at, f.id"""
    ).fetchall()
    blobs = [row["blob"] for row in rows]
    embeddings = EmbeddingSet(blobs, modal_dimension(blobs))
    keep = embeddings.indexes
    people: dict[int, int] = {}
    owner = []
    rank = []
    seen: dict[int, int] = {}
    for index in keep:
        person = people.setdefault(int(rows[index]["person"]), len(people))
        owner.append(person)
        rank.append(seen.get(person, 0))
        seen[person] = rank[-1] + 1
    return embeddings.matrix, np.array(owner, dtype=np.int64), np.array(rank, dtype=np.int64)


def load_unnamed(con: sqlite3.Connection, dimensions: int):
    rows = con.execute(
        """SELECT f.embedding_f32 FROM face_embeddings f JOIN assets a ON a.id = f.asset_id
           WHERE f.ignored_at IS NULL AND f.unknown_at IS NULL AND f.skipped_at IS NULL
             AND f.person_id IS NULL AND a.in_review_bin = 0
             AND f.box_left IS NOT NULL AND f.box_top IS NOT NULL
             AND f.box_right IS NOT NULL AND f.box_bottom IS NOT NULL
           ORDER BY f.id DESC LIMIT ?""",
        (UNNAMED_POOL_LIMIT,),
    ).fetchall()
    return EmbeddingSet([row[0] for row in rows], dimensions).matrix


def person_sums(vectors, owner, people: int):
    sums = np.zeros((people, vectors.shape[1]), dtype=np.float64)
    np.add.at(sums, owner, vectors)
    return sums


def count_different_person_pairs(vectors, owner, generator, draws: int):
    """Count, per cutoff, how many random pairs of different people score at least that.

    Pairs are drawn and counted in chunks so millions can be checked without
    holding them in memory; only the first PERCENTILE_PAIRS_KEPT scores are kept,
    for the percentiles. Returns (counts per cutoff, pairs counted, kept scores).
    """
    counts = {cutoff: 0 for cutoff in CUTOFFS}
    kept: list = []
    kept_total = 0
    total = 0
    remaining = draws
    while remaining > 0:
        size = min(PAIR_CHUNK, remaining)
        remaining -= size
        left = generator.integers(0, len(owner), size=size)
        right = generator.integers(0, len(owner), size=size)
        apart = owner[left] != owner[right]
        scores = np.einsum("ij,ij->i", vectors[left[apart]], vectors[right[apart]])
        total += len(scores)
        for cutoff in CUTOFFS:
            counts[cutoff] += int(np.count_nonzero(scores >= cutoff))
        if kept_total < PERCENTILE_PAIRS_KEPT:
            kept.append(scores)
            kept_total += len(scores)
    return counts, total, (np.concatenate(kept)[:PERCENTILE_PAIRS_KEPT] if kept else np.empty(0))


def measure_named(vectors, owner, generator: np.random.Generator, rng: random.Random,
                  unnamed_pool: int = 0) -> dict:
    """Same-person and different-person similarity for one set of named faces."""
    people = int(owner.max()) + 1 if len(owner) else 0
    counts = np.bincount(owner, minlength=people)
    result: dict = {"faces": int(len(owner)), "people": int((counts > 0).sum()),
                    "people_with_two_or_more_faces": int((counts >= 2).sum())}
    if result["people_with_two_or_more_faces"] < 2:
        result["note"] = "needs at least two people with two or more named faces"
        return result

    same_pairs = []
    for person in np.flatnonzero(counts >= 2):
        members = np.flatnonzero(owner == person)
        if len(members) > MAX_FACES_PER_PERSON_FOR_PAIRS:
            members = np.array(rng.sample(list(members), MAX_FACES_PER_PERSON_FOR_PAIRS))
        grid = vectors[members] @ vectors[members].T
        same_pairs.append(grid[np.triu_indices(len(members), k=1)])
    same_pairs = np.concatenate(same_pairs)

    draws = min(DIFFERENT_PERSON_PAIRS, len(owner) * (len(owner) - 1) // 2)
    different_counts, different_total, different_pairs = count_different_person_pairs(
        vectors, owner, generator, draws
    )

    sums = person_sums(vectors, owner, people)
    full_norm = np.linalg.norm(sums, axis=1)
    centroids = np.divide(sums, full_norm[:, None], out=np.zeros_like(sums), where=full_norm[:, None] > 0)
    solid = counts >= 3
    others = (sums[owner] - vectors)[solid[owner]]
    others_norm = np.linalg.norm(others, axis=1)
    own_centroid = np.einsum(
        "ij,ij->i", vectors[solid[owner]], others / np.maximum(others_norm, 1e-12)[:, None]
    )

    centroid_ok = counts >= 2
    to_centroids = vectors @ centroids[centroid_ok].T
    own_column = np.cumsum(centroid_ok) - 1
    mine = centroid_ok[owner]
    to_centroids[np.flatnonzero(mine), own_column[owner[mine]]] = -1.0
    best_other_centroid = to_centroids.max(axis=1)

    sample = np.array(rng.sample(range(len(owner)), min(IMPOSTOR_SAMPLE, len(owner))))
    nearest_impostor = np.empty(len(sample))
    for start in range(0, len(sample), 500):
        block = sample[start:start + 500]
        grid = vectors[block] @ vectors.T
        grid[owner[block][:, None] == owner[None, :]] = -1.0
        nearest_impostor[start:start + len(block)] = grid.max(axis=1)

    result["distributions"] = {
        "same_person_pairs": percentiles(same_pairs),
        "different_person_pairs": percentiles(different_pairs),
        "face_vs_its_own_person_centroid": percentiles(own_centroid),
        "face_vs_best_other_person_centroid": percentiles(best_other_centroid),
        "face_vs_nearest_face_of_another_person": percentiles(nearest_impostor),
    }
    result["by_cutoff"] = {
        f"{cutoff:.2f}": {
            "same_person_pairs_reached": share_at_least(same_pairs, cutoff),
            "different_person_pairs_matched": (
                round(different_counts[cutoff] / different_total, 7) if different_total else None
            ),
            "different_person_pairs_matched_count": different_counts[cutoff],
            "different_person_pairs_upper_bound": (
                round(3 / different_total, 7) if different_total and not different_counts[cutoff] else None
            ),
            "faces_whose_own_centroid_reaches": share_at_least(own_centroid, cutoff),
            "faces_a_wrong_centroid_reaches": share_at_least(best_other_centroid, cutoff),
            "faces_with_a_wrong_face_this_close": share_at_least(nearest_impostor, cutoff),
        }
        for cutoff in CUTOFFS
    }
    result["pairs_measured"] = {"same_person": int(len(same_pairs)), "different_person": int(different_total)}
    result["unnamed_pool"] = int(unnamed_pool)
    return result


def measure_unnamed(unnamed, rng: random.Random) -> dict:
    result: dict = {"unnamed_faces": int(len(unnamed))}
    if len(unnamed) < 2:
        return result
    sample = np.array(rng.sample(range(len(unnamed)), min(UNNAMED_SAMPLE, len(unnamed))))
    looks_alike = {cutoff: np.empty(len(sample), dtype=np.int64) for cutoff in CUTOFFS}
    for start in range(0, len(sample), 250):
        block = sample[start:start + 250]
        grid = unnamed[block] @ unnamed.T
        grid[np.arange(len(block)), block] = -1.0
        for cutoff in CUTOFFS:
            looks_alike[cutoff][start:start + len(block)] = (grid >= cutoff).sum(axis=1)
    result["sampled"] = int(len(sample))
    result["by_cutoff"] = {
        f"{cutoff:.2f}": {
            "share_with_a_look_alike": round(float((counts > 0).mean()), 4),
            "median_look_alikes": float(np.median(counts)),
        }
        for cutoff, counts in looks_alike.items()
    }
    return result


def build_report(db_path: Path, seeds: int, seed: int) -> dict:
    if np is None:
        raise SystemExit("This report needs NumPy (it is installed with face detection).")
    generator, rng = np.random.default_rng(seed), random.Random(seed)
    con = open_read_only(db_path)
    try:
        try:
            vectors, owner, rank = load_named(con)
            dimensions = vectors.shape[1] if len(vectors) else 0
            unnamed = load_unnamed(con, dimensions) if dimensions else np.empty((0, 0), dtype=np.float32)
        except sqlite3.OperationalError as error:
            raise SystemExit(
                f"This catalogue is from an older LensLedger ({error}). Open it once in the "
                "current LensLedger so it updates, then run this again."
            )
    finally:
        con.close()
    report: dict = {"seed": seed, "embedding_size": int(dimensions)}
    if not len(owner):
        report["note"] = "no named faces to measure"
        return report
    report["all_named_faces"] = measure_named(vectors, owner, generator, rng, len(unnamed))
    first = rank < seeds
    report["earliest_named_faces"] = {"per_person": seeds, **measure_named(vectors[first], owner[first], generator, rng, len(unnamed))}
    report["unnamed"] = measure_unnamed(unnamed, rng) if len(unnamed) else {"unnamed_faces": 0}
    return report


def pct(value) -> str:
    return "   -  " if value is None else f"{value * 100:5.1f}%"


def rate_text(row: dict) -> str:
    """A rate of different people matched, never rounded to a bare 0.0%."""
    if row.get("different_person_pairs_upper_bound") is not None:
        return f"<{row['different_person_pairs_upper_bound'] * 100:.5f}%"
    rate = row.get("different_person_pairs_matched")
    return "   -  " if rate is None else f"{rate * 100:.5f}%"


def wrong_faces_expected(row: dict, pool: int) -> float | None:
    """How many wrong faces one face would meet among `pool` faces, at the rate seen between named people."""
    rate = row.get("different_person_pairs_upper_bound")
    if rate is None:
        rate = row.get("different_person_pairs_matched")
    return None if rate is None or not pool else rate * pool


def render_named(title: str, section: dict, lines: list[str]) -> None:
    lines += ["", title, "-" * len(title),
              f"{section['faces']:,} faces of {section['people']:,} people "
              f"({section['people_with_two_or_more_faces']:,} with two or more)"]
    if "note" in section:
        lines.append(section["note"])
        return
    lines += ["", "Similarity percentiles (5th / 25th / 50th / 75th / 95th):"]
    labels = {
        "same_person_pairs": "two faces of the same person",
        "different_person_pairs": "two faces of different people",
        "face_vs_its_own_person_centroid": "a face vs the rest of its person's faces",
        "face_vs_best_other_person_centroid": "a face vs the closest OTHER person",
        "face_vs_nearest_face_of_another_person": "a face vs its nearest face of another person",
    }
    for key, label in labels.items():
        spread = section["distributions"][key]
        numbers = "   ".join(f"{spread[f'p{p}']:5.3f}" for p in PERCENTILES) if spread else "-"
        lines.append(f"  {label:<48}{numbers}")
    lines += ["", "At each cutoff:",
              "  cutoff | same-person pairs reached | different-person pairs matched | "
              "own centroid reaches | wrong centroid reaches | wrong face this close"]
    for cutoff, row in section["by_cutoff"].items():
        marker = " <- current" if float(cutoff) in CURRENT_CUTOFFS else ""
        lines.append(
            f"  {cutoff}   |          {pct(row['same_person_pairs_reached'])}          |"
            f"       {rate_text(row):>10}        |"
            f"       {pct(row['faces_whose_own_centroid_reaches'])}       |"
            f"       {pct(row['faces_a_wrong_centroid_reaches'])}       |"
            f"       {pct(row['faces_with_a_wrong_face_this_close'])}{marker}"
        )
    pool = section.get("unnamed_pool", 0)
    if pool:
        lines += ["", f"At your scale: wrong faces one face would meet among your {pool:,} unnamed faces,",
                  "if they differed from it the way your named people differ from each other",
                  "('<' means none were seen, so this is an upper bound):",
                  "  cutoff | wrong faces per face"]
        for cutoff, row in section["by_cutoff"].items():
            if float(cutoff) < 0.45:
                continue
            expected = wrong_faces_expected(row, pool)
            marker = " <- current" if float(cutoff) in CURRENT_CUTOFFS else ""
            mark = "<" if row.get("different_person_pairs_upper_bound") is not None else " "
            lines.append(f"  {cutoff}   | {mark}{expected:8.2f}{marker}" if expected is not None else f"  {cutoff}   |    -")


def render(report: dict) -> str:
    lines = ["LensLedger face-cutoff report",
             "Aggregate numbers only: no names, paths, folders, dates or ids.",
             f"Embedding size {report['embedding_size']}, seed {report['seed']}."]
    if "note" in report:
        return "\n".join(lines + ["", report["note"]])
    render_named("All confirmed faces", report["all_named_faces"], lines)
    earliest = report["earliest_named_faces"]
    render_named(f"Earliest {earliest['per_person']} named faces per person", earliest, lines)
    unnamed = report["unnamed"]
    lines += ["", "Unnamed faces", "-------------", f"{unnamed['unnamed_faces']:,} unnamed faces"]
    if "by_cutoff" in unnamed:
        lines += [f"(measured on {unnamed['sampled']:,} of them against all the others)", "",
                  "  cutoff | share with at least one look-alike | median look-alikes per face"]
        for cutoff, row in unnamed["by_cutoff"].items():
            marker = " <- current" if float(cutoff) in CURRENT_CUTOFFS else ""
            lines.append(f"  {cutoff}   |            {pct(row['share_with_a_look_alike'])}            |"
                         f"            {row['median_look_alikes']:g}{marker}")
    lines += [
        "", "How to read this",
        "- A good cutoff reaches most same-person pairs while matching almost no different-person pairs.",
        "- 'wrong face this close' is the stricter test: the share of faces that have SOME face of another",
        "  named person at or above that cutoff, so a match there could be wrong.",
        "- Neither block of named faces is an unbiased sample. Faces LensLedger confirmed automatically were",
        "  confirmed because they scored high, and even an early face is often named together with the faces",
        "  that matched it, so real same-person scores are likely lower than shown.",
        "- Only people you have named are measured. Strangers and look-alike relatives who are not named are",
        "  not in the wrong-person columns, so treat them as a floor for wrong matches, not a ceiling.",
        "- Current cutoffs: " + ", ".join(f"{c:.2f}" for c in CURRENT_CUTOFFS) + " (marked '<- current').",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None,
                        help="the library catalogue (.sqlite3) to read; by default the catalogue "
                             "of the library LensLedger last opened")
    parser.add_argument("--earliest", type=int, default=8,
                        help="how many of each person's earliest-named faces form the hand-named block")
    parser.add_argument("--seed", type=int, default=1, help="random seed, so a run can be repeated")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args()
    report = build_report(args.db or default_catalogue(), max(1, args.earliest), args.seed)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
