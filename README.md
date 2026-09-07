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
Facilitator -> Settlement -> Blockchain -> Independent Auditors (GenLayer) -> Verified Evidence -> Reputation
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
+-----------------+     +------------------+     +---------------------+
|   Facilitator   |---->|  Blockchain      |---->|  GenLayer Validators|
|                 |     |  (Base Sepolia)  |     |  (Independent)      |
+-----------------+     +------------------+     +----------+----------+
        |                                                    |
        |                                                    | byte-by-byte
        |                                                    | consensus
        v                                                    v
+-----------------+                                 +---------------------+
|  x402 Payment   |                                 |  Verified Evidence  |
|  Request / Resp |                                 |  + Reputation       |
+-----------------+                                 +----------+----------+
                                                             |
                                          +----------------+----------------+
                                          |                                 |
                                    +-----v-----+                   +-------v------+
                                    |   API     |                   |  Dashboard   |
                                    | (FastAPI) |                   |  (Static)    |
                                    +-----------+                   +--------------+
```

---

## Repository Structure

```
x402Proof/
|-- README.md                     # Project overview and vision
|-- .gitignore                    # Privacy and build exclusions
|
|-- x402Proof/                    # GenLayer smart contracts
|   |-- x402_auditor_v8.py        # Settlement vs. claim verification
|   |-- supported_probe.py        # Active /supported endpoint checker
|   |-- declaration_audit.py      # Declaration vs. on-chain behavior
|
|-- indexer/                      # External discovery, evidence, and API
|   |-- api.py                    # FastAPI service (deployed on Render)
|   |-- database.py               # SQLite/PostgreSQL evidence store
|   |-- claim_builder.py          # Claim construction
|   |-- settlement_hash_extractor.py
|   |-- rpc_transfer_scanner.py
|   |-- rpc_verification_adapter.py
|   |-- http_evidence_collector.py
|   |-- provider_discovery.py
|   |-- consensus_observer.py
|   |-- batch_evidence_capture.py
|   |-- scheduler.py
|   |-- test_*.py                 # Self-tests
|
|-- 402proof-site/                # Public dashboard
    |-- index.html
    |-- css/
    |-- js/
    |   |-- config.js              # API endpoint and environment labels
    |   |-- api.js                 # Data layer with API client + mock fallback
    |   |-- app.js                 # UI rendering
    |-- assets/
        |-- fonts/  logo/  icons/  graphics/
```

---

## Live

- **Dashboard:** https://x402-proof.vercel.app/
- **API:** https://x402proof-api.onrender.com/
- **Contracts:** GenLayer Studio
- **Chain data source:** Base Sepolia via RPC

---

## Current Status

| Component | Status |
|-----------|--------|
| Smart contracts (GenLayer) | 3 contracts live on GenLayer Studio; consensus verified |
| Indexer | A1-A11 complete and tested |
| API | Deployed on Render with PostgreSQL |
| Dashboard | Live and connected to API |
| Qualitative layer (LLM) | Planned for Phase D |
| Incentives | Planned for later phase |

---

## Example Output

```json
GET /facilitators
[
  {
    "provider_id": "x402org-public",
    "label": "x402.org",
    "facilitator_base_url": "https://x402.org",
    "supported_url": "https://x402.org/facilitator/supported",
    "networks": ["eip155:84532"],
    "status": "DECLARATION_CAPTURED"
  }
]
```

```json
GET /stats
{
  "providers": {
    "total": 7,
    "by_status": {
      "DECLARATION_CAPTURED": 4,
      "NO_SUPPORTED_ENDPOINT": 2,
      "GATED_REQUIRES_CREDENTIALS": 1
    }
  },
  "evidence": {
    "total": 0,
    "by_verdict": {},
    "by_source": {},
    "by_chain": {}
  }
}
```

The dashboard never invents providers, verdicts, or endpoints. When the API is unreachable, it falls back to local mock data and clearly labels it as a development snapshot.

---

## Roadmap

1. **Database and API**: FastAPI backed by SQLite/PostgreSQL, with transparent derivable metrics.
2. **Public Dashboard**: Search and browse registry, evidence, and reputation.
3. **Qualitative Layer**: Optional LLM-based comparison of written promises vs. observed behavior, isolated from deterministic verdicts.
4. **Incentives**: Bonding or complaint staking to prevent spam; GenLayer staking primitives under research.

---

## Verdicts and What They Mean

The contract emits a limited set of deterministic verdicts. Each maps to a specific trust question:

| Verdict | Meaning |
|---------|---------|
| `CONFIRMED` | The on-chain settlement matches the claim exactly. |
| `CONTRADICTED` | The on-chain settlement exists but contradicts the claim. |
| `REJECTED_*` | The claim is structurally invalid (no hash, no authorization, mismatch). |
| `UNDETERMINED_*` | Evidence was insufficient to judge. The contract refused to guess. |
| `PENDING_*` | A deadline has not passed yet. No final judgment is possible. |
| `SETTLED_NO_ANNOUNCEMENT_CAPTURED` | A settlement exists, but the facilitator did not announce it. |
| `UNVERIFIABLE_NO_TRANSACTION_HASH` | The claim lacks the minimum data required to verify. |

Every verdict is the output of a deterministic function over the same on-chain bytes. **No language model participates in the judgment.**

---

## How to Verify Independently

Anyone can verify the pipeline from public data alone, without trusting the API or the dashboard.

### Step 1: Find a Base Sepolia transaction
Open `https://sepolia.basescan.org` and search for the `transactionHash` shown on the dashboard. The receipt's `Transfer` event and `to` address are the on-chain evidence.

### Step 2: Find the GenLayer audit
Open GenLayer Studio and search for transactions sent to `X402_AUDITOR_ADDRESS = 0xc40f7bADb1E340C78E20CdEf8722114bBEb53e98` from the indexer's account. The `claim_transaction` field in the audit call should match the Base Sepolia hash.

### Step 3: Read the contract state
Call `X402Auditor.get_verdicts()` on GenLayer Studio. The last element is the verdict produced by the consensus round for that claim. Its `claimTransaction` field should match what you found in Step 1.

### Step 4: Cross-check
If the on-chain bytes, the contract call, and the dashboard all show the same `claimTransaction` and the same `verdict`, the pipeline is intact. If any of these three disagree, the discrepancy is itself a publishable finding.

---

## What the Contract Actually Writes

`X402Auditor` keeps a single append-only field:

```
verdicts: DynArray[str]
```

Every `audit()` call appends one stringified JSON record. The contract never edits or deletes prior records. The registry views (`get_registry()`, `get_registry_by_relayer()`) are **derived** from this list at read time. They are not stored separately.

This means:
- The on-chain footprint is the verdict list, nothing else.
- Anyone can recompute the registry from the verdicts and compare to the dashboard.
- The dashboard is a cache. The contract is the source.

---

## Design Principles

- **No decimal reputation scores.** Precision is illusory, and validators compare literal text. The denominator is always shown; absence is written as `null`, not zero or one hundred.
- **The interface is a cache, not a source.** Every figure is derivable from the contract. Raw evidence is published. Anyone can recompute and expose discrepancies.
- **Deterministic over descriptive.** All settlement verdicts are deterministic. Language models are optional and never adjudicate payment outcomes.
- **Explicit claim sources.** Every audit claim declares whether it came from self-probe, third-party report, or chain-only discovery. The contract adapts its verdict accordingly.

---

## License

Proprietary. All rights reserved.
