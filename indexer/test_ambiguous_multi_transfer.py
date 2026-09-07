"""Offline test for the multi-Transfer behaviour of judge() in x402_auditor_v8.py.

Verifies what judge() currently does when a single settlement transaction
contains more than one matching Transfer event, so the AMBIGUOUS_MULTIPLE_MATCHES
fix is designed against a measured fact instead of a guess.
"""

import importlib.util
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONTRACT_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir, "x402Proof", "x402_auditor_v8.py",
))


class _Meta(type):
    def __getattr__(cls, name):
        return _Stub


class _Stub(metaclass=_Meta):
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Stub()

    def __getattr__(self, name):
        return _Stub

    def __getitem__(self, key):
        return _Stub()


def _install_genlayer_stub():
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


spec = importlib.util.spec_from_file_location(
    "x402_auditor_v8_ambiguous_test", CONTRACT_PATH,
)
mod = importlib.util.module_from_spec(spec)
_install_genlayer_stub()
spec.loader.exec_module(mod)

TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)
PAYER = "0x1d8757aae49cb66adf814ccf26658a3e31a20aa1"
PAYEE = "0x572bb4287aacbd42d647d611459e996ec9c89c52"
ASSET = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
CHAIN_ID_HEX = "0x14a34"
VALID_BEFORE = 1788264386 + 300


class _FakeKeccak:
    def __init__(self, data):
        pass

    def hexdigest(self):
        return "cd" * 32


def _address_topic(address):
    return "0x" + address[2:].rjust(64, "0")


def _word_uint(value):
    return format(value, "x").rjust(64, "0")


def fake_decode_transfer(log):
    topics = log.get("topics") or []
    if len(topics) < 3 or topics[0] != TRANSFER_TOPIC0:
        return {}
    return {
        "token": str(log.get("address", "")).lower(),
        "from": "0x" + topics[1].lower()[-40:],
        "to": "0x" + topics[2].lower()[-40:],
        "amount": int(log.get("data", "0x0"), 16),
    }


def build_evidence_with_two_matching_transfers(first_amount, second_amount):
    """One successful tx whose receipt contains TWO matching Transfers."""
    return json.dumps({
        "chainIdHex": CHAIN_ID_HEX,
        "transactionHash": "0x" + "ab" * 32,
        "anchorRequested": False,
        "anchorBlockNumber": None,
        "anchorFound": False,
        "anchorTimestamp": None,
        "found": True,
        "blockNumber": "0x2c1b071",
        "status": "0x1",
        "from": PAYER,
        "to": ASSET,
        "logs": [
            {
                "address": ASSET,
                "topics": [TRANSFER_TOPIC0, _address_topic(PAYER), _address_topic(PAYEE)],
                "data": "0x" + _word_uint(first_amount),
            },
            {
                "address": ASSET,
                "topics": [TRANSFER_TOPIC0, _address_topic(PAYER), _address_topic(PAYEE)],
                "data": "0x" + _word_uint(second_amount),
            },
        ],
        "txTo": ASSET,
        "txInput": "0xffff0001" + "00" * 100,
        "settlementTimestamp": 1788264386,
    }, sort_keys=True, separators=(",", ":"))


def make_claim(amount):
    return {
        "source": "self_probe",
        "success": "true",
        "transaction": "0x" + "ab" * 32,
        "network": "eip155:84532",
        "payer": PAYER,
        "amount": str(amount),
        "errorReason": "",
        "validBefore": str(VALID_BEFORE),
    }


def make_req(scheme="exact", max_amount=100):
    return {
        "scheme": scheme,
        "network": "eip155:84532",
        "payTo": PAYEE,
        "asset": ASSET,
        "maxAmountRequired": str(max_amount),
    }


def run_judge(first_amount, second_amount, scheme="exact", max_amount=100):
    original_decode = mod.decode_transfer
    had_keccak = hasattr(mod, "Keccak256")
    original_keccak = getattr(mod, "Keccak256", None)
    mod.decode_transfer = fake_decode_transfer
    mod.Keccak256 = _FakeKeccak
    try:
        return mod.judge(
            build_evidence_with_two_matching_transfers(first_amount, second_amount),
            "multi-transfer-test",
            make_claim(first_amount),
            make_req(scheme, max_amount),
        )
    finally:
        mod.decode_transfer = original_decode
        if had_keccak:
            mod.Keccak256 = original_keccak
        else:
            del mod.Keccak256


def _run_self_test():
    checks = []

    # Both transfers exactly match (asset, payee, payer, exact amount).
    record = run_judge(first_amount=100, second_amount=100)
    checks.append((
        "two identical matching transfers: both are candidates",
        record["transferCount"] == 2,
    ))
    checks.append((
        "v12 publishes AMBIGUOUS_MULTIPLE_MATCHES instead of guessing",
        record["verdict"] == "AMBIGUOUS_MULTIPLE_MATCHES",
    ))
    checks.append((
        "v12 reports the candidate count",
        record.get("candidateCount") == 2,
    ))
    checks.append((
        "v12 reports every candidate amount, no silent pick",
        record.get("candidateAmounts") == [100, 100],
    ))
    checks.append((
        "v12 publishes NO settledAmount (money must not be counted)",
        record.get("settledAmount") is None,
    ))
    checks.append((
        "both transfers counted as observed, none hidden",
        len(record.get("observed", [])) == 2,
    ))

    # Two matching transfers with DIFFERENT amounts: v12 refuses to pick
    # by log order and lists both candidates.
    record_amt = run_judge(
        first_amount=50, second_amount=100, scheme="upto", max_amount=100
    )
    checks.append((
        "upto two matches: v12 lists both amounts, settles none",
        record_amt["verdict"] == "AMBIGUOUS_MULTIPLE_MATCHES"
        and record_amt.get("candidateAmounts") == [50, 100]
        and record_amt.get("settledAmount") is None,
    ))

    # Regression guard: a SINGLE matching transfer must still CONFIRM
    # (the v12 branch must not leak into the unambiguous path).
    record_single = run_judge(first_amount=100, second_amount=0)
    checks.append((
        "single matching transfer still confirms",
        record_single["verdict"] == "CONFIRMED"
        and record_single.get("settledAmount") == 100,
    ))

    failures = 0
    for description, passed in checks:
        print(description + ": " + ("True" if passed else "FAILED"))
        if not passed:
            failures = failures + 1
    print("")
    if failures == 0:
        print("All " + str(len(checks)) + " checks passed (v12 behaviour).")
    else:
        print(str(failures) + " of " + str(len(checks)) + " checks FAILED.")
        raise SystemExit(1)


if __name__ == "__main__":
    _run_self_test()