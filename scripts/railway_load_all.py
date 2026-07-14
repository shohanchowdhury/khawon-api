"""Load the full v2 pipeline dataset into Railway/local Postgres."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(r"C:\Users\shoha\OneDrive\Desktop\strip data\code\v2_output")


def main() -> None:
    cmd = [
        sys.executable,
        "-u",
        str(ROOT / "load_batch.py"),
        str(DATA / "consolidated.json"),
        str(DATA / "canonical_dishes.json"),
        str(DATA / "restaurants_*_restaurants.json"),
        "--area",
        "Dhanmondi",
    ]
    print("Running:", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
