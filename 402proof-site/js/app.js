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

  function isValidEthHash(hash) {
    if (!hash || typeof hash !== "string") return false;
    return /^0x[0-9a-fA-F]{64}$/.test(hash);
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

  function renderStats(payload) {
    const grid = document.getElementById("stats-grid");
    if (!grid) return;

    const data = payload || {};
    const providers = data.providers || {};
    const evidence = data.evidence || {};
    const byVerdict = evidence.by_verdict || {};
    const byVerdictTx = evidence.by_verdict_transactions || {};
    const bySource = evidence.by_source || {};

    const totalProviders = providers.total ?? 0;
    const totalEvidence = evidence.total ?? 0;
    const totalTransactions = evidence.total_transactions ?? 0;
    const hasTxCounts = Object.keys(byVerdictTx).length > 0;

    // Verdict keys carry long reasons after a prefix, so a family matches by prefix.
    function familyTotal(counts, family) {
      const exact = counts[family] ?? counts[family + "_"] ?? 0;
      if (exact) return exact;
      let matched = 0;
      Object.keys(counts).forEach((k) => {
        if (String(k).toUpperCase().startsWith(family)) matched += counts[k] || 0;
      });
      return matched;
    }

    const verdictChips = VERDICT_ORDER.map((v) => {
      const records = familyTotal(byVerdict, v);
      const transactions = hasTxCounts ? familyTotal(byVerdictTx, v) : records;
      if (!records && !transactions) return "";
      const noun = transactions === 1 ? "settlement transaction" : "settlement transactions";
      const title =
        transactions === records
          ? `${transactions} ${noun}`
          : `${transactions} ${noun}, ${records} evidence records`;
      return `<span class="stat-chip" title="${escapeHtml(title)}"><span class="verdict ${verdictClass(v)}">${escapeHtml(v)}</span> <strong>${transactions}</strong> <span class="stat-chip-sub">tx</span></span>`;
    }).filter(Boolean).join("");

    const verdictNote = totalTransactions
      ? `Counted once per settlement transaction, across ${totalEvidence} evidence records. One transaction lands in more than one family when more than one claim was audited for it.`
      : "";

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
        ${verdictNote ? `<p class="stat-sub">${verdictNote}</p>` : ""}
      </article>
      <article class="stat-card stat-card-wide">
        <p class="stat-label">Evidence source</p>
        <div class="stat-chips">${sourceChips || `<span class="stat-empty">No sources yet</span>`}</div>
      </article>
    `;

    grid.dataset.source = "api";
  }

  // —— Facilitators ——
  function renderFacilitators(list) {
    const container = document.getElementById("registry-list");
    const banner = document.getElementById("registry-banner");

    const state = freshnessState(lastUpdateTime);
    if (state === "stale") {
      const age = formatAge(Date.now() - Date.parse(lastUpdateTime));
      setBanner(banner, "offline", `Offline · last update ${age}`);
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
  let evidenceSource = "api";
  const ROWS_PER_PAGE_OPTIONS = [6, 10];
  let evidencePageSize = 6;
  let evidencePage = 1;

  function getFilteredEvidence() {
    const input = document.getElementById("evidence-search");
    const q = (input && input.value ? input.value : "").trim().toLowerCase();
    return evidenceCache.filter((rec) => matchesFilter(rec, q));
  }

  function rerenderEvidence() {
    const filtered = getFilteredEvidence();
    renderEvidenceList(filtered);
  }

  function buildPagerButton(label, ariaLabel, onClick, disabled, isActive) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = label;
    btn.setAttribute("aria-label", ariaLabel);
    btn.className = "chip" + (isActive ? " chip-status declared" : "");
    if (disabled) {
      btn.disabled = true;
      btn.setAttribute("aria-disabled", "true");
    } else {
      btn.addEventListener("click", onClick);
    }
    return btn;
  }

  function renderEvidencePagination(totalItems) {
    const listEl = document.getElementById("evidence-list");
    let pager = document.getElementById("evidence-pager");
    if (!pager) {
      pager = document.createElement("div");
      pager.id = "evidence-pager";
      pager.style.display = "flex";
      pager.style.flexWrap = "wrap";
      pager.style.gap = "8px";
      pager.style.justifyContent = "center";
      pager.style.alignItems = "center";
      pager.style.marginTop = "var(--space-5)";
      listEl.insertAdjacentElement("afterend", pager);
    }

    const totalPages = Math.max(1, Math.ceil(totalItems / evidencePageSize));
    if (evidencePage > totalPages) evidencePage = totalPages;
    if (evidencePage < 1) evidencePage = 1;

    const isFirst = evidencePage <= 1;
    const isLast = evidencePage >= totalPages;

    pager.innerHTML = "";
    pager.appendChild(
      buildPagerButton(
        "First",
        "Go to first page",
        () => { evidencePage = 1; rerenderEvidence(); listEl.scrollIntoView({ behavior: "smooth", block: "start" }); },
        isFirst,
        false
      )
    );
    pager.appendChild(
      buildPagerButton(
        "←",
        "Previous page",
        () => { evidencePage = evidencePage - 1; rerenderEvidence(); listEl.scrollIntoView({ behavior: "smooth", block: "start" }); },
        isFirst,
        false
      )
    );

    const pageInfo = document.createElement("span");
    pageInfo.textContent = "Page " + evidencePage + " of " + totalPages;
    pageInfo.className = "chip";
    pageInfo.style.padding = "6px 12px";
    pageInfo.setAttribute("aria-label", "Current page " + evidencePage + " of " + totalPages);
    pager.appendChild(pageInfo);

    pager.appendChild(
      buildPagerButton(
        "→",
        "Next page",
        () => { evidencePage = evidencePage + 1; rerenderEvidence(); listEl.scrollIntoView({ behavior: "smooth", block: "start" }); },
        isLast,
        false
      )
    );
    pager.appendChild(
      buildPagerButton(
        "Last",
        "Go to last page",
        () => { evidencePage = totalPages; rerenderEvidence(); listEl.scrollIntoView({ behavior: "smooth", block: "start" }); },
        isLast,
        false
      )
    );

    const select = document.createElement("select");
    select.id = "rows-per-page";
    select.setAttribute("aria-label", "Rows per page");
    select.style.padding = "6px 10px";
    select.style.borderRadius = "var(--radius-sm, 6px)";
    select.style.border = "1px solid var(--border, rgba(0,0,0,0.1))";
    select.style.background = "var(--bg, transparent)";
    select.style.color = "inherit";
    select.style.font = "inherit";
    ROWS_PER_PAGE_OPTIONS.forEach(function (n) {
      const opt = document.createElement("option");
      opt.value = String(n);
      opt.textContent = n + " / page";
      if (n === evidencePageSize) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener("change", function () {
      const next = parseInt(select.value, 10);
      if (ROWS_PER_PAGE_OPTIONS.indexOf(next) === -1) return;
      evidencePageSize = next;
      evidencePage = 1;
      rerenderEvidence();
    });
    pager.appendChild(select);

    pager.hidden = totalItems === 0;
  }

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
      renderEvidencePagination(0);
      return;
    }

    if (countEl) {
      countEl.textContent =
        list.length === evidenceCache.length
          ? `${list.length} record${list.length === 1 ? "" : "s"}`
          : `${list.length} of ${evidenceCache.length}`;
    }

    container.innerHTML = "";
    const start = (evidencePage - 1) * evidencePageSize;
    const pageItems = list.slice(start, start + evidencePageSize);
    pageItems.forEach((rec) => {
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
              ${summary.scope ? `<span class="chip">${escapeHtml(summary.scope)}</span>` : ""}
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
        const baseTx = tx || vr.transactionHash || "";
        const genTx = summary.genLayerTxHash || "";
        const hasBase = isValidEthHash(baseTx);
        const hasGen = isValidEthHash(genTx);
        const baseUrl = hasBase ? "https://sepolia.basescan.org/tx/" + baseTx : null;
        const genUrl = hasGen ? "https://explorer-studio.genlayer.com/tx/" + genTx : null;
        const contractUrl = "https://explorer-studio.genlayer.com/address/0xc40f7bADb1E340C78E20CdEf8722114bBEb53e98";
        const checklistItems = window.EvidenceChecklist ? window.EvidenceChecklist.build(rec) : [];
        const checklistHtml = checklistItems.map((entry) => `
          <li class="check-item check-${entry.status}">
            <span class="check-mark" aria-hidden="true">${entry.status === "pass" ? "✓" : entry.status === "fail" ? "✗" : "?"}</span>
            <span class="check-body">
              <span class="check-label">${escapeHtml(entry.label)}</span>
              ${entry.detail ? `<span class="check-detail">${escapeHtml(entry.detail)}</span>` : ""}
            </span>
          </li>`).join("");

        openDrawer("Verification record", `
          <dl>
            <dt>Transaction</dt><dd>${escapeHtml(tx)}</dd>
            <dt>Chain ID</dt><dd>${escapeHtml(String(chain))}</dd>
            <dt>Provider</dt><dd>${escapeHtml(summary.providerId || "—")}</dd>
            ${summary.scope ? `<dt>Scope</dt><dd>${escapeHtml(summary.scope)}</dd>` : ""}
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
          <section class="checklist">
            <h3>What was checked</h3>
            <ul class="check-list">${checklistHtml}</ul>
          </section>
          <section class="verification-actions">
            <h3>Independent Verification</h3>
            <div class="action-buttons">
              <a class="btn btn-ghost" ${hasBase ? `href="${baseUrl}" target="_blank" rel="noopener"` : "disabled"} title="${hasBase ? "View on Base Sepolia Explorer" : "Base transaction hash not available"}">
                View Base Sepolia ↗
              </a>
              <a class="btn btn-ghost" ${hasGen ? `href="${genUrl}" target="_blank" rel="noopener"` : "disabled"} title="${hasGen ? "View on GenLayer Explorer" : "GenLayer transaction hash not available"}">
                View GenLayer Audit ↗
              </a>
              <a class="btn btn-primary" href="${contractUrl}" target="_blank" rel="noopener">
                Verify Verdict On-Chain ↗
              </a>
            </div>
            <p class="action-hint">
              On-Chain Verdict opens the X402Auditor contract on GenLayer Explorer. Use <code>get_verdicts()</code> and match <code>claimTransaction</code> to the Base transaction above.
            </p>
          </section>
          <p style="margin-top:var(--space-5);font-size:var(--text-xs);color:var(--text-muted)">
            ${escapeHtml(cfg.NETWORK_HINT || "")}
          </p>
        `);
      });

      container.appendChild(row);
    });
    renderEvidencePagination(list.length);
  }

  function applyEvidenceFilter() {
    const input = document.getElementById("evidence-search");
    const q = (input && input.value ? input.value : "").trim().toLowerCase();
    const filtered = evidenceCache.filter((rec) => matchesFilter(rec, q));
    renderEvidenceList(filtered);
  }

  function renderEvidence(list) {
    const banner = document.getElementById("evidence-banner");
    evidenceCache = Array.isArray(list) ? list : [];
    evidenceSource = "api";

    const state = freshnessState(lastUpdateTime);
    if (state === "stale") {
      const age = formatAge(Date.now() - Date.parse(lastUpdateTime));
      setBanner(banner, "offline", `Offline · last update ${age}`);
    } else {
      setBanner(banner, "api", `Live API · ${list.length} record(s)`);
    }

    applyEvidenceFilter();
  }

  // -- On-chain registry (observed relayers) --
  let onchainCache = [];
  let onchainPage = 1;
  let onchainPageSize = 6;

  function renderOnchainRegistry(payload) {
    const banner = document.getElementById("onchain-banner");
    const summaryEl = document.getElementById("onchain-summary");
    const container = document.getElementById("onchain-registry-list");
    if (!banner || !summaryEl || !container) return;

    const data = payload || {};
    const byRelayer = Array.isArray(data.byRelayer) ? data.byRelayer : [];
    const summary = data.summary || {};

    onchainCache = byRelayer;

    if (data.status !== "ok") {
      onchainPage = 1;
      setBanner(banner, "offline", "No on-chain snapshot yet");
      summaryEl.innerHTML = "";
      container.innerHTML =
        `<div class="state-msg">The indexer has not pushed a registry snapshot yet.</div>`;
      buildOnchainPager(0);
      return;
    }

    const readAt = data.readAt ? String(data.readAt).slice(0, 19) : "";
    setBanner(banner, "api", `On-chain registry · read ${readAt} · ${byRelayer.length} relayer(s)`);

    const labelConflicts = summary.labelRelayerConflict || { count: 0, labels: [] };
    const relayerConflicts = summary.relayerLabelConflict || { count: 0, relayers: [] };

    summaryEl.innerHTML = "";
    if (relayerConflicts.count || labelConflicts.count) {
      summaryEl.appendChild(buildConflictTile(
        "Relayer label conflicts",
        relayerConflicts.count,
        "settling addresses named with more than one label · view breakdown",
        () => openConflictsDrawer(data)
      ));
      summaryEl.appendChild(buildConflictTile(
        "Label relayer conflicts",
        labelConflicts.count,
        "labels settled from more than one address · view breakdown",
        () => openConflictsDrawer(data)
      ));
    } else {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = "no conflicts flagged";
      summaryEl.appendChild(chip);
    }

    renderOnchainList();
  }

  function buildConflictTile(labelText, count, subText, onClick) {
    const tile = document.createElement("button");
    tile.type = "button";
    tile.className = "conflict-card";
    tile.setAttribute("aria-label", labelText + ": " + count + ", view breakdown");
    tile.innerHTML = `
      <p class="stat-label">${escapeHtml(labelText)}</p>
      <p class="stat-value">${Number(count)}</p>
      <p class="stat-sub">${escapeHtml(subText)}</p>
    `;
    tile.addEventListener("click", onClick);
    return tile;
  }

  function openConflictsDrawer(data) {
    const byRelayer = Array.isArray(data.byRelayer) ? data.byRelayer : [];
    const byLabel = Array.isArray(data.byLabel) ? data.byLabel : [];
    const conflictingRelayers = byRelayer.filter((e) => e.relayerLabelConflict === true);
    const conflictingLabels = byLabel.filter((e) => e.labelRelayerConflict === true);
    const relayerBlock = conflictingRelayers.map((entry) => `
      <div class="conflict-row">
        <div class="ev-hash">${escapeHtml(String(entry.relayer || "unknown"))}</div>
        <div class="ev-meta">${(entry.labels || []).map((l) => `<span class="chip">${escapeHtml(String(l))}</span>`).join("")}</div>
      </div>`).join("");
    const labelBlock = conflictingLabels.map((entry) => `
      <div class="conflict-row">
        <div class="ev-hash">${escapeHtml(String(entry.facilitator || "unlabeled"))}</div>
        <div class="ev-meta">${(entry.relayers || []).map((r) => `<span class="chip mono">${escapeHtml(truncate(String(r), 12, 8))}</span>`).join("")}</div>
      </div>`).join("");
    openDrawer("Conflict detail", `
      <section class="conflict-section">
        <h3>Relayer label conflicts</h3>
        <p class="stat-sub">One settling address, more than one typed label. Naming inconsistency, published rather than hidden.</p>
        ${relayerBlock || `<p class="stat-sub">None in this snapshot.</p>`}
      </section>
      <section class="conflict-section">
        <h3>Label relayer conflicts</h3>
        <p class="stat-sub">One typed label, more than one settling address. Not proof of anything by itself.</p>
        ${labelBlock || `<p class="stat-sub">None in this snapshot.</p>`}
      </section>
      <p class="action-hint">
        Recompute both groupings yourself: call <code>get_registry()</code> and <code>get_registry_by_relayer()</code> on the X402Auditor contract and compare the conflict flags.
      </p>
    `);
  }

  function renderOnchainList() {
    const container = document.getElementById("onchain-registry-list");
    if (!container) return;
    const totalPages = Math.max(1, Math.ceil(onchainCache.length / onchainPageSize));
    if (onchainPage > totalPages) onchainPage = totalPages;
    const start = (onchainPage - 1) * onchainPageSize;
    const pageItems = onchainCache.slice(start, start + onchainPageSize);
    container.innerHTML = "";
    pageItems.forEach((entry) => {
      container.appendChild(buildRelayerRow(entry));
    });
    buildOnchainPager(onchainCache.length);
  }

  function buildRelayerRow(entry) {
    const address = entry.relayer || "unknown";
    const labels = Array.isArray(entry.labels) ? entry.labels : [];
    const conflict = entry.relayerLabelConflict === true;
    const honesty = entry.announcementHonestyPct;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "evidence-row";
    row.setAttribute("aria-label", "Relayer " + truncate(address));
    row.innerHTML = `
      <div class="ev-top">
        <div>
          <div class="ev-hash">${escapeHtml(truncate(address, 14, 10))}</div>
          <div class="ev-meta">
            <span class="chip">audits: ${Number(entry.totalRecords ?? 0)}</span>
            <span class="chip">settled: ${Number(entry.settledOnChain ?? 0)}</span>
            <span class="chip">honesty: ${honesty == null ? "n/a" : escapeHtml(String(honesty)) + "%"}</span>
          </div>
        </div>
        <span class="chip ${conflict ? "chip-conflict" : ""}">${conflict ? "label conflict" : "no conflicts"}</span>
      </div>
    `;
    row.addEventListener("click", () => openRelayerDrawer(entry));
    return row;
  }

  function openRelayerDrawer(entry) {
    const address = entry.relayer || "unknown";
    const labels = Array.isArray(entry.labels) ? entry.labels : [];
    const rows = [
      ["Relayer address", address],
      ["Labels", labels.length ? labels.join(", ") : "none"],
      ["Total records", entry.totalRecords ?? 0],
      ["Settled on chain", entry.settledOnChain ?? 0],
      ["Settled atomic total", entry.settledAtomicTotal ?? 0],
      ["Confirmed", entry.confirmed ?? 0],
      ["Contradicted failure", entry.contradictedFailure ?? 0],
      ["Settled no claim", entry.settledNoClaim ?? 0],
      ["Settled no announcement captured", entry.settledNoAnnouncementCaptured ?? 0],
      ["Legacy unclassified silence", entry.legacyUnclassifiedSilence ?? 0],
      ["Pending", entry.pending ?? 0],
      ["Rejected", entry.rejected ?? 0],
      ["Undetermined", entry.undetermined ?? 0],
      ["Unremarkable", entry.unremarkable ?? 0],
      ["Duplicate audits", entry.duplicateAudits ?? 0],
      ["Ambiguous", entry.ambiguous ?? 0],
      ["Evidence: self probe", entry.evidenceSelfProbe ?? 0],
      ["Evidence: reported", entry.evidenceReported ?? 0],
      ["Evidence: discovered only", entry.evidenceDiscoveredOnly ?? 0],
      [
        "Announcement honesty",
        entry.announcementHonestyPct == null
          ? "n/a (no denominator yet)"
          : entry.announcementHonestyPct + "%",
      ],
    ];
    const dl = rows.map(([k, v]) => `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd>`).join("");
    const isBucket = address === "NO_RELAYER_OBSERVED";
    const isEvmAddress = /^0x[0-9a-fA-F]{40}$/.test(address);
    const relayerUrl = isEvmAddress ? "https://sepolia.basescan.org/address/" + address : null;
    const disabledTitle = isBucket
      ? "This is a bucket label for records with no observed settling relayer, not a chain address"
      : "Not a valid Base Sepolia address";
    const bucketHtml = isBucket
      ? `<p class="action-hint">NO_RELAYER_OBSERVED is a contract-level bucket for records where no settling relayer was observed. It has no chain address, so there is nothing to open on an explorer.</p>`
      : "";
    const conflictHtml = entry.relayerLabelConflict === true
      ? `<p class="action-hint">relayerLabelConflict: this settling address was named with more than one typed label. A naming inconsistency, published rather than hidden.</p>`
      : "";
    openDrawer("Relayer detail", `
      <dl>${dl}</dl>
      ${conflictHtml}
      ${bucketHtml}
      <section class="verification-actions">
        <h3>Independent Verification</h3>
        <div class="action-buttons">
          <a class="btn btn-primary" ${relayerUrl ? `href="${relayerUrl}" target="_blank" rel="noopener"` : "disabled"} title="${relayerUrl ? "View the settling address on Base Sepolia" : disabledTitle}">
            View on BaseScan ↗
          </a>
        </div>
        <p class="action-hint">
          The relayer is the Base Sepolia address that broadcast the settlement. Grouping by it cannot be forged by whoever calls audit(). Recompute by calling <code>get_registry_by_relayer()</code> on the X402Auditor contract and compare to this drawer.
        </p>
      </section>
    `);
  }

  function buildOnchainPager(totalItems) {
    const listEl = document.getElementById("onchain-registry-list");
    let pager = document.getElementById("onchain-pager");
    if (!pager) {
      pager = document.createElement("div");
      pager.id = "onchain-pager";
      pager.style.display = "flex";
      pager.style.flexWrap = "wrap";
      pager.style.gap = "8px";
      pager.style.justifyContent = "center";
      pager.style.alignItems = "center";
      pager.style.marginTop = "var(--space-5)";
      listEl.insertAdjacentElement("afterend", pager);
    }

    const totalPages = Math.max(1, Math.ceil(totalItems / onchainPageSize));
    if (onchainPage > totalPages) onchainPage = totalPages;
    if (onchainPage < 1) onchainPage = 1;
    const isFirst = onchainPage <= 1;
    const isLast = onchainPage >= totalPages;
    const goTo = (page) => {
      onchainPage = page;
      renderOnchainList();
      listEl.scrollIntoView({ behavior: "smooth", block: "start" });
    };

    pager.innerHTML = "";
    pager.appendChild(buildPagerButton("First", "Go to first page", () => goTo(1), isFirst, false));
    pager.appendChild(buildPagerButton("←", "Previous page", () => goTo(onchainPage - 1), isFirst, false));
    const pageInfo = document.createElement("span");
    pageInfo.textContent = "Page " + onchainPage + " of " + totalPages;
    pageInfo.className = "chip";
    pageInfo.style.padding = "6px 12px";
    pageInfo.setAttribute("aria-label", "Current page " + onchainPage + " of " + totalPages);
    pager.appendChild(pageInfo);
    pager.appendChild(buildPagerButton("→", "Next page", () => goTo(onchainPage + 1), isLast, false));
    pager.appendChild(buildPagerButton("Last", "Go to last page", () => goTo(totalPages), isLast, false));

    const select = document.createElement("select");
    select.id = "onchain-rows-per-page";
    select.setAttribute("aria-label", "Rows per page");
    select.style.padding = "6px 10px";
    select.style.borderRadius = "var(--radius-sm, 6px)";
    select.style.border = "1px solid var(--border, rgba(0,0,0,0.1))";
    select.style.background = "var(--bg, transparent)";
    select.style.color = "inherit";
    select.style.font = "inherit";
    ROWS_PER_PAGE_OPTIONS.forEach(function (n) {
      const opt = document.createElement("option");
      opt.value = String(n);
      opt.textContent = n + " / page";
      if (n === onchainPageSize) opt.selected = true;
      select.appendChild(opt);
    });
    select.addEventListener("change", function () {
      const next = parseInt(select.value, 10);
      if (ROWS_PER_PAGE_OPTIONS.indexOf(next) === -1) return;
      onchainPageSize = next;
      onchainPage = 1;
      renderOnchainList();
    });
    pager.appendChild(select);

    pager.hidden = totalItems === 0;
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
    searchInput.addEventListener("input", () => {
      evidencePage = 1;
      applyEvidenceFilter();
    });
  }

  // —— Boot ——
  const STALE_THRESHOLD_MS = 60 * 60 * 1000;

  function formatAge(ms) {
    if (ms < 60 * 1000) return "just now";
    if (ms < 60 * 60 * 1000) {
      const mins = Math.floor(ms / (60 * 1000));
      return mins + " min ago";
    }
    if (ms < 24 * 60 * 60 * 1000) {
      const hrs = Math.floor(ms / (60 * 60 * 1000));
      return hrs + " hour" + (hrs === 1 ? "" : "s") + " ago";
    }
    const days = Math.floor(ms / (24 * 60 * 60 * 1000));
    return days + " day" + (days === 1 ? "" : "s") + " ago";
  }

  function freshnessState(lastUpdateTime) {
    if (!lastUpdateTime) return "unknown";
    const ts = Date.parse(lastUpdateTime);
    if (Number.isNaN(ts)) return "unknown";
    const age = Date.now() - ts;
    if (age <= STALE_THRESHOLD_MS) return "fresh";
    return "stale";
  }

  let lastUpdateTime = null;

  async function boot() {
    try {
      const [fac, ev, stats, onchain] = await Promise.all([
        window.ProofAPI.getFacilitators(),
        window.ProofAPI.getEvidence(),
        window.ProofAPI.getStats(),
        window.ProofAPI.getOnchainRegistry(),
      ]);
      if (stats && stats.data && stats.data.last_update_time) {
        lastUpdateTime = stats.data.last_update_time;
      }
      renderStats(stats.data);
      renderFacilitators(fac.data);
      renderEvidence(ev.data);
      renderOnchainRegistry(onchain.data);
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
      setBanner(document.getElementById("registry-banner"), "offline", "Offline · API unreachable");
      setBanner(document.getElementById("evidence-banner"), "offline", "Offline · API unreachable");
      setBanner(document.getElementById("onchain-banner"), "offline", "Offline · API unreachable");
    }
  }

  boot();
  setInterval(boot, 30000);
})();
