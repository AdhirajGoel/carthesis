#!/usr/bin/env python3
"""Run the manual MarketCheck used-car search.

Usage:
  API key: $MARKETCHECK_API_KEY or macOS Keychain service MARKETCHECK_API_KEY
  python3 search.py --make Honda,Mazda
  python3 search.py --additional-zip 95814
  python3 search.py --model-only Mazda3,CX-5 --max-price 24000
  python3 search.py --make Honda,Mazda --mileage 60000 --min-year 2021 --exclude CRV,CX5
  python3 search.py --self-test
"""

from __future__ import annotations

import argparse
import ssl
import certifi
from copy import copy
from difflib import SequenceMatcher
from functools import partial
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from vehicle_reference import enrich_cars, load_reference, self_test as reference_self_test


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
SHORTLIST_FILE = RESULTS / "Shortlist.xlsx"
STATE_FILE = ROOT / ".state" / "dealer.json"
ROWS = 50
MAX_CALLS = 30
PAGINATION_LIMIT = 500
PRICE_MIN = 10_000
PRICE_MAX = 25_000
MILES_MAX = 60_000
YEAR_MIN = 2022
ZIP = os.environ.get("CARTHESIS_ZIP", "")
RADIUS = 75
DEALER_ENDPOINT = "https://api.marketcheck.com/v2/search/car/active"
MODELS = {
    "Toyota": {
        "Avalon", "Camry", "Corolla", "Crown", "4Runner", "C-HR", "Corolla Cross",
        "Grand Highlander", "Highlander", "Land Cruiser", "Prius", "RAV4", "Sequoia", "Venza",
    },
    "Subaru": {"Legacy", "WRX", "Ascent", "Crosstrek", "Forester", "Impreza", "Outback"},
    "Mazda": {
        "Mazda3", "Mazda6", "CX-3", "CX-30", "CX-5", "CX-50", "CX-70", "CX-9", "CX-90",
    },
    "Honda": {"Accord", "Civic", "Insight", "CR-V", "HR-V", "Passport", "Pilot"},
    "Tesla": {"Model 3", "Model Y", "Model S"},
}
DEFAULT_EXCLUDED_MODELS = frozenset()
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
CSV_FIELDS = (
    "status", "seller_section", "price", "current_msrp", "msrp_difference",
    "current_trim_match", "carplay", "year", "make", "model", "trim", "miles",
    "distance_miles", "body_type", "powertrain", "fuel_type", "transmission", "drivetrain",
    "exterior_color", "interior_color", "certified", "marketcheck_one_owner_flag",
    "marketcheck_clean_title_flag", "days_on_market", "first_seen", "last_seen", "seller",
    "dealer_type", "city", "state", "source", "vin", "listing_url",
)
WORKBOOK_FIELDS = ("shortlist", *CSV_FIELDS)


def load_api_key() -> str:
    if api_key := os.environ.get("MARKETCHECK_API_KEY", "").strip():
        return api_key
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", "MARKETCHECK_API_KEY", "-w"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
    except OSError:
        result = None
    if result and result.returncode == 0 and (api_key := result.stdout.strip()):
        return api_key
    raise RuntimeError(
        "No API key found in $MARKETCHECK_API_KEY or macOS Keychain service MARKETCHECK_API_KEY"
    )


def selected_models(value) -> frozenset[str]:
    return frozenset({value} if isinstance(value, str) else value or ())


def search_params(
    make: str, mileage: int = MILES_MAX, min_year: int = YEAR_MIN, excluded=(),
    model_only=(), max_price: int = PRICE_MAX, zip_code: str = ZIP,
) -> dict[str, str]:
    model_only = selected_models(model_only)
    models = (
        model_only if model_only else MODELS[make] - DEFAULT_EXCLUDED_MODELS
    ) - set(excluded)
    models &= MODELS[make]
    if not models:
        raise ValueError(f"no models remain for {make}")
    year_floor = max(min_year, 2023) if make == "Tesla" else min_year
    return {
        "append_api_key": "false",
        "country": "us",
        "car_type": "used",
        "make": make,
        "model": ",".join(sorted(models)),
        "year_range": f"{year_floor}-{datetime.now().year + 1}",
        "price_range": f"{PRICE_MIN}-{max_price}",
        "miles_range": f"0-{mileage}",
        "powertrain_type": "Combustion,BEV,HEV,MHEV",
        "transmission": "Automatic,CVT",
        "zip": zip_code,
        "radius": str(RADIUS),
        "has_price": "true",
        "has_miles": "true",
        "dedup": "true",
        "include_non_vin_listings": "false",
        "sort_by": "price",
        "sort_order": "asc",
        "rows": str(ROWS),
    }


def request_page(
    endpoint: str, api_key: str, start: int, make: str, *,
    mileage: int = MILES_MAX, min_year: int = YEAR_MIN, excluded=(),
    model_only=(), max_price: int = PRICE_MAX, zip_code: str = ZIP,
) -> dict:
    params = search_params(make, mileage, min_year, excluded, model_only, max_price, zip_code) | {
        "api_key": api_key, "start": str(start),
    }
    request = Request(
        f"{endpoint}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "manual-marketcheck-search/1"},
    )
    try:
        with urlopen(request, timeout=30, context=ssl.create_default_context(cafile=certifi.where())) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        message = ""
        try:
            body = json.loads(error.read(4096))
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
        except (ValueError, OSError):
            pass
        message = message.replace(api_key, "[redacted]")
        raise RuntimeError(f"MarketCheck HTTP {error.code}{': ' + message if message else ''}") from None
    except URLError as error:
        raise RuntimeError(f"Could not reach MarketCheck: {error.reason}") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("listings", []), list):
        raise RuntimeError("MarketCheck returned an unexpected response")
    try:
        payload["num_found"] = int(payload.get("num_found", 0))
    except (TypeError, ValueError):
        raise RuntimeError("MarketCheck returned an invalid result count") from None
    return payload


