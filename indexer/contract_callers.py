"""Shared write/wait/read helpers for the three GenLayer contracts; never interprets verdicts or retries."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genlayer_connection import get_client
from contracts_config import (
    DECLARATION_AUDIT_ADDRESS,
    SUPPORTED_PROBE_ADDRESS,
    X402_AUDITOR_ADDRESS,
)
from genlayer_py.types import TransactionStatus

DEFAULT_WAIT_STATUS = "ACCEPTED"
DEFAULT_WAIT_INTERVAL_MS = 5000
DEFAULT_WAIT_RETRIES = 60


class ContractWaitExhausted(RuntimeError):
    """Raised when wait_for_transaction_receipt exhausts its polling window."""

    def __init__(self, message, tx_hash=None, address=None,
                 read_function_name=None, record_noun=None,
                 contract_type=None):
        super().__init__(message)
        self.tx_hash = tx_hash
        self.address = address
        self.read_function_name = read_function_name
        self.record_noun = record_noun
        self.contract_type = contract_type



def _append_then_read(
    client,
    address,
    function_name,
    args,
    read_function_name,
    record_noun,
    wait_status=DEFAULT_WAIT_STATUS,
    wait_interval=DEFAULT_WAIT_INTERVAL_MS,
    wait_retries=DEFAULT_WAIT_RETRIES,
    capture=None,
    contract_type=None,
):
    """Write to one contract, wait, read the append-only list back, and
    return the newest record, parsed.

    This is the single implementation of the append-then-read pattern.
    It is deliberately private: callers pick one of the three public
    wrappers below so the contract address, function name and read-back
    function always travel together and cannot drift apart.

    Raises (fixed codes, never raw error text):
        CONTRACT_WRITE_FAILED  write_contract() returned nothing usable
        CONTRACT_EMPTY_READ    the read-back list came back empty
    """
    if client is None:
        client = get_client()

    tx_hash = client.write_contract(
        address=address,
        function_name=function_name,
        args=args,
    )
    if not isinstance(tx_hash, str) or not tx_hash:
        raise RuntimeError(
            "CONTRACT_WRITE_FAILED: write_contract(" + function_name + ") "
            "returned " + repr(tx_hash) + " instead of a transaction hash."
        )

    # genlayer_py's wait_for_transaction_receipt() compares the requested
    # status against TransactionStatus enum members. Converting a plain
    # string here keeps every caller's string API unchanged.
    if isinstance(wait_status, str):
        try:
            wait_status = TransactionStatus[wait_status]
        except KeyError:
            raise RuntimeError(
                "CONTRACT_UNKNOWN_WAIT_STATUS: '" + str(wait_status)
                + "' is not a genlayer_py TransactionStatus member "
                "(expected ACCEPTED or FINALIZED)."
            )

    # The SDK default wait is 10 retries x 3000ms = 30s total, which
    # studionet consensus can exceed when Leader Rotation happens.
    # Callers may pass a longer window.
    wait_kwargs = {}
    if wait_interval is not None:
        wait_kwargs["interval"] = wait_interval
    if wait_retries is not None:
        wait_kwargs["retries"] = wait_retries

    if capture is not None:
        capture["tx_hash"] = tx_hash
        try:
            capture["receipt"] = client.wait_for_transaction_receipt(
                transaction_hash=tx_hash,
                status=wait_status,
                full_transaction=True,
                **wait_kwargs,
            )
        except Exception as exc:
            raise ContractWaitExhausted(
                str(exc),
                tx_hash=tx_hash,
                address=address,
                read_function_name=read_function_name,
                record_noun=record_noun,
                contract_type=contract_type,
            ) from exc
    else:
        try:
            client.wait_for_transaction_receipt(
                transaction_hash=tx_hash,
                status=wait_status,
                **wait_kwargs,
            )
        except Exception as exc:
            raise ContractWaitExhausted(
                str(exc),
                tx_hash=tx_hash,
                address=address,
                read_function_name=read_function_name,
                record_noun=record_noun,
                contract_type=contract_type,
            ) from exc

    raw_list = client.read_contract(
        address=address,
        function_name=read_function_name,
        args=[],
    )

    records = (
        json.loads(raw_list)
        if isinstance(raw_list, (str, bytes, bytearray))
        else raw_list
    )
    if not records:
        raise RuntimeError(
            "CONTRACT_EMPTY_READ: " + function_name + "() was accepted but "
            + read_function_name + "() returned no " + record_noun
            + " records. Refusing to guess which record belongs to this "
            "call; an empty read after an accepted write is a fact to "
            "investigate, not something to paper over."
        )

    latest = records[-1]
    return (
        json.loads(latest)
        if isinstance(latest, (str, bytes, bytearray))
        else latest
    )


def check_transaction_receipt(
    client,
    address,
    transaction_hash,
    read_function_name,
    record_noun,
    wait_status=DEFAULT_WAIT_STATUS,
    wait_interval=DEFAULT_WAIT_INTERVAL_MS,
    wait_retries=DEFAULT_WAIT_RETRIES,
    contract_type=None,
):
    """Wait for an existing transaction and read back its contract record.

    This is the check-only counterpart of _append_then_read(): no
    write_contract() is called. The caller supplies the transaction hash
    from a prior submission and the same read function that would have
    been used after a successful write. Returns the parsed record, or
    raises if the wait window is exhausted.

    Raises (fixed codes, never raw error text):
        CONTRACT_WAIT_EXHAUSTED  wait_for_transaction_receipt() did not
            reach the requested status within the supplied window
        CONTRACT_EMPTY_READ      the read-back list came back empty
    """
    if client is None:
        client = get_client()

    if isinstance(wait_status, str):
        try:
            wait_status = TransactionStatus[wait_status]
        except KeyError:
            raise RuntimeError(
                "CONTRACT_UNKNOWN_WAIT_STATUS: '" + str(wait_status)
                + "' is not a genlayer_py TransactionStatus member "
                "(expected ACCEPTED or FINALIZED)."
            )

    wait_kwargs = {"interval": wait_interval, "retries": wait_retries}

    try:
        receipt = client.wait_for_transaction_receipt(
            transaction_hash=transaction_hash,
            status=wait_status,
            full_transaction=False,
            **wait_kwargs,
        )
    except Exception as exc:
        raise ContractWaitExhausted(
            str(exc),
            tx_hash=transaction_hash,
            address=address,
            read_function_name=read_function_name,
            record_noun=record_noun,
            contract_type=contract_type,
        ) from exc

    raw_list = client.read_contract(
        address=address,
        function_name=read_function_name,
        args=[],
    )

    records = (
        json.loads(raw_list)
        if isinstance(raw_list, (str, bytes, bytearray))
        else raw_list
    )
    if not records:
        raise RuntimeError(
            "CONTRACT_EMPTY_READ: " + read_function_name + "() returned no "
            + record_noun + " records for an existing transaction."
        )

    latest = records[-1]
    return (
        json.loads(latest)
        if isinstance(latest, (str, bytes, bytearray))
        else latest
    )

def submit_x402_audit(claim, client=None, wait_status=DEFAULT_WAIT_STATUS,
                      wait_interval=None, wait_retries=None):
    """Call X402Auditor.audit() with the 16-element claim A7 built.

    Args:
        claim: the exact 16-element string list produced by
            claim_builder.build_claim(), in the exact order the
            contract expects. Checked structurally only (a list of 16
            strings): building and validating the content is A7's job
            and judging it is the contract's job.
        client: an already-built genlayer_py client. When None, one is
            built here via genlayer_connection.get_client(). Passing a
            client explicitly is how http_evidence_collector.py keeps
            its own get_client seam monkeypatchable, which is what
            keeps the A6 and A7 integration tests working unchanged.
        wait_status: receipt status to wait for, ACCEPTED by default.

    Returns the verdict record the contract just appended, parsed.
    """
    if (
        not isinstance(claim, list)
        or len(claim) != 16
        or not all(isinstance(item, str) for item in claim)
    ):
        raise RuntimeError(
            "CONTRACT_CLAIM_NOT_16_STRINGS: X402Auditor.audit() takes "
            "exactly 16 string arguments in a fixed order. Refusing to "
            "send a structurally wrong list: it would waste a consensus "
            "round or misrecord fields. Build the claim with "
            "claim_builder.build_claim() first."
        )
    return _append_then_read(
        client=client,
        address=X402_AUDITOR_ADDRESS,
        function_name="audit",
        args=claim,
        read_function_name="get_verdicts",
        record_noun="verdict",
        wait_status=wait_status,
        wait_interval=wait_interval,
        wait_retries=wait_retries,
        contract_type="x402_audit",
    )


def submit_declaration_audit(
    facilitator_label,
    declaration_url,
    rpc_url,
    transaction_hash,
    client=None,
    wait_status=DEFAULT_WAIT_STATUS,
    wait_interval=None,
    wait_retries=None,
):
    """Call DeclarationAudit.audit_declaration() with its 4 arguments.

    The pre-flight refusal below matches the contract's own early
    return exactly: empty declaration_url, rpc_url or transaction_hash
    after strip means the contract writes NOTHING. facilitator_label
    may be empty: the contract records it as-is.
    """
    if (
        not isinstance(facilitator_label, str)
        or not isinstance(declaration_url, str)
        or not isinstance(rpc_url, str)
        or not isinstance(transaction_hash, str)
    ):
        raise RuntimeError(
            "CONTRACT_DECLARATION_ARGS_NOT_STRINGS: all four "
            "audit_declaration() arguments are str in the contract "
            "signature."
        )
    if (
        not declaration_url.strip()
        or not rpc_url.strip()
        or not transaction_hash.strip()
    ):
        raise RuntimeError(
            "CONTRACT_DECLARATION_EMPTY_ARG: audit_declaration() returns "
            "early and writes nothing when declaration_url, rpc_url or "
            "transaction_hash is empty, so this call would spend gas and "
            "then make the read-back return the PREVIOUS caller's record. "
            "Refused before any gas."
        )
    return _append_then_read(
        client=client,
        address=DECLARATION_AUDIT_ADDRESS,
        function_name="audit_declaration",
        args=[facilitator_label, declaration_url, rpc_url, transaction_hash],
        read_function_name="get_records",
        record_noun="declaration",
        wait_status=wait_status,
        wait_interval=wait_interval,
        wait_retries=wait_retries,
        contract_type="declaration_audit",
    )


def submit_supported_probe(
    facilitator,
    url,
    client=None,
    wait_status=DEFAULT_WAIT_STATUS,
    wait_interval=None,
    wait_retries=None,
    capture=None,
):
    """Call SupportedProbe.probe() with its 2 arguments.

    Same pre-flight rule as submit_declaration_audit(): the contract
    returns early and writes nothing when url is empty after strip
    (supported_probe.py), so that case is refused here before any gas.
    facilitator may be empty: the contract records it as-is.
    """
    if not isinstance(facilitator, str) or not isinstance(url, str):
        raise RuntimeError(
            "CONTRACT_PROBE_ARGS_NOT_STRINGS: both probe() arguments are "
            "str in the contract signature."
        )
    if not url.strip():
        raise RuntimeError(
            "CONTRACT_PROBE_EMPTY_URL: probe() returns early and writes "
            "nothing when url is empty, so this call would spend gas and "
            "then make the read-back return the PREVIOUS caller's probe. "
            "Refused before any gas."
        )
    return _append_then_read(
        client=client,
        address=SUPPORTED_PROBE_ADDRESS,
        function_name="probe",
        args=[facilitator, url],
        read_function_name="get_probes",
        record_noun="probe",
        wait_status=wait_status,
        wait_interval=wait_interval,
        wait_retries=wait_retries,
        capture=capture,
        contract_type="supported_probe",
    )


class _FakeClient:
    """Self-test-only stand-in for the genlayer_py client. Records every
    call so checks can assert exactly what would reach the network, and
    returns a fixed read-back result."""

    def __init__(self, read_result):
        self.calls = []
        self._read_result = read_result

    def write_contract(self, **kwargs):
        self.calls.append(("write_contract", kwargs))
        return "0x" + "11" * 32

    def wait_for_transaction_receipt(self, **kwargs):
        self.calls.append(("wait_for_transaction_receipt", kwargs))
        return None

    def read_contract(self, **kwargs):
        self.calls.append(("read_contract", kwargs))
        return self._read_result


def _run_self_test():
    checks = []

    good_hash = "0x" + "ab" * 32
    payer = "0x" + "22" * 20
    payee = "0x572bb4287aacbd42d647d611459e996ec9c89c52"
    asset = "0x" + "33" * 20
    claim = [
        "fac",
        "https://sepolia.base.org",
        "true",
        good_hash,
        "eip155:84532",
        payer,
        "",
        "",
        "1788264699",
        "exact",
        "eip155:84532",
        payee,
        asset,
        "10000",
        "0x2c1b079",
        "self_probe",
    ]

    # 1. audit: the claim reaches write_contract intact, at the right
    # address and function.
    client_1 = _FakeClient(json.dumps([json.dumps({"verdict": "CONFIRMED"})]))
    record_1 = submit_x402_audit(claim=claim, client=client_1)
    writes_1 = [c for c in client_1.calls if c[0] == "write_contract"]
    checks.append((
        "submit_x402_audit sends the 16-element claim to audit() at the auditor address",
        len(writes_1) == 1
        and writes_1[0][1]["address"] == X402_AUDITOR_ADDRESS
        and writes_1[0][1]["function_name"] == "audit"
        and writes_1[0][1]["args"] == claim,
    ))

    # 2. audit: order of calls is write, wait(ACCEPTED), read, and the
    # read-back targets get_verdicts.
    read_1 = [c for c in client_1.calls if c[0] == "read_contract"]
    waits_1 = [c for c in client_1.calls if c[0] == "wait_for_transaction_receipt"]
    checks.append((
        "submit_x402_audit waits with ACCEPTED before reading get_verdicts",
        len(waits_1) == 1
        and waits_1[0][1]["status"] == "ACCEPTED"
        and waits_1[0][1]["transaction_hash"] == "0x" + "11" * 32
        and len(read_1) == 1
        and read_1[0][1]["function_name"] == "get_verdicts"
        and [c[0] for c in client_1.calls]
        == ["write_contract", "wait_for_transaction_receipt", "read_contract"],
    ))

    # 3. audit: the verdict record comes back parsed.
    checks.append((
        "submit_x402_audit returns the parsed verdict record",
        isinstance(record_1, dict) and record_1.get("verdict") == "CONFIRMED",
    ))

    # 4. audit: when several records exist, the LAST one is returned.
    two_records = [json.dumps({"verdict": "OLD"}), json.dumps({"verdict": "NEW"})]
    record_4 = submit_x402_audit(claim=claim, client=_FakeClient(json.dumps(two_records)))
    checks.append((
        "submit_x402_audit returns the latest record when several exist",
        record_4.get("verdict") == "NEW",
    ))

    # 5. audit: a record returned as a plain dict still works.
    record_5 = submit_x402_audit(claim=claim, client=_FakeClient([{"verdict": "DICT"}]))
    checks.append((
        "submit_x402_audit handles a record that is already a dict",
        record_5.get("verdict") == "DICT",
    ))

    # 6. audit: a bytes read-back is handled the same as a string one.
    record_6 = submit_x402_audit(
        claim=claim,
        client=_FakeClient(
            json.dumps([json.dumps({"verdict": "FROM_BYTES"})]).encode("utf-8")
        ),
    )
    checks.append((
        "submit_x402_audit handles a bytes read-back",
        record_6.get("verdict") == "FROM_BYTES",
    ))

    # 7. audit: a 15-element claim is refused before ANY call is made.
    client_7 = _FakeClient(json.dumps([json.dumps({"verdict": "NEVER"})]))
    refused_7 = False
    try:
        submit_x402_audit(claim=claim[:15], client=client_7)
    except RuntimeError as exc:
        refused_7 = "CONTRACT_CLAIM_NOT_16_STRINGS" in str(exc)
    checks.append((
        "submit_x402_audit refuses a 15-element claim before any gas",
        refused_7 and client_7.calls == [],
    ))

    # 8. audit: a non-string element is refused with the same code.
    client_8 = _FakeClient(json.dumps([json.dumps({"verdict": "NEVER"})]))
    claim_8 = list(claim)
    claim_8[13] = 10000
    refused_8 = False
    try:
        submit_x402_audit(claim=claim_8, client=client_8)
    except RuntimeError as exc:
        refused_8 = "CONTRACT_CLAIM_NOT_16_STRINGS" in str(exc)
    checks.append((
        "submit_x402_audit refuses a non-string claim element",
        refused_8 and client_8.calls == [],
    ))

    # 9. declaration: the 4 arguments reach audit_declaration() in order.
    client_9 = _FakeClient(
        json.dumps([json.dumps({"verdict": "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR"})])
    )
    record_9 = submit_declaration_audit(
        facilitator_label="fac",
        declaration_url="https://fac.example/declaration.json",
        rpc_url="https://sepolia.base.org",
        transaction_hash=good_hash,
        client=client_9,
    )
    writes_9 = [c for c in client_9.calls if c[0] == "write_contract"]
    checks.append((
        "submit_declaration_audit sends the 4 arguments in order",
        len(writes_9) == 1
        and writes_9[0][1]["address"] == DECLARATION_AUDIT_ADDRESS
        and writes_9[0][1]["function_name"] == "audit_declaration"
        and writes_9[0][1]["args"]
        == [
            "fac",
            "https://fac.example/declaration.json",
            "https://sepolia.base.org",
            good_hash,
        ]
        and record_9.get("verdict") == "CONSISTENT_DECLARATION_MATCHES_BEHAVIOUR",
    ))

    # 10. declaration: an empty facilitator is allowed, matching the
    # contract, which records it as-is.
    client_10 = _FakeClient(json.dumps([json.dumps({"verdict": "OK"})]))
    record_10 = submit_declaration_audit(
        facilitator_label="",
        declaration_url="https://fac.example/declaration.json",
        rpc_url="https://sepolia.base.org",
        transaction_hash=good_hash,
        client=client_10,
    )
    checks.append((
        "submit_declaration_audit allows an empty facilitator like the contract does",
        record_10.get("verdict") == "OK",
    ))

    # 11. declaration: an empty declaration_url is refused before any gas.
    client_11 = _FakeClient(json.dumps([json.dumps({"verdict": "NEVER"})]))
    refused_11 = False
    try:
        submit_declaration_audit(
            facilitator_label="fac",
            declaration_url="",
            rpc_url="https://sepolia.base.org",
            transaction_hash=good_hash,
            client=client_11,
        )
    except RuntimeError as exc:
        refused_11 = "CONTRACT_DECLARATION_EMPTY_ARG" in str(exc)
    checks.append((
        "submit_declaration_audit refuses an empty declaration_url before any gas",
        refused_11 and client_11.calls == [],
    ))

    # 12. declaration: an empty transaction_hash is refused too (the
    # contract's own early-return condition, matched exactly).
    client_12 = _FakeClient(json.dumps([json.dumps({"verdict": "NEVER"})]))
    refused_12 = False
    try:
        submit_declaration_audit(
            facilitator_label="fac",
            declaration_url="https://fac.example/declaration.json",
            rpc_url="https://sepolia.base.org",
            transaction_hash="",
            client=client_12,
        )
    except RuntimeError as exc:
        refused_12 = "CONTRACT_DECLARATION_EMPTY_ARG" in str(exc)
    checks.append((
        "submit_declaration_audit refuses an empty transaction_hash before any gas",
        refused_12 and client_12.calls == [],
    ))

    # 13. probe: the 2 arguments reach probe() and a custom wait_status
    # passes through to the receipt wait.
    client_13 = _FakeClient(
        json.dumps([json.dumps({"outcome": "DECLARATION_CAPTURED"})])
    )
    record_13 = submit_supported_probe(
        facilitator="fac",
        url="https://fac.example/supported",
        client=client_13,
        wait_status="FINALIZED",
    )
    writes_13 = [c for c in client_13.calls if c[0] == "write_contract"]
    waits_13 = [c for c in client_13.calls if c[0] == "wait_for_transaction_receipt"]
    checks.append((
        "submit_supported_probe sends probe() args and honors a custom wait_status",
        len(writes_13) == 1
        and writes_13[0][1]["address"] == SUPPORTED_PROBE_ADDRESS
        and writes_13[0][1]["function_name"] == "probe"
        and writes_13[0][1]["args"] == ["fac", "https://fac.example/supported"]
        and waits_13[0][1]["status"] == "FINALIZED"
        and record_13.get("outcome") == "DECLARATION_CAPTURED",
    ))

    # 14. probe: an empty url is refused before any gas.
    client_14 = _FakeClient(json.dumps([json.dumps({"outcome": "NEVER"})]))
    refused_14 = False
    try:
        submit_supported_probe(facilitator="fac", url="", client=client_14)
    except RuntimeError as exc:
        refused_14 = "CONTRACT_PROBE_EMPTY_URL" in str(exc)
    checks.append((
        "submit_supported_probe refuses an empty url before any gas",
        refused_14 and client_14.calls == [],
    ))

    # 15. empty read-back: refused with CONTRACT_EMPTY_READ for both the
    # list-shaped and the JSON-string-shaped empty result.
    refused_15a = False
    try:
        submit_x402_audit(claim=claim, client=_FakeClient([]))
    except RuntimeError as exc:
        refused_15a = "CONTRACT_EMPTY_READ" in str(exc)
    refused_15b = False
    try:
        submit_declaration_audit(
            facilitator_label="fac",
            declaration_url="https://fac.example/declaration.json",
            rpc_url="https://sepolia.base.org",
            transaction_hash=good_hash,
            client=_FakeClient("[]"),
        )
    except RuntimeError as exc:
        refused_15b = "CONTRACT_EMPTY_READ" in str(exc)
    checks.append((
        "an empty read-back raises CONTRACT_EMPTY_READ (list and string shapes)",
        refused_15a and refused_15b,
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





