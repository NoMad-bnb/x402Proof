"""Observe consensus behaviour of SupportedProbe transactions on studionet."""

import json
import os
import sys
import time

# Portable/embeddable Python does not add the running script's own
# folder to sys.path (handoff section 23, note 12).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genlayer_py.types import (
    EXECUTION_RESULT_NUMBER_TO_NAME,
    TRANSACTION_STATUS_NUMBER_TO_NAME,
    ExecutionResult,
    TransactionStatus,
)

from contracts_config import SUPPORTED_PROBE_ADDRESS
from genlayer_connection import get_client
from provider_registry import load_registry
from supported_collector import probe_and_update_registry


def _enum_label(value):
    """Return the plain string value of an SDK enum member.

    The SDK's status/result members are str-Enums whose str() can render
    as the qualified member name depending on the Python version; the
    .value is always the plain word we want to publish.
    """
    if value is None:
        return None
    if isinstance(value, (TransactionStatus, ExecutionResult)):
        return value.value
    return str(value)


def _to_int(value):
    """Best-effort int conversion; None when the value is not numeric."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _walk(obj):
    """Yield every (key, value) pair found in nested dicts/lists."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key, value
            for pair in _walk(value):
                yield pair
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            for pair in _walk(item):
                yield pair


def count_undetermined(consensus_data) -> int | None:
    """Count UNDETERMINED occurrences anywhere inside consensus data.

    The exact nesting shape is not fixed by the SDK, so this scans
    defensively: any string value equal to "UNDETERMINED" counts, as does
    a numeric/string "6" stored under a status/state-like key (state 6 is
    UNDETERMINED per the SDK's status table). Returns None when there is
    no consensus data at all, so "zero hits" and "unknown" stay distinct.
    """
    if not consensus_data:
        return None
    hits = 0
    for key, value in _walk(consensus_data):
        if isinstance(value, str) and value.upper() == "UNDETERMINED":
            hits = hits + 1
            continue
        key_l = str(key).lower() if key is not None else ""
        if ("status" in key_l or "state" in key_l) and str(value) == "6":
            hits = hits + 1
    return hits


def extract_consensus_metrics(tx, wall_seconds=None) -> dict:
    """Pull the consensus story out of one full transaction receipt.

    Every field degrades to None independently when absent, so a partial
    receipt still yields whatever is actually known.
    """
    metrics = {
        "final_status": None,
        "result": None,
        "rounds": None,
        "initial_validators": None,
        "undetermined_hits": None,
        "duration_seconds": None,
        "wall_clock_seconds": (
            round(wall_seconds, 1) if wall_seconds is not None else None
        ),
    }
    if not isinstance(tx, dict):
        return metrics

    status_name = tx.get("status_name")
    if status_name is None:
        status_name = TRANSACTION_STATUS_NUMBER_TO_NAME.get(str(tx.get("status")))
    metrics["final_status"] = _enum_label(status_name)

    result_name = tx.get("result_name")
    if result_name is None:
        result_name = EXECUTION_RESULT_NUMBER_TO_NAME.get(str(tx.get("result")))
    metrics["result"] = _enum_label(result_name)

    metrics["rounds"] = _to_int(tx.get("num_of_rounds"))
    metrics["initial_validators"] = _to_int(tx.get("num_of_initial_validators"))
    metrics["undetermined_hits"] = count_undetermined(tx.get("consensus_data"))

    created = _to_int(tx.get("created_timestamp"))
    voted = _to_int(tx.get("last_vote_timestamp"))
    if created is not None and voted is not None and voted >= created:
        metrics["duration_seconds"] = voted - created
    return metrics


def observe_provider(provider_id: str) -> dict:
    """Run one real probe and return its record plus consensus metrics.

    Uses the normal probe_and_update_registry path (so the registry and
    database stay exactly as consistent as any other probe), with the
    capture dict collecting the tx hash and the FULL receipt.
    """
    capture: dict = {}
    started = time.time()
    record = probe_and_update_registry(provider_id, capture=capture)
    wall = time.time() - started

    receipt = capture.get("receipt")
    observation = {
        "provider_id": provider_id,
        "tx_hash": capture.get("tx_hash"),
        "consensus": extract_consensus_metrics(receipt, wall_seconds=wall),
        "probe_record": record,
    }
    if isinstance(receipt, dict) and receipt.get("consensus_data") is not None:
        # Keep the raw consensus structure (could be large) truncated, so
        # the real shape is visible in output for future parsing without
        # flooding the console.
        observation["consensus_data_raw_preview"] = json.dumps(
            receipt.get("consensus_data"), sort_keys=True, default=str
        )[:2000]
    return observation


