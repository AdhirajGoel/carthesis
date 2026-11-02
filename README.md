# CarThesis

CarThesis compares local asking prices and total ownership costs. Test new versus used and gas versus hybrid with your own financing, mileage, fuel, insurance, maintenance, and resale assumptions.

I love data, especially when it changes an everyday decision. This project grew out of wanting to test familiar car-buying advice against local listings and the full cost of ownership.

To try the demo, clone or download this repository, then open `web/index.html` in a browser or double-click `Open CarThesis.command` on macOS. It runs locally with synthetic data, without an account or API call.

![Synthetic new, hybrid, and used vehicles ranked by five-year ownership cost](docs/images/ownership-overview.png)

In this synthetic five-year comparison, the used car asks $4,000 less and sits 3.70% below its local peer benchmark, yet costs $12,385 more to own. Its higher interest, maintenance estimate, and lower resale value outweigh the purchase saving. The [demo walkthrough](docs/demo.md) shows how different mileage and financing change the result.

## Research your purchase

Open the repository in Codex or Claude Code on macOS and give it this instruction:

> Read AGENTS.md. Set up CarThesis, show me the demo, then help me research and compare cars for my purchase. Ask for my preferences and confirm any API spending before making live calls.

The agent guides the listing research and gathers vehicle assumptions; you confirm quotes and preferences. MarketCheck supplies live listings. Candidate and shortlist workbooks hold the research, and a comparison JSON brings your inputs into the browser app. Use **Save comparison** to keep edits and **Open comparison JSON** to restore them.

[Setup and verification](docs/setup.md) · [Model and local price index](docs/model.md) · [Example files](examples/)

MIT licensed. This is a static release with no ongoing maintenance commitment.
