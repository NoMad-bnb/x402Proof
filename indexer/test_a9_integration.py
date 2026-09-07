"""Integration test for A9 evidence store covering write, dedupe, and reload semantics."""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import http_evidence_collector as collector
import evidence_store

GOOD_HASH = "0x" + "ab" * 32
PAYER = "0x" + "11" * 32
PAYEE = "0x572bb4287aacbd42d647d611459e996ec9c89c52"
ASSET = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
BLOCK_HEX = "0x2c1b071"

REQUIREMENTS = {
    "scheme": "exact",
    "network": "eip155:84532",
    "payTo": PAYEE,
    "asset": ASSET,
    "maxAmountRequired": "10000",
    "maxTimeoutSeconds": 300,
    "x402Version": 2,
}

PROVIDER = {
    "provider_id": "test-provider",
    "label": "test-provider",
    "known_declaration_url": "https://fac.example/declaration.json",
}

PAYMENT_RESULT_HEADER_CAPTURE = {
    "initialStatus": 402,
    "requirements": REQUIREMENTS,
    "retryStatus": 200,
    "payerAddress": PAYER,
    "signedValidBefore": "1788264699",
    "settlementClaim": {
        "success": True,
        "transaction": GOOD_HASH,
        "network": "eip155:84532",
        "payer": PAYER,
    },
}


class FakeClient:
    """Minimal stand-in for the genlayer_py client object."""

    def __init__(self, captured_calls):
        self._captured = captured_calls

    def write_contract(self, **kwargs):
        self._captured.append(kwargs)
        return "0x" + "12" * 32

    def wait_for_transaction_receipt(self, **kwargs):
        return None

    def read_contract(self, **kwargs):
        return json.dumps([json.dumps({"verdict": "CONFIRMED"})])


def _make_temp_store():
    handle, temp_path = tempfile.mkstemp(suffix=".json", prefix="x402_evidence_")
    os.close(handle)
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump([], f)
        f.write("\n")
    return temp_path


def _patch_network(temp_path):
    """Monkeypatch the collector's live-network seams and evidence_store,
    returning a restore function."""

    captured_calls = []

    def fake_attempt_payment(resource_url, key):
        return PAYMENT_RESULT_HEADER_CAPTURE

    def fake_find_settlement_transfer(**kwargs):
        return (None, [])

    def fake_verify_transaction(**kwargs):
        return {
            "verificationStatus": "VERIFIED",
            "anchorBlock": BLOCK_HEX,
            "transactionHash": GOOD_HASH,
            "chainId": "0x14a34",
            "reason": None,
        }

    def fake_get_provider(provider_id):
        return PROVIDER

    def fake_get_client():
        client = FakeClient(captured_calls)
        return client

    def fake_store_evidence(summary, path=None):
        return evidence_store.store_evidence(summary, path=temp_path)

    originals = (
        collector.attempt_payment,
        collector.find_settlement_transfer,
        collector.verify_transaction,
        collector.get_provider,
        collector.get_client,
        collector.store_evidence,
    )
    collector.attempt_payment = fake_attempt_payment
    collector.find_settlement_transfer = fake_find_settlement_transfer
    collector.verify_transaction = fake_verify_transaction
    collector.get_provider = fake_get_provider
    collector.get_client = fake_get_client
    collector.store_evidence = fake_store_evidence

    def restore():
        (
            collector.attempt_payment,
            collector.find_settlement_transfer,
            collector.verify_transaction,
            collector.get_provider,
            collector.get_client,
            collector.store_evidence,
        ) = originals

    return restore, captured_calls


