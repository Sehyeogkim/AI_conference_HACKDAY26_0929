"""Validate a submitted recording without launching or claiming simulator replay."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from coffee_game.recording import inspect_episode

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode", type=Path)
    args = parser.parse_args()
    report = inspect_episode(args.episode)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["structural_valid"] else 1)
