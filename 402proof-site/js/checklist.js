/**
 * Evidence checklist builder for the verification drawer.
 * Pure functions, no DOM. Turns the stored verificationRecord and
 * auditRecord fields into an explicit what-was-checked matrix.
 */
(function (global) {
  function sameAddress(a, b) {
    if (!a || !b) return false;
    return String(a).toLowerCase() === String(b).toLowerCase();
  }

  function truncateAddr(address) {
    const s = String(address || "");
    if (s.length <= 22) return s;
    return s.slice(0, 12) + "..." + s.slice(-6);
  }

  function toInt(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function item(label, status, detail) {
    return { label: label, status: status, detail: detail || "" };
  }

  function buildEvidenceChecklist(rec) {
    const summary = (rec && rec.summary) || {};
    const vr = summary.verificationRecord || {};
    const audit = summary.auditRecord || {};
    const reqs = summary.requirements || {};
    const transfer = vr.transfer || {};
    const auth = vr.authorization || {};
    const observed = Array.isArray(audit.observed) ? audit.observed : [];
    const observed0 = observed[0] || {};
    const items = [];

    items.push(item(
      "Transaction found on chain",
      vr.transactionFound === true ? "pass" : vr.transactionFound === false ? "fail" : "unknown",
      String(vr.transactionHash || "hash not recorded")
    ));

    items.push(item(
      "Receipt status",
      vr.receiptStatus === "0x1" ? "pass" : vr.receiptStatus === "0x0" ? "fail" : "unknown",
      String(vr.receiptStatus ?? "not recorded")
    ));

    items.push(item(
      "Network label matches chain",
      vr.chainIdMatches === true ? "pass" : vr.chainIdMatches === false ? "fail" : "unknown",
      (vr.networkLabel || "?") + " -> " + (vr.chainId || "?")
    ));

    const payerClaimed = audit.claimPayer || "";
    const payerObserved = transfer.from || observed0.from || "";
    if (!payerClaimed || !payerObserved) {
      items.push(item("Payer", "unknown", "payer not recorded"));
    } else {
      items.push(item(
        "Payer",
        sameAddress(payerClaimed, payerObserved) ? "pass" : "fail",
        truncateAddr(payerObserved) + " (observed) vs " + truncateAddr(payerClaimed) + " (claimed)"
      ));
    }

    const payeeDeclared = reqs.payTo || audit.reqPayTo || "";
    const payeeObserved = transfer.to || observed0.to || "";
    if (!payeeDeclared || !payeeObserved) {
      items.push(item("Payee", "unknown", "payee not recorded"));
    } else {
      items.push(item(
        "Payee",
        sameAddress(payeeDeclared, payeeObserved) ? "pass" : "fail",
        truncateAddr(payeeObserved) + " (observed) vs " + truncateAddr(payeeDeclared) + " (declared)"
      ));
    }

    const assetDeclared = reqs.asset || audit.reqAsset || "";
    const assetObserved = observed0.token || "";
    if (!assetDeclared || !assetObserved) {
      items.push(item("Asset", "unknown", "asset not recorded"));
    } else {
      items.push(item(
        "Asset",
        sameAddress(assetDeclared, assetObserved) ? "pass" : "fail",
        truncateAddr(assetObserved) + " (observed) vs " + truncateAddr(assetDeclared) + " (declared)"
      ));
    }

    const amountObserved = toInt(
      audit.settledAmount != null ? audit.settledAmount : (transfer.value != null ? transfer.value : observed0.amount)
    );
    const amountMax = toInt(reqs.maxAmountRequired != null ? reqs.maxAmountRequired : audit.reqMaxAmountRequired);
    const amountAuth = toInt(audit.authValue);
    if (amountObserved === null || amountMax === null) {
      items.push(item("Amount within scheme limit", "unknown", "amounts not recorded"));
    } else if (amountAuth !== null && amountAuth !== amountObserved) {
      items.push(item(
        "Amount within scheme limit",
        "fail",
        "settled " + amountObserved + " vs authorized " + amountAuth
      ));
    } else {
      const scheme = audit.reqScheme || reqs.scheme || "";
      const within = amountObserved <= amountMax;
      const exact = scheme === "exact" ? amountObserved === amountMax : true;
      items.push(item(
        "Amount within scheme limit",
        within && exact ? "pass" : "fail",
        "settled " + amountObserved + " / max " + amountMax + (scheme ? " (" + scheme + ")" : "")
      ));
    }

    items.push(item(
      "EIP-3009 authorization present",
      auth.hasEIP3009Selector === true ? "pass" : auth.found === false ? "fail" : "unknown",
      auth.selector ? "selector " + String(auth.selector) : "authorization not recorded"
    ));

    const anchor = toInt(audit.anchorBlock != null ? audit.anchorBlock : vr.anchorBlock);
    items.push(item(
      "Finality anchor recorded",
      anchor !== null ? "pass" : "unknown",
      anchor !== null ? "anchor block " + anchor : "anchor not recorded"
    ));

    return items;
  }

  global.EvidenceChecklist = { build: buildEvidenceChecklist };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { build: buildEvidenceChecklist };
  }
})(typeof window !== "undefined" ? window : globalThis);
