#!/usr/bin/env python3
"""Add a seller-balanced local asking-price edge to a candidate workbook."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import copy
import math
import os
from pathlib import Path
import re
from statistics import median
import sys
from tempfile import TemporaryDirectory
import unicodedata

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter, range_boundaries

from vehicle_reference import key, load_reference, match_current_vehicle


COLUMN = "deal_edge_pct"
PEER_COUNT = 5
MAX_DISTANCE = 2.0
VIN_RE = re.compile(r"[A-HJ-NPR-Z0-9]{17}")
DATE_RE = re.compile(r"(?<!\d)(20\d{2})-\d{2}-\d{2}")
REQUIRED = {
    "price", "currentmsrp", "currenttrimmatch", "year", "make", "model", "trim",
    "miles", "bodytype", "powertrain", "drivetrain", "certified", "seller", "city",
    "state", "vin",
}


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _normalized_text(value) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def _vin(value) -> str:
    return str(value or "").strip().upper()


def _reference_drives(value) -> frozenset[str]:
    value = key(value)
    drives = set()
    if "fwd" in value or "frontwheeldrive" in value:
        drives.add("FWD")
    if "rwd" in value or "rearwheeldrive" in value:
        drives.add("RWD")
    if "awd" in value or "allwheeldrive" in value:
        drives.add("AWD")
    if "4wd" in value or "4x4" in value or "fourwheeldrive" in value:
        drives.add("4WD")
    return frozenset(drives)


def _resolved_drive(listing_value, reference_value) -> str | None:
    """Resolve MarketCheck's 4WD umbrella token against the current reference."""
    listing = key(listing_value)
    allowed = _reference_drives(reference_value)
    if listing == "4wd":
        four_wheel = allowed & {"AWD", "4WD"}
        return next(iter(four_wheel)) if len(four_wheel) == 1 else None
    aliases = {
        "fwd": "FWD", "frontwheeldrive": "FWD",
        "rwd": "RWD", "rearwheeldrive": "RWD",
        "awd": "AWD", "allwheeldrive": "AWD",
        "4x4": "4WD", "fourwheeldrive": "4WD",
    }
    resolved = aliases.get(listing)
    return resolved if resolved in allowed else None


