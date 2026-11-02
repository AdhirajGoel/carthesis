# Try the CarThesis demo

Open `web/index.html` or double-click `Open CarThesis.command` on macOS. The examples use synthetic vehicles and editable assumptions; they make no API calls.

## Change the buying decision

1. **Start at 30 miles per day.** The new gas car costs $33,933 over five years; the hybrid costs $38,795, or $4,862 more. In **Gas vs hybrid**, its fuel-only payback is 15.2 years.
2. **Set mileage to 100 miles per day.** In this high-mileage stress test, the hybrid becomes about $678 cheaper over the same five years. Reset the demo afterward.
3. **Test the financing.** In **APR crossover**, the used car's purchase saving disappears at about 6.37% over 60 months against the demo's 0% new-car loan. In **Loan & prepay**, select the used car and set its monthly payment to $600: it repays in 51 months and saves about $1,023 in interest.

The APR crossover compares cash through full loan payoff. Ownership cost also includes operating costs and resale; fuel-only payback considers the premium and fuel savings alone.

## Compare the local asking price

Open **Local price index**. The used listing asks $26,000 and scores 3.70% below its supported local peer benchmark. A positive score means the asking price is below that benchmark; a negative score means it is above. Unsupported comparisons stay unscored. The [model notes](model.md#local-price-index) explain how peers are selected.

## Save a comparison

Change the assumptions, edit a vehicle's quote, or choose which costs to include. Use **Save comparison** to download a JSON file. Reopening the app starts the synthetic demo; choose **Open comparison JSON** to restore your saved inputs and selections.

For your own listings and quotes, follow the [setup guide](setup.md) or give your coding agent the prompt in the [README](../README.md#research-your-purchase).
