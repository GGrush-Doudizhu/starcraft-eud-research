"""Extract the eud-book memory table into a standalone TSV reference."""

import argparse
from collections import Counter
import csv
import io
import json
from pathlib import Path
import re


HEADER = ["Address", "Player ID", "Name", "Size", "Length", "SCR", "Description"]
FIELDS = ["name", "addr", "size", "len", "scr"]
ADDRESS = re.compile(r"\[([0-9A-Fa-f]{8})\]\[([0-9A-Fa-f]{8})\]")
REFERENCE = re.compile(r"\[[0-9A-Fa-f]{8}\]:\s+\S.*")


def parse_table(text):
    entries = []
    phase = "header"
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue

        def invalid(reason):
            return ValueError(f"Line {line_number}: {reason}")

        if phase == "header":
            if [cell.strip() for cell in line.split("|")] != HEADER:
                raise invalid("unexpected table header")
            phase = "separator"
            continue
        if phase == "separator":
            cells = [cell.strip() for cell in line.split("|")]
            if len(cells) != len(HEADER) or not all(
                re.fullmatch(r"-+", cell) for cell in cells
            ):
                raise invalid("invalid table separator")
            phase = "rows"
            continue
        if REFERENCE.fullmatch(line):
            phase = "references"
            continue
        if phase == "references":
            raise invalid("unexpected content after link references")

        # Split only the six column delimiters; description text is not imported.
        cells = [cell.strip() for cell in line.split("|", 6)]
        if len(cells) != len(HEADER):
            raise invalid("expected seven columns")
        address = ADDRESS.fullmatch(cells[0])
        if not address or address[1].upper() != address[2].upper():
            raise invalid("invalid or mismatched address link")
        if not cells[2]:
            raise invalid("missing entry name")

        entry = {"name": cells[2], "addr": f"0x{int(address[1], 16):X}"}
        for key, cell in (("size", cells[3]), ("len", cells[4])):
            if cell:
                if not re.fullmatch(r"[0-9]+", cell):
                    raise invalid(f"invalid integer for {key}: {cell!r}")
                entry[key] = int(cell)
        if cells[5]:
            entry["scr"] = cells[5]
        # Keep source order and repeated addresses; each row is a distinct entry.
        entries.append(entry)

    if not entries or phase not in ("rows", "references"):
        raise ValueError("No complete table found")
    return entries


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, required=True,
        help="Path to the eud-book src/offset_table.md source table",
    )
    parser.add_argument("--output", type=Path, default=directory / "euddb.tsv")
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve():
        parser.error("source and output must be different files")

    entries = parse_table(args.source.read_text(encoding="utf-8-sig"))
    rows = [FIELDS] + [[str(entry.get(field, "")) for field in FIELDS] for entry in entries]
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter="\t", lineterminator="\n").writerows(rows)
    payload = buffer.getvalue()
    restored = list(csv.reader(io.StringIO(payload, newline=""), delimiter="\t"))
    if "\r" in payload or len(payload.splitlines()) != len(entries) + 1 or restored != rows:
        raise ValueError("Serialized TSV failed validation")
    args.output.write_bytes(payload.encode("utf-8"))
    with args.output.open(encoding="utf-8", newline="") as output:
        if list(csv.reader(output, delimiter="\t")) != rows:
            raise ValueError("Written TSV failed round-trip validation")

    address_counts = Counter(entry["addr"] for entry in entries)
    print(json.dumps({
        "source": str(args.source.resolve()),
        "output": str(args.output.resolve()),
        "entries": len(entries),
        "unique_addresses": len(address_counts),
        "repeated_address_groups": sum(n > 1 for n in address_counts.values()),
        "entries_without_scr": sum("scr" not in entry for entry in entries),
        "bytes": len(payload.encode("utf-8")),
        "validation": "passed",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
