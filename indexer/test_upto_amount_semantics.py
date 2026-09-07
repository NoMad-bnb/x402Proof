"""Offline integration test for the contract's per-scheme amount semantics.

Verifies that:
- "exact" requires exact equality
- "upto" confirms at or under the cap, rejects at zero or over the cap
- amount_acceptable() and judge() produce the right verdicts in both schemes
"""

import importlib.util
import json
import os
import sys
import types

# Portable/embeddable Python does not add the running script's own
# folder to sys.path; without this, sibling imports would fail.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONTRACT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, "x402Proof", "x402_auditor_v8.py",
))


class _Meta(type):
    def __getattr__(cls, name):
        return _Stub


class _Stub(metaclass=_Meta):
    """Universal do-nothing stand-in for anything imported from genlayer."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Stub()

    def __getattr__(self, name):
        return _Stub

    def __getitem__(self, key):
        return _Stub()


def _install_genlayer_stub():
    """Register a stub 'genlayer' module so the contract file imports.

    Only runs if the real SDK is absent; if genlayer ever becomes
    importable here, the real one wins and the stub is skipped.
    """
    try:
        import genlayer  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("genlayer")
    stub.gl = _Stub()
    for name in ("Address", "u256", "DynArray"):
        setattr(stub, name, _Stub)
    sys.modules["genlayer"] = stub


def _load_contract_module():
    _install_genlayer_stub()
    spec = importlib.util.spec_from_file_location(
        "x402_auditor_v8_offline_test", CONTRACT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load_contract_module()

GOOD_HASH = "0x" + "ab" * 32
PAYER = "0x1d8757aae49cb66adf814ccf26658a3e31a20aa1"
PAYEE = "0x572bb4287aacbd42d647d611459e996ec9c89c52"
ASSET = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
CHAIN_ID_HEX = "0x14a34"  # Base Sepolia = 84532, matches NETWORK_CHAIN_IDS
NETWORK_LABEL = "eip155:84532"
VALID_BEFORE = 1788264386 + 300
TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)


class _FakeKeccak:
    """Deterministic Keccak256 stand-in for selector_hex()."""

    def __init__(self, data):
        pass

    def hexdigest(self):
        return "cd" * 32


def _address_topic(address: str) -> str:
    return "0x" + address[2:].rjust(64, "0")


def _word_address(address: str) -> str:
    return address[2:].rjust(64, "0")


def _word_uint(value: int) -> str:
    return format(value, "x").rjust(64, "0")


def fake_decode_transfer(log: dict) -> dict:
    """Local stand-in for the contract's gl.evm.decode-based decoder.

    Parses exactly the topic/data shape this test generates, nothing
    more. The real decoder is verified in the studio; this patch exists
    only so judge() can run end-to-end offline.
    """
    topics = log.get("topics") or []
    if len(topics) < 3 or topics[0] != TRANSFER_TOPIC0:
        return {}
    return {
        "token": str(log.get("address", "")).lower(),
        "from": "0x" + topics[1].lower()[-40:],
        "to": "0x" + topics[2].lower()[-40:],
        "amount": int(log.get("data", "0x0"), 16),
    }


def build_auth_input(value: int, selector_hex_value: str) -> str:
    """EIP-3009 transferWithAuthorization calldata, split-v form.

    The six static words: from, to, value, validAfter, validBefore,
    nonce. The selector prefix is supplied by the caller and must be
    derived from the SAME patched Keccak256 the contract will use, so
    the comparison inside decode_authorization() is consistent.
    """
    body = (
        _word_address(PAYER)
        + _word_address(PAYEE)
        + _word_uint(value)
        + _word_uint(0)
        + _word_uint(VALID_BEFORE)
        + "11" * 32
    )
    return "0x" + selector_hex_value + body


def build_evidence(tx_input: str, transfer_value: int) -> str:
    """One successful settlement receipt with one matching Transfer."""
    return json.dumps({
        "chainIdHex": CHAIN_ID_HEX,
        "transactionHash": GOOD_HASH,
        "anchorRequested": False,
        "anchorBlockNumber": None,
        "anchorFound": False,
        "anchorTimestamp": None,
        "found": True,
        "blockNumber": "0x2c1b071",
        "status": "0x1",
        "from": PAYER,
        "to": ASSET,
        "logs": [{
            "address": ASSET,
            "topics": [
                TRANSFER_TOPIC0,
                _address_topic(PAYER),
                _address_topic(PAYEE),
            ],
            "data": "0x" + _word_uint(transfer_value),
        }],
        "txTo": ASSET,
        "txInput": tx_input,
        "settlementTimestamp": 1788264386,
    }, sort_keys=True, separators=(",", ":"))


def make_claim(amount: int) -> dict:
    return {
        "source": "self_probe",
        "success": "true",
        "transaction": GOOD_HASH,
        "network": NETWORK_LABEL,
        "payer": PAYER,
        "amount": str(amount),
        "errorReason": "",
        "validBefore": str(VALID_BEFORE),
    }


def make_req(scheme: str, max_amount_required: int) -> dict:
    return {
        "scheme": scheme,
        "network": NETWORK_LABEL,
        "payTo": PAYEE,
        "asset": ASSET,
        "maxAmountRequired": str(max_amount_required),
    }


def run_judge_scenario(scheme, required, settled_value, claim_amount):
    """Run judge() once with the local patches applied and restored.

    The Keccak256 patch must be applied BEFORE selector_hex() runs,
    because decode_authorization() keccaks the accepted method
    signatures regardless of which path it takes afterwards.
    """
    original_decode = mod.decode_transfer
    # Keccak256 is referenced inside selector_hex()/decode_authorization()
    # but only exists on the module after a real SDK star-import; with the
    # stub it is absent entirely, so save/restore must tolerate that.
    had_original_keccak = hasattr(mod, "Keccak256")
    original_keccak = getattr(mod, "Keccak256", None)
    mod.decode_transfer = fake_decode_transfer
    mod.Keccak256 = _FakeKeccak
    try:
        selector = mod.selector_hex("probe-signature-for-offline-test")
        evidence_json = build_evidence(
            tx_input=build_auth_input(settled_value, selector[2:]),
            transfer_value=settled_value,
        )
        return mod.judge(
            evidence_json,
            "upto-test-facilitator",
            make_claim(claim_amount),
            make_req(scheme, required),
        )
    finally:
        mod.decode_transfer = original_decode
        if had_original_keccak:
            mod.Keccak256 = original_keccak
        else:
            del mod.Keccak256


def _run_self_test() -> None:
    checks = []

    # --- Part 1: amount_acceptable() directly ---------------------------
    checks.append((
        "UPTO_SCHEMES is exactly [upto, permit2-upto]",
        mod.UPTO_SCHEMES == ["upto", "permit2-upto"],
    ))
    checks.append((
        "exact accepts the exact required amount",
        mod.amount_acceptable("exact", 100, 100) is True,
    ))
    checks.append((
        "exact rejects one unit above the required amount",
        mod.amount_acceptable("exact", 101, 100) is False,
    ))
    checks.append((
        "exact rejects one unit below the required amount",
        mod.amount_acceptable("exact", 99, 100) is False,
    ))
    checks.append((
        "exact rejects zero paid",
        mod.amount_acceptable("exact", 0, 100) is False,
    ))
    checks.append((
        "exact rejects an unreadable required amount",
        mod.amount_acceptable("exact", 100, None) is False,
    ))
    checks.append((
        "upto accepts a settlement under the cap",
        mod.amount_acceptable("upto", 50, 100) is True,
    ))
    checks.append((
        "upto accepts a settlement at the cap",
        mod.amount_acceptable("upto", 100, 100) is True,
    ))
    checks.append((
        "upto rejects a settlement above the cap",
        mod.amount_acceptable("upto", 101, 100) is False,
    ))
    checks.append((
        "upto rejects a zero settlement",
        mod.amount_acceptable("upto", 0, 100) is False,
    ))
    checks.append((
        "upto rejects an unreadable cap",
        mod.amount_acceptable("upto", 50, None) is False,
    ))
    checks.append((
        "permit2-upto accepts a settlement under the cap",
        mod.amount_acceptable("permit2-upto", 50, 100) is True,
    ))
    checks.append((
        "permit2-upto rejects a settlement above the cap",
        mod.amount_acceptable("permit2-upto", 101, 100) is False,
    ))

    # --- Part 2: judge() end-to-end with local patches ------------------
    record = run_judge_scenario("upto", 100, 50, 50)
    checks.append((
        "judge confirms an upto settlement under the cap",
        record["verdict"] == "CONFIRMED",
    ))
    checks.append((
        "judge records the EIP-3009 proof for the matching upto settlement",
        record.get("x402Proof") == "EIP3009_AUTHORIZATION_VERIFIED"
        and record.get("settledAmount") == 50,
    ))

    record = run_judge_scenario("upto", 100, 100, 100)
    checks.append((
        "judge confirms an upto settlement exactly at the cap",
        record["verdict"] == "CONFIRMED",
    ))

    record = run_judge_scenario("upto", 100, 101, 101)
    checks.append((
        "judge rejects an upto settlement above the cap",
        record["verdict"] == "REJECTED_AMOUNT_DOES_NOT_SATISFY_REQUIREMENT",
    ))

    record = run_judge_scenario("upto", 100, 0, 0)
    checks.append((
        "judge rejects an upto settlement of zero",
        record["verdict"] == "REJECTED_AMOUNT_DOES_NOT_SATISFY_REQUIREMENT",
    ))

    record = run_judge_scenario("permit2-upto", 100, 50, 50)
    checks.append((
        "judge confirms a permit2-upto settlement under the cap",
        record["verdict"] == "CONFIRMED",
    ))

    record = run_judge_scenario("exact", 100, 99, 99)
    checks.append((
        "judge still rejects an exact settlement below the requirement",
        record["verdict"] == "REJECTED_AMOUNT_DOES_NOT_SATISFY_REQUIREMENT",
    ))

    record = run_judge_scenario("exact", 100, 100, 100)
    checks.append((
        "judge confirms an exact settlement of exactly the requirement",
        record["verdict"] == "CONFIRMED",
    ))

    # Upto payment under the cap, but the calldata selector does not
    # match either accepted transferWithAuthorization form, so the
    # payment stays real-but-unproven: TRANSFER_EVENT_ONLY, CONFIRMED.
    evidence_json = build_evidence(
        tx_input=build_auth_input(50, "ffff0001"),
        transfer_value=50,
    )
    original_decode = mod.decode_transfer
    had_original_keccak = hasattr(mod, "Keccak256")
    original_keccak = getattr(mod, "Keccak256", None)
    mod.decode_transfer = fake_decode_transfer
    mod.Keccak256 = _FakeKeccak
    try:
        record = mod.judge(
            evidence_json,
            "upto-test-facilitator",
            make_claim(50),
            make_req("upto", 100),
        )
    finally:
        mod.decode_transfer = original_decode
        if had_original_keccak:
            mod.Keccak256 = original_keccak
        else:
            del mod.Keccak256
    checks.append((
        "upto payment without a provable EIP-3009 call downgrades honestly",
        record["verdict"] == "CONFIRMED"
        and record.get("x402Proof") == "TRANSFER_EVENT_ONLY",
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
