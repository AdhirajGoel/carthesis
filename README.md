# LotLogic

Compare car deals by local asking price and total cost of ownership.

LotLogic is an AI-assisted car-buying research tool for Codex and Claude Code. It pairs a seller-balanced local price comparison with an editable model of financing, fuel, insurance, maintenance, and resale, so you can test whether a cheaper used car or a hybrid premium makes sense for your purchase.

I love data, especially when it changes an everyday decision. This project grew out of wanting to test familiar car-buying advice against local listings and the full cost of ownership.

**Try the demo:** open **web/index.html** in a browser or double-click **Open LotLogic.command** after cloning or downloading the repository. It runs locally with synthetic data, without an account or API call. Use **Save comparison** to retain your assumptions and cost selections, then **Open comparison JSON** to restore them.

![Synthetic new, hybrid, and used vehicles ranked by five-year ownership cost](docs/images/ownership-overview.png)

In this synthetic five-year comparison, the used car asks $4,000 less and sits 3.70% below its local peer benchmark, yet costs $12,385 more to own. Its higher interest, maintenance estimate, and lower resale value outweigh the purchase saving.

## Try changing the decision

1. **Start at 30 miles per day.** The new gas car costs $33,933 over five years; the hybrid costs $38,795, or $4,862 more. In **Gas vs hybrid**, its fuel-only payback is 15.2 years.
2. **Set mileage to 100 miles per day.** In this high-mileage stress test, the hybrid becomes about $678 cheaper over the same five years. Reset the demo afterward.
3. **Test the financing.** In **APR crossover**, the used car's purchase saving disappears at about 6.37% over 60 months against the demo's 0% new-car loan. In **Loan & prepay**, select the used car and set its monthly payment to $600: it repays in 51 months and saves about $1,023 in interest.

The APR crossover compares cash through full loan payoff. Ownership cost also includes operating costs and resale; fuel-only payback considers the premium and fuel savings alone.

## Two different questions

**Is the asking price competitive?** The local price index compares a listing with five nearby peers in the searched inventory, balancing sellers and matching vehicle characteristics. A positive score means the ask is below the supported peer benchmark. Fewer than five matching seller representatives leaves the listing unscored.

![Synthetic local price comparison showing a listing 3.70 percent below its supported peer benchmark](docs/images/local-price-index.png)

**What will it cost to own?** The ownership model combines the purchase price and taxes, interest incurred during the holding period, fuel, insurance, maintenance and repairs, registration, and expected resale. Each assumption can be changed, and each cost component can be included or excluded.

## Start your research with a coding agent

Give Codex or Claude Code this instruction:

> Read AGENTS.md in this repository. Set up LotLogic on my Mac, show me the synthetic demo, then guide me through researching and comparing cars for my own purchase. Ask for my preferences and confirm any API spending before making live calls.

The agent installs the Python dependencies in a local environment, checks the setup, and collects your buying criteria. MarketCheck access is needed only for live listing research.

The agent researches listings and vehicle assumptions, you confirm quotes and preferences, and a comparison JSON carries those inputs into the local browser app. The workflow produces a candidate workbook, a shortlist workbook, and a saved comparison JSON.

The [setup guide](docs/setup.md) covers installation, live searches, shortlisting, and moving listing prices into the ownership comparison. The [model notes](docs/model.md) explain the formulas and local index. [Example files](examples/) show the input formats. The local index limits each seller to one nearest peer and withholds unsupported scores; the ownership model counts principal once and charges only the interest incurred before sale.

The research utilities include dealer and private-party search, incremental candidate tracking, trim/reference matching, Apple CarPlay classification, shortlist rebuilding, VIN lookup, and optional vehicle-history retrieval. Live results and credentials remain outside version control.

## Check the release

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tests/run_checks.py
```

The checks require Node.js for the JavaScript model tests. Optional browser verification uses Playwright and an isolated Chromium context:

```sh
.venv/bin/python -m pip install -r requirements-browser.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/test_browser.py
```

MIT licensed. This is a static release with no ongoing maintenance commitment.
