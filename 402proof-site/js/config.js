/**
 * 402Proof site configuration
 * Change API_BASE to the production endpoint when ready.
 * When API is unavailable, the site falls back to local mock snapshots
 * and clearly labels data as development / snapshot.
 */
window.PROOF_CONFIG = {
  API_BASE: "https://x402proof-api.onrender.com",

  MOCK: {
    facilitators: "./js/mock-providers.json",
    evidence: "./js/mock-evidence.json",
  },

  ENVIRONMENT_LABEL: "Base Sepolia · live",
  NETWORK_HINT: "Records shown were measured on Base Sepolia (eip155:84532). This is not production mainnet data.",

  SITE_NAME: "402Proof",
  TAGLINE: "Independent audit and reputation for x402 facilitators",
};
