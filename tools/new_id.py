#!/usr/bin/env python3
"""Make a collision-resistant id for a shared list (candidates, notes): PREFIX-LANE-XXXX.

    python3 tools/new_id.py --prefix C --lane AEC [--avoid governance/candidates.md]

Two sessions numbering one list independently produced duplicate ids on 2026-11-01
(C-11..C-13). A random Crockford base32 suffix needs no coordination: two sessions
pick the same one with probability about 1 in a million per pair. --avoid rereads a
file and retries if the id already appears there. Decisions keep their single DEC-NNN
sequence (DEC-018); git conflicts on concurrent appends and check_ledgers.py catches
any numbering slip after a merge. Standard library only."""
import argparse, pathlib, re, secrets, sys

B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--lane", required=True)
    ap.add_argument("--avoid", type=pathlib.Path, action="append", default=[])
    a = ap.parse_args()
    for part, name in ((a.prefix, "--prefix"), (a.lane, "--lane")):
        if not re.fullmatch(r"[A-Za-z0-9]{1,12}", part):
            sys.exit(f"{name} must be 1-12 letters or digits")
    seen = "".join(p.read_text(encoding="utf-8") for p in a.avoid if p.exists())
    for _ in range(50):
        new = f"{a.prefix.upper()}-{a.lane.upper()}-" + "".join(secrets.choice(B32) for _ in range(4))
        if new not in seen:
            print(new)
            return
    sys.exit("couldn't find an unused id in 50 tries")


if __name__ == "__main__":
    main()
