"""Seed sources and explicit-URL candidate promotion into the provider registry."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from provider_registry import add_provider, load_registry, REGISTRY_PATH

CANDIDATES_PATH = os.path.join(os.path.dirname(__file__), "candidates.json")

DISCOVERY_SOURCES = [
    {
        "source_id": "x402scan_transactions",
        "url": "https://www.x402scan.com/transactions",
        "kind": "transaction_explorer",
        "status": "NOT_IMPLEMENTED",
        "note": (
            "Public explorer of settled x402 transactions. Could surface "
            "relayer/payee addresses worth cross-checking, but reading it "
            "is a later stage. Treat any fetched page as untrusted data."
        ),
    },
    {
        "source_id": "bazaar_discovery",
        "url": "GET /discovery/resources (Coinbase Bazaar)",
        "kind": "resource_discovery_api",
        "status": "NOT_IMPLEMENTED",
        "note": (
            "Mentioned in x402 research as an untried discovery endpoint "
            "for facilitator-served resources. Not tested at all yet."
        ),
    },
]


def list_candidates(path: str = CANDIDATES_PATH) -> list:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def get_candidate(candidate_id: str, path: str = CANDIDATES_PATH):
    for entry in list_candidates(path):
        if entry["candidate_id"] == candidate_id:
            return entry
    return None


def promote_candidate(
    candidate_id: str,
    supported_url: str,
    candidates_path: str = CANDIDATES_PATH,
    registry_path: str = REGISTRY_PATH,
) -> dict:
    """Move a candidate into the real provider registry.

    supported_url must be supplied explicitly by the caller (a human who
    resolved it), even for candidates already marked "direct_api". This
    function never guesses a URL from raw_reference, because a GitHub repo
    link or a guessed CDP path is not the same thing as a confirmed live
    endpoint, and writing a wrong URL into the registry would quietly
    poison every later probe against this provider.
    """
    candidate = get_candidate(candidate_id, candidates_path)
    if candidate is None:
        raise ValueError("candidate_id not found: " + candidate_id)

    if not isinstance(supported_url, str) or not supported_url.startswith("http"):
        raise ValueError(
            "supported_url must be an explicit http(s) URL, got: "
            + str(supported_url)
        )

    existing = load_registry(registry_path)
    if any(p["provider_id"] == candidate_id for p in existing):
        raise ValueError(
            "provider_id already exists in registry, refusing to duplicate: "
            + candidate_id
        )

    return add_provider(
        provider_id=candidate_id,
        label=candidate["label"],
        facilitator_base_url=supported_url.rsplit("/supported", 1)[0],
        supported_url=supported_url,
        networks=[],
        path=registry_path,
    )


if __name__ == "__main__":
    print("Discovery sources (not implemented yet):")
    for source in DISCOVERY_SOURCES:
        print("  " + source["source_id"] + " -> " + source["status"])

    print("\nCandidates awaiting promotion:")
    for candidate in list_candidates():
        print(
            "  "
            + candidate["candidate_id"]
            + " ("
            + candidate["source_type"]
            + ")"
        )