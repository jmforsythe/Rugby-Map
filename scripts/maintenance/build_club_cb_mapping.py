#!/usr/bin/env python3
"""Rebuild data/rugby/club_cb_mapping.json from the RFU's "CB and Club
Relationships" export.

That export is an RFU-internal PDF (club name -> Constituent Body), not
checked into this repo. Download a fresh copy from the RFU help centre and
re-run this script whenever the RFU reissues it:

    https://help.rfu.com/support/solutions/articles/103000063985-how-do-i-find-my-constituent-body-cb-
    https://help.rfu.com/helpdesk/attachments/103014499181

    python scripts/build_club_cb_mapping.py "path/to/CB and Club Relationships.pdf"

The PDF lays club and CB out as two columns, but text extraction collapses
them onto one line separated by a single space, e.g.:

    "Barnes RFC Surrey Rugby (CB)"

Column boundaries aren't recoverable from whitespace once collapsed, so each
line is split by matching a known CB name as a trailing suffix instead (the
set of CBs is small, fixed, and long relative to club names).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rugby import DATA_DIR  # noqa: E402

OUTPUT_PATH = DATA_DIR / "club_cb_mapping.json"

# Every Constituent Body name as it appears verbatim at the end of a row.
# Sorted longest-first isn't required (none is a suffix of another) but keeps
# intent clear if the list grows.
KNOWN_CBS = [
    "Army Rugby Union (CB)",
    "Berkshire County RFU (CB)",
    "Buckinghamshire County RFU (CB)",
    "Cambridge University RFU (CB)",
    "Cheshire RFU (CB)",
    "Cornwall RFU (CB)",
    "Cumbria RFU Ltd. (CB)",
    "Devon RFU (CB)",
    "Dorset & Wilts RFU (CB)",
    "Durham County Rugby Union (CB)",
    "East Midlands Rugby Union (CB)",
    "Eastern Counties Rugby Union (CB)",
    "Essex County RFU (CB)",
    "Gloucestershire RFU (CB)",
    "Hampshire RFU Ltd. (CB)",
    "Hertfordshire RFU (CB)",
    "Kent County Rugby Football Union Limited (CB)",
    "Lancashire County RFU (CB)",
    "Leicestershire Rugby Union Ltd (CB)",
    "Middlesex County RFU (CB)",
    "North Midlands RFU (CB)",
    "Northumberland Rugby Union (CB)",
    "Notts, Lincs & Derbyshire RFU (CB)",
    "Oxford University RFU (CB)",
    "Oxfordshire RFU (CB)",
    "Royal Air Force Rugby Union (CB)",
    "Royal Navy Rugby Union",
    "Somerset County RFU Limited(CB)",
    "Staffordshire County RFU (CB)",
    "Students' Rugby Football Union (CB)",
    "Surrey Rugby (CB)",
    "Sussex RFU Ltd. (CB)",
    "Warwickshire RFU (CB)",
    "Yorkshire RFU (CB)",
]
_HEADER_LINE = "CLUB CONSTITUENT BODY"


def _display_cb_name(raw_cb: str) -> str:
    """Strip the trailing "(CB)" marker, e.g. "Surrey Rugby (CB)" -> "Surrey Rugby"."""
    if raw_cb.endswith("(CB)"):
        return raw_cb[: -len("(CB)")].strip()
    return raw_cb


def parse_pdf(pdf_path: Path) -> dict[str, str]:
    """Extract {club_name: cb_display_name} pairs from the RFU export."""
    import pypdf

    mapping: dict[str, str] = {}
    unmatched: list[str] = []
    reader = pypdf.PdfReader(str(pdf_path))
    for page in reader.pages:
        for raw_line in page.extract_text().split("\n"):
            line = raw_line.strip()
            if not line or line == _HEADER_LINE:
                continue
            for cb in KNOWN_CBS:
                if line.endswith(cb):
                    club = line[: -len(cb)].strip()
                    if club:
                        mapping[club] = _display_cb_name(cb)
                    break
            else:
                unmatched.append(line)

    if unmatched:
        print(f"Warning: {len(unmatched)} line(s) matched no known CB:", file=sys.stderr)
        for line in unmatched:
            print(f"  {line!r}", file=sys.stderr)

    return mapping


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path, help='Path to "CB and Club Relationships.pdf"')
    args = parser.parse_args()

    mapping = parse_pdf(args.pdf_path)
    print(f"Parsed {len(mapping)} club -> CB pairs ({len(set(mapping.values()))} distinct CBs)")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(mapping.items())), f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
