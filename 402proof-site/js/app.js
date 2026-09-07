/**
 * 402Proof product site — UI layer
 * Consumes ProofAPI only. No hardcoded providers or verdicts.
 */

(function () {
  const cfg = window.PROOF_CONFIG || {};

  // —— Helpers ——
  function truncate(str, start = 10, end = 8) {
    if (!str || str.length <= start + end + 1) return str || "—";
    return str.slice(0, start) + "…" + str.slice(-end);
  }

  function statusClass(status) {
    if (!status) return "no-endpoint";
    const s = String(status).toUpperCase();
    if (s.includes("DECLARATION_CAPTURED") || s.includes("CONFIRMED")) return "declared";
    return "no-endpoint";
  }

  function statusLabel(status) {
    if (!status) return "UNKNOWN";
    return String(status).replace(/_/g, " ");
  }

  function verdictClass(verdict) {
    if (!verdict) return "v-undetermined";
    const v = String(verdict).toUpperCase();
    if (v.startsWith("CONFIRMED")) return "v-confirmed";
    if (v.startsWith("CONTRADICTED")) return "v-contradicted";
    if (v.startsWith("SETTLED")) return "v-settled";
    if (v.startsWith("REJECTED")) return "v-rejected";
    if (v.startsWith("PENDING")) return "v-pending";
    return "v-undetermined";
  }

  function setBanner(el, source, message) {
    if (!el) return;
    el.dataset.source = source;
    el.textContent = message;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // —— Drawer ——
  const drawer = document.getElementById("drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  const drawerTitle = document.getElementById("drawer-title");
  const drawerBody = document.getElementById("drawer-body");
  const drawerClose = document.getElementById("drawer-close");

  function openDrawer(title, html) {
    drawerTitle.textContent = title;
    drawerBody.innerHTML = html;
    drawer.hidden = false;
    backdrop.hidden = false;
    requestAnimationFrame(() => {
      drawer.classList.add("open");
      backdrop.classList.add("open");
    });
    drawerClose.focus();
  }

  function closeDrawer() {
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
    setTimeout(() => {
      drawer.hidden = true;
      backdrop.hidden = true;
      drawerBody.innerHTML = "";
    }, 220);
  }

  drawerClose.addEventListener("click", closeDrawer);
  backdrop.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && drawer.classList.contains("open")) closeDrawer();
  });

  // —— Stats ——
  const VERDICT_ORDER = [
    "CONFIRMED",
    "CONTRADICTED",
    "SETTLED",
    "REJECTED",
    "PENDING",
    "UNDETERMINED",
  ];

  function renderStats(payload, source) {
    const grid = document.getElementById("stats-grid");
    if (!grid) return;

    const data = payload || {};
    const providers = data.providers || {};
    const evidence = data.evidence || {};
    const byVerdict = evidence.by_verdict || {};
    const bySource = evidence.by_source || {};

    const totalProviders = providers.total ?? 0;
    const totalEvidence = evidence.total ?? 0;

    const verdictChips = VERDICT_ORDER.map((v) => {
      let matched = byVerdict[v] ?? byVerdict[v + "_"] ?? 0;
      if (!matched) {
        Object.keys(byVerdict).forEach((k) => {
          if (String(k).toUpperCase().startsWith(v)) matched += byVerdict[k] || 0;
        });
      }
      if (!matched) return "";
      return `<span class="stat-chip"><span class="verdict ${verdictClass(v)}">${escapeHtml(v)}</span> <strong>${matched}</strong></span>`;
    }).filter(Boolean).join("");

    const sourceChips = Object.entries(bySource)
      .map(
        ([src, n]) =>
          `<span class="stat-chip mono">${escapeHtml(src)} <strong>${n}</strong></span>`
      )
      .join("");

    grid.innerHTML = `
      <article class="stat-card">
        <p class="stat-label">Providers</p>
        <p class="stat-value">${totalProviders}</p>
        <p class="stat-sub">tracked facilitators</p>
      </article>
      <article class="stat-card">
        <p class="stat-label">Evidence records</p>
        <p class="stat-value">${totalEvidence}</p>
        <p class="stat-sub">verification records</p>
      </article>
      <article class="stat-card stat-card-wide">
        <p class="stat-label">Verdict distribution</p>
        <div class="stat-chips">${verdictChips || `<span class="stat-empty">No verdicts yet</span>`}</div>
      </article>
      <article class="stat-card stat-card-wide">
        <p class="stat-label">Evidence source</p>
        <div class="stat-chips">${sourceChips || `<span class="stat-empty">No sources yet</span>`}</div>
      </article>
    `;

    grid.dataset.source = source === "mock" ? "mock" : "api";
  }

  // —— Facilitators ——
  function renderFacilitators(list, source) {
    const container = document.getElementById("registry-list");
    const banner = document.getElementById("registry-banner");

    if (source === "mock") {
      setBanner(
        banner,
        "mock",
        `${cfg.ENVIRONMENT_LABEL || "Development snapshot"} · ${list.length} facilitators · not live API`
      );
    } else {
      setBanner(banner, "api", `Live API · ${list.length} facilitators`);
    }

    if (!list.length) {
      container.innerHTML = `<div class="state-msg">No facilitators in the current snapshot.</div>`;
      return;
    }

    container.innerHTML = "";
    list.forEach((p) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "facilitator-row";
      btn.setAttribute("aria-label", `Details for ${p.label || p.provider_id}`);

      const networks = Array.isArray(p.networks) ? p.networks : [];
      const networkPreview =
        networks.length > 3
          ? networks.slice(0, 3).join(", ") + ` +${networks.length - 3}`
          : networks.join(", ") || "—";

      btn.innerHTML = `
        <div>
          <p class="fac-name">${escapeHtml(p.label || p.provider_id)}</p>
          <p class="fac-url">${escapeHtml(p.facilitator_base_url || "")}</p>
          <div class="fac-meta">
            <span class="chip chip-status ${statusClass(p.status)}">${escapeHtml(statusLabel(p.status))}</span>
            ${p.last_seen ? `<span class="chip">last seen ${escapeHtml(String(p.last_seen).slice(0, 19))}</span>` : ""}
          </div>
        </div>
        <div class="fac-side">
          <div>${networks.length} network${networks.length === 1 ? "" : "s"}</div>
          <div class="networks-preview">${escapeHtml(networkPreview)}</div>
        </div>
      `;

      btn.addEventListener("click", () => {
        const nets = networks.map((n) => `<li>${escapeHtml(n)}</li>`).join("") || "<li>—</li>";
        openDrawer(p.label || p.provider_id, `
          <dl>
            <dt>Provider ID</dt><dd>${escapeHtml(p.provider_id)}</dd>
            <dt>Base URL</dt><dd>${escapeHtml(p.facilitator_base_url || "—")}</dd>
            <dt>Supported</dt><dd>${escapeHtml(p.supported_url || "—")}</dd>
            <dt>Verify</dt><dd>${escapeHtml(p.verify_url || "—")}</dd>
            <dt>Settle</dt><dd>${escapeHtml(p.settle_url || "—")}</dd>
            <dt>Declaration URL</dt><dd>${escapeHtml(p.known_declaration_url || "—")}</dd>
            <dt>Status</dt><dd>${escapeHtml(statusLabel(p.status))}</dd>
            <dt>Last seen</dt><dd>${escapeHtml(p.last_seen || "—")}</dd>
            <dt>Networks</dt><dd><ul style="margin:4px 0 0;padding-left:1.2em">${nets}</ul></dd>
          </dl>
        `);
      });

      container.appendChild(btn);
    });
  }

  // —— Evidence (with client-side filter) ——
  let evidenceCache = [];
  let evidenceSource = "mock";

  function extractEvidenceFields(rec) {
    const summary = rec.summary || {};
    const key = rec.evidenceKey || rec.evidence_key || {};
    const vr = summary.verificationRecord || {};
    const verdict =
      summary.auditVerdict ||
      (summary.auditRecord && summary.auditRecord.verdict) ||
      "UNDETERMINED";
    const tx = key.transactionHash || vr.transactionHash || "";
    const provider = summary.providerId || "";
    const source = summary.evidenceSource || "";
    const evidenceDigest = rec.evidenceDigest || rec.evidence_digest || null;
    const storedAt = rec.storedAt || rec.stored_at || null;
    return { summary, key, vr, verdict, tx, provider, source, evidenceDigest, storedAt };
  }

  function matchesFilter(rec, q) {
    if (!q) return true;
    const { verdict, tx, provider, source } = extractEvidenceFields(rec);
    const hay = [tx, provider, verdict, source].join(" ").toLowerCase();
    return hay.includes(q);
  }

  function renderEvidenceList(list) {
    const container = document.getElementById("evidence-list");
    const countEl = document.getElementById("evidence-filter-count");

    if (!list.length) {
      container.innerHTML = `<div class="state-msg">No verification records match the current filter.</div>`;
      if (countEl) countEl.textContent = evidenceCache.length ? "0 matches" : "";
      return;
    }

    if (countEl) {
      countEl.textContent =
        list.length === evidenceCache.length
          ? `${list.length} record${list.length === 1 ? "" : "s"}`
          : `${list.length} of ${evidenceCache.length}`;
    }

    container.innerHTML = "";
    list.forEach((rec) => {
      const { summary, key, vr, verdict, tx, evidenceDigest, storedAt } = extractEvidenceFields(rec);
      const chain = key.chainId || vr.chainId || "—";

      const row = document.createElement("button");
      row.type = "button";
      row.className = "evidence-row";
      row.setAttribute("aria-label", `Evidence for ${truncate(tx)}`);

      row.innerHTML = `
        <div class="ev-top">
          <div>
            <div class="ev-hash">${escapeHtml(truncate(tx, 14, 10))}</div>
            <div class="ev-meta">
              <span class="chip">${escapeHtml(summary.providerId || "—")}</span>
              <span class="chip">${escapeHtml(summary.evidenceSource || "—")}</span>
              <span class="chip">${escapeHtml(vr.networkLabel || chain)}</span>
            </div>
          </div>
          <span class="verdict ${verdictClass(verdict)}">${escapeHtml(verdict)}</span>
        </div>
      `;

      row.addEventListener("click", () => {
        const auth = vr.authorization || {};
        const transfer = vr.transfer || {};
        openDrawer("Verification record", `
          <dl>
            <dt>Transaction</dt><dd>${escapeHtml(tx)}</dd>
            <dt>Chain ID</dt><dd>${escapeHtml(String(chain))}</dd>
            <dt>Provider</dt><dd>${escapeHtml(summary.providerId || "—")}</dd>
            <dt>Evidence source</dt><dd>${escapeHtml(summary.evidenceSource || "—")}</dd>
            <dt>Evidence digest</dt><dd>${escapeHtml(evidenceDigest || "—")}</dd>
            <dt>Stored at</dt><dd>${escapeHtml(storedAt || "—")}</dd>
            <dt>Verification status</dt><dd>${escapeHtml(summary.verificationStatus || vr.verificationStatus || "—")}</dd>
            <dt>Audit verdict</dt><dd><span class="verdict ${verdictClass(verdict)}">${escapeHtml(verdict)}</span></dd>
            <dt>Receipt status</dt><dd>${escapeHtml(String(vr.receiptStatus ?? "—"))}</dd>
            <dt>Block</dt><dd>${escapeHtml(String(vr.blockNumber ?? "—"))}</dd>
            <dt>Transfer</dt><dd>${transfer.found ? `${escapeHtml(String(transfer.value))} · ${escapeHtml(truncate(transfer.from))} → ${escapeHtml(truncate(transfer.to))}` : "not found"}</dd>
            <dt>Authorization</dt><dd>${auth.found ? `selector ${escapeHtml(auth.selector || "—")}${auth.hasEIP3009Selector ? " · EIP-3009" : ""}` : "not found"}</dd>
            <dt>Resource</dt><dd>${escapeHtml(summary.resourceUrl || "—")}</dd>
          </dl>
          <p style="margin-top:var(--space-5);font-size:var(--text-xs);color:var(--text-muted)">
            ${escapeHtml(cfg.NETWORK_HINT || "")}
          </p>
        `);
      });

      container.appendChild(row);
    });
  }

  function applyEvidenceFilter() {
    const input = document.getElementById("evidence-search");
    const q = (input && input.value ? input.value : "").trim().toLowerCase();
    const filtered = evidenceCache.filter((rec) => matchesFilter(rec, q));
    renderEvidenceList(filtered);
  }

  function renderEvidence(list, source) {
    const banner = document.getElementById("evidence-banner");
    evidenceCache = Array.isArray(list) ? list : [];
    evidenceSource = source;

    if (source === "mock") {
      setBanner(
        banner,
        "mock",
        `${cfg.ENVIRONMENT_LABEL || "Development snapshot"} · ${list.length} record(s) · illustrative structure from indexer`
      );
    } else {
      setBanner(banner, "api", `Live API · ${list.length} record(s)`);
    }

    applyEvidenceFilter();
  }

  // —— Mobile nav ——
  const menuBtn = document.getElementById("menu-btn");
  const nav = document.getElementById("site-nav");
  menuBtn.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
  });
  nav.querySelectorAll("a").forEach((a) => {
    a.addEventListener("click", () => {
      nav.classList.remove("open");
      menuBtn.setAttribute("aria-expanded", "false");
    });
  });

  // —— Search listener ——
  const searchInput = document.getElementById("evidence-search");
  if (searchInput) {
    searchInput.addEventListener("input", applyEvidenceFilter);
  }

  // —— Boot ——
  async function boot() {
    try {
      const [fac, ev, stats] = await Promise.all([
        window.ProofAPI.getFacilitators(),
        window.ProofAPI.getEvidence(),
        window.ProofAPI.getStats(),
      ]);
      renderStats(stats.data, stats.source);
      renderFacilitators(fac.data, fac.source);
      renderEvidence(ev.data, ev.source);
    } catch (err) {
      console.error(err);
      document.getElementById("registry-list").innerHTML =
        `<div class="state-msg">Unable to load registry data.</div>`;
      document.getElementById("evidence-list").innerHTML =
        `<div class="state-msg">Unable to load evidence data.</div>`;
      const statsGrid = document.getElementById("stats-grid");
      if (statsGrid) {
        statsGrid.innerHTML = `<div class="state-msg">Unable to load statistics.</div>`;
      }
      setBanner(document.getElementById("registry-banner"), "mock", "Data unavailable");
      setBanner(document.getElementById("evidence-banner"), "mock", "Data unavailable");
    }
  }

  boot();
  setInterval(boot, 30000);
})();
