"""Fallback eth_getLogs scan to find settlement Transfer events when the response header is missing."""

import json

import requests

TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

REQUEST_TIMEOUT_SECONDS = 30

# Base Sepolia block time measured live.
DEFAULT_LOOKBACK_SECONDS = 600
DEFAULT_BLOCK_TIME_SECONDS = 2


def _address_topic(address: str) -> str:
    """Left-pad a 20-byte address into a 32-byte topic, matching how
    Transfer indexes from/to. Same padding scheme X402Auditor's own
    decode_transfer() expects on the way back out."""
    clean = address.lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    return "0x" + clean.rjust(64, "0")


def _rpc_call(rpc_url: str, method: str, params: list):
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


def find_settlement_transfer(
    rpc_url: str,
    asset_address: str,
    payer_address: str,
    pay_to_address: str,
    lookback_seconds: int = DEFAULT_LOOKBACK_SECONDS,
    block_time_seconds: int = DEFAULT_BLOCK_TIME_SECONDS,
):
    """Scan recent blocks for a Transfer(payer -> payTo) on asset_address.

    Returns the transaction hash (str) of the newest matching transfer, or
    None if nothing matched in the lookback window. Raises on genuine RPC
    failure (connection, malformed response), never on "not found yet",
    since "not found yet" is an ordinary, expected outcome while a
    settlement is still pending, not an infrastructure fault.

    If MULTIPLE matching transfers exist in the window (the same payer has
    paid the same payee the same asset more than once recently), the
    newest one is returned and the full list is available in the second
    return value for the caller to record rather than silently discard.
    """
    latest_hex = _rpc_call(rpc_url, "eth_blockNumber", [])
    latest_block = int(latest_hex, 16)

    blocks_back = max(1, lookback_seconds // block_time_seconds)
    from_block = max(0, latest_block - blocks_back)

    logs = _rpc_call(
        rpc_url,
        "eth_getLogs",
        [
            {
                "address": asset_address,
                "topics": [
                    TRANSFER_TOPIC0,
                    _address_topic(payer_address),
                    _address_topic(pay_to_address),
                ],
                "fromBlock": "0x" + format(from_block, "x"),
                "toBlock": "0x" + format(latest_block, "x"),
            }
        ],
    )

    if not logs:
        return (None, [])

    tx_hashes = []
    for entry in logs:
        tx_hash = str(entry.get("transactionHash", "")).lower()
        if tx_hash != "" and tx_hash not in tx_hashes:
            tx_hashes.append(tx_hash)

    if len(tx_hashes) == 0:
        return (None, [])

    # Logs come back in ascending block order from eth_getLogs on every RPC
    # implementation checked; the newest is therefore last.
    return (tx_hashes[-1], tx_hashes)