def fetch_all(
    api_key: str, request=request_page, makes=None, model_only=(), zip_codes=(ZIP,),
) -> tuple[list[dict], int]:
    model_only = selected_models(model_only)
    searches = []
    for zip_code in zip_codes:
        for make in (makes or MODELS):
            scoped = model_only & MODELS[make]
            if model_only and not scoped:
                continue
            searches.append({
                "section": "dealer",
                "make": make,
                "model": next(iter(scoped)) if len(scoped) == 1 else None,
                "models": scoped or None,
                "zip": zip_code,
                "endpoint": DEALER_ENDPOINT,
                "listings": [],
                "total": None,
                "next_start": 0,
                "done": False,
                "complete": False,
                "error": "",
            })
    calls = 0

    def fetch(search: dict) -> None:
        nonlocal calls
        calls += 1
        try:
            payload = request(
                search["endpoint"], api_key, search["next_start"], search["make"],
                zip_code=search["zip"],
            )
        except RuntimeError as error:
            search["error"] = str(error)
            search["done"] = True
            return
        page = payload["listings"]
        search["total"] = payload["num_found"]
        search["listings"].extend(page)
        search["next_start"] += ROWS
        search["complete"] = search["next_start"] >= search["total"]
        search["done"] = search["complete"] or len(page) < ROWS
        if len(page) < ROWS and not search["complete"]:
            search["error"] = "MarketCheck stopped before the reported result count"
        elif search["next_start"] >= PAGINATION_LIMIT and not search["complete"]:
            search["done"] = True
            search["error"] = f"pagination limit reached after {PAGINATION_LIMIT} rows"

    for search in searches:
        if calls < MAX_CALLS:
            fetch(search)

    while calls < MAX_CALLS:
        pending = [search for search in searches if not search["done"]]
        if not pending:
            break
        search = max(
            pending,
            key=lambda item: (item["total"] or 0) - len(item["listings"]),
        )
        fetch(search)

    return searches, calls


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def text(value) -> str:
    return str(value).strip() if value is not None else ""


def yes_no_unknown(value) -> str:
    value = text(value).casefold()
    if value in {"1", "true", "yes"}:
        return "yes"
    if value in {"0", "false", "no"}:
        return "no"
    return "unknown"


def normalize_listing(
    raw: dict, section: str, mileage: int = MILES_MAX, min_year: int = YEAR_MIN, excluded=(),
    model_only=(), max_price: int = PRICE_MAX,
) -> dict | None:
    if not isinstance(raw, dict):
        return None
    one_owner = yes_no_unknown(raw.get("carfax_1_owner"))
    clean_title = yes_no_unknown(raw.get("carfax_clean_title"))
    build = raw.get("build") if isinstance(raw.get("build"), dict) else {}
    vin = text(raw.get("vin")).upper()
    price = number(raw.get("price"))
    miles = number(raw.get("miles"))
    year = number(build.get("year"))
    if not VIN_RE.fullmatch(vin) or price is None or miles is None or year is None:
        return None
    if not PRICE_MIN <= price <= max_price or not 0 <= miles <= mileage or year < min_year:
        return None

    make_lookup = {make.casefold(): make for make in MODELS}
    make = make_lookup.get(text(build.get("make")).casefold())
    if not make:
        return None
    if make == "Tesla" and year < 2023:
        return None
    model_lookup = {model.casefold(): model for model in MODELS[make]}
    model = model_lookup.get(text(build.get("model")).casefold())
    model_only = selected_models(model_only)
    if (
        not model or model in excluded or (model_only and model not in model_only)
        or (not model_only and model in DEFAULT_EXCLUDED_MODELS)
    ):
        return None
    transmission = text(build.get("transmission"))
    transmission_key = transmission.casefold()
    if "manual" in transmission_key or not any(
        marker in transmission_key for marker in ("automatic", "cvt", "continuously variable", "a/t")
    ):
        return None
    powertrain = text(build.get("powertrain_type"))
    if powertrain.casefold() not in {"combustion", "bev", "hev", "mhev"}:
        return None

    dealer = raw.get("dealer") if isinstance(raw.get("dealer"), dict) else {}
    mc_dealer = raw.get("mc_dealership") if isinstance(raw.get("mc_dealership"), dict) else {}
    seller = text(dealer.get("name") or mc_dealer.get("name") or raw.get("source"))
    location = dealer or mc_dealer
    distance = number(raw.get("dist"))
    days = number(raw.get("dom_active", raw.get("dom")))
    listing_url = text(raw.get("vdp_url"))
    listing_key = vin or text(raw.get("id")) or listing_url
    if not listing_key:
        return None
    return {
        "_key": listing_key,
        "status": "",
        "seller_section": section,
        "price": int(price) if price.is_integer() else price,
        "year": int(year),
        "make": make,
        "model": model,
        "trim": text(build.get("trim")),
        "miles": int(miles) if miles.is_integer() else miles,
        "distance_miles": round(distance, 1) if distance is not None else "",
        "body_type": text(build.get("body_type")),
        "powertrain": powertrain,
        "fuel_type": text(build.get("fuel_type")),
        "transmission": transmission,
        "drivetrain": text(build.get("drivetrain")),
        "exterior_color": text(raw.get("exterior_color")),
        "interior_color": text(raw.get("interior_color")),
        "certified": "yes" if raw.get("is_certified") == 1 else "no",
        "marketcheck_one_owner_flag": one_owner,
        "marketcheck_clean_title_flag": clean_title,
        "days_on_market": int(days) if days is not None and days.is_integer() else (days or ""),
        "first_seen": text(raw.get("first_seen_at_date") or raw.get("first_seen_at_mc_date")),
        "last_seen": text(raw.get("last_seen_at_date")),
        "seller": seller or ("Private seller" if section == "private" else "Unknown dealer"),
        "dealer_type": text(dealer.get("dealer_type") or mc_dealer.get("dealer_type")),
        "city": text(location.get("city")),
        "state": text(location.get("state")),
        "source": text(raw.get("source")),
        "vin": vin,
        "listing_url": listing_url,
    }


def sort_key(car: dict) -> tuple:
    distance = number(car.get("distance_miles"))
    return (
        number(car.get("price")) or float("inf"),
        number(car.get("miles")) or float("inf"),
        -(number(car.get("year")) or 0),
        distance if distance is not None else float("inf"),
    )


def shortlist_key(car: dict) -> str:
    vin = text(car.get("vin")).upper()
    return vin if VIN_RE.fullmatch(vin) else text(car.get("listing_url"))


