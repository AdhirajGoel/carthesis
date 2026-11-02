# Agent-guided setup

You are helping a buyer set up and use CarThesis on macOS. Read README.md, docs/setup.md, and docs/model.md. Keep the interaction concise. Inspect existing files and reuse completed steps. Do not overwrite the buyer's saved comparisons, reference data, environment, credentials, or unrelated Git changes.

## 1. Establish a working local demo

1. Resolve the repository directory. Check Python 3.10+ and Node.js 18+ with their version commands. Node is for verification; the browser app has no build step. If either is missing, explain the specific prerequisite and obtain approval before installing system software.
2. Create `.venv` if absent and install `requirements.txt` there. Never modify system Python. Run `.venv/bin/python tests/run_checks.py`, then `.venv/bin/python carthesis.py init`. Initialization preserves existing files.
3. Open `web/index.html` or the macOS launcher. The synthetic demo needs no account or API request. Confirm that the new, used, and hybrid examples display, that costs respond to edits, and that Save comparison produces a reusable JSON file. Do not infer live API success from this demo.

## 2. Collect buying inputs

Ask one focused question at a time. Start with the purchase decision and required vehicle characteristics. Gather the buyer's search ZIP/radius, makes/models, new/used preference, price ceiling, model-year and mileage bounds, must-have equipment, expected miles driven, ownership horizon, down payment, and financing term. Confirm what matters beyond cost before recommending a shortlist.

Before live requests, agree a small request or dollar budget and the permitted endpoints. Estimate costs from current official MarketCheck pricing. Do not upgrade a subscription, purchase a report, contact a seller, submit a loan application, or initiate a purchase without specific authorization. Use cached results and stop at the agreed cap. Never treat silence as approval.

## 3. Configure private access and research

Use an existing MARKETCHECK_API_KEY environment variable or the macOS Keychain item with service MARKETCHECK_API_KEY. Never print a key, put it in a command argument, paste it into chat, or save it in a tracked file. If neither exists, guide the buyer to obtain their own MarketCheck account and store the credential locally. Do not reuse somebody else's credentials.

The `.gitignore` excludes data/, results/, .state/, environments, and sessions. Keep all buyer inputs and live outputs there. Do not add ignored files to Git or turn on analytics.

Review the relevant entries in `data/reference.json`. It begins as an archived reference catalog, not current price evidence. Research the relevant current trims, MSRP, drivetrain, and equipment from primary sources; preserve source URLs and checked dates in local notes. Set `reviewed_for_live` to true only after that review. `examples/reference-template.json` shows the schema. Missing CarPlay evidence stays unverified.

Gather current advertised financing and fuel-economy figures with dates and sources, plus defensible maintenance and resale assumptions. Ask the buyer to confirm actual insurance quotes, financing eligibility/terms, tax and fees. Label estimates as estimates in the input source fields. Do not present advertisements as approved loan offers. Compare matched trims and clearly identified ownership horizons.

## 4. Search and prepare a comparison

Follow docs/setup.md. Supply the buyer's own ZIP and a bounded --max-calls value. Review coverage and rejected/unmatched rows. Apply the local price index to inventory from the intended area; do not silently mix distant markets or stale search snapshots. Preserve the raw candidate workbook before running an in-place enrichment command.

Build `data/comparison.json` from the documented example. Every vehicle needs its own purchase price, MPG, APR, annual maintenance/repair estimate, and 5-/10-year resale fractions. Add per-vehicle insurance or loan terms when different. Set the same `pair` identifier only for genuinely comparable gas/hybrid variants. Attach a `vin` when linking a used listing to the workbook. Research sources and checked dates belong in the source fields and local notes.

Run `carthesis.py compare` to transfer the chosen listing's price and local score. Open the resulting JSON in the app. Verify the ranking and explain which inputs drive it. Show both local price competitiveness and ownership cost. Do not claim that hybrids or used cars are universally worse. A change to a listing's identity, price, or comparison market requires a fresh local score.

## 5. Verify and hand off

Run offline checks after code changes. Exercise the buyer's comparison in the browser, including edits and save/reopen. If browser automation is available, use an isolated Playwright context without accessing an existing browser profile. Installing optional browser dependencies requires the buyer's agreement.

Summarize where the private comparison and evidence were saved, how to reopen them, and any concrete setup step still needed. Keep the README and example files free of buyer data. Do not publish, change repository visibility, or push buyer outputs unless explicitly requested.
