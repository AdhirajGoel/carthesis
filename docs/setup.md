# Set up a comparison

## Local installation

Use Python 3.10+ and Node.js 18+. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tests/run_checks.py
.venv/bin/python carthesis.py init
open web/index.html
```

Initialization copies example configuration into `data/` only when the destination does not exist. Nothing contacts MarketCheck until a live command is run. The browser app has no backend, telemetry, external fonts, or third-party scripts. Changes remain in memory until you choose **Save comparison**; reopening the page loads the synthetic demo.

## Live listing research

Provide your own MarketCheck credential through `MARKETCHECK_API_KEY` or a macOS Keychain generic-password item whose service is `MARKETCHECK_API_KEY`. Keep it out of shell history, command-line arguments, JSON comparison files, and Git. The CLI reads the credential locally and suppresses it in API errors.

Research the relevant entries in `data/reference.json` and set `reviewed_for_live` to true. The bundled reference contains archived vehicle facts; refresh the relevant MSRP and equipment assumptions for your search. The existing make/trim mapping covers Toyota, Honda, Mazda, Subaru, and Tesla. The ownership calculator models gasoline and conventional hybrid fuel costs.

Choose your search area and request cap. This example uses a generic ZIP and performs at most five API calls:

```sh
.venv/bin/python search.py --zip 10001 --radius 50 --make Honda --model-only Civic --max-price 25000 --min-year 2022 --mileage 60000 --max-calls 5
```

Use `search_private.py` with the same options for private-party inventory. `--additional-zip` adds a second area. Search output reports whether coverage was complete. Read current endpoint prices before budgeting: [MarketCheck pricing](https://www.marketcheck.com/pricing).

Candidates accumulate in `results/candidates-Master.xlsx`; `.state/` tracks changes between runs. Keep separate release clones for unrelated markets so historical candidates do not contaminate a local comparison. A repeated search appends newly encountered VINs and tracks state; it does not refresh every existing workbook row. For a current price assessment, start a new candidate workbook and state directory after preserving earlier results.

## Assess price and shortlist

Save a backup before modifying a workbook. Score the candidate workbook after checking its market and date coverage:

```sh
.venv/bin/python add_deal_edge.py results/candidates-Master.xlsx --run-year 2026
```

Use the actual search year for `--run-year`. Scoring and comparison export stop if a VIN has different scoring inputs across sheets; reconcile those rows before retrying. Mark `shortlist` as `1` on the individual make sheets, save the workbook, then run:

```sh
.venv/bin/python update_shortlist.py
```

Optional tools retain their own `--help` and offline `--self-test` commands:

- `CarPlay_filter.py`: classify factory equipment from the local reference.
- `vin_search.py`: find a VIN in local workbooks.
- `vin_tools.py`: retrieve and cache paid VIN/history data. It prints an estimate and asks before fetching; endpoint prices should be checked before confirming.
- `history_check.py`: inspect publicly accessible report links in isolated Playwright Chromium. Blocked or unrecognized reports remain unknown. It requires the optional browser dependencies below.

## Build the ownership comparison

Edit `data/comparison.json` using the [example](../examples/comparison.json) and [field definitions](model.md#comparison-files). Enter vehicle-specific quotes and estimates, with their sources and checked dates. Prices and costs are in US dollars.

For a listed vehicle, set `vin` to its candidate VIN. New-car quotes can omit `vin`. Transfer the workbook's asking prices and deal scores into the comparison:

```sh
.venv/bin/python carthesis.py compare results/candidates-Master.xlsx --assumptions data/comparison.json --output data/comparison.json
```

The command uses Node and the shared ownership model to validate the complete comparison before replacing the output. Invalid inputs or a refreshed price that makes a custom payment too low for the loan leave the saved file unchanged.

Choose **Open comparison JSON** in the app and select that file. Compare outcomes, then use **Save comparison** to keep your edits.

## Optional browser checks

```sh
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/test_browser.py
```

The checks use synthetic files and a fresh browser context. To recreate README images, add `--screenshots docs/images`.
