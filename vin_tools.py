#!/usr/bin/env python3
"""Run MarketCheck VIN tools for VINs in a CSV file.

Examples:
  python3 vin_tools.py history results/candidates-2026-08-15.csv
  python3 vin_tools.py basic results/candidates-2026-08-15.csv --limit 50 --yes
  python3 vin_tools.py title-generate shortlist.csv --limit 3
  python3 vin_tools.py history --export-only

Successful VINs are cached in results/vin-<tool>.jsonl and skipped on later runs.
History calls also produce a flat CSV. Use --refresh to fetch cached VINs again.
Set MARKETCHECK_API_KEY before making API calls.
"""

from __future__ import annotations

import argparse
import ssl
import certifi
import csv
import json
import os
import re
import sys
import time
from search import spreadsheet_value
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
TOOLS = {
    "basic": ("/v2/decode/car/{vin}/specs", 0.0015, {}),
    "history": ("/v2/history/car/{vin}", 0.006, {}),
    "neovin": (
        "/v2/decode/car/neovin/{vin}/specs",
        0.08,
        {"include_generic": "true", "include_available_options": "true"},
    ),
    "epi": ("/v2/decode/car/epi/{vin}/specs", 0.08, {}),
    "recalls": ("/v2/car/autorecalls/{vin}", 0.07, {}),
    "title-access": ("/v2/vindata/access-report/aamva/{vin}", None, {}),
    "title-generate": ("/v2/vindata/generate-report/aamva/{vin}", 0.49, {}),
}
HISTORY_FIELDS = (
    "vin", "record_order_newest_first", "price", "miles", "seller_name", "seller_type",
    "inventory_type", "source", "city", "state", "zip", "first_seen_at_date",
    "last_seen_at_date", "scraped_at_date", "vdp_url", "id", "data_source", "fetched_at",
)


class MarketCheckError(RuntimeError):
    def __init__(self, status: int | None, message: str):
        super().__init__(message)
        self.status = status


def read_vins(path: Path) -> tuple[list[str], int]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        vin_field = next((name for name in reader.fieldnames or [] if name.casefold() == "vin"), None)
        if not vin_field:
            raise RuntimeError(f"{path} has no VIN column")
        vins, seen, invalid = [], set(), 0
        for row in reader:
            vin = (row.get(vin_field) or "").strip().upper()
            if not VIN_RE.fullmatch(vin):
                invalid += 1
            elif vin not in seen:
                seen.add(vin)
                vins.append(vin)
    return vins, invalid


def cache_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                record = json.loads(line)
            except ValueError:
                raise RuntimeError(
                    f"{path} contains invalid JSON on line {line_number}; refusing to risk duplicate charges"
                ) from None
            if isinstance(record, dict):
                records.append(record)
    return records


def cached_vins(path: Path) -> set[str]:
    return {
        str(record.get("vin", "")).upper()
        for record in cache_records(path)
        if VIN_RE.fullmatch(str(record.get("vin", "")).upper())
    }


def write_history_csv(jsonl_path: Path) -> tuple[Path, int]:
    latest = {}
    for record in cache_records(jsonl_path):
        vin = str(record.get("vin", "")).upper()
        if record.get("tool") == "history" and VIN_RE.fullmatch(vin):
            latest[vin] = record

    rows, extra_fields = [], set()
    for vin, record in latest.items():
        history = record.get("data")
        if not isinstance(history, list):
            continue
        for index, event in enumerate(history, 1):
            if not isinstance(event, dict):
                continue
            row = {**event, "vin": vin, "record_order_newest_first": index, "fetched_at": record.get("fetched_at", "")}
            rows.append(row)
            extra_fields.update(row)

    fields = list(HISTORY_FIELDS) + sorted(extra_fields - set(HISTORY_FIELDS))
    csv_path = jsonl_path.with_suffix(".csv")
    temporary = csv_path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else spreadsheet_value(value)
                for key, value in row.items()
            })
    temporary.replace(csv_path)
    return csv_path, len(rows)


def fetch(tool: str, vin: str, api_key: str) -> tuple[object, dict[str, str]]:
    endpoint, _, extra_params = TOOLS[tool]
    params = {"api_key": api_key, "append_api_key": "false", **extra_params}
    request = Request(
        f"https://api.marketcheck.com{endpoint.format(vin=vin)}?{urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": "manual-marketcheck-vin-tools/1"},
    )
    try:
        with urlopen(request, timeout=60, context=ssl.create_default_context(cafile=certifi.where())) as response:
            payload = json.loads(response.read())
            usage = {
                name: response.headers.get(name, "")
                for name in ("Quota-Limit", "Quota-Remaining", "Quota-Reset-Time")
            }
    except HTTPError as error:
        message = ""
        try:
            body = json.loads(error.read(4096))
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
        except (OSError, ValueError):
            pass
        message = message.replace(api_key, "[redacted]")
        raise MarketCheckError(error.code, f"HTTP {error.code}{': ' + message if message else ''}") from None
    except URLError as error:
        raise MarketCheckError(None, f"Could not reach MarketCheck: {error.reason}") from None
    except ValueError:
        raise MarketCheckError(None, "MarketCheck returned invalid JSON") from None
    return payload, usage


