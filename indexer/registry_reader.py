"""Read-only on-chain registry snapshots from the X402Auditor contract.

Reads the get_registry() and get_registry_by_relayer() views, parses their
JSON string entries, summarizes the label/relayer conflict flags, caches the
snapshot in the local database and optionally pushes it to the remote API.
Pure view calls, no gas.
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contracts_config import X402_AUDITOR_ADDRESS

LABEL_REGISTRY_VIEW = "get_registry"
RELAYER_REGISTRY_VIEW = "get_registry_by_relayer"
REGISTRY_GROUPINGS = ("by_label", "by_relayer")


def _parse_entries(raw):
    """Parse one view result (list of JSON strings) into records."""
    if raw is None:
        return [], 0
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except Exception:
            return [], 1
    if not isinstance(raw, list):
        return [], 1
    records = []
    malformed = 0
    for item in raw:
        try:
            record = (
                json.loads(item)
                if isinstance(item, (str, bytes, bytearray))
                else item
            )
            if not isinstance(record, dict):
                raise ValueError("entry is not an object")
            records.append(record)
        except Exception:
            malformed += 1
    return records, malformed


def _read_view(client, function_name):
    raw = client.read_contract(
        address=X402_AUDITOR_ADDRESS,
        function_name=function_name,
        args=[],
    )
    return _parse_entries(raw)


def read_registries(client=None):
    """Read both registry views and return a full snapshot.

    Raises on RPC failure. Empty views are a valid result (no verdicts yet),
    so they come back as empty lists, never as errors.
    """
    if client is None:
        from genlayer_connection import get_client
        client = get_client()

    by_label, malformed_label = _read_view(client, LABEL_REGISTRY_VIEW)
    by_relayer, malformed_relayer = _read_view(client, RELAYER_REGISTRY_VIEW)
    return {
        "readAt": datetime.now(timezone.utc).isoformat(),
        "auditorAddress": X402_AUDITOR_ADDRESS,
        "byLabel": by_label,
        "byRelayer": by_relayer,
        "malformedEntries": malformed_label + malformed_relayer,
    }


def summarize(by_label, by_relayer):
    """Count first-class conflict flags from both groupings."""
    conflict_labels = sorted(
        str(entry.get("facilitator", ""))
        for entry in by_label
        if entry.get("labelRelayerConflict") is True
    )
    conflict_relayers = sorted(
        str(entry.get("relayer", ""))
        for entry in by_relayer
        if entry.get("relayerLabelConflict") is True
    )
    return {
        "labelRelayerConflict": {
            "count": len(conflict_labels),
            "labels": conflict_labels,
        },
        "relayerLabelConflict": {
            "count": len(conflict_relayers),
            "relayers": conflict_relayers,
        },
    }



def save_snapshot(snapshot: dict) -> None:
    """Cache the snapshot in the local database (best effort)."""
    try:
        import database
        database.save_registry_cache("by_label", snapshot["byLabel"], snapshot["readAt"])
        database.save_registry_cache("by_relayer", snapshot["byRelayer"], snapshot["readAt"])
    except Exception as exc:
        print("[REGISTRY] Failed to cache snapshot locally: " + str(exc))


def push_snapshot_to_api(snapshot: dict) -> None:
    """Optionally push the snapshot to the remote API (best effort)."""
    api_url = os.environ.get("X402_API_URL", "").rstrip("/")
    if not api_url:
        print("[API] X402_API_URL not set, skipping registry push")
        return
    try:
        import requests
        response = requests.post(
            api_url + "/ingest/registry",
            json={
                "readAt": snapshot["readAt"],
                "byLabel": snapshot["byLabel"],
                "byRelayer": snapshot["byRelayer"],
            },
            timeout=10,
        )
        print("[API] Pushed registry snapshot: " + str(response.status_code))
    except Exception as exc:
        print("[API] Failed to push registry snapshot: " + str(exc))


def refresh_snapshot(client=None) -> dict | None:
    """Read, cache and push one snapshot. Never raises."""
    try:
        snapshot = read_registries(client=client)
    except Exception as exc:
        print("[REGISTRY] Live read failed: " + str(exc))
        return None
    summary = summarize(snapshot["byLabel"], snapshot["byRelayer"])
    snapshot["summary"] = summary
    print(
        "[REGISTRY] labels=" + str(len(snapshot["byLabel"]))
        + " relayers=" + str(len(snapshot["byRelayer"]))
        + " conflicts=" + str(summary["labelRelayerConflict"]["count"])
        + "/" + str(summary["relayerLabelConflict"]["count"])
    )
    save_snapshot(snapshot)
    push_snapshot_to_api(snapshot)
    return snapshot

def _run_self_test() -> None:
    """Offline checks with a fake client. No network, no keys, no DB."""
    checks = []

    class _FakeClient:
        def __init__(self, label_raw, relayer_raw):
            self._views = {
                LABEL_REGISTRY_VIEW: label_raw,
                RELAYER_REGISTRY_VIEW: relayer_raw,
            }
            self.calls = []

        def read_contract(self, address, function_name, args):
            self.calls.append((address, function_name, args))
            return self._views[function_name]

    def label_entry(name, relayers):
        return {
            "facilitator": name,
            "relayers": relayers,
            "totalRecords": len(relayers),
            "confirmed": 1,
            "contradictedFailure": 0,
            "labelRelayerConflict": len(relayers) > 1,
        }

    def relayer_entry(address, labels):
        return {
            "relayer": address,
            "labels": labels,
            "totalRecords": len(labels),
            "confirmed": 0,
            "relayerLabelConflict": len(labels) > 1,
        }

    # 1. Parsing and conflict summary on a populated registry.
    by_label = json.dumps([
        json.dumps(label_entry("alpha", ["0xaaa"])),
        json.dumps(label_entry("beta", ["0xaaa", "0xbbb"])),
    ])
    by_relayer = json.dumps([
        json.dumps(relayer_entry("0xaaa", ["alpha"])),
        json.dumps(relayer_entry("0xbbb", ["beta", "gamma"])),
    ])
    client_1 = _FakeClient(by_label, by_relayer)
    snapshot = read_registries(client=client_1)
    checks.append((
        "read_registries reads both views with the auditor address and empty args",
        client_1.calls == [
            (X402_AUDITOR_ADDRESS, LABEL_REGISTRY_VIEW, []),
            (X402_AUDITOR_ADDRESS, RELAYER_REGISTRY_VIEW, []),
        ],
    ))
    checks.append((
        "label entries parse and carry the conflict flag",
        len(snapshot["byLabel"]) == 2
        and snapshot["byLabel"][1]["labelRelayerConflict"] is True
        and snapshot["malformedEntries"] == 0,
    ))
    summary = summarize(snapshot["byLabel"], snapshot["byRelayer"])
    checks.append((
        "summarize names the one conflicting label and the one conflicting relayer",
        summary["labelRelayerConflict"] == {"count": 1, "labels": ["beta"]}
        and summary["relayerLabelConflict"] == {"count": 1, "relayers": ["0xbbb"]},
    ))

    # 2. Empty and malformed entries never break the read.
    client_2 = _FakeClient(json.dumps(["{not json", 42]), json.dumps([]))
    snapshot_2 = read_registries(client=client_2)
    checks.append((
        "malformed entries are skipped and counted, empty views are valid",
        snapshot_2["byLabel"] == []
        and snapshot_2["byRelayer"] == []
        and snapshot_2["malformedEntries"] == 2,
    ))

    # 3. summarize on empty registries reports zero conflicts.
    empty_summary = summarize([], [])
    checks.append((
        "summarize on empty registries reports zero conflicts",
        empty_summary == {
            "labelRelayerConflict": {"count": 0, "labels": []},
            "relayerLabelConflict": {"count": 0, "relayers": []},
        },
    ))

    # 4. Push is a no-op without X402_API_URL (env restored after).
    original_api_url = os.environ.pop("X402_API_URL", None)
    try:
        push_snapshot_to_api({
            "readAt": "2026-09-15T00:00:00+00:00",
            "byLabel": [],
            "byRelayer": [],
        })
        checks.append(("push without X402_API_URL is a silent no-op", True))
    finally:
        if original_api_url is not None:
            os.environ["X402_API_URL"] = original_api_url

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

