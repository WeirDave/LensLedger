"""Timestamped console output for the LensLedger terminal window."""

from __future__ import annotations

import datetime as dt


def log(message: str) -> None:
    ts = dt.datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] {message}", flush=True)
