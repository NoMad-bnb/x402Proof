# 402Proof official site

Independent audit and reputation layer for x402 facilitators.

## Stack

- Static HTML / CSS / vanilla JS
- Brand tokens from the 402Proof Brand Identity Kit (v1.1)
- Typography: Fraunces (display), Outfit (UI), IBM Plex Mono (machine)
- No framework required

## Structure

```
index.html          Product page (single document, sectioned)
css/
  tokens.css        Design tokens (light + dark)
  fonts.css         @font-face rules
  main.css          Layout and components
js/
  config.js         API_BASE and environment labels
  api.js            Data layer (API client + mock fallback)
  app.js            UI rendering, drawer, nav
  mock-providers.json   Development snapshot from indexer
  mock-evidence.json    Development snapshot from indexer
assets/
  fonts/  logo/  icons/  graphics/
```

## Connecting the real API

1. Deploy the indexer FastAPI (`indexer/api.py`).
2. Set in `js/config.js`:

```js
API_BASE: "https://your-api-origin.example"
```

3. Reload. The site will call:

- `GET /facilitators`
- `GET /evidence`
- `GET /health`

If the API is unreachable, it falls back to local mock JSON and labels the data as a development snapshot. No rebuild required.

## Local preview

```bash
cd 402proof-site
python3 -m http.server 8765
# open http://127.0.0.1:8765
```

## Data honesty

- Facilitator list comes from the project `providers.json` (5 known providers).
- Evidence records come from the project `evidence.json` (indexer development snapshot on Base Sepolia).
- The UI never invents providers, verdicts, or endpoints.
- Mock/API source is shown in a banner on Registry and Evidence sections.
- Verdict families match the contract language: CONFIRMED, CONTRADICTED, SETTLED_*, REJECTED_*, PENDING_*, UNDETERMINED_*.

## Brand fidelity

Colors, type scale, radius, shadows, and status language follow `tokens/tokens.css` from the brand kit. Logo lockup and favicon are the official marks. Sequence language: Claim → Evidence → Verification → Proof → Reputation.
