# How the comparisons work

## Ownership cost

The model adds purchase price, sales tax, one-time fees, interest incurred through the ownership horizon, fuel, insurance, maintenance/repairs, and recurring registration, then subtracts resale value. The purchase term already includes principal, so monthly loan payments and remaining principal are not added again. The loan view separately shows the balance due at sale.

Loans use monthly simple-interest amortization. The default payment amortizes principal over the entered term. Higher payments shorten repayment; a payment below the contractual minimum is rejected. A zero-interest loan divides principal evenly by its term. Loan fees belong in the entered purchase fees; confirm the loan's treatment of prepayments with the lender.

Annual mileage is miles per day × 365. Fuel cost is mileage ÷ MPG × fuel price. Operating expenses grow at the entered annual inflation rate, with proportional costs for partial years. Resale fractions interpolate linearly between purchase, year five, and year ten, then extend the last slope with a zero floor. Enter fractions appropriate to the car's age at purchase. Values at five and ten years are assumptions, not forecasts inferred from its name.

Equivalent monthly ownership cost is the net cost divided by months held. It is not the monthly cash payment or a loan affordability test.

## Gas versus hybrid

Vehicles share a `pair` identifier only when the buyer or research agent has matched their variants. Fuel-only payback divides the upfront premium by annual fuel savings. The full comparison subtracts the hybrid's modeled ownership cost from the gas vehicle's, so financing, resale, and every enabled cost component can change the conclusion.

The synthetic demo deliberately contains a case where the hybrid's premium does not repay itself. Changing the assumptions can reverse that result. These examples describe the model's behavior, not current offers or a market-wide finding.

## Financing crossover

The APR crossover view compares purchase price, taxes, fees, and total scheduled interest through full payoff. It varies the used-car APR across 36-, 48-, and 60-month terms while holding the new-car financing fixed. The threshold is the rate where used-car savings fall to zero or to the buyer's entered preference for extra savings. Operating costs and resale remain in the separate ownership comparison.

## Local price index

`add_deal_edge.py` reuses the research tool's seller-balanced nearest-peer calculation. It requires a valid unique VIN, price, mileage, year, seller, verified current-trim match, matching reference MSRP, and resolved drivetrain. Comparison groups match the current benchmark, body, powertrain, drivetrain, and certification status.

Within a group, age and mileage differences are scaled by their seller-level interquartile ranges, with floors of one year and 10,000 miles. The closest listing represents each seller. The five nearest seller representatives form the comparison only when the furthest remains within the distance threshold and their age and mileage ranges bracket the target. The target VIN is excluded.

The benchmark is the median peer asking price adjusted to the target reference MSRP. Deal edge is `100 × (1 − asking price / benchmark)`. A positive value means below the benchmark. Insufficient or incompatible evidence produces an empty score. The geography comes from the source search, so the input workbook must represent the intended local market.

## Comparison files

Use `examples/comparison.json` as the version 1 input format. `maint` is annual maintenance plus repairs; `insurance` is an optional vehicle-specific annual quote. `r5` and `r10` are resale fractions of today's purchase price after another five and ten years of ownership. `term` optionally overrides the common loan term. `source` and `checkedAt` preserve the origin and date of assumptions.

Saved files retain `enabled` (the included cost components) and `preferencePremium` (extra savings required to choose used). Older files without these fields include all components and require $0 extra savings. Import errors leave the previous comparison intact. Editing a vehicle's pricing or identity clears its previous local score.

## Files and evidence

- `web/model.js` owns the ownership and loan calculations; `web/app.js` renders them.
- `add_deal_edge.py` owns the local comparison; `vehicle_reference.py` matches trims and equipment.
- `search.py` and `search_private.py` retrieve and track candidates.
- `carthesis.py compare` joins candidate prices and scores to buyer-reviewed assumptions.
- `examples/local-listings.json` reproduces the synthetic local score; `examples/comparison.json` reproduces the browser example.

The offline tests cover amortization, prepayment, early sale, hybrid comparisons, input validation, the local index, workbook export, search filtering, and the retained utilities. Browser tests exercise all views, edit/save, imports, invalid data, and narrow layouts.

Public vehicle-history facts are accepted only when the report body identifies the requested VIN without a conflicting identity. Missing or mismatched VINs stay unverified; older cached reports without VIN verification must be checked again.