def run_scenario_1():
    """Header capture -> evidence is stored with EVIDENCE_STORED."""
    temp_path = _make_temp_store()
    restore, captured_calls = _patch_network(temp_path)
    try:
        summary = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        restore()

    print("=== Scenario 1: header capture stores evidence ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    passed = True
    if summary.get("outcome") != "AUDITED":
        print("FAIL: expected outcome AUDITED, got " + str(summary.get("outcome")))
        passed = False
    if summary.get("auditVerdict") != "CONFIRMED":
        print("FAIL: expected auditVerdict CONFIRMED, got "
              + str(summary.get("auditVerdict")))
        passed = False

    stored = evidence_store.find_evidence(
        "0x14a34", GOOD_HASH, path=temp_path
    )
    if len(stored) != 1:
        print("FAIL: expected 1 stored evidence record, got " + str(len(stored)))
        passed = False
    else:
        record = stored[0]
        if record.get("evidenceKey", {}).get("transactionHash") != GOOD_HASH:
            print("FAIL: stored evidence key transactionHash mismatch")
            passed = False
        if record.get("summary", {}).get("auditVerdict") != "CONFIRMED":
            print("FAIL: stored evidence summary mismatch")
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    os.remove(temp_path)
    return passed


def run_scenario_2():
    """Re-run the exact same job -> EVIDENCE_DUPLICATE, file still has
    exactly one record for that key."""
    temp_path = _make_temp_store()
    restore, _ = _patch_network(temp_path)
    try:
        collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        restore()

    restore2, _ = _patch_network(temp_path)
    try:
        summary2 = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        restore2()

    print("=== Scenario 2: re-run returns EVIDENCE_DUPLICATE ===")
    print(json.dumps(summary2, indent=2, ensure_ascii=False))

    passed = True
    if summary2.get("evidenceStoreStatus") != "EVIDENCE_DUPLICATE":
        print("FAIL: expected evidenceStoreStatus EVIDENCE_DUPLICATE, got "
              + str(summary2.get("evidenceStoreStatus")))
        passed = False

    stored = evidence_store.find_evidence(
        "0x14a34", GOOD_HASH, path=temp_path
    )
    if len(stored) != 1:
        print("FAIL: expected exactly 1 stored record after duplicate, got "
              + str(len(stored)))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    os.remove(temp_path)
    return passed


def run_scenario_3():
    """No evidence path -> store_evidence is never called, evidence file
    stays empty."""
    temp_path = _make_temp_store()

    def fake_attempt_payment(resource_url, key):
        return {
            "initialStatus": 402,
            "requirements": REQUIREMENTS,
            "retryStatus": 200,
            "payerAddress": PAYER,
            "signedValidBefore": "1788264699",
            "settlementClaim": None,
        }

    def fake_find_settlement_transfer(**kwargs):
        return (None, [])

    def fake_verify_transaction(**kwargs):
        return {
            "verificationStatus": "VERIFIED",
            "anchorBlock": BLOCK_HEX,
            "reason": None,
        }

    def fake_get_provider(provider_id):
        return PROVIDER

    def fake_get_client():
        client = FakeClient([])
        return client

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_verify = collector.verify_transaction
    original_provider = collector.get_provider
    original_client = collector.get_client
    original_store = collector.store_evidence

    collector.attempt_payment = fake_attempt_payment
    collector.find_settlement_transfer = fake_find_settlement_transfer
    collector.verify_transaction = fake_verify_transaction
    collector.get_provider = fake_get_provider
    collector.get_client = fake_get_client
    collector.store_evidence = None

    try:
        summary = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        collector.attempt_payment = original_attempt
        collector.find_settlement_transfer = original_find
        collector.verify_transaction = original_verify
        collector.get_provider = original_provider
        collector.get_client = original_client
        collector.store_evidence = original_store

    print("=== Scenario 3: no evidence, store_evidence never called ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    passed = True
    if summary.get("outcome") != "PENDING_NO_EVIDENCE_YET":
        print("FAIL: expected outcome PENDING_NO_EVIDENCE_YET, got "
              + str(summary.get("outcome")))
        passed = False
    if "evidenceStoreStatus" in summary:
        print("FAIL: evidenceStoreStatus must not be present when no "
              "evidence was stored")
        passed = False

    stored = evidence_store.load_evidence(temp_path)
    if len(stored) != 0:
        print("FAIL: expected empty evidence file, got " + str(len(stored))
              + " records")
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    os.remove(temp_path)
    return passed


def run_scenario_4():
    """Batch-settlement branch: zero gas, no verdict.

    The payment result carries scheme=batch-settlement. collect_and_audit()
    must route to capture_batch_settlement and NOT call get_client() (no
    GenLayer consensus round, no contract write), must not produce an
    auditRecord/declarationRecord, must mark notJudged, and must still
    store the evidence under its natural key so the batch class surfaces
    in the store.
    """
    temp_path = _make_temp_store()
    batch_hash = "0x" + "cd" * 32

    batch_requirements = dict(REQUIREMENTS)
    batch_requirements["scheme"] = "batch-settlement"
    batch_requirements["amount"] = "100000"

    def fake_attempt_payment(resource_url, key):
        return {
            "initialStatus": 402,
            "requirements": batch_requirements,
            "retryStatus": 200,
            "payerAddress": PAYER,
            "signedValidBefore": "",
            "settlementClaim": {
                "success": True,
                "transaction": batch_hash,
                "network": "eip155:84532",
                "amount": "",
                "extra": {
                    "channelState": {"channelId": "0xabc", "balance": "100000"}
                },
            },
        }

    def fake_verify_transaction(**kwargs):
        return {
            "verificationStatus": "TRANSACTION_EXISTS",
            "anchorBlock": BLOCK_HEX,
            "transactionHash": batch_hash,
            "chainId": "0x14a34",
            "reason": None,
        }

    def fake_get_provider(provider_id):
        return PROVIDER

    def fake_get_client():
        raise AssertionError(
            "batch path must NEVER build a GenLayer client "
            "(zero gas, zero consensus round)"
        )

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_verify = collector.verify_transaction
    original_provider = collector.get_provider
    original_client = collector.get_client
    original_store = collector.store_evidence
    original_evidence_path = evidence_store.DEFAULT_EVIDENCE_PATH
    evidence_store.DEFAULT_EVIDENCE_PATH = temp_path

    # batch_evidence_capture imports verify_transaction into its OWN module
    # namespace, so patching collector.verify_transaction does not affect
    # it. Patch the module-level name directly to keep this scenario fully
    # offline (no live RPC).
    import batch_evidence_capture as batch_mod
    original_batch_verify = batch_mod.verify_transaction
    batch_mod.verify_transaction = fake_verify_transaction

    collector.attempt_payment = fake_attempt_payment
    collector.find_settlement_transfer = lambda **kwargs: (None, [])
    collector.verify_transaction = fake_verify_transaction
    collector.get_provider = fake_get_provider
    collector.get_client = fake_get_client
    collector.store_evidence = None  # batch path stores via its own import

    try:
        summary = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        collector.attempt_payment = original_attempt
        collector.find_settlement_transfer = original_find
        collector.verify_transaction = original_verify
        collector.get_provider = original_provider
        collector.get_client = original_client
        collector.store_evidence = original_store
        evidence_store.DEFAULT_EVIDENCE_PATH = original_evidence_path
        batch_mod.verify_transaction = original_batch_verify

    print("=== Scenario 4: batch-settlement branch is zero-gas and un-judged ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    passed = True
    if summary.get("outcome") != "BATCH_CAPTURED_NOT_JUDGED":
        print("FAIL: expected outcome BATCH_CAPTURED_NOT_JUDGED, got "
              + str(summary.get("outcome")))
        passed = False
    if summary.get("notJudged") is not True:
        print("FAIL: notJudged must be True")
        passed = False
    if summary.get("evidenceSource") != "batch_capture":
        print("FAIL: expected evidenceSource batch_capture, got "
              + str(summary.get("evidenceSource")))
        passed = False
    if "auditRecord" in summary or "declarationRecord" in summary:
        print("FAIL: batch path must not create audit/declaration records")
        passed = False
    if summary.get("evidenceStoreStatus") != "EVIDENCE_STORED":
        print("FAIL: batch evidence with a hash must be stored, got "
              + str(summary.get("evidenceStoreStatus")))
        passed = False

    stored = evidence_store.load_evidence(temp_path)
    batch_stored = [
        r for r in stored
        if (r.get("summary") or {}).get("claimSource") == "batch_settlement"
    ]
    if len(batch_stored) != 1:
        print("FAIL: expected exactly 1 stored batch record, got "
              + str(len(batch_stored)))
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    os.remove(temp_path)
    return passed


def main():
    results = [
        run_scenario_1(),
        run_scenario_2(),
        run_scenario_3(),
        run_scenario_4(),
    ]
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
