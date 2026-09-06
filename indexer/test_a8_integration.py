"""Integration test for A8 contract callers."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contract_callers
import http_evidence_collector as collector

GOOD_HASH = "0x" + "ab" * 32
PAYER = "0x1d8757aae49cb66adf814ccf26658a3e31a20aa1"
PAYEE = "0x572bb4287aacbd42d647d611459e996ec9c89c52"
ASSET = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
BLOCK_HEX = "0x2c1b071"
TX_HASH_OUT = "0x" + "12" * 32

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
    "known_declaration_url": None,
}

# The exact 16-element list X402Auditor.audit() must receive, in order,
# for the scenario 1 fixtures below. Same expectations as test_a7's
# self_probe list: A8 must forward A7's claim untouched.
EXPECTED_CLAIM_SELF_PROBE = [
    "test-provider",
    "https://sepolia.base.org",
    "true",
    GOOD_HASH,
    "eip155:84532",
    PAYER,
    "",
    "",
    "1788264699",
    "exact",
    "eip155:84532",
    PAYEE,
    ASSET,
    "10000",
    BLOCK_HEX,
    "self_probe",
]

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
    """Minimal stand-in for the genlayer_py client. Records every call
    in order with its kwargs, and returns a configurable read-back
    result, so tests can assert exactly what would reach the network."""

    def __init__(self, read_result=None):
        self.calls = []
        self._read_result = (
            read_result
            if read_result is not None
            else json.dumps([json.dumps({"verdict": "CONFIRMED"})])
        )

    def write_contract(self, **kwargs):
        self.calls.append(("write_contract", kwargs))
        return TX_HASH_OUT

    def wait_for_transaction_receipt(self, **kwargs):
        self.calls.append(("wait_for_transaction_receipt", kwargs))
        return None

    def read_contract(self, **kwargs):
        self.calls.append(("read_contract", kwargs))
        return self._read_result

def _patch_collector_network(client, payment_result):
    """Monkeypatch only the collector's live-network seams, returning a
    restore function. get_client is replaced so the delegating submit
    functions receive OUR client through their own namespace seam."""

    def fake_attempt_payment(resource_url, key):
        return payment_result

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
        return client

    originals = (
        collector.attempt_payment,
        collector.find_settlement_transfer,
        collector.verify_transaction,
        collector.get_provider,
        collector.get_client,
    )
    collector.attempt_payment = fake_attempt_payment
    collector.find_settlement_transfer = fake_find_settlement_transfer
    collector.verify_transaction = fake_verify_transaction
    collector.get_provider = fake_get_provider
    collector.get_client = fake_get_client

    def restore():
        (
            collector.attempt_payment,
            collector.find_settlement_transfer,
            collector.verify_transaction,
            collector.get_provider,
            collector.get_client,
        ) = originals

    return restore


def run_scenario_1():
    """Full collector path with a captured settlement header: the claim
    must reach write_contract in exact order THROUGH the A8 layer."""
    client = FakeClient(json.dumps([json.dumps({"verdict": "CONFIRMED"})]))
    restore = _patch_collector_network(client, PAYMENT_RESULT_HEADER_CAPTURE)
    try:
        summary = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        restore()

    print("=== Scenario 1: full pipeline, claim sent THROUGH the A8 layer ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    order = [c[0] for c in client.calls]
    writes = [c for c in client.calls if c[0] == "write_contract"]
    waits = [c for c in client.calls if c[0] == "wait_for_transaction_receipt"]
    reads = [c for c in client.calls if c[0] == "read_contract"]

    passed = True
    if summary.get("outcome") != "AUDITED":
        print("FAIL: expected outcome AUDITED, got " + str(summary.get("outcome")))
        passed = False
    if summary.get("auditVerdict") != "CONFIRMED":
        print("FAIL: expected auditVerdict CONFIRMED parsed back through A8, got "
              + str(summary.get("auditVerdict")))
        passed = False
    if order != ["write_contract", "wait_for_transaction_receipt", "read_contract"]:
        print("FAIL: unexpected call order: " + repr(order))
        passed = False
    if len(writes) != 1:
        print("FAIL: expected 1 write_contract call, got " + str(len(writes)))
        passed = False
    else:
        write_kwargs = writes[0][1]
        if write_kwargs.get("address") != contract_callers.X402_AUDITOR_ADDRESS:
            print("FAIL: wrong address: " + str(write_kwargs.get("address")))
            passed = False
        if write_kwargs.get("function_name") != "audit":
            print("FAIL: wrong function_name: " + str(write_kwargs.get("function_name")))
            passed = False
        if write_kwargs.get("args") != EXPECTED_CLAIM_SELF_PROBE:
            print("FAIL: the 16-element claim did not arrive in exact order")
            print(json.dumps(EXPECTED_CLAIM_SELF_PROBE, indent=2))
            print("got:")
            print(json.dumps(write_kwargs.get("args"), indent=2))
            passed = False
    if len(waits) != 1 or waits[0][1].get("status") != "ACCEPTED" or waits[0][1].get("transaction_hash") != TX_HASH_OUT:
        print("FAIL: expected one wait with ACCEPTED for the write's tx hash")
        passed = False
    if len(reads) != 1 or reads[0][1].get("function_name") != "get_verdicts":
        print("FAIL: expected one read of get_verdicts")
        passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed

def run_scenario_2():
    """Declaration path through A8, plus the pre-flight empty guard."""
    # Part A: the 4 arguments reach audit_declaration() in order.
    client = FakeClient(
        json.dumps([json.dumps({"verdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR"})])
    )
    original_client = collector.get_client
    collector.get_client = lambda: client
    try:
        record = collector.submit_declaration_audit(
            facilitator_label="test-provider",
            declaration_url="https://fac.example/declaration.json",
            rpc_url="https://sepolia.base.org",
            transaction_hash=GOOD_HASH,
        )
    finally:
        collector.get_client = original_client

    print("=== Scenario 2a: declaration sent THROUGH the A8 layer ===")
    print(json.dumps(record, indent=2, ensure_ascii=False))

    writes = [c for c in client.calls if c[0] == "write_contract"]
    waits = [c for c in client.calls if c[0] == "wait_for_transaction_receipt"]
    reads = [c for c in client.calls if c[0] == "read_contract"]

    passed_a = True
    if len(writes) != 1:
        print("FAIL: expected 1 write_contract call, got " + str(len(writes)))
        passed_a = False
    else:
        write_kwargs = writes[0][1]
        if write_kwargs.get("address") != contract_callers.DECLARATION_AUDIT_ADDRESS:
            print("FAIL: wrong address: " + str(write_kwargs.get("address")))
            passed_a = False
        if write_kwargs.get("function_name") != "audit_declaration":
            print("FAIL: wrong function_name: " + str(write_kwargs.get("function_name")))
            passed_a = False
        expected_args = [
            "test-provider",
            "https://fac.example/declaration.json",
            "https://sepolia.base.org",
            GOOD_HASH,
        ]
        if write_kwargs.get("args") != expected_args:
            print("FAIL: declaration args did not arrive in order")
            print(json.dumps(expected_args, indent=2))
            print("got:")
            print(json.dumps(write_kwargs.get("args"), indent=2))
            passed_a = False
    if len(waits) != 1 or waits[0][1].get("status") != "ACCEPTED":
        print("FAIL: expected one wait with ACCEPTED")
        passed_a = False
    if len(reads) != 1 or reads[0][1].get("function_name") != "get_records":
        print("FAIL: expected one read of get_records")
        passed_a = False
    if record.get("verdict") != "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR":
        print("FAIL: wrong record parsed back: " + str(record.get("verdict")))
        passed_a = False
    print("PASSED" if passed_a else "FAILED")
    print("")

    # Part B: an empty transaction_hash is refused BEFORE any client
    # call. The contract itself would write nothing, and the
    # append-then-read-last pattern would then return the PREVIOUS
    # caller's record, so the guard must fire with zero gas.
    guard_client = FakeClient(json.dumps([json.dumps({"verdict": "NEVER"})]))
    collector.get_client = lambda: guard_client
    refused = False
    try:
        collector.submit_declaration_audit(
            facilitator_label="test-provider",
            declaration_url="https://fac.example/declaration.json",
            rpc_url="https://sepolia.base.org",
            transaction_hash="",
        )
    except RuntimeError as exc:
        refused = "CONTRACT_DECLARATION_EMPTY_ARG" in str(exc)
    finally:
        collector.get_client = original_client

    print("=== Scenario 2b: empty transaction_hash refused before any gas ===")
    passed_b = refused and guard_client.calls == []
    if not passed_b:
        print("FAIL: refused=" + str(refused) + " calls=" + repr(guard_client.calls))
    print("PASSED" if passed_b else "FAILED")
    print("")

    return passed_a and passed_b

def run_scenario_3():
    """An empty read-back after an accepted write must surface as
    CONTRACT_EMPTY_READ out of the REAL submit function, not silently
    return some other record. The write and wait DID happen; the
    refusal concerns the read-back only."""
    client = FakeClient("[]")
    original_client = collector.get_client
    collector.get_client = lambda: client
    raised = False
    try:
        collector.submit_x402_audit(
            facilitator_label="test-provider",
            rpc_url="https://sepolia.base.org",
            claim_success="true",
            claim_transaction=GOOD_HASH,
            claim_network="eip155:84532",
            claim_payer=PAYER,
            claim_amount="",
            claim_valid_before="1788264699",
            requirements=REQUIREMENTS,
            claim_source="self_probe",
            anchor_block=BLOCK_HEX,
        )
    except RuntimeError as exc:
        raised = "CONTRACT_EMPTY_READ" in str(exc)
    finally:
        collector.get_client = original_client

    print("=== Scenario 3: empty read-back surfaces CONTRACT_EMPTY_READ ===")
    order = [c[0] for c in client.calls]
    passed = raised and order == [
        "write_contract",
        "wait_for_transaction_receipt",
        "read_contract",
    ]
    if not passed:
        print("FAIL: raised=" + str(raised) + " calls=" + repr(order))
    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def main():
    results = [
        run_scenario_1(),
        run_scenario_2(),
        run_scenario_3(),
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