def _percentile(values, probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    fraction = position - lower
    if lower == len(ordered) - 1:
        return ordered[lower]
    return ordered[lower] + fraction * (ordered[lower + 1] - ordered[lower])


def _run_year(path: Path) -> int:
    matches = list(DATE_RE.finditer(path.name))
    if not matches:
        raise ValueError("the candidate filename has no YYYY-MM-DD search timestamp; use --run-year")
    return int(matches[-1].group(1))


def _headers(sheet) -> dict[str, int]:
    return {key(cell.value): cell.column for cell in sheet[1] if cell.value}


def _sheet_rows(sheet) -> list[dict]:
    headers = _headers(sheet)
    missing = sorted(REQUIRED - headers.keys())
    if missing:
        raise ValueError(f"worksheet {sheet.title!r} is missing columns: {', '.join(missing)}")
    return [
        {name: row[column - 1] for name, column in headers.items()}
        for row in sheet.iter_rows(min_row=2, values_only=True)
    ]


def validate_scoring_inputs(workbook) -> None:
    """Reject duplicated VINs whose local-price evidence differs between rows."""
    scoring_inputs = {}
    fields = sorted(REQUIRED - {"vin"})
    for sheet in workbook.worksheets:
        if not {"vin", "price"} <= _headers(sheet).keys():
            continue
        for row in _sheet_rows(sheet):
            vin = _vin(row.get("vin"))
            if not vin:
                continue
            values = tuple(row.get(field) for field in fields)
            if vin in scoring_inputs and scoring_inputs[vin] != values:
                raise ValueError(f"Conflicting scoring inputs for VIN {vin} in {sheet.title!r}; reconcile its rows before continuing")
            scoring_inputs[vin] = values


def _prepared(row: dict, catalog: tuple[dict, ...], run_year: int, vin_counts: Counter):
    vin = _vin(row.get("vin"))
    price = _number(row.get("price"))
    msrp = _number(row.get("currentmsrp"))
    year = _number(row.get("year"))
    miles = _number(row.get("miles"))
    seller_name = _normalized_text(row.get("seller"))
    if (
        not VIN_RE.fullmatch(vin) or vin_counts[vin] != 1 or price is None or price <= 0
        or msrp is None or msrp <= 0 or year is None or int(year) != year
        or miles is None or miles < 0 or not key(row.get("powertrain"))
        or not seller_name or key(seller_name) == "unknowndealer"
    ):
        return None

    current = match_current_vehicle({
        "make": row.get("make"), "model": row.get("model"), "trim": row.get("trim"),
        "body_type": row.get("bodytype"), "powertrain": row.get("powertrain"),
    }, catalog)
    if not current:
        return None
    expected_match = " | ".join(str(current[field]) for field in (
        "year", "make", "model", "body", "trim", "powertrain",
    ))
    if str(row.get("currenttrimmatch") or "").strip() != expected_match:
        return None
    reference_msrp = _number(current["msrp"])
    if reference_msrp is None or not math.isclose(msrp, reference_msrp, abs_tol=0.01):
        return None
    drive = _resolved_drive(row.get("drivetrain"), current["drivetrain"])
    if not drive:
        return None

    seller = "\x1f".join((_normalized_text(row.get("seller")),
                           _normalized_text(row.get("city")),
                           _normalized_text(row.get("state"))))
    certification = "yes" if key(row.get("certified")) == "yes" else "not_reported"
    group = (
        int(current["year"]), key(current["make"]), key(current["model"]), key(current["body"]),
        key(current["trim"]), key(current["powertrain"]), key(current["drivetrain"]),
        reference_msrp, key(row.get("powertrain")), drive, certification,
    )
    return {
        "vin": vin, "price": price, "msrp": msrp, "age": max(0, run_year - int(year)),
        "miles": miles, "seller": seller, "group": group,
    }


def calculate_scores(rows: list[dict], catalog: tuple[dict, ...], run_year: int) -> dict[str, float | None]:
    """Return numeric percentage-point scores keyed by VIN; unsupported rows map to None."""
    vins = [_vin(row.get("vin")) for row in rows]
    counts = Counter(vins)
    scores = {vin: None for vin in vins if vin}
    groups = defaultdict(list)
    for row in rows:
        car = _prepared(row, catalog, run_year, counts)
        if car:
            groups[car["group"]].append(car)

    for cars in groups.values():
        by_seller = defaultdict(list)
        for car in cars:
            by_seller[car["seller"]].append(car)
        seller_ages = [median(car["age"] for car in seller_cars) for seller_cars in by_seller.values()]
        seller_miles = [median(car["miles"] for car in seller_cars) for seller_cars in by_seller.values()]
        age_scale = max(_percentile(seller_ages, 0.75) - _percentile(seller_ages, 0.25), 1.0)
        mile_scale = max(_percentile(seller_miles, 0.75) - _percentile(seller_miles, 0.25), 10_000.0)

        for target in cars:
            representatives = {}
            for peer in cars:
                if peer["vin"] == target["vin"]:
                    continue
                distance = (
                    abs(target["age"] - peer["age"]) / age_scale
                    + abs(target["miles"] - peer["miles"]) / mile_scale
                )
                candidate = (distance, peer["vin"], peer)
                previous = representatives.get(peer["seller"])
                if previous is None or candidate[:2] < previous[:2]:
                    representatives[peer["seller"]] = candidate

            nearest = sorted(
                ((distance, seller, vin, peer) for seller, (distance, vin, peer)
                 in representatives.items()),
                key=lambda item: item[:3],
            )[:PEER_COUNT]
            if len(nearest) < PEER_COUNT or nearest[-1][0] > MAX_DISTANCE:
                continue
            peers = [item[3] for item in nearest]
            if not (
                min(peer["age"] for peer in peers) <= target["age"] <= max(peer["age"] for peer in peers)
                and min(peer["miles"] for peer in peers) <= target["miles"] <= max(peer["miles"] for peer in peers)
            ):
                continue
            fair_value = median(peer["price"] * target["msrp"] / peer["msrp"] for peer in peers)
            if fair_value <= 0:
                continue
            score = round(100.0 * (1.0 - target["price"] / fair_value), 2)
            scores[target["vin"]] = 0.0 if score == -0.0 else score
    return scores


def _extend_filter(sheet, score_column: int) -> None:
    if sheet.auto_filter.ref:
        min_col, min_row, max_col, max_row = range_boundaries(sheet.auto_filter.ref)
        sheet.auto_filter.ref = (
            f"{get_column_letter(min_col)}{min_row}:"
            f"{get_column_letter(max(max_col, score_column))}{max(max_row, sheet.max_row)}"
        )
    else:
        sheet.auto_filter.ref = f"A1:{get_column_letter(score_column)}{sheet.max_row}"


def _add_shortlist_format(sheet, score_column: int) -> None:
    if sheet.max_row < 2:
        return
    shortlist_rule = None
    for conditional_format in sheet.conditional_formatting:
        for rule in sheet.conditional_formatting[conditional_format]:
            if any("$A2=1" in formula.replace(" ", "") for formula in (rule.formula or ())):
                shortlist_rule = copy(rule)
                break
        if shortlist_rule:
            break
    if shortlist_rule:
        letter = get_column_letter(score_column)
        sheet.conditional_formatting.add(f"{letter}2:{letter}{sheet.max_row}", shortlist_rule)


def add_deal_edge(
    path: Path, *, catalog: tuple[dict, ...] | None = None, run_year: int | None = None,
) -> tuple[int, int, list[str]]:
    workbook = load_workbook(path, data_only=False)
    temporary = path.with_name(f".{path.stem}.deal-edge-tmp{path.suffix}")
    try:
        if "All" not in workbook.sheetnames:
            raise ValueError("candidate workbook has no 'All' worksheet")
        rows = _sheet_rows(workbook["All"])
        validate_scoring_inputs(workbook)
        if catalog is None:
            catalog, _ = load_reference()
        scores = calculate_scores(rows, catalog, run_year if run_year is not None else _run_year(path))

        processed = []
        score_key = key(COLUMN)
        for sheet in workbook.worksheets:
            headers = _headers(sheet)
            if not {"vin", "price"} <= headers.keys():
                continue
            new_column = score_key not in headers
            score_column = headers.get(score_key, sheet.max_column + 1)
            if new_column:
                header = sheet.cell(1, score_column)
                prior = sheet.cell(1, max(1, score_column - 1))
                header._style = copy(prior._style)
                header.alignment = copy(prior.alignment)
                header.value = COLUMN
                header.comment = Comment(
                    "Positive = ask below the supported five-peer median; negative = above it; blank = insufficient support.",
                    "Deal Edge",
                )
                _add_shortlist_format(sheet, score_column)
            vin_column = headers["vin"]
            price_column = headers["price"]
            for row_number in range(2, sheet.max_row + 1):
                cell = sheet.cell(row_number, score_column)
                if new_column:
                    cell._style = copy(sheet.cell(row_number, price_column)._style)
                cell.number_format = "0.0"
                cell.value = scores.get(_vin(sheet.cell(row_number, vin_column).value))
            sheet.column_dimensions[get_column_letter(score_column)].width = 15
            _extend_filter(sheet, score_column)
            processed.append(sheet.title)

        if "All" not in processed:
            raise ValueError("worksheet 'All' has no usable VIN and price columns")
        temporary.unlink(missing_ok=True)
        workbook.save(temporary)
        os.replace(temporary, path)
        return sum(score is not None for score in scores.values()), len(rows), processed
    finally:
        workbook.close()
        temporary.unlink(missing_ok=True)


def self_test() -> None:
    from search import write_workbook

    catalog = ({
        "year": 2026, "make": "Test", "model": "Model", "body": "SUV", "trim": "Base",
        "powertrain": "Gas", "drivetrain": "AWD", "msrp": 30_000,
    },)
    match = "2026 | Test | Model | SUV | Base | Gas"

    def car(number, seller, year, miles, price, *, certified="", drivetrain="4WD"):
        return {
            "price": price, "current_msrp": 30_000, "current_trim_match": match,
            "year": year, "make": "Test", "model": "Model", "trim": "Base", "miles": miles,
            "body_type": "SUV", "powertrain": "Gas", "drivetrain": drivetrain,
            "certified": certified, "seller": seller, "city": "Town", "state": "NY",
            "vin": f"1TEST{number:012d}", "listing_url": f"https://example.test/{number}",
        }

    cars = [
        car(1, "Dealer A", 2022, 40_000, 18_000),
        car(2, " dealer   a ", 2022, 41_000, 25_000),
        car(3, "Dealer B", 2021, 30_000, 20_000),
        car(4, "Dealer C", 2022, 35_000, 21_000),
        car(5, "Dealer D", 2022, 45_000, 22_000),
        car(6, "Dealer E", 2023, 50_000, 23_000),
        car(7, "Dealer F", 2023, 40_000, 24_000),
        car(8, "Dealer G", 2022, 40_000, 10_000, certified="yes"),
        car(9, "Dealer H", 2022, 40_000, 10_000, drivetrain="FWD"),
    ]
    assert _percentile([0, 10, 20, 30], 0.25) == 7.5
    assert _resolved_drive("4WD", "AWD") == "AWD"
    assert _resolved_drive("4x4", "AWD") is None
    assert _run_year(Path("candidates-Dealer-Test-2026-08-18_120000.xlsx")) == 2026

    with TemporaryDirectory() as directory:
        source = Path(directory) / "candidates-Dealer-Test-2026-08-18_120000.xlsx"
        write_workbook(source, cars, makes=("Test",))
        workbook = load_workbook(source)
        notes = workbook.create_sheet("Notes")
        notes["A1"] = "preserve me"
        workbook.save(source)
        workbook.close()

        supported, total, sheets = add_deal_edge(source, catalog=catalog, run_year=2026)
        assert total == 9 and supported >= 1 and sheets == ["All", "Test"]
        result = load_workbook(source, data_only=False)
        assert result.sheetnames == ["All", "Test", "Notes"]
        headers = _headers(result["All"])
        column = headers[key(COLUMN)]
        assert result["All"].cell(2, column).value == 18.18
        assert result["All"].cell(9, column).value is None
        assert result["All"].cell(10, column).value is None
        assert result["Test"].cell(2, _headers(result["Test"])[key(COLUMN)]).value == 18.18
        assert result["All"]["A2"].value == "='Test'!A2"
        assert result["All"].freeze_panes == "B2" and result["Notes"]["A1"].value == "preserve me"
        assert result["All"].cell(2, headers["listingurl"]).hyperlink is not None
        assert result["All"].cell(2, column).number_format == "0.0"
        result.close()

        add_deal_edge(source, catalog=catalog, run_year=2026)
        result = load_workbook(source, read_only=True)
        assert sum(key(cell.value) == key(COLUMN) for cell in result["All"][1]) == 1
        result.close()
    print("Deal Edge self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update a MarketCheck candidate workbook in place with deal_edge_pct.",
    )
    parser.add_argument("workbook", nargs="?", help="candidate .xlsx workbook")
    parser.add_argument("--run-year", type=int, help="override the year parsed from the search timestamp")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    raw_source = args.workbook or input("Candidate workbook path: ").strip()
    source = Path(raw_source.strip().strip("'\"")).expanduser().resolve()
    if source.name.startswith("~$"):
        parser.error("choose the real workbook, not Excel's temporary ~$ lock file")
    if source.suffix.casefold() != ".xlsx" or not source.is_file():
        parser.error(f"not an existing .xlsx file: {source}")
    if args.run_year is not None and not 2000 <= args.run_year <= 2100:
        parser.error("--run-year must be between 2000 and 2100")

    try:
        supported, total, sheets = add_deal_edge(source, run_year=args.run_year)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Updated in place: {source}")
    print(f"Scored: {supported:,}/{total:,}; blank: {total - supported:,}")
    print(f"Worksheets: {', '.join(sheets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
