"""Verify settlement transaction hashes on-chain via JSON-RPC, returning contract-mirrored verification statuses."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests

from settlement_hash_extractor import normalize_tx_hash

TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

EIP3009_SELECTOR = "0xe3ee160e"

REQUEST_TIMEOUT_SECONDS = 30

# Base Sepolia block time measured live in handoff section 20.
DEFAULT_BLOCK_TIME_SECONDS = 2

NETWORK_CHAIN_IDS = {
    "eip155:84532": "0x14a34",
    "base-sepolia": "0x14a34",
    "eip155:8453": "0x2105",
    "base": "0x2105",
}


def _rpc_call(rpc_url: str, method: str, params: list):
    """One JSON-RPC call. Same shape as rpc_transfer_scanner._rpc_call:
    raises on genuine RPC failure (connection, malformed response, error
    field), never on "not found yet", since "not found yet" is an
    ordinary, expected outcome while a settlement is still pending, not
    an infrastructure fault."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    )
    response = requests.post(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    parsed = response.json()
    if "error" in parsed and parsed["error"] is not None:
        raise RuntimeError("rpc_error: " + json.dumps(parsed["error"]))
    if "result" not in parsed:
        raise RuntimeError("rpc_no_result_field")
    return parsed["result"]


def _address_topic(address: str) -> str:
    """Left-pad a 20-byte address into a 32-byte topic, matching how
    Transfer indexes from/to. Same padding scheme rpc_transfer_scanner
    and X402Auditor's own decode_transfer() use."""
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    return "0x" + clean.rjust(64, "0")


def _chain_id_of_network(network_label: str):
    """Map a CAIP-2 network label to the chain id the contract itself knows.
    Returns None for an unknown label."""
    if not isinstance(network_label, str):
        return None
    label = network_label.strip().lower()
    return NETWORK_CHAIN_IDS.get(label)


def _fetch_transaction(rpc_url: str, transaction_hash: str):
    """eth_getTransactionByHash. Returns the raw transaction dict, or
    None if the RPC returned null (transaction does not exist)."""
    return _rpc_call(rpc_url, "eth_getTransactionByHash", [transaction_hash])


def _fetch_receipt(rpc_url: str, transaction_hash: str):
    """eth_getTransactionReceipt. Returns the raw receipt dict, or None
    if the RPC returned null (no receipt yet, transaction still pending
    or does not exist)."""
    return _rpc_call(rpc_url, "eth_getTransactionReceipt", [transaction_hash])


def _fetch_block(rpc_url: str, block_hex: str):
    """eth_getBlockByNumber with full transaction objects = False. We
    only need the timestamp, not the transactions."""
    return _rpc_call(rpc_url, "eth_getBlockByNumber", [block_hex, False])


def _decode_transfer(logs: list, asset_address: str = None) -> dict:
    """Find the first Transfer event in a transaction's logs and decode
    from/to/value. Mirrors X402Auditor's decode_transfer() logic: the
    Transfer event has topic0 = TRANSFER_TOPIC0, and the from/to are
    indexed as 32-byte topics that need the leading 24 bytes stripped.

    If asset_address is given, only logs emitted by that contract are
    considered, because a Transfer of the wrong token is not the
    settlement we are verifying.

    Returns {"found": True, "from": ..., "to": ..., "value": ...} or
    {"found": False}."""
    if not logs:
        return {"found": False}

    expected_asset = asset_address.lower() if asset_address else None

    for entry in logs:
        topics = entry.get("topics") or []
        if len(topics) < 3:
            continue
        if topics[0].lower() != TRANSFER_TOPIC0:
            continue

        # If an asset address was given, the log must come from that
        # contract. A Transfer of a different token is not the
        # settlement we are verifying.
        log_address = str(entry.get("address", "")).lower()
        if expected_asset and log_address and log_address != expected_asset:
            continue

        # topics[1] is the 32-byte padded sender, topics[2] the padded
        # receiver. Strip the leading 24 bytes (12 hex chars after 0x).
        raw_from = topics[1]
        raw_to = topics[2]
        if raw_from.startswith("0x") and len(raw_from) == 66:
            from_addr = "0x" + raw_from[26:]
        else:
            from_addr = raw_from
        if raw_to.startswith("0x") and len(raw_to) == 66:
            to_addr = "0x" + raw_to[26:]
        else:
            to_addr = raw_to

        # data is the 32-byte big-endian value
        data = entry.get("data") or "0x"
        try:
            value = int(data, 16)
        except (ValueError, TypeError):
            value = None

        return {
            "found": True,
            "from": from_addr.lower(),
            "to": to_addr.lower(),
            "value": value,
        }

    return {"found": False}


def _decode_authorization(tx_input: str) -> dict:
    """Check whether the transaction's input data starts with the
    EIP-3009 transferWithAuthorization selector (handoff section 14:
    0xe3ee160e). This is evidence, not a full decode: the contract does
    the full authorization decode (Keccak256, ecrecover, nonce check)
    inside GenLayer. This file only records whether the selector is
    present, so A7 can decide whether to pass the transaction to the
    contract as a potential EIP-3009 settlement."""
    if not isinstance(tx_input, str) or tx_input == "":
        return {"found": False, "selector": None, "hasEIP3009Selector": False}

    selector = tx_input[:10].lower()
    return {
        "found": True,
        "selector": selector,
        "hasEIP3009Selector": selector == EIP3009_SELECTOR,
    }


def verify_transaction(
    rpc_url: str,
    transaction_hash: str,
    expected_network: str,
    requirements: dict = None,
) -> dict:
    """Verify one settlement transaction hash on-chain and produce a
    structured evidence record.

    Args:
        rpc_url: JSON-RPC endpoint (e.g. https://sepolia.base.org)
        transaction_hash: normalized 0x-prefixed 32-byte hash from A5
        expected_network: CAIP-2 network label (e.g. "eip155:84532")
        requirements: payment requirements dict (scheme, network, payTo,
            asset, maxAmountRequired) the hash is supposed to satisfy

    Returns a dict with:
        transactionHash, chainId, chainIdMatches, networkLabel,
        transactionFound, receiptStatus, blockNumber, blockTimestamp,
        anchorBlock, transfer, authorization, verificationStatus, reason

    verificationStatus is one of the statuses listed in the module
    docstring. These are verification statuses, NOT verdicts. The final
    verdict is exclusively X402Auditor's job inside GenLayer.
    """
    normalized = normalize_tx_hash(transaction_hash)
    if normalized is None:
        return {
            "transactionHash": None,
            "verificationStatus": "UNDETERMINED_MALFORMED_HASH",
            "reason": (
                "transaction_hash did not normalize to a valid 32-byte "
                "hash"
            ),
        }

    record = {
        "transactionHash": normalized,
        "chainId": None,
        "chainIdMatches": False,
        "networkLabel": expected_network,
        "transactionFound": False,
        "receiptStatus": None,
        "blockNumber": None,
        "blockTimestamp": None,
        "anchorBlock": None,
        "transfer": {"found": False},
        "authorization": {"found": False},
        "verificationStatus": None,
        "reason": None,
    }

    # 1. Chain id check. An unknown network label is our problem, not
    # the facilitator's (contract's UNDETERMINED_UNKNOWN_NETWORK_LABEL).
    expected_chain_id = _chain_id_of_network(expected_network)
    if expected_chain_id is None:
        record["verificationStatus"] = "UNDETERMINED_UNKNOWN_NETWORK_LABEL"
        record["reason"] = "unknown network label: " + repr(expected_network)
        return record

    try:
        chain_id = _rpc_call(rpc_url, "eth_chainId", [])
    except Exception as exc:
        record["verificationStatus"] = "UNDETERMINED_RPC_UNREADABLE"
        record["reason"] = "eth_chainId failed: " + str(exc)
        return record

    record["chainId"] = chain_id
    record["chainIdMatches"] = str(chain_id).lower() == str(expected_chain_id).lower()
    if not record["chainIdMatches"]:
        record["verificationStatus"] = "UNDETERMINED_CHAIN_MISMATCH"
        record["reason"] = (
            "chain id " + str(chain_id) + " does not match expected "
            + str(expected_chain_id) + " for " + expected_network
        )
        return record

    # 2. Transaction existence.
    try:
        tx = _fetch_transaction(rpc_url, normalized)
    except Exception as exc:
        record["verificationStatus"] = "UNDETERMINED_RPC_UNREADABLE"
        record["reason"] = "eth_getTransactionByHash failed: " + str(exc)
        return record

    if tx is None:
        # Transaction does not exist. Whether this absence is final is
        # the contract's decision using validBefore and the anchor block.
        # A6 records the current head as the anchor and marks the absence
        # as PENDING (not final), because finality is not ours to declare.
        try:
            latest_hex = _rpc_call(rpc_url, "eth_blockNumber", [])
            block = _fetch_block(rpc_url, latest_hex)
            record["blockNumber"] = latest_hex
            record["blockTimestamp"] = int(block.get("timestamp", "0x0"), 16)
            record["anchorBlock"] = latest_hex
        except Exception as exc:
            record["verificationStatus"] = "UNDETERMINED_RPC_UNREADABLE"
            record["reason"] = (
                "eth_blockNumber/eth_getBlockByNumber failed: " + str(exc)
            )
            return record

        record["verificationStatus"] = "PENDING_TRANSACTION_NOT_FOUND"
        record["reason"] = (
            "transaction not found on chain; absence not yet final"
        )
        return record

    record["transactionFound"] = True
    record["blockNumber"] = tx.get("blockNumber")
    record["anchorBlock"] = tx.get("blockNumber")

    # 3. Receipt status.
    try:
        receipt = _fetch_receipt(rpc_url, normalized)
    except Exception as exc:
        record["verificationStatus"] = "UNDETERMINED_RPC_UNREADABLE"
        record["reason"] = "eth_getTransactionReceipt failed: " + str(exc)
        return record

    if receipt is None:
        record["verificationStatus"] = "PENDING_TRANSACTION_NOT_FOUND"
        record["reason"] = (
            "no receipt yet; transaction may still be pending"
        )
        return record

    record["receiptStatus"] = receipt.get("status")
    if str(receipt.get("status", "")).lower() == "0x0":
        record["verificationStatus"] = "REJECTED_TRANSACTION_REVERTED"
        record["reason"] = "receipt status 0x0: transaction reverted"
        return record

    # 4. Block timestamp. Nice-to-have evidence, not a blocker: the
    # contract reads the block itself inside GenLayer. This is only for
    # the off-chain record.
    if record["blockNumber"] is not None:
        try:
            block = _fetch_block(rpc_url, record["blockNumber"])
            record["blockTimestamp"] = int(block.get("timestamp", "0x0"), 16)
        except Exception:
            record["blockTimestamp"] = None

    # 5. Transfer event. If requirements specify an asset, only logs
    # from that contract count.
    logs = receipt.get("logs") or []
    asset_address = requirements.get("asset") if requirements else None
    transfer = _decode_transfer(logs, asset_address)
    record["transfer"] = transfer

    if not transfer["found"]:
        record["verificationStatus"] = "REJECTED_NO_TRANSFER_EVENT"
        record["reason"] = (
            "no Transfer event found in transaction logs"
            + (" from required asset " + asset_address if asset_address else "")
        )
        return record

    # 6. Transfer matches requirements. A6 checks the two fields it can
    # check from the requirements alone: payee and (for scheme ==
    # "exact") amount. Payer matching and scheme rules beyond "exact"
    # are the contract's job, because they depend on the claim, not just
    # the requirements.
    if requirements is not None:
        pay_to = str(requirements.get("payTo", "")).lower()
        if pay_to and transfer["to"] != pay_to:
            record["verificationStatus"] = "REJECTED_TRANSFER_PAYEE_MISMATCH"
            record["reason"] = (
                "transfer payee " + transfer["to"] + " does not match "
                "required payee " + pay_to
            )
            return record

        scheme = str(requirements.get("scheme", ""))
        if scheme == "exact":
            max_amount = requirements.get("maxAmountRequired")
            if max_amount is not None and transfer["value"] is not None:
                try:
                    required = int(max_amount)
                except (ValueError, TypeError):
                    required = None
                if required is not None and transfer["value"] != required:
                    record["verificationStatus"] = (
                        "REJECTED_TRANSFER_AMOUNT_MISMATCH"
                    )
                    record["reason"] = (
                        "transfer amount " + str(transfer["value"]) + " does "
                        "not match exact required amount " + str(required)
                    )
                    return record

    # 7. Authorization selector. Evidence only: the full EIP-3009
    # decode (Keccak256, ecrecover, nonce check) is the contract's job.
    tx_input = tx.get("input") or "0x"
    record["authorization"] = _decode_authorization(tx_input)

    record["verificationStatus"] = "VERIFIED"
    record["reason"] = None
    return record


def _run_self_test() -> None:
    """Offline checks for the logic that does not need a live RPC
    endpoint: network label mapping, Transfer decoding from a synthetic
    log entry, authorization selector detection, and the malformed-hash
    guard. The live RPC path cannot be self-tested offline; it is
    exercised by running http_evidence_collector.py against a real
    facilitator, exactly like A4 and A5 before it."""
    checks = []

    # Network label mapping
    checks.append((
        '_chain_id_of_network maps eip155:84532 to 0x14a34',
        _chain_id_of_network("eip155:84532") == "0x14a34",
    ))
    checks.append((
        '_chain_id_of_network maps eip155:8453 to 0x2105',
        _chain_id_of_network("eip155:8453") == "0x2105",
    ))
    checks.append((
        '_chain_id_of_network maps v1 flat label base-sepolia to 0x14a34',
        _chain_id_of_network("base-sepolia") == "0x14a34",
    ))
    checks.append((
        '_chain_id_of_network maps v1 flat label base to 0x2105',
        _chain_id_of_network("base") == "0x2105",
    ))
    checks.append((
        '_chain_id_of_network returns None for unknown label',
        _chain_id_of_network("near:mainnet") is None,
    ))

    # Transfer decoding
    payer = "0x1d8757aae49cb66adf814ccf26658a3e31a20aa1"
    payee = "0x572bb4287aacbd42d647d611459e996ec9c89c52"
    logs = [{
        "address": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "topics": [
            TRANSFER_TOPIC0,
            _address_topic(payer),
            _address_topic(payee),
        ],
        "data": "0x" + format(10000, "x").rjust(64, "0"),
    }]
    transfer = _decode_transfer(logs)
    checks.append((
        '_decode_transfer extracts from/to/value from a Transfer log',
        transfer["found"]
        and transfer["from"] == payer
        and transfer["to"] == payee
        and transfer["value"] == 10000,
    ))

    # Transfer decoding with asset filter: wrong asset is skipped
    wrong_asset_logs = [{
        "address": "0x1111111111111111111111111111111111111111",
        "topics": [
            TRANSFER_TOPIC0,
            _address_topic(payer),
            _address_topic(payee),
        ],
        "data": "0x" + format(10000, "x").rjust(64, "0"),
    }]
    transfer_wrong_asset = _decode_transfer(
        wrong_asset_logs, "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
    )
    checks.append((
        '_decode_transfer skips logs from a different asset contract',
        not transfer_wrong_asset["found"],
    ))

    # Authorization selector
    auth = _decode_authorization("0xe3ee160e" + "00" * 100)
    checks.append((
        '_decode_authorization detects EIP-3009 selector',
        auth["found"] and auth["hasEIP3009Selector"],
    ))
    auth_other = _decode_authorization("0x82ad56cb" + "00" * 100)
    checks.append((
        '_decode_authorization does not flag a different selector',
        auth_other["found"] and not auth_other["hasEIP3009Selector"],
    ))
    auth_empty = _decode_authorization("")
    checks.append((
        '_decode_authorization handles empty input',
        not auth_empty["found"],
    ))

    # Malformed hash guard: must reject without any RPC call
    result = verify_transaction(
        rpc_url="http://unused.invalid",
        transaction_hash="0x" + "a" * 63,
        expected_network="eip155:84532",
    )
    checks.append((
        'verify_transaction rejects a malformed hash without any RPC call',
        result["verificationStatus"] == "UNDETERMINED_MALFORMED_HASH",
    ))

    # Unknown network label guard: must reject without any RPC call
    result_unknown_net = verify_transaction(
        rpc_url="http://unused.invalid",
        transaction_hash="0x" + "ab" * 32,
        expected_network="near:mainnet",
    )
    checks.append((
        'verify_transaction rejects an unknown network label without any RPC call',
        result_unknown_net["verificationStatus"] == "UNDETERMINED_UNKNOWN_NETWORK_LABEL",
    ))

    failures = 0
    for description, passed in checks:
        status = "True" if passed else "FAILED"
        print(description + ": " + status)
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