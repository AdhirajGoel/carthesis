#!/usr/bin/env python3
"""Rebuild results/Shortlist.xlsx from rows marked 1 in search workbooks.

Usage:
  python3 update_shortlist.py
  python3 update_shortlist.py --self-test
"""

from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import search as base


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
OUTPUT = RESULTS / "Shortlist.xlsx"
KBB_FIELDS = (
    "kbb_buyer_value",
    "kbb_value_type",
    "price_vs_kbb",
    "kbb_checked_at",
    "kbb_source_url",
)
EXTRA_FIELDS = ("deal_edge_pct", *KBB_FIELDS)


def previous_kbb_values(path: Path) -> dict[str, dict]:
    """Keep user-entered KBB values when the shortlist is rebuilt."""
    if not path.exists():
        return {}
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("Reading Excel workbooks requires openpyxl") from None

    workbook = load_workbook(path, read_only=True, data_only=True)
    if "All" not in workbook.sheetnames:
        workbook.close()
        return {}
    rows = workbook["All"].iter_rows(values_only=True)
    headers = next(rows, ())
    indexes = {str(value).strip().casefold(): index for index, value in enumerate(headers) if value}
    if "vin" not in indexes or "kbb_buyer_value" not in indexes:
        workbook.close()
        return {}
    cached = {}
    for row in rows:
        vin = str(row[indexes["vin"]] or "").strip().upper()
        value = row[indexes["kbb_buyer_value"]]
        if not vin or not isinstance(value, (int, float)):
            continue
        cached[vin] = {
            field: row[indexes[field]] if field in indexes else ""
            for field in ("kbb_buyer_value", "kbb_checked_at", "kbb_source_url")
        }
        seller_section = str(row[indexes["seller_section"]] or "").strip().casefold()
        cached[vin]["kbb_value_type"] = (
            "Private Party Value" if seller_section == "private" else "Fair Purchase Price"
        )
    workbook.close()
    return cached


