"""Integration test for A7 claim builder covering REQUIREMENTS_INCOMPLETE and UNKNOWN_CLAIM_SOURCE branches."""

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

import http_evidence_collector as collector
import claim_builder

GOOD_HASH = "0x" + "ab" * 32
PAYER = "0x1d8757aae49cb66adf814ccf26658a3e31a20aa1"
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
    "known_declaration_url": None,
}

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

EXPECTED_CLAIM_DISCOVERED = [
    "test-provider",
    "https://sepolia.base.org",
    "",
    GOOD_HASH,
    "",
    "",
    "",
    "",
    "1788264699",
    "exact",
    "eip155:84532",
    PAYEE,
    ASSET,
    "10000",
    BLOCK_HEX,
    "discovered_only",
]


class FakeClient:
    """Minimal stand-in for the genlayer_py client object. The real
    client has write_contract, wait_for_transaction_receipt and
    read_contract as methods; this object provides the same three with
    captured call recording."""

    def __init__(self, captured_calls):
        self._captured = captured_calls

    def write_contract(self, **kwargs):
        self._captured.append(kwargs)
        return "0x" + "12" * 32

    def wait_for_transaction_receipt(self, **kwargs):
        return None

    def read_contract(self, **kwargs):
        return json.dumps([json.dumps({"verdict": "CONFIRMED"})])


def run_scenario(name, payment_result, captured_calls, expect_calls,
                 expected_claim=None):
    """Run collect_and_audit with network/GenLayer monkeypatched out,
    capturing the args passed to write_contract."""
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

    fake_client = FakeClient(captured_calls)

    def fake_get_client():
        return fake_client

    def fake_submit_declaration_audit(**kwargs):
        return {"verdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR"}

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_verify = collector.verify_transaction
    original_provider = collector.get_provider
    original_client = collector.get_client
    original_decl = collector.submit_declaration_audit

    collector.attempt_payment = fake_attempt_payment
    collector.find_settlement_transfer = fake_find_settlement_transfer
    collector.verify_transaction = fake_verify_transaction
    collector.get_provider = fake_get_provider
    collector.get_client = fake_get_client
    collector.submit_declaration_audit = fake_submit_declaration_audit

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
        collector.submit_declaration_audit = original_decl

    print("=== " + name + " ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    passed = True
    if expect_calls:
        if len(captured_calls) != 1:
            print("FAIL: expected 1 write_contract call, got "
                  + str(len(captured_calls)))
            passed = False
        else:
            call = captured_calls[0]
            if call.get("function_name") != "audit":
                print("FAIL: expected function_name 'audit', got "
                      + str(call.get("function_name")))
                passed = False
            actual_args = call.get("args", [])
            if expected_claim is not None and actual_args != expected_claim:
                print("FAIL: expected args:")
                print(json.dumps(expected_claim, indent=2))
                print("got:")
                print(json.dumps(actual_args, indent=2))
                passed = False
            if len(actual_args) != 16:
                print("FAIL: expected 16 args, got " + str(len(actual_args)))
                passed = False
    else:
        if len(captured_calls) != 0:
            print("FAIL: expected no write_contract calls, got "
                  + str(len(captured_calls)))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def main():
    results = []

    # Scenario 1: header capture, self_probe.
    captured_1 = []
    payment_result_1 = {
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
    results.append(run_scenario(
        "Scenario 1: header capture, A7 builds self_probe claim",
        payment_result_1,
        captured_1,
        expect_calls=True,
        expected_claim=EXPECTED_CLAIM_SELF_PROBE,
    ))

    # Scenario 2: RPC fallback, discovered_only.
    captured_2 = []

    def fake_find_settlement_transfer_2(**kwargs):
        return (GOOD_HASH, [GOOD_HASH])

    def fake_verify_transaction_2(**kwargs):
        return {
            "verificationStatus": "VERIFIED",
            "anchorBlock": BLOCK_HEX,
            "reason": None,
        }

    fake_client_2 = FakeClient(captured_2)

    def fake_get_client_2():
        return fake_client_2

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_verify = collector.verify_transaction
    original_provider = collector.get_provider
    original_client = collector.get_client

    collector.attempt_payment = lambda resource_url, key: {
        "initialStatus": 402,
        "requirements": REQUIREMENTS,
        "retryStatus": 200,
        "payerAddress": PAYER,
        "signedValidBefore": "1788264699",
        "settlementClaim": None,
    }
    collector.find_settlement_transfer = fake_find_settlement_transfer_2
    collector.verify_transaction = fake_verify_transaction_2
    collector.get_provider = lambda provider_id: PROVIDER
    collector.get_client = fake_get_client_2

    try:
        summary_2 = collector.collect_and_audit(
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

    print("=== Scenario 2: RPC fallback, A7 builds discovered_only claim ===")
    print(json.dumps(summary_2, indent=2, ensure_ascii=False))

    passed_2 = False
    if len(captured_2) == 1:
        actual_args = captured_2[0].get("args", [])
        passed_2 = actual_args == EXPECTED_CLAIM_DISCOVERED
    if not passed_2:
        print("FAIL: expected args:")
        print(json.dumps(EXPECTED_CLAIM_DISCOVERED, indent=2))
        print("got calls: " + json.dumps(captured_2, indent=2))
    print("PASSED" if passed_2 else "FAILED")
    print("")
    results.append(passed_2)

    # Scenario 3: no evidence, A7 and contract never called.
    captured_3 = []

    def fake_get_client_3():
        return FakeClient(captured_3)

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_provider = collector.get_provider
    original_client = collector.get_client

    collector.attempt_payment = lambda resource_url, key: {
        "initialStatus": 402,
        "requirements": REQUIREMENTS,
        "retryStatus": 200,
        "payerAddress": PAYER,
        "signedValidBefore": "1788264699",
        "settlementClaim": None,
    }
    collector.find_settlement_transfer = lambda **kwargs: (None, [])
    collector.get_provider = lambda provider_id: PROVIDER
    collector.get_client = fake_get_client_3

    try:
        summary_3 = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="https://sepolia.base.org",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        collector.attempt_payment = original_attempt
        collector.find_settlement_transfer = original_find
        collector.get_provider = original_provider
        collector.get_client = original_client

    print("=== Scenario 3: no evidence, A7 and contract never called ===")
    print(json.dumps(summary_3, indent=2, ensure_ascii=False))

    passed_3 = (
        len(captured_3) == 0
        and summary_3.get("outcome") == "PENDING_NO_EVIDENCE_YET"
    )
    print("PASSED" if passed_3 else "FAILED")
    print("")
    results.append(passed_3)

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