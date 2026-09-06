/**
 * 402Proof site configuration
 * Change API_BASE to the production endpoint when ready.
 * When API is unavailable, the site falls back to local mock snapshots
 * and clearly labels data as development / snapshot.
 */
window.PROOF_CONFIG = {
  // Set to your live API origin, e.g. "https://api.402proof.example"
  // Leave empty or null to use local mock data only.
  API_BASE: "",

  // Relative paths used when API_BASE is empty or the request fails
  MOCK: {
    facilitators: "./js/mock-providers.json",
    evidence: "./js/mock-evidence.json",
  },

  // Network labels for clarity (testnet / development)
  ENVIRONMENT_LABEL: "Base Sepolia · development snapshot",
  NETWORK_HINT: "Records shown were measured on Base Sepolia (eip155:84532). This is not production mainnet data.",

  // Display
  SITE_NAME: "402Proof",
  TAGLINE: "Independent audit and reputation for x402 facilitators",
};
