"""Map a copy of sheet music to detector families before using LiveMatcher."""

import argparse
import sys
from pathlib import Path
from common import NORMALIZE, read_notes, write_notes


def normalize(notes, drop_unsupported=False):
    unsupported = sorted({m for _, m in notes if m not in NORMALIZE})
    if unsupported and not drop_unsupported:
        raise ValueError(
            f"Unsupported percussion {unsupported}; explicitly use --drop-unsupported to omit it"
        )
    return sorted({(t, NORMALIZE[m]) for t, m in notes if m in NORMALIZE}), unsupported


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("output")
    p.add_argument("--drop-unsupported", action="store_true")
    a = p.parse_args()
    if Path(a.source).resolve() == Path(a.output).resolve():
        raise ValueError("Do not overwrite the original reference")
    notes = read_notes(a.source)
    normalized, unsupported = normalize(notes, a.drop_unsupported)
    write_notes(a.output, normalized, "reference normalized to six drum families")
    print(
        f"{len(notes)} -> {len(normalized)} notes; omitted MIDI: {unsupported}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
