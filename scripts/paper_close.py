#!/usr/bin/env python3
"""Record near-kickoff closing prices for open paper rows. PAPER ONLY.

Thin entry point; the logic is in `services/paper/paper/closing.py`. This file
owns the network (the paper package opens no socket, pinned by test_gate.py):
one read-only, unauthenticated GET per ticker to Kalshi's public market
endpoint. Run from the repo root:  python3 scripts/paper_close.py
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "paper"))

from paper.closing import main  # noqa: E402 - the path has to be set first


def http_get(url: str) -> tuple[int, str]:
    """GET only, no auth header. A network error is status 0 (retried)."""
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


if __name__ == "__main__":
    sys.exit(main(http_get))
