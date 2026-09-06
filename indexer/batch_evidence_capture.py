"""Capture batch-settlement payments as an un-judged evidence class (zero gas, no contract calls)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from evidence_store import store_evidence
from rpc_verification_adapter import verify_transaction
from settlement_hash_extractor import extract_hash_from_claim

# The scheme name as it arrives in the accepted PaymentRequirements.
BATCH_SCHEME_NAMES = ("batch-settlement", "batch_settlement", "batch")

# Canonical escrow contract address (CREATE2, all EVM chains); the settle-path token sender.
BATCH_ESCROW_ADDRESS = "0x4020074e9dF2ce1deE5A9C1b5c3f541D02a10003"

DEFAULT_RPC_URL = "https://sepolia.base.org"


def is_batch_scheme(scheme) -> bool:
    """True when a PaymentRequirements.scheme names the batch settlement."""
    if scheme is None:
        return False
    return str(scheme).strip().lower() in BATCH_SCHEME_NAMES


def describe_batch_payment(requirements: dict, settlement_claim) -> dict:
    """Structural description of a batch payment as observed, no interpretation."""
    description = {
        "scheme": str(requirements.get("scheme", "")),
        "network": str(requirements.get("network", "")),
        "asset": str(requirements.get("asset", "")),
        "payTo": str(requirements.get("payTo", "")),
        "maxAmountRequired": str(
            requirements.get("maxAmountRequired", requirements.get("amount", ""))
        ),
        "escrowContract": BATCH_ESCROW_ADDRESS,
        "whyNotJudged": (
            "batch-settlement moves value through an escrow contract "
            "(settle sweeps escrow->receiver); a settle tx represents many "
            "payers and cannot be attributed to one payer without "
            "off-chain voucher data. X402Auditor.audit() would "
            "false-negative with REJECTED_PAYER_MISMATCH, so it is not called."
        ),
    }

    if isinstance(settlement_claim, dict):
        description["settlementSuccess"] = settlement_claim.get("success")
        description["settlementTransaction"] = settlement_claim.get("transaction")
        description["settlementNetwork"] = settlement_claim.get("network")
        description["settlementAmount"] = settlement_claim.get("amount")
        description["settlementPayer"] = settlement_claim.get("payer")
        description["settlementExtra"] = settlement_claim.get("extra")
        hash_result = extract_hash_from_claim(
            settlement_claim, source_label="batch_settlement"
        )
        description["transactionHash"] = hash_result["hash"]
        description["hashFieldUsed"] = hash_result["fieldUsed"]
        if hash_result["rejectedFields"]:
            description["hashRejectedFields"] = hash_result["rejectedFields"]
    else:
        description["settlement_claim_absent"] = True

    return description


def capture_batch_settlement(
    provider_id: str,
    provider_label: str,
    resource_url: str,
    requirements: dict,
    settlement_claim,
    rpc_url: str = DEFAULT_RPC_URL,
) -> dict:
    """Zero-gas batch-capture path; returns a summary that is never a verdict."""
    summary = {
        "providerId": provider_id,
        "resourceUrl": resource_url,
        "evidenceSource": "batch_capture",
        "claimSource": "batch_settlement",
        "notJudged": True,
        "schemeEvidence": describe_batch_payment(requirements, settlement_claim),
    }

    tx_hash = summary["schemeEvidence"].get("transactionHash") or ""
    summary["claimTransaction"] = tx_hash

    # Read-only on-chain verification (free JSON-RPC, no gas): evidence, not a verdict.
    if tx_hash:
        verification_record = verify_transaction(
            rpc_url=rpc_url,
            transaction_hash=tx_hash,
            expected_network=requirements.get("network", ""),
            requirements=requirements,
        )
        summary["verificationStatus"] = verification_record.get("verificationStatus")
        summary["verificationRecord"] = verification_record

    # Persist under the natural key when a hash exists; keyless captures are not stored.
    if tx_hash:
        store_result = store_evidence(summary)
        summary["evidenceStoreStatus"] = store_result.get("status")
        summary["evidenceKey"] = store_result.get("evidenceKey")

    summary["outcome"] = "BATCH_CAPTURED_NOT_JUDGED"
    return summary


def _run_self_test() -> None:
    """Offline self-test with a temp evidence file and a stubbed verification path."""
    import tempfile

    checks = []

    # --- is_batch_scheme -----------------------------------------------
    checks.append((
        "is_batch_scheme recognizes batch-settlement",
        is_batch_scheme("batch-settlement") is True,
    ))
    checks.append((
        "is_batch_scheme recognizes case-insensitive variants",
        is_batch_scheme("Batch-Settlement") is True
        and is_batch_scheme("batch_settlement") is True,
    ))
    checks.append((
        "is_batch_scheme rejects exact",
        is_batch_scheme("exact") is False,
    ))
    checks.append((
        "is_batch_scheme rejects None/empty",
        is_batch_scheme(None) is False and is_batch_scheme("") is False,
    ))

    # --- describe_batch_payment with a claim response -------------------
    requirements = {
        "scheme": "batch-settlement",
        "network": "eip155:84532",
        "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "payTo": "0x572bb4287aacbd42d647d611459e996ec9c89c52",
        "amount": "10000",
    }
    good_hash = "0x" + "ab" * 32
    claim = {
        "success": True,
        "transaction": good_hash,
        "network": "eip155:84532",
        "amount": "",
        "extra": {"channelState": {"channelId": "0x11", "balance": "100000"}},
    }
    desc = describe_batch_payment(requirements, claim)
    checks.append((
        "describe_batch_payment records scheme/network/escrow",
        desc["scheme"] == "batch-settlement"
        and desc["network"] == "eip155:84532"
        and desc["escrowContract"] == BATCH_ESCROW_ADDRESS,
    ))
    checks.append((
        "describe_batch_payment extracts the tx hash from transaction field",
        desc["transactionHash"] == good_hash
        and desc["hashFieldUsed"] == "transaction",
    ))
    checks.append((
        "describe_batch_payment keeps empty amount visible (claim moves no value)",
        desc["settlementAmount"] == "",
    ))
    checks.append((
        "describe_batch_payment records whyNotJudged",
        "false-negative with REJECTED_PAYER_MISMATCH" in desc["whyNotJudged"],
    ))

    # --- describe_batch_payment with absent claim -----------------------
    desc_none = describe_batch_payment(requirements, None)
    checks.append((
        "describe_batch_payment marks absent claim and no hash",
        desc_none.get("settlement_claim_absent") is True
        and desc_none.get("transactionHash") is None,
    ))

    # --- capture_batch_settlement end-to-end with verification stubbed --
    temp_dir = tempfile.mkdtemp(prefix="x402_batch_test_")
    temp_evidence = os.path.join(temp_dir, "evidence.json")
    import evidence_store
    original_target = evidence_store.DEFAULT_EVIDENCE_PATH
    evidence_store.DEFAULT_EVIDENCE_PATH = temp_evidence

    def fake_verify_transaction(**kwargs):
        return {
            "transactionHash": good_hash,
            "chainId": "0x14a34",
            "verificationStatus": "TRANSACTION_EXISTS",
            "found": True,
            "status": "0x1",
            "from": BATCH_ESCROW_ADDRESS.lower(),
            "to": requirements["payTo"],
            "logs": [],
            "anchorBlock": "0x2c1b071",
        }

    from rpc_verification_adapter import verify_transaction as patched_verify
    # Patch this module's own reference (capture_batch_settlement holds it here).
    original_verify_in_module = globals().get("verify_transaction")
    _ = patched_verify  # noqa: F841
    globals()["verify_transaction"] = fake_verify_transaction

    try:
        result = capture_batch_settlement(
            provider_id="test-batch-provider",
            provider_label="test-batch-provider",
            resource_url="http://127.0.0.1:9999/resource",
            requirements=requirements,
            settlement_claim=claim,
            rpc_url="http://fake-rpc",
        )
    finally:
        evidence_store.DEFAULT_EVIDENCE_PATH = original_target
        globals()["verify_transaction"] = original_verify_in_module

    checks.append((
        "capture outcome is BATCH_CAPTURED_NOT_JUDGED",
        result["outcome"] == "BATCH_CAPTURED_NOT_JUDGED",
    ))
    checks.append((
        "capture marks notJudged and sources",
        result["notJudged"] is True
        and result["evidenceSource"] == "batch_capture"
        and result["claimSource"] == "batch_settlement",
    ))
    checks.append((
        "capture stores evidence when a hash exists",
        result.get("evidenceStoreStatus") == "EVIDENCE_STORED",
    ))
    checks.append((
        "capture records on-chain verification facts",
        result.get("verificationStatus") == "TRANSACTION_EXISTS"
        and result["verificationRecord"]["from"] == BATCH_ESCROW_ADDRESS.lower(),
    ))
    checks.append((
        "capture records no verdict",
        "auditVerdict" not in result and "auditRecord" not in result,
    ))

    # --- keyless capture is not stored ----------------------------------
    claim_keyless = {"success": True, "amount": ""}
    globals()["verify_transaction"] = fake_verify_transaction
    evidence_store.DEFAULT_EVIDENCE_PATH = temp_evidence
    try:
        result2 = capture_batch_settlement(
            provider_id="test-batch-provider",
            provider_label="test-batch-provider",
            resource_url="http://127.0.0.1:9999/resource",
            requirements=requirements,
            settlement_claim=claim_keyless,
            rpc_url="http://fake-rpc",
        )
    finally:
        evidence_store.DEFAULT_EVIDENCE_PATH = original_target
        globals()["verify_transaction"] = original_verify_in_module
    checks.append((
        "keyless capture is not stored",
        result2["outcome"] == "BATCH_CAPTURED_NOT_JUDGED"
        and "evidenceStoreStatus" not in result2,
    ))

    failures = 0
    for description, passed in checks:
        print(description + ": " + ("True" if passed else "FAILED"))
        if not passed:
            failures = failures + 1
    print("")
    if failures == 0:
        print("All " + str(len(checks)) + " checks passed.")
    else:
        print(str(failures) + " of " + str(len(checks)) + " checks FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    _run_self_test()