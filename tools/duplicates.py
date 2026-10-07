#!/usr/bin/env python3
"""Find likely duplicate books in a split_library.py log.

    python3 tools/duplicates.py /path/to/library-split/_log/dry-run.jsonl

Groups files whose names match once trailing copy markers are removed ("-1",
" 2", "-1 2", "(1)", "copy") and prints every group with more than one member,
with each member's outcome, page count, bookmark count and producer. The
judgment of which copy to keep is yours; usually the one with an outline.

Only name-based: two copies named differently are not found.
"""
import json
import re
import sys
from collections import defaultdict

COPY_MARKERS = re.compile(r"(\s*(-\d+|\(\d+\)|\s\d+|copy(\s\d+)?))+$", re.IGNORECASE)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    latest = {}
    with open(sys.argv[1], encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                latest[r["book"]] = r  # last line per book wins

    groups = defaultdict(list)
    for r in latest.values():
        key = COPY_MARKERS.sub("", r["book"]).strip().casefold()
        groups[key].append(r)

    found = 0
    for key, rs in sorted(groups.items()):
        if len(rs) < 2:
            continue
        found += 1
        print(key)
        for r in sorted(rs, key=lambda r: r["book"]):
            pages = r["pages"] if r["pages"] is not None else "?"
            entries = r["outline_entries"] if r["outline_entries"] is not None else "?"
            print(f"    {r['outcome']:<12} {pages:>5}p  bookmarks={entries:<5} {r['producer'] or '?':<40} {r['book']}")
    print(f"\n{found} groups among {len(latest)} files", file=sys.stderr)


if __name__ == "__main__":
    main()
