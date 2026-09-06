"""Integration test for A6 RPC verification adapter against a fake RPC server."""

import json
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

import http_evidence_collector as collector
import rpc_verification_adapter as adapter

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


def make_fake_rpc(chain_id="0x14a34", tx_found=True, receipt_status="0x1",
                  transfer_from=PAYER, transfer_to=PAYEE, transfer_value=10000,
                  tx_input="0xe3ee160e" + "00" * 100):
    """Build a fake _rpc_call that answers the four RPC methods A6 uses,
    plus eth_blockNumber for the not-found path."""
    def fake_rpc_call(rpc_url, method, params):
        if method == "eth_chainId":
            return chain_id
        if method == "eth_getTransactionByHash":
            if not tx_found:
                return None
            return {
                "blockNumber": BLOCK_HEX,
                "input": tx_input,
            }
        if method == "eth_getTransactionReceipt":
            if not tx_found:
                return None
            return {
                "status": receipt_status,
                "logs": [{
                    "address": ASSET,
                    "topics": [
                        adapter.TRANSFER_TOPIC0,
                        adapter._address_topic(transfer_from),
                        adapter._address_topic(transfer_to),
                    ],
                    "data": "0x" + format(transfer_value, "x").rjust(64, "0"),
                }],
            }
        if method == "eth_getBlockByNumber":
            return {"timestamp": "0x1788264386"}
        if method == "eth_blockNumber":
            return "0x2c1b079"
        raise AssertionError("unexpected RPC method: " + method)
    return fake_rpc_call


