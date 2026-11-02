#!/usr/bin/env python3
"""Find a VIN across every Excel workbook in this project.

Usage:
  python3 vin_search.py --vin 1TEST000000000001
  python3 vin_search.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

import search as base


ROOT = Path(__file__).resolve().parent
SHORTLIST = ROOT / "results" / "Shortlist.xlsx"


def valid_vin(value: str) -> str:
    vin = value.strip().upper()
    if not base.VIN_RE.fullmatch(vin):
        raise argparse.ArgumentTypeError("VIN must contain 17 valid characters")
    return vin


def find_vin(vin: str, root: Path = ROOT) -> tuple[list[tuple[Path, str, int, bool]], list[str]]:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:
        raise RuntimeError("Reading Excel workbooks requires openpyxl") from None

    matches = []
    warnings = []
    for path in sorted(root.rglob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        try:
            workbook = load_workbook(path, read_only=True, data_only=True)
        except (OSError, ValueError, KeyError, BadZipFile, InvalidFileException) as error:
            warnings.append(f"Could not read {path}: {error}")
            continue
        try:
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                headers = next(rows, ())
                indexes = {
                    str(value).strip().casefold(): index
                    for index, value in enumerate(headers) if value is not None
                }
                if "vin" not in indexes:
                    continue
                vin_index = indexes["vin"]
                shortlist_index = indexes.get("shortlist")
                for row_number, row in enumerate(rows, 2):
                    found = str(row[vin_index] or "").strip().upper() if vin_index < len(row) else ""
                    if found != vin:
                        continue
                    flag = row[shortlist_index] if shortlist_index is not None and shortlist_index < len(row) else 0
                    matches.append((path, sheet.title, row_number, flag in (1, "1")))
        finally:
            workbook.close()
    return matches, warnings


def self_test() -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        raise RuntimeError("Testing Excel workbooks requires openpyxl") from None

    vin = "1HGCM82633A004352"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, flag in (("first.xlsx", 0), ("Shortlist.xlsx", 1)):
            workbook = Workbook()
            sheet = workbook.active
            sheet.title = "All"
            sheet.append(["shortlist", "vin"])
            sheet.append([flag, vin])
            workbook.save(root / name)
        matches, warnings = find_vin(vin, root)
        assert not warnings and len(matches) == 2
        assert {match[3] for match in matches} == {False, True}
    print("VIN-search self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Find a VIN across all project Excel workbooks")
    parser.add_argument("--vin", type=valid_vin)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        if args.vin:
            parser.error("--self-test cannot be combined with --vin")
        self_test()
        return 0
    if not args.vin:
        parser.error("--vin is required")

    matches, warnings = find_vin(args.vin)
    for warning in warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if not matches:
        print(f"{args.vin}: not found")
        return 1

    for path, sheet, row, shortlisted in matches:
        print(f"{path.relative_to(ROOT)} | {sheet}!{row} | shortlist: {'yes' if shortlisted else 'no'}")
    in_shortlist = any(path == SHORTLIST for path, _, _, _ in matches)
    print(f"Found {len(matches)} occurrence(s); current Shortlist.xlsx: {'yes' if in_shortlist else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
