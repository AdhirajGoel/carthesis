#!/usr/bin/env python3
"""Run the MarketCheck search for private-party listings only.

Usage:
  export MARKETCHECK_API_KEY='your key'
  python3 search_private.py --make Honda,Mazda
  python3 search_private.py --model-only Mazda3,CX-5 --max-price 24000
  python3 search_private.py --make Honda,Mazda --mileage 60000 --min-year 2021 --exclude CRV,CX5
  python3 search_private.py --self-test
"""

from __future__ import annotations

import sys
from pathlib import Path

import search as base


PRIVATE_ENDPOINT = "https://api.marketcheck.com/v2/search/car/fsbo/active"
ROOT = Path(__file__).resolve().parent

base.DEALER_ENDPOINT = PRIVATE_ENDPOINT
base.RESULTS = ROOT / "results"
base.STATE_FILE = ROOT / ".state" / "private.json"
base_fetch_all = base.fetch_all


def fetch_all(
    api_key: str, request=base.request_page, makes=None, model_only=(), zip_codes=(base.ZIP,),
) -> tuple[list[dict], int]:
    searches, calls = base_fetch_all(api_key, request, makes, model_only, zip_codes)
    for search in searches:
        search["section"] = "private"
    return searches, calls


base.fetch_all = fetch_all


def self_test() -> None:
    requested = []

    def fake_request(endpoint: str, api_key: str, start: int, make: str, *, zip_code=base.ZIP) -> dict:
        requested.append((endpoint, start, make, zip_code))
        return {"num_found": 1, "listings": [{}]}

    searches, calls = fetch_all("test", fake_request, ("Honda", "Mazda"))
    assert calls == 2
    assert [search["make"] for search in searches] == ["Honda", "Mazda"]
    assert all(search["section"] == "private" for search in searches)
    selected, calls = fetch_all("test", fake_request, ("Honda", "Mazda"), {"Civic", "Mazda3", "CX-5"})
    assert calls == 2 and selected[0]["models"] == frozenset({"Civic"})
    assert selected[1]["models"] == frozenset({"Mazda3", "CX-5"})
    assert all(endpoint == PRIVATE_ENDPOINT for endpoint, _, _, _ in requested)
    assert base.RESULTS.name == "results"
    assert base.STATE_FILE == ROOT / ".state" / "private.json"
    assert base.search_params("Honda")["include_non_vin_listings"] == "false"
    custom = base.search_params("Honda", 60_000, 2021, {"CR-V"})
    assert custom["miles_range"] == "0-60000" and "CR-V" not in custom["model"]
    assert base.search_params("Subaru", model_only={"Crosstrek"})["model"] == "Crosstrek"
    assert base.search_params("Subaru", max_price=24_000)["price_range"] == "10000-24000"
    no_vin = {
        "id": "private-listing-1", "price": 15_000, "miles": 30_000,
        "vdp_url": "https://example.com/private-car",
        "build": {
            "year": 2022, "make": "Honda", "model": "Civic",
            "transmission": "Automatic", "powertrain_type": "Combustion",
        },
    }
    assert base.normalize_listing(no_vin, "private") is None
    print("Private-search self-test passed")


def main() -> int:
    args = base.parse_args(sys.argv[1:], "Run the private-party MarketCheck search")
    if args.self_test:
        self_test()
        return 0
    try:
        return base.run(
            args.makes, args.mileage, args.min_year, args.excluded_models,
            args.model_only, args.max_price, args.additional_zip,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