def load_shortlist_keys(path: Path = SHORTLIST_FILE) -> set[str]:
    if not path.exists():
        return set()
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("Reading the shortlist requires openpyxl") from None

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["All"]
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, ())
        indexes = {str(value).strip().casefold(): index for index, value in enumerate(headers) if value}
        return {
            key for row in rows
            if (key := shortlist_key({
                "vin": row[indexes["vin"]] if "vin" in indexes and indexes["vin"] < len(row) else "",
                "listing_url": row[indexes["listing_url"]]
                if "listing_url" in indexes and indexes["listing_url"] < len(row) else "",
            }))
        }
    except KeyError:
        raise RuntimeError(f"{path.name} has no All worksheet") from None
    finally:
        workbook.close()


def deduplicate(cars: list[dict]) -> list[dict]:
    by_key = {}
    for car in cars:
        key = car.get("_key", car["vin"])
        old = by_key.get(key)
        if old is None or sort_key(car) < sort_key(old):
            by_key[key] = car
    return sorted(by_key.values(), key=sort_key)


def apply_statuses(cars: list[dict], previous: dict, baseline: bool) -> None:
    for car in cars:
        old = previous.get(car.get("_key", car["vin"]))
        old_price = number(old.get("price")) if isinstance(old, dict) else None
        if baseline:
            car["status"] = "BASELINE"
        elif old_price is None:
            car["status"] = "NEW"
        elif car["price"] < old_price:
            car["status"] = f"PRICE DROP ${old_price - car['price']:,.0f}"
        elif car["price"] > old_price:
            car["status"] = f"PRICE INCREASE ${car['price'] - old_price:,.0f}"
        else:
            car["status"] = "SEEN"


