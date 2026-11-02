#!/usr/bin/env python3
"""Add a CarPlay yes/no/unverified column to a MarketCheck workbook."""

from __future__ import annotations

import argparse
from copy import copy
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from vehicle_reference import factory_carplay, key

BAD_FILL = PatternFill("solid", fgColor="F4CCCC")
BAD_FONT = Font(color="9C0006")
UNVERIFIED_FILL = PatternFill("solid", fgColor="FFF2CC")
NO_FILL = PatternFill()


def filter_workbook(path: Path) -> tuple[int, int, int, list[str]]:
    workbook = load_workbook(path)
    compatible = incompatible = unverified = 0
    processed = []
    try:
        for sheet in workbook.worksheets:
            headers = {key(cell.value): cell.column for cell in sheet[1] if cell.value}
            if not {"year", "make", "model"} <= headers.keys():
                continue
            if "carplay" not in headers:
                sheet.insert_cols(2)
                sheet["B1"]._style = copy(sheet["A1"]._style)
                sheet["B1"].alignment = copy(sheet["A1"].alignment)
                sheet["B1"] = "carplay"
            headers = {key(cell.value): cell.column for cell in sheet[1] if cell.value}
            carplay_column = headers["carplay"]
            carplay_letter = get_column_letter(carplay_column)
            sheet.cell(1, carplay_column).comment = Comment(
                "yes = confirmed factory CarPlay; no = confirmed incompatible; unverified = unsupported or ambiguous.",
                "CarPlay filter",
            )
            sheet.column_dimensions[carplay_letter].width = 11

            sheet_compatible = sheet_incompatible = sheet_unverified = 0
            for row in range(2, sheet.max_row + 1):
                result = factory_carplay(
                    sheet.cell(row, headers["year"]).value,
                    sheet.cell(row, headers["make"]).value,
                    sheet.cell(row, headers["model"]).value,
                    sheet.cell(row, headers.get("trim", 0)).value if headers.get("trim") else "",
                    sheet.cell(row, headers.get("bodytype", 0)).value if headers.get("bodytype") else "",
                    sheet.cell(row, headers.get("powertrain", 0)).value if headers.get("powertrain") else "",
                )
                cell = sheet.cell(row, carplay_column)
                cell._style = copy(sheet.cell(row, 1)._style)
                if result is True:
                    cell.value = "yes"
                    sheet_compatible += 1
                    for row_cell in sheet[row]:
                        if row_cell.fill.fill_type == "solid" and row_cell.fill.fgColor.rgb in {
                            "00F4CCCC", "FFF4CCCC", "00FFF2CC", "FFFFF2CC",
                        }:
                            row_cell.fill = copy(NO_FILL)
                elif result is False:
                    cell.value = "no"
                    sheet_incompatible += 1
                    for row_cell in sheet[row]:
                        row_cell.fill = copy(BAD_FILL)
                else:
                    cell.value = "unverified"
                    sheet_unverified += 1
                    for row_cell in sheet[row]:
                        row_cell.fill = copy(UNVERIFIED_FILL)

            last_column = get_column_letter(sheet.max_column)
            if sheet.max_row >= 2:
                formula = f'${carplay_letter}2="no"'
                rules = [
                    rule for conditional_format in sheet.conditional_formatting
                    for rule in sheet.conditional_formatting[conditional_format]
                ]
                if not any(formula in (rule.formula or ()) for rule in rules):
                    for existing in rules:
                        if existing.priority:
                            existing.priority += 1
                    rule = FormulaRule(
                        formula=[formula], fill=BAD_FILL, font=BAD_FONT, stopIfTrue=True,
                    )
                    rule.priority = 1
                    sheet.conditional_formatting.add(f"A2:{last_column}{sheet.max_row}", rule)
            sheet.auto_filter.ref = f"A1:{last_column}{sheet.max_row}"
            if carplay_column == 2:
                sheet.freeze_panes = "C2"
            compatible += sheet_compatible
            incompatible += sheet_incompatible
            unverified += sheet_unverified
            processed.append(sheet.title)

        if not processed:
            raise ValueError("no worksheet has year, make, and model columns in row 1")
        temporary = path.with_name(f".{path.stem}.carplay-tmp{path.suffix}")
        try:
            workbook.save(temporary)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        workbook.close()
    return compatible, incompatible, unverified, processed


