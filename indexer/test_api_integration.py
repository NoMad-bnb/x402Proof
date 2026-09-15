"""Integration test for the FastAPI layer (api.py) via TestClient.

All database access is redirected to a temporary SQLite file before api
is imported, so the real indexer.db, evidence.json and providers.json
are never touched, and no remote API is contacted.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database

os.environ["X402_API_URL"] = ""

_TEMP_DIR = tempfile.mkdtemp(prefix="x402_api_test_")
_ORIGINAL_DB_PATH = database.DB_PATH
database.DB_PATH = Path(_TEMP_DIR) / "api_test.db"

import api  # imported after the DB redirect on purpose

from fastapi.testclient import TestClient

CLIENT = TestClient(api.app)

CHAIN = "eip155:84532"

PROVIDER_A = {
    "provider_id": "api-test-a",
    "label": "API Test A",
    "facilitator_base_url": "https://a.example",
    "supported_url": "https://a.example/supported",
    "verify_url": None,
    "settle_url": None,
    "known_declaration_url": None,
    "networks": ["eip155:84532"],
    "last_seen": "2026-09-13T00:00:00+00:00",
    "status": "healthy",
}

PROVIDER_B = {
    "provider_id": "api-test-b",
    "label": "API Test B",
    "facilitator_base_url": "https://b.example",
    "supported_url": None,
    "verify_url": None,
    "settle_url": None,
    "known_declaration_url": None,
    "networks": [],
    "last_seen": "2026-09-13T00:00:01+00:00",
    "status": "unhealthy",
}


def _evidence(tx_char, verdict, stored_at):
    return {
        "schemaVersion": 1,
        "storedAt": stored_at,
        "evidenceKey": {
            "chainId": CHAIN,
            "transactionHash": "0x" + tx_char * 64,
        },
        "evidenceDigest": "0x" + "d" * 64,
        "summary": {
            "providerId": "api-test-a",
            "auditVerdict": verdict,
            "evidenceSource": "integration_test",
        },
        "evidenceStoreStatus": "EVIDENCE_STORED",
    }


def run_scenario_1():
    """/health on an empty temporary store."""
    passed = True
    response = CLIENT.get("/health")
    body = response.json()
    print("=== Scenario 1: /health on empty store ===")
    print(json.dumps(body, indent=2))
    if response.status_code != 200:
        print("FAIL: expected 200, got " + str(response.status_code))
        passed = False
    if body.get("status") != "ok":
        print("FAIL: expected status ok, got " + str(body.get("status")))
        passed = False
    if body.get("providers_count") != 0 or body.get("evidence_count") != 0:
        print("FAIL: expected zero counts, got " + json.dumps(body))
        passed = False
    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_2():
    """Providers: ingest -> list -> single read -> missing returns 404."""
    passed = True
    print("=== Scenario 2: providers ingest and reads ===")
    ingest = CLIENT.post(
        "/ingest/providers", json={"providers": [PROVIDER_A, PROVIDER_B]}
    )
    if ingest.status_code != 200 or ingest.json().get("ingested") != 2:
        print("FAIL: ingest/providers -> " + str(ingest.status_code)
              + " " + ingest.text)
        passed = False

    listing = CLIENT.get("/facilitators")
    if listing.status_code != 200 or len(listing.json()) != 2:
        print("FAIL: /facilitators -> " + str(listing.status_code)
              + " " + listing.text)
        passed = False

    single = CLIENT.get("/facilitators/api-test-a")
    if single.status_code != 200:
        print("FAIL: /facilitators/api-test-a -> " + str(single.status_code)
              + " " + single.text)
        passed = False
    else:
        body = single.json()
        if body.get("provider_id") != "api-test-a":
            print("FAIL: wrong provider returned: " + json.dumps(body))
            passed = False
        if body.get("networks") != ["eip155:84532"]:
            print("FAIL: networks round-trip broken: "
                  + json.dumps(body.get("networks")))
            passed = False

    missing = CLIENT.get("/facilitators/api-test-missing")
    if missing.status_code != 404:
        print("FAIL: missing provider expected 404, got "
              + str(missing.status_code))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_3():
    """Evidence: ingest -> /health count -> by-key read -> missing 404."""
    passed = True
    print("=== Scenario 3: evidence ingest and by-key reads ===")
    records = [
        _evidence("ab", "CONFIRMED", "2026-09-13T00:00:00+00:00"),
        _evidence("cd", "CONTRADICTED", "2026-09-13T00:00:01+00:00"),
    ]
    ingest = CLIENT.post("/ingest/evidence", json={"records": records})
    if ingest.status_code != 200 or ingest.json().get("ingested") != 2:
        print("FAIL: ingest/evidence -> " + str(ingest.status_code)
              + " " + ingest.text)
        passed = False

    health = CLIENT.get("/health").json()
    if health.get("evidence_count") != 2:
        print("FAIL: /health evidence_count != 2: " + json.dumps(health))
        passed = False

    found = CLIENT.get("/evidence/" + CHAIN + "/0x" + "ab" * 64)
    if found.status_code != 200 or len(found.json()) != 1:
        print("FAIL: by-key read -> " + str(found.status_code) + " "
              + found.text)
        passed = False
    else:
        record = found.json()[0]
        if record.get("chain_id") != CHAIN:
            print("FAIL: chain_id mismatch: " + json.dumps(record))
            passed = False
        if record.get("summary", {}).get("auditVerdict") != "CONFIRMED":
            print("FAIL: verdict mismatch: " + json.dumps(record))
            passed = False

    not_found = CLIENT.get("/evidence/" + CHAIN + "/0x" + "ef" * 64)
    if not_found.status_code != 404:
        print("FAIL: missing evidence expected 404, got "
              + str(not_found.status_code))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_4():
    """/evidence filtering: chain, provider, verdict, limit, from_date."""
    passed = True
    print("=== Scenario 4: /evidence filters ===")

    def count(params):
        response = CLIENT.get("/evidence", params=params)
        if response.status_code != 200:
            print("FAIL: /evidence " + json.dumps(params) + " -> "
                  + str(response.status_code) + " " + response.text)
            return -1
        return len(response.json())

    checks = [
        ("no filter", {}, 2),
        ("chain filter", {"chain_id": CHAIN}, 2),
        ("other provider", {"provider_id": "api-test-b"}, 0),
        ("verdict filter", {"verdict": "CONFIRMED"}, 1),
        ("verdict + chain", {"verdict": "CONFIRMED", "chain_id": CHAIN}, 1),
        ("limit", {"limit": 1}, 1),
        ("from_date", {"from_date": "2026-09-13T00:00:01+00:00"}, 1),
    ]
    for name, params, expected in checks:
        got = count(params)
        if got != expected:
            print("FAIL: filter " + name + " expected " + str(expected)
                  + ", got " + str(got))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_5():
    """/stats aggregation over the ingested fixtures."""
    passed = True
    response = CLIENT.get("/stats")
    body = response.json()
    print("=== Scenario 5: /stats ===")
    print(json.dumps(body, indent=2))
    if response.status_code != 200:
        print("FAIL: expected 200, got " + str(response.status_code))
        passed = False
    providers = body.get("providers", {})
    evidence = body.get("evidence", {})
    if providers.get("total") != 2:
        print("FAIL: providers total != 2: " + json.dumps(providers))
        passed = False
    if evidence.get("total") != 2:
        print("FAIL: evidence total != 2: " + json.dumps(evidence))
        passed = False
    by_verdict = evidence.get("by_verdict", {})
    if by_verdict.get("CONFIRMED") != 1 or by_verdict.get("CONTRADICTED") != 1:
        print("FAIL: verdict counts wrong: " + json.dumps(by_verdict))
        passed = False
    by_verdict_tx = evidence.get("by_verdict_transactions", {})
    if by_verdict_tx.get("CONFIRMED") != 1 or by_verdict_tx.get("CONTRADICTED") != 1:
        print("FAIL: per-transaction verdict counts wrong: "
              + json.dumps(by_verdict_tx))
        passed = False
    if evidence.get("total_transactions") != 2:
        print("FAIL: total_transactions wrong: "
              + json.dumps(evidence.get("total_transactions")))
        passed = False
    if evidence.get("by_store_status", {}).get("EVIDENCE_STORED") != 2:
        print("FAIL: store status counts wrong: "
              + json.dumps(evidence.get("by_store_status")))
        passed = False
    if evidence.get("by_chain", {}).get(CHAIN) != 2:
        print("FAIL: chain counts wrong: "
              + json.dumps(evidence.get("by_chain")))
        passed = False
    if evidence.get("by_source", {}).get("integration_test") != 2:
        print("FAIL: source counts wrong: "
              + json.dumps(evidence.get("by_source")))
        passed = False
    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_6():
    """A provider with a NULL status must still be served (regression)."""
    passed = True
    print("=== Scenario 6: NULL provider status is served ===")
    provider = {
        "provider_id": "api-test-null-status",
        "label": "API Test Null Status",
        "facilitator_base_url": "https://null-status.example",
        "supported_url": None,
        "verify_url": None,
        "settle_url": None,
        "known_declaration_url": None,
        "networks": [],
        "last_seen": None,
        "status": None,
    }
    ingest = CLIENT.post("/ingest/providers", json={"providers": [provider]})
    if ingest.status_code != 200 or ingest.json().get("ingested") != 1:
        print("FAIL: ingest -> " + str(ingest.status_code) + " " + ingest.text)
        passed = False

    listing = CLIENT.get("/facilitators")
    if listing.status_code != 200:
        print("FAIL: /facilitators -> " + str(listing.status_code) + " "
              + listing.text)
        passed = False

    single = CLIENT.get("/facilitators/api-test-null-status")
    if single.status_code != 200:
        print("FAIL: single read -> " + str(single.status_code) + " "
              + single.text)
        passed = False
    else:
        if single.json().get("status") is not None:
            print("FAIL: status should be null, got "
                  + str(single.json().get("status")))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_7():
    """/registry/onchain with no snapshot stored yet."""
    passed = True
    print("=== Scenario 7: /registry/onchain on empty cache ===")
    response = CLIENT.get("/registry/onchain")
    body = response.json()
    print(json.dumps(body, indent=2))
    if response.status_code != 200:
        print("FAIL: expected 200, got " + str(response.status_code))
        passed = False
    if body.get("status") != "no_snapshot":
        print("FAIL: expected status no_snapshot, got " + str(body.get("status")))
        passed = False
    if body.get("byLabel") != [] or body.get("byRelayer") != []:
        print("FAIL: expected empty groupings, got " + json.dumps(body))
        passed = False
    if body.get("summary", {}).get("labelRelayerConflict", {}).get("count") != 0:
        print("FAIL: expected zero label conflicts, got " + json.dumps(body.get("summary")))
        passed = False
    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def run_scenario_8():
    """Registry ingest -> read round trip, conflict flags, bad payload rejected."""
    passed = True
    print("=== Scenario 8: registry ingest and reads ===")
    by_label = [
        {
            "facilitator": "alpha",
            "relayers": ["0x" + "aa" * 20],
            "totalRecords": 3,
            "confirmed": 2,
            "contradictedFailure": 1,
            "announcementHonestyPct": 66,
            "labelRelayerConflict": False,
        },
        {
            "facilitator": "beta",
            "relayers": ["0x" + "aa" * 20, "0x" + "bb" * 20],
            "totalRecords": 2,
            "confirmed": 1,
            "labelRelayerConflict": True,
        },
    ]
    by_relayer = [
        {
            "relayer": "0x" + "aa" * 20,
            "labels": ["alpha", "beta"],
            "totalRecords": 5,
            "relayerLabelConflict": True,
        },
    ]
    read_at = "2026-09-15T12:00:00+00:00"
    ingest = CLIENT.post(
        "/ingest/registry",
        json={"readAt": read_at, "byLabel": by_label, "byRelayer": by_relayer},
    )
    if ingest.status_code != 200 or ingest.json().get("ingested") != 2:
        print("FAIL: ingest/registry -> " + str(ingest.status_code) + " " + ingest.text)
        passed = False

    listing = CLIENT.get("/registry/onchain")
    body = listing.json()
    print(json.dumps(body, indent=2))
    if listing.status_code != 200 or body.get("status") != "ok":
        print("FAIL: /registry/onchain -> " + str(listing.status_code) + " " + listing.text)
        passed = False
    if body.get("readAt") != read_at:
        print("FAIL: readAt mismatch: " + str(body.get("readAt")))
        passed = False
    if len(body.get("byLabel", [])) != 2 or len(body.get("byRelayer", [])) != 1:
        print("FAIL: grouping sizes wrong: " + json.dumps(body))
        passed = False
    summary = body.get("summary", {})
    if summary.get("labelRelayerConflict", {}).get("count") != 1:
        print("FAIL: expected one label conflict, got " + json.dumps(summary))
        passed = False
    if summary.get("labelRelayerConflict", {}).get("labels") != ["beta"]:
        print("FAIL: label conflict list wrong: " + json.dumps(summary))
        passed = False
    if summary.get("relayerLabelConflict", {}).get("count") != 1:
        print("FAIL: expected one relayer conflict, got " + json.dumps(summary))
        passed = False

    rejected = CLIENT.post(
        "/ingest/registry",
        json={"readAt": read_at, "byLabel": "not-a-list", "byRelayer": []},
    )
    if rejected.status_code != 400:
        print("FAIL: non-list byLabel should be 400, got " + str(rejected.status_code))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def main():
    database.init_database()
    results = []
    try:
        results = [
            run_scenario_1(),
            run_scenario_2(),
            run_scenario_3(),
            run_scenario_4(),
            run_scenario_5(),
            run_scenario_6(),
            run_scenario_7(),
            run_scenario_8(),
        ]
    finally:
        database.DB_PATH = _ORIGINAL_DB_PATH
        os.environ.pop("X402_API_URL", None)
        shutil.rmtree(_TEMP_DIR, ignore_errors=True)

    total = len(results)
    passed_count = sum(1 for r in results if r)
    print("")
    if passed_count == total:
        print("All " + str(total) + " scenarios passed.")
    else:
        print(str(total - passed_count) + " of " + str(total)
              + " scenarios FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