def load_state() -> tuple[bool, dict]:
    if not STATE_FILE.exists():
        return False, {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot read {STATE_FILE.name}: {error}") from None
    vehicles = data.get("vehicles") if isinstance(data, dict) else None
    if not isinstance(vehicles, dict):
        raise RuntimeError(f"{STATE_FILE.name} has an invalid format; it was left unchanged")
    return True, vehicles


def update_snapshot(previous: dict, cars: list[dict], searches: list[dict]) -> tuple[dict, list[dict]]:
    snapshot = dict(previous)
    for car in cars:
        snapshot[car.get("_key", car["vin"])] = {
            key: value for key, value in car.items() if key not in {"status", "_key"}
        }

    removed = []
    for search in searches:
        if not search["complete"] or any(
            not candidate["complete"]
            and candidate["section"] == search["section"]
            and candidate.get("make") == search.get("make")
            and candidate.get("model") == search.get("model")
            and candidate.get("models") == search.get("models")
            for candidate in searches
        ):
            continue
        section = search["section"]
        make = search.get("make")
        model = search.get("model")
        models = search.get("models") or ({model} if model else None)
        seen = {
            car.get("_key", car["vin"]) for car in cars
            if car["seller_section"] == section and (make is None or car["make"] == make)
            and (models is None or car["model"] in models)
        }
        for vin, old in list(snapshot.items()):
            if (
                old.get("seller_section") == section
                and (make is None or old.get("make") == make)
                and (models is None or old.get("model") in models)
                and vin not in seen
            ):
                gone = dict(old)
                gone["status"] = "NO LONGER MATCHED"
                removed.append(gone)
                del snapshot[vin]
    return snapshot, sorted(removed, key=sort_key)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def spreadsheet_value(value):
    # Third-party listing text must not become an executable spreadsheet formula.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def write_workbook(path: Path, cars: list[dict], makes=None, extra_fields=()) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.formatting.rule import FormulaRule
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        raise RuntimeError("Creating the Excel workbook requires openpyxl") from None

    workbook = Workbook()
    workbook.calculation.calcMode = "auto"
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.remove(workbook.active)
    selected_makes = MODELS if makes is None else makes
    fields = (*WORKBOOK_FIELDS, *(field for field in extra_fields if field not in WORKBOOK_FIELDS))
    make_groups = [
        (make, [car for car in cars if car["make"] == make]) for make in selected_makes
    ]
    make_rows = {
        id(car): (make, row)
        for make, rows in make_groups
        for row, car in enumerate(rows, 2)
    }
    groups = [("All", cars), *make_groups]
    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    selected_fill = PatternFill(
        fill_type="solid", start_color="FFC6EFCE", end_color="FFC6EFCE",
    )
    widths = {
        "shortlist": 11, "status": 20, "seller_section": 14, "price": 12,
        "current_msrp": 14, "msrp_difference": 17, "current_trim_match": 52,
        "carplay": 10,
        "year": 9, "make": 12,
        "model": 18, "trim": 20, "miles": 12, "distance_miles": 15, "body_type": 14,
        "powertrain": 14, "fuel_type": 18, "transmission": 24, "drivetrain": 12,
        "exterior_color": 16, "interior_color": 16, "certified": 10,
        "marketcheck_one_owner_flag": 30, "marketcheck_clean_title_flag": 30,
        "days_on_market": 15, "first_seen": 23, "last_seen": 23, "seller": 28,
        "dealer_type": 16, "city": 18, "state": 9, "source": 24, "vin": 20,
        "listing_url": 48, "deal_edge_pct": 15,
        "kbb_buyer_value": 18, "kbb_value_type": 22, "price_vs_kbb": 16,
        "kbb_checked_at": 18, "kbb_source_url": 48,
    }
    for name, rows in groups:
        sheet = workbook.create_sheet(name)
        sheet.sheet_view.showGridLines = False
        sheet.freeze_panes = "B2"
        sheet.append(fields)
        for car in rows:
            sheet.append([spreadsheet_value(car.get(field, 0 if field == "shortlist" else "")) for field in fields])
            if name == "All":
                make, row = make_rows[id(car)]
                sheet.cell(sheet.max_row, 1, f"='{make}'!A{row}")
        for cell in sheet[1]:
            cell.fill = header_fill
            cell.font = header_font
        sheet.auto_filter.ref = sheet.dimensions
        last_row = max(2, sheet.max_row)
        last_column = get_column_letter(len(fields))
        shortlist_validation = DataValidation(
            type="whole", operator="between", formula1="0", formula2="1", allow_blank=True,
        )
        shortlist_validation.error = "Enter 0 or 1."
        shortlist_validation.errorTitle = "Shortlist value"
        shortlist_validation.showErrorMessage = True
        if name != "All":
            sheet.add_data_validation(shortlist_validation)
            shortlist_validation.add(f"A2:A{last_row}")
        sheet.conditional_formatting.add(
            f"A2:{last_column}{last_row}",
            FormulaRule(formula=["$A2=1"], fill=selected_fill),
        )
        for index, field in enumerate(fields, 1):
            sheet.column_dimensions[get_column_letter(index)].width = widths.get(field, 14)
        for field in ("price", "current_msrp", "msrp_difference", "kbb_buyer_value", "price_vs_kbb"):
            if field not in fields:
                continue
            for cell in sheet[get_column_letter(fields.index(field) + 1)][1:]:
                cell.number_format = '$#,##0'
        for cell in sheet[get_column_letter(fields.index("year") + 1)][1:]:
            cell.number_format = '0'
        for field in ("miles", "days_on_market"):
            for cell in sheet[get_column_letter(fields.index(field) + 1)][1:]:
                cell.number_format = '#,##0'
        distance_column = get_column_letter(fields.index("distance_miles") + 1)
        for cell in sheet[distance_column][1:]:
            cell.number_format = '0.0'
        if "deal_edge_pct" in fields:
            for cell in sheet[get_column_letter(fields.index("deal_edge_pct") + 1)][1:]:
                cell.number_format = '0.0'
        if "kbb_buyer_value" in fields:
            kbb_column = get_column_letter(fields.index("kbb_buyer_value") + 1)
            type_column = fields.index("kbb_value_type") + 1
            delta_column = fields.index("price_vs_kbb") + 1
            for row in range(2, sheet.max_row + 1):
                sheet.cell(row, type_column).value = (
                    f'=IF({kbb_column}{row}="","",IF($C{row}="private",'
                    '"Private Party Value","Fair Purchase Price"))'
                )
                sheet.cell(row, delta_column).value = (
                    f'=IF({kbb_column}{row}="","",$D{row}-{kbb_column}{row})'
                )
        for field in ("listing_url", "kbb_source_url"):
            if field not in fields:
                continue
            link_column = get_column_letter(fields.index(field) + 1)
            for cell in sheet[link_column][1:]:
                if isinstance(cell.value, str) and cell.value.startswith(("http://", "https://")):
                    cell.hyperlink = cell.value
                    cell.style = "Hyperlink"

    temporary = path.with_suffix(path.suffix + ".tmp")
    workbook.save(temporary)
    temporary.replace(path)


def find_master_workbook(results: Path = RESULTS) -> Path:
    matches = sorted(results.glob("candidates-Master*.xlsx"))
    if not matches:
        results.mkdir(parents=True, exist_ok=True)
        path = results / "candidates-Master.xlsx"
        write_workbook(path, [], tuple(MODELS))
        return path
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise RuntimeError(f"Multiple master workbooks found; keep one in {results}: {names}")
    return matches[0]


def find_reference_workbook() -> Path:
    from vehicle_reference import REFERENCE_WORKBOOK
    return REFERENCE_WORKBOOK


def append_new_cars(path: Path, cars: list[dict]) -> list[dict]:
    try:
        from openpyxl import load_workbook
        from openpyxl.formula.translate import Translator
        from openpyxl.utils import get_column_letter, range_boundaries
        from openpyxl.workbook.properties import CalcProperties
    except ImportError:
        raise RuntimeError("Updating the master Excel workbook requires openpyxl") from None

    workbook = load_workbook(path)
    try:
        if "All" not in workbook.sheetnames:
            raise RuntimeError(f"{path.name} has no All worksheet")
        all_sheet = workbook["All"]
        all_headers = {
            text(cell.value).casefold(): cell.column for cell in all_sheet[1] if cell.value
        }
        if "vin" not in all_headers:
            raise RuntimeError(f"{path.name} has no vin column in the All worksheet")
        vin_column = all_headers["vin"]
        seen_vins = {
            text(all_sheet.cell(row, vin_column).value).upper()
            for row in range(2, all_sheet.max_row + 1)
        }
        new_cars = []
        for car in cars:
            vin = text(car.get("vin")).upper()
            if not VIN_RE.fullmatch(vin) or vin in seen_vins:
                continue
            added = dict(car, vin=vin, status="NEW")
            new_cars.append(added)
            seen_vins.add(vin)
        if not new_cars:
            return []

        missing_sheets = sorted({car["make"] for car in new_cars} - set(workbook.sheetnames))
        if missing_sheets:
            raise RuntimeError(
                f"{path.name} has no worksheet for {', '.join(missing_sheets)}"
            )

        def extended_sqref(sqref, old_row: int, new_row: int) -> str:
            ranges = []
            for cell_range in sqref.ranges:
                updated = copy(cell_range)
                if updated.max_row == old_row:
                    updated.max_row = new_row
                ranges.append(str(updated))
            return " ".join(ranges)

        def append_row(sheet, car: dict, shortlist_formula: str | None = None) -> int:
            old_row = sheet.max_row
            new_row = old_row + 1
            headers = [text(cell.value).casefold() for cell in sheet[1]]
            for column, field in enumerate(headers, 1):
                source = sheet.cell(old_row, column)
                target = sheet.cell(new_row, column)
                if source.has_style:
                    target._style = copy(source._style)
                if source.data_type == "f":
                    try:
                        target.value = Translator(
                            source.value, origin=source.coordinate,
                        ).translate_formula(target.coordinate)
                    except Exception as error:
                        raise RuntimeError(
                            f"Cannot extend formula {source.coordinate} in {sheet.title}: {error}"
                        ) from None
                elif field:
                    target.value = spreadsheet_value(car.get(field, 0 if field == "shortlist" else ""))
            if shortlist_formula is not None:
                target = sheet.cell(new_row, headers.index("shortlist") + 1)
                target.value = shortlist_formula
            for field in ("listing_url", "kbb_source_url", "history_report_url"):
                if field not in headers:
                    continue
                cell = sheet.cell(new_row, headers.index(field) + 1)
                if isinstance(cell.value, str) and cell.value.startswith(("http://", "https://")):
                    cell.hyperlink = cell.value
                    cell.style = "Hyperlink"
            sheet.row_dimensions[new_row].height = sheet.row_dimensions[old_row].height

            for table in sheet.tables.values():
                min_col, min_row, max_col, max_row = range_boundaries(table.ref)
                if max_row == old_row:
                    table.ref = (
                        f"{get_column_letter(min_col)}{min_row}:"
                        f"{get_column_letter(max_col)}{new_row}"
                    )
                    if table.autoFilter:
                        table.autoFilter.ref = table.ref
            for validation in sheet.data_validations.dataValidation:
                validation.sqref = extended_sqref(validation.sqref, old_row, new_row)
            formatting_updates = []
            for formatting in list(sheet.conditional_formatting):
                new_sqref = extended_sqref(formatting.sqref, old_row, new_row)
                if new_sqref != str(formatting.sqref):
                    formatting_updates.append((formatting, new_sqref))
            for formatting, new_sqref in formatting_updates:
                rules = list(sheet.conditional_formatting[formatting])
                del sheet.conditional_formatting[str(formatting.sqref)]
                for rule in rules:
                    sheet.conditional_formatting.add(new_sqref, rule)
            return new_row

        for car in new_cars:
            make = car["make"]
            make_row = append_row(workbook[make], car)
            escaped_make = make.replace("'", "''")
            append_row(all_sheet, car, f"='{escaped_make}'!A{make_row}")

        if workbook.calculation is None:
            workbook.calculation = CalcProperties()
        workbook.calculation.calcMode = "auto"
        workbook.calculation.fullCalcOnLoad = True
        workbook.calculation.forceFullCalc = True
        temporary = path.with_suffix(path.suffix + ".tmp")
        workbook.save(temporary)
        temporary.replace(path)
        return new_cars
    finally:
        workbook.close()


def run(
    makes=None, mileage=MILES_MAX, min_year=YEAR_MIN, excluded=(), model_only=(),
    max_price: int = PRICE_MAX, additional_zip: str | None = None,
) -> int:
    if not re.fullmatch(r"\d{5}", ZIP):
        raise ValueError("Set --zip or CARTHESIS_ZIP to your five-digit search ZIP")
    if not 1 <= RADIUS <= 100 or not 1 <= MAX_CALLS <= 100:
        raise ValueError("Radius and max calls must be between 1 and 100")
    reference_path = find_reference_workbook()
    if reference_path.suffix == ".json" and not json.loads(reference_path.read_text()).get("reviewed_for_live", False):
        raise ValueError("Research the relevant reference entries and set reviewed_for_live to true before a live search")
    makes = tuple(dict.fromkeys(makes or MODELS))
    excluded = frozenset(excluded)
    model_only = selected_models(model_only)
    RESULTS.mkdir(parents=True, exist_ok=True)
    workbook_path = find_master_workbook()
    api_key = load_api_key()

    reference = load_reference(find_reference_workbook())
    shortlist_keys = load_shortlist_keys()
    state_exists, previous = load_state()
    request = partial(
        request_page, mileage=mileage, min_year=min_year, excluded=excluded,
        model_only=model_only, max_price=max_price,
    )
    zip_codes = tuple(dict.fromkeys(zip_code for zip_code in (ZIP, additional_zip) if zip_code))
    searches, calls = fetch_all(
        api_key, request=request, makes=makes, model_only=model_only, zip_codes=zip_codes,
    )
    if not any(search["total"] is not None for search in searches):
        errors = "; ".join(search["error"] for search in searches if search["error"])
        raise RuntimeError(f"All MarketCheck searches failed: {errors}")

    normalized = []
    rejected = 0
    for search in searches:
        for raw in search["listings"]:
            car = normalize_listing(
                raw, search["section"], mileage, min_year, excluded, model_only, max_price,
            )
            if car is None:
                rejected += 1
            else:
                normalized.append(car)
    cars = deduplicate(normalized)
    duplicates = len(normalized) - len(cars)
    enrichment = enrich_cars(cars, reference)
    for car in cars:
        car["shortlist"] = int(shortlist_key(car) in shortlist_keys)
    apply_statuses(cars, previous, not state_exists)
    snapshot, _ = update_snapshot(previous, cars, searches)

    run_at = datetime.now().astimezone()
    new_cars = append_new_cars(workbook_path, cars)
    atomic_text(
        STATE_FILE,
        json.dumps({"last_run": run_at.isoformat(), "vehicles": snapshot}, indent=2, sort_keys=True) + "\n",
    )

    if new_cars:
        print(f"Appended {len(new_cars):,} new VINs to {workbook_path}")
    else:
        print(f"No new VINs found; {workbook_path} was left unchanged")
    print(f"Found {len(cars):,} matching candidates; {len(cars) - len(new_cars):,} already in master")
    print(f"Filters: model year {min_year}+, under {mileage:,} miles, up to ${max_price:,}")
    if "Tesla" in makes and min_year < 2023:
        print("Tesla minimum model year: 2023")
    print(f"Search areas: {', '.join(zip_codes)} ({RADIUS} miles each)")
    if model_only:
        print(f"Models only: {', '.join(sorted(model_only))}")
    if excluded:
        print(f"Excluded models: {', '.join(sorted(excluded))}")
    print(f"Preselected {sum(car['shortlist'] for car in cars):,} existing shortlist entries")
    print(
        f"Current MSRP matched {enrichment['matched']:,} of {len(cars):,}; "
        f"CarPlay yes for {sum(car['carplay'] == 'yes' for car in cars):,}"
    )
    if enrichment["unmatched"]:
        print(
            f"Warning: {enrichment['unmatched']:,} rows had no safe current-trim MSRP match",
            file=sys.stderr,
        )
        for example in enrichment["unmatched_examples"]:
            print(f"  Unmatched: {example}", file=sys.stderr)
    if enrichment["carplay_unverified"]:
        print(
            f"Warning: {enrichment['carplay_unverified']:,} CarPlay rows remain unverified",
            file=sys.stderr,
        )
    print(f"Used {calls} of {MAX_CALLS} allowed calls; rejected {rejected:,}; deduplicated {duplicates:,}")
    for search in searches:
        total = "unknown" if search["total"] is None else f"{search['total']:,}"
        coverage = "complete" if search["complete"] else "partial"
        label = f"{search['section']}/{search['make']}/{search['zip']}"
        print(f"{label}: {len(search['listings']):,} of {total} ({coverage})")
        if search["error"]:
            print(f"Warning ({label}): {search['error']}", file=sys.stderr)
    return 0


def self_test() -> None:
    from unittest.mock import patch

    with patch.dict(os.environ, {}, clear=True), patch("subprocess.run") as keychain:
        keychain.return_value = subprocess.CompletedProcess([], 0, "test-key\n", "")
        assert load_api_key() == "test-key"
        assert keychain.call_args.args[0][-3:] == ["-s", "MARKETCHECK_API_KEY", "-w"]
    reference = load_reference(find_reference_workbook())
    with patch("vehicle_reference.load_reference", partial(load_reference, find_reference_workbook())):
        reference_self_test()
    broad = parse_args(["--max-price", "25500"])
    assert broad.makes == list(MODELS) and broad.max_price == 25_500
    assert "Tesla" in broad.makes and MODELS["Tesla"] == {"Model 3", "Model Y", "Model S"}
    assert parse_args(["--additional-zip", "95814"]).additional_zip == "95814"
    assert parse_args(["--make", "Honda,Subaru,Mazda"]).makes == ["Honda", "Subaru", "Mazda"]
    assert parse_args(["--make", "Honda,", "Mazda"]).makes == ["Honda", "Mazda"]
    assert parse_args(["--make", "Honda", "--make", "Mazda"]).makes == ["Honda", "Mazda"]
    assert parse_args(["--make", "Tesla"]).makes == ["Tesla"]
    model_args = parse_args(["--model-only", "crisstrek"])
    assert model_args.makes == ["Subaru"] and model_args.model_only == frozenset({"Crosstrek"})
    impreza_args = parse_args(["--model-only", "Imprezza"])
    assert impreza_args.makes == ["Subaru"] and impreza_args.model_only == frozenset({"Impreza"})
    assert parse_args(["--model-only", "corola cross"]).model_only == frozenset({"Corolla Cross"})
    selected = parse_args(["--model-only", "mazda3,", "cx-5,Civic,RAV4"])
    assert selected.makes == ["Toyota", "Mazda", "Honda"]
    assert selected.model_only == frozenset({"Mazda3", "CX-5", "Civic", "RAV4"})
    args = parse_args([
        "--make", "Honda,Mazda", "--mileage", "60000", "--min-year", "2021",
        "--max-price", "25000", "--exclude", "CRV,cx5,mazda 3",
    ])
    assert args.mileage == 60_000 and args.min_year == 2021 and args.max_price == 25_000
    assert args.excluded_models == frozenset({"CR-V", "CX-5", "Mazda3"})
    spaced_commas = parse_args(["--make", "Honda,Toyota", "--exclude", "Civic,", "RAV4,", "Corolla"])
    assert spaced_commas.excluded_models == frozenset({"Civic", "RAV4", "Corolla"})
    params = search_params("Toyota")
    models = set(params["model"].split(","))
    assert "Corolla Cross" in models
    assert {"Corolla", "Prius"} <= models
    assert params["make"] == "Toyota"
    assert "carfax_1_owner" not in params and "carfax_clean_title" not in params
    assert MAX_CALLS == 30
    assert params["year_range"].startswith("2022-")
    assert params["price_range"] == "10000-25000"
    assert params["miles_range"] == "0-60000"
    custom = search_params("Honda", 60_000, 2021, {"CR-V"})
    assert custom["miles_range"] == "0-60000" and custom["year_range"].startswith("2021-")
    assert "CR-V" not in custom["model"].split(",")
    assert search_params("Subaru", model_only={"Crosstrek"})["model"] == "Crosstrek"
    assert search_params("Mazda", model_only={"Mazda3", "CX-5", "Civic"})["model"] == "CX-5,Mazda3"
    assert "Impreza" in search_params("Subaru")["model"].split(",")
    assert search_params("Subaru", model_only={"Impreza"})["model"] == "Impreza"
    assert search_params("Subaru", max_price=24_000)["price_range"] == "10000-24000"
    tesla_params = search_params("Tesla")
    assert tesla_params["year_range"].startswith("2023-")
    assert tesla_params["price_range"] == "10000-25000" and tesla_params["miles_range"] == "0-60000"
    assert "BEV" in tesla_params["powertrain_type"]

    requested = []
    totals = {"Toyota": 501, "Subaru": 1, "Mazda": 1, "Honda": 1, "Tesla": 1}

    def fake_request(endpoint: str, api_key: str, start: int, make: str, *, zip_code=ZIP) -> dict:
        requested.append((make, start, zip_code))
        count = max(0, min(ROWS, totals[make] - start))
        return {"num_found": totals[make], "listings": [{"make": make}] * count}

    searches, calls = fetch_all("test", fake_request)
    toyota = next(search for search in searches if search["make"] == "Toyota")
    assert calls == 14 and len(toyota["listings"]) == PAGINATION_LIMIT
    assert toyota["done"] and not toyota["complete"] and toyota["error"]
    assert all(start < PAGINATION_LIMIT for _, start, _ in requested)

    honda_only, calls = fetch_all("test", fake_request, ["Honda"])
    assert calls == 1 and [search["make"] for search in honda_only] == ["Honda"]
    crosstrek_only, calls = fetch_all("test", fake_request, ["Subaru"], "Crosstrek")
    assert calls == 1 and crosstrek_only[0]["model"] == "Crosstrek"
    selected_only, calls = fetch_all("test", fake_request, ["Mazda", "Honda"], {"Mazda3", "CX-5", "Civic"})
    assert calls == 2 and selected_only[0]["models"] == frozenset({"Mazda3", "CX-5"})
    assert selected_only[1]["models"] == frozenset({"Civic"})
    two_zips, calls = fetch_all("test", fake_request, ["Honda"], zip_codes=(ZIP, "95814"))
    assert calls == 2 and [search["zip"] for search in two_zips] == [ZIP, "95814"]
    assert [zip_code for _, _, zip_code in requested[-2:]] == [ZIP, "95814"]
    raw = {
        "vin": "1HGCM82633A004352",
        "price": 15_000,
        "miles": 30_000,
        "dist": 12.3,
        "source": "example.com",
        "vdp_url": "https://example.com/car",
        "build": {
            "year": 2022,
            "make": "Toyota",
            "model": "Camry",
            "trim": "LE",
            "transmission": "Automatic",
            "powertrain_type": "Combustion",
        },
    }
    car = normalize_listing(raw, "dealer")
    assert car and car["model"] == "Camry"
    assert car["marketcheck_one_owner_flag"] == car["marketcheck_clean_title_flag"] == "unknown"
    assert shortlist_key(car) == car["vin"]
    assert normalize_listing({**raw, "carfax_1_owner": "no"}, "dealer")
    assert normalize_listing({**raw, "carfax_clean_title": False}, "dealer")
    assert normalize_listing({**raw, "carfax_1_owner": True}, "dealer")
    tesla_raw = {
        **raw,
        "carfax_1_owner": True,
        "carfax_clean_title": True,
        "build": {
            **raw["build"], "year": 2023, "make": "Tesla", "model": "Model 3",
            "powertrain_type": "BEV",
        },
    }
    assert normalize_listing(tesla_raw, "dealer")["model"] == "Model 3"
    assert normalize_listing({**tesla_raw, "build": {**tesla_raw["build"], "year": 2022}}, "dealer") is None
    assert normalize_listing({**tesla_raw, "carfax_1_owner": None}, "dealer")
    manual = {**raw, "build": {**raw["build"], "transmission": "Manual"}}
    assert normalize_listing(manual, "dealer") is None
    assert normalize_listing(raw, "dealer", 25_000, 2021) is None
    assert normalize_listing(raw, "dealer", 75_000, 2023) is None
    assert normalize_listing(raw, "dealer", excluded={"Camry"}) is None
    assert normalize_listing(raw, "dealer", model_only={"Crosstrek", "CX-5"}) is None
    assert normalize_listing(raw, "dealer", max_price=14_999) is None
    impreza = {
        **raw,
        "vin": "JF1GUAFC1P8123456",
        "build": {**raw["build"], "make": "Subaru", "model": "Impreza"},
    }
    assert normalize_listing(impreza, "dealer")
    assert normalize_listing(impreza, "dealer", excluded={"Impreza"}) is None
    assert normalize_listing(impreza, "dealer", model_only={"Impreza", "CX-5"})
    duplicate = {**car, "price": 14_000}
    assert deduplicate([car, duplicate])[0]["price"] == 14_000
    apply_statuses([duplicate], {duplicate["vin"]: {"price": 15_000}}, False)
    assert duplicate["status"] == "PRICE DROP $1,000"

    old_honda = {
        **car, "vin": "2HGCM82633A004352", "make": "Honda", "model": "Accord", "trim": "EX-L",
    }
    snapshot, removed = update_snapshot(
        {car["vin"]: car, old_honda["vin"]: old_honda},
        [],
        [
            {"section": "dealer", "make": "Toyota", "complete": True},
            {"section": "dealer", "make": "Honda", "complete": False},
        ],
    )
    assert car["vin"] not in snapshot and removed[0]["status"] == "NO LONGER MATCHED"
    assert old_honda["vin"] in snapshot

    snapshot, removed = update_snapshot(
        {car["vin"]: car}, [],
        [
            {"section": "dealer", "make": "Toyota", "model": None, "models": None, "complete": True},
            {"section": "dealer", "make": "Toyota", "model": None, "models": None, "complete": False},
        ],
    )
    assert car["vin"] in snapshot and not removed

    other_toyota = {**car, "vin": "4T1BF1FK5CU123456", "model": "Avalon"}
    snapshot, removed = update_snapshot(
        {car["vin"]: car, other_toyota["vin"]: other_toyota},
        [],
        [{
            "section": "dealer", "make": "Toyota", "model": None,
            "models": frozenset({"Camry", "Corolla"}), "complete": True,
        }],
    )
    assert car["vin"] not in snapshot and other_toyota["vin"] in snapshot and len(removed) == 1

    with TemporaryDirectory() as directory:
        workbook_path = Path(directory) / "candidates-Master-Test.xlsx"
        shortlist_path = Path(directory) / "Shortlist.xlsx"
        car["shortlist"] = 1
        write_workbook(shortlist_path, [car], ("Toyota",))
        shortlist_keys = load_shortlist_keys(shortlist_path)
        assert shortlist_keys == {car["vin"]}
        assert int(shortlist_key(car) in shortlist_keys) == 1
        enrichment = enrich_cars([car, old_honda], reference)
        assert enrichment["matched"] == 2 and enrichment["carplay_unverified"] == 0
        assert car["current_msrp"] == 29_600 and car["msrp_difference"] == 14_600
        assert car["carplay"] == "yes" and "2026 | Toyota | Camry" in car["current_trim_match"]
        assert old_honda["carplay"] == "yes"
        write_workbook(workbook_path, [car, old_honda], ("Toyota", "Honda"))
        from openpyxl import load_workbook
        workbook = load_workbook(workbook_path)
        assert workbook.sheetnames == ["All", "Toyota", "Honda"]
        assert workbook["All"].max_row == 3
        assert workbook["Toyota"].max_row == 2
        assert workbook["Honda"].max_row == 2
        assert workbook["All"]["A1"].value == "shortlist"
        assert workbook["All"]["A2"].value == "='Toyota'!A2"
        assert workbook["Toyota"]["A2"].value == 1
        headers = {cell.value: cell.column for cell in workbook["Toyota"][1]}
        assert workbook["Toyota"].cell(2, headers["current_msrp"]).value == 29_600
        assert workbook["Toyota"].cell(2, headers["msrp_difference"]).value == 14_600
        assert workbook["Toyota"].cell(2, headers["carplay"]).value == "yes"
        assert workbook["Toyota"].cell(2, headers["current_msrp"]).number_format == '$#,##0'
        assert workbook["All"].data_validations.count == 0
        assert workbook["Toyota"].data_validations.count == 1
        formatting = list(workbook["Toyota"].conditional_formatting)
        rule = workbook["Toyota"].conditional_formatting[formatting[0]][0]
        assert rule.dxf.fill.fgColor.rgb == "FFC6EFCE"
        assert rule.dxf.fill.bgColor.rgb == "FFC6EFCE"
        assert workbook.calculation.calcMode == "auto"
        from openpyxl.worksheet.table import Table
        for sheet in workbook.worksheets:
            headers = {cell.value: cell.column for cell in sheet[1]}
            for row in range(2, sheet.max_row + 1):
                sheet.cell(row, headers["msrp_difference"], f"=E{row}-D{row}")
            sheet.add_table(Table(displayName=f"Candidates{sheet.title}", ref=sheet.dimensions))
        workbook.save(workbook_path)
        workbook.close()

        assert find_master_workbook(Path(directory)) == workbook_path
        new_honda = {
            **old_honda, "_key": "1HGCM82633A004353", "vin": "1HGCM82633A004353",
            "price": 14_500, "shortlist": 0,
        }
        appended = append_new_cars(
            workbook_path, [old_honda, new_honda, {**new_honda, "vin": ""}],
        )
        assert [added["vin"] for added in appended] == [new_honda["vin"]]
        assert appended[0]["status"] == "NEW"
        workbook = load_workbook(workbook_path, data_only=False)
        assert workbook["All"].max_row == 4 and workbook["Honda"].max_row == 3
        assert workbook["All"]["A4"].value == "='Honda'!A3"
        assert workbook["All"]["F4"].value == "=E4-D4"
        assert workbook["Honda"]["F3"].value == "=E3-D3"
        assert workbook["Honda"].tables["CandidatesHonda"].ref.endswith("3")
        assert str(workbook["Honda"].data_validations.dataValidation[0].sqref) == "A2:A3"
        assert str(list(workbook["Honda"].conditional_formatting)[0].sqref).endswith("3")
        workbook.close()
        unchanged = workbook_path.read_bytes()
        assert append_new_cars(workbook_path, [new_honda]) == []
        assert workbook_path.read_bytes() == unchanged
    print("Self-test passed")


def positive_int(value: str) -> int:
    try:
        parsed = int(value.replace(",", ""))
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def us_zip(value: str) -> str:
    if not re.fullmatch(r"\d{5}", value):
        raise argparse.ArgumentTypeError("must be a five-digit ZIP code")
    return value


def model_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def comma_values(values) -> list[str]:
    return [part.strip() for value in values or () for part in value.split(",") if part.strip()]


def resolve_exclusions(values, makes) -> frozenset[str]:
    aliases = {
        alias: model
        for make in makes
        for model in MODELS[make]
        for alias in (model_name(model), model_name(make + model))
    }
    tokens = comma_values(values)
    resolved = set()
    while tokens:
        match = None
        consumed = 1
        for count in range(min(3, len(tokens)), 0, -1):
            match = aliases.get(model_name(" ".join(tokens[:count])))
            if match:
                consumed = count
                break
        if not match:
            query = model_name(tokens[0])
            scored = sorted(
                (
                    max(SequenceMatcher(None, query, alias).ratio() for alias, value in aliases.items() if value == model),
                    model,
                )
                for model in set(aliases.values())
            )
            if not scored or scored[-1][0] < 0.65 or (
                len(scored) > 1 and scored[-1][0] - scored[-2][0] < 0.08
            ):
                suggestions = ", ".join(model for _, model in scored[-3:][::-1])
                raise ValueError(f"cannot confidently match model {tokens[0]!r}; closest: {suggestions}")
            match = scored[-1][1]
        resolved.add(match)
        del tokens[:consumed]
    return frozenset(resolved)


def parse_args(argv=None, description="Run the manual MarketCheck used-car search") -> argparse.Namespace:
    global ZIP, RADIUS, MAX_CALLS
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--zip", type=us_zip, default=ZIP or None, dest="zip_code")
    parser.add_argument("--radius", type=positive_int, default=RADIUS)
    parser.add_argument("--max-calls", type=positive_int, default=MAX_CALLS)
    parser.add_argument(
        "--make", action="extend", nargs="+", dest="makes", metavar="MAKE[,MAKE...]",
        help="comma-separated makes to query (default: all)",
    )
    parser.add_argument(
        "--mileage", type=positive_int, default=MILES_MAX,
        help=f"maximum mileage, inclusive (default: {MILES_MAX})",
    )
    parser.add_argument(
        "--min-year", type=positive_int, default=YEAR_MIN,
        help=f"minimum model year (default: {YEAR_MIN})",
    )
    parser.add_argument(
        "--max-price", type=positive_int, default=PRICE_MAX,
        help=f"maximum price, inclusive (default: {PRICE_MAX})",
    )
    parser.add_argument(
        "--exclude", action="extend", nargs="+", default=[], metavar="MODEL[,MODEL...]",
        help="comma-separated models to exclude; accepts close names such as CRV or CX5",
    )
    parser.add_argument(
        "--model-only", action="extend", nargs="+", metavar="MODEL[,MODEL...]",
        help="query comma-separated models only; accepts misspellings and infers their makes",
    )
    parser.add_argument(
        "--additional-zip", type=us_zip,
        help=f"also search within {RADIUS} miles of this ZIP code",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    ZIP, RADIUS, MAX_CALLS = args.zip_code or "", args.radius, args.max_calls
    if args.self_test and (
        args.makes or args.exclude or args.model_only
        or args.mileage != MILES_MAX or args.min_year != YEAR_MIN or args.max_price != PRICE_MAX
        or args.additional_zip
    ):
        parser.error("--self-test cannot be combined with search options")
    make_lookup = {make.casefold(): make for make in MODELS}
    make_values = comma_values(args.makes)
    unknown_makes = [make for make in make_values if make.casefold() not in make_lookup]
    if unknown_makes:
        parser.error(f"unknown make {unknown_makes[0]!r}; choose from {', '.join(MODELS)}")
    args.makes = list(dict.fromkeys(make_lookup[make.casefold()] for make in make_values))
    if args.model_only:
        if args.exclude:
            parser.error("--model-only cannot be combined with --exclude")
        try:
            resolved = resolve_exclusions(args.model_only, MODELS)
        except ValueError as error:
            parser.error(str(error))
        if not resolved:
            parser.error("--model-only needs at least one model")
        selected_make_models = set().union(*(MODELS[make] for make in args.makes)) if args.makes else set()
        wrong_make = resolved - selected_make_models if args.makes else set()
        if wrong_make:
            parser.error(f"{', '.join(sorted(wrong_make))} do not belong to {', '.join(args.makes)}")
        args.model_only = resolved
        args.makes = [make for make, models in MODELS.items() if resolved & models]
    else:
        args.model_only = frozenset()
    if not args.self_test and not args.makes:
        args.makes = list(MODELS)
    if args.min_year < 1900:
        parser.error("--min-year must be 1900 or later")
    if args.min_year > datetime.now().year + 1:
        parser.error(f"--min-year cannot exceed {datetime.now().year + 1}")
    if args.max_price < PRICE_MIN:
        parser.error(f"--max-price must be at least {PRICE_MIN}")
    try:
        args.excluded_models = resolve_exclusions(args.exclude, args.makes or ())
    except ValueError as error:
        parser.error(str(error))
    for make in args.makes or ():
        if not MODELS[make] - DEFAULT_EXCLUDED_MODELS - args.excluded_models:
            parser.error(f"all {make} models were excluded")
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        return run(
            args.makes, args.mileage, args.min_year, args.excluded_models,
            args.model_only, args.max_price, args.additional_zip,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
