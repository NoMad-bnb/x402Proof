"""Track pending GenLayer transactions across scheduler cycles to avoid re-sending."""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PENDING_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pending_transactions.json")
MAX_PENDING_ATTEMPTS = 10
MAX_PENDING_AGE_SECONDS = 24 * 60 * 60


def _load() -> dict:
    if not os.path.exists(PENDING_PATH):
        return {}
    try:
        with open(PENDING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    tmp = PENDING_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PENDING_PATH)


def record_pending(provider_id: str, tx_hash: str, contract_address: str,
                   read_function_name: str, record_noun: str,
                   wait_status: str = "ACCEPTED",
                   contract_type: str = "x402_audit") -> dict:
    """Record or refresh a pending transaction entry."""
    data = _load()
    existing = data.get(provider_id)
    now = datetime.now(timezone.utc).isoformat()

    if existing and existing.get("tx_hash") == tx_hash:
        entry = dict(existing)
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        entry["last_attempt_at"] = now
    else:
        entry = {
            "provider_id": provider_id,
            "tx_hash": tx_hash,
            "contract_address": contract_address,
            "read_function_name": read_function_name,
            "record_noun": record_noun,
            "wait_status": wait_status,
            "contract_type": contract_type,
            "attempts": 1,
            "first_attempt_at": now,
            "last_attempt_at": now,
        }
    data[provider_id] = entry
    _save(data)
    return entry


def get_pending(provider_id: str) -> dict | None:
    """Return pending entry for provider_id, or None."""
    data = _load()
    entry = data.get(provider_id)
    if not entry:
        return None
    if _is_stale(entry):
        remove_pending(provider_id)
        return None
    return entry


def remove_pending(provider_id: str) -> None:
    """Remove pending entry for provider_id if present."""
    data = _load()
    if provider_id in data:
        data.pop(provider_id, None)
        _save(data)


def _is_stale(entry: dict) -> bool:
    attempts = int(entry.get("attempts", 0))
    if attempts >= MAX_PENDING_ATTEMPTS:
        return True
    first = entry.get("first_attempt_at")
    if not first:
        return False
    try:
        then = datetime.fromisoformat(first)
        age = (datetime.now(timezone.utc) - then).total_seconds()
        return age > MAX_PENDING_AGE_SECONDS
    except (ValueError, TypeError):
        return False


def _run_self_test() -> None:
    import tempfile

    global PENDING_PATH
    original = PENDING_PATH
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        PENDING_PATH = tf.name
    try:
        entry = record_pending("p1", "0xabc", "0xaddr", "fn", "noun")
        checks = []
        checks.append((
            "record_pending creates entry",
            entry["tx_hash"] == "0xabc" and entry["attempts"] == 1,
        ))
        entry2 = record_pending("p1", "0xabc", "0xaddr", "fn", "noun")
        checks.append((
            "record_pending increments attempts on same tx",
            entry2["attempts"] == 2,
        ))
        got = get_pending("p1")
        checks.append((
            "get_pending returns entry",
            got is not None and got["attempts"] == 2,
        ))
        checks.append((
            "record_pending stores contract_type",
            got is not None and got.get("contract_type") == "x402_audit",
        ))
        remove_pending("p1")
        checks.append((
            "remove_pending deletes entry",
            get_pending("p1") is None,
        ))
        stale = False
        entry3 = record_pending("p2", "0xdef", "0xaddr", "fn", "noun")
        entry3["first_attempt_at"] = "2000-01-01T00:00:00+00:00"
        with open(PENDING_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["p2"] = entry3
        with open(PENDING_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
        if _is_stale(entry3):
            stale = True
        checks.append((
            "_is_stale flags old entries",
            stale is True,
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
    finally:
        PENDING_PATH = original
        if os.path.exists(PENDING_PATH) and PENDING_PATH != original:
            os.remove(PENDING_PATH)


if __name__ == "__main__":
    _run_self_test()
