# x402Proof

![Live](https://img.shields.io/badge/status-live-green)
![GenLayer](https://img.shields.io/badge/chain-GenLayer%20Studio-blue)
![Open Source](https://img.shields.io/badge/license-Proprietary-lightgrey)

**Independent audit and reputation layer for x402 facilitators.**

---

## What is x402Proof

x402Proof is an **independent audit and reputation layer** for x402 facilitators, built on **GenLayer**.

It does not settle payments. It does not hold funds. It does not run wallets or gas management. It only **verifies** - using deterministic smart contracts and independent validators - whether a facilitator's settlement claim matches what actually happened on-chain.

The result is an immutable, reproducible evidence record that anyone can check without trusting the API, the dashboard, or the indexer.

---

## The Problem

The **x402 protocol** (introduced by Coinbase and Cloudflare) revived the HTTP 402 status code to make payment a native step in HTTP requests. It is already being adopted by multiple facilitators across different networks.

Within this protocol, the **Facilitator** plays a central role:

- Receives a signed payment authorization from the client.
- Executes the settlement on-chain (pays gas, submits the transaction).
- Returns a response to the seller server: success or failure.

**The trust gap:** this response is verified by nobody.

The facilitator is, in practice, a trusted centralized party. There is no independent way to answer:

1. Did it actually settle what it claims to have settled?
2. Does it silently discriminate between clients (serving some while ignoring others)?
3. Does its actual behavior match what it publicly declares about itself?

Without independent verification, the seller must either trust the facilitator blindly or build custom monitoring per provider. Both approaches re-introduce the centralization problem that decentralized protocols are supposed to solve.

---

## The Solution

**x402Proof** sits between the facilitator's claim and the seller's acceptance:

```
Facilitator -> Settlement -> Blockchain -> Independent Auditors (GenLayer) -> Verified Evidence -> Reputation
```

The facilitator executes settlement as usual. An external indexer discovers candidate transactions. **GenLayer independently verifies the on-chain proof**, and the resulting immutable record feeds reputation scores, an API, and a public dashboard.

### Why GenLayer, and why not a language model

This is a critical design choice.

A normal backend is a **single machine you must trust**. If we told users "trust our server because it monitors the facilitator," we would recreate the exact x402 trust problem with a different name.

GenLayer lets **multiple independent validators** fetch the receipt themselves, then compare the result **byte-by-byte** via deterministic consensus. If they differ, nothing is written.

This is the decentralization in this project, and it is **measured, not assumed**.

**Language models remain optional and confined to a qualitative layer only (Phase D). They never adjudicate settlement.** All verdicts are deterministic.

### What we will never do

This project will never become a facilitator that executes settlement. **No payment transactions, no wallets, no gas management, no nonce handling, no fund custody.** The project is an audit, verification, and reputation layer. **Its independence is its value.** The moment it executes settlement, it loses that independence.

---

## How It Works

### 1. Discovery & Evidence

An external indexer runs on a schedule and, for each known facilitator:

- Performs an x402 payment flow against a protected resource.
- Captures the settlement transaction hash from the facilitator's response or discovers it via RPC fallback.
- Verifies the transaction on-chain using a Base Sepolia RPC node.
- Builds a structured claim with explicit source attribution.

If no settlement evidence is found within the provider timeout, the record is marked `PENDING_NO_EVIDENCE_YET` and retried on the next cycle.

### 2. Independent Audit (GenLayer)

The indexer submits the claim to one of three GenLayer smart contracts:

- **X402Auditor** - validates EIP-3009 authorization, transfer events, and payer/amount/network against the claim.
- **SupportedProbe** - fetches `/supported` and records a digest for change detection.
- **DeclarationAudit** - cross-checks a facilitator's self-declared signers against the actual on-chain sender.

GenLayer validators run deterministic consensus on the same on-chain bytes. The contract appends the verdict to an append-only list. The indexer waits for consensus, then reads back the latest record.

If consensus takes longer than the default SDK window, the indexer tracks the pending transaction hash and re-checks it on subsequent cycles without re-sending the claim.

### 3. Public Dashboard

The indexer stores evidence in a local store and exposes it through a public API.

The public dashboard connects to the API and displays:

- Facilitator registry with live status
- Evidence records with full verification detail
- Independent verification links (Base Sepolia, GenLayer Explorer, on-chain contract)

The dashboard refreshes automatically every 30 seconds. The indexer runs on a 10-minute cycle, so evidence records appear on the dashboard shortly after each successful cycle.

Some facilitators use **batch-settlement** schemes, where value moves through an escrow contract instead of a direct payer-to-receiver transfer. These records are captured as evidence but are **not adjudicated** by `X402Auditor`, because the settlement cannot be attributed to a single payer. They appear on the dashboard as `BATCH_CAPTURED_NOT_JUDGED`.

Provider status reflects the last health check:
- `healthy` - both `/supported` and RPC are reachable
- `degraded` - one of the two is unreachable
- `unhealthy` - both are unreachable

Evidence store status reflects deduplication:
- `EVIDENCE_STORED` - new record written
- `EVIDENCE_DUPLICATE` - same semantic evidence already exists
- `EVIDENCE_NO_KEY` - transaction hash was missing, nothing stored

---

## Live

- **Dashboard:** https://x402-proof.vercel.app/
- **Contracts (GenLayer Studio):**
  - **X402Auditor:** https://explorer-studio.genlayer.com/address/0xc40f7bADb1E340C78E20CdEf8722114bBEb53e98
  - **SupportedProbe:** https://explorer-studio.genlayer.com/address/0xb878840aE798078D8ED3CE371f6dC33eD98e0B8F
  - **DeclarationAudit:** https://explorer-studio.genlayer.com/address/0xeC9B3Bb176B22a31F659AB4581a9F22D3522737A
- **Chain data source:** Base Sepolia via RPC

---

## Verification Guide

### From the Dashboard (Recommended)

Open any **Verification Record** in the side panel and use the built-in actions:

- **View Base Sepolia Transaction** - opens the settlement on BaseScan.
- **View GenLayer Audit** - opens the audit transaction on GenLayer Explorer.
- **Verify Verdict On-Chain** - opens the `X402Auditor` contract on GenLayer Explorer, where you can call `get_verdicts()` and match `claimTransaction` to the Base transaction.

If a button is disabled, the required transaction hash was not captured for that record.

### Manual Verification (Advanced)

1. Find a Base Sepolia transaction on `sepolia.basescan.org` using the `transactionHash` from the dashboard.
2. Find the GenLayer audit transaction on `explorer-studio.genlayer.com` sent to `X402_AUDITOR_ADDRESS = 0xc40f7bADb1E340C78E20CdEf8722114bBEb53e98`.
3. Call `get_verdicts()` on the contract. The last element is the verdict for the most recent claim.
4. Cross-check that `claimTransaction` in the contract matches the Base Sepolia hash, and that the dashboard shows the same verdict.

If on-chain bytes, the contract call, and the dashboard all agree, the pipeline is intact. If any of these three disagree, the discrepancy is itself a publishable finding.

---

## What the Contract Actually Writes

`X402Auditor` keeps a single append-only list:

```solidity
verdicts: DynArray[str]
```

Every `audit()` call appends one stringified JSON record. The contract never edits or deletes prior records. Registry views (`get_registry()`, `get_registry_by_relayer()`) are **derived** from this list at read time.

Each record contains:

- `verdict` - the deterministic outcome string
- `claimTransaction` - the Base Sepolia transaction hash being judged
- `claimSource` - `self_probe`, `discovered_only`, or similar
- `facilitator`, `network`, `payer`, `payee`, `amount`, `timestamp`, and other claim fields

This means:

- The on-chain footprint is the verdict list, nothing else.
- Anyone can recompute the registry from the verdicts and compare to the dashboard.
- The dashboard is a cache. The contract is the source.

---

## Verdicts and What They Mean

The contract emits a limited set of deterministic verdicts. Each maps to a specific trust question:

| Verdict | Meaning |
|---------|---------|
| `CONFIRMED` | The on-chain settlement matches the claim exactly. |
| `CONFIRMED_FAILURE_TRANSACTION_REVERTED` | Claim asserted success, but transaction reverted on-chain. |
| `CONFIRMED_FAILURE_NOT_ON_CHAIN` | Claim asserted success, but no settlement found on-chain. |
| `CONTRADICTED_*` | The on-chain settlement exists but contradicts the claim. |
| `REJECTED_*` | The claim is structurally invalid (no hash, no authorization, mismatch). |
| `UNDETERMINED_*` | Evidence was insufficient to judge. The contract refused to guess. |
| `PENDING_*` | A deadline has not passed yet. No final judgment is possible. |
| `SETTLED_*` | Settlement exists, but the facilitator did or did not announce it. |
| `UNVERIFIABLE_NO_TRANSACTION_HASH` | The claim lacks the minimum data required to verify. |

Every verdict is the output of a deterministic function over the same on-chain bytes. **No language model participates in the judgment.**

---

## Roadmap

1. **Independent Verification** - 3 GenLayer contracts live, consensus verified, dashboard live.
2. **Resilience** - Pending transaction tracking with check-only retry; extended consensus windows; dynamic scheduler intervals.
3. **Transparency** - One-click independent verification links in every record; full public API.
4. **Qualitative Layer (Phase D)** - Optional LLM-based comparison of written promises vs. observed behavior, isolated from deterministic verdicts.

---

## Design Principles

- **No decimal reputation scores.** Precision is illusory, and validators compare literal text. The denominator is always shown; absence is written as `null`, not zero or one hundred.
- **The interface is a cache, not a source.** Every figure is derivable from the contract. Raw evidence is published. Anyone can recompute and expose discrepancies.
- **Deterministic over descriptive.** All settlement verdicts are deterministic. Language models are optional and never adjudicate payment outcomes.
- **Explicit claim sources.** Every audit claim declares whether it came from self-probe, third-party report, or chain-only discovery. The contract adapts its verdict accordingly.

---

## License

Proprietary. All rights reserved.

---

## Status

This project is **under active development**. The current version is a working prototype on Base Sepolia and GenLayer Studio. Features, interfaces, and supported facilitators may change before the final release.