def run_scenario(name, payment_result, rpc_fake, expect_audit_called,
                 expect_verification_status=None, expect_anchor=None):
    """Run one scenario against the real collect_and_audit() with the
    network/GenLayer functions monkeypatched out."""
    calls = {"audit": [], "declaration": [], "verify": []}

    def fake_attempt_payment(resource_url, key):
        return payment_result

    def fake_find_settlement_transfer(**kwargs):
        return (None, [])

    def fake_submit_x402_audit(**kwargs):
        calls["audit"].append(kwargs)
        return {"verdict": "CONFIRMED"}

    def fake_submit_declaration_audit(**kwargs):
        calls["declaration"].append(kwargs)
        return {"verdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR"}

    def fake_get_provider(provider_id):
        return PROVIDER

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_audit = collector.submit_x402_audit
    original_decl = collector.submit_declaration_audit
    original_provider = collector.get_provider
    original_rpc = adapter._rpc_call

    collector.attempt_payment = fake_attempt_payment
    collector.find_settlement_transfer = fake_find_settlement_transfer
    collector.submit_x402_audit = fake_submit_x402_audit
    collector.submit_declaration_audit = fake_submit_declaration_audit
    collector.get_provider = fake_get_provider
    adapter._rpc_call = rpc_fake

    try:
        summary = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="http://fake-rpc",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        collector.attempt_payment = original_attempt
        collector.find_settlement_transfer = original_find
        collector.submit_x402_audit = original_audit
        collector.submit_declaration_audit = original_decl
        collector.get_provider = original_provider
        adapter._rpc_call = original_rpc

    print("=== " + name + " ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    passed = True
    if expect_audit_called:
        if len(calls["audit"]) != 1:
            print("FAIL: expected submit_x402_audit to be called once, got "
                  + str(len(calls["audit"])))
            passed = False
        else:
            audit_kwargs = calls["audit"][0]
            if expect_verification_status is not None:
                actual_status = summary.get("verificationStatus")
                if actual_status != expect_verification_status:
                    print("FAIL: expected verificationStatus "
                          + expect_verification_status + ", got "
                          + str(actual_status))
                    passed = False
            if expect_anchor is not None:
                actual_anchor = audit_kwargs.get("anchor_block", "")
                if actual_anchor != expect_anchor:
                    print("FAIL: expected anchor_block " + expect_anchor
                          + ", got " + str(actual_anchor))
                    passed = False
    else:
        if len(calls["audit"]) != 0:
            print("FAIL: expected submit_x402_audit NOT to be called, got "
                  + str(len(calls["audit"])))
            passed = False

    print("PASSED" if passed else "FAILED")
    print("")
    return passed


def main():
    results = []

    # Scenario 1: header capture with valid transaction hash.
    payment_result = {
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
        "Scenario 1: header capture, A6 verifies, anchor passed",
        payment_result,
        make_fake_rpc(),
        expect_audit_called=True,
        expect_verification_status="VERIFIED",
        expect_anchor=BLOCK_HEX,
    ))

    # Scenario 2: RPC fallback path. find_settlement_transfer is stubbed
    # to return None in run_scenario, so this scenario needs its own
    # runner with a different stub. We reuse run_scenario but override
    # find_settlement_transfer inside a custom block.
    calls = {"audit": []}

    def fake_attempt_payment_2(resource_url, key):
        return {
            "initialStatus": 402,
            "requirements": REQUIREMENTS,
            "retryStatus": 200,
            "payerAddress": PAYER,
            "signedValidBefore": "1788264699",
            "settlementClaim": None,
        }

    def fake_find_settlement_transfer_2(**kwargs):
        return (GOOD_HASH, [GOOD_HASH])

    def fake_submit_x402_audit_2(**kwargs):
        calls["audit"].append(kwargs)
        return {"verdict": "CONFIRMED"}

    def fake_get_provider_2(provider_id):
        return PROVIDER

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_audit = collector.submit_x402_audit
    original_provider = collector.get_provider
    original_rpc = adapter._rpc_call

    collector.attempt_payment = fake_attempt_payment_2
    collector.find_settlement_transfer = fake_find_settlement_transfer_2
    collector.submit_x402_audit = fake_submit_x402_audit_2
    collector.get_provider = fake_get_provider_2
    adapter._rpc_call = make_fake_rpc()

    try:
        summary2 = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="http://fake-rpc",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        collector.attempt_payment = original_attempt
        collector.find_settlement_transfer = original_find
        collector.submit_x402_audit = original_audit
        collector.get_provider = original_provider
        adapter._rpc_call = original_rpc

    print("=== Scenario 2: RPC fallback, A6 verifies, anchor passed ===")
    print(json.dumps(summary2, indent=2, ensure_ascii=False))
    passed2 = (
        len(calls["audit"]) == 1
        and summary2.get("verificationStatus") == "VERIFIED"
        and calls["audit"][0].get("anchor_block", "") == BLOCK_HEX
        and summary2.get("evidenceSource") == "rpc_fallback"
    )
    print("PASSED" if passed2 else "FAILED")
    print("")
    results.append(passed2)

    # Scenario 3: no header, no RPC match. A6 and the contract must never
    # be called.
    def fake_attempt_payment_3(resource_url, key):
        return {
            "initialStatus": 402,
            "requirements": REQUIREMENTS,
            "retryStatus": 200,
            "payerAddress": PAYER,
            "signedValidBefore": "1788264699",
            "settlementClaim": None,
        }

    def fake_find_settlement_transfer_3(**kwargs):
        return (None, [])

    audit_calls_3 = []

    def fake_submit_x402_audit_3(**kwargs):
        audit_calls_3.append(kwargs)
        return {"verdict": "CONFIRMED"}

    original_attempt = collector.attempt_payment
    original_find = collector.find_settlement_transfer
    original_audit = collector.submit_x402_audit
    original_provider = collector.get_provider

    collector.attempt_payment = fake_attempt_payment_3
    collector.find_settlement_transfer = fake_find_settlement_transfer_3
    collector.submit_x402_audit = fake_submit_x402_audit_3
    collector.get_provider = fake_get_provider_2

    try:
        summary3 = collector.collect_and_audit(
            provider_id="test-provider",
            resource_url="http://127.0.0.1:9999/resource",
            rpc_url="http://fake-rpc",
            payer_private_key="0x" + "11" * 32,
        )
    finally:
        collector.attempt_payment = original_attempt
        collector.find_settlement_transfer = original_find
        collector.submit_x402_audit = original_audit
        collector.get_provider = original_provider

    print("=== Scenario 3: no evidence, A6 and contract never called ===")
    print(json.dumps(summary3, indent=2, ensure_ascii=False))
    passed3 = (
        len(audit_calls_3) == 0
        and summary3.get("outcome") == "PENDING_NO_EVIDENCE_YET"
        and "verificationStatus" not in summary3
    )
    print("PASSED" if passed3 else "FAILED")
    print("")
    results.append(passed3)

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