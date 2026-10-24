"""Produce onset suggestions, never ground-truth labels. Review missing/false onsets."""

import argparse
import csv
from pathlib import Path
from common import extract, read_audio


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("audio")
    p.add_argument("output")
    a = p.parse_args()
    candidates = extract(read_audio(a.audio))
    with Path(a.output).open("x", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "piece_id"])
        for c in candidates:
            writer.writerow([f"{c.time_s:.6f}", "REVIEW_REQUIRED"])
    print(
        f"{len(candidates)} suggestions. Assign piece IDs, add misses, remove false positives, then save as labels.csv."
    )


if __name__ == "__main__":
    main()
