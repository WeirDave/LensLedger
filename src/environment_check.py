"""Checks that the Python running LensLedger, and the packages it needs, are current.

`Start LensLedger.cmd` runs this before every launch. It reads requirements.txt
-- the same file an update replaces -- so a package that gains a newer minimum,
or joins the list, is installed on the next launch without anyone editing the
launcher. An installation whose packages already satisfy the file starts
without contacting the network.

Exit codes: 0 ready, 1 packages out of date (check only), 2 Python too old,
3 the packages could not be installed.

Only the standard library is used, and nothing here may need a newer Python
than the one it is checking.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

MINIMUM_PYTHON = (3, 11)
REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
DOWNLOAD_PAGE = "https://www.python.org/downloads/"

_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*?)\s*$")
_CLAUSE = re.compile(r"^(>=|<=|==|!=|>|<)\s*([0-9][0-9A-Za-z.*+!_-]*)$")


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_version(text: str) -> tuple[int, ...]:
    """The numeric release of a version: "13.0.0rc1" is (13, 0, 0)."""
    parts = []
    for piece in text.split("+")[0].split("."):
        digits = re.match(r"\d+", piece)
        if not digits:
            break
        parts.append(int(digits.group()))
        if digits.group() != piece:
            break
    return tuple(parts) or (0,)


def _padded(left: tuple[int, ...], right: tuple[int, ...]):
    size = max(len(left), len(right))
    return left + (0,) * (size - len(left)), right + (0,) * (size - len(right))


def clause_met(installed: str, operator: str, wanted: str) -> bool:
    have, need = _padded(parse_version(installed), parse_version(wanted.rstrip(".*")))
    if operator == ">=":
        return have >= need
    if operator == "<=":
        return have <= need
    if operator == ">":
        return have > need
    if operator == "<":
        return have < need
    if operator == "!=":
        return have != need
    return have == need


def requirement_lines(path: Path = REQUIREMENTS) -> list[tuple[str, list[tuple[str, str]]]]:
    """(package, [(operator, version), ...]) for each package a plain install needs.

    Commented lines, which hold the optional features' packages, are skipped.
    """
    wanted = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        clauses = []
        for piece in match.group(2).split(","):
            clause = _CLAUSE.match(piece.strip())
            if clause:
                clauses.append((clause.group(1), clause.group(2)))
        wanted.append((match.group(1), clauses))
    return wanted


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def importable(name: str) -> bool:
    """False when the package is registered but its modules are gone or unfindable."""
    wanted = normalise(name)
    modules = [
        module for module, distributions in importlib.metadata.packages_distributions().items()
        if wanted in {normalise(d) for d in distributions}
    ]
    if not modules:
        return True
    try:
        return any(importlib.util.find_spec(module) is not None for module in modules)
    except (ImportError, ValueError):
        return False


def unmet_requirements(path: Path = REQUIREMENTS) -> list[str]:
    """What is missing, too old, too new, or broken, each as a short sentence."""
    problems = []
    for name, clauses in requirement_lines(path):
        have = installed_version(name)
        if have is None:
            problems.append(f"{name} is not installed")
            continue
        failed = [f"{op}{version}" for op, version in clauses if not clause_met(have, op, version)]
        if failed:
            problems.append(f"{name} {have} does not meet {','.join(failed)}")
        elif not importable(name):
            problems.append(f"{name} {have} is installed but cannot be loaded")
    return problems


def python_problem(version=None) -> str | None:
    version = tuple(version if version is not None else sys.version_info[:3])
    if version[:2] >= MINIMUM_PYTHON:
        return None
    return (
        f"LensLedger needs Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer, "
        f"and this is Python {'.'.join(str(part) for part in version[:3])} ({sys.executable}).\n"
        f"Install a current Python from {DOWNLOAD_PAGE} and select \"Add Python to PATH\"."
    )


def install(path: Path = REQUIREMENTS) -> bool:
    command = [sys.executable, "-m", "pip", "install", "-r", str(path)]
    return subprocess.run(command, check=False).returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--requirements", type=Path, default=REQUIREMENTS)
    parser.add_argument("--python-only", action="store_true", help="check the Python version and nothing else")
    parser.add_argument("--fix", action="store_true", help="install or update packages that are out of date")
    args = parser.parse_args(argv)

    problem = python_problem()
    if problem:
        print()
        print(problem)
        return 2
    if args.python_only:
        return 0
    problems = unmet_requirements(args.requirements)
    if not problems:
        return 0
    if not args.fix:
        for line in problems:
            print(line)
        return 1
    print("Updating LensLedger's Python packages...")
    for line in problems:
        print(f"  {line}")
    if not install(args.requirements) or unmet_requirements(args.requirements):
        print()
        print("LensLedger could not install its Python requirements.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
