# x402Proof

![Live](https://img.shields.io/badge/status-live-green)
[![CI](https://github.com/NoMad-bnb/x402Proof/actions/workflows/ci.yml/badge.svg)](https://github.com/NoMad-bnb/x402Proof/actions/workflows/ci.yml)
![GenLayer](https://img.shields.io/badge/chain-GenLayer%20Studio-blue)
![License](https://img.shields.io/badge/license-Proprietary-lightgrey)

**Independent audit and reputation layer for x402 facilitators.**

---

## What is x402Proof

x402Proof is an **independent audit and reputation layer** for x402 facilitators, built on **GenLayer**.

It does not settle payments for anyone. It does not hold funds or customer keys, and it does not manage gas or nonces on behalf of a facilitator or a seller. The only key the indexer holds is a testnet key it uses to sign its own probe payments on Base Sepolia. It only **verifies** - using deterministic smart contracts and independent validators - whether a facilitator's settlement claim matches what actually happened on-chain.

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

### How this differs from the alternatives

- **A provider status page** is the provider talking about itself. x402Proof reads the settlement off the chain instead.
- **Per-platform monitoring** only sees the traffic that platform already routed, and it answers to the platform that asked. x402Proof publishes the same evidence for every facilitator, including ones the requester does not control.
- **A hosted reputation API** answers with a number you have to trust. Here the interface is a cache: the verdict list on GenLayer is the source, and every count can be recomputed from it.

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

## Who This Is For

**Sellers and merchants running x402 endpoints.** You accept payments through a facilitator, and the facilitator is also the party telling you the payment succeeded. Before you release goods or unlock a resource, read the verdict for that settlement from the public API. No account, no key, no integration.

**Marketplaces and payment platforms.** Compare facilitators before routing traffic through them, and keep the evidence for disputes. The registry shows whether one typed name is settled by several addresses, and which names share one settling address.

**Reviewers and researchers.** Every recorded verdict is recomputable from the contract. Nothing on the dashboard is an input: it is a cache over an append-only list that anyone can read.

---

## How It Works

### 1. Discovery & Evidence

An external indexer runs on a schedule and, for each known facilitator:

- Performs an x402 payment flow against a protected resource.
- Captures the settlement transaction hash from the facilitator's response or discovers it via RPC fallback.
- Verifies the transaction on-chain using a Base Sepolia RPC node.
- Builds a structured claim with explicit source attribution.

When the registry lists real paid resources for a facilitator (`resource_urls`), the scheduler pays that external resource through the facilitator being audited and tags the evidence record `scope: real_facilitator_audit`. Facilitators without a listed resource are probed against the internal test resource and tagged `scope: self_probe`, so the dashboard never presents them as facilitator-specific settlements.

If no settlement evidence is found on the first pass, the record is marked `PENDING_NO_EVIDENCE_YET` and retried on the next cycle.

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
- On-chain registry grouped by observed settling relayer, with label/relayer conflict flags
- Verdict distribution counted once per settlement transaction, next to the evidence record total
- Evidence records with full verification detail
- Independent verification links (Base Sepolia, GenLayer Explorer, on-chain contract)

Verdict counts are per settlement transaction, so a transaction audited by more than one claim is counted once per family instead of once per evidence record. The record total stays visible beside it, so the two numbers can always be reconciled: records outnumber transactions because a transaction can carry more than one audited claim.

The on-chain registry section reads the contract's `get_registry()` and `get_registry_by_relayer()` views on each indexer cycle. The relayer grouping is the authoritative display, because the settling address is observed on chain. Two conflict flags are surfaced as first-class signals:

- `labelRelayerConflict` - one typed label settled through more than one relayer address
- `relayerLabelConflict` - one relayer address settled under more than one typed label

Conflicts are published, not hidden. They are naming observations, not verdicts.

The on-chain counts and the evidence record total are counted over different sets: the contract keeps every audit ever written, including records that are not pushed to the API, so its totals can be larger. Both numbers are always shown side by side rather than reconciled into one figure.

Relayer rows are clickable. Each row opens a drawer with the full counter set and the typed labels, and the address opens on BaseScan (Sepolia), because the observed relayer is the Base Sepolia address that broadcast the settlement. `NO_RELAYER_OBSERVED` is a contract-level bucket for records where no settling relayer was observed, so it is shown as a bucket with no explorer link.

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

## Results So Far

The numbers below are read from the live deployment on 2026-09-15, and every one of them can be recomputed from the contracts.

- **7 facilitators tracked**, each with a live health status.
- **3 GenLayer contracts live**: the auditor, the declaration audit, and the supported probe.
- **657 evidence records** served by the public API, covering **615 settlement transactions**. Every one of those transactions carries a confirmed claim, and 40 of them also carry a rejected claim family.
- **755 audit records** in the contract store, across **11 typed labels** and **3 relayer groupings**.
- **Two conflict flags are published**, both observed live: 3 relayer groupings carry more than one typed label (one of the three is the contract's no-relayer bucket, not an address), and 2 typed labels settled through more than one observed address (`PayAI facilitator` and `x402.org public facilitator`, 2 addresses each).

---

## Live

- **Dashboard:** https://x402-proof.vercel.app/
- **Public API:** https://x402proof-api.onrender.com
- **Contracts (GenLayer Studio):**
  - **X402Auditor:** https://explorer-studio.genlayer.com/address/0xc40f7bADb1E340C78E20CdEf8722114bBEb53e98
  - **SupportedProbe:** https://explorer-studio.genlayer.com/address/0xb878840aE798078D8ED3CE371f6dC33eD98e0B8F
  - **DeclarationAudit:** https://explorer-studio.genlayer.com/address/0xeC9B3Bb176B22a31F659AB4581a9F22D3522737A
- **Contract source in this repository:**
  - `x402Proof/x402_auditor_v8.py` (X402Auditor)
  - `x402Proof/supported_probe.py` (SupportedProbe)
  - `x402Proof/declaration_audit.py` (DeclarationAudit)
- **Chain data source:** Base Sepolia via RPC

Three reads hold everything above together, and each one is callable from a browser or from your own server:

```bash
curl https://x402proof-api.onrender.com/health
curl "https://x402proof-api.onrender.com/evidence?verdict=CONFIRMED&limit=5"
curl https://x402proof-api.onrender.com/registry/onchain
```

---

## Verification Guide

### From the Dashboard (Recommended)

Click any evidence record to open its verification side panel, then use the built-in actions:

- **View Base Sepolia Transaction** - opens the settlement on BaseScan.
- **View GenLayer Audit** - opens the audit transaction on GenLayer Explorer.
- **Verify Verdict On-Chain** - opens the `X402Auditor` contract on GenLayer Explorer, where you can call `get_verdicts()` and match `claimTransaction` to the Base transaction.

Each drawer also carries a **What was checked** matrix listing the components the verdict was built from: transaction found, receipt status, network match, payer, payee, asset, amount within the scheme limit, EIP-3009 authorization presence, and the finality anchor. Each component shows its own pass/fail/unknown state with the observed values, so the verdict reads as a reconstructable judgment instead of an opinion.

If a button is disabled, the required transaction hash was not captured for that record.

### One Record, End to End

A real record, so the whole pipeline can be checked without running anything.

| Step | Value |
|---|---|
| Resource paid | `https://x402.org/protected`, through the `x402.org public facilitator` (scope `real_facilitator_audit`) |
| Payment flow | first request `402`, retry after payment `200` |
| Settlement on Base Sepolia | [`0xb0822c14...69f5be`](https://sepolia.basescan.org/tx/0xb0822c148f67b2e95bf4b0ac7f2c0d02aacf52febb37fb2ab95915d0da69f5be) |
| Audit on GenLayer | [`0x4891a9bd...25b95e`](https://explorer-studio.genlayer.com/tx/0x4891a9bdf19a853888c1c009aec216149e372516219b566e025d51ea9025b95e) |
| Verdict | `CONFIRMED`, `10000` base units, payer `0x959d38cb...6524e2`, anchor block `46862197` |

Search that settlement hash on the dashboard to open the same record. The drawer carries the same verdict, the same GenLayer hash, and the What was checked matrix that produced it.

### Manual Verification (Advanced)

1. Find a Base Sepolia transaction on `sepolia.basescan.org` using the `transactionHash` from the dashboard.
2. Find the GenLayer audit transaction on `explorer-studio.genlayer.com` sent to the `X402Auditor` address listed under **Live**.
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

## FAQ

**Is this real data or a mock?**

Real. Every figure in this README is read from the live deployment, and the examples use actual transaction hashes that resolve on BaseScan and on the GenLayer explorer.

**Is any of this on mainnet?**

No. Everything runs on Base Sepolia and GenLayer Studio. No mainnet keys or funds are involved.

**Why GenLayer instead of a server that checks the chain?**

A server is a single machine, and trusting it only renames the problem. Here several independent validators fetch the same receipt and must agree on the same bytes before a verdict is written. See **Why GenLayer, and why not a language model** above.

**What stops a facilitator from faking its record?**

It cannot choose how it is grouped. The registry groups audits by the address that actually broadcast the settlement, read from the chain, so a typed name is a label and never a key. Where a name and an observed address disagree, the disagreement is published instead of resolved.

**Do I need an account or a key to read a verdict?**

No. Reads are public and unauthenticated, so one HTTP GET from a browser or from your own server is enough. See **Live** for the endpoints.

**What if the indexer is wrong or stops running?**

The verdicts are already on chain. The API and the dashboard are caches over that append-only list, so anyone can recompute them and publish a mismatch.

**Does one audit cover every settlement a facilitator makes?**

No, and the scope tag says which is which: `real_facilitator_audit` when the payment ran against that facilitator's own paid resource, `self_probe` when it ran against the internal test resource. Coverage is a sample, not a census.

**What happens when no settlement evidence is found yet?**

The record stays `PENDING_NO_EVIDENCE_YET` and is retried on later cycles. The contract does not treat silence as a failure.

---

## Tests

The repository ships an offline verification suite that runs automatically on GitHub Actions for every push:

- 13 module self-tests, each proving the module's own contract
- 9 integration, contract, and API test files (the API is tested through FastAPI's TestClient), plus a Node test for the dashboard verification checklist
- Every store write in the suite is redirected to temporary files, so running it never touches the real evidence store, the local SQLite cache, or the remote API

---

## Roadmap

1. **Independent Verification** - 3 GenLayer contracts live, consensus verified, dashboard live, per-facilitator real-resource audits implemented (live confirmation pending).
2. **Resilience** - Completed: pending transaction tracking with check-only retry; extended consensus windows; dynamic scheduler intervals.
3. **Transparency** - One-click independent verification links in every evidence record.
4. **Qualitative Layer (Phase D)** - LLM-based comparison of written promises vs. observed behavior, isolated from deterministic verdicts.

This roadmap reflects the current direction. Additional features and improvements may be added in future releases.

---

## Design Principles

- **No decimal reputation scores.** Precision is illusory, and validators compare literal text. The denominator is always shown; absence is written as `null`, not zero or one hundred.
- **The interface is a cache, not a source.** Every figure is derivable from the contract. Raw evidence is published. Anyone can recompute and expose discrepancies.
- **Deterministic over descriptive.** All settlement verdicts are deterministic. Language models are optional and never adjudicate payment outcomes.
- **Explicit claim sources.** Every audit claim declares whether it came from self-probe, third-party report, or chain-only discovery. The contract adapts its verdict accordingly.
- **Explicit audit scope.** Every stored evidence record carries a `scope` tag: `real_facilitator_audit` when the settlement ran against an external resource of that facilitator, `self_probe` when it ran against the internal test resource. The dashboard renders it beside the verdict.

---

## License

Proprietary. All rights reserved.

The source is published so the work can be reviewed, audited, and evaluated. It is not licensed for reuse, redistribution, or deployment, in whole or in part, without written permission.

---

## What Is Real Now, and What Is in Progress

Working and verifiable today: the three contracts on GenLayer Studio, the audit pipeline from claim to verdict, the append-only record, the public API, and the dashboard with per-record verification.

In progress, stated plainly:

- **Per-facilitator real-resource audits** are implemented and have produced confirmed records against a facilitator's own paid resource. The live confirmation run is on hold because two facilitators, `x402org-public` and `payai`, are failing on their side.
- **The qualitative layer (Phase D) has not started.** No language model touches a verdict today, and none is planned inside the deterministic path.
- **Coverage is partial.** The indexer audits the facilitators it probes on a schedule, so a settlement that was never probed has no record.
- The prototype runs on **Base Sepolia and GenLayer Studio only**. No mainnet keys or funds are used, and interfaces may change before the final release.
