/**
 * API client for 402Proof.
 * Separates data fetching from UI. Easy to point at a real FastAPI backend.
 *
 * Real endpoints (from indexer/api.py):
 *   GET /health
 *   GET /facilitators
 *   GET /facilitators/{id}
 *   GET /evidence
 *   GET /evidence/{chain}/{tx}
 *   GET /stats
 */

(function (global) {
  const cfg = () => global.PROOF_CONFIG || {};

  async function fetchJson(url) {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.json();
  }

  async function getFacilitators() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (!base) {
      throw new Error("API_BASE is not configured");
    }
    const data = await fetchJson(`${base}/facilitators`);
    return { source: "api", data: Array.isArray(data) ? data : data.providers || data };
  }

  async function getEvidence() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (!base) {
      throw new Error("API_BASE is not configured");
    }
    const data = await fetchJson(`${base}/evidence`);
    return { source: "api", data: Array.isArray(data) ? data : data.records || data };
  }

  async function getHealth() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (!base) {
      return { source: "offline", status: "unconfigured" };
    }
    try {
      const data = await fetchJson(`${base}/health`);
      return { source: "api", ...data };
    } catch {
      return { source: "offline", status: "unreachable" };
    }
  }

  async function getStats() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (!base) {
      throw new Error("API_BASE is not configured");
    }
    const data = await fetchJson(`${base}/stats`);
    return { source: "api", data };
  }

  global.ProofAPI = {
    getFacilitators,
    getEvidence,
    getHealth,
    getStats,
  };
})(window);