def estimate(tool: str, count: int) -> str:
    cost = TOOLS[tool][1]
    return "unknown published data fee" if cost is None else f"up to ${count * cost:,.2f}"


def run(args: argparse.Namespace) -> int:
    if args.limit < 0:
        raise RuntimeError("--limit must be zero or greater")

    RESULTS.mkdir(exist_ok=True)
    output = args.output or RESULTS / f"vin-{args.tool}.jsonl"
    if args.export_only:
        if args.tool != "history":
            raise RuntimeError("--export-only is available only for the history tool")
        csv_path, rows = write_history_csv(output)
        print(f"Saved {rows:,} history records to {csv_path}")
        return 0

    if not args.csv:
        raise RuntimeError("Provide an input CSV containing a VIN column")
    api_key = os.environ.get("MARKETCHECK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Set MARKETCHECK_API_KEY in your shell before running VIN tools")

    vins, invalid = read_vins(args.csv)
    completed = set() if args.refresh else cached_vins(output)
    pending = [vin for vin in vins if vin not in completed]
    selected = pending if args.limit == 0 else pending[:args.limit]

    print(
        f"{len(vins):,} unique valid VINs; {len(completed & set(vins)):,} cached; "
        f"{len(selected):,} calls selected; estimated data fee: {estimate(args.tool, len(selected))}"
    )
    if invalid:
        print(f"Warning: skipped {invalid:,} blank or invalid VIN rows", file=sys.stderr)
    if not selected:
        print("Nothing to fetch")
        if args.tool == "history":
            csv_path, rows = write_history_csv(output)
            print(f"Saved {rows:,} history records to {csv_path}")
        return 0
    if not args.yes:
        if not sys.stdin.isatty() or input("Continue? [y/N] ").strip().casefold() != "y":
            print("Cancelled")
            return 0

    successes = failures = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        for index, vin in enumerate(selected, 1):
            try:
                payload, usage = fetch(args.tool, vin, api_key)
            except MarketCheckError as error:
                failures += 1
                print(f"[{index}/{len(selected)}] {vin}: {error}", file=sys.stderr)
                if error.status == 429:
                    print("Stopped after MarketCheck returned a quota/rate-limit error", file=sys.stderr)
                    break
            else:
                record = {
                    "vin": vin,
                    "tool": args.tool,
                    "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "quota": usage,
                    "data": payload,
                }
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                successes += 1
                print(f"[{index}/{len(selected)}] {vin}: saved")
            if index < len(selected):
                time.sleep(0.22)  # Free/Basic plans allow five requests per second.

    print(f"Saved {successes:,} results to {output}; {failures:,} failed")
    if args.tool == "history":
        csv_path, rows = write_history_csv(output)
        print(f"Saved {rows:,} history records to {csv_path}")
    return 1 if failures else 0


def self_test() -> None:
    with TemporaryDirectory() as directory:
        source = Path(directory) / "cars.csv"
        source.write_text(
            "price,VIN\n10000,1HGCM82633A004352\n11000,1HGCM82633A004352\n12000,invalid\n",
            encoding="utf-8",
        )
        vins, invalid = read_vins(source)
        assert vins == ["1HGCM82633A004352"] and invalid == 1
        cache = Path(directory) / "cache.jsonl"
        cache.write_text(
            '{"vin":"1HGCM82633A004352","tool":"history","fetched_at":"2026-08-15",'
            '"data":[{"price":15000,"miles":30000,"seller_name":"Example Dealer"}]}\n',
            encoding="utf-8",
        )
        assert cached_vins(cache) == {"1HGCM82633A004352"}
        csv_path, rows = write_history_csv(cache)
        assert rows == 1 and "Example Dealer" in csv_path.read_text(encoding="utf-8")
    assert estimate("history", 20) == "up to $0.12"
    assert estimate("title-generate", 3) == "up to $1.47"
    print("Self-test passed")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=TOOLS)
    parser.add_argument("csv", type=Path, nargs="?", help="CSV containing a VIN column")
    parser.add_argument("--limit", type=int, default=5, help="maximum calls; 0 means all (default: 5)")
    parser.add_argument("--output", type=Path, help="JSONL cache/output path")
    parser.add_argument("--refresh", action="store_true", help="call VINs already present in the output cache")
    parser.add_argument("--export-only", action="store_true", help="convert cached history JSONL to CSV without API calls")
    parser.add_argument("--yes", action="store_true", help="skip the cost confirmation prompt")
    return parser.parse_args(argv)


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    try:
        return run(parse_args(sys.argv[1:]))
    except (OSError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
