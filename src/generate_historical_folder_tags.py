"""Generate conservative LensLedger folder tags from descriptive folder names.

The generator is intentionally narrow: a tag is emitted only when a folder name
contains an explicit matching word or phrase. Existing CSV rows are retained and
unioned with generated tags. Photos and their embedded metadata are never changed.
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import Counter
from pathlib import Path

from app_paths import data_root, libraries_root


HERE = Path(__file__).resolve().parent
DEFAULT_DB = libraries_root() / "default.sqlite3"
DEFAULT_CSV = data_root() / "folder-tags.csv"

# Specific, recurring names are useful search terms and are only emitted when the
# text appears literally in the folder path.
LITERAL_TAGS = {
    "Busch Gardens": ("busch gardens",),
    "Walt Disney World": ("walt disney world", "disney world"),
    "Epcot": ("epcot",),
    "SeaWorld": ("seaworld", "sea world"),
    "Universal Studios": ("universal studios",),
    "Florida State Fair": ("florida state fair",),
    "Relay for Life": ("relay for life",),
    "Kennedy Space Center": ("kennedy space center", "space center"),
    "St. Augustine": ("st. augustine", "st augustine"),
    "Key Largo": ("key largo",),
    "Key West": ("key west",),
    "Las Vegas": ("las vegas", "vegas"),
    "Chicago": ("chicago",),
    "New York City": ("new york city", "nyc"),
    "New Orleans": ("new orleans",),
    "Los Angeles": ("los angeles",),
    "San Francisco": ("san francisco",),
    "Seattle": ("seattle",),
    "Denver": ("denver",),
    "Savannah": ("savannah",),
    "Nashville": ("nashville",),
    "Russia": ("russia",),
    "Peru": ("peru",),
    "Bolivia": ("bolivia",),
    "Australia": ("australia", "austrailia"),
    "VFW": ("vfw",),
    "The Castle": ("the castle", " castle"),
}

# Each expression is deliberately tied to an unambiguous visible event, activity,
# setting, or subject named by the folder owner. Order controls CSV readability.
PATTERN_TAGS: list[tuple[str, str]] = [
    ("Birthday", r"\bb(?:irth|rith)day\b|\bbday\b"),
    ("Wedding", r"\bwedding\b|\bhoneymoon\b"),
    ("Anniversary", r"\banniversary\b"),
    ("Christmas", r"\bchristmas\b|\bxmas\b"),
    ("Halloween", r"\bhalloween\b"),
    ("Thanksgiving", r"\bthanksgiving\b"),
    ("Easter", r"\beaster\b"),
    ("New Year's", r"\bnew year(?:'s|s)?\b"),
    ("Valentine's Day", r"\bvalentine(?:'s|s)?\b"),
    ("Mother's Day", r"\bmother(?:'s|s)? day\b"),
    ("Father's Day", r"\bfather(?:'s|s)? day\b"),
    ("Graduation", r"\bgraduation\b"),
    ("Prom", r"\bprom\b"),
    ("Homecoming", r"\bhomecoming\b"),
    ("Funeral", r"\bfuneral\b|\bmemorial\b"),
    ("Reunion", r"\breunion\b"),
    ("Party", r"\bparty\b|\bafterparty\b"),
    ("Parade", r"\bparade\b"),
    ("Festival", r"\bfestival\b|\brena+issance fair\b"),
    ("Fair", r"\bfair\b"),
    ("Carnival", r"\bcarnival\b"),
    ("Fireworks", r"\bfireworks?\b"),
    ("Concert", r"\bconcert\b|\blive show\b"),
    ("Theater", r"\btheat(?:er|re)\b|\bmusical\b"),
    ("Museum", r"\bmuseum\b"),
    ("Aquarium", r"\baquarium\b"),
    ("Zoo", r"\bzoo\b"),
    ("Amusement Park", r"\bamusement park\b|\btheme park\b|\broller coaster"),
    ("Camping", r"\bcamp(?:ing)?\b"),
    ("Hiking", r"\bhik(?:e|ing)\b"),
    ("Cycling", r"\bbik(?:e|ing)\b|\bcycle\b|\bcycling\b"),
    ("Kayaking", r"\bkayak(?:ing)?\b"),
    ("Canoeing", r"\bcanoe(?:ing)?\b"),
    ("Tubing", r"\btubing\b"),
    ("Boating", r"\bboat(?:ing)?\b"),
    ("Golf", r"\bgolf\b|\bputt[ -]?putt\b"),
    ("Beach", r"\bbeach\b"),
    ("Park", r"\bpark\b"),
    ("Picnic", r"\bpicnic\b"),
    ("Swimming", r"\bswim(?:ming)?\b|\bpool\b"),
    ("Travel", r"\btrip\b|\bvacation\b|\btravel(?:ing|ling)?\b|\bsightseeing\b"),
    ("Cruise", r"\bcruise\b"),
    ("Flying", r"\bflight\b|\bflying\b|\bon plane\b"),
    ("Road Trip", r"\broad trip\b|\bscenic drive\b"),
    ("Portrait", r"\bportraits?\b|\bheadshots?\b"),
    ("Selfie", r"\bselfie(?:s)?\b|\bdelfie\b"),
    ("Family", r"\bfamily\b"),
    ("Food", r"\bfood\b|\bmeal\b|\bcake\b|\bpizza\b|\bsushi\b|\bbarbecue\b|\bbbq\b"),
    ("Breakfast", r"\bbreakfast\b|\bbrunch\b"),
    ("Lunch", r"\blunch\b"),
    ("Dinner", r"\bdinner\b|\bout to eat\b"),
    ("Restaurant", r"\brestaurant\b|\bdiner\b|\bcafe\b"),
    ("Dancing", r"\bdanc(?:e|ing)\b|\bclub night\b"),
    ("Costume", r"\bcostume\b|\bcosplay\b|\bsteampunk\b"),
    ("Sports", r"\bfootball\b|\bbaseball\b|\bsuper ?bowl\b|\bgrand prix\b"),
    ("Dog", r"\bdogs?\b|\bpuppy\b|\bdog ?park\b"),
    ("Cat", r"\bcats?\b|\bkitten\b"),
    ("Bird", r"\bbirds?\b|\bwater ?fowl\b|\bduck\b|\bpeacock\b"),
    ("Insect", r"\binsects?\b|\bbugs?\b|\bdragonfly\b|\bwasp\b|\bmoth\b|\bspider\b"),
    ("Animals", r"\banimals?\b|\bhamster\b|\bmanatee\b|\biguana\b|\bturtle\b"),
    ("Flowers", r"\bflowers?\b|\bsunflower\b|\bpoppies\b"),
    ("Garden", r"\bgarden(?:ing)?\b|\blandscap(?:e|ing)\b"),
    ("Sky", r"\bsky\b|\bsunrise\b|\bsunset\b|\bclouds?\b|\bmoon\b"),
    ("Weather", r"\bstorm\b|\bhurricane\b|\brain\b|\bfog(?:gy)?\b|\bsnow\b"),
    ("Nature", r"\bnature\b|\bwaterfall\b|\bmountains?\b|\bforest\b"),
    ("School", r"\bschool\b|\bfield trip\b|\bcareer day\b"),
    ("Work", r"\bwork\b|\boffice\b|\bcoworker\b|\bcompany\b"),
    ("Home", r"\bhouse\b|\bhome\b|\bapartment\b|\bporch\b|\bbackyard\b"),
    ("Home Improvement", r"\bbuilding\b|\binstalled?\b|\breplaced?\b|\brenovation\b|\bremodel\b|\brepair\b"),
    ("Medical", r"\bhospital\b|\bsurgery\b|\bbroken arm\b|\binjury\b|\bmedical\b|\bdental\b|\bdoctor\b"),
    ("Vehicles", r"\bcars?\b|\btrucks?\b|\bscooters?\b|\bmotorcycle\b|\bautomotive\b"),
    ("Technology", r"\bcomputers?\b|\bnetwork\b|\bwireless\b|\bwifi\b|\bdevice\b|\bwebcam\b"),
    ("Art", r"\bart\b|\bpaintings?\b|\bdrawings?\b"),
    ("Scanned Photos", r"\bscann?ed\b|\bfilm roll\b"),
]

COMPILED_RULES = [(tag, re.compile(pattern, re.IGNORECASE)) for tag, pattern in PATTERN_TAGS]


def searchable_folder_text(folder: str) -> str:
    parts = folder.replace("\\", "/").split("/")
    useful = []
    for part in parts[1:] if parts and parts[0].isdigit() else parts:
        if re.fullmatch(r"(?i)web|originals?|panoramic|stitch_?\d+|canon powershot .+", part.strip()):
            continue
        # Dates are valuable structurally but should not trigger textual rules.
        part = re.sub(r"^\d{4}_\d{2}_\d{2}\s*-+\s*", "", part).strip()
        useful.append(part)
    return " / ".join(useful)


def infer_tags(folder: str) -> list[str]:
    text = searchable_folder_text(folder)
    folded = f" {text.casefold()} "
    tags: list[str] = []
    for tag, needles in LITERAL_TAGS.items():
        if any(needle.casefold() in folded for needle in needles):
            tags.append(tag)
    for tag, pattern in COMPILED_RULES:
        if pattern.search(text):
            tags.append(tag)
    # "Dog" is visually meaningful, but common food names are not pet evidence.
    if "Dog" in tags and re.search(r"\b(?:corn|hot|slaw|chili) dogs?\b", text, re.IGNORECASE):
        if not re.search(r"\bpuppy\b|\bdog ?park\b", text, re.IGNORECASE):
            tags.remove("Dog")
    # Higher-level context that is strictly implied by a more specific tag.
    if any(tag in tags for tag in ("Cruise", "Flying", "Road Trip")) and "Travel" not in tags:
        tags.append("Travel")
    if any(tag in tags for tag in ("Breakfast", "Lunch", "Dinner", "Restaurant")) and "Food" not in tags:
        tags.append("Food")
    if "Wedding" in tags and re.search(r"\bhoneymoon\b", text, re.IGNORECASE):
        tags.append("Honeymoon")
    if not tags:
        tags.extend(_fallback_tags(text))
    return list(dict.fromkeys(tags))


GENERIC_FOLDER_NAMES = {
    "new folder", "untitled", "misc", "miscellaneous", "photos", "pictures",
    "images", "web", "originals", "export", "exports", "temp", "backup",
}


def _fallback_tags(text: str) -> list[str]:
    """Use a folder's own descriptive words as its tag when no curated
    category recognizes the name. A folder someone deliberately named
    "Out with Candy" or "Downtown LA and Olvera Street" should never end up
    with zero tags just because it's not a recognized category of event."""
    candidates = [part.strip() for segment in text.split(" / ") for part in segment.split(",")]
    return [
        part for part in candidates
        if part and part.casefold() not in GENERIC_FOLDER_NAMES and not part.isdigit()
    ]


