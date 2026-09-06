"""Top-level pipeline: run an x402 payment, verify on-chain, build a claim, and submit audit evidence."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genlayer_connection import get_client
from contract_callers import (
    submit_x402_audit as send_x402_audit,
    submit_declaration_audit as send_declaration_audit,
)
from provider_registry import get_provider
from x402_payment_client import attempt_payment
from rpc_transfer_scanner import find_settlement_transfer
from settlement_hash_extractor import extract_settlement_hash
from rpc_verification_adapter import verify_transaction
from claim_builder import build_claim
from evidence_store import store_evidence
from batch_evidence_capture import (
    is_batch_scheme as detect_batch_scheme,
    capture_batch_settlement as capture_batch,
)

DEFAULT_RPC_URL = "https://sepolia.base.org"

PAYER_PRIVATE_KEY_ENV_VAR = "PAYER_PRIVATE_KEY"


def _payer_key_from_env() -> str:
    key = os.environ.get(PAYER_PRIVATE_KEY_ENV_VAR)
    if not key:
        raise RuntimeError(
            "PAYER_PRIVATE_KEY was not found in the environment. Add a "
            "line to indexer/.env (the same file that already holds "
            "GENLAYER_PRIVATE_KEY):\n"
            "  PAYER_PRIVATE_KEY=0x....\n"
            "This can be the SAME key as GENLAYER_PRIVATE_KEY if you are "
            "reusing one testnet-only wallet for both GenLayer and Base "
            "Sepolia, which is fine for a wallet that will never hold "
            "real value. It is kept as a separate named variable so the "
            "two roles (signing GenLayer transactions vs signing x402 "
            "payment authorizations) stay legible in the config, even "
            "when the underlying key is identical."
        )
    return key


def submit_x402_audit(
    facilitator_label: str,
    rpc_url: str,
    claim_success: str,
    claim_transaction: str,
    claim_network: str,
    claim_payer: str,
    claim_amount: str,
    claim_valid_before: str,
    requirements: dict,
    claim_source: str,
    anchor_block: str = "",
) -> dict:
    """Call X402Auditor.audit() with the 16-argument signature in the exact
    order the contract expects it, then read back the verdict we just
    wrote via get_verdicts(). Since A8, the write/wait/read-back mechanics
    live in contract_callers.py (send_x402_audit below); this function
    keeps its exact signature and its A7 claim-building step. The pattern
    is still the append-then-read-the-last-one supported_collector.run_probe()
    proved live, since audit() itself returns nothing (it is a write with
    no return value) and the record only becomes visible through the view
    method.

    anchor_block comes from A6 (rpc_verification_adapter.py): the block
    number the transaction was found in, or the current head when the
    transaction was not found yet. The contract uses it to decide whether
    an absence is final or not yet final. It is passed as a string because
    all 16 audit() arguments are strings.
    """
    # A7 (claim_builder.py): build the exact 16-element argument list in
    # the exact order X402Auditor.audit() expects. This replaces the old
    # inline list construction, so the claim structure is validated
    # before any gas is spent on a consensus round.
    claim_result = build_claim(
        facilitator=facilitator_label,
        rpc_url=rpc_url,
        claim_success=claim_success,
        claim_transaction=claim_transaction,
        claim_network=claim_network,
        claim_payer=claim_payer,
        claim_amount=claim_amount,
        claim_valid_before=claim_valid_before,
        requirements=requirements,
        claim_source=claim_source,
        anchor_block=anchor_block,
    )
    if claim_result["status"] != "CLAIM_READY":
        raise RuntimeError(
            "claim_builder refused to build the claim: "
            + claim_result["status"]
            + ": "
            + str(claim_result["reason"])
        )

    # A8 (contract_callers.py): the write/wait/read-back mechanics now
    # live in ONE shared place, contract_callers.send_x402_audit(). This
    # function keeps its exact signature and its A7 claim-building step
    # above; only the send mechanics moved. get_client() is resolved
    # HERE, in this module's namespace, so the A6 and A7 integration
    # tests keep their existing monkeypatch seam and pass unchanged.
    return send_x402_audit(
        claim_result["claim"],
        client=get_client(),
    )


def submit_declaration_audit(
    facilitator_label: str,
    declaration_url: str,
    rpc_url: str,
    transaction_hash: str,
) -> dict:
    """Call DeclarationAudit.audit_declaration(), same append-then-read
    pattern: once we have provider + declaration URL + rpc URL +
    transaction hash, we can check whether the settling address matches
    what the facilitator itself declares.

    Since A8 the write/wait/read-back mechanics live in
    contract_callers.send_declaration_audit(); this function keeps its
    exact signature so the A6 and A7 integration tests pass unchanged.
    """
    # A8 (contract_callers.py): same delegation as submit_x402_audit
    # above. The shared layer now also refuses, before any gas, the one
    # case the contract itself silently no-ops on: empty declaration_url
    # / rpc_url / transaction_hash would make audit_declaration() return
    # early with NOTHING written, and the append-then-read-last pattern
    # would then return the PREVIOUS caller's record. collect_and_audit()
    # already guards both fields before calling here, so the live path
    # behaves exactly as before; the guard now also holds for any future
    # caller of this function.
    return send_declaration_audit(
        facilitator_label=facilitator_label,
        declaration_url=declaration_url,
        rpc_url=rpc_url,
        transaction_hash=transaction_hash,
        client=get_client(),
    )


def collect_and_audit(
    provider_id: str,
    resource_url: str,
    rpc_url: str = DEFAULT_RPC_URL,
    payer_private_key: str = None,
) -> dict:
    """The full A4 pipeline for one payment against one resource:

        1. Execute the payment (x402_payment_client.attempt_payment)
        2. Decide evidence class: header capture, RPC fallback, or nothing
        3. If we have a transaction hash, feed X402Auditor.audit()
        4. If the provider also has a known_declaration_url, feed
           DeclarationAudit.audit_declaration() with the same hash

    Returns a summary dict recording what actually happened at each step,
    even the outcomes that produced no on-chain audit, because a run that
    found nothing is still information the caller should see, not a
    silent no-op.
    """
    provider = get_provider(provider_id)
    if provider is None:
        raise ValueError("provider_id not found in registry: " + provider_id)

    key = payer_private_key or _payer_key_from_env()

    summary = {"providerId": provider_id, "resourceUrl": resource_url}

    payment_result = attempt_payment(resource_url, key)
    summary["initialStatus"] = payment_result["initialStatus"]

    if payment_result["initialStatus"] != 402:
        summary["outcome"] = "NO_PAYMENT_REQUIRED_RECEIVED"
        return summary

    requirements = payment_result["requirements"]
    summary["requirements"] = requirements
    summary["retryStatus"] = payment_result.get("retryStatus")

    # Batch-settlement branch (zero gas, no verdict). The accepted scheme
    # being batch-settlement means this payment settles through the x402
    # escrow contract, NOT payer -> receiver directly. X402Auditor.audit()
    # would false-negative with REJECTED_PAYER_MISMATCH because the
    # transfer sender is the escrow, never the payer, so we deliberately
    # capture the payment as its own un-judged evidence class instead.
    # Never runs audit()/DeclarationAudit for this path.
    if detect_batch_scheme(requirements.get("scheme")):
        batch_summary = capture_batch(
            provider_id=provider_id,
            provider_label=provider.get("label", provider_id),
            resource_url=resource_url,
            requirements=requirements,
            settlement_claim=payment_result.get("settlementClaim"),
            rpc_url=rpc_url,
        )
        batch_summary["initialStatus"] = payment_result["initialStatus"]
        return batch_summary

    claim_transaction = ""
    claim_success = ""
    claim_network = ""
    claim_payer = ""
    claim_amount = ""
    claim_valid_before = ""
    claim_source = ""

    settlement_claim = payment_result.get("settlementClaim")
    if settlement_claim is not None:
        # Path 1: header capture. Report exactly what THEY claimed, not
        # what we independently believe to be true.
        claim_source = "self_probe"

        # A5 (settlement_hash_extractor.py) replaces the old inline
        # "transaction or transactionHash or empty string" lookup. The
        # difference is not cosmetic: this validates the value is
        # actually a well-formed 32-byte hash before it is ever written
        # toward audit(), and if a field IS present but malformed, that
        # fact is kept visible in the summary instead of being folded
        # into the same empty string an absent field would produce.
        hash_extraction = extract_settlement_hash("self_probe", settlement_claim)
        claim_transaction = hash_extraction["hash"] or ""
        summary["settlementHashFieldUsed"] = hash_extraction["fieldUsed"]
        if hash_extraction["rejectedFields"]:
            summary["settlementHashRejectedFields"] = hash_extraction["rejectedFields"]

        raw_success = settlement_claim.get("success")
        if raw_success is True:
            claim_success = "true"
        elif raw_success is False:
            claim_success = "false"
        claim_network = str(settlement_claim.get("network", ""))
        claim_payer = str(settlement_claim.get("payer", ""))
        summary["evidenceSource"] = "header_capture"
    else:
        # Path 2: RPC fallback. We know nothing the facilitator told us;
        # we only know what we can find ourselves.
        found_hash, all_matches = find_settlement_transfer(
            rpc_url=rpc_url,
            asset_address=requirements["asset"],
            payer_address=payment_result["payerAddress"],
            pay_to_address=requirements["payTo"],
        )
        summary["rpcFallbackMatches"] = all_matches
        if found_hash is None:
            summary["outcome"] = "PENDING_NO_EVIDENCE_YET"
            summary["evidenceSource"] = "none"
            return summary

        # Same A5 validation path, applied here as a second, independent
        # check on rpc_transfer_scanner's own output. find_settlement_transfer()
        # is only ever supposed to return None or an already-valid
        # lowercase hash; this is a deliberate defense-in-depth check on
        # that assumption, not a duplicate of it.
        hash_extraction = extract_settlement_hash("discovered_only", found_hash)
        if hash_extraction["hash"] is None:
            raise RuntimeError(
                "rpc_transfer_scanner.find_settlement_transfer() returned "
                "a non-empty value that settlement_hash_extractor could "
                "not validate as a transaction hash: " + repr(found_hash)
            )

        claim_source = "discovered_only"
        claim_transaction = hash_extraction["hash"]
        summary["evidenceSource"] = "rpc_fallback"

    if claim_valid_before == "":
        claim_valid_before = str(payment_result.get("signedValidBefore", ""))

    # A6 (rpc_verification_adapter.py): verify the transaction hash
    # on-chain and produce the structured evidence record, including the
    # anchor block the contract needs. This is verification, not a
    # verdict: the contract remains the only authority that names a
    # verdict, and we pass the anchor block through so the contract can
    # decide finality itself.
    anchor_block = ""
    verification_record = None
    if claim_transaction:
        verification_record = verify_transaction(
            rpc_url=rpc_url,
            transaction_hash=claim_transaction,
            expected_network=requirements["network"],
            requirements=requirements,
        )
        summary["verificationStatus"] = verification_record.get("verificationStatus")
        summary["verificationRecord"] = verification_record
        anchor_block = verification_record.get("anchorBlock") or ""

    audit_record = submit_x402_audit(
        facilitator_label=provider["label"],
        rpc_url=rpc_url,
        claim_success=claim_success,
        claim_transaction=claim_transaction,
        claim_network=claim_network,
        claim_payer=claim_payer,
        claim_amount=claim_amount,
        claim_valid_before=claim_valid_before,
        requirements=requirements,
        claim_source=claim_source,
        anchor_block=anchor_block,
    )
    summary["auditVerdict"] = audit_record.get("verdict")
    summary["auditRecord"] = audit_record

    declaration_url = provider.get("known_declaration_url")
    if declaration_url and claim_transaction:
        declaration_record = submit_declaration_audit(
            facilitator_label=provider["label"],
            declaration_url=declaration_url,
            rpc_url=rpc_url,
            transaction_hash=claim_transaction,
        )
        summary["declarationVerdict"] = declaration_record.get("verdict")
        summary["declarationRecord"] = declaration_record

    # A9 (evidence_store.py): persist the full summary under its natural
    # key, unless the same semantic evidence already exists. Keyless runs
    # (no transaction hash) are not stored: a keyless record can neither
    # be deduplicated nor attributed, so surfacing those stays the
    # caller's job until the scheduler needs them.
    evidence_store_result = None
    if claim_transaction:
        evidence_store_result = store_evidence(summary)
        summary["evidenceStoreStatus"] = evidence_store_result.get("status")

    summary["outcome"] = "AUDITED"
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python3 http_evidence_collector.py <provider_id> "
            "<resource_url>"
        )
        print(
            "Example: python3 http_evidence_collector.py x402org-public "
            "https://x402.org/facilitator/some-paid-resource"
        )
        sys.exit(1)

    provider_id_arg = sys.argv[1]
    resource_url_arg = sys.argv[2]

    print("Running x402 payment + audit against " + resource_url_arg + " ...")
    outcome_summary = collect_and_audit(provider_id_arg, resource_url_arg)
    print(json.dumps(outcome_summary, indent=2, ensure_ascii=False))
