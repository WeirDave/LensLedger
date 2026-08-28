"""Timestamped console output for the LensLedger terminal window."""

from __future__ import annotations

import datetime as dt


def log(message: str) -> None:
    ts = dt.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    print(f"  [{ts}] {message}", flush=True)