def selected_rows(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("Reading Excel workbooks requires openpyxl") from None

    selected = []
    workbook = load_workbook(path, data_only=True)
    for sheet in workbook.worksheets:
        rows = sheet.iter_rows()
        header_cells = next(rows, None)
        if not header_cells:
            continue
        headers = [cell.value for cell in header_cells]
        indexes = {str(value).strip().casefold(): index for index, value in enumerate(headers) if value}
        if "vin" not in indexes and "listing_url" not in indexes:
            continue
        legacy = "shortlist" not in indexes
        for cells in rows:
            values = [cell.value for cell in cells]
            flag = values[indexes["shortlist"]] if not legacy and indexes["shortlist"] < len(values) else None
            legacy_green = legacy and any(
                cell.fill.fill_type == "solid"
                and cell.fill.fgColor.type == "rgb"
                and str(cell.fill.fgColor.rgb).upper().endswith("92D050")
                for cell in cells
            )
            if not (flag in (1, "1") or legacy_green):
                continue
            car = {
                field: values[indexes[field]] if field in indexes and indexes[field] < len(values) else ""
                for field in (*base.CSV_FIELDS, *EXTRA_FIELDS)
            }
            car["vin"] = str(car["vin"] or "").strip().upper()
            if not base.shortlist_key(car):
                continue
            car["shortlist"] = 1
            selected.append(car)
    workbook.close()
    return selected


def rebuild(results: Path = RESULTS, output: Path = OUTPUT) -> tuple[int, int]:
    saved_kbb = previous_kbb_values(output)
    sources = sorted(
        (
            path for path in results.glob("candidates-*.xlsx")
            if path != output and not path.name.startswith("~$")
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    by_key = {}
    selected_count = 0
    for source in sources:
        rows = selected_rows(source)
        selected_count += len(rows)
        for car in rows:
            by_key[base.shortlist_key(car)] = car

    cars = sorted(by_key.values(), key=base.sort_key)
    for car in cars:
        if values := saved_kbb.get(car.get("vin", "")):
            car.update(values)
    makes = tuple(make for make in base.MODELS if any(car.get("make") == make for car in cars))
    output.parent.mkdir(parents=True, exist_ok=True)
    base.write_workbook(output, cars, makes, extra_fields=EXTRA_FIELDS)
    history_cache = results / "history-reports.json"
    if history_cache.exists():
        import history_check
        history_check.apply_to_workbook(output, history_check.load_cache(history_cache))
    return selected_count, len(cars)


def self_test() -> None:
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.styles import PatternFill
    except ImportError:
        raise RuntimeError("Testing Excel workbooks requires openpyxl") from None

    with TemporaryDirectory() as directory:
        results = Path(directory)
        vin = "1HGCM82633A004352"
        older = {
            "shortlist": 1, "status": "NEW", "seller_section": "dealer", "price": 15_000,
            "year": 2022, "make": "Honda", "model": "Accord", "trim": "LX", "miles": 30_000,
            "vin": vin, "listing_url": "https://example.com/older",
        }
        newer = {
            **older, "price": 14_000, "deal_edge_pct": 12.5,
            "listing_url": "https://example.com/newer",
        }
        first = results / "candidates-Dealer-Honda-2026-08-01_120000.xlsx"
        second = results / "candidates-Private-Honda-2026-08-02_120000.xlsx"
        third = results / "candidates-Private-Honda-2026-08-03_120000.xlsx"
        base.write_workbook(first, [older], ("Honda",))
        base.write_workbook(second, [newer], ("Honda",), extra_fields=EXTRA_FIELDS)
        base.write_workbook(
            third,
            [{**older, "vin": "", "price": 12_000, "listing_url": "https://example.com/no-vin"}],
            ("Honda",),
        )
        legacy = results / "candidates-legacy.xlsx"
        legacy_workbook = Workbook()
        legacy_sheet = legacy_workbook.active
        legacy_sheet.title = "Honda"
        legacy_sheet.append(base.CSV_FIELDS)
        legacy_car = {**older, "vin": "2HGCM82633A004352", "price": 13_000}
        legacy_sheet.append([legacy_car.get(field, "") for field in base.CSV_FIELDS])
        for cell in legacy_sheet[2]:
            cell.fill = PatternFill("solid", fgColor="92D050")
        legacy_workbook.save(legacy)
        import history_check
        history_check.save_cache(
            results / "history-reports.json",
            {vin: history_check.outcome("not_provided", "no public report found")},
        )
        output = results / "Shortlist.xlsx"
        selected_count, unique_count = rebuild(results, output)
        editable = load_workbook(output)
        headers = [cell.value for cell in editable["All"][1]]
        vin_column = headers.index("vin") + 1
        vin_row = next(row for row in range(2, 5) if editable["All"].cell(row, vin_column).value == vin)
        editable["All"].cell(vin_row, headers.index("kbb_buyer_value") + 1).value = 15_500
        editable["All"].cell(vin_row, headers.index("kbb_value_type") + 1).value = "Fair Purchase Price"
        editable["All"].cell(vin_row, headers.index("kbb_checked_at") + 1).value = "2026-08-23"
        editable["All"].cell(vin_row, headers.index("kbb_source_url") + 1).value = "https://www.kbb.com/"
        editable.save(output)
        selected_count, unique_count = rebuild(results, output)
        assert selected_count == 4 and unique_count == 3
        workbook = load_workbook(output, read_only=True, data_only=False)
        headers = [cell.value for cell in workbook["All"][1]]
        price_column = headers.index("price") + 1
        assert workbook.sheetnames == ["All", "Honda"]
        assert workbook["Honda"].cell(2, 1).value == 1
        prices = {workbook["All"].cell(row, price_column).value for row in (2, 3, 4)}
        assert prices == {12_000, 13_000, 14_000}
        history_column = headers.index("history_status") + 1
        vin_column = headers.index("vin") + 1
        vin_row = next(row for row in range(2, 5) if workbook["All"].cell(row, vin_column).value == vin)
        assert workbook["All"].cell(vin_row, history_column).value == "not_provided"
        deal_edge_column = headers.index("deal_edge_pct") + 1
        honda_headers = [cell.value for cell in workbook["Honda"][1]]
        honda_vin_column = honda_headers.index("vin") + 1
        honda_row = next(
            row for row in range(2, 5) if workbook["Honda"].cell(row, honda_vin_column).value == vin
        )
        assert workbook["All"].cell(vin_row, deal_edge_column).value == 12.5
        kbb_column = headers.index("kbb_buyer_value") + 1
        delta_column = headers.index("price_vs_kbb") + 1
        assert workbook["All"].cell(vin_row, kbb_column).value == 15_500
        assert str(workbook["All"].cell(vin_row, delta_column).value).startswith("=IF(")
        assert workbook["Honda"].cell(honda_row, deal_edge_column).value == 12.5
        assert workbook["All"].cell(vin_row, deal_edge_column).number_format == "0.0"
        assert workbook["All"].cell(vin_row, 1).value == f"='Honda'!A{honda_row}"
        workbook.close()
    print("Shortlist self-test passed")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    if sys.argv[1:] in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    if sys.argv[1:]:
        print("Usage: python3 update_shortlist.py [--self-test]", file=sys.stderr)
        return 2
    try:
        selected_count, unique_count = rebuild()
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Saved {unique_count:,} unique cars from {selected_count:,} selected rows to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
