"""Offline EIP-712 signing of EIP-3009 TransferWithAuthorization for x402 payments."""

import secrets
import time

from eth_account import Account


# EIP-712 domain fields for the EIP-3009 TransferWithAuthorization type.
# Fixed by the standard, not by this project.
AUTHORIZATION_TYPES = {
    "TransferWithAuthorization": [
        {"name": "from", "type": "address"},
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"},
        {"name": "nonce", "type": "bytes32"},
    ],
}

# Same table as NETWORK_CHAIN_IDS in x402_auditor_v8.py and
# declaration_audit.py, duplicated deliberately rather than imported, since
# this file must stay import-free of the GenLayer contracts (it runs
# entirely off-chain, before anything ever reaches GenLayer).
NETWORK_CHAIN_IDS = {
    "base": 8453,
    "eip155:8453": 8453,
    "base-sepolia": 84532,
    "eip155:84532": 84532,
    "polygon": 137,
    "eip155:137": 137,
    "avalanche": 43114,
    "eip155:43114": 43114,
}


def chain_id_for_network(network_label: str) -> int:
    """Resolve a network label to a chain id. Raises, does not guess, on an
    unknown label, because signing an authorization under the wrong chain id
    produces a signature that is silently invalid nowhere useful to us."""
    key = network_label.strip().lower()
    if key not in NETWORK_CHAIN_IDS:
        raise ValueError(
            "unknown network label for signing, refusing to guess a chain "
            "id: " + network_label
        )
    return NETWORK_CHAIN_IDS[key]


def random_nonce_hex() -> str:
    """A fresh 32-byte nonce per authorization. EIP-3009 nonces are
    arbitrary bytes32 values chosen by the signer, not a sequential counter,
    so a cryptographically random value is the correct choice, not a bug
    waiting to collide."""
    return "0x" + secrets.token_hex(32)


def build_authorization(
    payer_address: str,
    pay_to: str,
    value: int,
    max_timeout_seconds: int,
    valid_after_skew_seconds: int = 60,
) -> dict:
    """Build the plain (unsigned) authorization fields.

    validAfter is set slightly in the past (default 60s skew) to tolerate
    clock drift between this machine and whatever eventually checks the
    authorization on chain. validBefore is derived from the facilitator's
    own declared maxTimeoutSeconds from PaymentRequirements, never a value
    invented by this client, because overrunning the facilitator's own
    stated window would make settlement fail for a reason we caused.
    """
    now = int(time.time())
    return {
        "from": payer_address,
        "to": pay_to,
        "value": int(value),
        "validAfter": max(0, now - valid_after_skew_seconds),
        "validBefore": now + int(max_timeout_seconds),
        "nonce": random_nonce_hex(),
    }


def sign_authorization(
    private_key: str,
    network_label: str,
    verifying_contract: str,
    token_name: str,
    token_version: str,
    authorization: dict,
) -> str:
    """Sign an EIP-3009 TransferWithAuthorization message. Returns the
    signature as a 0x-prefixed hex string.

    token_name and token_version MUST come from the facilitator's own
    PaymentRequirements.extra field (e.g. {"name":"USDC","version":"2"}
    captured live in handoff section 14), never hardcoded here, because a
    wrong EIP-712 domain produces a signature that recovers to a different
    address than intended and simply fails verification with no useful
    error message pointing back at the domain mismatch.
    """
    chain_id = chain_id_for_network(network_label)

    domain = {
        "name": token_name,
        "version": token_version,
        "chainId": chain_id,
        "verifyingContract": verifying_contract,
    }

    message = {
        "from": authorization["from"],
        "to": authorization["to"],
        "value": authorization["value"],
        "validAfter": authorization["validAfter"],
        "validBefore": authorization["validBefore"],
        "nonce": authorization["nonce"],
    }

    signed = Account.sign_typed_data(
        private_key,
        domain_data=domain,
        message_types=AUTHORIZATION_TYPES,
        message_data=message,
    )
    return "0x" + signed.signature.hex().replace("0x", "")


def payer_address_from_key(private_key: str) -> str:
    """Derive the checksum address for a private key, so callers never have
    to keep the address in sync with the key by hand."""
    return Account.from_key(private_key).address
