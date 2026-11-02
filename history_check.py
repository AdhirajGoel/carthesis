#!/usr/bin/env python3
"""Add free public CARFAX/AutoCheck facts to results/Shortlist.xlsx.

Public pages are read in a isolated Playwright Chromium context. Your normal Chrome
profile is never accessed; blocked or unrecognized reports stay unknown.

Usage:
  python3 history_check.py                 # check every incomplete VIN
  python3 history_check.py --limit 5       # optionally check only five VINs
  python3 history_check.py --refresh       # retry cached outcomes
  python3 history_check.py --cache-only    # reapply cached results only
  python3 history_check.py --report-url VIN=https://public-report-url
  python3 history_check.py --self-test
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import copy
from datetime import datetime
from html import unescape
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, unquote, urlparse

import search as base


ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "results" / "Shortlist.xlsx"
CACHE = ROOT / "results" / "history-reports.json"
HISTORY_FIELDS = (
    "history_summary",
    "history_status",
    "history_provider",
    "owner_count",
    "prior_use",
    "rental_or_fleet_ever",
    "reported_accidents",
    "max_accident_severity",
    "structural_or_airbag",
    "title_or_odometer_issue",
    "history_report_url",
    "history_checked_at",
)
WIDTHS = {
    "history_summary": 62,
    "history_status": 20,
    "history_provider": 14,
    "owner_count": 13,
    "prior_use": 24,
    "rental_or_fleet_ever": 23,
    "reported_accidents": 20,
    "max_accident_severity": 23,
    "structural_or_airbag": 23,
    "title_or_odometer_issue": 25,
    "history_report_url": 48,
    "history_checked_at": 23,
}
REPORT_RE = re.compile(
    r"carfax|auto\s*check|experian|vehicle\s+history|history\s+report|\bvhr\b", re.I,
)
BLOCK_RE = re.compile(
    r"access denied|attention required|request could not be satisfied|captcha|"
    r"verify (?:that )?you are human|automated access|unusual traffic|temporarily blocked",
    re.I,
)
URL_RE = re.compile(r"https?://[^\s\"'<>()[\]]+", re.I)
IMAGE_RE = re.compile(r"\.(?:png|jpe?g|gif|svg|webp)(?:\?|$)", re.I)
USE_ORDER = ("personal", "rental", "fleet", "lease", "commercial", "taxi", "police", "government")


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def provider_from_url(url: str) -> str:
    value = unquote(url).casefold()
    if "carfax" in value:
        return "CARFAX"
    if "autocheck" in value or "experian" in value:
        return "AutoCheck"
    return ""


def unwrap_report_url(url: str) -> str:
    current = unescape(url).replace("\\/", "/")
    for _ in range(3):
        query = parse_qs(urlparse(current).query)
        nested = next(
            (
                values[0] for key in ("src", "url", "report_url", "reportUrl", "target")
                if (values := query.get(key)) and provider_from_url(values[0])
            ),
            "",
        )
        if not nested:
            break
        current = unquote(nested)
    return current


def clean_lines(text: str) -> list[str]:
    return [
        line for raw in text.splitlines()
        if (line := re.sub(r"\s+", " ", raw).strip())
    ]


def next_line(lines: list[str], label: str) -> str:
    for index, line in enumerate(lines[:-1]):
        if line.casefold() == label.casefold():
            return lines[index + 1]
    return ""


def parse_report(text: str, provider: str, report_url: str, checked_at: str | None = None) -> dict:
    lines = clean_lines(text)
    joined = "\n".join(lines)
    lowered = joined.casefold()

    owner_numbers = [int(value) for value in re.findall(r"\bowner\s+(\d+)\b", lowered)]
    owner_numbers += [
        int(value) for value in re.findall(r"\b(?:calculated owners?|owners?)\s*:?\s*(\d+)\b", lowered)
    ]
    if re.search(r"\bcarfax\s+1-owner\b", lowered):
        owner_numbers.append(1)
    owner_count = max(owner_numbers, default=None)

    uses = set()
    for line in lines:
        low = line.casefold()
        relevant = (
            re.search(r"\b(?:usage|type of owner|vehicle use)\s*:", low)
            or "titled or registered as a rental vehicle" in low
            or low in {
                "personal", "personal vehicle", "rental", "rental vehicle", "fleet",
                "fleet vehicle", "lease", "lease vehicle", "commercial", "commercial vehicle",
                "taxi", "police vehicle", "government vehicle",
            }
        )
        if relevant:
            uses.update(value for value in USE_ORDER if value in low)
    prior_use = ", ".join(value for value in USE_ORDER if value in uses)
    rental_or_fleet = "yes" if uses & {"rental", "fleet"} else ("no" if uses else "unknown")

    no_accident = bool(re.search(
        r"\bno accidents?(?: or damage)? reported\b|\bno accidents or damage reported\b",
        lowered,
    ))
    accident_count = next(
        (int(value) for value in re.findall(r"\b(\d+)\s+accidents?\s+reported\b", lowered)),
        None,
    )
    accident_reported = any(
        re.search(r"\baccident(?: or damage)? reported\b|\bdamage reported\b", line.casefold())
        and not line.casefold().startswith("no ")
        for line in lines
    )
    reported_accidents: int | str = (
        accident_count if accident_count is not None else
        "reported" if accident_reported else
        "none reported" if no_accident else
        "unknown"
    )

    severities = []
    for line in lines:
        low = line.casefold()
        if low.startswith("no "):
            continue
        match = re.search(
            r"(?:damage severity|severity)\s*:?\s*(minor|moderate|severe)|"
            r"\b(minor|moderate|severe) damage (?:was )?reported\b",
            low,
        )
        if match:
            severities.append(match.group(1) or match.group(2))
    severity = max(severities, key={"minor": 1, "moderate": 2, "severe": 3}.get) if severities else "unknown"

    structural_positive = any(
        re.search(r"\b(?:structural damage|air ?bag deployment) (?:was )?reported\b", line.casefold())
        and not line.casefold().startswith("no ")
        for line in lines
    )
    structural = "yes" if structural_positive else (
        "none reported" if no_accident and not accident_reported else "unknown"
    )

    title_value = next_line(lines, "State Title Brand").casefold()
    title_clean = title_value in {"clean", "no issue", "no issues reported"} or (
        "no reported major state title brands" in lowered
    )
    odometer_clean = bool(re.search(
        r"no odometer (?:brands|problems).*reported|odometer check\s+no issues reported",
        lowered,
    ))
    title_issue = bool(re.search(r"\b(?:salvage|rebuilt|flood|lemon|junk)\b", title_value)) or any(
        re.search(
            r"\b(?:salvage|rebuilt|flood|lemon|junk) (?:title|brand)\b|"
            r"\b(?:odometer rollback|not actual mileage|mileage discrepancy|odometer tampering) "
            r"(?:reported|indicated|detected)\b|\bodometer check\s+(?:problems?|issues?) reported\b",
            line.casefold(),
        )
        for line in lines
    )
    title_or_odometer = (
        "reported" if title_issue else
        "none reported" if title_clean and odometer_clean else
        "unknown"
    )

    recognized = any((owner_count, uses, no_accident, accident_reported, title_clean, odometer_clean, title_issue))
    status = "available" if recognized else "parse_failed"
    summary = []
    if owner_count:
        summary.append(f"{owner_count} owner{'s' if owner_count != 1 else ''}")
    if prior_use:
        summary.append(f"use: {prior_use}")
    if isinstance(reported_accidents, int):
        summary.append(f"{reported_accidents} reported accident{'s' if reported_accidents != 1 else ''}")
    elif reported_accidents != "unknown":
        summary.append(f"accidents/damage: {reported_accidents}")
    if severity != "unknown":
        summary.append(f"max severity: {severity}")
    if title_or_odometer != "unknown":
        summary.append(f"title/odometer: {title_or_odometer}")
    return {
        "history_summary": " | ".join(summary) or "report found; fields not recognized",
        "history_status": status,
        "history_provider": provider,
        "owner_count": owner_count or "",
        "prior_use": prior_use or "unknown",
        "rental_or_fleet_ever": rental_or_fleet,
        "reported_accidents": reported_accidents,
        "max_accident_severity": severity,
        "structural_or_airbag": structural,
        "title_or_odometer_issue": title_or_odometer,
        "history_report_url": report_url,
        "history_checked_at": checked_at or now(),
    }


def outcome(status: str, summary: str, report_url: str = "", provider: str = "") -> dict:
    return {
        "history_summary": summary,
        "history_status": status,
        "history_provider": provider,
        "owner_count": "",
        "prior_use": "unknown",
        "rental_or_fleet_ever": "unknown",
        "reported_accidents": "unknown",
        "max_accident_severity": "unknown",
        "structural_or_airbag": "unknown",
        "title_or_odometer_issue": "unknown",
        "history_report_url": report_url,
        "history_checked_at": now(),
    }


def report_links(text: str, vin: str) -> list[str]:
    normalized = unescape(text).replace("\\/", "/")
    candidates = {}
    for match in URL_RE.finditer(normalized):
        url = unwrap_report_url(match.group().rstrip(".,;)]}>"))
        if not provider_from_url(url) or IMAGE_RE.search(url):
            continue
        context = normalized[max(0, match.start() - 100):match.end() + 100]
        score = 50 if vin.casefold() in url.casefold() else 0
        score += 30 if re.search(r"vehiclehistory|report|\bvhr\b", url, re.I) else 0
        score += 20 if REPORT_RE.search(context) else 0
        candidates[url] = max(score, candidates.get(url, 0))
    return [url for url, _ in sorted(candidates.items(), key=lambda item: item[1], reverse=True)]


def page_content(page) -> tuple[str, str]:
    try:
        text = page.locator("body").inner_text(timeout=5_000)[:500_000]
    except Exception:
        text = ""
    try:
        urls = page.locator("a,iframe,[href],[src],[data-href],[data-url],[data-src]").evaluate_all(
            """els => els.flatMap(e => ['href','src','data-href','data-url','data-src']
              .map(name => e.getAttribute(name)).filter(Boolean))"""
        )
        source = f"{page.content()}\n" + "\n".join(urls)
    except Exception:
        source = ""
    return text, source


def check_car(context, car: dict, direct_url: str = "") -> dict:
    listing_url = str(car["listing_url"])
    report_url = direct_url.strip()
    if not report_url:
        page = context.new_page()
        observed = []
        page.on("request", lambda request: observed.append(request.url) if REPORT_RE.search(request.url) else None)
        try:
            page.goto(listing_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)
            listing_text, source = page_content(page)
        except Exception as error:
            return outcome("listing_error", f"listing could not be loaded: {str(error).splitlines()[0]}")
        finally:
            page.close()
        links = report_links(f"{source}\n" + "\n".join(observed), car["vin"])
        if not links:
            if BLOCK_RE.search(listing_text[:10_000]):
                return outcome("listing_blocked", "listing blocked public browser access")
            return outcome("not_provided", "no public CARFAX/AutoCheck report found")
        report_url = links[0]

    report_url = unwrap_report_url(report_url)
    provider = provider_from_url(report_url)
    page = context.new_page()
    try:
        page.goto(report_url, referer=listing_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(3_000)
        report_text, _ = page_content(page)
        parsed = parse_report(report_text, provider, report_url)
        if parsed["history_status"] == "available":
            return parsed
        if BLOCK_RE.search(report_text[:10_000]):
            return outcome("report_blocked", f"{provider} report blocked public browser access", report_url, provider)
        return parsed
    except Exception as error:
        return outcome(
            "report_error",
            f"{provider} report could not be loaded: {str(error).splitlines()[0]}",
            report_url,
            provider,
        )
    finally:
        page.close()


def load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"Cannot read {path.name}: {error}") from None
    records = data.get("vehicles") if isinstance(data, dict) else None
    if not isinstance(records, dict):
        raise RuntimeError(f"{path.name} has an invalid format; it was left unchanged")
    return {str(vin).upper(): record for vin, record in records.items() if isinstance(record, dict)}


def save_cache(path: Path, records: dict[str, dict]) -> None:
    base.atomic_text(
        path,
        json.dumps({"updated_at": now(), "vehicles": records}, indent=2, sort_keys=True) + "\n",
    )


def workbook_cars(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("Reading the shortlist requires openpyxl") from None
    if not path.exists():
        raise RuntimeError(f"{path} does not exist; run update_shortlist.py first")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook["All"]
        rows = sheet.iter_rows(values_only=True)
        headers = next(rows, ())
        indexes = {str(value).strip(): index for index, value in enumerate(headers) if value}
        required = {"vin", "listing_url"}
        if not required <= indexes.keys():
            raise RuntimeError(f"{path.name} must contain vin and listing_url columns")
        cars = []
        for row in rows:
            vin = str(row[indexes["vin"]] or "").strip().upper()
            listing_url = str(row[indexes["listing_url"]] or "").strip()
            if base.VIN_RE.fullmatch(vin) and listing_url.startswith(("http://", "https://")):
                cars.append({
                    "vin": vin,
                    "listing_url": listing_url,
                    "history_report_url": str(
                        row[indexes["history_report_url"]] or ""
                        if "history_report_url" in indexes and indexes["history_report_url"] < len(row)
                        else ""
                    ).strip(),
                })
        return cars
    finally:
        workbook.close()


def apply_to_workbook(path: Path, records: dict[str, dict]) -> None:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise RuntimeError("Updating the shortlist requires openpyxl") from None

    workbook = load_workbook(path)
    for sheet in workbook.worksheets:
        for field in HISTORY_FIELDS:
            headers = [cell.value for cell in sheet[1]]
            if field not in headers:
                column = sheet.max_column + 1
                sheet.cell(1, column)._style = copy(sheet.cell(1, column - 1)._style)
                sheet.cell(1, column).value = field

        indexes = {str(cell.value).strip(): cell.column for cell in sheet[1] if cell.value}
        vin_column = indexes.get("vin")
        if vin_column:
            for row in range(2, sheet.max_row + 1):
                vin = str(sheet.cell(row, vin_column).value or "").strip().upper()
                record = records.get(vin)
                if not record:
                    continue
                for field in HISTORY_FIELDS:
                    cell = sheet.cell(row, indexes[field], base.spreadsheet_value(record.get(field, "")))
                    if field == "history_report_url" and isinstance(cell.value, str) and cell.value.startswith("http"):
                        cell.hyperlink = cell.value
                        cell.style = "Hyperlink"
        for field, width in WIDTHS.items():
            sheet.column_dimensions[get_column_letter(indexes[field])].width = width
        sheet.auto_filter.ref = sheet.dimensions

    temporary = path.with_suffix(path.suffix + ".tmp")
    workbook.save(temporary)
    workbook.close()
    temporary.replace(path)


def report_overrides(values: list[str]) -> dict[str, str]:
    overrides = {}
    for value in values:
        vin, separator, url = value.partition("=")
        vin = vin.strip().upper()
        url = url.strip()
        if not separator or not base.VIN_RE.fullmatch(vin) or not url.startswith(("http://", "https://")):
            raise ValueError("--report-url must use VIN=https://public-report-url")
        if not provider_from_url(url):
            raise ValueError("--report-url must be a CARFAX, AutoCheck, or Experian URL")
        overrides[vin] = url
    return overrides


def run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise RuntimeError("--limit must be zero or greater")
    cars = workbook_cars(args.workbook)
    cache = load_cache(args.cache)
    overrides = report_overrides(args.report_url)
    pending = [
        car for car in cars
        if car["vin"] in overrides or args.refresh
        or cache.get(car["vin"], {}).get("history_status") != "available"
    ]
    pending.sort(key=lambda car: car["vin"] not in overrides)
    selected = [] if args.cache_only else (pending if args.limit == 0 else pending[:args.limit])
    print(
        f"{len(cars):,} shortlisted VINs; {len(cars) - len(pending):,} completed reports cached; "
        f"{len(selected):,} isolated Chromium checks selected; data fee: $0"
    )

    if selected:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise RuntimeError("Playwright is required for isolated Chromium checks") from None
        with TemporaryDirectory(prefix="history-chrome-") as profile:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context()
                try:
                    for index, car in enumerate(selected, 1):
                        direct_url = overrides.get(car["vin"]) or car.get("history_report_url", "")
                        try:
                            record = check_car(context, car, direct_url)
                        except Exception as error:
                            record = outcome(
                                "listing_error", f"history check failed: {str(error).splitlines()[0]}",
                            )
                        cache[car["vin"]] = record
                        save_cache(args.cache, cache)
                        print(f"[{index}/{len(selected)}] {car['vin']}: {record['history_status']}")
                finally:
                    context.close()
                    browser.close()

    current = {car["vin"]: cache[car["vin"]] for car in cars if car["vin"] in cache}
    apply_to_workbook(args.workbook, current)
    counts = Counter(record.get("history_status", "unknown") for record in current.values())
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items())) or "none"
    print(f"Updated {args.workbook} with {len(current):,} cached results ({summary})")
    return 0


def self_test() -> None:
    auto = parse_report(
        """Vehicle Usage
