import json
import subprocess
import sys
from pathlib import Path

DATA = Path(r"C:\Users\shoha\OneDrive\Desktop\strip data\code\v2_output")
GLOB = str(DATA / "restaurants_*_restaurants.json")


def test_cli_writes_chains_json_for_the_real_dataset(tmp_path):
    out = tmp_path / "chains.json"
    proc = subprocess.run(
        [sys.executable, "bootstrap_chains.py", "--restaurants", GLOB, "--out", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    brands = json.loads(out.read_text(encoding="utf-8"))

    # Canaries against the real dataset. These are MEASURED from this
    # normalizer, not predicted: 451 restaurants -> 378 brands, 53 of them
    # multi-location. (The plan first guessed 383/52 from a throwaway prototype
    # whose own count was really 381; the difference is 3 verified merge events,
    # 0 splits -- Gloria Jean's Coffee, Ledor and Tabaq Coffee, each of which
    # the prototype wrongly split. See test_known_chain_code_failures_are_fixed.)
    assert len(brands) == 378, f"expected 378 brands, got {len(brands)}"
    assert sum(len(b["member_codes"]) for b in brands) == 451
    assert len([b for b in brands if len(b["member_codes"]) > 1]) == 53

    by_slug = {b["slug"]: b for b in brands}
    # The four known source chain_code failures must be fixed by normalization.
    assert len(by_slug["waffle-up"]["member_codes"]) == 4
    assert len(by_slug["thai-bistro"]["member_codes"]) == 2
    assert len(by_slug["new-hanif-biryani"]["member_codes"]) == 2
    assert len(by_slug["rice-and-more"]["member_codes"]) == 2

    # Brands a naive normalizer splits: a trailing outlet digit ("Ledor- Gulshan-2",
    # "Gloria Jean's Coffee-Gulshan 1") or an area token stripped in the wrong
    # order ("Tabaq Coffee Gulshan Avenue" -> stray "avenue"). Regression guards.
    assert len(by_slug["gloria-jean-s-coffee"]["member_codes"]) == 2
    assert len(by_slug["ledor"]["member_codes"]) == 3
    assert len(by_slug["tabaq-coffee"]["member_codes"]) == 3


def test_every_restaurant_appears_in_exactly_one_brand(tmp_path):
    out = tmp_path / "chains.json"
    subprocess.run(
        [sys.executable, "bootstrap_chains.py", "--restaurants", GLOB, "--out", str(out)],
        capture_output=True, text=True, check=True,
    )
    brands = json.loads(out.read_text(encoding="utf-8"))
    codes = [c for b in brands for c in b["member_codes"]]
    assert len(codes) == len(set(codes)), "a restaurant landed in two brands"