def stability_report() -> dict:
    """Compare every facilitator's probes against each other.

    Reads all probe records from the contract (a free view call) and
    groups them by facilitator label. A facilitator whose bodyDigest is
    identical across probes has a stable /supported declaration; one with
    differing digests gets its first and last body prefixes and declared
    pairs side by side so the drift is visible, not buried.
    """
    client = get_client()
    raw = client.read_contract(
        address=SUPPORTED_PROBE_ADDRESS,
        function_name="get_probes",
        args=[],
    )
    items = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw

    by_facilitator: dict = {}
    for index, item in enumerate(items, start=1):
        record = json.loads(item) if isinstance(item, str) else item
        label = str(record.get("facilitator", "?"))
        by_facilitator.setdefault(label, []).append((index, record))

    facilitators = []
    for label, entries in sorted(by_facilitator.items()):
        digests = [rec.get("bodyDigest") for _, rec in entries]
        entry = {
            "facilitator": label,
            "probe_count": len(entries),
            "probe_indexes": [index for index, _ in entries],
            "body_digests": digests,
            "digest_stable": len(set(digests)) <= 1,
        }
        if not entry["digest_stable"] and len(entries) >= 2:
            first_index, first = entries[0]
            last_index, last = entries[-1]
            entry["first_probe_index"] = first_index
            entry["last_probe_index"] = last_index
            entry["first_body_prefix"] = (first.get("bodyPrefix") or "")[:220]
            entry["last_body_prefix"] = (last.get("bodyPrefix") or "")[:220]
            entry["pairs_first"] = first.get("pairs")
            entry["pairs_last"] = last.get("pairs")
        facilitators.append(entry)

    return {"total_probes": len(items), "facilitators": facilitators}


def _run_self_test() -> None:
    checks = []

    rich_tx = {
        "status_name": "FINALIZED",
        "result_name": "MAJORITY_AGREE",
        "num_of_rounds": "3",
        "num_of_initial_validators": "5",
        "created_timestamp": "1788264000",
        "last_vote_timestamp": "1788264123",
        "consensus_data": {
            "rounds": [
                {"state": "UNDETERMINED"},
                {"state": "ACCEPTED"},
            ],
        },
    }
    metrics = extract_consensus_metrics(rich_tx, wall_seconds=61.5)
    checks.append(("metrics reads final_status", metrics["final_status"] == "FINALIZED"))
    checks.append(("metrics reads result", metrics["result"] == "MAJORITY_AGREE"))
    checks.append(("metrics reads rounds", metrics["rounds"] == 3))
    checks.append((
        "metrics reads validators",
        metrics["initial_validators"] == 5,
    ))
    checks.append((
        "metrics finds UNDETERMINED in consensus data",
        metrics["undetermined_hits"] == 1,
    ))
    checks.append((
        "metrics computes duration from timestamps",
        metrics["duration_seconds"] == 123,
    ))
    checks.append(("metrics keeps wall clock", metrics["wall_clock_seconds"] == 61.5))

    empty = extract_consensus_metrics({}, wall_seconds=None)
    checks.append(("empty tx yields unknown status", empty["final_status"] is None))
    checks.append(("empty tx yields unknown rounds", empty["rounds"] is None))
    checks.append((
        "empty tx yields unknown undetermined, not zero",
        empty["undetermined_hits"] is None,
    ))

    numeric_status_tx = {"status": "7", "consensus_data": {"state": "6"}}
    metrics = extract_consensus_metrics(numeric_status_tx, wall_seconds=None)
    checks.append((
        "numeric status maps through the SDK table",
        metrics["final_status"] == "FINALIZED",
    ))
    checks.append((
        "state 6 under a state key counts as undetermined",
        metrics["undetermined_hits"] == 1,
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
    if len(sys.argv) >= 2 and sys.argv[1] == "self-test":
        _run_self_test()
        raise SystemExit(0)

    usage = (
        "Usage:\n"
        "  python consensus_observer.py self-test\n"
        "  python consensus_observer.py observe <provider_id>\n"
        "  python consensus_observer.py observe-all\n"
        "  python consensus_observer.py report\n"
    )
    if len(sys.argv) < 2:
        print(usage)
        raise SystemExit(1)

    mode = sys.argv[1]
    if mode == "observe":
        if len(sys.argv) != 3:
            print(usage)
            raise SystemExit(1)
        print(json.dumps(observe_provider(sys.argv[2]), indent=2, ensure_ascii=False))
    elif mode == "observe-all":
        providers = [
            p for p in load_registry()
            if p.get("supported_url")
        ]
        results = []
        for provider in providers:
            print("Observing " + provider["provider_id"] + " ...", flush=True)
            results.append(observe_provider(provider["provider_id"]))
        print(json.dumps(results, indent=2, ensure_ascii=False))
    elif mode == "report":
        print(json.dumps(stability_report(), indent=2, ensure_ascii=False))
    else:
        print(usage)
        raise SystemExit(1)