def self_test() -> None:
    assert factory_carplay(2020, "Mazda", "CX-30", "S", "SUV") is False
    assert factory_carplay(2020, "Mazda", "CX-30", "Select", "SUV") is True
    assert factory_carplay(2019, "Mazda", "CX-5", "Sport", "SUV") is False
    assert factory_carplay(2019, "Mazda", "CX-5", "Touring", "SUV") is True
    assert factory_carplay(2021, "Mazda", "Mazda3", "S", "Sedan") is True
    assert factory_carplay(2021, "Mazda", "Mazda3", "2.0", "Sedan") is False
    assert factory_carplay(2019, "Honda", "Civic", "Sport", "Hatchback") is False
    assert factory_carplay(2019, "Honda", "Civic", "EX", "Hatchback") is True
    assert factory_carplay(2021, "Honda", "Civic", "LX", "Sedan") is False
    assert factory_carplay(2022, "Honda", "Civic", "LX", "Sedan") is True
    assert factory_carplay(2022, "Honda", "CR-V", "SE", "SUV") is True
    assert factory_carplay(2020, "Honda", "CR-V", "LX", "SUV") is False
    assert factory_carplay(2021, "Subaru", "Forester", "Base", "SUV") is True
    assert factory_carplay(2019, "Toyota", "Corolla", "LE", "Sedan") is None
    assert factory_carplay(2021, "Toyota", "Camry", "LE", "Sedan") is True

    with TemporaryDirectory() as directory:
        path = Path(directory) / "cars.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "All"
        sheet.append(["shortlist", "year", "make", "model", "trim", "body_type"])
        sheet.append(["='Mazda'!A2", 2020, "Mazda", "CX-30", "S", "SUV"])
        sheet.append([0, 2022, "Honda", "Civic", "LX", "Sedan"])
        sheet.append([0, 2022, "Honda", "Pilot", "EX-L", "SUV"])
        mazda = workbook.create_sheet("Mazda")
        mazda.append(["shortlist", "year", "make", "model", "trim", "body_type"])
        mazda.append([1, 2020, "Mazda", "CX-30", "Select", "SUV"])
        workbook.save(path)
        workbook.close()
        compatible, incompatible, unverified, sheets = filter_workbook(path)
        assert (compatible, incompatible, unverified) == (2, 1, 1)
        assert sheets == ["All", "Mazda"]
        result = load_workbook(path, data_only=False)
        assert result["All"]["A2"].value == "='Mazda'!A2"
        assert result["All"]["B2"].value == "no" and result["All"]["B3"].value == "yes"
        assert result["All"]["B4"].value == "unverified"
        assert result["All"]["A2"].fill.fgColor.rgb in {"00F4CCCC", "FFF4CCCC"}
        assert result["All"]["A4"].fill.fgColor.rgb in {"00FFF2CC", "FFFFF2CC"}
        assert result["Mazda"]["B2"].value == "yes"
        result.close()
    print("CarPlay filter self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Edit a MarketCheck .xlsx workbook in place, adding CarPlay yes/no/unverified labels.",
    )
    parser.add_argument("workbook", nargs="?", help="path to the .xlsx workbook to edit")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    raw_path = args.workbook or input("Excel workbook path: ").strip()
    path = Path(raw_path.strip().strip("'\"")).expanduser().resolve()
    if path.name.startswith("~$"):
        parser.error("choose the real workbook, not Excel's temporary ~$ lock file")
    if path.suffix.casefold() != ".xlsx" or not path.is_file():
        parser.error(f"not an existing .xlsx file: {path}")
    try:
        compatible, incompatible, unverified, sheets = filter_workbook(path)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(f"Updated in place: {path}")
    print(f"Worksheets: {', '.join(sheets)}")
    print(f"CarPlay yes: {compatible:,}; no: {incompatible:,}; unverified: {unverified:,}")
    if unverified:
        print(f"Warning: {unverified:,} unsupported or ambiguous rows remain unverified", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