Rental
State Title Brand
Clean
Accident / Damage
No Accidents or Damage Reported
Odometer Check
No issues reported
Owner 1
Usage: Rental
Titled or Registered as a Rental Vehicle, or Part of a Rental Fleet
No odometer brands, rollbacks, rollover or tampering has been reported.
State Title Brand Vehicle History at a Glance: brands checked include Flood, Junk, Lemon, Salvage and Rebuilt.""",
        "AutoCheck",
        "https://autocheck.web.dealer.com/?data=test",
        "2026-08-17T12:00:00-04:00",
    )
    assert auto["owner_count"] == 1 and auto["prior_use"] == "rental, fleet"
    assert auto["rental_or_fleet_ever"] == "yes"
    assert auto["reported_accidents"] == "none reported"
    assert auto["title_or_odometer_issue"] == "none reported"

    carfax = parse_report(
        """CARFAX Vehicle History Report
Owner 1
Personal Vehicle
Owner 2
Rental Vehicle
No Accidents or Damage Reported
Accident reported
Damage Severity: Minor
No structural damage reported""",
        "CARFAX",
        "https://www.carfax.com/vehiclehistory/ar20/test",
    )
    assert carfax["owner_count"] == 2 and carfax["prior_use"] == "personal, rental"
    assert carfax["reported_accidents"] == "reported" and carfax["max_accident_severity"] == "minor"
    assert unwrap_report_url(
        "https://dealer.example/iframe.htm?src=https%3A%2F%2Fautocheck.web.dealer.com%2F%3Fdata%3Dabc"
    ) == "https://autocheck.web.dealer.com/?data=abc"
    assert report_links(
        "[CARFAX report](https://www.carfax.com/vehiclehistory/ar20/test) "
        "![logo](https://partnerstatic.carfax.com/logo.svg)",
        "JM3KFBBM2R0368395",
    ) == ["https://www.carfax.com/vehiclehistory/ar20/test"]

    with TemporaryDirectory() as directory:
        path = Path(directory) / "Shortlist.xlsx"
        car = {
            "shortlist": 1, "status": "NEW", "seller_section": "dealer", "price": 20_000,
            "year": 2024, "make": "Mazda", "model": "CX-5", "miles": 20_000,
            "vin": "JM3KFBBM2R0368395", "listing_url": "https://example.com/car",
        }
        base.write_workbook(path, [car], ("Mazda",))
        apply_to_workbook(path, {car["vin"]: auto})
        from openpyxl import load_workbook
        workbook = load_workbook(path, read_only=False, data_only=True)
        headers = [cell.value for cell in workbook["All"][1]]
        assert headers.index("history_summary") == headers.index("listing_url") + 1
        assert workbook["All"].cell(2, headers.index("owner_count") + 1).value == 1
        assert workbook["All"].cell(2, headers.index("history_report_url") + 1).hyperlink
        workbook.close()
    print("History-check self-test passed")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=WORKBOOK)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--limit", type=int, default=0, help="maximum incomplete VINs; 0 means all (default: all)")
    parser.add_argument("--refresh", action="store_true", help="retry cached outcomes")
    parser.add_argument("--cache-only", action="store_true", help="update workbook without public-page checks")
    parser.add_argument(
        "--report-url", action="append", default=[], metavar="VIN=URL",
        help="use a public report URL directly; may be repeated",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    supplied = sys.argv[1:] if argv is None else argv
    if args.self_test and supplied != ["--self-test"]:
        parser.error("--self-test cannot be combined with other options")
    return args


def main() -> int:
    args = parse_args()
    if args.self_test:
        self_test()
        return 0
    try:
        return run(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
