# x402Proof

**Independent audit and reputation layer for x402 facilitators.**

---

## The Problem

The **x402 protocol** (introduced by Coinbase and Cloudflare) revived the HTTP 402 status code to make payment a native step in HTTP requests. It is already processing hundreds of thousands of transactions.

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

**x402Proof** is an independent audit and reputation layer for x402 facilitators, built on **GenLayer**.

```
Facilitator → Settlement → Blockchain → Independent Auditors (GenLayer) → Verified Evidence → Reputation
```

The facilitator executes settlement as usual. An external indexer discovers candidate transactions. **GenLayer independently verifies the on-chain proof**, and the resulting immutable record feeds reputation scores, an API, and a public dashboard.

### Why GenLayer, and why not a language model

This is a critical design choice.

A normal backend is a **single machine you must trust**. If we told users "trust our server because it monitors the facilitator," we would recreate the exact x402 trust problem with a different name.

GenLayer lets **multiple independent validators** fetch the receipt themselves, then compare the result **byte-by-byte** via deterministic consensus. If they differ, nothing is written.

This is the decentralization in this project, and it is **measured, not assumed**: five identical readings in the first trial, and successful consensus on every run afterward.

**Language models remain optional and confined to a qualitative layer only (Phase D). They never adjudicate settlement.** All verdicts are deterministic.

### What we will never do

This project will never become a facilitator that executes settlement. **No payment transactions, no wallets, no gas management, no nonce handling, no fund custody.** The project is an audit, verification, and reputation layer. **Its independence is its value.** The moment it executes settlement, it loses that independence.

---

## How It Works

### 1. Independent Verification via Smart Contracts

Three separate smart contracts run on GenLayer. Each contract serves a distinct purpose and does not call the others. Binding between them happens in higher layers (the indexer or the API).

**X402Auditor** validates whether an on-chain settlement is actually an x402 payment. It reads transaction calldata, decodes EIP-3009 authorization, and compares it against the claim. It distinguishes between a real payment that is not x402 and a fabricated x402 claim.

**SupportedProbe** actively fetches the `/supported` endpoint from facilitators and stores the response digest. Any future change in a facilitator's declaration is detected by a single digest comparison.

**DeclarationAudit** compares a facilitator's self-declared address or signers against the actual observed sender on-chain. It runs two independent consensus rounds in a single transaction so that failures are attributed to the correct party.

### 2. The Indexer

The indexer is the external discovery and evidence-capture layer. It:

- Discovers candidate settlement transactions from the blockchain.
- Extracts settlement hashes and normalizes amounts.
- Captures evidence from RPC nodes and HTTP endpoints.
- Builds structured claims and submits them to the GenLayer contracts.

The indexer is designed so that **every claim carries an explicit `claim_source`** with no exceptions. This prevents the indexer from ever becoming a source of truth; the blockchain and GenLayer consensus remain the only authorities.

### 3. The Frontend

A static, framework-free frontend displays the registry and evidence. It connects to the indexer API when available and falls back to local mock data labeled as a development snapshot. The UI never invents providers, verdicts, or endpoints.

---

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│   Facilitator   │────▶│  Blockchain      │────▶│  GenLayer Validators│
│                 │     │  (Base Sepolia)  │     │  (Independent)      │
└─────────────────┘     └──────────────────┘     └─────────┬───────────┘
        │                                                    │
        │                                                    │ byte-by-byte
        │                                                    │ consensus
        ▼                                                    ▼
┌─────────────────┐                                 ┌─────────────────────┐
│  x402 Payment   │                                 │  Verified Evidence  │
│  Request / Resp │                                 │  + Reputation       │
└─────────────────┘                                 └─────────┬───────────┘
                                                             │
                                          ┌────────────────┴────────────────┐
                                          │                                 │
                                    ┌─────▼─────┐                   ┌──────▼──────┐
                                    │   API     │                   │  Dashboard  │
                                    │ (FastAPI) │                   │  (Static)   │
                                    └───────────┘                   └────────────┘
```

---

## Repository Structure

```
x402Proof/
├── README.md                    # Project overview and vision
├── .gitignore                   # Privacy and build exclusions
│
├── x402Proof/                   # GenLayer smart contracts
│   ├── x402_auditor_v8.py       # Settlement vs. claim verification
│   ├── supported_probe.py       # Active /supported endpoint checker
│   └── declaration_audit.py     # Declaration vs. on-chain behavior
│
├── indexer/                     # External discovery, evidence, and API
│   ├── api.py                   # FastAPI service
│   ├── database.py              # SQLite evidence store
│   ├── claim_builder.py         # Claim construction
│   ├── settlement_hash_extractor.py
│   ├── rpc_transfer_scanner.py
│   ├── rpc_verification_adapter.py
│   ├── http_evidence_collector.py
│   ├── provider_discovery.py
│   ├── consensus_observer.py
│   ├── batch_evidence_capture.py
│   ├── scheduler.py
│   └── test_*.py                # Self-tests
│
└── 402proof-site/               # Public dashboard
    ├── index.html
    ├── css/
    ├── js/
    └── assets/
```

---

## Current Status

| Component | Status |
|-----------|--------|
| Smart contracts (GenLayer) | Live-tested on Base Sepolia; consensus verified |
| Indexer | Discovery, evidence capture, and claim building complete and tested |
| API | Built and tested locally; frontend connection pending |
| Dashboard | Complete static implementation |
| Qualitative layer (LLM) | Planned for Phase D |
| Incentives | Planned for later phase |

---

## Roadmap

1. **Database and API** — FastAPI backed by SQLite, with transparent derivable metrics.
2. **Public Dashboard** — Search and browse registry, evidence, and reputation.
3. **Qualitative Layer** — Optional LLM-based comparison of written promises vs. observed behavior, isolated from deterministic verdicts.
4. **Incentives** — Bonding or complaint staking to prevent spam; GenLayer staking primitives under research.

---

## Design Principles

- **No decimal reputation scores.** Precision is illusory, and validators compare literal text. The denominator is always shown; absence is written as `null`, not zero or one hundred.
- **The interface is a cache, not a source.** Every figure is derivable from the contract. Raw evidence is published. Anyone can recompute and expose discrepancies.
- **Deterministic over descriptive.** All settlement verdicts are deterministic. Language models are optional and never adjudicate payment outcomes.
- **Explicit claim sources.** Every audit claim declares whether it came from self-probe, third-party report, or chain-only discovery. The contract adapts its verdict accordingly.

---

## License

Proprietary. All rights reserved.
