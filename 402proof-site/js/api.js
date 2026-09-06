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
    if (base) {
      try {
        const data = await fetchJson(`${base}/facilitators`);
        return { source: "api", data: Array.isArray(data) ? data : data.providers || data };
      } catch (err) {
        console.warn("API facilitators failed, falling back to mock:", err.message);
      }
    }
    const mockUrl = cfg().MOCK?.facilitators || "./js/mock-providers.json";
    const data = await fetchJson(mockUrl);
    return { source: "mock", data: Array.isArray(data) ? data : [] };
  }

  async function getEvidence() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (base) {
      try {
        const data = await fetchJson(`${base}/evidence`);
        return { source: "api", data: Array.isArray(data) ? data : data.records || data };
      } catch (err) {
        console.warn("API evidence failed, falling back to mock:", err.message);
      }
    }
    const mockUrl = cfg().MOCK?.evidence || "./js/mock-evidence.json";
    const data = await fetchJson(mockUrl);
    return { source: "mock", data: Array.isArray(data) ? data : [] };
  }

  async function getHealth() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (!base) return { source: "mock", status: "offline" };
    try {
      const data = await fetchJson(`${base}/health`);
      return { source: "api", ...data };
    } catch {
      return { source: "mock", status: "unreachable" };
    }
  }

  async function getStats() {
    const base = (cfg().API_BASE || "").replace(/\/$/, "");
    if (base) {
      try {
        const data = await fetchJson(`${base}/stats`);
        return { source: "api", data };
      } catch (err) {
        console.warn("API stats failed, falling back to mock:", err.message);
      }
    }
    // Derive a mock stats object from local snapshots when API is unavailable
    try {
      const [fac, ev] = await Promise.all([
        fetchJson(cfg().MOCK?.facilitators || "./js/mock-providers.json"),
        fetchJson(cfg().MOCK?.evidence || "./js/mock-evidence.json"),
      ]);
      const providers = Array.isArray(fac) ? fac : [];
      const evidence = Array.isArray(ev) ? ev : [];
      const by_status = {};
      providers.forEach((p) => {
        const s = p.status || "UNKNOWN";
        by_status[s] = (by_status[s] || 0) + 1;
      });
      const by_verdict = {};
      const by_source = {};
      const by_chain = {};
      evidence.forEach((rec) => {
        const summary = rec.summary || {};
        const key = rec.evidenceKey || {};
        const verdict =
          summary.auditVerdict ||
          (summary.auditRecord && summary.auditRecord.verdict) ||
          "UNDETERMINED";
        const source = summary.evidenceSource || "unknown";
        const chain = key.chainId || "unknown";
        by_verdict[verdict] = (by_verdict[verdict] || 0) + 1;
        by_source[source] = (by_source[source] || 0) + 1;
        by_chain[chain] = (by_chain[chain] || 0) + 1;
      });
      return {
        source: "mock",
        data: {
          providers: { total: providers.length, by_status },
          evidence: {
            total: evidence.length,
            by_verdict,
            by_source,
            by_chain,
          },
        },
      };
    } catch {
      return {
        source: "mock",
        data: {
          providers: { total: 0, by_status: {} },
          evidence: {
            total: 0,
            by_verdict: {},
            by_source: {},
            by_chain: {},
          },
        },
      };
    }
  }

  global.ProofAPI = {
    getFacilitators,
    getEvidence,
    getHealth,
    getStats,
  };
})(window);