def read_existing(path: Path) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            folder = row["folder"].strip().replace("\\", "/")
            rows[folder] = [tag.strip() for tag in row["tags"].split(";") if tag.strip()]
    return rows


def generate(db_path: Path, csv_path: Path, dry_run: bool) -> tuple[int, int, Counter[str]]:
    existing = read_existing(csv_path)
    con = sqlite3.connect(db_path)
    folders = [row[0] for row in con.execute(
        "SELECT DISTINCT folder FROM assets WHERE in_review_bin=0 ORDER BY folder"
    )]
    con.close()

    output: dict[str, list[str]] = {folder: list(tags) for folder, tags in existing.items()}
    generated_count = 0
    tag_counts: Counter[str] = Counter()
    for folder in folders:
        inferred = infer_tags(folder)
        if not inferred:
            continue
        generated_count += 1
        merged = output.setdefault(folder, [])
        known = {tag.casefold() for tag in merged}
        for tag in inferred:
            if tag.casefold() not in known:
                merged.append(tag)
                known.add(tag.casefold())
        tag_counts.update(inferred)

    if not dry_run:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(("folder", "tags"))
            for folder in sorted(output, key=str.casefold):
                writer.writerow((folder, ";".join(output[folder])))
    return generated_count, len(output), tag_counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    generated, total, counts = generate(args.db, args.csv, args.dry_run)
    print(f"historical folders matched: {generated}")
    print(f"total CSV rows: {total}")
    print("most common tags:")
    for tag, count in counts.most_common(20):
        print(f"  {count:4d}  {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
