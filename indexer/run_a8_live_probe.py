"""One-off CLI that runs a live A8 supported_probe against studionet and verifies the count grew."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genlayer_connection import get_client
from contracts_config import SUPPORTED_PROBE_ADDRESS
from provider_registry import get_provider
from contract_callers import submit_supported_probe


def read_probe_count(client):
    """Free view call: the number of probe records the contract holds.
    Returns an int; a non-numeric answer surfaces as an error, never as
    a guessed count."""
    raw = client.read_contract(
        address=SUPPORTED_PROBE_ADDRESS,
        function_name="get_probe_count",
        args=[],
    )
    return int(str(raw))


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 run_a8_live_probe.py <provider_id>")
        print("Example: python3 run_a8_live_probe.py x402.mikedotexe.com")
        sys.exit(1)

    provider_id = sys.argv[1]
    provider = get_provider(provider_id)
    if provider is None:
        raise SystemExit("provider_id not found in registry: " + provider_id)
    if not provider.get("supported_url"):
        raise SystemExit(
            "provider " + provider_id + " has no supported_url in the "
            "registry; refusing to guess one"
        )

    client = get_client()

    try:
        account_address = client.account.address
    except Exception:
        account_address = "<address attribute not exposed by this SDK version; skipped>"
    print("Account (public address only): " + str(account_address))
    print("Provider: " + provider_id)
    print("Supported URL (from registry): " + provider["supported_url"])
    print("")

    print("PHASE 1 (free): reading get_probe_count() ...")
    count_before = read_probe_count(client)
    print("count_before = " + str(count_before))
    print("")

    print("PHASE 2 (gas): submit_supported_probe via contract_callers (A8) ...")
    record = submit_supported_probe(
        facilitator=provider_id,
        url=provider["supported_url"],
        client=client,
    )
    print("Record returned through the A8 layer:")
    print(json.dumps(record, indent=2, ensure_ascii=False, sort_keys=True))
    print("")

    print("PHASE 3 (free): reading get_probe_count() again ...")
    count_after = read_probe_count(client)
    print("count_after = " + str(count_after))
    print("")

    count_ok = count_after == count_before + 1
    facilitator_ok = str(record.get("facilitator", "")) == provider_id
    print("count grew by exactly one: " + ("True" if count_ok else "FAILED"))
    print("record facilitator matches: " + ("True" if facilitator_ok else "FAILED"))

    if count_ok and facilitator_ok:
        print("")
        print("A8 LIVE PROBE: SUCCESS")
    else:
        print("")
        print("A8 LIVE PROBE: FAILED")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
